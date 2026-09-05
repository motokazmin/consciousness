"""Протокол PMD (Polar Measurement Data) Polar H10: акселерометр по тому же BLE-соединению.

Отвечает на задачу «дыхание из акселерометра, RR неприкосновенен» (ADR-003 в
research/, сюда не заглядываем — только код). Выделено из одноразовой пробы
`research/tools/pmd_probe.py`, которая теперь импортирует отсюда.

Разбор кадров и маски — чистые функции без bleak: их можно тестировать без
живого устройства. `PmdAccStream` — тонкая обвязка вокруг уже подключённого
`BleakClient` (второе BLE-соединение H10 не даёт, поэтому клиент общий с RR).

Только акселерометр (бит 2 маски). ЭКГ (бит 0) не трогаем.

**Важная оговорка.** На живом устройстве подтверждены только перечисление
сервисов и чтение control point (маска возможностей = 0x05: ЭКГ + акселерометр).
Команда start measurement, приём кадров данных и разбор дельта-кадров железа
ни разу не видели — написаны по протоколу и старой пробе, но не проверены.
Поэтому `PmdAccStream` принимает необязательный `on_event` — хук, который
получает построчную диагностику (сырой hex, статусы, разбор первых кадров).
В боевой записи (`hrv_core/sources.py`) он не задан — тихо, лишнего не пишет.
В `python -m hrv_core.pmd_check` он подключён к печати: одного прогона должно
хватить, чтобы увидеть, на каком шаге разбор разошёлся с реальностью.
"""

from __future__ import annotations

import struct
import time
from typing import Callable

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
# 25 Гц с запасом хватает на полосу дыхания (0.05-0.6 Гц) и в 8 раз снижает
# трафик и расход батареи против 200 Гц. Если прошивка откажет — пробуем по
# возрастанию следующие частоты.
ACC_DEFAULT_HZ = 25
ACC_CANDIDATE_HZ: tuple[int, ...] = (25, 50, 100, 200)
ACC_RESOLUTION_BITS = 16
ACC_RANGE_G = 8
ACC_STOP = bytes([0x03, ACC_TYPE])

# Полоса поиска дыхания: 3-36 циклов в минуту.
BREATH_BAND = (0.05, 0.60)

PAIRING_HINT = (
    "Polar H10 требует сопряжения (bonding) для потока PMD. "
    "Выполните один раз: bluetoothctl pair <MAC>, затем повторите."
)

_PAIRING_ERROR_MARKERS = (
    "0x0e",
    "insufficient authentication",
    "not authorized",
    "not permitted",
    "not paired",
)

# Коды статуса в ответе control point на запись команды (0xF0 op status ...).
# По публичному протоколу Polar PMD/BLE SDK — на этом экземпляре устройства
# не проверено (start measurement железа ещё не видел). Неизвестный код —
# не ошибка разбора, просто печатаем число как есть.
PMD_STATUS_CODES = {
    0: "SUCCESS",
    1: "INVALID_OP_CODE",
    2: "INVALID_MEASUREMENT_TYPE",
    3: "NOT_SUPPORTED",
    4: "INVALID_LENGTH",
    5: "INVALID_PARAMETER",
    6: "ALREADY_IN_STATE",
    7: "INVALID_RESOLUTION",
    8: "INVALID_SAMPLE_RATE",
    9: "INVALID_RANGE",
    10: "INVALID_MTU",
    11: "INVALID_NUMBER_OF_CHANNELS",
    12: "INVALID_STATE",
    13: "DEVICE_IN_CHARGER",
}


def describe_status(status: int | None) -> str:
    if status is None:
        return "нет ответа"
    name = PMD_STATUS_CODES.get(status)
    return f"{status} ({name})" if name else f"{status} (код не в справочнике)"


def hexdump(raw: bytes, limit: int = 220) -> str:
    """Hex целиком, с ограничением на явный мусор/огромные кадры."""
    if not raw:
        return "—"
    if len(raw) > limit:
        return raw[:limit].hex() + f"…(+{len(raw) - limit} байт)"
    return raw.hex()


class PmdError(RuntimeError):
    """PMD недоступен или отказал: нет сопряжения, маска без акселерометра,
    поток не пошёл, кадр не разобрался. Вызывающая сторона обязана проглотить —
    RR от этого не зависит."""


class PmdPairingRequiredError(PmdError):
    """ATT 0x0e: прошивка требует bonding до обращения к PMD."""


