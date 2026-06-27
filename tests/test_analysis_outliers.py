"""Тесты опционального фильтра единичных выбросов RR в post-session analysis."""

from __future__ import annotations

import numpy as np

from hrv_core.analysis import session_analysis


def _session_with_spike(duration_sec: int = 600, spike_rr: float = 1400.0) -> list[tuple[float, float, float]]:
    ts = np.arange(duration_sec, dtype=float)
    rr = 800.0 + 20.0 * np.sin(np.linspace(0, 12, duration_sec))
    rr[120] = spike_rr
    rmssd = np.full(duration_sec, 45.0)
    return list(zip(ts.tolist(), rr.tolist(), rmssd.tolist()))


def test_filter_outliers_disabled_keeps_spike_in_analysis():
    points = _session_with_spike()
    result = session_analysis(points, started=0.0, ended=600.0, filter_outliers=False)

    assert result["filter_outliers"] is False
    assert result["outliers"]["applied"] is False
    assert result["outliers"]["removed"] == 0
    assert 1400.0 in result["analysis_rr"]


def test_filter_outliers_removes_single_spike():
    points = _session_with_spike()
    raw = session_analysis(points, started=0.0, ended=600.0, filter_outliers=False)
    filtered = session_analysis(points, started=0.0, ended=600.0, filter_outliers=True)

    assert filtered["filter_outliers"] is True
    assert filtered["outliers"]["applied"] is True
    assert filtered["outliers"]["removed"] == 1
    assert 1400.0 not in filtered["analysis_rr"]
    assert len(filtered["raw_rr"]) == len(raw["raw_rr"])
    assert filtered["mean_rr"] != raw["mean_rr"]
