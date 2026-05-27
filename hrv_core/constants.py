"""Общие константы HRV pipeline и источников."""

from pathlib import Path

# GATT UUID характеристики Heart Rate Measurement (BLE, Polar H10 и др.)
HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# Скользящее окно RR-интервалов для расчёта RMSSD (сек)
RMSSD_WINDOW_SEC = 60

# Горизонт графика RR в CLI matplotlib (сек «назад» от текущего момента)
PLOT_RR_SEC = 60

# Горизонт графика RMSSD в CLI matplotlib (сек «назад»)
PLOT_RMSSD_SEC = 300

# Число последних точек RMSSD для session baseline и порога drift
BASELINE_SAMPLES = 60

# Окно усреднения RR для метрики smoothed_rr (сек)
SMOOTHED_RR_WINDOW_SEC = 15

# Drift: RMSSD ниже baseline × этого коэффициента (~20% падение)
DRIFT_THRESHOLD = 0.80

# Минимальный интервал между повторными событиями drift (сек)
DRIFT_COOLDOWN_SEC = 120

# Пауза перед повторным BLE-подключением после обрыва (сек)
RECONNECT_DELAY = 3.0

# Длительность прогона mock-verify без UI (сек)
MOCK_VERIFY_SEC = 300.0

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
SESSION_TAGS = ("meditation", "focus", "rest", "scroll", "untagged")
