"""SQLite: схема, baseline, миграции."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_types (
            slug           TEXT PRIMARY KEY,
            label          TEXT NOT NULL,
            phrase_prefix  TEXT,
            mock_profile   TEXT NOT NULL DEFAULT 'default',
            chart_profile  TEXT NOT NULL DEFAULT 'default',
            is_custom      INTEGER NOT NULL DEFAULT 0
        )""")
    st_cols = {row[1] for row in conn.execute("PRAGMA table_info(session_types)")}
    if "cluster_marker" in st_cols:
        conn.executescript("""
            CREATE TABLE session_types_new (
                slug           TEXT PRIMARY KEY,
                label          TEXT NOT NULL,
                phrase_prefix  TEXT,
                mock_profile   TEXT NOT NULL DEFAULT 'default',
                chart_profile  TEXT NOT NULL DEFAULT 'default',
                is_custom      INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO session_types_new
                (slug, label, phrase_prefix, mock_profile, chart_profile, is_custom)
            SELECT slug, label, phrase_prefix, mock_profile,
                   COALESCE(chart_profile, 'default'), is_custom
            FROM session_types;
            DROP TABLE session_types;
            ALTER TABLE session_types_new RENAME TO session_types;
        """)
        conn.commit()
        st_cols = {row[1] for row in conn.execute("PRAGMA table_info(session_types)")}
    chart_profile_added = "chart_profile" not in st_cols
    if chart_profile_added:
        conn.execute(
            "ALTER TABLE session_types ADD COLUMN chart_profile TEXT NOT NULL DEFAULT 'default'"
        )
    from hrv_core.session_types import SESSION_TYPES as _ST

    def _upsert_builtin_session_types() -> None:
        for st in _ST.values():
            conn.execute(
                "INSERT OR IGNORE INTO session_types "
                "(slug, label, phrase_prefix, mock_profile, chart_profile, is_custom) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (st.slug, st.label, st.phrase_prefix, st.mock_profile, st.chart_profile),
            )
            conn.execute(
                "UPDATE session_types SET label = ?, phrase_prefix = ?, mock_profile = ?, "
                "chart_profile = ? "
                "WHERE slug = ? AND is_custom = 0",
                (st.label, st.phrase_prefix, st.mock_profile, st.chart_profile, st.slug),
            )

    def _remove_stale_builtin_types() -> None:
        builtin = tuple(_ST.keys())
        placeholders = ",".join("?" * len(builtin))
        conn.execute(
            f"DELETE FROM session_types WHERE is_custom = 0 AND slug NOT IN ({placeholders})",
            builtin,
        )

    if not conn.execute("SELECT 1 FROM session_types LIMIT 1").fetchone():
        _upsert_builtin_session_types()
    else:
        _upsert_builtin_session_types()
        _remove_stale_builtin_types()
        if chart_profile_added:
            for st in _ST.values():
                conn.execute(
                    "UPDATE session_types SET chart_profile = ? "
                    "WHERE slug = ? AND is_custom = 0",
                    (st.chart_profile, st.slug),
                )
    conn.commit()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "session_name" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN session_name TEXT")
    if "participant" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN participant TEXT")
    if "drift_events" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN drift_events INTEGER DEFAULT 0")
    if "opt_guided_phrases" not in cols:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN opt_guided_phrases INTEGER NOT NULL DEFAULT 0"
        )
    if "opt_audio_biofeedback" not in cols:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN opt_audio_biofeedback INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meditation_phrase_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER NOT NULL,
            phrase_file     TEXT NOT NULL,
            played_at       REAL NOT NULL,
            rn_before       REAL,
            rmssd_before    REAL,
            rn_after_30s    REAL,
            rmssd_after_30s REAL
        )""")
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
    log.debug("Baseline updated for hours %s", updated)


def delete_session(conn: sqlite3.Connection, session_id: int) -> bool:
    """Удалить сессию и связанные точки/логи фраз. Возвращает True, если сессия была."""
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM hrv_points WHERE session_id = ?", (session_id,))
    conn.execute(
        "DELETE FROM meditation_phrase_log WHERE session_id = ?", (session_id,)
    )
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    return True


def wipe_all_history(conn: sqlite3.Connection) -> int:
    """Удалить всю историю. Возвращает число удалённых сессий."""
    n_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    conn.execute("DELETE FROM hrv_points")
    conn.execute("DELETE FROM meditation_phrase_log")
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM baseline")
    conn.commit()
    return int(n_sessions)
