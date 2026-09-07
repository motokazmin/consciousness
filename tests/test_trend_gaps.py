"""Тесты тренда RMSSD/SDNN: счёт по исправленному ряду, обрезка старта, разрыв по паузе."""

from __future__ import annotations

import numpy as np

from hrv_core.analysis import (
    find_ts_gaps,
    moving_sdnn,
    progress_session_analysis,
    rmssd_trend,
    session_analysis,
)
from hrv_core.preprocessing import SDNN_INITIAL_CROP_SEC


def _flat_points(n: int, rr_ms: float = 800.0, live_rmssd: float = 0.0) -> list[tuple[float, float, float]]:
    ts = np.arange(n, dtype=float)
    rr = np.full(n, rr_ms)
    live = np.full(n, live_rmssd)
    return list(zip(ts.tolist(), rr.tolist(), live.tolist()))


def test_find_ts_gaps_detects_pause_over_threshold():
    ts = np.array([0.0, 1.0, 2.0, 8.0, 9.0])
    assert find_ts_gaps(ts, gap_sec=4.0) == [(2.0, 8.0)]


def test_find_ts_gaps_ignores_short_pauses():
    ts = np.array([0.0, 1.0, 3.5, 5.0])
    assert find_ts_gaps(ts, gap_sec=4.0) == []


def test_rmssd_trend_ignores_live_column_uses_corrected_rr():
    """Раньше тренд рисовался по hrv_points.rmssd — живому расчёту по нефильтрованному
    буферу. Единичный выброс RR (надевание ремня) корректируется, а «живая» колонка
    с тем же выбросом всё ещё несла бы его в тренд — этого больше не должно быть.
    """
    n = 200
    ts = np.arange(n, dtype=float)
    rr = np.full(n, 800.0)
    rr[5] = 1800.0  # артефакт вроде контакта ремня — correct_rr_artifacts его уберёт
    bogus_live_rmssd = np.full(n, 999.0)  # то, что якобы лежит в БД как живой расчёт
    points = list(zip(ts.tolist(), rr.tolist(), bogus_live_rmssd.tolist()))

    result = session_analysis(points, started=0.0, ended=float(n))
    values = [p["rmssd"] for p in result["rmssd_trend"] if p["rmssd"] is not None]

    assert values
    assert max(values) < 50.0  # далеко от «живых» 999 и от выброса 1800


def test_rmssd_trend_crops_initial_seconds():
    points = _flat_points(120)
    result = session_analysis(points, started=0.0, ended=120.0)
    xs = [p["x"] for p in result["rmssd_trend"]]
    assert xs
    assert min(xs) >= SDNN_INITIAL_CROP_SEC


def test_rmssd_trend_direct_matches_moving_window_rmssd():
    ts = np.arange(0.0, 130.0, 1.0)
    rr = 800.0 + 20.0 * np.sin(np.linspace(0, 6, ts.size))
    trend = rmssd_trend(ts, rr, t0=0.0, window_sec=60.0, crop_initial_sec=20.0, gaps=[])
    assert trend
    # окно последней точки — последние 60 значений ряда
    expected = float(np.sqrt(np.mean(np.diff(rr[-61:]) ** 2)))
    assert abs(trend[-1]["rmssd"] - round(expected, 2)) < 1e-6


def test_trend_breaks_on_ts_gap_and_reports_in_summary():
    ts = np.array(list(range(0, 100)) + list(range(110, 210)), dtype=float)
    rr = np.full(ts.size, 800.0)
    points = list(zip(ts.tolist(), rr.tolist(), rr.tolist()))

    result = session_analysis(points, started=0.0, ended=float(ts[-1]))

    assert result["gaps"] == [{"t_start": 99.0, "t_end": 110.0, "rejected": True}]

    broken_rmssd = [p for p in result["rmssd_trend"] if p["rmssd"] is None]
    broken_sdnn = [p for p in result["sdnn_trend"] if p["sdnn"] is None]
    assert broken_rmssd
    assert broken_sdnn

    # точки далеко от разрыва (в конце записи) остаются валидными
    tail = [p for p in result["rmssd_trend"] if p["x"] > 170.0]
    assert tail and all(p["rmssd"] is not None for p in tail)

    assert result["break_summary"]["broken_minutes"] >= 1
    assert result["break_summary"]["total_minutes"] >= 1


