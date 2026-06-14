# HRV Awareness Monitor — MVP

## Контекст и цель

Система мониторинга осознанности в реальном времени на основе HRV (Heart Rate Variability).
Цель MVP — проверить гипотезу: **можно ли по одному RMSSD-сигналу надёжно выделить
кластеры состояний пользователя, соответствующие его субъективным ощущениям.**

Пользователь проводит контролируемые эксперименты (медитация, фокусная работа, скроллинг)
с тегированием сессий. После накопления данных — кластеризация HDBSCAN только по RMSSD,
визуальная валидация: попадают ли tagged-сессии в ожидаемые кластеры.
Час суток сохраняется в БД и используется на scatter-графике (ось X) для визуального
анализа активности по времени, но в признаки алгоритма не включается.

---

## Стек

- **Python 3.12**, Ubuntu
- **bleak** (в `requirements.txt`: **0.22.x**, не 1.x+) — BLE к Polar H10; **bleak ≥1** требует **BlueZ ≥5.55**, на Ubuntu 20.04 часто **5.53** — тогда `pip install 'bleak>=0.22.3,<1'`
- **numpy** — вычисления RMSSD и кластеризация
- **matplotlib** — только offline-графики в `cluster.py`
- **FastAPI + uvicorn** — единственный UI: форма сессии, WebSocket, архив; фронт: **uPlot** (CDN)
- **SQLite** — локальное хранилище точек, сессий и персонального baseline по часу
- **hdbscan + scikit-learn** — кластеризация по RMSSD (`cluster.py`)
- **notify-send** — опционально в `HRVSessionState` (в веб-сессии отключён)

Зависимости: файл **`requirements.txt`** в корне проекта. Установка:

```bash
pip install -r requirements.txt
```

---

## Архитектура

### Абстракция источника данных

```python
class HRVSource(ABC):
    def start(self, callback): ...  # callback(rr_ms: float, ts: float)
    def stop(self): ...
```

Реализации с общим интерфейсом:

| Класс | Назначение |
|---|---|
| `MockHRVSource` | Симуляция RR через AR(1) процесс, цикл состояний focused→drift→recovering |
| `PolarH10Source` | BLE-подключение к реальному Polar H10, парсинг GATT 0x2A37 |

Переключение — поле **`source`** в веб-форме: `mock`, `ble`; для BLE — MAC в **`address`**.

### Поток данных (веб)

```
HRVSource (BLE asyncio thread / mock thread)
    ↓  callback(rr_ms, ts)  — per beat
SessionManager.on_beat() → HRVSessionState.process_beat()
    ├── compute_rmssd(), drift check
    ├── SQLite INSERT (hrv_points)
    └── queue → WebSocket { type: "beat", r, m, sr, rn, bl, drift, … }

FastAPI + браузер (app.js)
    ← WebSocket beat-кадры
    — uPlot: RR + RMSSD (live)
    — Web Audio биофидбек (опционально)
    — guided mp3-фразы (meditation / relaxation)
```

BLE asyncio loop живёт в daemon-треде. Обмен с UI — через `queue.Queue` (WebSocket) и SQLite (`check_same_thread=False`).

### Аудио-биофидбек (веб)

