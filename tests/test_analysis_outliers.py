"""Тесты коррекции артефактов RR (Malik ~20% + интерполяция) в post-session analysis."""

from __future__ import annotations

import numpy as np

from hrv_core.analysis import session_analysis
from hrv_core.preprocessing import artifact_mask, correct_rr_artifacts


def _session_with_spike(duration_sec: int = 600, spike_rr: float = 1400.0) -> list[tuple[float, float, float]]:
    ts = np.arange(duration_sec, dtype=float)
    rr = 800.0 + 20.0 * np.sin(np.linspace(0, 12, duration_sec))
    rr[120] = spike_rr
    rmssd = np.full(duration_sec, 45.0)
    return list(zip(ts.tolist(), rr.tolist(), rmssd.tolist()))


def test_artifact_mask_flags_relative_spike():
    rr = np.array([800.0, 810.0, 1400.0, 805.0])
    mask = artifact_mask(rr)
    assert mask.tolist() == [True, True, False, True]


def test_correct_rr_interpolates_and_keeps_length():
    rr = np.array([800.0, 810.0, 1400.0, 805.0])
    corrected, mask, n_bad = correct_rr_artifacts(rr)
    assert n_bad == 1
    assert mask.sum() == 3
    assert corrected.size == rr.size
    assert 1400.0 not in corrected
    assert abs(corrected[2] - 807.5) < 1e-6


def test_session_analysis_always_corrects_spike():
    points = _session_with_spike()
    result = session_analysis(points, started=0.0, ended=600.0)

    assert result["outliers"]["applied"] is True
    assert result["outliers"]["removed"] == 1
    assert 1400.0 in result["raw_rr"]
    assert 1400.0 not in result["analysis_rr"]
    assert len(result["analysis_rr"]) == len(result["raw_rr"])
