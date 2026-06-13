"""Общие константы HRV pipeline и источников."""

from pathlib import Path

# GATT UUID характеристики Heart Rate Measurement (BLE, Polar H10 и др.)
HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# Polar PMD (Measurement Data) — нестандартный сервис для потока ACC и др.
PMD_SERVICE_UUID = "fb005c80-02e7-f387-1cad-8acd2d8df0c8"
PMD_CONTROL_UUID = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA_UUID = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"

# Команда старта стрима ACC (тип=0x02, диапазон 8G, 50Hz) — требует
# проверки на реальном H10, точные байты могут отличаться по модели/прошивке.
PMD_ACC_START = bytearray(
    [0x02, 0x02, 0x00, 0x01, 0xC8, 0x00, 0x01, 0x01, 0x10, 0x00, 0x02, 0x01, 0x08, 0x00]
)
PMD_ACC_STREAM_TYPE = 0x02  # байт 0 в data-уведомлении PMD для ACC

# ACC: частота сэмплов (Hz), используемая при парсинге PMD-фреймов
ACC_SAMPLE_RATE_HZ = 200

# Respiration: окно накопления ACC-сигнала для оценки дыхания (сек)
RESP_WINDOW_SEC = 30

# Respiration: полоса частот дыхательной волны (Hz) — 6..30 вдохов/мин
RESP_BAND_HZ = (0.1, 0.5)

# Respiration: минимальное расстояние между пиками дыхательной волны (сек)
RESP_MIN_PEAK_DISTANCE_SEC = 1.5

# Respiration: число точек волны, отдаваемых на фронт за один кадр
RESP_WAVEFORM_POINTS = 50

# Скользящее окно RR-интервалов для расчёта RMSSD (сек)
RMSSD_WINDOW_SEC = 60

# Число последних точек RMSSD для session baseline и порога drift
BASELINE_SAMPLES = 60

# Минимум накопленных точек RMSSD в сессии для перехода на session baseline
BASELINE_MIN_SAMPLES = BASELINE_SAMPLES // 2  # 30

# Окно усреднения RR для метрики smoothed_rr (сек)
SMOOTHED_RR_WINDOW_SEC = 15

# Drift: RMSSD ниже baseline × этого коэффициента (~20% падение)
DRIFT_THRESHOLD = 0.80

# Минимальный интервал между повторными событиями drift (сек)
DRIFT_COOLDOWN_SEC = 120

# Пауза перед повторным BLE-подключением после обрыва (сек)
RECONNECT_DELAY = 3.0

# BLE/ANT+: нет RR дольше этого — watchdog считает потерю потока (сек)
RR_WATCHDOG_SEC = 12.0

# BLE: ожидание первого RR после connect до предупреждения/реконнекта (сек)
BLE_FIRST_RR_GRACE_SEC = 15.0

# BLE→ANT fallback: ждать RR по Bluetooth перед переключением на донгл (сек)
ANT_FALLBACK_WAIT_SEC = 30.0

# Таймаут одной попытки client.start_notify (сек)
START_NOTIFY_TIMEOUT = 25.0

# Число повторов start_notify при ошибке GATT
START_NOTIFY_RETRIES = 3

# Текст подсказки, если датчик занят другим приложением
BUSY_DEVICE_HINT = (
    "Подсказка: если датчик уже подключён в Polar Flow или к другому клиенту, "
    "отключите там или закройте приложение."
)

# Файл SQLite с сессиями, точками HRV и baseline по часам
DB_PATH = Path("hrv_data.sqlite")

# Допустимые метки типа активности при старте сессии
# Источник правды — hrv_core/session_types.py
from hrv_core.session_types import SESSION_SLUGS as SESSION_TAGS  # noqa: F401
