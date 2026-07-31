---
name: project-context
description: >-
  Bootstrap from ARCHITECTURE.md and keep it synced after architecture-changing
  commits. Use at the start of work on this project, when the user asks about
  architecture or project context, when creating or finishing a git commit, or
  when updating ARCHITECTURE.md / project docs.
---

# Project context (ARCHITECTURE.md)

Канонический файл: [`ARCHITECTURE.md`](../../../ARCHITECTURE.md) в корне репозитория.

Связанные документы (читать по необходимости, не вместо канона):
- [`hrv_mvp.md`](../../../hrv_mvp.md) — UI, BLE, baseline, mock
- [`explain.md`](../../../explain.md) — графики и интерпретация

## Bootstrap (начало сессии)

1. **Read** `ARCHITECTURE.md` целиком.
2. Если задача узкая (только BLE, только графики, только аудио) — дочитай соответствующий linked doc.
3. Дальше работай, опираясь на прочитанное; не выдумывай модули/API, которых нет в каноне.

## Refresh after commit

Триггер: пользователь попросил коммит, или коммит только что создан в этом чате.

1. Посмотри `git show --stat HEAD` и/или diff staged/just-committed.
2. Реши, затронута ли **архитектура**:
   - новые/удалённые модули или точки входа;
   - REST/WebSocket контракт (в т.ч. типы сообщений);
   - схема SQLite / смысл полей;
   - поток сессии (старт, arm, стоп, таймеры);
   - ключевые константы поведения (окна, пороги, таймауты).
3. **Если да** — точечно обнови разделы `ARCHITECTURE.md`:
   - стиль как в файле: таблицы, mermaid, пути к файлам;
   - не раздувай; правь только устаревшее;
   - чек-лист ниже.
4. **Если нет** — не трогай файл.
5. Коммит docs:
   - если ARCHITECTURE ещё можно включить в текущий коммит пользователя — включи;
   - если коммит уже сделан — отдельный `docs: sync ARCHITECTURE.md` **только** когда пользователь просил коммит / явно согласен на docs-коммит; иначе оставь в working tree и сообщи.

## Чек-лист синхронизации

При refresh пройди и поправь затронутые пункты:

- [ ] Слои / дерево репозитория
- [ ] Таблица модулей (`hrv_core/`, `hrv_web/`)
- [ ] Поток данных / обработка удара / жизненный цикл сессии
- [ ] Веб-API и типы WS-сообщений (`meta`, `armed`, `beat`, `ended`, …)
- [ ] Схема SQLite и смысл полей (`started`, …)
- [ ] Точки входа (`python -m hrv_web`, …)
