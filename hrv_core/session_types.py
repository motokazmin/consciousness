"""Конфигурация типов сессий — единственное место правды.

Чтобы добавить новый тип:
  1. Добавьте запись в SESSION_TYPES ниже.
  2. Положите mp3-фразы в static/phrases/ с нужным prefix (sit/lay/…).
  3. При необходимости укажите chart_profile — имя набора/настроек графиков
     для архивного просмотра (см. CHART_PROFILES во static/app.js).
     Если не указан — используется "default". Пример:
       chart_profile="my_profile"
     и в app.js:
       my_profile: { panels: ["rr", "poincare"], options: { … } }
  4. Готово — всё остальное (mock-профиль, phrases-UI, cluster-маркеры,
     графики) подтянется автоматически.
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
    # (marker, size) для scatter-графика в cluster.py
    cluster_marker: tuple[str, int]
    # Профиль набора/настроек графиков в архиве (CHART_PROFILES во app.js).
    # "default", если для типа сессии достаточно стандартного набора графиков.
    chart_profile: str = "default"


# ── Базовые типы (не удалять) ──────────────────────────────────────────────
SESSION_TYPES: dict[str, SessionType] = {
    "meditation": SessionType(
        slug="meditation",
        label="Медитация",
        mock_profile="meditation",
        phrase_prefix="sit",
        cluster_marker=("*", 180),
    ),
    "relaxation": SessionType(
        slug="relaxation",
        label="Релаксация",
        mock_profile="default",
        phrase_prefix="lay",
        cluster_marker=("s", 100),
    ),
    "test": SessionType(
        slug="test",
        label="Тестирование",
        mock_profile="default",
        phrase_prefix=None,
        cluster_marker=("D", 70),
    ),
    # ── Дополнительные ────────────────────────────────────────────────────
    "focus": SessionType(
        slug="focus",
        label="Фокус",
        mock_profile="default",
        phrase_prefix=None,
        cluster_marker=("^", 120),
    ),
    "scroll": SessionType(
        slug="scroll",
        label="Скролл",
        mock_profile="default",
        phrase_prefix=None,
        cluster_marker=("v", 90),
    ),
    "untagged": SessionType(
        slug="untagged",
        label="Без тега",
        mock_profile="default",
        phrase_prefix=None,
        cluster_marker=("o", 40),
    ),
}

# Кортеж slug-ов для валидации (используется в constants.py и tags.py)
SESSION_SLUGS: tuple[str, ...] = tuple(SESSION_TYPES.keys())

# Удобный маппинг slug → marker для cluster.py
TAG_MARKERS: dict[str, tuple[str, int]] = {
    slug: st.cluster_marker for slug, st in SESSION_TYPES.items()
}
