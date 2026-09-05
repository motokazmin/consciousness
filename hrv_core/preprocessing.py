"""RR-interval preprocessing: artifact correction, FFT detrending, Poincaré viewport (raw preserved)."""

from __future__ import annotations

from typing import Any

import numpy as np
import scipy.signal as signal

POINCARE_VIEWPORT_MIN_PAD_MS = 30
POINCARE_VIEWPORT_MAX_PAD_MS = 50
POINCARE_PERCENTILE_LO = 5
POINCARE_PERCENTILE_HI = 95
MIN_RR_FOR_VIEWPORT = 4
DEFAULT_VIEWPORT = {"min": 600, "max": 1000}
SDNN_INITIAL_CROP_SEC = 20.0


# Классическая коррекция артефактов RR (Malik / Kubios-style):
# относительный порог к локальной медиане + физиологические границы.
# Всегда применяется к аналитике; raw в БД и raw_rr_* не трогаем.
#
# Опора — скользящая медиана, а не последний принятый интервал. Опора по
# последнему принятому работает как храповик: при медленном дрейфе пульса
# первый же отказ замораживает опору, ряд уходит от неё всё дальше и
# бракуется целиком (сессия #117: разрывов записи нет, реальных артефактов
# 0.1%, каскад давал 100% и превращал ряд в прямую после интерполяции).
ARTIFACT_REL_THRESHOLD = 0.20
RR_PHYSIO_MIN_MS = 300.0
RR_PHYSIO_MAX_MS = 2000.0
ARTIFACT_MEDIAN_WINDOW = 5


def _local_median(rr: np.ndarray, window: int) -> np.ndarray:
    """Скользящая медиана по окну (нечётному), края достраиваются краевым значением."""
    n = rr.size
    w = window if window % 2 else window + 1
    if n < w:
        w = n if n % 2 else n - 1
    if w < 3:
        return np.full(n, float(np.median(rr)))
    half = w // 2
    padded = np.pad(rr, half, mode="edge")
    views = np.lib.stride_tricks.sliding_window_view(padded, w)
    return np.median(views, axis=1)


def artifact_mask(
    rr: np.ndarray,
    *,
    rel_threshold: float = ARTIFACT_REL_THRESHOLD,
    physio_min_ms: float = RR_PHYSIO_MIN_MS,
    physio_max_ms: float = RR_PHYSIO_MAX_MS,
    median_window: int = ARTIFACT_MEDIAN_WINDOW,
) -> np.ndarray:
    """True = валидный удар (Malik: |RRᵢ − med| / med ≤ threshold, med — локальная медиана)."""
    rr = np.asarray(rr, dtype=float)
    n = rr.size
    if n == 0:
        return np.zeros(0, dtype=bool)

    valid = np.isfinite(rr) & (rr >= physio_min_ms) & (rr <= physio_max_ms)
    if n == 1:
        return valid

    reference = _local_median(rr, median_window)
    valid &= np.abs(rr - reference) <= rel_threshold * reference
    return valid


def correct_rr_artifacts(
    rr: np.ndarray,
    *,
    rel_threshold: float = ARTIFACT_REL_THRESHOLD,
    physio_min_ms: float = RR_PHYSIO_MIN_MS,
    physio_max_ms: float = RR_PHYSIO_MAX_MS,
    median_window: int = ARTIFACT_MEDIAN_WINDOW,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Маска артефактов + линейная интерполяция по индексу (длина ряда сохраняется).

    Returns:
        corrected_rr, valid_mask (True=исходный валидный), n_corrected
    """
    rr = np.asarray(rr, dtype=float).copy()
    mask = artifact_mask(
        rr,
        rel_threshold=rel_threshold,
        physio_min_ms=physio_min_ms,
        physio_max_ms=physio_max_ms,
        median_window=median_window,
    )
    n_bad = int((~mask).sum())
    if n_bad == 0 or mask.sum() == 0:
        return rr, mask, n_bad

    idx = np.arange(rr.size, dtype=float)
    good = mask.astype(bool)
    rr[~good] = np.interp(idx[~good], idx[good], rr[good])
    return rr, mask, n_bad


def ectopic_mask(
    rr: np.ndarray,
    *,
    rel_threshold: float = ARTIFACT_REL_THRESHOLD,
    physio_min_ms: float = RR_PHYSIO_MIN_MS,
    physio_max_ms: float = RR_PHYSIO_MAX_MS,
    median_window: int = ARTIFACT_MEDIAN_WINDOW,
    iqr_factor: float | None = None,  # noqa: ARG001 — legacy no-op
) -> np.ndarray:
    """Alias: True = валидный удар. См. artifact_mask (Malik ~20%)."""
    del iqr_factor
    return artifact_mask(
        rr,
        rel_threshold=rel_threshold,
        physio_min_ms=physio_min_ms,
        physio_max_ms=physio_max_ms,
        median_window=median_window,
    )


def _fft_input(rr: np.ndarray) -> np.ndarray:
    if rr.size == 0:
        return rr
    if rr.size == 1:
        return np.array([0.0])
    return signal.detrend(rr - np.mean(rr))


def _poincare_viewport_bounds(rr: np.ndarray) -> dict[str, int]:
    if rr.size < MIN_RR_FOR_VIEWPORT:
        return dict(DEFAULT_VIEWPORT)
    p5, p95 = np.percentile(rr, [POINCARE_PERCENTILE_LO, POINCARE_PERCENTILE_HI])
    return {
        "min": int(p5 - POINCARE_VIEWPORT_MIN_PAD_MS),
        "max": int(p95 + POINCARE_VIEWPORT_MAX_PAD_MS),
    }


def preprocess_rr_session(raw_rr: np.ndarray | list[float]) -> dict[str, Any]:
    """Derive FFT input and Poincaré viewport bounds without modifying raw RR data."""
    rr = np.asarray(raw_rr, dtype=float)
    if rr.size == 0:
        return {
            "raw_rr": [],
            "fft_input_rr": [],
            "poincare_bounds": {"min": 0, "max": 0},
        }

    if rr.size == 1:
        val = float(rr[0])
        return {
            "raw_rr": [val],
            "fft_input_rr": [0.0],
            "poincare_bounds": {
                "min": int(val - POINCARE_VIEWPORT_MIN_PAD_MS),
                "max": int(val + POINCARE_VIEWPORT_MAX_PAD_MS),
            },
        }

    return {
        "raw_rr": rr.tolist(),
        "fft_input_rr": _fft_input(rr).tolist(),
        "poincare_bounds": _poincare_viewport_bounds(rr),
    }
