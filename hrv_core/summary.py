"""Сводка по сессии — JSON для API."""

from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np

from hrv_core.analysis import coherence_score, compute_spectrum, mean_rr, rmssd_trend
from hrv_core.preprocessing import correct_rr_artifacts, preprocess_rr_session


def session_summary_dict(
    conn: sqlite3.Connection,
    session_id: int,
    baseline_at_start: float | None,  # не используется, см. vs_baseline_pct ниже
    drift_count: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT tag, session_name, participant, source, started, ended FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        return None
    tag, session_name, participant, source, started, ended = row
    if ended is None or started is None:
        return None

    out: dict[str, Any] = {
        "id": session_id,
        "tag": tag,
        "session_name": session_name,
        "participant": participant,
        "source": source,
        "started": started,
        "ended": ended,
        "duration_sec": ended - started,
        "drift_events": drift_count,
        "rmssd_mean": None,
        "rmssd_median": None,
        "rmssd_p10": None,
        "rmssd_p90": None,
        "rmssd_min": None,
        "rmssd_max": None,
        "point_count": 0,
        # vs baseline больше не считается: baseline (таблица по часам) и
        # «живая» колонка hrv_points.rmssd, по которым он шёл, собраны по
        # сырому буферу с артефактами — на ночах сравнение давало +114% на
        # сбоях датчика. Сопоставимый baseline по исправленному ряду — отдельно.
        "vs_baseline_pct": None,
    }

    rr_rows = conn.execute(
        "SELECT ts, rr_ms FROM hrv_points WHERE session_id = ? ORDER BY ts",
        (session_id,),
    ).fetchall()
    if rr_rows:
        rr_arr = np.array([r[1] for r in rr_rows], dtype=float)
        ts_arr = np.array([r[0] for r in rr_rows], dtype=float)
        rr_corr, _, _ = correct_rr_artifacts(rr_arr)
        preprocessed = preprocess_rr_session(rr_corr)
        analysis_rr = np.array(preprocessed["raw_rr"], dtype=float)
        fft_rr = np.array(preprocessed["fft_input_rr"], dtype=float)
        out["point_count"] = int(rr_arr.size)
        # RMSSD — по скользящему окну над исправленным рядом, тем же способом,
        # что график RMSSD в архиве. Раньше сводка брала «живую» колонку
        # hrv_points.rmssd, посчитанную по нефильтрованному буферу: один сбой
        # датчика давал 600+ мс и завышал среднее ночи почти вдвое.
        trend = rmssd_trend(ts_arr, rr_corr, float(started), max_points=10**7)
        vals = np.array([p["rmssd"] for p in trend if p["rmssd"] is not None], dtype=float)
        if vals.size:
            out["rmssd_mean"] = round(float(vals.mean()), 1)
            out["rmssd_median"] = round(float(np.median(vals)), 1)
            out["rmssd_p10"] = round(float(np.percentile(vals, 10)), 1)
            out["rmssd_p90"] = round(float(np.percentile(vals, 90)), 1)
            out["rmssd_min"] = round(float(vals.min()), 1)
            out["rmssd_max"] = round(float(vals.max()), 1)
        m_rr = mean_rr(analysis_rr)
        out["mean_rr"] = round(m_rr, 1) if m_rr is not None else None
        spec = compute_spectrum(ts_arr, analysis_rr, fft_rr=fft_rr)
        if not spec.get("insufficient_data") and spec["freqs"]:
            coherence = coherence_score(
                np.array(spec["freqs"]),
                np.array(spec["power"]),
                spec.get("peak_freq"),
            )
            out["coherence_score"] = coherence
        else:
            out["coherence_score"] = None
    else:
        out["mean_rr"] = None
        out["coherence_score"] = None

    return out
