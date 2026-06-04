"""Нормализация меток типа активности."""

from __future__ import annotations

import re

_TAG_RE = re.compile(r"^[\w\s\-\.а-яА-ЯёЁ]+$", re.UNICODE)
_MAX_LEN = 64


def normalize_tag(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        raise ValueError("Укажите тип активности")
    if len(s) > _MAX_LEN:
        raise ValueError(f"Тип активности не длиннее {_MAX_LEN} символов")
    if not _TAG_RE.match(s):
        raise ValueError(
            "Тип активности: только буквы, цифры, пробел, дефис, точка и подчёркивание"
        )
    return s
