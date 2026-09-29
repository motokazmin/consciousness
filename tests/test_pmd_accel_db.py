"""Пачки отсчётов акселерометра: упаковка/распаковка через БД (roundtrip)."""

import tempfile
import time
import unittest
from pathlib import Path

from hrv_core.db import (
    init_db,
    insert_accel_batch,
    load_accel_samples,
    pack_accel_samples,
    unpack_accel_samples,
)


class AccelPackingTests(unittest.TestCase):
    def test_pack_unpack_roundtrip(self):
        samples = [(100, -200, 900), (0, 0, 0), (-32768, 32767, 1)]
        blob = pack_accel_samples(samples)
        self.assertEqual(unpack_accel_samples(blob), samples)

    def test_pack_unpack_empty(self):
        self.assertEqual(unpack_accel_samples(pack_accel_samples([])), [])


class AccelBatchDbTests(unittest.TestCase):
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

    def tearDown(self):
        self.conn.close()
        self.db_path.unlink(missing_ok=True)

    def test_roundtrip_single_batch(self):
        samples = [(100, -200, 900), (105, -195, 905), (110, -190, 910)]
        batch_ts = 1000.0
        hz = 25.0
        insert_accel_batch(self.conn, self.session_id, batch_ts, samples, hz)

        out = load_accel_samples(self.conn, self.session_id)
        self.assertEqual(len(out), len(samples))
        for i, (ts, x, y, z) in enumerate(out):
            self.assertAlmostEqual(ts, batch_ts + i / hz, places=6)
            self.assertEqual((x, y, z), samples[i])

    def test_batches_ordered_by_ts_regardless_of_insert_order(self):
        insert_accel_batch(self.conn, self.session_id, 20.0, [(1, 2, 3)], 25.0)
        insert_accel_batch(self.conn, self.session_id, 10.0, [(4, 5, 6)], 25.0)
        out = load_accel_samples(self.conn, self.session_id)
        self.assertEqual([o[1:] for o in out], [(4, 5, 6), (1, 2, 3)])

    def test_empty_batch_is_not_inserted(self):
        insert_accel_batch(self.conn, self.session_id, 5.0, [], 25.0)
        self.assertEqual(load_accel_samples(self.conn, self.session_id), [])

    def test_row_stores_one_row_per_batch_not_per_sample(self):
        samples = [(1, 1, 1)] * 25
        insert_accel_batch(self.conn, self.session_id, 0.0, samples, 25.0)
        n_rows = self.conn.execute(
            "SELECT COUNT(*) FROM hrv_accel_batches WHERE session_id = ?",
            (self.session_id,),
        ).fetchone()[0]
        self.assertEqual(n_rows, 1)
        self.assertEqual(len(load_accel_samples(self.conn, self.session_id)), 25)

    def test_delete_session_removes_accel_batches(self):
        from hrv_core.db import delete_session

        insert_accel_batch(self.conn, self.session_id, 0.0, [(1, 2, 3)], 25.0)
        delete_session(self.conn, self.session_id)
        self.assertEqual(load_accel_samples(self.conn, self.session_id), [])


if __name__ == "__main__":
    unittest.main()
