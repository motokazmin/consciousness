"""Проба Polar H10: есть ли сервис PMD и виден ли в акселерометре дыхание.

Отвечает на P-001. Проверяет три вещи по очереди, каждая следующая имеет смысл
только если предыдущая удалась:

1. Перечисление всех сервисов и характеристик, включая нестандартные UUID.
   Обычное перечисление PMD показывает, но приложения его пропускают, потому
   что ищут только стандартные сервисы.
2. Чтение control point PMD: устройство отдаёт битовую маску того, какие потоки
   оно умеет (ЭКГ, PPG, акселерометр, PPI, гироскоп, магнитометр).
3. Запуск потока акселерометра и поиск дыхания в движении грудной клетки.
   Дыхание обязано быть механическим каналом, независимым от RR (ADR-003):
   акселерометр таким и является, ряд RR — нет.

Датчик должен быть надет (лёжащий на столе ремень даёт ЭКГ-мусор и неподвижный
акселерометр) и не занят другим клиентом BLE.

Запуск из корня репо:
    python -m research.tools.pmd_probe [--mac AA:BB:..] [--seconds 40]
"""

from __future__ import annotations

import argparse
import asyncio
import struct
import sys

import numpy as np
from scipy.signal import detrend, welch

from hrv_core.ble_scan import (
    bleak_adapter_kwargs,
    discover_ble_devices,
    format_bleak_connect_error,
)

PMD_SERVICE = "fb005c80-02e7-f387-1cad-8acd2d8df0c8"
PMD_CONTROL = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"

# Биты маски возможностей, приходящей чтением control point.
PMD_FEATURES = {
    0: "ЭКГ",
    1: "PPG",
    2: "акселерометр",
    3: "PPI",
    5: "гироскоп",
    6: "магнитометр",
}

ACC_TYPE = 0x02
ACC_HZ = 200
# start measurement: тип ACC, частота 200 Гц, разрешение 16 бит, диапазон 8g
ACC_START = bytes([
    0x02, ACC_TYPE,
    0x00, 0x01, ACC_HZ & 0xFF, ACC_HZ >> 8,   # sample rate
    0x01, 0x01, 0x10, 0x00,                   # resolution 16
    0x02, 0x01, 0x08, 0x00,                   # range 8g
])
ACC_STOP = bytes([0x03, ACC_TYPE])

# Полоса поиска дыхания: 3-36 циклов в минуту.
BREATH_BAND = (0.05, 0.60)


def decode_features(raw: bytes) -> list[str]:
    """Первый байт — код ответа, второй — маска возможностей."""
    if len(raw) < 2:
        return []
    mask = raw[1]
    return [name for bit, name in PMD_FEATURES.items() if mask & (1 << bit)]


def _unpack_bits(buf: bytes, offset_bits: int, width: int, signed: bool) -> int:
    """Достать width бит начиная с offset_bits, младшими вперёд."""
    value = 0
    for i in range(width):
        bit_index = offset_bits + i
        byte = buf[bit_index >> 3]
        value |= ((byte >> (bit_index & 7)) & 1) << i
    if signed and width and value & (1 << (width - 1)):
        value -= 1 << width
    return value


def parse_acc_frame(payload: bytes) -> list[tuple[int, int, int]]:
    """Кадр PMD-акселерометра → список отсчётов (x, y, z) в mg.

    Формат: тип(1) + timestamp(8) + frame type(1) + данные. Дельта-кадр несёт
    опорный отсчёт целиком, дальше блоки [ширина дельты в битах][сколько
    отсчётов] и упакованные дельты по три канала на отсчёт.
    """
    if len(payload) < 10 or payload[0] != ACC_TYPE:
        return []
    frame_type = payload[9]
    body = payload[10:]
    if frame_type == 0x00:  # без дельт, 16 бит на канал
        n = len(body) // 6
        return [struct.unpack_from("<hhh", body, i * 6) for i in range(n)]
    if frame_type != 0x01:
        return []

    ref = list(struct.unpack_from("<hhh", body, 0))
    out = [tuple(ref)]
    pos = 6
    while pos + 2 <= len(body):
        width = body[pos]
        count = body[pos + 1]
        pos += 2
        if width == 0 or count == 0:
            break
        need_bits = width * 3 * count
        need_bytes = (need_bits + 7) // 8
        if pos + need_bytes > len(body):
            break
        chunk = body[pos:pos + need_bytes]
        bit = 0
        for _ in range(count):
            for axis in range(3):
                ref[axis] += _unpack_bits(chunk, bit, width, signed=True)
                bit += width
            out.append(tuple(ref))
        pos += need_bytes
    return out


