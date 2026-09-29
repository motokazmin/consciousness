"""Протокол PMD (Polar Measurement Data) Polar H10: акселерометр по тому же BLE-соединению.

Отвечает на задачу «дыхание из акселерометра, RR неприкосновенен» (ADR-003 в
research/, сюда не заглядываем — только код). Выделено из одноразовой пробы
`research/tools/pmd_probe.py`, которая теперь импортирует отсюда.

Разбор кадров и маски — чистые функции без bleak: их можно тестировать без
живого устройства. `PmdAccStream` — тонкая обвязка вокруг уже подключённого
`BleakClient` (второе BLE-соединение H10 не даёт, поэтому клиент общий с RR).

Только акселерометр (бит 2 маски). ЭКГ (бит 0) не трогаем.

**Состояние по живым прогонам на H10 (прошивка 5.0.0).** Первый прогон отдал
SUCCESS на команду старта, но не дал ни одного кадра данных. Тогда была
гипотеза, что не хватает настройки `channels` (тип 0x04) — **эта гипотеза
опровергнута вторым прогоном**: как только `channels` добавили в команду,
устройство стало отвечать `INVALID_PARAMETER` (код 5) на всех частотах.
Устройство `channels` вообще не объявляет в ответе на запрос настроек — значит
это лишнее поле, а не пропущенное. Команда старта собирается строго из
настроек, которые устройство само объявило (`parse_measurement_settings` →
`build_acc_start_command`), без единого поля сверху.

Второй прогон также дал прямое свидетельство, что первый старт (без channels)
и правда запускал измерение: ответ на команду стопа после него — SUCCESS
(`f003020000`), тогда как стоп при незапущенном измерении в неудачном прогоне
вернул другой код (6). То есть договорённость о настройках и старт — рабочая
часть протокола; открытый вопрос — только доставка BLE-уведомлений с данными
на характеристику `PMD_DATA` (см. `_ensure_data_after_start`: три варианта
подписки по возрастанию инвазивности, ни один пока не проверен на живом
устройстве).

`PmdAccStream` принимает необязательный `on_event` — хук, который получает
построчную диагностику (сырой hex, статусы, разбор первых кадров). В боевой
записи (`hrv_core/sources.py`) он не задан — тихо, лишнего не пишет. В
`python -m hrv_core.pmd_check` он подключён к печати: одного прогона должно
хватить, чтобы увидеть, на каком шаге разбор разошёлся с реальностью.
"""

from __future__ import annotations

import logging
import struct
import time
from typing import Callable

log = logging.getLogger(__name__)

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

# Типы настроек в ответе на запрос "получить настройки измерения" (control
# point 0x01, ACC_TYPE) и в самой команде старта (0x02, ACC_TYPE, ...).
# `channels` (тип 0x04) сюда сознательно не входит: H10 его не объявляет, а
# при отправке в команде старта отвечает INVALID_PARAMETER — см. модульный
# docstring. Если какое-то устройство объявит незнакомый тип, он всё равно
# попадёт в команду старта (см. `build_acc_start_command`) — просто без
# читаемого имени в `describe_settings`.
SETTING_SAMPLE_RATE = 0x00
SETTING_RESOLUTION = 0x01
SETTING_RANGE = 0x02
_SETTING_NAMES = {
    SETTING_SAMPLE_RATE: "sample rate (Гц)",
    SETTING_RESOLUTION: "resolution (бит)",
    SETTING_RANGE: "range (g)",
}

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

# Коды статуса в ответе control point на запись команды (0xF0 op type error
# [more]) — один байт (`error`), не два. Имена — по публичному протоколу Polar
# PMD/BLE SDK. Опознаны эмпирически на живом H10 (прошивка 5.0.0), второй
# прогон: 0 (SUCCESS на старт без channels и на стоп реально шедшего
# измерения — ответ `f003020000`), 5 (INVALID_PARAMETER — прилетел на старт с
# лишней настройкой channels), 6 (ALREADY_IN_STATE — прилетел на стоп, когда
# измерение не было запущено, ответ `f003020600`). Остальные коды — из
# документации, на этом устройстве не наблюдались. Неизвестный код — не
# ошибка разбора, просто печатаем число как есть.
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
    """Команда control point: запуск акселерометра на заданной частоте.

    Три настройки (sample rate, resolution, range) — без channels: устройство
    его не объявляет и отвергает команду, если оно там есть (см. модульный
    docstring). `PmdAccStream` не использует эту функцию напрямую — собирает
    команду через `build_acc_start_command` из объявленных устройством
    настроек. Оставлена ради `research/tools/pmd_probe.py`, который
    импортирует её отдельно.
    """
    return bytes([
        0x02, ACC_TYPE,
        0x00, 0x01, hz & 0xFF, (hz >> 8) & 0xFF,               # sample rate
        0x01, 0x01, resolution_bits & 0xFF, (resolution_bits >> 8) & 0xFF,
        0x02, 0x01, range_g & 0xFF, (range_g >> 8) & 0xFF,     # range
    ])


