"""Сводка по сессии — JSON для API."""

from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np

from hrv_core.analysis import coherence_score, compute_spectrum, mean_rr
from hrv_core.preprocessing import preprocess_rr_session


def session_summary_dict(
    conn: sqlite3.Connection,
    session_id: int,
    baseline_at_start: float | None,
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

    stats = conn.execute(
        "SELECT MIN(rmssd), MAX(rmssd), AVG(rmssd), COUNT(*) "
        "FROM hrv_points WHERE session_id = ?",
        (session_id,),
    ).fetchone()

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
    }
    if stats and stats[3] and stats[3] > 0:
        out["rmssd_min"] = float(stats[0])
        out["rmssd_max"] = float(stats[1])
        out["rmssd_mean"] = float(stats[2])
        out["point_count"] = int(stats[3])
    else:
        out["rmssd_min"] = None
        out["rmssd_max"] = None
        out["rmssd_mean"] = None
        out["point_count"] = 0

    if baseline_at_start is not None and baseline_at_start > 0 and out.get("rmssd_mean"):
        mean_rmssd = float(out["rmssd_mean"])
        out["vs_baseline_pct"] = (mean_rmssd - baseline_at_start) / baseline_at_start * 100.0
    else:
        out["vs_baseline_pct"] = None

    rr_rows = conn.execute(
        "SELECT ts, rr_ms FROM hrv_points WHERE session_id = ? ORDER BY ts",
        (session_id,),
    ).fetchall()
    if rr_rows:
        rr_arr = np.array([r[1] for r in rr_rows], dtype=float)
        ts_arr = np.array([r[0] for r in rr_rows], dtype=float)
        preprocessed = preprocess_rr_session(rr_arr)
        raw_rr = np.array(preprocessed["raw_rr"], dtype=float)
        fft_rr = np.array(preprocessed["fft_input_rr"], dtype=float)
        m_rr = mean_rr(raw_rr)
        out["mean_rr"] = round(m_rr, 1) if m_rr is not None else None
        spec = compute_spectrum(ts_arr, raw_rr, fft_rr=fft_rr)
        if not spec.get("insufficient_data") and spec["freqs"]:
            coherence = coherence_score(
                np.array(spec["freqs"]),
                np.array(spec["power"]),
            )
            out["coherence_score"] = coherence
        else:
            out["coherence_score"] = None
    else:
        out["mean_rr"] = None
        out["coherence_score"] = None

    return out