def breathing_from_acc(samples: list[tuple[int, int, int]], fs: float) -> dict | None:
    """Доминирующая частота движения грудной клетки в дыхательной полосе."""
    if len(samples) < int(fs * 20):
        return None
    arr = np.asarray(samples, dtype=float)
    # Ось с наибольшей медленной изменчивостью и есть ось дыхания: её выбирает
    # то, как ремень сидит на груди, а не наши предположения.
    decim = max(1, int(round(fs / 10.0)))
    slow = np.stack([
        detrend(arr[: len(arr) // decim * decim, k].reshape(-1, decim).mean(axis=1))
        for k in range(3)
    ])
    fs_slow = fs / decim
    axis = int(np.argmax(slow.std(axis=1)))
    sig = slow[axis]
    nper = min(len(sig), int(fs_slow * 60))
    freqs, power = welch(sig, fs=fs_slow, nperseg=nper)
    band = (freqs >= BREATH_BAND[0]) & (freqs <= BREATH_BAND[1])
    if not band.any():
        return None
    peak = freqs[band][int(np.argmax(power[band]))]
    share = float(power[band].sum() / power[1:].sum()) if power[1:].sum() else 0.0
    return {
        "axis": "XYZ"[axis],
        "cpm": peak * 60.0,
        "band_share_pct": share * 100.0,
        "fs_slow": fs_slow,
    }


async def probe(mac: str | None, seconds: float, check_only: bool = False) -> int:
    from bleak import BleakClient

    if mac is None:
        print("Ищу датчик…")
        devices = await discover_ble_devices(timeout=10.0)
        polar = [d for d in devices if (d.name or "").lower().startswith("polar")]
        if not polar:
            names = ", ".join(sorted({d.name for d in devices if d.name})) or "ничего"
            print(f"Polar не найден. Видно: {names}")
            return 1
        mac = polar[0].address
        print(f"Нашёл: {polar[0].name} [{mac}]")

    async with BleakClient(mac, **bleak_adapter_kwargs()) as client:
        print("\n=== 1. Все сервисы и характеристики ===")
        has_pmd = False
        for service in client.services:
            mark = ""
            if service.uuid.lower() == PMD_SERVICE:
                has_pmd = True
                mark = "   <== PMD, нестандартный сервис Polar"
            print(f"\n{service.uuid}  {service.description}{mark}")
            for ch in service.characteristics:
                props = ",".join(ch.properties)
                print(f"    {ch.uuid}  [{props}]  {ch.description}")

        if not has_pmd:
            print("\nСервиса PMD на устройстве нет. Дыхание с этого ремня не снять.")
            return 2

        print("\n=== 2. Что PMD умеет отдавать ===")
        # Читать control point до подписки бесполезно: H10 отвечает ATT 0x0e,
        # пока не включены индикации. Подписка обязана идти первой.
        samples: list[tuple[int, int, int]] = []
        frames = 0
        control_replies: list[bytes] = []

        def on_control(_h, data: bytearray) -> None:
            control_replies.append(bytes(data))

        def on_data(_h, data: bytearray) -> None:
            nonlocal frames
            frames += 1
            samples.extend(parse_acc_frame(bytes(data)))

        await client.start_notify(PMD_CONTROL, on_control)
        await asyncio.sleep(0.5)

        raw = b""
        try:
            raw = bytes(await client.read_gatt_char(PMD_CONTROL))
        except Exception as exc:
            print(f"чтение control point не прошло: {exc}")
        if not raw and control_replies:
            raw = control_replies[0]  # часть прошивок присылает маску индикацией
        feats = decode_features(raw)
        print(f"маска: {raw.hex() or '—'} → {', '.join(feats) if feats else 'пусто'}")
        if not feats:
            print("Возможности не прочитаны. Пробую запросить настройки акселерометра…")
            await client.write_gatt_char(PMD_CONTROL, bytes([0x01, ACC_TYPE]), response=True)
            await asyncio.sleep(1.0)
            if control_replies:
                print(f"ответ на запрос настроек: {control_replies[-1].hex()}")
                feats = ["акселерометр"] if control_replies[-1][:2] == bytes([0xf0, 0x01]) else []
        if "акселерометр" not in feats:
            print("Акселерометр не подтверждён. Смотреть ответы выше.")
            return 3

        if check_only:
            print("\nСвязь есть, акселерометр заявлен. Поток не запускался (--check).")
            return 0

        print(f"\n=== 3. Поток акселерометра, {seconds:.0f} с ===")
        await client.start_notify(PMD_DATA, on_data)
        await client.write_gatt_char(PMD_CONTROL, ACC_START, response=True)
        await asyncio.sleep(1.0)
        if control_replies:
            print(f"ответ на запуск: {control_replies[-1].hex()}")

        await asyncio.sleep(seconds)
        try:
            await client.write_gatt_char(PMD_CONTROL, ACC_STOP, response=True)
        except Exception:
            pass
        await client.stop_notify(PMD_DATA)
        await client.stop_notify(PMD_CONTROL)

        print(f"кадров: {frames}, отсчётов: {len(samples)}")
        if not samples:
            print("Поток не пошёл: сервис есть, данных нет.")
            return 4
        fs = len(samples) / seconds
        arr = np.asarray(samples, dtype=float)
        print(f"частота по факту: {fs:.0f} Гц (заявлено {ACC_HZ})")
        for k, name in enumerate("XYZ"):
            print(f"  ось {name}: среднее {arr[:, k].mean():8.0f}  "
                  f"разброс {arr[:, k].std():7.1f}  "
                  f"размах {arr[:, k].ptp():8.0f}")

        br = breathing_from_acc(samples, fs)
        if br is None:
            print("\nОтсчётов мало для оценки дыхания.")
            return 0
        print(f"\nДоминирующее движение по оси {br['axis']}: "
              f"**{br['cpm']:.1f} цикл/мин**, "
              f"доля дыхательной полосы в мощности {br['band_share_pct']:.0f}%.")
        print("Сверить с тем, как Роман дышал в эти секунды. Совпало — канал есть.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mac", help="MAC датчика, если не искать сканированием")
    ap.add_argument("--seconds", type=float, default=40.0, help="длина пробы потока")
    ap.add_argument("--check", action="store_true",
                    help="только связь и список возможностей, без запуска потока")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(probe(args.mac, args.seconds, args.check)))
    except Exception as exc:  # проба одноразовая: важен диагноз, не стектрейс
        hint = format_bleak_connect_error(exc)
        print(f"Не удалось: {exc}")
        if hint:
            print(hint)
        sys.exit(1)


if __name__ == "__main__":
    main()
