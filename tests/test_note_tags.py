"""Тесты parse_note_tags."""

from hrv_core.note_tags import note_tag_sql_pattern, normalize_note_tag, parse_note_tags


def test_parse_note_tags_basic():
    assert parse_note_tags("Утренняя практика #утро #глубоко") == ["утро", "глубоко"]


def test_parse_note_tags_dedup():
    assert parse_note_tags("#утро текст #УТРО") == ["утро"]


def test_parse_note_tags_empty():
    assert parse_note_tags("") == []
    assert parse_note_tags(None) == []


def test_normalize_note_tag():
    assert normalize_note_tag("#Утро") == "утро"
    assert normalize_note_tag("утро") == "утро"


def test_note_tag_sql_pattern():
    assert note_tag_sql_pattern("утро") == "%#утро%"
