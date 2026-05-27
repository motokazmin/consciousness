"""Детекция фазы дыхания и RSA-резонанса по потоку RR-интервалов."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable

# Размер окна медианного фильтра RR (число последних принятых ударов)
MEDIAN_WINDOW = 3

# Отклонение RR от предыдущего filtered > этого доли — артефакт, бит отбрасывается
ARTIFACT_PCT = 0.20

# Допустимый диапазон RR (мс); вне диапазона — отбрасывается
RR_MIN_MS = 300.0
RR_MAX_MS = 2000.0

# EMA-сглаживание медленного baseline RR: alpha нового значения (0…1)
SLOW_BASELINE_ALPHA = 0.004

# Число последних точек signal для линейной регрессии наклона
SLOPE_WINDOW = 5

# Мёртвая зона наклона signal (мс/с): ниже — фаза не меняется / HOLD
SLOPE_DEADBAND = 0.15

# Минимум принятых ударов до выдачи BreathState (прогрев фильтров)
MIN_BEATS_WARMUP = 6

# Оценка длительности полуцикла дыхания по умолчанию (сек), если ещё нет истории
DEFAULT_HALF_PERIOD_SEC = 5.0

# RSA amplitude (мс): ниже этого порога amp_score = 0 в resonance_score
RSA_AMP_MIN_MS = 15

# Диапазон RSA amplitude (мс) для нормализации amp_score в 0…1
RSA_AMP_RANGE_MS = 85


class BreathPhase(Enum):
    INHALE = "inhale"
    EXHALE = "exhale"
    HOLD = "hold"


@dataclass
class BreathState:
    phase: BreathPhase
    phase_progress: float
    rsa_amplitude: int
    resonance_score: float
    half_period_sec: float


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _regression_slope(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 3:
        return None
    t0 = points[0][0]
    ts = [p[0] - t0 for p in points]
    rs = [p[1] for p in points]
    n = len(points)
    mt = sum(ts) / n
    mr = sum(rs) / n
    num = sum((ts[i] - mt) * (rs[i] - mr) for i in range(n))
    den = sum((t - mt) ** 2 for t in ts)
    if den < 1e-9:
        return 0.0
    return num / den


class BreathFeedbackLoop:
    """Закрытая петля biofeedback: RR → фаза дыхания + метрика резонанса."""

    def __init__(
        self,
        *,
        on_state: Callable[[BreathState], None] | None = None,
    ) -> None:
        self._on_state = on_state
        self._accepted: deque[float] = deque(maxlen=MEDIAN_WINDOW)
        self._signal_points: deque[tuple[float, float]] = deque(maxlen=48)
        self._filtered_history: deque[tuple[float, float]] = deque(maxlen=64)
        self._prev_filtered: float | None = None
        self._baseline: float | None = None
        self._accepted_count = 0
        self._phase = BreathPhase.HOLD
        self._half_periods: deque[float] = deque(maxlen=6)
        self._half_start_ts = 0.0
        self._half_anchor_signal = 0.0
        self._half_extreme_signal = 0.0
        self._cycle_min = float("inf")
        self._cycle_max = float("-inf")

    def process_rr(self, rr_ms: float, ts: float) -> BreathState | None:
        filtered = self._filter_rr(rr_ms, ts)
        if filtered is None:
            return None

        self._accepted_count += 1
        if self._baseline is None:
            self._baseline = filtered
        else:
            self._baseline = (
                SLOW_BASELINE_ALPHA * filtered
                + (1.0 - SLOW_BASELINE_ALPHA) * self._baseline
            )

        signal = filtered - self._baseline
        self._signal_points.append((ts, signal))
        phase, progress = self._update_half_cycle(signal, ts)
        self._phase = phase
        self._update_cycle_extrema(filtered)

        if self._accepted_count < MIN_BEATS_WARMUP:
            return None

        rsa_amplitude = int(round(max(0.0, self._cycle_max - self._cycle_min)))
        half_period = self._estimated_half_period()
        resonance_score = self._resonance_score(rsa_amplitude)

        state = BreathState(
            phase=phase,
            phase_progress=progress,
            rsa_amplitude=rsa_amplitude,
            resonance_score=resonance_score,
            half_period_sec=half_period,
        )
        if self._on_state is not None:
            self._on_state(state)
        return state

    def _filter_rr(self, rr_ms: float, ts: float) -> float | None:
        if not (RR_MIN_MS < rr_ms < RR_MAX_MS):
            return None
        if self._prev_filtered is not None:
            delta = abs(rr_ms - self._prev_filtered) / self._prev_filtered
            if delta > ARTIFACT_PCT:
                return None

        self._accepted.append(rr_ms)
        filtered = float(sorted(self._accepted)[len(self._accepted) // 2])
        self._prev_filtered = filtered
        self._filtered_history.append((ts, filtered))
        return filtered

    def _signal_slope(self) -> float | None:
        if len(self._signal_points) < 3:
            return None
        return _regression_slope(list(self._signal_points)[-SLOPE_WINDOW:])

    def _begin_half_cycle(self, phase: BreathPhase, signal: float, ts: float) -> None:
        prev_phase = self._phase
        if self._half_start_ts > 0 and prev_phase in (BreathPhase.INHALE, BreathPhase.EXHALE):
            elapsed = ts - self._half_start_ts
            if 2.0 < elapsed < 14.0:
                self._half_periods.append(elapsed)
        if phase == BreathPhase.INHALE and prev_phase == BreathPhase.EXHALE:
            self._cycle_min = float("inf")
            self._cycle_max = float("-inf")
        self._phase = phase
        self._half_start_ts = ts
        self._half_anchor_signal = signal
        self._half_extreme_signal = signal

    def _update_half_cycle(self, signal: float, ts: float) -> tuple[BreathPhase, float]:
        slope = self._signal_slope()
        if slope is None:
            return BreathPhase.HOLD, 0.0

        if slope < -SLOPE_DEADBAND:
            target = BreathPhase.INHALE
        elif slope > SLOPE_DEADBAND:
            target = BreathPhase.EXHALE
        elif self._phase in (BreathPhase.INHALE, BreathPhase.EXHALE):
            target = self._phase
        else:
            return BreathPhase.HOLD, 0.0

        if self._phase != target:
            self._begin_half_cycle(target, signal, ts)
        elif self._half_start_ts <= 0:
            self._begin_half_cycle(target, signal, ts)

        if target == BreathPhase.INHALE:
            self._half_extreme_signal = min(self._half_extreme_signal, signal)
            span = self._half_anchor_signal - self._half_extreme_signal
            progress = _clamp((self._half_anchor_signal - signal) / max(span, 8.0), 0.0, 1.0)
        else:
            self._half_extreme_signal = max(self._half_extreme_signal, signal)
            span = self._half_extreme_signal - self._half_anchor_signal
            progress = _clamp((signal - self._half_anchor_signal) / max(span, 8.0), 0.0, 1.0)

        return target, progress

    def _update_cycle_extrema(self, filtered: float) -> None:
        if self._cycle_min == float("inf"):
            self._cycle_min = filtered
            self._cycle_max = filtered
        self._cycle_min = min(self._cycle_min, filtered)
        self._cycle_max = max(self._cycle_max, filtered)

    def _estimated_half_period(self) -> float:
        if self._half_periods:
            return sum(self._half_periods) / len(self._half_periods)
        if self._half_start_ts > 0:
            return max(DEFAULT_HALF_PERIOD_SEC, min(8.0, self._accepted_count * 0.85 / 2))
        return DEFAULT_HALF_PERIOD_SEC

    def _sinusoid_score(self) -> float:
        if len(self._filtered_history) < 6:
            return 0.5
        values = [v for _, v in self._filtered_history]
        mean_v = sum(values) / len(values)
        amplitude = max(values) - min(values)
        if amplitude < 5.0:
            return 0.0
        centered = [v - mean_v for v in values]
        sign_changes = sum(
            1
            for i in range(1, len(centered))
            if centered[i - 1] * centered[i] < 0
        )
        expected = max(1, len(values) // 3)
        return _clamp(sign_changes / expected, 0.0, 1.0)

    def _resonance_score(self, rsa_amplitude: int) -> float:
        amp_score = _clamp(
            (rsa_amplitude - RSA_AMP_MIN_MS) / RSA_AMP_RANGE_MS,
            0.0,
            1.0,
        )
        return _clamp(amp_score * self._sinusoid_score(), 0.0, 1.0)
