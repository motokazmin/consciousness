"""SQLite: схема, baseline, миграции."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from hrv_core.constants import DB_PATH


def init_db(path: Path | None = None) -> sqlite3.Connection:
    db = path or DB_PATH
    conn = sqlite3.connect(db, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            tag     TEXT,
            source  TEXT,
            started REAL,
            ended   REAL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hrv_points (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            ts         REAL,
            rr_ms      REAL,
            rmssd      REAL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS baseline (
            hour        INTEGER PRIMARY KEY,
            rmssd_mean  REAL,
            n_samples   INTEGER,
            updated_at  REAL
        )""")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "session_name" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN session_name TEXT")
    if "participant" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN participant TEXT")
    if "drift_events" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN drift_events INTEGER DEFAULT 0")
    conn.commit()
    return conn


def load_hour_baseline(conn: sqlite3.Connection, hour: int) -> float | None:
    row = conn.execute(
        "SELECT rmssd_mean FROM baseline WHERE hour = ?", (hour,)
    ).fetchone()
    return float(row[0]) if row else None


def update_session_baseline(conn: sqlite3.Connection, session_id: int) -> None:
    rows = conn.execute("""
        SELECT
            CAST(strftime('%H', datetime(ts, 'unixepoch', 'localtime')) AS INTEGER) AS hour,
            AVG(rmssd)  AS session_mean,
            COUNT(*)    AS n
        FROM hrv_points
        WHERE session_id = ?
        GROUP BY hour
    """, (session_id,)).fetchall()

    if not rows:
        return

    now = time.time()
    for hour, session_mean, n in rows:
        existing = conn.execute(
            "SELECT rmssd_mean, n_samples FROM baseline WHERE hour = ?", (hour,)
        ).fetchone()

        if existing:
            old_mean, old_n = existing
            capped_n = min(old_n, 500)
            new_n = capped_n + n
            new_mean = (old_mean * capped_n + session_mean * n) / new_n
            conn.execute(
                "UPDATE baseline SET rmssd_mean=?, n_samples=?, updated_at=? "
                "WHERE hour=?",
                (new_mean, new_n, now, hour),
            )
        else:
            conn.execute(
                "INSERT INTO baseline (hour, rmssd_mean, n_samples, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (hour, session_mean, n, now),
            )

    conn.commit()
    updated = [r[0] for r in rows]
    print(f"Baseline updated for hours {updated}")
