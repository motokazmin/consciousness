"""Общие константы HRV pipeline и источников."""

from pathlib import Path

HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
RMSSD_WINDOW_SEC = 60
PLOT_RR_SEC = 60
PLOT_RMSSD_SEC = 300
BASELINE_SAMPLES = 60
DRIFT_THRESHOLD = 0.80
DRIFT_COOLDOWN_SEC = 120
RECONNECT_DELAY = 3.0
MOCK_VERIFY_SEC = 300.0
RR_WATCHDOG_SEC = 12.0
BLE_FIRST_RR_GRACE_SEC = 15.0
ANT_FALLBACK_WAIT_SEC = 30.0
START_NOTIFY_TIMEOUT = 25.0
START_NOTIFY_RETRIES = 3
BUSY_DEVICE_HINT = (
    "Подсказка: если датчик уже подключён в Polar Flow или к другому клиенту, "
    "отключите там или закройте приложение."
)
DB_PATH = Path("hrv_data.sqlite")

SESSION_TAGS = ("meditation", "focus", "rest", "scroll", "untagged")