Вкладка «Биофидбек»: Web Audio в браузере ([`hrv_audio_engine.js`](hrv_web/static/hrv_audio_engine.js)) — пульс на каждый RR, фоновая текстура, трансовый pad по `rmssd_normalized`. Включается чекбоксом «Аудио-биофидбек» в форме сессии. Подробнее — раздел [Веб-аудио](#веб-аудио-где-генерируется-звук) и [ARCHITECTURE.md](ARCHITECTURE.md#веб-аудио-где-генерируется-звук).

### RMSSD

```python
def compute_rmssd(rr_list):
    diffs = np.diff(rr_list)
    return float(np.sqrt(np.mean(diffs ** 2)))
```

Скользящее окно 60 секунд. Пересчёт после каждого нового RR-интервала.

### Baseline и drift

**RMSSD** в приложении считается по **скользящему окну последних 60 с** RR-интервалов (см. выше). **Baseline** — это опорное значение RMSSD, с которым сравнивается **текущий** RMSSD, чтобы поймать резкое **понижение** вариабельности внутри сессии.

| Термин | Смысл | Когда используется |
|--------|--------|---------------------|
| **Session baseline** (baseline сессии) | Среднее арифметическое по **последним до 60** уже накопленным точкам RMSSD в этой сессии (последние значения в истории `rmssd_history`). | Как только в сессии есть **≥ 30** точек RMSSD — именно это среднее становится baseline для проверки drift. |
| **Persistent baseline** (персональный baseline по часу) | Среднее RMSSD **по вашему локальному часу суток (0–23)**, накопленное в таблице **`baseline`** из прошлых сессий. | Пока в **текущей** сессии ещё **меньше 30** точек RMSSD, для порога drift используется persistent baseline **на час старта** (если в БД уже есть запись для этого часа). Если и его нет — уведомления о drift не считаются, пока не накопится 30 точек. |
| **Drift** | Событие: **текущий RMSSD < baseline × 0,80** (то есть ниже опоры примерно на **20%**). | Не чаще **одного срабатывания в 120 с** (антидребезг); счётчик **`drift_events`** в сессии. В веб-сессии **`notify-send` отключён** (`desktop_notify=False`); флаг drift уходит в WebSocket. |

**Интерпретация (осторожно):** drift — это **не диагноз и не «плохая медитация»**, а сигнал «по сравнению с вашей недавней (или типичной для этого часа) линией RMSSD сейчас заметно ниже». Физиология и контекст индивидуальны.

**Live-графики (веб):** RR и RMSSD (uPlot); пунктирные линии session / persistent baseline и смена цвета по drift **не выведены**. Флаг drift — в WebSocket (`drift` в кадре `beat`). Если в форме задана **длительность сессии (мин)**, оси — **секунды от старта** \(0…T\); без длительности — скользящее окно (последние ~60 с для RR и ~5 мин для RMSSD).

### SQLite-схема

```sql
sessions        (id, tag, source, session_name, participant, started, ended, drift_events)
hrv_points      (id, session_id, ts, rr_ms, rmssd)
baseline        (hour, rmssd_mean, n_samples, updated_at)  -- hour 0–23, локальное время
session_types   (slug, label, phrase_prefix, mock_profile, cluster_marker, is_custom)
meditation_phrase_log (…)  -- лог guided mp3-фраз (meditation / relaxation)
```

**`tag`** — slug типа активности в `sessions` (строка): системные — `meditation`, `relaxation`, `test`, `focus`, `scroll`, `untagged`. Справочник для UI и mock-профилей — таблица **`session_types`** (seed из `hrv_core/session_types.py` при первом создании БД). В веб-форме можно добавить свой тип («Новая активность…») — он сохраняется через `POST /api/session-types`. Старый slug **`rest`** переименован в **`relaxation`**.

**Веб: типы активности**

| Действие | API |
|----------|-----|
| Список для формы и фильтров | `GET /api/session-types` → `{ session_types: [{ slug, label, phrase_prefix, mock_profile, is_custom }, …] }` |
| Новый пользовательский тип | `POST /api/session-types` — body `{ slug, label }` |
| Удалить пользовательский | `DELETE /api/session-types/{slug}` (системные — 403) |

При старте сессии `POST /api/sessions` принимает `tag` (slug), `participant`, `source`, опционально `session_name`, `address`, `minutes`. Полный список endpoint — [ARCHITECTURE.md § Веб-API](ARCHITECTURE.md#веб-api-кратко).
`source` — строка источника, например: `"mock"`; `"Polar H10  AA:BB:…"` (BLE).

### MockHRVSource

Генерирует реалистичные RR через AR(1) с медленным переходом состояний:

```
focused    (rmssd_target=58, noise=7,  duration=90s)
drift      (rmssd_target=20, noise=3,  duration=55s)
recovering (rmssd_target=38, noise=11, duration=70s)
```

Каждое новое RR: AR(1) `mean_rr + 0.5*(prev_rr - mean_rr) + gauss(0, σ)`, где
`σ` зависит от переданного в шаг целевого RMSSD (в коде: `max(1, rmssd_target)/√2 * 0.7`),
а не от поля `noise` состояния — поле `noise` задаёт только турбулентность целевого RMSSD (`noisy_target`).
Плавное движение current_rmssd к target: `current += (target - current) * 0.04`.

Если **`tag = meditation`** и источник **mock**, включается **профиль медитации**: без цикла focused/drift/recovering; на `mean_rr` накладывается **RSA** (медленная синусоида ~8,5–11,5 с), плавный дрейф центра и целевой RMSSD в зоне ~48–58 ms.

### BLE (Polar H10)

Characteristic `0x2A37` (Heart Rate Measurement), стандартный GATT.
RR в пакете: `rr_ms = raw * 1000 / 1024` (единицы 1/1024 с).

- Автоматическое переподключение при ошибках соединения.
- **`start_notify`:** таймаут и несколько попыток до полного сбоя сессии (GATT «зависания»).
- **Watchdog RR:** если за **12 с** не пришло ни одного распарсенного RR — разрыв и переподключение (silent gap / потеря уведомлений).
- **Первый RR:** если за **15 с** после connect не было ни одного RR — переподключение и подсказка, что датчик может быть занят другим клиентом (например Polar Flow).
- При типичных текстах ошибок подключения выводится та же подсказка про «занятый» H10.

Полностью устранить конфликт с другим BLE-клиентом программно нельзя — только быстрый fail и понятные сообщения.

---

## Веб-аудио: где генерируется звук

Генеративный звук синтезируется **только в браузере** (Web Audio API). Сервер аудио не передаёт: по WebSocket приходят метрики (`beat`), клиент воспроизводит звук локально.

**Файлы:** `hrv_web/static/hrv_audio_engine.js` (синтез), `hrv_web/static/app.js` (маршрутизация).

### Цепочка вызова

```
WebSocket { type: "beat" }
  → app.js: onWsMessage()
  → processAudioFrame(msg, i)
  → audioEngine.processFrame(frame)   // фон + трансовый pad
  → audioEngine.triggerBeat(rr_ms)    // щелчок на каждый удар
```

Кадр `beat` содержит: `r` (RR), `m` (RMSSD), `sr` (smoothed_rr), `rn` (rmssd_normalized), `bl` (session baseline), `drift`.

### 1. Звук на каждый пульс

| | |
|---|---|
| **Метод** | `HrvAudioEngine.triggerBeat(rrMs)` |
| **Когда** | На **каждый** RR из WebSocket, в **обоих** режимах |
| **Как** | Два одноразовых осциллятора (sine + triangle), AD-огибающая ~0.22 с |
| **Частота** | `_rrToPitch()` — пентатоника из `config.beat.pentatonic` по RR |
| **Выход** | `heartBeatGain` → `masterGain` → динамики |

Параметры: `config.beat.duration`, `gainPeak`, `pentatonic`.

### 2. Монотонный (фоновый) звук

| | |
|---|---|
| **Запуск** | `HrvAudioEngine.start()` → `_createTexture()` |
| **Текстуры** | `space_pad` (4 sawtooth), `sea_wave` (loop-шум + LFO), `tibetan_bowl` (5 sine + LFO) |
| **Когда играет** | Постоянно после «▶ Запустить звук», пока сессия активна |

**Режим «Дышащий Эмбиент»** (`smooth_rr`): громкость фона не меняется, меняется **cutoff lowpass** по `smoothed_rr` — `_setTextureCutoff()` в `processFrame()`.

**Режим «Трансовый Порог»** (`rmssd_trigger`): та же текстура играет тихо (`rmssdTrigger.textureGain`) через `rmssdMixGain`.

### 3. Звук на резкую смену состояния (только «Трансовый Порог»)

| | |
|---|---|
| **Режим** | `rmssd_trigger` |
| **Осцилляторы** | 4 sine на `padFreqs` — создаются в `start()`, крутятся всегда |
| **Триггер** | `processFrame()` при изменении `rmssd_normalized` |
| **Громкость** | `_rmssdToPadGain(rn)` → `padGain.setTargetAtTime(gain, t0, padSmoothSec)` |

Пороги (`config.rmssdTrigger`):

| Параметр | Значение | Смысл |
|----------|----------|--------|
| `threshold` | 1.0 | ниже — pad выключен |
| `rampStart` | 2.5 | начало нарастания |
| `rampEnd` | 3.5 | полная громкость `padGainMax` |
| `padSmoothSec` | 0.08 | скорость нарастания/затухания pad |

«Скачок» = рост `rn` выше `rampStart`; затухание — когда `rn` падает (тот же `padSmoothSec`).

### Режимы и микшер

```
masterGain
├── heartBeatGain          ← triggerBeat (всегда)
├── smoothMixGain          ← текстура в режиме smooth_rr
└── rmssdMixGain           ← текстура (тихо) + padGain (транс)
```

Переключение режимов: радиокнопки `audio_mode` в форме → `setMode()` кроссфейдом `rampSec`.

Подробнее: [ARCHITECTURE.md § Веб-аудио](ARCHITECTURE.md#веб-аудио-где-генерируется-звук).

---

## Что реализовано

- [x] Абстракция `HRVSource` — mock и BLE за единым интерфейсом
- [x] `MockHRVSource` — AR(1), цикл состояний, профиль meditation (RSA)
- [x] `PolarH10Source` — BLE, реконнект, `start_notify` с таймаутом/ретраями, watchdog по отсутствию RR
- [x] RMSSD на скользящем окне 60 с
- [x] **Веб-UI** (`python -m hrv_web`): live-графики RR + RMSSD (uPlot), архив, вкладка «Прогресс», guided mp3-фразы; типы активности в БД (`/api/session-types`)
- [x] SQLite: `sessions`, `hrv_points`, `baseline`, `session_types`, `meditation_phrase_log`; обновление baseline при завершении сессии
- [x] Drift detection (флаг в WebSocket; `notify-send` в веб-сессии отключён)
- [x] **Session summary** после «Стоп»: длительность, min/mean/max RMSSD, drift-события, **vs baseline**
- [x] `cluster.py` — HDBSCAN по RMSSD, matplotlib-графики (scatter, boxplot)
- [x] `requirements.txt`
- [x] **Web Audio биофидбек** — `hrv_audio_engine.js`: пульс, фоновая текстура, трансовый pad по RMSSD (см. раздел *Веб-аудио*)
- [x] `tests/` — unittest (pipeline, tags, delete_session)

---

## Что осталось (опционально)

Идеи на будущее: BLE-сканирование из веб-UI, настраиваемые таймауты reconnect/watchdog.

---

## Файловая структура

```
hrv_core/         — ядро: источники RR, RMSSD/drift, SQLite, session_types (seed)
tests/            — unittest (pipeline, tags, delete_session, …)
hrv_web/          — FastAPI + статика (единственный UI записи сессий)
  static/app.js              — SPA: форма, WebSocket, архив, прогресс
  static/hrv_audio_engine.js — Web Audio: triggerBeat, текстуры, rmssd pad
  static/meditation_engine.js — guided mp3-фразы (meditation / relaxation)
  static/index.html          — UI режимов «Дышащий Эмбиент» / «Трансовый Порог»
cluster.py        — offline-кластеризация + matplotlib-графики
requirements.txt  — зависимости pip
hrv_data.sqlite   — база данных (создаётся автоматически)
```

---

## Запуск

```bash
# зависимости (один раз или после смены Python)
pip install -r requirements.txt

# основной UI: форма параметров, live-графики uPlot, архив, прогресс
python -m hrv_web
# или: uvicorn hrv_web.server:app --host 127.0.0.1 --port 8765
# Откройте в браузере: http://127.0.0.1:8765/

# mock без железа: в форме source = Mock, выберите тип активности, ▶ Старт

# BLE: source = BLE Polar H10, укажите MAC (AA:BB:CC:DD:EE:FF)

# кластеризация (после накопления данных)
python cluster.py
python cluster.py --include-mock
python cluster.py --min-cluster-size 10
```

После «Стоп» в вебе — **session summary** в архиве (кнопка «Сводка»).

---

## Mock-данные в веб-UI

Симулятор (`source = mock`) нужен, чтобы отладить пайплайн (графики, БД, кластеризацию) без Bluetooth.

1. `python -m hrv_web` → http://127.0.0.1:8765/
2. Укажите участника, тип активности, **Mock (без железа)**, при желании длительность.
3. ▶ **Старт** — `MockHRVSource` пишет точки в `hrv_data.sqlite`; при **Стоп** обновляется baseline и доступна сводка.
4. Для **`meditation`** — профиль RSA (спокойный ритм); для остальных тегов — цикл focused → drift → recovering.
5. После нескольких mock-сессий: `python cluster.py --include-mock`.

### `python cluster.py --include-mock`

**Разведочный анализ уже сохранённых точек, включая mock-сессии.**

- По умолчанию `cluster.py` **отфильтровывает** строки, где `source` выглядит как mock, чтобы не смешивать синтетику с реальными измерениями. Флаг **`--include-mock`** отключает этот фильтр.
- Имеет смысл только если в базе есть mock-сессии, записанные через веб (`source = mock`).

**Когда использовать:** проверить, что пайплайн кластеризации и графики `cluster.py` работают на накопленных тестовых данных.

---

## Как читать live-графики (веб)

Важно: **RMSSD — это один числовой признак вариабельности сердечного ритма**, а не готовый измеритель «уровня осознанности». В этом MVP **осознанность** вы проверяете **как гипотезу**: совпадают ли *ваши* субъективные метки сессий с участками графика и с кластерами после накопления данных.

### RR (верхний график)

- **Без заданной длительности:** ось — последние ~60 с.
- **С длительностью:** ось — секунды от старта записи.
- **Что смотреть:** насколько линия **ровная или «дышит»** — амплитуда RR связана с RMSSD.
- **На mock:** смена «текстуры» при переходах focused → drift → recovering (кроме профиля meditation).

### RMSSD (нижний график)

Сначала прочитайте раздел **«Baseline и drift»** — там определены термины.

- Отображается **текущая кривая RMSSD** (uPlot); пунктир session / persistent baseline и цвет по drift **не выведены**.
- В полоске статистики — текущие RMSSD, HR, RN (нормализованный RMSSD), session baseline.
- Флаг **drift** — в WebSocket (`drift` в кадре `beat`); в UI явно не подсвечивается.

### Практический порядок на mock

1. Записать 2–3 сессии с разными тегами (`meditation`, `focus`, …) через **Mock** в вебе.
2. Сравнить форму кривых на вкладке «Запись» и наложение на «Прогресс».
3. `python cluster.py --include-mock` — группировка точек на scatter RMSSD × час.

После перехода на **реальный H10** смысл тот же: смотреть **дифференсы и устойчивые участки** относительно своих baseline и своих тегов сессий, а выводы об осознанности оставлять за **самонаблюдением и последующей разметкой**, а не за цветом линии один в один.