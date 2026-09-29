"""GET /api/sessions/{id}/breathing — вызывается напрямую как функция (см.
остальные тесты `hrv_web/server.py`-эндпойнтов в этом репо: HTTP-слой не
тестируется, только его логика, FastAPI decorator не мешает прямому вызову).

Проверяется контракт, а не сам расчёт дыхания (он — в test_breathing.py):
явное различение «акселерометра нет» / «мало данных» / «есть отчёт», ось
времени та же, что у /analysis (секунды от sessions.started)."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi import HTTPException

from hrv_core.db import init_db, insert_accel_batch
import hrv_web.server as server


def _breath_samples(duration_sec: float, fs: float, breath_hz: float):
    n = int(duration_sec * fs)
    t = np.arange(n) / fs
    x = 1000.0 + 15.0 * np.sin(2 * np.pi * breath_hz * t)
    y = np.full(n, 500.0)
    z = np.full(n, 200.0)
    return list(zip(x.round().astype(int).tolist(), y.round().astype(int).tolist(), z.round().astype(int).tolist()))


class BreathingEndpointTests(unittest.TestCase):
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

    def _insert_session(self, *, ended: bool = True) -> int:
        started = time.time() - 200
        cur = self.conn.execute(
            "INSERT INTO sessions (tag, source, started, ended) VALUES (?, ?, ?, ?)",
            ("focus", "ble", started, started + 180 if ended else None),
        )
        self.conn.commit()
        return int(cur.lastrowid), started

    def test_404_for_unknown_session(self):
        with self.assertRaises(HTTPException) as ctx:
            server.session_breathing_endpoint(999_999)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_400_before_session_stopped(self):
        sid, _ = self._insert_session(ended=False)
        with self.assertRaises(HTTPException) as ctx:
            server.session_breathing_endpoint(sid)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_no_accel_rows_reported_explicitly_not_as_empty_arrays(self):
        """Сессия без единой строки в hrv_accel_batches (все старые записи) —
        has_accel=False, а не пустые массивы, которые фронт нарисует как ноль."""
        sid, _ = self._insert_session()
        j = server.session_breathing_endpoint(sid)
        self.assertFalse(j["has_accel"])
        self.assertTrue(j["insufficient_data"])
        self.assertNotIn("t", j)

    def test_too_little_accel_data_reported_explicitly(self):
        sid, started = self._insert_session()
        insert_accel_batch(self.conn, sid, started, [(1, 2, 3)] * 5, 25.0)
        j = server.session_breathing_endpoint(sid)
        self.assertTrue(j["has_accel"])
        self.assertTrue(j["insufficient_data"])

    def test_full_report_time_axis_matches_analysis_convention(self):
        """Ось времени — секунды от sessions.started, как у /analysis."""
        sid, started = self._insert_session()
        fs = 25.5
        samples = _breath_samples(180.0, fs, 18.0 / 60.0)
        i = 0
        step = int(fs)
        while i < len(samples):
            insert_accel_batch(self.conn, sid, started + i / fs, samples[i:i + step], fs)
            i += step

        j = server.session_breathing_endpoint(sid)
        self.assertTrue(j["has_accel"])
        self.assertFalse(j["insufficient_data"])
        self.assertAlmostEqual(j["t"][0], 0.0, delta=0.5)
        self.assertAlmostEqual(j["t"][-1], 180.0, delta=1.0)
        self.assertEqual(len(j["t"]), len(j["wave_mg"]))
        self.assertEqual(len(j["t"]), len(j["rate_cpm"]))
        self.assertAlmostEqual(j["summary"]["cpm_median"], 18.0, delta=1.0)
        self.assertGreater(len(j["windows"]), 0)

    def test_max_points_and_rate_cap_are_respected(self):
        sid, started = self._insert_session()
        fs = 25.5
        samples = _breath_samples(180.0, fs, 18.0 / 60.0)
        i = 0
        step = int(fs)
        while i < len(samples):
            insert_accel_batch(self.conn, sid, started + i / fs, samples[i:i + step], fs)
            i += step

        # Эндпойнт зажимает нижнюю границу (как /analysis) — 50 превращается в
        # минимум 100, но не в тысячи точек 10 Гц-сетки за 180с (~1800).
        j = server.session_breathing_endpoint(sid, max_points=50)
        self.assertLessEqual(len(j["t"]), 100)
        self.assertLess(len(j["t"]), 1800)


if __name__ == "__main__":
    unittest.main()
