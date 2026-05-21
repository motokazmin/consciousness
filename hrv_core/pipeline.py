"""RMSSD, буферы и drift — без UI."""

from __future__ import annotations

import subprocess
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from hrv_core.constants import (
    BASELINE_SAMPLES,
    DRIFT_COOLDOWN_SEC,
    DRIFT_THRESHOLD,
    RMSSD_WINDOW_SEC,
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
    drift_just_fired: bool


class HRVSessionState:
    """Состояние одной сессии: буферы RR/RMSSD и проверка drift."""

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

        drift_fired = False
        if len(self.rmssd_history) >= BASELINE_SAMPLES // 2:
            recent = [r for _, r in list(self.rmssd_history)[-BASELINE_SAMPLES:]]
            baseline = float(np.mean(recent))
        elif self._persistent_baseline is not None:
            baseline = self._persistent_baseline
        else:
            return BeatSample(ts, rr_ms, rmssd, False)

        now = time.time()
        if (
            baseline > 1
            and rmssd < baseline * DRIFT_THRESHOLD
            and now - self._last_notification > DRIFT_COOLDOWN_SEC
        ):
            self._last_notification = now
            self.drift_events += 1
            drift_fired = True
            msg = (
                f"RMSSD {rmssd:.0f} ms  (baseline {baseline:.0f} ms) — drift detected"
            )
            if self._desktop_notify:
                try:
                    subprocess.run(["notify-send", "HRV Monitor", msg], timeout=2)
                except Exception:
                    pass
            print(f"\n⚠  {msg}")

        return BeatSample(ts, rr_ms, rmssd, drift_fired)
