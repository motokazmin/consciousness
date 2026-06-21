"""Тесты стабильной зоны (trim краёв) в post-session analysis."""

from __future__ import annotations

import numpy as np

from hrv_core.analysis import session_analysis
from hrv_core.preprocessing import STABLE_ZONE_TRIM_SEC, stable_zone_mask


def _session_points(duration_sec: int = 1800, spike_at: int = 60) -> list[tuple[float, float, float]]:
    ts = np.arange(duration_sec, dtype=float)
    rr = np.full(duration_sec, 800.0)
    rr[0:30] = 950.0
    rr[-30:] = 650.0
    rr[spike_at] = 1400.0
    rmssd = np.full(duration_sec, 45.0)
    return list(zip(ts.tolist(), rr.tolist(), rmssd.tolist()))


def test_stable_zone_mask_trims_edges():
    ts = np.arange(600, dtype=float)
    mask = stable_zone_mask(ts, trim_start_sec=60, trim_end_sec=60)
    assert mask.sum() < ts.size
    assert not mask[0]
    assert not mask[-1]
    assert mask[100]


def test_session_stable_zone_excludes_edge_spikes_from_spectrum():
    points = _session_points()
    full = session_analysis(points, started=0.0, ended=1800.0, stable_zone=False)
    trimmed = session_analysis(points, started=0.0, ended=1800.0, stable_zone=True)

    assert full["stable_zone"] is False
    assert trimmed["stable_zone"] is True
    assert trimmed["trim"]["applied"] is True
    assert len(trimmed["raw_rr"]) == len(full["raw_rr"])
    assert full["raw_rr"][0] != trimmed["mean_rr"]

    if not full["spectrum"].get("insufficient_data") and not trimmed["spectrum"].get(
        "insufficient_data"
    ):
        assert full["spectrum"]["power"] != trimmed["spectrum"]["power"]


def test_short_session_skips_trim():
    points = _session_points(duration_sec=90)
    result = session_analysis(
        points,
        started=0.0,
        ended=90.0,
        stable_zone=True,
        trim_start_sec=STABLE_ZONE_TRIM_SEC,
        trim_end_sec=STABLE_ZONE_TRIM_SEC,
    )
    assert result["stable_zone"] is False
    assert result["trim"]["applied"] is False
