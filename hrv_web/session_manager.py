"""Одна активная запись + поток источника RR."""

from __future__ import annotations

import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from hrv_core.db import init_db, load_hour_baseline, update_session_baseline
from hrv_core.pipeline import HRVSessionState
from hrv_core.sources import build_source, require_openant
from hrv_core.summary import session_summary_dict


def _source_label(kind: str, address: str | None, *, mock_tag: str | None = None) -> str:
    if kind == "mock":
        if mock_tag == "meditation":
            return "mock — профиль медитации (RSA)"
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

        import datetime

        hour = datetime.datetime.now().hour
        pers = load_hour_baseline(conn, hour)
        stop_event = threading.Event()
        conn_lock = threading.Lock()
        state = HRVSessionState(pers, desktop_notify=False)
        source = build_source(
            source_kind,
            session_stop=stop_event,
            address=address,
            conn=conn,
            session_id=session_id,
            mock_tag=tag if source_kind == "mock" else None,
            conn_lock=conn_lock,
        )
        rs = RunningSession(
            session_id=session_id,
            conn=conn,
            conn_lock=conn_lock,
            stop_event=stop_event,
            state=state,
            source=source,
            baseline_at_start=pers,
            started_at=started,
            duration_minutes=minutes,
        )

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

        try:
            rs._enqueue_ws({"type": "ended", "session_id": session_id})
        except Exception:
            pass
        rs.stop_source_only()
        ended = time.time()
        with rs.conn_lock:
            rs.conn.execute(
                "UPDATE sessions SET ended=?, drift_events=? WHERE id=?",
                (ended, rs.state.drift_events, session_id),
            )
            rs.conn.commit()
            update_session_baseline(rs.conn, session_id)
            summary = session_summary_dict(
                rs.conn, session_id, rs.baseline_at_start, rs.state.drift_events
            )
        rs.conn.close()
        return summary


MANAGER = SessionManager()
