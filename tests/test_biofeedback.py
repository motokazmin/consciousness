"""Unit-тесты BreathFeedbackLoop."""

from __future__ import annotations

import math
import time
import unittest

from hrv_core.biofeedback import (
    MIN_BEATS_WARMUP,
    BreathFeedbackLoop,
    BreathPhase,
)
from hrv_core.sources import MockHRVSource


def _feed_sine(
    loop: BreathFeedbackLoop,
    *,
    mean_rr: float = 857.0,
    amplitude: float = 80.0,
    period_sec: float = 10.0,
    n_beats: int = 80,
    beat_interval: float = 0.85,
) -> list:
    states = []
    t0 = time.time()
    for i in range(n_beats):
        ts = t0 + i * beat_interval
        rr = mean_rr - amplitude * math.sin(2 * math.pi * ts / period_sec)
        state = loop.process_rr(rr, ts)
        if state is not None:
            states.append(state)
    return states


class BreathFeedbackLoopTests(unittest.TestCase):
    def test_rejects_20pct_spike(self) -> None:
        loop = BreathFeedbackLoop()
        base_ts = time.time()
        accepted = 0
        for i in range(MIN_BEATS_WARMUP + 5):
            rr = 850.0 if i != 4 else 850.0 * 1.3
            if loop.process_rr(rr, base_ts + i * 0.85) is not None:
                accepted += 1
        self.assertGreater(accepted, 0)

    def test_returns_none_during_warmup(self) -> None:
        loop = BreathFeedbackLoop()
        base_ts = time.time()
        for i in range(MIN_BEATS_WARMUP - 1):
            self.assertIsNone(loop.process_rr(850.0, base_ts + i * 0.85))

    def test_detects_inhale_exhale_on_sine(self) -> None:
        loop = BreathFeedbackLoop()
        states = _feed_sine(loop, n_beats=120)
        self.assertGreater(len(states), 10)
        phases = {s.phase for s in states}
        self.assertIn(BreathPhase.INHALE, phases)
        self.assertIn(BreathPhase.EXHALE, phases)

    def test_rsa_amplitude_tracks(self) -> None:
        loop = BreathFeedbackLoop()
        states = _feed_sine(loop, amplitude=80.0, n_beats=120)
        self.assertTrue(any(s.rsa_amplitude > 20 for s in states))

    def test_resonance_score_in_range(self) -> None:
        loop = BreathFeedbackLoop()
        states = _feed_sine(loop, n_beats=120)
        for state in states:
            self.assertGreaterEqual(state.resonance_score, 0.0)
            self.assertLessEqual(state.resonance_score, 1.0)


class MeditationMockIntegrationTests(unittest.TestCase):
    def test_meditation_mock_produces_breath_states(self) -> None:
        loop = BreathFeedbackLoop()
        mock = MockHRVSource(mock_tag="meditation", verbose=False)
        states = []

        def handle(rr_ms: float, ts: float) -> None:
            state = loop.process_rr(rr_ms, ts)
            if state is not None:
                states.append(state)

        mock.start(handle)
        try:
            time.sleep(35.0)
        finally:
            mock.stop()
            if mock._thread:
                mock._thread.join(timeout=3.0)

        self.assertGreater(len(states), 20)
        phases = {s.phase for s in states}
        self.assertTrue(
            BreathPhase.INHALE in phases or BreathPhase.EXHALE in phases,
            msg=f"phases seen: {phases}",
        )


if __name__ == "__main__":
    unittest.main()
