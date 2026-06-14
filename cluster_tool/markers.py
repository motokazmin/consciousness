"""Маркеры scatter-графика по типу сессии (только для offline-кластеризации)."""

from __future__ import annotations

DEFAULT_MARKER: tuple[str, int] = ("o", 40)

# Известные типы — для легенды на графике cluster_tool.
TAG_MARKERS: dict[str, tuple[str, int]] = {
    "relaxation": ("s", 100),
    "meditation": ("*", 180),
    "test": ("D", 70),
    "yoga": ("p", 110),
    "sleep": ("h", 90),
    "work": ("^", 80),
    "mental_training": ("v", 120),
}


def marker_for_tag(tag: str | None) -> tuple[str, int]:
    if not tag:
        return DEFAULT_MARKER
    return TAG_MARKERS.get(tag, DEFAULT_MARKER)
