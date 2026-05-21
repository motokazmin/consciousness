"""Режим --mock-verify без UI и БД."""

import time
from collections import deque

import numpy as np

from hrv_core.constants import MOCK_VERIFY_SEC, RMSSD_WINDOW_SEC
from hrv_core.pipeline import compute_rmssd
from hrv_core.sources import MockHRVSource


def run_mock_verify(duration_sec: float = MOCK_VERIFY_SEC) -> None:
    rr_buf = deque()
    by_state: dict[str, list[float]] = {s["name"]: [] for s in MockHRVSource.STATES}
    mock = MockHRVSource(verbose=False)

    def handle(rr_ms: float, ts: float):
        rr_buf.append((ts, rr_ms))
        cutoff = ts - RMSSD_WINDOW_SEC
        while rr_buf and rr_buf[0][0] < cutoff:
            rr_buf.popleft()
        rmssd = compute_rmssd([r for _, r in rr_buf])
        if rmssd <= 0:
            return
        st = mock._current_state
        if st in by_state:
            by_state[st].append(rmssd)

    mock.start(handle)
    try:
        t_end = time.time() + duration_sec
        while time.time() < t_end:
            time.sleep(min(0.5, t_end - time.time()))
    except KeyboardInterrupt:
        print("\n(mock-verify interrupted)")
    finally:
        mock.stop()
        if mock._thread:
            mock._thread.join(timeout=3.0)

    if duration_sec >= 60:
        dur_fmt = f"{duration_sec / 60:.1f} min"
    else:
        dur_fmt = f"{duration_sec:.0f} s"
    print(
        f"\nMock verify  duration={dur_fmt}  "
        f"(RMSSD = {RMSSD_WINDOW_SEC}s sliding window; соседние состояния "
        f"на границах смешиваются)\n"
    )
    header = (
        f"{'state':<12} {'target':>8} {'n':>6} {'mean':>8} {'std':>8} "
        f"{'min':>8} {'max':>8} {'Δ mean':>8}"
    )
    print(header)
    print("-" * len(header))
    for st in MockHRVSource.STATES:
        name = st["name"]
        tgt = float(st["rmssd_target"])
        vals = by_state[name]
        if not vals:
            print(
                f"{name:<12} {tgt:8.1f} {0:6d} "
                f"{'—':>8} {'—':>8} {'—':>8} {'—':>8} {'—':>8}"
            )
            continue
        arr = np.asarray(vals, dtype=float)
        mean = float(np.mean(arr))
        std = float(np.std(arr))
        mn = float(np.min(arr))
        mx = float(np.max(arr))
        delta = mean - tgt
        print(
            f"{name:<12} {tgt:8.1f} {len(vals):6d} {mean:8.1f} {std:8.1f} "
            f"{mn:8.1f} {mx:8.1f} {delta:+8.1f}"
        )
