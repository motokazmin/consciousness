# cluster_tool

Offline-кластеризация накопленных RMSSD (HDBSCAN + matplotlib). **Отдельный инструмент** — не часть веб-приложения `hrv_web`, не описан в основной документации проекта.

Читает SQLite-базу HRV Monitor (`sessions`, `hrv_points`). Путь к файлу **указывается явно** при запуске.

## Зачем

Проверка гипотезы: **можно ли по одному RMSSD-сигналу выделить кластеры состояний, согласующиеся с субъективными метками сессий** (тип активности в `sessions.tag`).

Алгоритм — **HDBSCAN только по RMSSD** (после StandardScaler). Час суток берётся из timestamp и показывается на scatter-графике (ось X), но **не входит** в признаки кластеризации.

## Установка

Из корня репозитория:

```bash
pip install -r cluster_tool/requirements.txt
```

Зависимости: matplotlib, hdbscan, scikit-learn, numpy (см. `cluster_tool/requirements.txt`).

## Запуск

```bash
python -m cluster_tool --db hrv_data.sqlite
python -m cluster_tool --db /полный/путь/hrv_data.sqlite --include-mock
python -m cluster_tool --db hrv_data.sqlite --min-cluster-size 10
```

| Аргумент | Описание |
|----------|----------|
| `--db PATH` | **Обязательно.** Путь к SQLite-базе HRV Monitor |
| `--include-mock` | Включить mock-сессии (`source LIKE 'mock%'`) |
| `--min-cluster-size N` | Параметр HDBSCAN (по умолчанию 15) |

При старте выводится абсолютный путь к базе: `Database: …`

## Фильтр mock-данных

По умолчанию mock-сессии **исключаются**, чтобы не смешивать синтетику с реальными измерениями Polar H10.

Флаг **`--include-mock`** — для отладки на данных, записанных через Mock в веб-UI. Имеет смысл после нескольких тестовых сессий с разными типами активности.

## Что делает скрипт

1. Загружает точки `hrv_points` с join на `sessions` (RMSSD, tag, час суток).
2. Запускает HDBSCAN по одномерному RMSSD.
3. Печатает сводку по кластерам (число точек, mean/min/max RMSSD, распределение tag).
4. Открывает matplotlib: scatter RMSSD × час (цвет — кластер, маркеры — тип активности) и boxplot RMSSD по кластерам.

Маркеры типов активности — в [`markers.py`](markers.py) (только для этого инструмента, не связаны с основным приложением).

## Минимум данных

Нужно не меньше `2 × min_cluster_size` точек с RMSSD > 0, иначе скрипт завершится с ошибкой. Запишите несколько сессий через `python -m hrv_web` перед запуском.

## Структура пакета

```
cluster_tool/
  cluster.py      — CLI и логика HDBSCAN
  markers.py      — маркеры scatter по типу сессии
  requirements.txt
  README.md       — этот файл
```

## Связь с основным проектом

- **Запись данных:** `python -m hrv_web` → `hrv_data.sqlite`
- **Анализ кластеров:** `python -m cluster_tool --db hrv_data.sqlite`

Основной проект (`hrv_core`, `hrv_web`) не импортирует `cluster_tool` и не тянет его зависимости.
