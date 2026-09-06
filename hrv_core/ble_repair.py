"""Пересопряжение Polar H10 без участия человека.

**Зачем это вообще нужно.** На один bond H10 отдаёт поток PMD (акселерометр)
ровно в одном BLE-соединении. Внутри него измерение можно перезапускать
сколько угодно, но первый же разрыв соединения, после которого PMD хоть раз
работал, гасит акселерометр для этого bond'а окончательно — и молча:
шифрование поднимается, control point отвечает, старт возвращает SUCCESS,
повторный старт — ALREADY_IN_STATE (измерение внутри датчика идёт), а
уведомлений на характеристике данных нет ни одного. Проверено сравнением
HCI-трасс рабочего и нерабочего прогонов: на уровне ATT они совпадают
побайтово, различается только наличие Handle Value Notification.

Не помогают: физическое обесточивание датчика, перезапуск bluetoothd, снос
карты сервисов и GATT-кэша, переподписка на характеристику данных, подъём
соединения сторонним клиентом, отказ от команды стопа перед разрывом.
Помогает только новое сопряжение — одно на одну запись.

**Почему псевдотерминал.** Из неинтерактивного вызова `bluetoothctl pair`
падает с AuthenticationFailed: агенту сопряжения некому ответить. Поэтому
bluetoothctl запускается в pty и первым делом сам регистрирует агент
NoInputNoOutput — H10 использует Just Works, никакого ввода не требуется.

**Важное ограничение вызывающей стороны.** Пересопряжение расходуется на
первое же соединение, в котором пойдёт акселерометр. Линк, поднятый самим
`pair`, ронять не страшно — следующее соединение отработает. Но вторая запись
потребует ещё одного вызова: пересопрягаться нужно перед каждой. Поэтому
`hrv_core.sources.PolarH10Source` вызывает `repair()` сама один раз в момент
старта каждой записи с включённым `opt_acc_recording` (см.
`_maybe_repair_bond`), а не разово при запуске UI.

Запуск вручную (диагностика, без записи): `python -m hrv_core.ble_repair [--mac AA:BB:..]`
"""

from __future__ import annotations

import argparse
import os
import pty
import re
import select
import subprocess
import sys
import time

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

PAIR_OK = "Pairing successful"
_PAIR_FAIL = ("Failed to pair", "AuthenticationFailed", "org.bluez.Error")

# Сколько ждать появления датчика в эфире и сколько — самого сопряжения.
SCAN_TIMEOUT_SEC = 30.0
PAIR_TIMEOUT_SEC = 40.0


def find_polar_mac() -> str | None:
    """MAC ремня среди известных bluetoothctl устройств (по имени `Polar…`)."""
    try:
        out = subprocess.run(
            ["bluetoothctl", "devices"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) == 3 and parts[0] == "Device" and parts[2].lower().startswith("polar"):
            return parts[1]
    return None


class _Bluetoothctl:
    """Интерактивный bluetoothctl в pty: пишем команды, ждём подстроки в выводе."""

    def __init__(self, echo=None):
        self._echo = echo or (lambda _s: None)
        self._buf = ""
        self._pid, self._fd = pty.fork()
        if self._pid == 0:  # дочерний процесс
            os.execvp("bluetoothctl", ["bluetoothctl"])

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self._fd], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(self._fd, 4096)
            except OSError:
                return
            if not chunk:
                return
            text = _ANSI.sub("", chunk.decode(errors="replace"))
            self._buf += text
            self._echo(text)

    def send(self, cmd: str, settle: float = 1.0) -> None:
        os.write(self._fd, (cmd + "\n").encode())
        self.pump(settle)

    def wait_for(self, needles: tuple[str, ...], seconds: float) -> str | None:
        """Ждать любую из подстрок в новом выводе; вернуть найденную или None."""
        start = len(self._buf)
        end = time.time() + seconds
        while time.time() < end:
            self.pump(0.3)
            tail = self._buf[start:]
            for needle in needles:
                if needle in tail:
                    return needle
        return None

    def close(self) -> None:
        try:
            self.send("quit", settle=0.5)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass


def repair(mac: str, echo=None) -> bool:
    """Снять bond и сопрячься заново. True — сопряжение прошло.

    После успеха устройство остаётся подключённым, но держаться за этот линк не
    обязательно: акселерометр отработает и в следующем соединении. Расходуется
    пересопряжение на одну запись.
    """
    say = echo or (lambda _s: None)
    ctl = _Bluetoothctl(echo=echo)
    try:
        ctl.pump(1.5)
        ctl.send("agent NoInputNoOutput")
        ctl.send("default-agent")
        ctl.send(f"remove {mac}", settle=2.0)
        ctl.send("scan on", settle=0.5)
        say(f"\n[repair] жду {mac} в эфире…\n")
        if ctl.wait_for((mac,), SCAN_TIMEOUT_SEC) is None:
            say("\n[repair] датчик не появился в эфире — надет ли ремень?\n")
            ctl.send("scan off", settle=0.5)
            return False
        ctl.send(f"pair {mac}", settle=0.5)
        result = ctl.wait_for((PAIR_OK, *_PAIR_FAIL), PAIR_TIMEOUT_SEC)
        say(f"\n[repair] pair → {result or 'нет ответа'}\n")
        if result != PAIR_OK:
            ctl.send("scan off", settle=0.5)
            return False
        ctl.send(f"trust {mac}", settle=1.5)
        ctl.send("scan off", settle=1.5)
        return True
    finally:
        ctl.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mac", help="MAC ремня; по умолчанию ищется по имени Polar")
    ap.add_argument("--quiet", action="store_true", help="без вывода bluetoothctl")
    args = ap.parse_args()

    mac = args.mac or find_polar_mac()
    if not mac:
        print("Polar среди известных устройств не найден. Укажите --mac.")
        sys.exit(2)

    echo = None if args.quiet else (lambda s: (sys.stdout.write(s), sys.stdout.flush()))
    print(f"Пересопрягаю {mac}…")
    ok = repair(mac, echo=echo)
    print("Сопряжение восстановлено ✓" if ok else "Сопряжение не удалось ✗")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
