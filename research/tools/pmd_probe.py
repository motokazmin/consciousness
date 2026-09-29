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

Протокол PMD (UUID, маска возможностей, разбор кадров) — в `hrv_core.pmd`;
этот файл — только одноразовый ручной прогон поверх него.

Запуск из корня репо:
    python -m research.tools.pmd_probe [--mac AA:BB:..] [--seconds 40]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import numpy as np

from hrv_core.ble_scan import (
    bleak_adapter_kwargs,
    discover_ble_devices,
    format_bleak_connect_error,
)
from hrv_core.pmd import (
    ACC_STOP,
    ACC_TYPE,
    PMD_CONTROL,
    PMD_DATA,
    PMD_SERVICE,
    acc_start_command,
    breathing_from_acc,
    decode_features,
    parse_acc_frame,
)

# Частота пробы: в проекте PMD теперь работает на 25 Гц (см. hrv_core/pmd.py),
# но проба исторически ходила на 200 — этого хватает с большим запасом и здесь
# не критично, оставлено как было.
ACC_HZ = 200
ACC_START = acc_start_command(ACC_HZ)


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
