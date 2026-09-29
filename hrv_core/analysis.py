"""Post-session HRV analysis: Poincaré, Welch PSD, SDNN trends, coherence score."""

from __future__ import annotations

from typing import Any

import numpy as np

from hrv_core.constants import RMSSD_WINDOW_SEC, TREND_BREAK_GAP_SEC
from hrv_core.preprocessing import (
    SDNN_INITIAL_CROP_SEC,
    correct_rr_artifacts,
    preprocess_rr_session,
)

MIN_POINCARE_RR = 10
MIN_SPECTRAL_SEC = 60.0
QUALITY_BUCKET_SEC = 60.0
"""Размер минутного окна для полоски качества и счётчика разрывов в сводке."""
COHERENCE_HALF_WIDTH = 0.02
"""Ширина полосы вокруг peak_freq для расчёта когерентности (±Гц).
Адаптивная полоса: score высокий когда спектр остроконечный,
низкий когда размазан по нескольким пикам.
"""
SPECTRUM_MAX_HZ = 0.5
RESONANCE_BAND = (0.04, 0.15)
DEFAULT_FS = 4.0


def mean_rr(rr: np.ndarray) -> float | None:
    if rr.size == 0:
        return None
    return float(np.mean(rr))


def session_sd1(rr: np.ndarray) -> float | None:
    """SD1 по RR-ряду после коррекции артефактов (как в session_analysis)."""
    if rr.size < MIN_POINCARE_RR:
        return None
    rr_f, _, _ = correct_rr_artifacts(rr.astype(float))
    if rr_f.size < MIN_POINCARE_RR:
        return None
    analysis_rr = np.array(preprocess_rr_session(rr_f)["raw_rr"], dtype=float)
    return poincare_pairs(analysis_rr, max_points=analysis_rr.size).get("sd1")


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


def coherence_score(
    freqs: np.ndarray,
    power: np.ndarray,
    peak_freq: float | None,
) -> float | None:
    """Доля мощности в полосе ±COHERENCE_HALF_WIDTH вокруг peak_freq.

    Адаптивная полоса: score высокий когда спектр остроконечный
    (вся энергия сконцентрирована у пика), низкий когда размазан.
    """
    if freqs.size == 0 or power.size == 0 or peak_freq is None:
        return None

    mask_total = (freqs >= 0.003) & (freqs <= SPECTRUM_MAX_HZ)
    if not np.any(mask_total):
        return None

    total_power = float(np.sum(power[mask_total]))
    if total_power <= 0:
        return None

    lo = peak_freq - COHERENCE_HALF_WIDTH
    hi = peak_freq + COHERENCE_HALF_WIDTH
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


def find_ts_gaps(ts: np.ndarray, gap_sec: float = TREND_BREAK_GAP_SEC) -> list[tuple[float, float]]:
    """Паузы в потоке RR: соседние ts дальше друг от друга, чем gap_sec.

    Удары в паузе физически не были получены (обрыв записи/пересопряжение),
    любое скользящее окно, которое её захватывает, недостоверно.
    Возвращает [(ts_before_gap, ts_after_gap), ...] в исходных (не сдвинутых) ts.
    """
    if ts.size < 2:
        return []
    dt = np.diff(ts)
    idx = np.nonzero(dt > gap_sec)[0]
    return [(float(ts[i]), float(ts[i + 1])) for i in idx]


def _gap_hits_mask(ts: np.ndarray, window_sec: float, gaps: list[tuple[float, float]]) -> np.ndarray:
    """Векторная версия «окно [ts_i - window_sec, ts_i] пересекает хотя бы одну паузу».

    gaps — короткий список (обычно < 100), поэтому цикл идёт по паузам, а не по точкам:
    на каждую паузу — один проход по всему ts целиком.
    """
    hit = np.zeros(ts.size, dtype=bool)
    if not gaps:
        return hit
    lo_bound = ts - window_sec
    for g_start, g_end in gaps:
        hit |= (lo_bound < g_end) & (ts > g_start)
    return hit


