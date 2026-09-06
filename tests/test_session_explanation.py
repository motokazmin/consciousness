"""Разбор сессии: текст, который Claude пишет по номеру сессии.

Хранится отдельно от `sessions.session_name` намеренно — заметки принадлежат
испытуемому, разбор пишет Claude, и смешивать их в одном поле уже приводило
к потере авторства (сессии 91–93). Проверяется хранилище и контракт ручек;
HTTP-слой, как и в остальных тестах, не поднимается — функции вызываются прямо.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from hrv_core.db import (
    delete_session,
    delete_session_explanation,
    init_db,
    load_session_explanation,
    save_session_explanation,
    wipe_all_history,
)
import hrv_web.server as server


class _FakeRequest:
    """Минимум от fastapi.Request, который читают ручки разбора."""

    def __init__(self, *, body: bytes = b"", content_type: str = "", params: dict | None = None):
        self.headers = {"content-type": content_type} if content_type else {}
        self.query_params = params or {}
        self._body = body

    async def body(self) -> bytes:
        return self._body

    async def json(self):
        import json

        return json.loads(self._body.decode("utf-8"))


class SessionExplanationTests(unittest.TestCase):
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

    def _session(self, *, with_points: bool = False) -> int:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO sessions (tag, source, started, ended, participant) "
            "VALUES (?, ?, ?, ?, ?)",
            ("meditation", "mock", now - 600, now, "roman"),
        )
        sid = int(cur.lastrowid)
        if with_points:
            for i in range(20):
                self.conn.execute(
                    "INSERT INTO hrv_points (session_id, ts, rr_ms, rmssd) VALUES (?, ?, ?, ?)",
                    (sid, now - 600 + i, 900 + i, 40 + i),
                )
        self.conn.commit()
        return sid

    # ── хранилище ──────────────────────────────────────────────────────────

    def test_save_load_roundtrip(self):
        sid = self._session()
        saved = save_session_explanation(self.conn, sid, "Дыхание ровное, выход мягкий.")
        loaded = load_session_explanation(self.conn, sid)
        self.assertEqual(loaded["body"], "Дыхание ровное, выход мягкий.")
        self.assertEqual(loaded["author"], "claude")
        self.assertEqual(saved["created_at"], loaded["created_at"])

    def test_rewrite_keeps_created_at(self):
        sid = self._session()
        first = save_session_explanation(self.conn, sid, "первая версия")
        time.sleep(0.01)
        second = save_session_explanation(self.conn, sid, "вторая версия", author="roman")
        self.assertEqual(first["created_at"], second["created_at"])
        self.assertGreater(second["updated_at"], first["updated_at"])
        loaded = load_session_explanation(self.conn, sid)
        self.assertEqual(loaded["body"], "вторая версия")
        self.assertEqual(loaded["author"], "roman")

    def test_missing_is_none(self):
        self.assertIsNone(load_session_explanation(self.conn, self._session()))

    def test_delete(self):
        sid = self._session()
        save_session_explanation(self.conn, sid, "текст")
        self.assertTrue(delete_session_explanation(self.conn, sid))
        self.assertFalse(delete_session_explanation(self.conn, sid))
        self.assertIsNone(load_session_explanation(self.conn, sid))

    def test_removed_with_session(self):
        sid = self._session()
        save_session_explanation(self.conn, sid, "текст")
        delete_session(self.conn, sid)
        self.assertIsNone(load_session_explanation(self.conn, sid))

    def test_removed_with_history_wipe(self):
        sid = self._session()
        save_session_explanation(self.conn, sid, "текст")
        with patch("hrv_core.db.wipe_session_audio_dir", lambda **kw: None):
            wipe_all_history(self.conn)
        self.assertIsNone(load_session_explanation(self.conn, sid))

    # ── ручки ──────────────────────────────────────────────────────────────

    def test_put_json_then_get(self):
        sid = self._session()
        req = _FakeRequest(
            body='{"body": "## Что было\\n\\nСпокойно.", "author": "claude"}'.encode("utf-8"),
            content_type="application/json",
        )
        res = asyncio.run(server.put_session_explanation(sid, req))
        self.assertTrue(res["ok"])
        got = server.get_session_explanation(sid)
        self.assertEqual(got["explanation"]["body"], "## Что было\n\nСпокойно.")

    def test_put_raw_markdown(self):
        sid = self._session()
        req = _FakeRequest(
            body="# Разбор\n\nДыхание держалось ровно.".encode("utf-8"),
            content_type="text/markdown",
            params={"author": "claude"},
        )
        asyncio.run(server.put_session_explanation(sid, req))
        got = server.get_session_explanation(sid)
        self.assertEqual(got["explanation"]["body"], "# Разбор\n\nДыхание держалось ровно.")
        self.assertEqual(got["explanation"]["author"], "claude")

    def test_put_empty_rejected(self):
        sid = self._session()
        req = _FakeRequest(body=b"   \n ", content_type="text/markdown")
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(server.put_session_explanation(sid, req))
        self.assertEqual(cm.exception.status_code, 400)

    def test_unknown_session_404(self):
        with self.assertRaises(HTTPException) as cm:
            server.get_session_explanation(999_999)
        self.assertEqual(cm.exception.status_code, 404)
        req = _FakeRequest(body="текст".encode("utf-8"), content_type="text/markdown")
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(server.put_session_explanation(999_999, req))
        self.assertEqual(cm.exception.status_code, 404)

    def test_delete_endpoint(self):
        sid = self._session()
        save_session_explanation(self.conn, sid, "текст")
        self.assertTrue(server.delete_session_explanation_endpoint(sid)["deleted"])
        self.assertIsNone(server.get_session_explanation(sid)["explanation"])

    def test_summary_carries_explanation(self):
        sid = self._session(with_points=True)
        save_session_explanation(self.conn, sid, "Ровный выход в конце.")
        summary = server.get_session(sid)
        self.assertEqual(summary["explanation"]["body"], "Ровный выход в конце.")

    def test_list_flags_explanation(self):
        with_expl = self._session(with_points=True)
        without = self._session(with_points=True)
        save_session_explanation(self.conn, with_expl, "текст")
        by_id = {s["id"]: s for s in server.list_sessions(note_tag=[])["sessions"]}
        self.assertTrue(by_id[with_expl]["has_explanation"])
        self.assertFalse(by_id[without]["has_explanation"])


if __name__ == "__main__":
    unittest.main()