def is_pairing_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _PAIRING_ERROR_MARKERS)


def acc_start_command(
    hz: int,
    *,
    resolution_bits: int = ACC_RESOLUTION_BITS,
    range_g: int = ACC_RANGE_G,
) -> bytes:
    """Команда control point: запуск акселерометра на заданной частоте."""
    return bytes([
        0x02, ACC_TYPE,
        0x00, 0x01, hz & 0xFF, (hz >> 8) & 0xFF,               # sample rate
        0x01, 0x01, resolution_bits & 0xFF, (resolution_bits >> 8) & 0xFF,
        0x02, 0x01, range_g & 0xFF, (range_g >> 8) & 0xFF,     # range
    ])


def decode_features(raw: bytes) -> list[str]:
    """Первый байт — код ответа, второй — маска возможностей."""
    if len(raw) < 2:
        return []
    mask = raw[1]
    return [name for bit, name in PMD_FEATURES.items() if mask & (1 << bit)]


def start_ack_status(replies: list[bytes]) -> int | None:
    """Статус ответа на команду старта измерения акселерометра (0 = принято).

    Формат ответа control point: 0xF0, op_code, measurement_type, status, ...
    None — среди ответов нет подходящего (устройство могло промолчать).
    """
    for raw in reversed(replies):
        if len(raw) >= 4 and raw[0] == 0xF0 and raw[1] == 0x02 and raw[2] == ACC_TYPE:
            return raw[3]
    return None


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
    """Доминирующая частота движения грудной клетки в дыхательной полосе.

    Только для проверочной команды (`pmd_check`) — вывод человеку, не пишется
    в БД и не участвует в live-графиках или post-session анализе.
    """
    import numpy as np
    from scipy.signal import detrend, welch

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


OnAccelBatch = Callable[[float, list[tuple[int, int, int]], float], None]


