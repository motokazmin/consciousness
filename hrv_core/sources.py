"""Источники RR: mock, BLE."""

from __future__ import annotations

import asyncio
import math
import random
import threading
import time
from abc import ABC, abstractmethod

from hrv_core.session_types import SESSION_TYPES
from hrv_core.constants import (
    BLE_FIRST_RR_GRACE_SEC,
    BUSY_DEVICE_HINT,
    HR_UUID,
    RECONNECT_DELAY,
    RR_WATCHDOG_SEC,
    START_NOTIFY_RETRIES,
    START_NOTIFY_TIMEOUT,
)


class HRVSource(ABC):
    @abstractmethod
    def start(self, callback):
        """callback(rr_ms: float, ts: float) — на каждый RR."""

    @abstractmethod
    def stop(self):
        pass


class MockHRVSource(HRVSource):
    """Mock RR: по умолчанию цикл focused→drift→recovering; для tag meditation — RSA + спокойный ритм."""

    STATES = [
        dict(name="focused", rmssd_target=58, noise=7, duration=90),
        dict(name="drift", rmssd_target=20, noise=3, duration=55),
        dict(name="recovering", rmssd_target=38, noise=11, duration=70),
    ]

    def __init__(
        self,
        base_hr: float = 65.0,
        verbose: bool = True,
        *,
        mock_tag: str | None = None,
    ):
        self._base_hr = base_hr
        self._running = False
        self._thread = None
        self._prev_rr = 60_000 / base_hr
        self._verbose = verbose
        self._current_state = self.STATES[0]["name"]
        self._mock_tag = (mock_tag or "").strip().lower()
        self._meditation = self._mock_tag == "meditation"
        self._breath_period = random.uniform(8.5, 11.5)
        self._breath_phase0 = random.uniform(0, 2 * math.pi)
        self._t0 = 0.0

    def _next_rr(self, rmssd_target: float, mean_rr: float | None = None) -> float:
        m = float(mean_rr) if mean_rr is not None else 60_000 / self._base_hr
        noise_std = max(1, rmssd_target) / math.sqrt(2)
        ar = 0.52 if self._meditation else 0.5
        noise_scale = 0.78 if self._meditation else 0.7
        new_rr = m + ar * (self._prev_rr - m) + random.gauss(0, noise_std * noise_scale)
        self._prev_rr = new_rr
        return max(380, min(1400, new_rr))

    def _run_meditation(self, callback):
        """Спокойный ЧСС ~55–62 уд/мин, RSA (волна RR с дыханием), медленный дрейф базы."""
        self._current_state = "meditation"
        if self._verbose:
            print(
                f"[mock] профиль=meditation  RSA T≈{self._breath_period:.1f}s  "
                "цель RMSSD ~48–58 (парасимпатика)"
            )
        current_rmssd = 52.0 + random.gauss(0, 2)
        while self._running:
            t = time.time() - self._t0
            breath = math.sin(
                (2 * math.pi * t / self._breath_period)
                + self._breath_phase0
                + 0.08 * math.sin(2 * math.pi * t / 37.0)
            )
            rsa_amp = 32.0 + 14.0 * math.sin(2 * math.pi * t / 140.0)
            center = 985.0 + 45.0 * math.sin(2 * math.pi * t / 220.0) + 18.0 * math.sin(
                2 * math.pi * t / 91.0
            )
            mean_rr = center + rsa_amp * breath + random.gauss(0, 2.8)
            noisy_target = (
                52.0
                + 7.0 * math.sin(2 * math.pi * t / 88.0)
                + random.gauss(0, 2.2)
            )
            current_rmssd += (noisy_target - current_rmssd) * 0.035
            rmssd_use = max(18.0, min(72.0, current_rmssd))
            rr = self._next_rr(rmssd_use, mean_rr=mean_rr)
            callback(rr, time.time())
            beat_sec = rr / 1000.0
            time.sleep(max(0.34, beat_sec + random.gauss(0, 0.018)))

    def _run(self, callback):
        if self._meditation:
            self._run_meditation(callback)
            return
        state_idx = 0
        cfg = self.STATES[state_idx]
        state_ts = time.time()
        current_rmssd = float(cfg["rmssd_target"])
        self._current_state = cfg["name"]
        if self._verbose:
            print(f"[mock] state={cfg['name']}  target RMSSD={cfg['rmssd_target']} ms")

        while self._running:
            if time.time() - state_ts > cfg["duration"]:
                state_idx = (state_idx + 1) % len(self.STATES)
                cfg = self.STATES[state_idx]
                state_ts = time.time()
                self._current_state = cfg["name"]
                if self._verbose:
                    print(
                        f"[mock] → state={cfg['name']}  "
                        f"target RMSSD={cfg['rmssd_target']} ms"
                    )
            noisy_target = cfg["rmssd_target"] + random.gauss(0, cfg["noise"])
            current_rmssd += (noisy_target - current_rmssd) * 0.04
            rr = self._next_rr(max(4, current_rmssd))
            callback(rr, time.time())
            beat_sec = rr / 1000.0
            time.sleep(max(0.3, beat_sec + random.gauss(0, 0.015)))

    def start(self, callback):
        self._running = True
        self._t0 = time.time()
        self._thread = threading.Thread(target=self._run, args=(callback,), daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)


