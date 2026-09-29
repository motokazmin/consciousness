"""`--dump` у pmd_check: сырой CSV со всеми принятыми отсчётами.

Живой BLE не участвует — проверяется только запись CSV (`_write_dump`),
остальное в `check()` не запускается (см. модульный docstring pmd_check.py:
живой BLE в тестах не участвует)."""

import csv
import tempfile
import unittest
from pathlib import Path

from hrv_core.pmd_check import _write_dump


class WriteDumpTests(unittest.TestCase):
    def test_creates_file_with_one_row_per_sample(self):
        rows = [
            (1, 0, 1_000_000_000, 100.0, 10, 20, 30),
            (1, 1, 1_000_000_000, 100.0, 11, 21, 31),
            (2, 0, None, 100.5, 12, 22, 32),  # метка не разобралась — пусто в CSV
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dump.csv"
            _write_dump(
                path, rows,
                requested_hz=25.0, measured_hz=24.8,
                total_frames=2, total_samples=3,
                measured_window=1.5, first_frame_wall_time=1234567890.123,
            )
            self.assertTrue(path.exists())

            with path.open(newline="") as f:
                lines = f.readlines()
            self.assertTrue(lines[0].startswith("#"))
            self.assertIn("measured_hz=24.80", lines[0])
            self.assertIn("first_frame_wall_time=1234567890.123", lines[0])

            with path.open(newline="") as f:
                next(f)  # пропустить комментарий — csv.reader его не поймёт как заголовок
                reader = csv.reader(f)
                data_rows = list(reader)
            header, *body = data_rows
            self.assertEqual(
                header,
                ["frame_idx", "sample_idx", "device_ts_ns", "host_monotonic", "x", "y", "z"],
            )
            self.assertEqual(len(body), len(rows))
            self.assertEqual(body[2][2], "")  # device_ts_ns=None → пустое поле

    def test_measured_hz_none_is_marked_as_not_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dump.csv"
            _write_dump(
                path, [],
                requested_hz=25.0, measured_hz=None,
                total_frames=1, total_samples=1,
                measured_window=None, first_frame_wall_time=None,
            )
            first_line = path.open().readline()
            self.assertIn("measured_hz=н/д", first_line)
            self.assertIn("first_frame_wall_time=н/д", first_line)


if __name__ == "__main__":
    unittest.main()
