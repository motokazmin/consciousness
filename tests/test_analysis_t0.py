"""t₀ analysis: fallback when sessions.started predates first RR."""

from __future__ import annotations

from hrv_core.analysis import session_analysis


def _points(from_sec: float, n: int = 80, step: float = 0.85) -> list[tuple[float, float, float]]:
    return [(from_sec + i * step, 800.0 + (i % 5) * 3, 45.0) for i in range(n)]


def test_t0_uses_first_point_when_started_predates_arm():
    """started = POST time; first RR ~16 s later — ось X должна начинаться с 0."""
    first_ts = 16.0
    points = _points(first_ts)
    result = session_analysis(points, started=0.0, ended=100.0, stable_zone=False)
    assert result["raw_rr_x"][0] == 0.0
    assert result["raw_rr_x"][1] > 0


def test_t0_keeps_started_when_arm_updated():
    points = _points(100.0)
    result = session_analysis(points, started=100.0, ended=200.0, stable_zone=False)
    assert result["raw_rr_x"][0] == 0.0