class PmdAccStream:
    """Один поток акселерометра PMD поверх уже подключённого BleakClient.

    Второе BLE-соединение к H10 не открывается — акселерометр подписывается на
    том же клиенте, что и HR notify. Копит отсчёты и отдаёт их пачками
    (`on_batch(batch_ts, samples, hz)`) примерно раз в секунду, а не по одному.

    Любая ошибка здесь — PmdError (или подкласс `PmdPairingRequiredError`).
    RR от этого класса не зависит: вызывающая сторона (`PolarH10Source`)
    обязана поймать исключение и продолжить работу без акселерометра.
    """

    def __init__(
        self,
        client,
        on_batch: OnAccelBatch,
        *,
        candidate_hz: tuple[int, ...] = ACC_CANDIDATE_HZ,
        on_event: Callable[[str], None] | None = None,
        first_frames_kept: int = 3,
    ):
        self._client = client
        self._on_batch = on_batch
        self._candidate_hz = candidate_hz
        self._on_event = on_event or (lambda _msg: None)
        self._control_replies: list[bytes] = []
        self._buffer: list[tuple[int, int, int]] = []
        self._buffer_ts: float | None = None
        self._hz: float | None = None
        self._frames_seen = 0  # за текущую попытку частоты (сброс на каждый hz)
        self.total_frames = 0  # за всё время жизни потока
        self.total_samples = 0
        self._first_frames_kept = first_frames_kept
        self.first_frames_raw: list[bytes] = []

    def _on_control(self, _handle, data) -> None:
        raw = bytes(data)
        self._control_replies.append(raw)
        self._on_event(f"control ← {hexdump(raw)}")

    def _on_data(self, _handle, data) -> None:
        raw = bytes(data)
        self.total_frames += 1
        samples = parse_acc_frame(raw)
        self.total_samples += len(samples)
        if len(self.first_frames_raw) < self._first_frames_kept:
            self.first_frames_raw.append(raw)
            frame_type = raw[9] if len(raw) > 9 else None
            type_name = {0x00: "raw", 0x01: "delta"}.get(frame_type, f"?{frame_type}")
            self._on_event(
                f"data кадр #{self.total_frames}: {len(raw)} байт, тип={type_name}, "
                f"отсчётов={len(samples)}, hex={hexdump(raw)}"
            )
        if not samples:
            return
        self._frames_seen += 1
        if self._buffer_ts is None:
            self._buffer_ts = time.time()
        self._buffer.extend(samples)
        if self._hz and len(self._buffer) >= self._hz:
            self._flush()

    def _flush(self) -> None:
        if not self._buffer or self._buffer_ts is None:
            return
        self._on_batch(self._buffer_ts, self._buffer, self._hz or 0.0)
        self._buffer = []
        self._buffer_ts = None

    async def start(self) -> float:
        """Подписаться и договориться о частоте. Возвращает фактическую частоту."""
        import asyncio

        await self._client.start_notify(PMD_CONTROL, self._on_control)
        await asyncio.sleep(0.3)

        raw = b""
        try:
            raw = bytes(await self._client.read_gatt_char(PMD_CONTROL))
            self._on_event(f"чтение control point (возможности): {hexdump(raw)}")
        except Exception as exc:
            if is_pairing_error(exc):
                raise PmdPairingRequiredError(PAIRING_HINT) from exc
            raise PmdError(f"чтение возможностей PMD не удалось: {exc}") from exc
        if not raw and self._control_replies:
            raw = self._control_replies[0]  # часть прошивок отвечает индикацией
        feats = decode_features(raw)
        if "акселерометр" not in feats:
            # Запасной путь: явный запрос настроек акселерометра (0x01, ACC_TYPE) —
            # часть прошивок не кладёт маску в первый ответ.
            self._on_event(
                f"акселерометр не в маске ({feats or 'пусто'}), "
                "пробую запрос настроек акселерометра…"
            )
            self._control_replies.clear()
            try:
                await self._client.write_gatt_char(
                    PMD_CONTROL, bytes([0x01, ACC_TYPE]), response=True
                )
                await asyncio.sleep(1.0)
            except Exception as exc:
                if is_pairing_error(exc):
                    raise PmdPairingRequiredError(PAIRING_HINT) from exc
                raise PmdError(f"запрос настроек акселерометра не удался: {exc}") from exc
            if self._control_replies:
                reply = self._control_replies[-1]
                self._on_event(f"ответ на запрос настроек: {hexdump(reply)}")
                if reply[:2] != bytes([0xF0, 0x01]):
                    raise PmdError(f"акселерометр не подтверждён: {hexdump(reply)}")
            else:
                raise PmdError(f"акселерометр не заявлен в маске PMD: {hexdump(raw)}")

        await self._client.start_notify(PMD_DATA, self._on_data)
        for hz in self._candidate_hz:
            self._frames_seen = 0
            self._control_replies.clear()
            # Отсчёты, пришедшие при предыдущей (отклонённой) частоте, не должны
            # попасть в пачку согласованной — иначе load_accel_samples разложит
            # их по времени с чужим шагом. total_frames/total_samples — счётчики
            # за всё время жизни потока, их не трогаем: это диагностика для
            # pmd_check, ей важно и то, что шло на отклонённой частоте.
            self._buffer = []
            self._buffer_ts = None
            try:
                await self._client.write_gatt_char(
                    PMD_CONTROL, acc_start_command(hz), response=True
                )
            except Exception as exc:
                if is_pairing_error(exc):
                    raise PmdPairingRequiredError(PAIRING_HINT) from exc
                self._on_event(f"hz={hz}: запись команды старта не удалась: {exc}")
                continue
            await asyncio.sleep(1.2)
            status = start_ack_status(self._control_replies)
            accepted = status == 0 or (status is None and self._frames_seen > 0)
            self._on_event(
                f"hz={hz}: статус={describe_status(status)}, "
                f"кадров данных за 1.2s={self._frames_seen} → "
                f"{'принято' if accepted else 'отказ'}"
            )
            if accepted:
                self._hz = float(hz)
                return self._hz
            # Отказ: то, что успело накопиться в буфере за это окно, снято на
            # отклонённой частоте — выбрасываем, а не переносим на следующую.
            self._buffer = []
            self._buffer_ts = None
            try:
                await self._client.write_gatt_char(PMD_CONTROL, ACC_STOP, response=True)
            except Exception:
                pass
        raise PmdError("устройство отказало на всех частотах акселерометра")

    async def stop(self) -> None:
        self._flush()
        try:
            await self._client.write_gatt_char(PMD_CONTROL, ACC_STOP, response=True)
        except Exception:
            pass
        try:
            await self._client.stop_notify(PMD_DATA)
        except Exception:
            pass
        try:
            await self._client.stop_notify(PMD_CONTROL)
        except Exception:
            pass
