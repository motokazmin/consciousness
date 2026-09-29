"""measured_hz потока PMD-акселерометра — без живого BLE.

Раньше частота потока (`hrv_core/pmd_check.py`) считалась как
`total_samples / elapsed`, где `elapsed` шёл от начала переговоров с control
point — то есть включал 10-16с без единого кадра данных, и частота
занижалась вдвое. `measured_hz` считает только по кадрам, реально принёсшим
отсчёты (`PmdAccStream._on_data`), поэтому проверяется здесь напрямую, без
подключения к устройству."""

import struct
import unittest
from unittest.mock import patch

from hrv_core.pmd import ACC_TYPE, PmdAccStream

SAMPLES_PER_FRAME = 36  # 226 байт = 1(тип) + 8(метка) + 1(frame type) + 36*6(тело)


def _frame(ts_ns: int, n: int = SAMPLES_PER_FRAME) -> bytes:
    """Синтетический кадр акселерометра с заданной меткой времени устройства."""
    payload = bytearray([ACC_TYPE])
    payload += ts_ns.to_bytes(8, "little")
    payload += bytes([0x01])  # frame type — тело в обоих наблюдавшихся типах одинаковое
    for i in range(n):
        payload += struct.pack("<hhh", i, i, i)
    return bytes(payload)


def _stream() -> PmdAccStream:
    return PmdAccStream(client=None, on_batch=lambda *_a: None)


class MeasuredHzTests(unittest.TestCase):
    def test_none_with_single_frame(self):
        """Отсчёты самого первого кадра в числитель не идут — одного кадра мало
        даже для оценки интервала."""
        stream = _stream()
        with patch("hrv_core.pmd.time.monotonic", return_value=100.0):
            stream._on_data(None, _frame(ts_ns=0))
        self.assertIsNone(stream.measured_hz)

    def test_uses_device_clock_when_consistent_with_host(self):
        """Три кадра по 36 отсчётов, метка устройства и хостовые часы согласованы
        (интервал 1с на кадр) — частота считается по отсчётам ПОСЛЕ первого
        кадра (72 отсчёта за 2с = 36 Гц), не по всем 108."""
        stream = _stream()
        host_times = iter([100.0, 101.0, 102.0])
        with patch("hrv_core.pmd.time.monotonic", side_effect=lambda: next(host_times)):
            stream._on_data(None, _frame(ts_ns=0))
            stream._on_data(None, _frame(ts_ns=1_000_000_000))
            stream._on_data(None, _frame(ts_ns=2_000_000_000))
        self.assertIsNotNone(stream.measured_hz)
        self.assertAlmostEqual(stream.measured_hz, 36.0, places=6)
        self.assertAlmostEqual(stream.measured_window_s, 2.0, places=6)

    def test_falls_back_to_host_clock_when_device_timestamps_implausible(self):
        """Метки устройства идут назад (мусор/переполнение) — интервал по ним
        не положителен, берём хостовые часы и не падаем с исключением."""
        stream = _stream()
        host_times = iter([100.0, 101.0, 102.0])
        with patch("hrv_core.pmd.time.monotonic", side_effect=lambda: next(host_times)):
            stream._on_data(None, _frame(ts_ns=1_000_000_000))
            stream._on_data(None, _frame(ts_ns=500_000_000))
            stream._on_data(None, _frame(ts_ns=100_000_000))
        # host: 72 отсчёта (2 кадра после первого) за 2с хостового времени = 36 Гц
        self.assertAlmostEqual(stream.measured_hz, 36.0, places=6)
        self.assertAlmostEqual(stream.measured_window_s, 2.0, places=6)

    def test_falls_back_to_host_clock_when_device_rate_diverges_too_much(self):
        """Метка устройства даёт частоту, расходящуюся с хостовой больше чем в
        1.5 раза (не назад, просто неправдоподобно) — не доверяем ей."""
        stream = _stream()
        host_times = iter([100.0, 101.0, 102.0])
        with patch("hrv_core.pmd.time.monotonic", side_effect=lambda: next(host_times)):
            stream._on_data(None, _frame(ts_ns=0))
            # По метке устройства прошло всего 0.1с на два кадра (72/0.1=720 Гц)
            # против 36 Гц по хостовым часам — расхождение больше чем в 1.5 раза.
            stream._on_data(None, _frame(ts_ns=50_000_000))
            stream._on_data(None, _frame(ts_ns=100_000_000))
        self.assertAlmostEqual(stream.measured_hz, 36.0, places=6)


if __name__ == "__main__":
    unittest.main()