def _moving_trend(
    ts: np.ndarray,
    values: np.ndarray,
    t0: float,
    stat: str,
    window_sec: float,
    crop_initial_sec: float,
    gaps: list[tuple[float, float]],
) -> tuple[list[float], list[float | None]]:
    """Общий скользящий тренд по времени: окно window_sec, минимум 2 точки в окне.

    Начало короче crop_initial_sec от t0 не эмитится вовсе (обрезка arm/контакта
    ремня — see SDNN_INITIAL_CROP_SEC). Окно, захватившее паузу в потоке RR
    (find_ts_gaps), эмитится с value=None — кривая рисуется разрывом вместо
    ложного пика/провала на неполных данных.

    Раньше на каждую точку строилась булева маска по всему ts (`(ts>=lo)&(ts<=hi)`) —
    O(n²), на длинной ночной записи (десятки тысяч ударов) секунды на один тренд.
    Здесь — один проход: левая граница окна через np.searchsorted (ts отсортирован,
    граница монотонна), статистика — через префиксные суммы: SDNN — сумма значений
    и сумма квадратов, RMSSD — префиксная сумма квадратов последовательных разностей.
    Оба тренда считаются этой же функцией за одинаковую по устройству O(n log n)-операцию.
    """
    n = values.size
    if n == 0:
        return [], []
    values_f = values.astype(float)

    # Левая граница окна для каждой точки: первый индекс с ts >= ts[i]-window_sec.
    lo_idx = np.searchsorted(ts, ts - window_sec, side="left")
    idx = np.arange(n)
    count = idx - lo_idx + 1  # число точек в окне [lo_idx[i], i]

    if stat == "sdnn":
        s1 = np.concatenate(([0.0], np.cumsum(values_f)))
        s2 = np.concatenate(([0.0], np.cumsum(values_f ** 2)))
        safe_count = np.maximum(count, 2)  # там, где count<2 — всё равно замаскируем
        total = s1[idx + 1] - s1[lo_idx]
        total_sq = s2[idx + 1] - s2[lo_idx]
        variance = (total_sq - total * total / safe_count) / (safe_count - 1)
        raw = np.sqrt(np.clip(variance, 0.0, None))
    else:  # rmssd
        d2 = np.diff(values_f) ** 2 if n > 1 else np.zeros(0)
        d2_prefix = np.concatenate(([0.0], np.cumsum(d2)))  # длина n
        diff_count = np.maximum(idx - lo_idx, 1)
        sumsq = d2_prefix[idx] - d2_prefix[lo_idx]
        raw = np.sqrt(sumsq / diff_count)

    insufficient = count < 2
    hit = _gap_hits_mask(ts, window_sec, gaps)
    bad = insufficient | hit

    x = ts - t0
    keep = x >= crop_initial_sec

    xs = [round(float(v), 2) for v in x[keep]]
    ys = [None if b else round(float(v), 2) for b, v in zip(bad[keep], raw[keep])]
    return xs, ys


def moving_sdnn(
    ts: np.ndarray,
    rr: np.ndarray,
    t0: float,
    window_sec: float = 60.0,
    max_points: int = 500,
    *,
    crop_initial_sec: float = SDNN_INITIAL_CROP_SEC,
    gaps: list[tuple[float, float]] | None = None,
) -> list[dict[str, float | None]]:
    if ts.size < MIN_POINCARE_RR:
        return []

    xs, ys = _moving_trend(
        ts, rr, t0, "sdnn",
        window_sec, crop_initial_sec,
        gaps if gaps is not None else find_ts_gaps(ts),
    )
    if not xs:
        return []

    idx = _decimate_indices(len(xs), max_points)
    return [{"x": xs[i], "sdnn": ys[i]} for i in idx]


def rmssd_trend(
    ts: np.ndarray,
    rr: np.ndarray,
    t0: float,
    window_sec: float = RMSSD_WINDOW_SEC,
    max_points: int = 500,
    *,
    crop_initial_sec: float = SDNN_INITIAL_CROP_SEC,
    gaps: list[tuple[float, float]] | None = None,
) -> list[dict[str, float | None]]:
    """Скользящий RMSSD (окно RMSSD_WINDOW_SEC) по исправленному ряду rr.

    Раньше рисовался по «живой» колонке hrv_points.rmssd — посчитанной на лету
    по нефильтрованному буферу (см. hrv_core/pipeline.compute_rmssd) — и любой
    единичный выброс RR перед коррекцией артефактов давал пик, который сплющивал
    всю кривую. Здесь — тот же способ, каким moving_sdnn считает SDNN: окно по
    времени над rr после correct_rr_artifacts.
    """
    if ts.size < MIN_POINCARE_RR:
        return []

    xs, ys = _moving_trend(
        ts, rr, t0, "rmssd",
        window_sec, crop_initial_sec,
        gaps if gaps is not None else find_ts_gaps(ts),
    )
    if not xs:
        return []

    idx = _decimate_indices(len(xs), max_points)
    return [{"x": xs[i], "rmssd": ys[i]} for i in idx]


