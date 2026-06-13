"""Одна активная запись + поток источника RR."""

from __future__ import annotations

import datetime
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from hrv_core.db import init_db, load_hour_baseline, update_session_baseline
from hrv_core.pipeline import HRVSessionState
from hrv_core.sources import build_source, require_openant
from hrv_core.session_types import SESSION_TYPES
from hrv_core.summary import session_summary_dict


def _source_label(kind: str, address: str | None, *, mock_tag: str | None = None) -> str:
    if kind == "mock":
        st = SESSION_TYPES.get((mock_tag or "").strip().lower())
        if st and st.mock_profile != "default":
            return f"mock — профиль {st.label}"
        return "mock"
    if kind == "ble":
        return f"Polar H10  {address}"
    if kind == "ant":
        return "Polar H10 ANT+"
    if kind == "ble_ant_fallback":
        return f"Polar H10 BLE {address} (+ANT fallback)"
    return kind


@dataclass
class RunningSession:
    session_id: int
    conn: sqlite3.Connection
    conn_lock: threading.Lock
    stop_event: threading.Event
    state: HRVSessionState
    source: Any
    baseline_at_start: float | None
    started_at: float
    duration_minutes: float | None
    ws_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=2000))
    timer: threading.Timer | None = None
    last_resp_rate: float | None = field(default=None)
    last_resp_wave: list[float] = field(default_factory=list)

    def _enqueue_ws(self, payload: dict[str, Any]) -> None:
        try:
            self.ws_queue.put_nowait(payload)
        except queue.Full:
            try:
                self.ws_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.ws_queue.put_nowait(payload)
            except queue.Full:
                pass

    def on_resp(self, resp_rate: float | None, resp_wave: list[float]) -> None:
        if resp_rate is not None:
            self.last_resp_rate = resp_rate
        self.last_resp_wave = resp_wave

    def on_beat(self, rr_ms: float, ts: float) -> None:
        if self.stop_event.is_set():
            return
        sample = self.state.process_beat(rr_ms, ts)
        if sample is None:
            return
        with self.conn_lock:
            self.conn.execute(
                "INSERT INTO hrv_points (session_id, ts, rr_ms, rmssd) VALUES (?, ?, ?, ?)",
                (self.session_id, sample.ts, sample.rr_ms, sample.rmssd),
            )
            self.conn.commit()
        payload = {
            "type": "beat",
            "t": [sample.ts],
            "r": [sample.rr_ms],
            "m": [sample.rmssd],
            "sr": [sample.smoothed_rr],
            "rn": [sample.rmssd_normalized],
            "bl": sample.session_baseline,
            "drift": sample.drift_just_fired,
            "resp_rate": self.last_resp_rate,
            "resp_wave": self.last_resp_wave,
        }
        self._enqueue_ws(payload)

    def stop_source_only(self) -> None:
        self.stop_event.set()
        try:
            self.source.stop()
        except Exception:
            pass
        if self.timer is not None:
            try:
                self.timer.cancel()
            except Exception:
                pass


class SessionManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: RunningSession | None = None

    def has_active(self) -> bool:
        with self._lock:
            return self._running is not None

    def get_active(self) -> RunningSession | None:
        with self._lock:
            return self._running

    def start(
        self,
        *,
        participant: str,
        tag: str,
        session_name: str | None,
        source_kind: str,
        address: str | None,
        minutes: float | None,
    ) -> RunningSession:
        if source_kind in ("ant", "ble_ant_fallback"):
            require_openant()
        with self._lock:
            if self._running is not None:
                raise RuntimeError("already_running")

        conn = init_db()
        label = _source_label(source_kind, address, mock_tag=tag if source_kind == "mock" else None)
        started = time.time()
        cur = conn.execute(
            "INSERT INTO sessions (tag, source, session_name, participant, started, drift_events) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (tag, label, session_name, participant, started),
        )
        session_id = int(cur.lastrowid)
        conn.commit()

        hour = datetime.datetime.now().hour
        pers = load_hour_baseline(conn, hour)
        stop_event = threading.Event()
        conn_lock = threading.Lock()
        state = HRVSessionState(pers, desktop_notify=False)
        rs = RunningSession(
            session_id=session_id,
            conn=conn,
            conn_lock=conn_lock,
            stop_event=stop_event,
            state=state,
            source=None,
            baseline_at_start=pers,
            started_at=started,
            duration_minutes=minutes,
        )

        def _resp(resp_rate: float | None, resp_wave: list[float]) -> None:
            rs.on_resp(resp_rate, resp_wave)

        source = build_source(
            source_kind,
            session_stop=stop_event,
            address=address,
            conn=conn,
            session_id=session_id,
            mock_tag=tag if source_kind == "mock" else None,
            conn_lock=conn_lock,
            resp_callback=_resp,
        )
        rs.source = source

        def _beat(rr: float, ts: float) -> None:
            rs.on_beat(rr, ts)

        with self._lock:
            if self._running is not None:
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                conn.commit()
                conn.close()
                raise RuntimeError("already_running")
            self._running = rs

        source.start(_beat)

        if minutes is not None and minutes > 0:

            def _auto_stop() -> None:
                self.stop(session_id)

            rs.timer = threading.Timer(minutes * 60.0, _auto_stop)
            rs.timer.daemon = True
            rs.timer.start()

        return rs

    def stop(self, session_id: int) -> dict[str, Any] | None:
        with self._lock:
            rs = self._running
            if rs is None or rs.session_id != session_id:
                return None
            self._running = None

        # Set stop_event first so on_beat() returns early and no more beats
        # are appended to ws_queue after the "ended" message.
        rs.stop_source_only()
        ended = time.time()

        try:
            rs._enqueue_ws({"type": "ended", "session_id": session_id})
        except Exception:
            pass

        with rs.conn_lock:
            # Read drift_events inside conn_lock to avoid race with on_beat →
            # _check_drift which increments drift_events without holding conn_lock.
            drift_events = rs.state.drift_events
            rs.conn.execute(
                "UPDATE sessions SET ended=?, drift_events=? WHERE id=?",
                (ended, drift_events, session_id),
            )
            rs.conn.commit()
            update_session_baseline(rs.conn, session_id)
            summary = session_summary_dict(
                rs.conn, session_id, rs.baseline_at_start, drift_events
            )
        rs.conn.close()
        return summary


MANAGER = SessionManager()