def parse_measurement_settings(raw: bytes) -> dict[int, list[int]]:
    """Тело ответа на запрос настроек измерения → {тип настройки: [значения]}.

    `raw` — то, что идёт СРАЗУ ПОСЛЕ 5-байтного заголовка ответа (0xF0, 0x01,
    measurement_type, error, more): вызывающая сторона обрезает заголовок и
    проверяет error сама. Формат тела — последовательность блоков
    [тип(1 байт)][число значений(1 байт)][значения (uint16 LE каждое)].
    Подтверждено на живом H10: ответ `f0010200000004190032006400c800…` разбирается
    этой схемой ровно в `{0: [25,50,100,200], 1: [16], 2: [2,4,8]}` — счётчик
    значений однобайтовый, не uint16, как предполагалось раньше.
    Обрезанный/битый хвост не роняет разбор — просто не попадает в результат.
    """
    settings: dict[int, list[int]] = {}
    pos = 0
    while pos + 2 <= len(raw):
        setting_type = raw[pos]
        count = raw[pos + 1]
        pos += 2
        need = count * 2
        if need == 0 or pos + need > len(raw):
            break
        values = [
            raw[pos + 2 * i] | (raw[pos + 2 * i + 1] << 8) for i in range(count)
        ]
        pos += need
        settings[setting_type] = values
    return settings


def describe_settings(settings: dict[int, list[int]]) -> str:
    """Настройки, объявленные устройством, человеку — для pmd_check."""
    if not settings:
        return "пусто"
    parts = []
    for setting_type in sorted(settings):
        name = _SETTING_NAMES.get(setting_type, f"тип 0x{setting_type:02x}")
        parts.append(f"{name}={settings[setting_type]}")
    return ", ".join(parts)


def more_frames_pending(raw: bytes) -> bool:
    """Флаг «будут ещё кадры ответа» в ответе control point.

    Заголовок ответа — 5 байт: 0xF0, op, type, error(1 байт), more(1 байт) —
    подтверждено на живом устройстве несколькими ответами (настройки — 5-байтный
    заголовок + тело; стоп — ровно 5 байт, `f003020000`/`f003020600`; отказ
    старта — 4 байта без `more` вовсе, `f0020205`). Флаг — байт по фиксированному
    смещению 4, а не последний байт кадра: на длинных ответах (например, тело
    настроек) последний байт — это данные, не флаг. Кадр короче 5 байт (нет
    байта `more`) трактуем как «продолжения не будет».
    """
    return len(raw) >= 5 and raw[4] == 1


def _resolve_hz(declared: list[int] | None) -> int:
    """Частота: минимальная объявленная не ниже ACC_DEFAULT_HZ, иначе ближайшая
    большая; если ничего не объявлено (или запрос настроек ничего не вернул) —
    ACC_DEFAULT_HZ."""
    if not declared:
        return ACC_DEFAULT_HZ
    if ACC_DEFAULT_HZ in declared:
        return ACC_DEFAULT_HZ
    at_least = [v for v in declared if v >= ACC_DEFAULT_HZ]
    return min(at_least) if at_least else max(declared)


def _resolve_range(declared: list[int] | None) -> int:
    """Range: предпочтительно ACC_RANGE_G, иначе максимум из объявленных."""
    if not declared:
        return ACC_RANGE_G
    return ACC_RANGE_G if ACC_RANGE_G in declared else max(declared)


