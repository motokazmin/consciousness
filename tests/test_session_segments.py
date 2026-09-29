"""Разметка отрезков сессии: данные ленты над графиками архива.

Отрезки (стадии сна, фазы практики) с уверенностью и основанием и точечные
события. Пишутся целиком, как разбор; ручки вызываются напрямую.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from hrv_core.db import (
    delete_session,
    init_db,
    load_session_segments,
    save_session_segments,
)
import hrv_web.server as server


def _seg(t0, t1, kind="nrem", confidence="assume"):
    return {"t0": t0, "t1": t1, "kind": kind, "label": "ровный сон",
            "confidence": confidence, "basis": "дыхание ровное"}


class SessionSegmentsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.conn = init_db(self.db_path)
        self._patch = patch.object(server, "init_db", lambda: init_db(self.db_path))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.conn.close()
        self.db_path.unlink(missing_ok=True)

    def _session(self) -> int:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO sessions (tag, source, started, ended, participant) "
            "VALUES (?, ?, ?, ?, ?)",
            ("sleep", "mock", now - 3600, now, "roman"),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def test_roundtrip_keeps_created_at(self):
        sid = self._session()
        first = save_session_segments(self.conn, sid, {"segments": [_seg(0, 600)]})
        save_session_segments(self.conn, sid, {"segments": [_seg(0, 900)], "events": []})
        loaded = load_session_segments(self.conn, sid)
        self.assertEqual(loaded["segments"][0]["t1"], 900)
        self.assertEqual(loaded["events"], [])
        self.assertEqual(loaded["created_at"], first["created_at"])

    def test_missing_is_none(self):
        self.assertIsNone(load_session_segments(self.conn, self._session()))

    def test_put_sorts_and_get_returns(self):
        sid = self._session()
        body = server.PutSegmentsBody(
            segments=[_seg(600, 900, "rem"), _seg(0, 600)],
            events=[{"t": 700, "kind": "turn", "label": "поворот"}],
        )
        server.put_session_segments(sid, body)
        got = server.get_session_segments(sid)["segments"]
        self.assertEqual([s["t0"] for s in got["segments"]], [0, 600])
        self.assertEqual(got["events"][0]["kind"], "turn")

    def test_put_rejects_inverted_segment(self):
        sid = self._session()
        with self.assertRaises(HTTPException) as cm:
            server.put_session_segments(sid, server.PutSegmentsBody(segments=[_seg(900, 600)]))
        self.assertEqual(cm.exception.status_code, 400)

    def test_bad_confidence_rejected(self):
        with self.assertRaises(ValidationError):
            server.PutSegmentsBody(segments=[_seg(0, 600, confidence="sure")])

    def test_unknown_session_404(self):
        with self.assertRaises(HTTPException) as cm:
            server.get_session_segments(9999)
        self.assertEqual(cm.exception.status_code, 404)

    def test_delete_session_removes_segments(self):
        sid = self._session()
        save_session_segments(self.conn, sid, {"segments": [_seg(0, 600)]})
        delete_session(self.conn, sid)
        self.assertIsNone(load_session_segments(self.conn, sid))


if __name__ == "__main__":
    unittest.main()
