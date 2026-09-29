"""Одна активная запись + поток источника RR."""

from __future__ import annotations

import datetime
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from hrv_core.db import (
    init_db,
    insert_accel_batch,
    load_hour_baseline,
    update_session_baseline,
)
from hrv_core.pipeline import HRVSessionState
from hrv_core.sources import build_source
from hrv_core.session_types import SESSION_TYPES
from hrv_core.summary import session_summary_dict

# Если за это время не пришёл ни один RR — сессия останавливается сама.
ARM_TIMEOUT_SEC = 300.0

# BLE: сколько ждём после первого RR первую пачку акселерометра, прежде чем
# взвести сессию по самому RR и писать без канала дыхания. Заказчик хочет,
# чтобы t0 совпадал с реальным стартом акселерометра (обе кривые должны
# покрывать сессию целиком) — но PMD документированно умеет отказывать молча
# (SUCCESS без единого кадра, см. ARCHITECTURE.md), и если ждать его
# безусловно, такая сессия никогда не взведётся и умрёт по ARM_TIMEOUT_SEC,
# унеся с собой RR. Это прямое нарушение «RR неприкосновенен», поэтому у
# ожидания есть потолок.
ACC_ARM_WAIT_SEC = 60.0


def _source_label(kind: str, address: str | None, *, mock_tag: str | None = None) -> str:
    if kind == "mock":
        st = SESSION_TYPES.get((mock_tag or "").strip().lower())
        if st and st.mock_profile != "default":
            return f"mock — профиль {st.label}"
        return "mock"
    if kind == "ble":
        return f"Polar H10  {address}"
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
    first_beat_at: float | None = None
    ws_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=2000))
    timer: threading.Timer | None = None
    # ts первого сырого RR — считается только для отмера ACC_ARM_WAIT_SEC,
    # сам этот удар может быть отброшен (если сессия в итоге взведётся по
    # акселерометру).
    first_rr_at: float | None = None
    # True, если взвели по RR-запасу, а не по акселерометру: канал дыхания в
    # этой записи не ответил за ACC_ARM_WAIT_SEC.
    accel_missing: bool = False
    # Для фронта: "ble_repair" → "waiting_accel" → "recording" (BLE),
    # либо "waiting_beat" → "recording" (mock — акселерометра нет вовсе).
    device_state: str = "waiting_beat"
    # Метка последней принятой пачки акселерометра — строка состояния канала
    # в панели идущей записи (PMD умеет умирать молча, см. ARCHITECTURE.md).
    last_accel_at: float | None = None

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
            # Первый удар (RMSSD ещё 0) — сохраняем для оси RR и sync с аудио.
            with self.conn_lock:
                self.conn.execute(
                    "INSERT INTO hrv_points (session_id, ts, rr_ms, rmssd) VALUES (?, ?, ?, ?)",
                    (self.session_id, ts, rr_ms, 0.0),
                )
                self.conn.commit()
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

    def on_accel_batch(
        self, batch_ts: float, samples: list[tuple[int, int, int]], hz: float
    ) -> None:
        """Пачка отсчётов PMD-акселерометра (~1 с). RR неприкосновенен: любая
        ошибка здесь логируется и проглатывается, запись RR не прерывается."""
        if self.stop_event.is_set():
            return
        self.last_accel_at = batch_ts
        self._enqueue_ws({"type": "accel_status", "ts": batch_ts})
        try:
            with self.conn_lock:
                insert_accel_batch(self.conn, self.session_id, batch_ts, samples, hz)
        except Exception as exc:
            print(f"PMD: не удалось записать пачку акселерометра (игнорирую): {exc}")

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
        opt_guided_phrases: bool = False,
        opt_audio_biofeedback: bool = False,
        opt_mic_recording: bool = False,
    ) -> RunningSession:
        if source_kind not in ("mock", "ble"):
            raise ValueError(f"неизвестный source: {source_kind}")
        with self._lock:
            if self._running is not None:
                raise RuntimeError("already_running")

        conn = init_db()
        label = _source_label(source_kind, address, mock_tag=tag if source_kind == "mock" else None)
        started = time.time()
        cur = conn.execute(
            "INSERT INTO sessions "
            "(tag, source, session_name, participant, started, drift_events, "
            "opt_guided_phrases, opt_audio_biofeedback, opt_mic_recording, "
            "opt_acc_recording) "
            # Акселерометр обязателен для каждой записи (решение заказчика) —
            # opt_acc_recording пишется 1 всегда; колонка остаётся только
            # затем, чтобы отличать старые сессии (см. ARCHITECTURE.md).
            # Фактическое наличие канала в сессии — по строкам в
            # hrv_accel_batches, не по этому полю.
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, 1)",
            (
                tag,
                label,
                session_name,
                participant,
                started,
                int(opt_guided_phrases),
                int(opt_audio_biofeedback),
                int(opt_mic_recording),
            ),
        )
        session_id = int(cur.lastrowid)
        conn.commit()

        hour = datetime.datetime.now().hour
        pers = load_hour_baseline(conn, hour)
        stop_event = threading.Event()
        conn_lock = threading.Lock()
        state = HRVSessionState(pers, desktop_notify=False)
        # Акселерометр не умеет только mock — там взводим по первому RR, как
        # раньше. BLE ждёт первую пачку PMD (см. ACC_ARM_WAIT_SEC).
        wait_for_accel = source_kind == "ble"
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
            device_state="ble_repair" if wait_for_accel else "waiting_beat",
        )

        def _device_state(new_state: str) -> None:
            rs.device_state = new_state
            rs._enqueue_ws({"type": "device_state", "state": new_state})

        source = build_source(
            source_kind,
            session_stop=stop_event,
            address=address,
            mock_tag=tag if source_kind == "mock" else None,
            on_state=_device_state if wait_for_accel else None,
        )
        rs.source = source

        def _beat(rr: float, ts: float) -> None:
            if rs.first_beat_at is not None:
                rs.on_beat(rr, ts)
                return
            if not wait_for_accel:
                self._arm(rs, ts)
                rs.on_beat(rr, ts)
                return
            if rs.first_rr_at is None:
                rs.first_rr_at = ts
            if ts - rs.first_rr_at < ACC_ARM_WAIT_SEC:
                # Ждём акселерометр — этот удар как будто не приходил: не
                # пишем в БД и не отдаём в HRVSessionState (иначе первый
                # «настоящий» удар после взведения перестанет быть первым для
                # расчёта дельт RMSSD).
                return
            print(
                f"PMD: акселерометр не дал ни одной пачки за {ACC_ARM_WAIT_SEC:.0f}с "
                "после первого RR — сессия взведена по RR, без канала дыхания."
            )
            self._arm(rs, ts, accel_missing=True)
            rs.on_beat(rr, ts)

        def _accel(batch_ts: float, samples: list[tuple[int, int, int]], hz: float) -> None:
            if wait_for_accel and rs.first_beat_at is None:
                self._arm(rs, batch_ts)
            rs.on_accel_batch(batch_ts, samples, hz)

        with self._lock:
            if self._running is not None:
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                conn.commit()
                conn.close()
                raise RuntimeError("already_running")
            self._running = rs

        source.start(_beat, _accel)

        def _arm_timeout() -> None:
            self.stop(session_id)

        rs.timer = threading.Timer(ARM_TIMEOUT_SEC, _arm_timeout)
        rs.timer.daemon = True
        rs.timer.start()

        return rs

    def _arm(self, rs: RunningSession, ts: float, *, accel_missing: bool = False) -> None:
        """Взведение: отсчёт длительности, sessions.started и ws «armed».

        Момент взведения — первая пачка акселерометра (BLE) или первый RR
        (mock, либо BLE после ACC_ARM_WAIT_SEC без акселерометра —
        `accel_missing=True`, см. вызовы в `start()`)."""
        if rs.first_beat_at is not None:
            return
        rs.first_beat_at = ts
        rs.accel_missing = accel_missing
        rs.device_state = "recording"
        if rs.timer is not None:
            try:
                rs.timer.cancel()
            except Exception:
                pass
            rs.timer = None
        with rs.conn_lock:
            rs.conn.execute(
                "UPDATE sessions SET started = ? WHERE id = ?",
                (ts, rs.session_id),
            )
            rs.conn.commit()
        rs._enqueue_ws({"type": "armed", "started_at": ts, "accel_missing": accel_missing})
        if (
            rs.duration_minutes is not None
            and rs.duration_minutes > 0
            and not rs.stop_event.is_set()
        ):
            session_id = rs.session_id

            def _auto_stop() -> None:
                self.stop(session_id)

            rs.timer = threading.Timer(rs.duration_minutes * 60.0, _auto_stop)
            rs.timer.daemon = True
            rs.timer.start()

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