"""Тесты HRV pipeline: smoothed_rr и rmssd_normalized."""

from hrv_core.pipeline import HRVSessionState


def _feed_beats(state: HRVSessionState, rr_values: list[float], start_ts: float = 1000.0):
    ts = start_ts
    last = None
    for rr in rr_values:
        last = state.process_beat(rr, ts)
        ts += rr / 1000.0
    return last


def test_smoothed_rr_uses_recent_window():
    state = HRVSessionState(persistent_baseline=50.0)
    _feed_beats(state, [800.0, 810.0, 805.0, 815.0, 808.0], start_ts=0.0)
    sample = state.process_beat(800.0, 5.0)
    assert sample is not None
    assert abs(sample.smoothed_rr - 800.0) < 20.0

    _feed_beats(state, [600.0, 602.0, 598.0, 601.0, 599.0, 603.0, 600.0, 601.0, 599.0, 602.0], start_ts=6.0)
    sample = state.process_beat(600.0, 16.0)
    assert sample is not None
    assert sample.smoothed_rr < 750.0
    assert sample.smoothed_rr > 590.0


def test_rmssd_normalized_against_session_baseline():
    state = HRVSessionState(persistent_baseline=40.0)
    rr_stable = [820.0, 830.0, 825.0, 835.0, 820.0, 828.0, 822.0, 826.0]
    sample = _feed_beats(state, rr_stable, start_ts=10.0)
    assert sample is not None
    assert sample.session_baseline > 0
    assert abs(sample.rmssd_normalized - sample.rmssd / sample.session_baseline) < 1e-9


def test_rmssd_normalized_fallback_when_baseline_too_small():
    state = HRVSessionState(persistent_baseline=None)
    assert state.process_beat(900.0, 1.0) is None

    sample = state.process_beat(910.0, 2.9)
    assert sample is not None
    assert sample.rmssd_normalized == 1.0
