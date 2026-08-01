"""Миграция timeline сессий и t₀ analysis."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from hrv_core.analysis import session_analysis
from hrv_core.db import _repair_session_timelines, init_db


def _points(from_sec: float, n: int = 80, step: float = 0.85) -> list[tuple[float, float, float]]:
    return [(from_sec + i * step, 800.0 + (i % 5) * 3, 45.0) for i in range(n)]


class AnalysisT0Tests(unittest.TestCase):
    def test_first_point_at_zero_when_started_predates_rr(self):
        points = _points(17.0)
        result = session_analysis(points, started=0.0, ended=120.0, stable_zone=False)
        self.assertEqual(result["raw_rr_x"][0], 0.0)

    def test_started_used_when_close_to_first_point(self):
        points = _points(16.0)
        result = session_analysis(points, started=16.0, ended=120.0, stable_zone=False)
        self.assertEqual(result["raw_rr_x"][0], 0.0)


class SessionTimelineRepairTests(unittest.TestCase):
    def test_repair_started_to_first_rr(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        path = Path(tmp.name)
        try:
            conn = init_db(path)
            conn.execute(
                "INSERT INTO sessions (id, tag, source, started, ended) VALUES (1, 't', 'mock', 0, 200)"
            )
            conn.execute(
                "INSERT INTO hrv_points (session_id, ts, rr_ms, rmssd) VALUES (1, 17.0, 800, 45)"
            )
            conn.commit()
            _repair_session_timelines(conn)
            row = conn.execute("SELECT started FROM sessions WHERE id = 1").fetchone()
            self.assertAlmostEqual(row[0], 17.0, places=3)
            conn.close()
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
