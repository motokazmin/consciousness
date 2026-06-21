"""Post-session HRV analysis: Poincaré, Welch PSD, SDNN trends, coherence score."""

from __future__ import annotations

from typing import Any

import numpy as np

from hrv_core.preprocessing import (
    MIN_STABLE_ZONE_SEC,
    SDNN_INITIAL_CROP_SEC,
    STABLE_ZONE_TRIM_SEC,
    preprocess_rr_session,
    stable_zone_mask,
)

MIN_POINCARE_RR = 10
MIN_SPECTRAL_SEC = 60.0
COHERENCE_BAND = (0.08, 0.12)
SPECTRUM_MAX_HZ = 0.5
RESONANCE_BAND = (0.04, 0.15)
DEFAULT_FS = 4.0


def mean_rr(rr: np.ndarray) -> float | None:
    if rr.size == 0:
        return None
    return float(np.mean(rr))


def _decimate_indices(n: int, max_points: int) -> np.ndarray:
    if n <= max_points:
        return np.arange(n)
    return np.linspace(0, n - 1, max_points, dtype=int)


def poincare_pairs(
    rr: np.ndarray,
    max_points: int = 2500,
    *,
    bounds: dict[str, int] | None = None,
) -> dict[str, Any]:
    if rr.size < MIN_POINCARE_RR:
        return {
            "points": [],
            "sd1": None,
            "sd2": None,
            "bounds": bounds,
            "insufficient_data": True,
            "message": f"Нужно ≥ {MIN_POINCARE_RR} RR-интервалов",
        }

    x = rr[:-1].astype(float)
    y = rr[1:].astype(float)
    idx = _decimate_indices(x.size, max_points)
    points = [{"x": round(float(x[i]), 2), "y": round(float(y[i]), 2)} for i in idx]

    diff = np.diff(rr.astype(float))
    sd1 = float(np.std(diff, ddof=1) / np.sqrt(2)) if diff.size >= 2 else None
    sd2_raw = float(np.std(rr.astype(float), ddof=1)) if rr.size >= 2 else None
    sd2 = float(np.sqrt(max(0.0, 2 * sd2_raw**2 - sd1**2)) if sd1 is not None and sd2_raw is not None else None)

    return {
        "points": points,
        "sd1": round(sd1, 2) if sd1 is not None else None,
        "sd2": round(sd2, 2) if sd2 is not None else None,
        "bounds": bounds,
        "insufficient_data": False,
    }


def resample_tachogram(
    ts: np.ndarray,
    rr: np.ndarray,
    fs: float = DEFAULT_FS,
    *,
    value_rr: np.ndarray | None = None,
) -> np.ndarray | None:
    if ts.size < 2 or rr.size < 2:
        return None

    t0 = float(ts[0])
    t_end = float(ts[-1])
    duration = t_end - t0
    if duration < MIN_SPECTRAL_SEC:
        return None

    timing_rr = rr.astype(float)
    values = value_rr.astype(float) if value_rr is not None else timing_rr

    beat_times = np.cumsum(timing_rr / 1000.0)
    beat_times = beat_times - beat_times[0] + (float(ts[0]) - t0)

    grid = np.arange(0.0, duration, 1.0 / fs)
    if grid.size < int(MIN_SPECTRAL_SEC * fs):
        return None

    signal = np.interp(grid, beat_times[: timing_rr.size], values[: timing_rr.size])
    signal = signal - np.mean(signal)
    return signal


