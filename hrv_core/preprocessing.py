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
# относительный порог к последнему принятому интервалу + физиологические границы.
# Всегда применяется к аналитике; raw в БД и raw_rr_* не трогаем.
ARTIFACT_REL_THRESHOLD = 0.20
RR_PHYSIO_MIN_MS = 300.0
RR_PHYSIO_MAX_MS = 2000.0


def artifact_mask(
    rr: np.ndarray,
    *,
    rel_threshold: float = ARTIFACT_REL_THRESHOLD,
    physio_min_ms: float = RR_PHYSIO_MIN_MS,
    physio_max_ms: float = RR_PHYSIO_MAX_MS,
) -> np.ndarray:
    """True = валидный удар (Malik: |RRᵢ − RR_last| / RR_last ≤ threshold)."""
    rr = np.asarray(rr, dtype=float)
    n = rr.size
    if n == 0:
        return np.zeros(0, dtype=bool)
    if n == 1:
        v = float(rr[0])
        return np.array([physio_min_ms <= v <= physio_max_ms], dtype=bool)

    valid = np.ones(n, dtype=bool)
    last_good: float | None = None
    for i, v in enumerate(rr):
        val = float(v)
        if not (physio_min_ms <= val <= physio_max_ms) or not np.isfinite(val):
            valid[i] = False
            continue
        if last_good is None:
            last_good = val
            continue
        if last_good > 0 and abs(val - last_good) / last_good > rel_threshold:
            valid[i] = False
            continue
        last_good = val
    return valid


def correct_rr_artifacts(
    rr: np.ndarray,
    *,
    rel_threshold: float = ARTIFACT_REL_THRESHOLD,
    physio_min_ms: float = RR_PHYSIO_MIN_MS,
    physio_max_ms: float = RR_PHYSIO_MAX_MS,
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
    iqr_factor: float | None = None,  # noqa: ARG001 — legacy no-op
) -> np.ndarray:
    """Alias: True = валидный удар. См. artifact_mask (Malik ~20%)."""
    del iqr_factor
    return artifact_mask(
        rr,
        rel_threshold=rel_threshold,
        physio_min_ms=physio_min_ms,
        physio_max_ms=physio_max_ms,
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