def build_acc_start_command(settings: dict[int, list[int]]) -> bytes:
    """Собрать команду старта из настроек, которые устройство само объявило —
    и ТОЛЬКО из них, ни одним полем больше.

    Раньше сюда безусловно добавлялось `channels` (тип 0x04) — гипотеза, что
    его пропуск и есть причина отказа. Гипотеза опровергнута: H10 эту настройку
    не объявляет вовсе, а если её всё-таки положить в команду, устройство
    отвечает INVALID_PARAMETER на всех частотах. Поэтому в команду попадают
    только типы, реально присутствующие в `settings`. Для типов, о выборе
    которых у нас есть мнение (sample rate, range), решаем по правилу; для
    resolution и любого другого объявленного типа берём первое объявленное
    значение как есть.

    Если `settings` пуст (устройство не ответило или ответ не разобрался),
    откатываемся на жёстко зашитый набор по умолчанию из трёх настроек —
    ровно ту команду, которую устройство один раз уже приняло (SUCCESS).
    """
    chosen: dict[int, int] = {
        SETTING_SAMPLE_RATE: _resolve_hz(settings.get(SETTING_SAMPLE_RATE)),
        SETTING_RESOLUTION: (settings.get(SETTING_RESOLUTION) or [ACC_RESOLUTION_BITS])[0],
        SETTING_RANGE: _resolve_range(settings.get(SETTING_RANGE)),
    }
    for setting_type, values in settings.items():
        if setting_type not in chosen and values:
            chosen[setting_type] = values[0]
    body = bytearray([0x02, ACC_TYPE])
    for setting_type in sorted(chosen):
        value = chosen[setting_type]
        body += bytes([setting_type, 0x01, value & 0xFF, (value >> 8) & 0xFF])
    return bytes(body)


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


_warned_bad_body = False  # чтобы не заспамить лог одним и тем же выводом на каждый кадр


