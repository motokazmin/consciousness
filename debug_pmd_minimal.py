"""Минимальный тест PMD без notify/indicate вообще — только write+read."""
import asyncio
import sys
from bleak import BleakClient

PMD_CONTROL_UUID = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"


def hx(data) -> str:
    return " ".join(f"{b:02x}" for b in data)


async def main(address: str):
    async with BleakClient(address, timeout=15.0) as client:
        print("Connected ✓")
        await asyncio.sleep(1.0)

        print(f"is_connected: {client.is_connected}")

        # Try plain read first (no write at all)
        try:
            val = await client.read_gatt_char(PMD_CONTROL_UUID)
            print(f"[CP READ before write] {hx(val)}")
        except Exception as exc:
            print(f"read before write failed: {exc!r}")

        await asyncio.sleep(0.5)
        print(f"is_connected: {client.is_connected}")

        # Try enabling SDK mode first (Request Measurement Start, type=9 SDK_MODE)
        sdk_cmd = bytearray([0x02, 0x09])
        print(f"\n--> write SDK mode start {hx(sdk_cmd)}")
        try:
            await client.write_gatt_char(PMD_CONTROL_UUID, sdk_cmd, response=True)
            print("SDK mode write ok")
        except Exception as exc:
            print(f"SDK mode write failed: {exc!r}")

        await asyncio.sleep(1.0)
        print(f"is_connected: {client.is_connected}")
        if not client.is_connected:
            print("disconnected after SDK mode attempt — stopping.")
            return

        cmd = bytearray([0x01, 0x02])
        print(f"\n--> write {hx(cmd)}")
        try:
            await client.write_gatt_char(PMD_CONTROL_UUID, cmd, response=True)
            print("write ok")
        except Exception as exc:
            print(f"write failed: {exc!r}")

        await asyncio.sleep(1.0)
        print(f"is_connected: {client.is_connected}")

        try:
            val = await client.read_gatt_char(PMD_CONTROL_UUID)
            print(f"[CP READ after write] {hx(val)}")
        except Exception as exc:
            print(f"read after write failed: {exc!r}")

        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
