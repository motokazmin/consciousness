"""Пересопряжение ремня — в момент старта записи, а не разово при запуске UI.

`PolarH10Source._maybe_repair_bond` решает, вызывать ли `ble_repair.repair()`,
по одному признаку — задан ли `acc_callback` (акселерометр обязателен для
каждой BLE-записи, SessionManager передаёт колбэк всегда; условие остаётся
защитой для вызовов в обход SessionManager). RR неприкосновенен: любая ошибка
пересопряжения (не нашёлся датчик, bluetoothctl недоступен, таймаут,
исключение) логируется и проглатывается, `_loop` идёт дальше как обычно.

Заодно проверяется `on_state` — хук, которым источник сообщает фронту фазу
подключения канала («ble_repair» → «waiting_accel»).
"""

import asyncio
import threading
import unittest
from unittest.mock import patch

from hrv_core.sources import PolarH10Source


class MaybeRepairBondTests(unittest.TestCase):
    def test_acc_disabled_skips_repair(self):
        """Опция акселерометра выключена (acc_callback не задан) — repair() не зовётся."""
        src = PolarH10Source("AA:BB:CC:DD:EE:FF", session_stop=threading.Event())
        # opt_acc_recording=False → session_manager передаёт acc_callback=None
        self.assertIsNone(src._acc_callback)

        with patch("hrv_core.ble_repair.repair") as mock_repair:
            asyncio.run(src._maybe_repair_bond())

        mock_repair.assert_not_called()

    def test_acc_enabled_calls_repair_once_with_address(self):
        """Опция включена — вызов сделан ровно один раз, с адресом этой записи."""
        src = PolarH10Source("AA:BB:CC:DD:EE:FF", session_stop=threading.Event())
        src._acc_callback = lambda *a: None

        with patch("hrv_core.ble_repair.repair", return_value=True) as mock_repair:
            asyncio.run(src._maybe_repair_bond())

        mock_repair.assert_called_once_with("AA:BB:CC:DD:EE:FF")

    def test_repair_failure_does_not_raise(self):
        """repair() вернул False (датчик не найден / сопряжение не удалось) — не исключение."""
        src = PolarH10Source("AA:BB:CC:DD:EE:FF", session_stop=threading.Event())
        src._acc_callback = lambda *a: None

        with patch("hrv_core.ble_repair.repair", return_value=False):
            asyncio.run(src._maybe_repair_bond())  # не должно бросить

    def test_repair_exception_is_swallowed_rr_unaffected(self):
        """bluetoothctl недоступен / таймаут — исключение из repair() не долетает наружу."""
        src = PolarH10Source("AA:BB:CC:DD:EE:FF", session_stop=threading.Event())
        src._acc_callback = lambda *a: None

        with patch("hrv_core.ble_repair.repair", side_effect=RuntimeError("bluetoothctl недоступен")):
            asyncio.run(src._maybe_repair_bond())  # не должно бросить

    def test_repair_reports_ble_repair_then_waiting_accel_state(self):
        """on_state получает фазы в порядке «идёт пересопряжение» → «ждём поток»."""
        states: list[str] = []
        src = PolarH10Source(
            "AA:BB:CC:DD:EE:FF", session_stop=threading.Event(), on_state=states.append
        )
        src._acc_callback = lambda *a: None

        with patch("hrv_core.ble_repair.repair", return_value=True):
            asyncio.run(src._maybe_repair_bond())

        self.assertEqual(states, ["ble_repair", "waiting_accel"])

    def test_repair_failure_still_reports_waiting_accel(self):
        """Даже если пересопряжение не удалось, состояние переходит дальше —
        PMD сам откажет по месту, фронт не должен зависнуть на «сопряжении»."""
        states: list[str] = []
        src = PolarH10Source(
            "AA:BB:CC:DD:EE:FF", session_stop=threading.Event(), on_state=states.append
        )
        src._acc_callback = lambda *a: None

        with patch("hrv_core.ble_repair.repair", return_value=False):
            asyncio.run(src._maybe_repair_bond())

        self.assertEqual(states, ["ble_repair", "waiting_accel"])

    def test_no_acc_callback_reports_no_state(self):
        """Акселерометр не запрошен — репрайр пропущен, и состояние тоже не шлём."""
        states: list[str] = []
        src = PolarH10Source(
            "AA:BB:CC:DD:EE:FF", session_stop=threading.Event(), on_state=states.append
        )

        with patch("hrv_core.ble_repair.repair") as mock_repair:
            asyncio.run(src._maybe_repair_bond())

        mock_repair.assert_not_called()
        self.assertEqual(states, [])


if __name__ == "__main__":
    unittest.main()
