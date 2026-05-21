"""Сканирование BLE: обход KeyError Roles в bleak/BlueZ и понятные ошибки."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice


def list_bluetooth_adapters() -> list[str]:
    """Имена адаптеров Linux: hci0, hci1, …"""
    base = Path("/sys/class/bluetooth")
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and p.name.startswith("hci"))


class BleScanError(RuntimeError):
    """Сканирование невозможно (нет BT, выключен, ошибка BlueZ)."""


async def discover_ble_devices(timeout: float = 10.0) -> list[BLEDevice]:
    """
    Найти BLE-устройства через bleak.

    На части систем BlueZ не отдаёт свойство Adapter1.Roles — тогда bleak падает
    с KeyError при get_default_adapter(). Явный adapter=hci0 обходит это.
    """
    from bleak import BleakScanner
    from bleak.exc import BleakDBusError

    adapters = list_bluetooth_adapters()
    not_ready: list[str] = []
    last_exc: BaseException | None = None

    for hci in adapters:
        try:
            return await BleakScanner.discover(timeout=timeout, bluez={"adapter": hci})
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