class PolarH10Source(HRVSource):
    def __init__(
        self,
        address: str,
        *,
        session_stop: threading.Event,
    ):
        self.address = address
        self._session_stop = session_stop
        self._callback = None
        self._last_rr_ts: float | None = None

    @staticmethod
    def _parse_rr(data: bytearray) -> list[float]:
        if len(data) < 2:
            return []
        flags = data[0]
        hr_16bit = bool(flags & 0x01)
        energy_present = bool(flags & 0x08)
        rr_present = bool((flags >> 4) & 0x01)
        if not rr_present:
            return []
        idx = 3 if hr_16bit else 2
        if energy_present:
            idx += 2
        values = []
        while idx + 1 < len(data):
            raw = int.from_bytes(data[idx : idx + 2], "little")
            ms = raw * 1000.0 / 1024.0
            if 300 < ms < 2000:
                values.append(ms)
            idx += 2
        return values

    def _ble_notify(self, _sender, data: bytearray):
        now = time.time()
        for rr in self._parse_rr(data):
            self._last_rr_ts = now
            self._callback(rr, now)

    async def _start_notify_with_retries(self, client) -> None:
        from bleak import BleakError

        last_exc: Exception | None = None
        for attempt in range(START_NOTIFY_RETRIES):
            try:
                await asyncio.wait_for(
                    client.start_notify(HR_UUID, self._ble_notify),
                    timeout=START_NOTIFY_TIMEOUT,
                )
                return
            except asyncio.TimeoutError:
                last_exc = asyncio.TimeoutError(
                    f"start_notify exceeded {START_NOTIFY_TIMEOUT}s"
                )
                print(
                    f"GATT timeout на start_notify (попытка {attempt + 1}/"
                    f"{START_NOTIFY_RETRIES})."
                )
            except BleakError as exc:
                last_exc = exc
                print(
                    f"start_notify BleakError (попытка {attempt + 1}/"
                    f"{START_NOTIFY_RETRIES}): {exc}"
                )
            except Exception as exc:
                last_exc = exc
                print(
                    f"start_notify error (попытка {attempt + 1}/"
                    f"{START_NOTIFY_RETRIES}): {exc}"
                )
            if attempt < START_NOTIFY_RETRIES - 1:
                await asyncio.sleep(2.0)
        assert last_exc is not None
        raise last_exc

    async def _loop(self):
        from bleak import BleakClient

        from hrv_core.ble_scan import (
            bleak_adapter_kwargs,
            ensure_ble_stack_compatible,
            format_bleak_connect_error,
        )

        try:
            ensure_ble_stack_compatible()
        except Exception as e:
            print(f"BLE: {e}")
            return

        bt_kw = bleak_adapter_kwargs()

        while not self._session_stop.is_set():
            self._last_rr_ts = None
            reconnect_pause = False
            try:
                print(f"Connecting to {self.address}...")
                async with BleakClient(
                    self.address, timeout=15.0, **bt_kw
                ) as client:
                    print("Connected ✓")
                    await self._start_notify_with_retries(client)
                    print(
                        f"Notifications ✓  (watchdog: нет RR {RR_WATCHDOG_SEC:.0f}s → "
                        f"переподключение)"
                    )
                    session_start = time.time()
                    while not self._session_stop.is_set():
                        await asyncio.sleep(0.5)
                        now = time.time()
                        if self._last_rr_ts is None:
                            if now - session_start > BLE_FIRST_RR_GRACE_SEC:
                                print(
                                    f"\nНет RR за {BLE_FIRST_RR_GRACE_SEC:.0f}s после подключения. "
                                    f"{BUSY_DEVICE_HINT}"
                                )
                                reconnect_pause = True
                                try:
                                    await client.disconnect()
                                except Exception:
                                    pass
                                break
                        elif now - self._last_rr_ts > RR_WATCHDOG_SEC:
                            print(
                                f"\nWatchdog: нет RR {RR_WATCHDOG_SEC:.0f}s "
                                f"(silent gap / потеря уведомлений). Переподключение…"
                            )
                            reconnect_pause = True
                            try:
                                await client.disconnect()
                            except Exception:
                                pass
                            break
                    else:
                        try:
                            await client.stop_notify(HR_UUID)
                        except Exception:
                            pass
            except Exception as exc:
                hint = format_bleak_connect_error(exc)
                if hint:
                    print(f"BLE error: {hint}")
                    return
                err_s = str(exc).lower()
                if any(
                    w in err_s
                    for w in ("failed", "disconnect", "not found", "unreachable", "refused")
                ):
                    print(f"BLE error: {exc}")
                    print(BUSY_DEVICE_HINT)
                else:
                    print(f"BLE error: {exc}")
                print(f"Reconnecting in {RECONNECT_DELAY}s…")
                await asyncio.sleep(RECONNECT_DELAY)
            else:
                if reconnect_pause and not self._session_stop.is_set():
                    await asyncio.sleep(RECONNECT_DELAY)

    def start(self, callback):
        self._callback = callback
        threading.Thread(
            target=lambda: asyncio.run(self._loop()),
            daemon=True,
        ).start()

    def stop(self):
        self._session_stop.set()


def build_source(
    kind: str,
    *,
    session_stop: threading.Event,
    address: str | None = None,
    mock_tag: str | None = None,
) -> HRVSource:
    if kind == "mock":
        mt = (mock_tag or "").strip().lower()
        st = SESSION_TYPES.get(mt)
        profile = mt if (st and st.mock_profile == mt) else None
        return MockHRVSource(mock_tag=profile, verbose=True)
    if kind == "ble":
        if not address:
            raise ValueError("ble требует address")
        return PolarH10Source(address, session_stop=session_stop)
    raise ValueError(f"неизвестный source kind: {kind}")