"""Сканирование BLE: обход KeyError Roles в bleak/BlueZ и проверка версии стека."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

MIN_BLUEZ_FOR_BLEAK_1 = (5, 55)
MIN_BLUEZ_FOR_BLEAK_0 = (5, 34)


def list_bluetooth_adapters() -> list[str]:
    """Имена адаптеров Linux: hci0, hci1, …"""
    base = Path("/sys/class/bluetooth")
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and p.name.startswith("hci"))


def default_bluetooth_adapter() -> str | None:
    adapters = list_bluetooth_adapters()
    return adapters[0] if adapters else None


def get_bluez_version_sync() -> tuple[int, int] | None:
    """Версия BlueZ из `bluetoothctl --version`, например (5, 53)."""
    try:
        proc = subprocess.run(
            ["bluetoothctl", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        text = (proc.stdout or "") + (proc.stderr or "")
        m = re.search(r"(\d+)\.(\d+)", text)
        if m:
            return int(m.group(1)), int(m.group(2))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def bleak_adapter_kwargs(adapter: str | None = None) -> dict[str, str]:
    """Параметры bleak для явного hci (0.22 и 3.x)."""
    hci = adapter or default_bluetooth_adapter()
    return {"adapter": hci} if hci else {}


def _installed_bleak_major() -> int | None:
    try:
        from importlib.metadata import version

        return int(version("bleak").split(".", 1)[0])
    except Exception:
        return None


class BleScanError(RuntimeError):
    """Сканирование / BLE недоступны (нет BT, выключен, старая BlueZ)."""


def bluez_too_old_message(bluez: tuple[int, int]) -> str:
    return (
        f"Установлена BlueZ {bluez[0]}.{bluez[1]}, а установленный bleak (≥1.x) "
        f"требует BlueZ ≥ {MIN_BLUEZ_FOR_BLEAK_1[0]}.{MIN_BLUEZ_FOR_BLEAK_1[1]}.\n"
        "Варианты:\n"
        "  1) Обновить BlueZ в системе (Ubuntu 22.04+ / свежий дистрибутив), или\n"
        "  2) В venv: pip install 'bleak>=0.22.3,<1'  (поддерживает BlueZ 5.34+, в т.ч. 5.53)\n"
        "  3) Укажите MAC датчика вручную в веб-форме"
    )


def ensure_ble_stack_compatible() -> None:
    """Проверка до подключения BLE; иначе BleScanError с подсказкой."""
    bluez = get_bluez_version_sync()
    if bluez is None:
        return
    major = _installed_bleak_major()
    if major is not None and major >= 1 and bluez < MIN_BLUEZ_FOR_BLEAK_1:
        raise BleScanError(bluez_too_old_message(bluez))
    if major is not None and major == 0 and bluez < MIN_BLUEZ_FOR_BLEAK_0:
        raise BleScanError(
            f"BlueZ {bluez[0]}.{bluez[1]} слишком стара для bleak 0.x "
            f"(нужна ≥ {MIN_BLUEZ_FOR_BLEAK_0[0]}.{MIN_BLUEZ_FOR_BLEAK_0[1]})."
        )


def format_bleak_connect_error(exc: BaseException) -> str | None:
    """Расшифровка типичной ошибки bleak при connect."""
    msg = str(exc)
    if "BlueZ >=" in msg or "5.55" in msg:
        bluez = get_bluez_version_sync()
        if bluez:
            return bluez_too_old_message(bluez)
        return (
            msg
            + "\nПереустановите зависимости: pip install 'bleak>=0.22.3,<1' "
            "или обновите BlueZ в системе."
        )
    return None


async def discover_ble_devices(timeout: float = 10.0) -> list[BLEDevice]:
    """
    Найти BLE-устройства через bleak.

    На части систем BlueZ не отдаёт свойство Adapter1.Roles — тогда bleak падает
    с KeyError при get_default_adapter(). Явный adapter=hci0 обходит это.
    """
    ensure_ble_stack_compatible()
    from bleak import BleakScanner
    from bleak.exc import BleakDBusError

    adapters = list_bluetooth_adapters()
    not_ready: list[str] = []
    last_exc: BaseException | None = None

    for hci in adapters:
        try:
            return await BleakScanner.discover(
                timeout=timeout, **bleak_adapter_kwargs(hci)
            )
        except KeyError as e:
            if "Roles" not in str(e):
                raise
            last_exc = e
            continue
        except BleakDBusError as e:
            last_exc = e
            if getattr(e, "dbus_error", None) == "org.bluez.Error.NotReady":
                not_ready.append(hci)
                continue
            raise BleScanError(
                f"BlueZ ({hci}): {e.dbus_error} — {e}"
            ) from e

    if not_ready:
        names = ", ".join(not_ready)
        raise BleScanError(
            f"Bluetooth выключен или адаптер не готов ({names}). "
            "Включите: `rfkill unblock bluetooth`, `bluetoothctl power on`, "
            "или включите BT в настройках системы."
        )

    if not adapters:
        raise BleScanError(
            "Bluetooth-адаптер не найден (нет /sys/class/bluetooth/hci*). "
            "Подключите dongle или включите Bluetooth."
        )

    try:
        return await BleakScanner.discover(timeout=timeout)
    except KeyError as e:
        if "Roles" in str(e):
            raise BleScanError(
                "Ошибка bleak/BlueZ: у адаптера нет свойства «Roles» "
                f"(адаптеры: {', '.join(adapters)}). Обновите BlueZ или укажите MAC вручную."
            ) from e
        raise
    except BleakDBusError as e:
        raise BleScanError(f"BlueZ: {e.dbus_error} — {e}") from e

    if last_exc is not None:
        raise BleScanError(f"Сканирование не удалось: {last_exc}") from last_exc
    raise BleScanError("Сканирование не удалось.")