def quality_strip(
    ts: np.ndarray,
    valid_mask: np.ndarray,
    t0: float,
    bucket_sec: float = QUALITY_BUCKET_SEC,
) -> list[dict[str, float]]:
    """Доля исправленных ударов (valid_mask=False) по минутным окнам от t0.

    Замена прятанию точек по доле артефактов в окне (см. спецификацию —
    такой фильтр стирает обычную кривую наравне с интересной): полоска
    качества только помечает, ничего не скрывает.
    """
    if ts.size == 0:
        return []
    rel = ts - t0
    bucket_idx = np.clip(np.floor(rel / bucket_sec).astype(int), 0, None)
    strip: list[dict[str, float]] = []
    for b in range(int(bucket_idx.max()) + 1):
        in_bucket = bucket_idx == b
        n = int(in_bucket.sum())
        if n == 0:
            continue
        corrected_fraction = float((~valid_mask[in_bucket]).sum()) / n
        strip.append({"x": round(b * bucket_sec, 1), "corrected_fraction": round(corrected_fraction, 4)})
    return strip


def break_summary(
    gaps: list[tuple[float, float]],
    t0: float,
    duration_sec: float,
    bucket_sec: float = QUALITY_BUCKET_SEC,
) -> dict[str, int]:
    """«Минут с разрывами N из M» — разрыв это пауза в RR > TREND_BREAK_GAP_SEC (find_ts_gaps)."""
    if duration_sec <= 0:
        return {"broken_minutes": 0, "total_minutes": 0}
    total_minutes = max(1, int(np.ceil(duration_sec / bucket_sec)))
    broken: set[int] = set()
    for g_start, g_end in gaps:
        b_start = max(0, int(np.floor((g_start - t0) / bucket_sec)))
        b_end = min(total_minutes - 1, int(np.floor((g_end - t0) / bucket_sec)))
        broken.update(range(b_start, b_end + 1))
    return {"broken_minutes": len(broken), "total_minutes": total_minutes}


def raw_rr_timeline(
    ts: np.ndarray,
    raw_rr: np.ndarray,
    t0: float,
    max_points: int | None = None,
) -> tuple[list[float], list[float]]:
    """RR-тахограмма для графика. max_points — децимация выхода (не входа):

    полный ряд нужен целиком для расчёта (RMSSD/SD1 — разности соседних ударов,
    прорежывание входа их портит), а вот отрисовать имеет смысл не больше
    max_points точек — так же, как rmssd_trend/moving_sdnn режут свой выход.
    """
    idx = _decimate_indices(ts.size, max_points) if max_points else np.arange(ts.size)
    xs = [round(float(ts[i] - t0), 3) for i in idx]
    ys = [round(float(raw_rr[i]), 2) for i in idx]
    return xs, ys


