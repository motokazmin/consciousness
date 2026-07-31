"""Таймер сессии стартует с первого реального RR."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hrv_core.db import init_db as real_init_db
import hrv_web.session_manager as sm


class SessionArmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        active = sm.MANAGER.get_active()
        if active is not None:
            sm.MANAGER.stop(active.session_id)

    def tearDown(self):
        active = sm.MANAGER.get_active()
        if active is not None:
            sm.MANAGER.stop(active.session_id)
        self.db_path.unlink(missing_ok=True)

    def _wait_armed(self, rs: sm.RunningSession, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if rs.first_beat_at is not None:
                return
            time.sleep(0.05)
        self.fail(f"first_beat_at не появился за {timeout}s")

    def test_arm_on_first_mock_beat(self):
        def _init():
            return real_init_db(self.db_path)

        with patch.object(sm, "init_db", _init):
            rs = sm.MANAGER.start(
                participant="test",
                tag="focus",
                session_name=None,
                source_kind="mock",
                address=None,
                minutes=1.0,
            )
            created_at = rs.started_at

            self._wait_armed(rs)

            self.assertIsNotNone(rs.first_beat_at)
            self.assertGreaterEqual(rs.first_beat_at, created_at)

            with rs.conn_lock:
                row = rs.conn.execute(
                    "SELECT started FROM sessions WHERE id = ?",
                    (rs.session_id,),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertAlmostEqual(row[0], rs.first_beat_at, places=3)

            # «armed» должен быть в очереди (или уже снят клиентом — но при тесте
            # никто не читает, так что сообщение ещё там вместе с beat'ами).
            armed_msgs = []
            drained = []
            while True:
                try:
                    msg = rs.ws_queue.get_nowait()
                except Exception:
                    break
                drained.append(msg)
                if msg.get("type") == "armed":
                    armed_msgs.append(msg)
            self.assertTrue(armed_msgs, f"нет armed в ws_queue: {drained[:5]}")
            self.assertAlmostEqual(
                armed_msgs[0]["started_at"], rs.first_beat_at, places=3
            )
            # armed раньше первого beat
            types = [m.get("type") for m in drained]
            self.assertIn("armed", types)
            if "beat" in types:
                self.assertLess(types.index("armed"), types.index("beat"))

            summary = sm.MANAGER.stop(rs.session_id)
            self.assertIsNotNone(summary)


if __name__ == "__main__":
    unittest.main()
