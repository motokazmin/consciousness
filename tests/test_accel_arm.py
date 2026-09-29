"""Взведение сессии по акселерометру (BLE), а не по первому RR.

Заказчик: t0 должен совпадать с реальным стартом канала дыхания, чтобы обе
кривые (RR и дыхание) покрывали сессию целиком. Но PMD документированно
умеет отказывать молча (SUCCESS без единого кадра, см. ARCHITECTURE.md) — и
если ждать его безусловно, сессия без акселерометра никогда не взведётся и
умрёт по ARM_TIMEOUT_SEC, унеся с собой RR. Отсюда запасной путь:
ACC_ARM_WAIT_SEC после первого RR без единой пачки акселерометра — взводим
по этому RR и пишем без канала дыхания.

Источник здесь — подложный (никакого реального BLE): тесты вызывают колбэки
`_beat`/`_accel`, которые `SessionManager.start()` передаёт в `source.start()`,
напрямую с нужными `ts`, так что таймер ACC_ARM_WAIT_SEC проверяется по
переданным меткам времени, а не по настоящим часам.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hrv_core.db import init_db as real_init_db
import hrv_web.session_manager as sm


class _FakeBleSource:
    """Копит колбэки `SessionManager`, отдаёт их тесту для ручного вождения."""

    def __init__(self, *a, **kw):
        self.callback = None
        self.acc_callback = None

    def start(self, callback, acc_callback=None):
        self.callback = callback
        self.acc_callback = acc_callback

    def stop(self):
        pass


class AccelArmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        active = sm.MANAGER.get_active()
        if active is not None:
            sm.MANAGER.stop(active.session_id)

    def tearDown(self):
        active = sm.MANAGER.get_active()
        if active is not None:
            sm.MANAGER.stop(active.session_id)
        self.db_path.unlink(missing_ok=True)

    def _start_ble(self) -> tuple[sm.RunningSession, _FakeBleSource]:
        def _init():
            return real_init_db(self.db_path)

        fake = _FakeBleSource()
        with patch.object(sm, "init_db", _init), \
             patch.object(sm, "build_source", return_value=fake):
            rs = sm.MANAGER.start(
                participant="test",
                tag="focus",
                session_name=None,
                source_kind="ble",
                address="AA:BB:CC:DD:EE:FF",
                minutes=None,
            )
        return rs, fake

    def _n_points(self, rs: sm.RunningSession) -> int:
        with rs.conn_lock:
            return rs.conn.execute(
                "SELECT COUNT(*) FROM hrv_points WHERE session_id = ?",
                (rs.session_id,),
            ).fetchone()[0]

    def test_arms_on_first_accel_batch_not_first_rr(self):
        rs, fake = self._start_ble()
        self.assertEqual(rs.device_state, "ble_repair")
        t0 = time.time()

        fake.callback(800.0, t0 + 0.1)  # RR до акселерометра — должен быть отброшен
        self.assertIsNone(rs.first_beat_at)
        self.assertEqual(self._n_points(rs), 0)

        fake.acc_callback(t0 + 0.3, [(1, 2, 3)], 25.0)  # первая пачка — взводит
        self.assertEqual(rs.first_beat_at, t0 + 0.3)
        self.assertFalse(rs.accel_missing)
        self.assertEqual(rs.device_state, "recording")
        self.assertEqual(self._n_points(rs), 0)  # RR до взведения так и не записан

        fake.callback(800.0, t0 + 0.35)  # RR после взведения — уже пишется
        self.assertEqual(self._n_points(rs), 1)

    def test_rr_before_arm_not_persisted_across_several_beats(self):
        rs, fake = self._start_ble()
        t0 = time.time()
        for i in range(5):
            fake.callback(800.0, t0 + i * 0.8)
        self.assertEqual(self._n_points(rs), 0)
        self.assertIsNone(rs.first_beat_at)

    def test_acc_timeout_falls_back_to_rr(self):
        rs, fake = self._start_ble()
        t0 = time.time()

        fake.callback(800.0, t0)  # первый RR — отсчёт ACC_ARM_WAIT_SEC
        self.assertEqual(rs.first_rr_at, t0)
        fake.callback(800.0, t0 + sm.ACC_ARM_WAIT_SEC - 1)  # ещё ждём, не взведено
        self.assertIsNone(rs.first_beat_at)
        self.assertEqual(self._n_points(rs), 0)

        arm_ts = t0 + sm.ACC_ARM_WAIT_SEC + 1  # дедлайн прошёл — взводим этим ударом
        fake.callback(800.0, arm_ts)
        self.assertEqual(rs.first_beat_at, arm_ts)
        self.assertTrue(rs.accel_missing)
        self.assertEqual(rs.device_state, "recording")
        self.assertEqual(self._n_points(rs), 1)  # именно этот удар и записан

    def test_accel_after_fallback_arm_is_stored_without_second_arm(self):
        rs, fake = self._start_ble()
        t0 = time.time()
        fake.callback(800.0, t0)
        arm_ts = t0 + sm.ACC_ARM_WAIT_SEC + 1
        fake.callback(800.0, arm_ts)
        self.assertTrue(rs.accel_missing)

        fake.acc_callback(arm_ts + 5, [(1, 2, 3)], 25.0)  # канал всё же откликнулся

        self.assertEqual(rs.first_beat_at, arm_ts)  # повторного взведения не было
        self.assertTrue(rs.accel_missing)  # флаг не переписан задним числом
        with rs.conn_lock:
            n_accel = rs.conn.execute(
                "SELECT COUNT(*) FROM hrv_accel_batches WHERE session_id = ?",
                (rs.session_id,),
            ).fetchone()[0]
        self.assertEqual(n_accel, 1)  # но пачка всё равно сохранена


if __name__ == "__main__":
    unittest.main()
