"""Сводка сессии: RMSSD по исправленному ряду, а не по «живой» колонке.

Живая колонка hrv_points.rmssd считается по нефильтрованному буферу, и один
сбой датчика давал в ней сотни мс — среднее ночи завышалось почти вдвое.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from hrv_core.db import (
    BASELINE_VERSION,
    ensure_baseline_current,
    init_db,
    load_hour_baseline,
    rebuild_baseline,
)
from hrv_core.summary import session_summary_dict


class SummaryRmssdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.conn = init_db(Path(self.tmp.name))

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def _session_with_artifact(self, start: float = 1000.0) -> int:
        rng = np.random.default_rng(0)
        rr = 900 + rng.normal(0, 15, 1200)
        rr[600] = 1900  # пропущенный удар — сбой датчика
        ts = start + np.cumsum(rr) / 1000.0
        cur = self.conn.execute(
            "INSERT INTO sessions (tag, source, started, ended, participant) VALUES (?,?,?,?,?)",
            ("sleep", "mock", start, float(ts[-1]), "roman"),
        )
        sid = int(cur.lastrowid)
        for i, (t, r) in enumerate(zip(ts, rr)):
            live = 600.0 if 598 <= i <= 610 else 20.0  # «живая» колонка со всплеском
            self.conn.execute(
                "INSERT INTO hrv_points (session_id, ts, rr_ms, rmssd) VALUES (?,?,?,?)",
                (sid, float(t), float(r), live),
            )
        self.conn.commit()
        return sid

    def test_baseline_rebuilt_from_corrected_series(self):
        import datetime

        self._session_with_artifact()
        # Старая таблица: собрана по живой колонке, со всплеском
        self.conn.execute(
            "INSERT INTO baseline (hour, rmssd_mean, n_samples, updated_at) VALUES (?,?,?,?)",
            (datetime.datetime.fromtimestamp(1000.0).hour, 79.0, 4000, 0.0),
        )
        self.conn.commit()
        self.assertTrue(ensure_baseline_current(self.conn))
        hour = datetime.datetime.fromtimestamp(1000.0).hour
        self.assertLess(load_hour_baseline(self.conn, hour), 30)
        # Повторный старт — версия уже текущая, пересборки нет
        self.assertFalse(ensure_baseline_current(self.conn))
        v = self.conn.execute("SELECT value FROM meta WHERE key='baseline_version'").fetchone()[0]
        self.assertEqual(v, str(BASELINE_VERSION))

    def test_rebuild_is_idempotent(self):
        import datetime

        self._session_with_artifact()
        hour = datetime.datetime.fromtimestamp(1000.0).hour
        rebuild_baseline(self.conn)
        first = load_hour_baseline(self.conn, hour)
        rebuild_baseline(self.conn)
        self.assertAlmostEqual(load_hour_baseline(self.conn, hour), first)

    def test_artifact_does_not_inflate_rmssd(self):
        sid = self._session_with_artifact()

        out = session_summary_dict(self.conn, sid, baseline_at_start=10.0, drift_count=0)
        # ~ sqrt(2)·15 ≈ 21 мс; сбой не должен задирать ни медиану, ни 90-й перцентиль
        self.assertLess(out["rmssd_median"], 30)
        self.assertLess(out["rmssd_p90"], 40)
        self.assertLess(out["rmssd_mean"], 30)
        # vs baseline — от среднего по исправленному ряду, а не от живой колонки (~600)
        expected = (out["rmssd_mean"] - 10.0) / 10.0 * 100.0
        self.assertAlmostEqual(out["vs_baseline_pct"], expected, delta=2.0)
        self.assertEqual(out["point_count"], 1200)


if __name__ == "__main__":
    unittest.main()
