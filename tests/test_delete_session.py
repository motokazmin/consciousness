"""Тесты delete_session и wipe_all_history."""

import tempfile
import time
import unittest
from pathlib import Path

from hrv_core.db import delete_session, init_db, wipe_all_history


class DeleteSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.conn = init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.db_path.unlink(missing_ok=True)

    def _insert_session(self, sid: int | None = None) -> int:
        now = time.time()
        if sid is None:
            cur = self.conn.execute(
                "INSERT INTO sessions (tag, source, started, ended) VALUES (?, ?, ?, ?)",
                ("focus", "mock", now - 60, now),
            )
            self.conn.commit()
            return int(cur.lastrowid)
        self.conn.execute(
            "INSERT INTO sessions (id, tag, source, started, ended) VALUES (?, ?, ?, ?, ?)",
            (sid, "focus", "mock", now - 60, now),
        )
        self.conn.commit()
        return sid

    def test_delete_session_removes_related_rows(self):
        sid = self._insert_session()
        self.conn.execute(
            "INSERT INTO hrv_points (session_id, ts, rr_ms, rmssd) VALUES (?, ?, ?, ?)",
            (sid, time.time(), 800.0, 45.0),
        )
        self.conn.execute(
            """
            INSERT INTO meditation_phrase_log
                (session_id, phrase_file, played_at)
            VALUES (?, ?, ?)
            """,
            (sid, "sit_v_1.mp3", time.time()),
        )
        self.conn.commit()

        self.assertTrue(delete_session(self.conn, sid))
        self.assertIsNone(
            self.conn.execute("SELECT id FROM sessions WHERE id = ?", (sid,)).fetchone()
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM hrv_points WHERE session_id = ?", (sid,)
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM meditation_phrase_log WHERE session_id = ?",
                (sid,),
            ).fetchone()[0],
            0,
        )

    def test_delete_session_missing_returns_false(self):
        self.assertFalse(delete_session(self.conn, 9999))

    def test_wipe_all_history_clears_tables(self):
        sid = self._insert_session()
        self.conn.execute(
            "INSERT INTO hrv_points (session_id, ts, rr_ms, rmssd) VALUES (?, ?, ?, ?)",
            (sid, time.time(), 800.0, 45.0),
        )
        self.conn.execute(
            "INSERT INTO baseline (hour, rmssd_mean, n_samples, updated_at) VALUES (?, ?, ?, ?)",
            (12, 50.0, 10, time.time()),
        )
        self.conn.execute(
            """
            INSERT INTO meditation_phrase_log
                (session_id, phrase_file, played_at)
            VALUES (?, ?, ?)
            """,
            (sid, "sit_v_1.mp3", time.time()),
        )
        self.conn.commit()

        n = wipe_all_history(self.conn)
        self.assertEqual(n, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM hrv_points").fetchone()[0], 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM meditation_phrase_log").fetchone()[0], 0
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM baseline").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
