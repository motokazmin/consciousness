"""RMSSD, буферы, drift и метрики для аудио-биофидбека — без UI."""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from hrv_core.constants import (
    BASELINE_SAMPLES,
    BASELINE_MIN_SAMPLES,
    DRIFT_COOLDOWN_SEC,
    DRIFT_THRESHOLD,
    RMSSD_WINDOW_SEC,
    SMOOTHED_RR_WINDOW_SEC,
)


def compute_rmssd(rr_list: list[float]) -> float:
    if len(rr_list) < 2:
        return 0.0
    return float(np.sqrt(np.mean(np.diff(rr_list) ** 2)))


@dataclass
class BeatSample:
    ts: float
    rr_ms: float
    rmssd: float
    smoothed_rr: float
    rmssd_normalized: float
    session_baseline: float
    drift_just_fired: bool = False


class HRVSessionState:
    """Состояние одной сессии: буферы RR/RMSSD, drift и производные метрики."""

    def __init__(
        self,
        persistent_baseline: float | None,
        *,
        desktop_notify: bool = True,
    ):
        self.rr_buffer: deque = deque()
        self.rr_history: deque = deque(maxlen=500)
        self.rmssd_history: deque = deque(maxlen=500)
        self._persistent_baseline = persistent_baseline
        self._last_notification = 0.0
        self.drift_events = 0
        self._desktop_notify = desktop_notify

    @property
    def persistent_baseline(self) -> float | None:
        return self._persistent_baseline

    def set_persistent_baseline(self, value: float | None) -> None:
        self._persistent_baseline = value

    def _smoothed_rr(self, ts: float) -> float | None:
        # Iterate from the end — rr_history is already updated before this call.
        # Stop as soon as we step outside the window (deque is time-ordered).
        cutoff = ts - SMOOTHED_RR_WINDOW_SEC
        recent: list[float] = []
        for t, r in reversed(self.rr_history):
            if t < cutoff:
                break
            recent.append(r)
        if not recent:
            return None
        return float(np.mean(recent))

    def _session_baseline(self) -> float | None:
        if len(self.rmssd_history) >= BASELINE_MIN_SAMPLES:
            # Take last BASELINE_SAMPLES from the end without copying the full deque.
            recent: list[float] = []
            for _, r in reversed(self.rmssd_history):
                recent.append(r)
                if len(recent) == BASELINE_SAMPLES:
                    break
            return float(np.mean(recent))
        if self._persistent_baseline is not None:
            return self._persistent_baseline
        return None

    def _check_drift(self, rmssd: float, baseline: float) -> bool:
        now = time.time()
        if (
            baseline <= 1
            or rmssd >= baseline * DRIFT_THRESHOLD
            or now - self._last_notification <= DRIFT_COOLDOWN_SEC
        ):
            return False
        self._last_notification = now
        self.drift_events += 1
        msg = f"RMSSD {rmssd:.0f} ms  (baseline {baseline:.0f} ms) — drift detected"
        if self._desktop_notify:
            try:
                threading.Thread(
                    target=subprocess.run,
                    args=(["notify-send", "HRV Monitor", msg],),
                    kwargs={"timeout": 2},
                    daemon=True,
                ).start()
            except Exception:
                pass
        print(f"\n⚠  {msg}")
        return True

    def process_beat(self, rr_ms: float, ts: float) -> BeatSample | None:
        self.rr_buffer.append((ts, rr_ms))
        self.rr_history.append((ts, rr_ms))

        cutoff = ts - RMSSD_WINDOW_SEC
        while self.rr_buffer and self.rr_buffer[0][0] < cutoff:
            self.rr_buffer.popleft()

        rmssd = compute_rmssd([r for _, r in self.rr_buffer])
        if rmssd <= 0:
            return None

        self.rmssd_history.append((ts, rmssd))
        smoothed_rr = self._smoothed_rr(ts)
        if smoothed_rr is None:
            smoothed_rr = rr_ms
        drift_baseline = self._session_baseline()
        session_baseline = drift_baseline if drift_baseline is not None else rmssd
        rmssd_normalized = rmssd / session_baseline if session_baseline >= 1.0 else 1.0
        drift_fired = False
        if drift_baseline is not None:
            drift_fired = self._check_drift(rmssd, drift_baseline)

        return BeatSample(
            ts=ts,
            rr_ms=rr_ms,
            rmssd=rmssd,
            smoothed_rr=smoothed_rr,
            rmssd_normalized=rmssd_normalized,
            session_baseline=session_baseline,
            drift_just_fired=drift_fired,
        )