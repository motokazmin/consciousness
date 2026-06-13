"""RR-interval preprocessing: FFT detrending and Poincaré viewport bounds (raw data preserved)."""

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
