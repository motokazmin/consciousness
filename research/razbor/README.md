# Разборы сессий — исходники

Черновики и исходники того, что кладётся в БД рядом с сессией:

- `<id>.md` — текст разбора (`session_explanations`), навык `/razbor`;
- `<id>.segments.json` — разметка отрезков для ленты над графиками архива
  (`session_segments`): `segments` — `{t0, t1, kind, label, confidence, basis}`,
  `events` — `{t, kind, label}`. `t` — секунды от начала сессии.
  `confidence`: `know` / `assume` / `guess` (знаю / предполагаю / догадка).

Загрузить в свою базу (сервер поднят):

```bash
curl -X PUT -H 'Content-Type: application/json' \
     --data-binary @research/razbor/245.segments.json \
     http://127.0.0.1:8765/api/sessions/245/segments
curl -X PUT -H 'Content-Type: text/markdown' \
     --data-binary @research/razbor/245.md \
     http://127.0.0.1:8765/api/sessions/245/explanation
```

**Не источник для выводов исследования.** Находки из разбора переносятся в
`journal.md` со ссылкой на `session_id`; разметка стадий — предположение, а не
измерение.