def welch_psd(signal: np.ndarray, fs: float = DEFAULT_FS) -> tuple[np.ndarray, np.ndarray]:
    n = signal.size
    if n < 64:
        freqs = np.fft.rfftfreq(n, d=1.0 / fs)
        power = np.abs(np.fft.rfft(signal)) ** 2 / n
        return freqs, power

    seg_len = min(256, n // 4)
    if seg_len < 32:
        seg_len = 32
    overlap = seg_len // 2
    step = seg_len - overlap
    window = np.hanning(seg_len)

    accum = None
    count = 0
    for start in range(0, n - seg_len + 1, step):
        segment = signal[start : start + seg_len] * window
        fft_vals = np.fft.rfft(segment)
        psd = (np.abs(fft_vals) ** 2) / (fs * (window**2).sum())
        if accum is None:
            accum = psd
        else:
            accum += psd
        count += 1

    if accum is None or count == 0:
        freqs = np.fft.rfftfreq(n, d=1.0 / fs)
        power = np.abs(np.fft.rfft(signal)) ** 2 / n
        return freqs, power

    power = accum / count
    freqs = np.fft.rfftfreq(seg_len, d=1.0 / fs)
    return freqs, power


def coherence_score(freqs: np.ndarray, power: np.ndarray) -> float | None:
    if freqs.size == 0 or power.size == 0:
        return None

    mask_total = (freqs >= 0.0) & (freqs <= SPECTRUM_MAX_HZ)
    if not np.any(mask_total):
        return None

    total_power = float(np.sum(power[mask_total]))
    if total_power <= 0:
        return None

    lo, hi = COHERENCE_BAND
    mask_band = (freqs >= lo) & (freqs <= hi)
    band_power = float(np.sum(power[mask_band])) if np.any(mask_band) else 0.0
    return round(min(100.0, band_power / total_power * 100.0), 1)


def compute_spectrum(
    ts: np.ndarray,
    rr: np.ndarray,
    fs: float = DEFAULT_FS,
    *,
    fft_rr: np.ndarray | None = None,
) -> dict[str, Any]:
    if fft_rr is not None:
        signal = resample_tachogram(ts, rr, fs, value_rr=fft_rr)
    else:
        signal = resample_tachogram(ts, rr, fs)

    if signal is None:
        return {
            "freqs": [],
            "power": [],
            "peak_freq": None,
            "peak_power": None,
            "insufficient_data": True,
            "message": f"Нужно ≥ {int(MIN_SPECTRAL_SEC)} с записи",
        }

    freqs, power = welch_psd(signal, fs)
    mask = freqs <= SPECTRUM_MAX_HZ
    freqs = freqs[mask]
    power = power[mask].copy()
    power[freqs < 0.001] = 0.0

    peak_freq = None
    peak_power = None
    if freqs.size > 0:
        lo, hi = RESONANCE_BAND
        resonance = (freqs >= lo) & (freqs <= hi)
        if np.any(resonance):
            band_freqs = freqs[resonance]
            band_power = power[resonance]
            peak_idx = int(np.argmax(band_power))
            peak_freq = round(float(band_freqs[peak_idx]), 4)
            peak_power = round(float(band_power[peak_idx]), 6)
        else:
            nonzero = freqs > 0.001
            if np.any(nonzero):
                band_power = power[nonzero]
                peak_idx = int(np.argmax(band_power))
                peak_freq = round(float(freqs[nonzero][peak_idx]), 4)
                peak_power = round(float(band_power[peak_idx]), 6)

    return {
        "freqs": [round(float(f), 4) for f in freqs],
        "power": [round(float(p), 6) for p in power],
        "peak_freq": peak_freq,
        "peak_power": peak_power,
        "insufficient_data": False,
    }


def moving_sdnn(
    ts: np.ndarray,
    rr: np.ndarray,
    t0: float,
    window_sec: float = 60.0,
    max_points: int = 500,
    *,
    crop_initial_sec: float = SDNN_INITIAL_CROP_SEC,
) -> list[dict[str, float]]:
    if ts.size < MIN_POINCARE_RR:
        return []

    xs: list[float] = []
    ys: list[float] = []
    rr_f = rr.astype(float)

    for i in range(rr_f.size):
        x = float(ts[i] - t0)
        if x < crop_initial_sec:
            continue
        lo = float(ts[i] - window_sec)
        mask = (ts >= lo) & (ts <= ts[i])
        window = rr_f[mask]
        if window.size < 2:
            continue
        xs.append(round(x, 2))
        ys.append(round(float(np.std(window, ddof=1)), 2))

    if not xs:
        return []

    idx = _decimate_indices(len(xs), max_points)
    return [{"x": xs[i], "sdnn": ys[i]} for i in idx]


def rmssd_trend(
    ts: np.ndarray,
    rmssd: np.ndarray,
    t0: float,
    max_points: int = 500,
) -> list[dict[str, float]]:
    if ts.size == 0:
        return []
    xs = [round(float(t - t0), 2) for t in ts]
    ys = [round(float(r), 2) for r in rmssd]
    idx = _decimate_indices(len(xs), max_points)
    return [{"x": xs[i], "rmssd": ys[i]} for i in idx]


def raw_rr_timeline(
    ts: np.ndarray,
    raw_rr: np.ndarray,
    t0: float,
) -> tuple[list[float], list[float]]:
    xs = [round(float(t - t0), 3) for t in ts]
    ys = [round(float(r), 2) for r in raw_rr]
    return xs, ys


def session_analysis(
    points: list[tuple[float, float, float]],
    started: float,
    ended: float | None,
    *,
    poincare_max: int = 2500,
    trend_max: int = 500,
    stable_zone: bool = False,
    trim_start_sec: float = STABLE_ZONE_TRIM_SEC,
    trim_end_sec: float = STABLE_ZONE_TRIM_SEC,
) -> dict[str, Any]:
    """Full analysis payload from (ts, rr_ms, rmssd) rows."""
    trim_meta = {
        "start_sec": trim_start_sec,
        "end_sec": trim_end_sec,
        "applied": False,
    }
    if not points:
        return {
            "duration_sec": 0.0,
            "mean_rr": None,
            "coherence_score": None,
            "stable_zone": False,
            "trim": trim_meta,
            "poincare": {"points": [], "insufficient_data": True, "message": "Нет данных"},
            "spectrum": {"freqs": [], "power": [], "insufficient_data": True, "message": "Нет данных"},
            "sdnn_trend": [],
            "rmssd_trend": [],
            "raw_rr": [],
            "raw_rr_x": [],
        }

    ts = np.array([p[0] for p in points], dtype=float)
    rr = np.array([p[1] for p in points], dtype=float)
    rmssd = np.array([p[2] for p in points], dtype=float)
    t0 = float(ts[0])

    duration_sec = float(ended - started) if ended and started else float(ts[-1] - t0)
    if duration_sec <= 0:
        duration_sec = float(ts[-1] - t0)

    full_rr_x, full_rr_y = raw_rr_timeline(ts, rr, t0)

    if stable_zone:
        mask = stable_zone_mask(ts, trim_start_sec=trim_start_sec, trim_end_sec=trim_end_sec)
        total_dur = float(ts[-1] - ts[0])
        trim_meta["applied"] = bool(
            total_dur >= trim_start_sec + trim_end_sec + MIN_STABLE_ZONE_SEC
            and mask.sum() >= MIN_POINCARE_RR
            and mask.sum() < ts.size
        )
        if trim_meta["applied"]:
            ts_a = ts[mask]
            rr_a = rr[mask]
        else:
            ts_a = ts
            rr_a = rr
    else:
        ts_a = ts
        rr_a = rr

    preprocessed = preprocess_rr_session(rr_a)
    analysis_rr = np.array(preprocessed["raw_rr"], dtype=float)
    fft_rr = np.array(preprocessed["fft_input_rr"], dtype=float)
    poincare_bounds = preprocessed["poincare_bounds"]

    spectrum = compute_spectrum(ts_a, analysis_rr, fft_rr=fft_rr)
    coherence = None
    if not spectrum.get("insufficient_data"):
        freqs = np.array(spectrum["freqs"])
        power = np.array(spectrum["power"])
        coherence = coherence_score(freqs, power)

    return {
        "duration_sec": round(duration_sec, 2),
        "mean_rr": round(mean_rr(analysis_rr), 1) if mean_rr(analysis_rr) is not None else None,
        "coherence_score": coherence,
        "stable_zone": stable_zone and trim_meta["applied"],
        "trim": trim_meta,
        "raw_rr": full_rr_y,
        "raw_rr_x": full_rr_x,
        "poincare": poincare_pairs(
            analysis_rr, max_points=poincare_max, bounds=poincare_bounds
        ),
        "spectrum": spectrum,
        "sdnn_trend": moving_sdnn(ts_a, analysis_rr, t0, max_points=trend_max),
        "rmssd_trend": rmssd_trend(ts, rmssd, t0, max_points=trend_max),
    }


def progress_session_analysis(
    points: list[tuple[float, float, float]],
    started: float,
    ended: float | None,
    rmssd_mean: float | None,
    *,
    stable_zone: bool = False,
    trim_start_sec: float = STABLE_ZONE_TRIM_SEC,
    trim_end_sec: float = STABLE_ZONE_TRIM_SEC,
) -> dict[str, Any]:
    """Compact analysis for multi-session overlay."""
    full = session_analysis(
        points,
        started,
        ended,
        poincare_max=400,
        trend_max=500,
        stable_zone=stable_zone,
        trim_start_sec=trim_start_sec,
        trim_end_sec=trim_end_sec,
    )
    # Для overlay Poincaré — RR только из стабильной зоны (если trim применён).
    poincare_rr = full["raw_rr"]
    poincare_rr_x = full["raw_rr_x"]
    if full.get("stable_zone"):
        trim = full.get("trim") or {}
        t_start = trim.get("start_sec", 0)
        t_end = full["duration_sec"] - trim.get("end_sec", 0)
        poincare_rr = []
        poincare_rr_x = []
        for x, y in zip(full["raw_rr_x"], full["raw_rr"]):
            if t_start <= x <= t_end:
                poincare_rr_x.append(x)
                poincare_rr.append(y)
    return {
        "mean_rr": full["mean_rr"],
        "coherence_score": full["coherence_score"],
        "stable_zone": full.get("stable_zone", False),
        "trim": full.get("trim"),
        "rmssd_mean": round(rmssd_mean, 1) if rmssd_mean is not None else None,
        "duration_sec": full["duration_sec"],
        "raw_rr": poincare_rr,
        "raw_rr_x": poincare_rr_x,
        "poincare_outline": full["poincare"].get("points", []),
        "poincare_bounds": full["poincare"].get("bounds"),
        "spectrum": full["spectrum"],
        "sdnn_trend": full["sdnn_trend"],
    }
