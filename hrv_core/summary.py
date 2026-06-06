"""Сводка по сессии — JSON для API."""

from __future__ import annotations

import sqlite3
from typing import Any


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

    return out
