"""opt_acc_recording: сессии mock/BLE-без-акселерометра работают ровно как раньше."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hrv_core.constants import DEFAULT_OPT_ACC_RECORDING
from hrv_core.db import init_db as real_init_db
import hrv_web.session_manager as sm


class AccRecordingFlagTests(unittest.TestCase):
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

    def test_default_is_off(self):
        self.assertFalse(DEFAULT_OPT_ACC_RECORDING)

    def test_mock_session_with_acc_flag_on_still_records_rr(self):
        """Mock не умеет PMD: флаг включён, но callback просто не вызывается — RR как обычно."""

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
                opt_acc_recording=True,
            )
            self._wait_armed(rs)
            time.sleep(0.3)

            with rs.conn_lock:
                opt_acc = rs.conn.execute(
                    "SELECT opt_acc_recording FROM sessions WHERE id = ?",
                    (rs.session_id,),
                ).fetchone()[0]
                n_points = rs.conn.execute(
                    "SELECT COUNT(*) FROM hrv_points WHERE session_id = ?",
                    (rs.session_id,),
                ).fetchone()[0]
                n_accel = rs.conn.execute(
                    "SELECT COUNT(*) FROM hrv_accel_batches WHERE session_id = ?",
                    (rs.session_id,),
                ).fetchone()[0]
            self.assertEqual(opt_acc, 1)
            self.assertGreater(n_points, 0)
            self.assertEqual(n_accel, 0)  # mock не производит акселерометр

            summary = sm.MANAGER.stop(rs.session_id)
            self.assertIsNotNone(summary)

    def test_mock_session_default_flag_off_unchanged(self):
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
            self._wait_armed(rs)
            with rs.conn_lock:
                opt_acc = rs.conn.execute(
                    "SELECT opt_acc_recording FROM sessions WHERE id = ?",
                    (rs.session_id,),
                ).fetchone()[0]
            self.assertEqual(opt_acc, 0)
            summary = sm.MANAGER.stop(rs.session_id)
            self.assertIsNotNone(summary)


if __name__ == "__main__":
    unittest.main()
