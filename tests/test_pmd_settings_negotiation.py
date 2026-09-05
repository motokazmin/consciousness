"""PmdAccStream.start(): запрос настроек и сборка команды старта из них.

Три сценария: устройство перечисляет настройки (в т.ч. незнакомый тип —
команда обязана нести все объявленные, не только первые три), перечисляет их
несколькими кадрами подряд, и вовсе молчит на запрос (откат на жёстко зашитый
набор). Заголовок ответа — 5 байт (F0, op, type, error, more); счётчик
значений в блоке настройки — 1 байт. Гипотеза «устройству не хватает
настройки channels» опровергнута на живом H10 (см. hrv_core/pmd.py) — эти
тесты её не проверяют и не предполагают.
"""

import asyncio
import struct
import unittest

from hrv_core.pmd import ACC_TYPE, PMD_CONTROL, PMD_DATA, PmdAccStream


def _no_delta_frame(samples: list[tuple[int, int, int]]) -> bytes:
    header = bytes([ACC_TYPE]) + (0).to_bytes(8, "little") + bytes([0x00])
    body = b"".join(struct.pack("<hhh", *s) for s in samples)
    return header + body


def _settings_block(setting_type: int, values: list[int]) -> bytes:
    body = bytes([setting_type, len(values)])
    for v in values:
        body += v.to_bytes(2, "little")
    return body


class _FakeClient:
    """Control/data на одном соединении, как H10. write_gatt_char синхронно
    бьёт в подписанные колбэки — как если бы ответ пришёл мгновенно."""

    def __init__(self, settings_frames: list[bytes] | None, accept_command=None):
        self._control_cb = None
        self._data_cb = None
        self._settings_frames = settings_frames  # None → устройство молчит
        # accept_command(command: bytes) -> bool — какая именно команда старта
        # будет принята этим фейком.
        self._accept_command = accept_command or (lambda _cmd: True)

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
        data = bytes(data)
        if data[:2] == bytes([0x01, ACC_TYPE]):
            # Запрос настроек акселерометра.
            if self._settings_frames is not None:
                for frame in self._settings_frames:
                    self._control_cb(None, bytearray(frame))
            return
        if not (len(data) >= 6 and data[0] == 0x02 and data[1] == ACC_TYPE):
            return  # ACC_STOP и прочее — не интересует эти тесты
        if self._accept_command(data):
            self._data_cb(None, bytearray(_no_delta_frame([(1, 2, 3)])))
            self._control_cb(None, bytearray([0xF0, 0x02, ACC_TYPE, 0]))  # SUCCESS
        else:
            self._control_cb(None, bytearray([0xF0, 0x02, ACC_TYPE, 8]))  # NOT_SUPPORTED


def _run_start(client: _FakeClient, **kwargs) -> tuple[float, list]:
    async def run():
        batches = []
        stream = PmdAccStream(
            client, lambda ts, samples, hz: batches.append((ts, samples, hz)), **kwargs
        )
        hz = await stream.start()
        await stream.stop()
        return hz, batches

    return asyncio.run(run())


class DeclaredSettingsDriveStartCommandTests(unittest.TestCase):
    def test_start_command_includes_every_declared_setting(self):
        """Устройство перечисляет настройки, включая незнакомый тип 0x09 —
        команда старта обязана содержать все объявленные, а не только первые
        три (sample rate/resolution/range)."""
        settings_reply = bytes([0xF0, 0x01, ACC_TYPE, 0x00, 0x00]) + (
            _settings_block(0x00, [25, 50, 100, 200])
            + _settings_block(0x01, [16])
            + _settings_block(0x02, [2, 4, 8])
            + _settings_block(0x09, [7])
        )
        sent_commands = []

        def accept(command: bytes) -> bool:
            sent_commands.append(command)
            return True

        client = _FakeClient([settings_reply], accept_command=accept)
        hz, batches = _run_start(client)

        self.assertEqual(hz, 25.0)
        self.assertEqual(len(sent_commands), 1)
        command = sent_commands[0]
        # Незнакомый объявленный тип обязан попасть в команду как есть.
        self.assertIn(bytes([0x09, 0x01, 0x07, 0x00]), command)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0][1], [(1, 2, 3)])

    def test_multiframe_settings_response_is_reassembled(self):
        """Ответ на запрос настроек пришёл двумя кадрами (флаг «больше кадров»
        в первом — байт `more` заголовка, а не последний байт кадра, — взведён)
        — второй кадр обязан быть дочитан и учтён."""
        first = bytes([0xF0, 0x01, ACC_TYPE, 0x00, 0x01]) + _settings_block(0x00, [25])
        # Кадр-продолжение несёт только хвост параметров, без повторного заголовка.
        second = _settings_block(0x01, [16]) + _settings_block(0x09, [7])

        sent_commands = []

        def accept(command: bytes) -> bool:
            sent_commands.append(command)
            return True

        client = _FakeClient([first, second], accept_command=accept)
        hz, batches = _run_start(client)

        self.assertEqual(hz, 25.0)
        command = sent_commands[0]
        # Настройка из кадра-продолжения обязана попасть в команду.
        self.assertIn(bytes([0x09, 0x01, 0x07, 0x00]), command)
        self.assertIn(bytes([0x01, 0x01, 0x10, 0x00]), command)

    def test_silent_device_falls_back_to_hardcoded_settings(self):
        """Устройство не отвечает на запрос настроек вовсе — используем жёстко
        зашитый набор по умолчанию (три настройки, без channels — устройство
        его не объявляет, а лишнее поле отклоняется как INVALID_PARAMETER)."""
        sent_commands = []

        def accept(command: bytes) -> bool:
            sent_commands.append(command)
            return True

        client = _FakeClient(None, accept_command=accept)
        hz, batches = _run_start(client)

        self.assertEqual(hz, 25.0)
        command = sent_commands[0]
        self.assertEqual(
            command,
            bytes([0x02, ACC_TYPE, 0x00, 0x01, 25, 0x00, 0x01, 0x01, 16, 0x00, 0x02, 0x01, 8, 0x00]),
        )
        self.assertEqual(len(batches), 1)


if __name__ == "__main__":
    unittest.main()
