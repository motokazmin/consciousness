"""Оценка частоты дыхания из ACC-данных Polar H10 (PMD-сервис).

Дыхательная волна извлекается из движения грудной клетки: сигнал
акселерометра фильтруется полосовым фильтром в диапазоне типичных
respiration rate (6–30 вдохов/мин), затем по пикам оценивается RPM.
"""

from __future__ import annotations

from collections import deque

import numpy as np
import time
from scipy.signal import butter, filtfilt, find_peaks

from hrv_core.constants import (
    ACC_SAMPLE_RATE_HZ,
    RESP_BAND_HZ,
    RESP_MIN_PEAK_DISTANCE_SEC,
    RESP_WAVEFORM_POINTS,
    RESP_WINDOW_SEC,
)


class RespirationEstimator:
    """Накопление ACC-сэмплов и оценка частоты/формы дыхания."""

    def __init__(self):
        self._buf: deque[tuple[float, float]] = deque(
            maxlen=ACC_SAMPLE_RATE_HZ * RESP_WINDOW_SEC
        )
        self._cache: np.ndarray | None = None
        self._cache_ts: float = 0.0

    def add_samples(self, samples: list[tuple[int, int, int]], ts: float) -> None:
        """samples — список (x, y, z) в мг; ts — время первого сэмпла фрейма."""
        dt = 1.0 / ACC_SAMPLE_RATE_HZ
        for i, (_x, _y, z) in enumerate(samples):
            # Ось Z как стартовое приближение (перпендикулярно груди при
            # типичном креплении ремня); требует проверки на устройстве —
            # при необходимости заменить на magnitude или другую ось.
            self._buf.append((ts + i * dt, float(z)))

    def _filtered(self) -> np.ndarray | None:
        if len(self._buf) < ACC_SAMPLE_RATE_HZ * 10:
            return None
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_ts) < 1.0:
            return self._cache
        vals = np.array([v for _, v in self._buf], dtype=float)
        vals -= vals.mean()
        nyq = ACC_SAMPLE_RATE_HZ / 2
        b, a = butter(2, [f / nyq for f in RESP_BAND_HZ], btype="band")
        self._cache = filtfilt(b, a, vals)
        self._cache_ts = now
        return self._cache

    def estimate_rate(self) -> float | None:
        """Частота дыхания, вдохов/мин, либо None если данных недостаточно."""
        filtered = self._filtered()
        if filtered is None:
            return None

        timestamps = np.array([t for t, _ in self._buf], dtype=float)
        distance = max(1, int(RESP_MIN_PEAK_DISTANCE_SEC * ACC_SAMPLE_RATE_HZ))
        peaks, _ = find_peaks(filtered, distance=distance)
        if len(peaks) < 2:
            return None

        span_sec = timestamps[peaks[-1]] - timestamps[peaks[0]]
        if span_sec <= 0:
            return None
        return float((len(peaks) - 1) / span_sec * 60.0)

    def waveform(self, n: int = RESP_WAVEFORM_POINTS) -> list[float]:
        """Последние n точек отфильтрованной дыхательной волны для графика."""
        filtered = self._filtered()
        if filtered is None:
            return []
        return [round(v, 4) for v in filtered[-n:]]
