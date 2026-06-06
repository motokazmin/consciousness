# consciousness

Экспериментальный **HRV Awareness Monitor** (Polar H10 / mock, RMSSD, кластеризация).

- **Архитектура и назначение:** [ARCHITECTURE.md](ARCHITECTURE.md)
- **Детали MVP** (веб-UI, BLE/ANT+, baseline, mock): [hrv_mvp.md](hrv_mvp.md)
- **Веб-аудио (Web Audio):** [ARCHITECTURE.md § Веб-аудио](ARCHITECTURE.md#веб-аудио-где-генерируется-звук)

## Быстрый старт

```bash
# зависимости
pip install -r requirements.txt

# веб-интерфейс (форма сессии, графики, архив) — основной путь
python -m hrv_web
# откройте в браузере: http://127.0.0.1:8765/
# Укажите участника, тип активности, источник (Mock / BLE / …), при необходимости MAC и длительность.

# кластеризация по накопленным сессиям (offline)
python cluster.py
```

ANT+ (опционально): `pip install 'openant>=1.3'`, см. поле `source` в веб-форме (`ant`, `ble_ant_fallback`) в [hrv_mvp.md](hrv_mvp.md).

**BLE / BlueZ:** если при подключении видите `Bleak requires BlueZ >= 5.55`, в системе старая BlueZ (часто 5.53). Переустановите зависимости: `pip install 'bleak>=0.22.3,<1'`, либо обновите BlueZ в ОС.
