"""Конфигурация типов сессий — единственное место правды.

Чтобы добавить новый тип:
  1. Добавьте запись в SESSION_TYPES ниже.
  2. Положите mp3-фразы в static/phrases/{prefix}/{set}/ (например sit/directive/).
  3. При необходимости укажите chart_profile — имя набора/настроек графиков
     для архивного просмотра (см. CHART_PROFILES во static/app.js).
     Если не указан — используется "default". Пример:
       chart_profile="my_profile"
     и в app.js:
       my_profile: { panels: ["rr", "poincare"], options: { … } }
  4. Готово — mock-профиль, phrases-UI и графики подтянутся автоматически.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionType:
    slug: str
    label: str
    # Профиль mock-источника: "meditation" | "default"
    mock_profile: str
    # Префикс папки с mp3-фразами ("sit", "lay", ...) или None
    phrase_prefix: str | None
    # Профиль набора/настроек графиков в архиве (CHART_PROFILES во app.js).
    chart_profile: str = "default"


# ── Базовые типы (не удалять) ──────────────────────────────────────────────
SESSION_TYPES: dict[str, SessionType] = {
    "relaxation": SessionType(
        slug="relaxation",
        label="Релаксация",
        mock_profile="default",
        phrase_prefix="lay",
    ),
    "meditation": SessionType(
        slug="meditation",
        label="Медитация",
        mock_profile="meditation",
        phrase_prefix="sit",
    ),
    "test": SessionType(
        slug="test",
        label="Тестирование",
        mock_profile="default",
        phrase_prefix=None,
    ),
    "yoga": SessionType(
        slug="yoga",
        label="Йога",
        mock_profile="default",
        phrase_prefix=None,
    ),
    "sleep": SessionType(
        slug="sleep",
        label="Сон",
        mock_profile="default",
        phrase_prefix=None,
    ),
    "work": SessionType(
        slug="work",
        label="Работа",
        mock_profile="default",
        phrase_prefix=None,
    ),
    "mental_training": SessionType(
        slug="mental_training",
        label="Ментальная тренировка",
        mock_profile="default",
        phrase_prefix=None,
    ),
}

# Кортеж slug-ов для валидации (используется в constants.py и tags.py)
SESSION_SLUGS: tuple[str, ...] = tuple(SESSION_TYPES.keys())