def session_analysis(
    points: list[tuple[float, float, float]],
    started: float,
    ended: float | None,
    *,
    poincare_max: int = 2500,
    trend_max: int = 500,
    raw_rr_max: int | None = None,
) -> dict[str, Any]:
    """Full analysis payload from (ts, rr_ms, rmssd) rows.

    Аналитика всегда на corrected RR (Malik ~20% + интерполяция).
    raw_rr_* — сырой ряд как в БД; analysis_rr_* — для всех графиков/метрик.

    points — ВСЕГДА полный ряд сессии, без децимации на входе: RMSSD/SD1/тренды
    считаются как разности соседних ударов, и прореженный вход делает их
    соседями, которыми они не были — величины расходятся с реальными (были
    случаи расхождения почти вдвое на записях длиннее нескольких часов).
    raw_rr_max — децимация ТОЛЬКО отображаемых raw_rr/analysis_rr тахограмм
    (тяжёлый JSON на длинных сессиях); остальные графики режут свой выход сами
    (poincare_max, trend_max, quality_strip — минутные бакеты).
    """
    outlier_meta = {
        "applied": False,
        "removed": 0,
    }
    if not points:
        return {
            "duration_sec": 0.0,
            "mean_rr": None,
            "coherence_score": None,
            "outliers": outlier_meta,
            "poincare": {"points": [], "insufficient_data": True, "message": "Нет данных"},
            "spectrum": {"freqs": [], "power": [], "insufficient_data": True, "message": "Нет данных"},
            "sdnn_trend": [],
            "rmssd_trend": [],
            "gaps": [],
            "quality_strip": [],
            "break_summary": {"broken_minutes": 0, "total_minutes": 0},
            "raw_rr": [],
            "raw_rr_x": [],
            "analysis_rr": [],
            "analysis_rr_x": [],
        }

    ts = np.array([p[0] for p in points], dtype=float)
    rr = np.array([p[1] for p in points], dtype=float)
    # Ось RR: t₀ = первая сохранённая точка (≈ arm / первый RR).
    first_ts = float(ts[0])
    t0 = first_ts
    if started and abs(float(started) - first_ts) <= 1.0:
        t0 = float(started)

    duration_sec = float(ended - t0) if ended else float(ts[-1] - t0)
    if duration_sec <= 0:
        duration_sec = float(ts[-1] - t0)

    full_rr_x, full_rr_y = raw_rr_timeline(ts, rr, t0, max_points=raw_rr_max)

    rr_a, valid_mask, removed = correct_rr_artifacts(rr)
    outlier_meta["removed"] = removed
    outlier_meta["applied"] = removed > 0
    ts_a = ts

    # Паузы в потоке RR — общие для тренда SDNN и RMSSD (тот же ts_a),
    # окна, которые их захватывают, идут в null; сами паузы — для затенения
    # графиков и счётчика «минут с разрывами» в сводке.
    gaps = find_ts_gaps(ts_a)

    preprocessed = preprocess_rr_session(rr_a)
    analysis_rr = np.array(preprocessed["raw_rr"], dtype=float)
    fft_rr = np.array(preprocessed["fft_input_rr"], dtype=float)
    poincare_bounds = preprocessed["poincare_bounds"]

    spectrum = compute_spectrum(ts_a, analysis_rr, fft_rr=fft_rr)
    coherence = None
    if not spectrum.get("insufficient_data"):
        freqs = np.array(spectrum["freqs"])
        power = np.array(spectrum["power"])
        coherence = coherence_score(freqs, power, spectrum.get("peak_freq"))

    analysis_rr_x, analysis_rr_y = raw_rr_timeline(ts_a, rr_a, t0, max_points=raw_rr_max)

    return {
        "duration_sec": round(duration_sec, 2),
        "mean_rr": round(mean_rr(analysis_rr), 1) if mean_rr(analysis_rr) is not None else None,
        "coherence_score": coherence,
        "outliers": outlier_meta,
        "raw_rr": full_rr_y,
        "raw_rr_x": full_rr_x,
        "analysis_rr": analysis_rr_y,
        "analysis_rr_x": analysis_rr_x,
        "poincare": poincare_pairs(
            analysis_rr, max_points=poincare_max, bounds=poincare_bounds
        ),
        "spectrum": spectrum,
        "sdnn_trend": moving_sdnn(ts_a, analysis_rr, t0, max_points=trend_max, gaps=gaps),
        "rmssd_trend": rmssd_trend(ts_a, analysis_rr, t0, max_points=trend_max, gaps=gaps),
        "gaps": [
            {"t_start": round(g_start - t0, 2), "t_end": round(g_end - t0, 2), "rejected": True}
            for g_start, g_end in gaps
        ],
        "quality_strip": quality_strip(ts_a, valid_mask, t0),
        "break_summary": break_summary(gaps, t0, duration_sec),
    }


def _trend_mean(trend, key: str, fallback: float | None) -> float | None:
    vals = [p[key] for p in (trend or []) if p.get(key) is not None]
    if vals:
        return round(float(np.mean(vals)), 1)
    return round(fallback, 1) if fallback is not None else None


def progress_session_analysis(
    points: list[tuple[float, float, float]],
    started: float,
    ended: float | None,
    rmssd_mean: float | None,
    *,
    raw_rr_max: int | None = None,
) -> dict[str, Any]:
    """Compact analysis for multi-session overlay (всегда corrected RR).

    points — полный ряд сессии (см. session_analysis: SD1/coherence/sdnn_trend
    считаются на разностях соседних ударов, децимация входа их портит).
    raw_rr_max — децимация ТОЛЬКО отдаваемой тахограммы raw_rr/raw_rr_x
    (её потом используют для отрисовки облака Пуанкаре на клиенте, см.
    poincarePointsFromRawRr в analysis_charts.js) — не расчёта.
    """
    full = session_analysis(
        points,
        started,
        ended,
        poincare_max=400,
        trend_max=500,
        raw_rr_max=raw_rr_max,
    )
    poincare_rr = full.get("analysis_rr", full["raw_rr"])
    poincare_rr_x = full.get("analysis_rr_x", full["raw_rr_x"])
    return {
        "mean_rr": full["mean_rr"],
        "coherence_score": full["coherence_score"],
        "outliers": full.get("outliers"),
        # Среднее скользящего RMSSD по исправленному ряду — как сводка архива
        # (hrv_core.summary). Прежде сюда шло среднее «живой» колонки с
        # артефактами: для ночи 245 — 30.4 против 18.3 в архиве.
        "rmssd_mean": _trend_mean(full.get("rmssd_trend"), "rmssd", rmssd_mean),
        "duration_sec": full["duration_sec"],
        "raw_rr": poincare_rr,
        "raw_rr_x": poincare_rr_x,
        "poincare_outline": full["poincare"].get("points", []),
        "poincare_bounds": full["poincare"].get("bounds"),
        "sd1": full["poincare"].get("sd1"),
        "spectrum": full["spectrum"],
        "sdnn_trend": full["sdnn_trend"],
    }