def test_moving_sdnn_none_when_window_has_gap():
    ts = np.array(list(range(0, 60)) + list(range(70, 130)), dtype=float)
    rr = np.full(ts.size, 800.0) + np.arange(ts.size) % 3
    gaps = find_ts_gaps(ts)
    trend = moving_sdnn(ts, rr, t0=0.0, crop_initial_sec=0.0, gaps=gaps)
    # первая точка после разрыва ещё видит его в своём 60с окне
    just_after_gap = next(p for p in trend if p["x"] == 70.0)
    assert just_after_gap["sdnn"] is None


def test_quality_strip_tracks_corrected_fraction_per_minute():
    n = 120
    ts = np.arange(n, dtype=float)
    rr = np.full(n, 800.0)
    rr[10] = 1600.0  # артефакт в первую минуту
    points = list(zip(ts.tolist(), rr.tolist(), rr.tolist()))

    result = session_analysis(points, started=0.0, ended=float(n))
    strip = {p["x"]: p["corrected_fraction"] for p in result["quality_strip"]}

    assert strip[0.0] > 0.0
    assert strip[60.0] == 0.0


# ── Второй заход: _decimate_rows резал ВХОД session_analysis, из-за чего
# RMSSD/SD1 считались как разности уже не соседних (после прореживания)
# ударов — величины расходились с реальными почти вдвое на длинных записях.
# Теперь децимация — только на выходе (raw_rr/analysis_rr тахограммы),
# расчёт всегда идёт по полному ряду; см. session_analysis(raw_rr_max=...).

import time


def _long_session_points(n: int = 20000, seed: int = 0) -> list[tuple[float, float, float]]:
    rng = np.random.default_rng(seed)
    rr = 800.0 + rng.normal(0.0, 20.0, n)
    ts = np.cumsum(rr / 1000.0)
    ts = ts - ts[0]
    live_rmssd = np.zeros(n)
    return list(zip(ts.tolist(), rr.tolist(), live_rmssd.tolist()))


def test_session_analysis_metrics_independent_of_raw_rr_max():
    """raw_rr_max режет только отображаемую тахограмму — метрики (mean_rr,
    coherence, SD1, значения самих трендов) не должны зависеть от него.
    """
    points = _long_session_points(20000)
    duration = points[-1][0]

    small = session_analysis(points, started=0.0, ended=duration, raw_rr_max=50)
    full = session_analysis(points, started=0.0, ended=duration, raw_rr_max=None)

    assert len(small["raw_rr"]) == 50
    assert len(full["raw_rr"]) == len(points)
    assert len(small["raw_rr"]) != len(full["raw_rr"])

    assert small["mean_rr"] == full["mean_rr"]
    assert small["coherence_score"] == full["coherence_score"]
    assert small["poincare"]["sd1"] == full["poincare"]["sd1"]
    assert small["sdnn_trend"] == full["sdnn_trend"]
    assert small["rmssd_trend"] == full["rmssd_trend"]
    assert small["break_summary"] == full["break_summary"]


def test_session_analysis_stays_fast_on_long_session():
    """Регрессия на O(n²) в скользящем окне тренда (маска по всему ts на
    каждой точке): на реальной ночной сессии (~30 тыс. ударов) это отдавало
    секунды на один тренд. Ориентир из задачи — весь разбор укладывается
    в секунду; берём щедрый запас под медленный CI.
    """
    points = _long_session_points(20000)
    duration = points[-1][0]

    start = time.perf_counter()
    session_analysis(points, started=0.0, ended=duration)
    elapsed = time.perf_counter() - start

    assert elapsed < 5.0, f"session_analysis слишком медленный: {elapsed:.2f}s на {len(points)} точек"


# ── Третий заход: тот же класс бага в /api/progress/analysis
# (progress_session_analysis) — hrv_web/server.py резал hrv_points ДО расчёта
# через _decimate_rows(rows, max_points_per_session). SD1/coherence/sdnn_trend
# там точно так же считаются на разностях соседних ударов.

def test_progress_session_analysis_metrics_independent_of_raw_rr_max():
    points = _long_session_points(20000, seed=1)
    duration = points[-1][0]

    small = progress_session_analysis(points, started=0.0, ended=duration, rmssd_mean=42.0, raw_rr_max=50)
    full = progress_session_analysis(points, started=0.0, ended=duration, rmssd_mean=42.0, raw_rr_max=None)

    assert len(small["raw_rr"]) == 50
    assert len(full["raw_rr"]) != len(small["raw_rr"])

    assert small["mean_rr"] == full["mean_rr"]
    assert small["coherence_score"] == full["coherence_score"]
    assert small["sd1"] == full["sd1"]
    assert small["sdnn_trend"] == full["sdnn_trend"]
