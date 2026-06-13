"""Диагностика PMD ACC для Polar H10.

Подключается, читает PMD Control Point индикацией,
запрашивает Get Measurement Settings (0x01, ACC=0x02),
логирует все сырые байты ответа.

Запуск: python3 debug_pmd.py AA:BB:CC:DD:EE:FF
"""
import asyncio
import sys
from bleak import BleakClient

PMD_CONTROL_UUID = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"
PMD_DATA_UUID = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"


def hx(data: bytearray) -> str:
    return " ".join(f"{b:02x}" for b in data)


async def main(address: str):
    response_event = asyncio.Event()
    last = bytearray()

    def on_cp(_sender, data: bytearray):
        nonlocal last
        last = data
        print(f"[CONTROL POINT] {hx(data)}")
        response_event.set()

    def on_data(_sender, data: bytearray):
        print(f"[DATA] {hx(data)[:80]}")

    async with BleakClient(address, timeout=15.0) as client:
        print("Connected ✓")
        await asyncio.sleep(1.0)

        cp_notify_ok = False
        for attempt in range(3):
            try:
                await client.start_notify(PMD_CONTROL_UUID, on_cp)
                print("CP notify/indicate enabled ✓")
                cp_notify_ok = True
                break
            except Exception as exc:
                print(f"start_notify(CP) attempt {attempt+1} failed: {exc!r}")
                await asyncio.sleep(1.5)
                if not client.is_connected:
                    print("device disconnected, reconnecting...")
                    await client.connect()
                    await asyncio.sleep(1.0)

        if not cp_notify_ok:
            print("Proceeding WITHOUT CP indication (blind writes).")

        # 1) Get Measurement Settings for ACC
        response_event.clear()
        cmd = bytearray([0x01, 0x02])
        print(f"\n--> sending Get Measurement Settings (ACC): {hx(cmd)}")
        await client.write_gatt_char(PMD_CONTROL_UUID, cmd, response=True)

        if cp_notify_ok:
            try:
                await asyncio.wait_for(response_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                print("(no notify response within 5s)")
        else:
            await asyncio.sleep(1.0)
            try:
                val = await client.read_gatt_char(PMD_CONTROL_UUID)
                print(f"[CP READ] {hx(val)}")
                last = val
            except Exception as exc:
                print(f"read_gatt_char(CP) failed: {exc!r}")

        await asyncio.sleep(1.0)

        # 2) Request Measurement Start
        if len(last) >= 4 and last[0] == 0x0F and last[3] == 0x00:
            settings_payload = last[4:]
            start_cmd = bytearray([0x02, 0x02]) + settings_payload
        else:
            print("\nNo valid settings response — using default ACC settings "
                  "(200Hz, 16-bit, 8G).")
            # type=0 sample_rate len=1 val=200(2B LE); type=1 resolution len=1 val=16(2B LE);
            # type=2 range len=1 val=8(2B LE)
            settings_payload = bytearray(
                [0x00, 0x01, 0xC8, 0x00, 0x01, 0x01, 0x10, 0x00, 0x02, 0x01, 0x08, 0x00]
            )
            start_cmd = bytearray([0x02, 0x02]) + settings_payload

        print(f"\n--> sending Request Measurement Start (ACC): {hx(start_cmd)}")
        response_event.clear()
        await client.write_gatt_char(PMD_CONTROL_UUID, start_cmd, response=True)

        if cp_notify_ok:
            try:
                await asyncio.wait_for(response_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                print("(no notify response within 5s)")
        else:
            await asyncio.sleep(1.0)
            try:
                val = await client.read_gatt_char(PMD_CONTROL_UUID)
                print(f"[CP READ] {hx(val)}")
                last = val
            except Exception as exc:
                print(f"read_gatt_char(CP) failed: {exc!r}")

        if not client.is_connected:
            print("\nDevice disconnected after start command.")
            return

        print("\n--> Subscribing to PMD DATA for 5s...")
        try:
            await client.start_notify(PMD_DATA_UUID, on_data)
            await asyncio.sleep(5.0)
            await client.stop_notify(PMD_DATA_UUID)
        except Exception as exc:
            print(f"PMD DATA notify failed: {exc!r}")

        stop_cmd = bytearray([0x03, 0x02])
        print(f"\n--> sending Request Measurement Stop (ACC): {hx(stop_cmd)}")
        try:
            await client.write_gatt_char(PMD_CONTROL_UUID, stop_cmd, response=True)
        except Exception as exc:
            print(f"stop failed: {exc!r}")
        await asyncio.sleep(1.0)

        if cp_notify_ok:
            await client.stop_notify(PMD_CONTROL_UUID)
        print("\nDone.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 debug_pmd.py <BLE_ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))