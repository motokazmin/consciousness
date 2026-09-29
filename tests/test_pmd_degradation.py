"""RR неприкосновенен: любая ошибка PMD логируется и проглатывается."""

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hrv_core.db import init_db
from hrv_core.pipeline import HRVSessionState
from hrv_core.pmd import PmdError, PmdPairingRequiredError
from hrv_core.sources import PolarH10Source
from hrv_web.session_manager import RunningSession


class _FakeClient:
    """Заглушка BleakClient — в эти тесты не должна вызываться вовсе."""


class StartPmdAccelDegradationTests(unittest.TestCase):
    """PolarH10Source._start_pmd_accel не должен ронять RR-цикл ни при какой ошибке PMD."""

    def test_no_callback_means_no_attempt(self):
        src = PolarH10Source("AA:BB:CC:DD:EE:FF", session_stop=threading.Event())
        result = asyncio.run(src._start_pmd_accel(_FakeClient()))
        self.assertIsNone(result)

    def test_pairing_required_error_is_swallowed(self):
        src = PolarH10Source("AA:BB:CC:DD:EE:FF", session_stop=threading.Event())
        src._acc_callback = lambda *a: None

        class BoomStream:
            def __init__(self, *a, **kw):
                pass

            async def start(self):
                raise PmdPairingRequiredError("нужен bluetoothctl pair")

        with patch("hrv_core.pmd.PmdAccStream", BoomStream):
            result = asyncio.run(src._start_pmd_accel(_FakeClient()))
        self.assertIsNone(result)

    def test_generic_pmd_error_is_swallowed(self):
        src = PolarH10Source("AA:BB:CC:DD:EE:FF", session_stop=threading.Event())
        src._acc_callback = lambda *a: None

        class BoomStream:
            def __init__(self, *a, **kw):
                pass

            async def start(self):
                raise PmdError("акселерометр не заявлен в маске PMD: 0001")

        with patch("hrv_core.pmd.PmdAccStream", BoomStream):
            result = asyncio.run(src._start_pmd_accel(_FakeClient()))
        self.assertIsNone(result)

    def test_unexpected_exception_is_also_swallowed(self):
        """Даже неучтённая ошибка (не PmdError) не должна долетать до RR-цикла."""
        src = PolarH10Source("AA:BB:CC:DD:EE:FF", session_stop=threading.Event())
        src._acc_callback = lambda *a: None

        class BoomStream:
            def __init__(self, *a, **kw):
                pass

            async def start(self):
                raise RuntimeError("что-то совсем не то")

        with patch("hrv_core.pmd.PmdAccStream", BoomStream):
            result = asyncio.run(src._start_pmd_accel(_FakeClient()))
        self.assertIsNone(result)

    def test_stop_pmd_accel_none_is_noop(self):
        asyncio.run(PolarH10Source._stop_pmd_accel(None))

    def test_stop_pmd_accel_swallows_error(self):
        class BoomStream:
            async def stop(self):
                raise RuntimeError("BLE уже отвалился")

        asyncio.run(PolarH10Source._stop_pmd_accel(BoomStream()))


class AccelBatchStorageDegradationTests(unittest.TestCase):
    """Ошибка записи пачки в БД не должна прерывать запись RR."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.conn = init_db(self.db_path)
        cur = self.conn.execute(
            "INSERT INTO sessions (tag, source, started) VALUES (?, ?, ?)",
            ("focus", "ble", time.time()),
        )
        self.conn.commit()
        self.session_id = int(cur.lastrowid)
        self.rs = RunningSession(
            session_id=self.session_id,
            conn=self.conn,
            conn_lock=threading.Lock(),
            stop_event=threading.Event(),
            state=HRVSessionState(None, desktop_notify=False),
            source=None,
            baseline_at_start=None,
            started_at=time.time(),
            duration_minutes=None,
        )

    def tearDown(self):
        self.conn.close()
        self.db_path.unlink(missing_ok=True)

    def test_on_accel_batch_error_does_not_raise(self):
        with patch(
            "hrv_web.session_manager.insert_accel_batch",
            side_effect=RuntimeError("диск отвалился"),
        ):
            # не должно бросать исключение наружу
            self.rs.on_accel_batch(time.time(), [(1, 2, 3)], 25.0)

    def test_rr_recording_continues_after_accel_failure(self):
        with patch(
            "hrv_web.session_manager.insert_accel_batch",
            side_effect=RuntimeError("PMD сломался"),
        ):
            self.rs.on_accel_batch(time.time(), [(1, 2, 3)], 25.0)

        # RR как ни в чём не бывало
        self.rs.on_beat(800.0, time.time())
        rows = self.conn.execute(
            "SELECT COUNT(*) FROM hrv_points WHERE session_id = ?", (self.session_id,)
        ).fetchone()[0]
        self.assertEqual(rows, 1)

    def test_on_accel_batch_after_stop_is_noop(self):
        self.rs.stop_event.set()
        with patch("hrv_web.session_manager.insert_accel_batch") as mocked:
            self.rs.on_accel_batch(time.time(), [(1, 2, 3)], 25.0)
        mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