def parse_acc_frame(payload: bytes) -> list[tuple[int, int, int]]:
    """Кадр PMD-акселерометра → список отсчётов (x, y, z) в mg.

    Формат: тип(1) + timestamp(8) + frame type(1) + тело. Тело — плоская
    последовательность int16 LE троек (x, y, z), без какой-либо упаковки —
    подтверждено на живом H10 (прошивка 5.0.0): 216 байт тела = 36 отсчётов,
    первые совпали с ручным разбором hex с точностью до счёта. Раньше здесь
    была ветка побитовой распаковки дельт по frame type 0x01 — гипотеза,
    ничем не подтверждённая и опровергнутая первым же живым потоком (см.
    задачу, git-историю). Frame type не влияет на разбор тела: наблюдался
    только 0x01, и тело в нём — такая же плоская последовательность.
    """
    if len(payload) < 10 or payload[0] != ACC_TYPE:
        return []
    body = payload[10:]
    if len(body) % 6 != 0:
        global _warned_bad_body
        if not _warned_bad_body:
            log.warning(
                "тело кадра акселерометра (%d байт) не кратно 6 — формат не распознан, hex: %s",
                len(body), hexdump(payload),
            )
            _warned_bad_body = True
        return []
    n = len(body) // 6
    return [struct.unpack_from("<hhh", body, i * 6) for i in range(n)]


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
            # Оба наблюдавшихся типа кадра несут плоские int16-тройки; на
            # живом H10 приходит 0x01. Слово "delta" из прежней (опровергнутой)
            # гипотезы убрано, чтобы вывод не вводил в заблуждение.
            type_name = f"0x{frame_type:02x}" if frame_type is not None else "?"
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

    async def _await_control_continuation(self, timeout_per_frame: float = 1.0) -> None:
        """Не считать диалог законченным, пока в последнем полученном кадре
        control point взведён флаг «больше кадров» (`more_frames_pending`).
        Кадры уже попадают в `self._control_replies` через `_on_control` (и
        печатаются там же) — здесь только решаем, ждать ли ещё."""
        import asyncio

        while self._control_replies and more_frames_pending(self._control_replies[-1]):
            before = len(self._control_replies)
            deadline = time.time() + timeout_per_frame
            while len(self._control_replies) == before and time.time() < deadline:
                await asyncio.sleep(0.02)
            if len(self._control_replies) == before:
                break  # продолжение не пришло за отведённое время

    async def _request_settings(self) -> dict[int, list[int]]:
        """Запросить настройки акселерометра (control point 0x01, ACC_TYPE) —
        безусловно, независимо от того, есть ли акселерометр в маске
        возможностей (см. модульный docstring: маска SUCCESS не гарантирует,
        что список настроек и старт по ним совпадают)."""
        import asyncio

        self._control_replies.clear()
        try:
            await self._client.write_gatt_char(
                PMD_CONTROL, bytes([0x01, ACC_TYPE]), response=True
            )
        except Exception as exc:
            if is_pairing_error(exc):
                raise PmdPairingRequiredError(PAIRING_HINT) from exc
            raise PmdError(f"запрос настроек акселерометра не удался: {exc}") from exc
        await asyncio.sleep(1.0)
        await self._await_control_continuation()

        settings: dict[int, list[int]] = {}
        if not self._control_replies:
            self._on_event("устройство не ответило на запрос настроек акселерометра")
            return settings
        first = self._control_replies[0]
        self._on_event(f"ответ на запрос настроек, первый кадр: {hexdump(first)}")
        if len(first) < 4 or first[0] != 0xF0 or first[1] != 0x01 or first[2] != ACC_TYPE:
            self._on_event("ответ на запрос настроек не распознан (неожиданный заголовок)")
            return settings
        status = first[3]
        if status != 0:
            self._on_event(f"запрос настроек отклонён: {describe_status(status)}")
            return settings
        # Заголовок первого кадра — 5 байт (F0, op, type, error, more); тело
        # начинается сразу за ним. Кадр-продолжение (если был — флаг `more` в
        # первом кадре) несёт только хвост параметров без повторного
        # заголовка — предположение, не проверенное на живом устройстве
        # (реальный ответ уместился в один кадр).
        head_len = 5 if len(first) >= 5 else 4
        payload = first[head_len:] + b"".join(self._control_replies[1:])
        settings = parse_measurement_settings(payload)
        self._on_event(f"настройки, объявленные устройством: {describe_settings(settings)}")
        return settings

    async def _attempt_start(self, command: bytes, wait_s: float = 1.2) -> tuple[bool, int | None]:
        """Отправить одну команду старта, подождать ack и данные, вернуть
        (принято, статус). При отказе сама шлёт ACC_STOP и чистит буфер."""
        import asyncio

        self._frames_seen = 0
        self._control_replies.clear()
        # Отсчёты, пришедшие при предыдущей (отклонённой) попытке, не должны
        # попасть в пачку согласованной — иначе load_accel_samples разложит их
        # по времени с чужим шагом. total_frames/total_samples — счётчики за
        # всё время жизни потока, их не трогаем: это диагностика для
        # pmd_check, ей важно и то, что шло на отклонённой попытке.
        self._buffer = []
        self._buffer_ts = None
        try:
            await self._client.write_gatt_char(PMD_CONTROL, command, response=True)
        except Exception as exc:
            if is_pairing_error(exc):
                raise PmdPairingRequiredError(PAIRING_HINT) from exc
            self._on_event(f"команда старта {hexdump(command)}: запись не удалась: {exc}")
            return False, None
        await asyncio.sleep(wait_s)
        await self._await_control_continuation()
        status = start_ack_status(self._control_replies)
        accepted = status == 0 or (status is None and self._frames_seen > 0)
        self._on_event(
            f"команда старта {hexdump(command)}: статус={describe_status(status)}, "
            f"кадров данных за {wait_s:.1f}s={self._frames_seen} → "
            f"{'принято' if accepted else 'отказ'}"
        )
        if not accepted:
            self._buffer = []
            self._buffer_ts = None
            try:
                await self._client.write_gatt_char(PMD_CONTROL, ACC_STOP, response=True)
            except Exception:
                pass
        return accepted, status

    async def _fallback_frequency_sweep(self, skip_hz: int | None) -> tuple[bool, float, bytes]:
        """Запасной путь на случай отказа: перебор частот по жёстко зашитым
        настройкам (sample rate/resolution/range, без channels — см.
        build_acc_start_command), а не по объявленным устройством. `skip_hz` —
        то, что уже пробовали через объявленные настройки, повторно не шлём.
        Возвращает и принятую командy — она нужна дальше для варианта 3
        доставки уведомлений (повторный старт той же командой)."""
        for hz in self._candidate_hz:
            if hz == skip_hz:
                continue
            command = build_acc_start_command({SETTING_SAMPLE_RATE: [hz]})
            accepted, status = await self._attempt_start(command)
            if accepted:
                return True, float(hz), command
        return False, 0.0, b""

    async def _ensure_data_after_start(self, start_command: bytes) -> None:
        """Доставка уведомлений — открытый вопрос, не переговоры о настройках.

        Второй живой прогон дал прямое свидетельство, что старт (без channels)
        реально запускал измерение: ответ на стоп был SUCCESS (`f003020000`),
        а не ALREADY_IN_STATE (см. модульный docstring). Значит, договор о
        настройках прошёл — а BLE-уведомления с данными всё равно не дошли.
        Три варианта по возрастанию инвазивности, каждый явно назван в
        on_event вместе с числом полученных кадров — по одному прогону должно
        быть видно, какой (если хоть один) сработал. Ни один не проверен на
        живом устройстве."""
        import asyncio

        if self._frames_seen > 0:
            self._on_event(f"вариант 1 (подписка до старта): данные уже идут, кадров={self._frames_seen}")
            return
        await asyncio.sleep(5.0)
        if self._frames_seen > 0:
            self._on_event(
                f"вариант 1 (подписка до старта) сработал за 5с после старта, "
                f"кадров={self._frames_seen}"
            )
            return
        self._on_event("вариант 1 (подписка до старта): кадров нет за 5с — пробую вариант 2")

        self._on_event(
            "вариант 2: переподписка на PMD_DATA (stop_notify → start_notify) "
            "при уже идущем измерении"
        )
        try:
            await self._client.stop_notify(PMD_DATA)
            await self._client.start_notify(PMD_DATA, self._on_data)
        except Exception as exc:
            self._on_event(f"вариант 2: переподписка не удалась: {exc} — пробую вариант 3")
        else:
            await asyncio.sleep(5.0)
            if self._frames_seen > 0:
                self._on_event(f"вариант 2 сработал: кадров={self._frames_seen}")
                return
            self._on_event("вариант 2: кадров нет за 5с после переподписки — пробую вариант 3")

        self._on_event(
            "вариант 3: stop_notify(PMD_DATA) → команда старта заново → "
            "start_notify(PMD_DATA) (подписка строго после запуска измерения)"
        )
        try:
            await self._client.stop_notify(PMD_DATA)
        except Exception as exc:
            self._on_event(f"вариант 3: stop_notify(PMD_DATA) не удался: {exc}")
            return
        self._frames_seen = 0
        self._control_replies.clear()
        try:
            await self._client.write_gatt_char(PMD_CONTROL, start_command, response=True)
        except Exception as exc:
            self._on_event(f"вариант 3: повторная команда старта не удалась: {exc}")
            return
        await self._await_control_continuation()
        status = start_ack_status(self._control_replies)
        self._on_event(f"вариант 3: повторный старт, статус={describe_status(status)}")
        try:
            await self._client.start_notify(PMD_DATA, self._on_data)
        except Exception as exc:
            self._on_event(f"вариант 3: start_notify(PMD_DATA) не удался: {exc}")
            return
        await asyncio.sleep(5.0)
        if self._frames_seen > 0:
            self._on_event(f"вариант 3 сработал: кадров={self._frames_seen}")
        else:
            self._on_event("ни один из трёх вариантов подписки не дал кадров данных")

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
        self._on_event(
            f"акселерометр в маске возможностей: {feats}" if "акселерометр" in feats
            else f"акселерометр не в маске ({feats or 'пусто'}) — запрашиваю настройки всё равно"
        )

        # Обязательный и безусловный шаг, а не запасной путь на случай пустой
        # маски: маска SUCCESS не гарантирует, что список настроек будет
        # получен (устройство может промолчать) или что старт по ним пройдёт.
        settings = await self._request_settings()
        if not settings and "акселерометр" not in feats:
            raise PmdError(f"акселерометр не заявлен ни в маске PMD, ни в настройках: {hexdump(raw)}")

        await self._client.start_notify(PMD_DATA, self._on_data)

        hz = _resolve_hz(settings.get(SETTING_SAMPLE_RATE))
        command = build_acc_start_command(settings)
        accepted, status = await self._attempt_start(command)
        if not accepted:
            self._on_event(
                f"старт по объявленным настройкам ({describe_settings(settings)}) отклонён "
                f"({describe_status(status)}) — откатываюсь на перебор частот"
            )
            accepted, hz, command = await self._fallback_frequency_sweep(skip_hz=hz)
            if not accepted:
                raise PmdError("устройство отказало на всех частотах акселерометра")

        self._hz = float(hz)
        await self._ensure_data_after_start(command)
        return self._hz

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
