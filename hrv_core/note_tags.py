"""Теги в тексте заметок к сессии (#утро, #глубоко)."""

from __future__ import annotations

import re

_NOTE_TAG_RE = re.compile(r"#([\w\-а-яА-ЯёЁ]+)", re.UNICODE)
_MAX_TAG_LEN = 32


def parse_note_tags(text: str | None) -> list[str]:
    """Уникальные теги из заметки, в нижнем регистре, в порядке появления."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _NOTE_TAG_RE.finditer(text):
        tag = m.group(1).lower()
        if len(tag) > _MAX_TAG_LEN or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def normalize_note_tag(raw: str) -> str:
    s = (raw or "").strip().lstrip("#").lower()
    if not s:
        raise ValueError("Укажите тег заметки")
    if len(s) > _MAX_TAG_LEN:
        raise ValueError(f"Тег заметки не длиннее {_MAX_TAG_LEN} символов")
    if not re.match(r"^[\w\-а-яё]+$", s, re.UNICODE):
        raise ValueError(
            "Тег заметки: только буквы, цифры, дефис и подчёркивание"
        )
    return s


def note_tag_sql_pattern(tag: str) -> str:
    """LIKE-паттерн: тег как отдельное слово после #."""
    norm = normalize_note_tag(tag)
    return f"%#{norm}%"
