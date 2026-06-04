"""Тесты normalize_tag."""

from hrv_core.tags import normalize_tag


def test_normalize_tag_presets():
    assert normalize_tag("meditation") == "meditation"
    assert normalize_tag("  focus  ") == "focus"


def test_normalize_tag_cyrillic():
    assert normalize_tag("Йога") == "Йога"


def test_normalize_tag_empty():
    try:
        normalize_tag("")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "тип активности" in str(e).lower() or "Укажите" in str(e)
