"""Буфер отсчётов не должен переживать смену частоты при переговорах.

Если кандидат-частота отклонена, всё накопленное на ней в PmdAccStream._buffer
обязано быть выброшено, а не попасть в первую пачку уже согласованной частоты
— иначе load_accel_samples разложит эти отсчёты по времени с чужим шагом.
"""

import asyncio
import struct
import unittest

from hrv_core.pmd import ACC_TYPE, PMD_CONTROL, PMD_DATA, PmdAccStream


def _no_delta_frame(samples: list[tuple[int, int, int]]) -> bytes:
    header = bytes([ACC_TYPE]) + (0).to_bytes(8, "little") + bytes([0x00])
    body = b"".join(struct.pack("<hhh", *s) for s in samples)
    return header + body


class _FakeClient:
    """Единственный клиент: control/data подписаны на одном соединении, как H10."""

    def __init__(self):
        self._control_cb = None
        self._data_cb = None

    async def start_notify(self, uuid, cb):
        if uuid == PMD_CONTROL:
            self._control_cb = cb
        elif uuid == PMD_DATA:
            self._data_cb = cb

    async def stop_notify(self, uuid):
        pass

    async def read_gatt_char(self, uuid):
        return bytes([0x00, 0x05])  # ЭКГ + акселерометр — как на живом ремне

    async def write_gatt_char(self, uuid, data, response=True):
        if not (len(data) >= 6 and data[0] == 0x02 and data[1] == ACC_TYPE):
            return  # ACC_STOP и прочее — не интересует этот тест
        hz = data[4] | (data[5] << 8)
        if hz == 25:
            # Кадр пришёл, пока попытка ещё не отвечена (или уже мусор для этой
            # частоты) — и после этого частота явно отклонена устройством.
            self._data_cb(None, bytearray(_no_delta_frame([(999, 999, 999)])))
            self._control_cb(None, bytearray([0xF0, 0x02, ACC_TYPE, 8]))  # отказ
        elif hz == 50:
            self._data_cb(None, bytearray(_no_delta_frame([(1, 2, 3)])))
            self._control_cb(None, bytearray([0xF0, 0x02, ACC_TYPE, 0]))  # принято


class BufferResetBetweenAttemptsTests(unittest.TestCase):
    def test_rejected_attempt_samples_do_not_leak_into_accepted_batch(self):
        async def run():
            client = _FakeClient()
            batches = []
            stream = PmdAccStream(
                client,
                lambda ts, samples, hz: batches.append((ts, samples, hz)),
                candidate_hz=(25, 50),
            )
            hz = await stream.start()
            await stream.stop()  # флашит то, что осталось в буфере
            return stream, hz, batches

        stream, hz, batches = asyncio.run(run())

        self.assertEqual(hz, 50.0)
        self.assertEqual(len(batches), 1)
        _, samples, batch_hz = batches[0]
        # Только отсчёт, снятый на согласованной частоте — мусор с отклонённой
        # (999, 999, 999) не должен просочиться.
        self.assertEqual(samples, [(1, 2, 3)])
        self.assertEqual(batch_hz, 50.0)

        # Диагностические счётчики — за всё время жизни потока, включая
        # отклонённую попытку: pmd_check должен видеть, что кадры туда шли.
        self.assertEqual(stream.total_frames, 2)
        self.assertEqual(stream.total_samples, 2)


if __name__ == "__main__":
    unittest.main()
