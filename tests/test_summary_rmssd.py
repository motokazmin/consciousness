"""Сводка сессии: RMSSD по исправленному ряду, а не по «живой» колонке.

Живая колонка hrv_points.rmssd считается по нефильтрованному буферу, и один
сбой датчика давал в ней сотни мс — среднее ночи завышалось почти вдвое.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from hrv_core.db import init_db
from hrv_core.summary import session_summary_dict


class SummaryRmssdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.conn = init_db(Path(self.tmp.name))

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_artifact_does_not_inflate_rmssd(self):
        rng = np.random.default_rng(0)
        rr = 900 + rng.normal(0, 15, 1200)
        rr[600] = 1900  # пропущенный удар — сбой датчика
        ts = 1000.0 + np.cumsum(rr) / 1000.0
        cur = self.conn.execute(
            "INSERT INTO sessions (tag, source, started, ended, participant) VALUES (?,?,?,?,?)",
            ("sleep", "mock", 1000.0, float(ts[-1]), "roman"),
        )
        sid = int(cur.lastrowid)
        for i, (t, r) in enumerate(zip(ts, rr)):
            live = 600.0 if 598 <= i <= 610 else 20.0  # «живая» колонка со всплеском
            self.conn.execute(
                "INSERT INTO hrv_points (session_id, ts, rr_ms, rmssd) VALUES (?,?,?,?)",
                (sid, float(t), float(r), live),
            )
        self.conn.commit()

        out = session_summary_dict(self.conn, sid, baseline_at_start=10.0, drift_count=0)
        # ~ sqrt(2)·15 ≈ 21 мс; сбой не должен задирать ни медиану, ни 90-й перцентиль
        self.assertLess(out["rmssd_median"], 30)
        self.assertLess(out["rmssd_p90"], 40)
        self.assertLess(out["rmssd_mean"], 30)
        self.assertIsNone(out["vs_baseline_pct"])
        self.assertEqual(out["point_count"], 1200)


if __name__ == "__main__":
    unittest.main()
