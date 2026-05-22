"""
HRV Monitor MVP — CLI (matplotlib) и точка входа mock-verify / scan.

Веб-UI:  uvicorn hrv_web.server:app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import argparse
import asyncio
import threading
import time
import datetime

import matplotlib.pyplot as plt
import matplotlib.animation as animation
import numpy as np

from hrv_core.constants import (
    ANT_FALLBACK_WAIT_SEC,
    DB_PATH,
    MOCK_VERIFY_SEC,
    PLOT_RMSSD_SEC,
    PLOT_RR_SEC,
    BASELINE_SAMPLES,
    DRIFT_THRESHOLD,
)
from hrv_core.db import init_db, load_hour_baseline, update_session_baseline
from hrv_core.mock_verify import run_mock_verify
from hrv_core.pipeline import HRVSessionState
from hrv_core.sources import build_source, require_openant
from hrv_core.summary import print_session_summary


def build_plots(
    state: HRVSessionState,
    source_label: str,
    *,
    display_name: str | None = None,
    timer_minutes: float | None = None,
):
    fig, (ax_rr, ax_rmssd) = plt.subplots(2, 1, figsize=(13, 7))
    fig.patch.set_facecolor("#111")
    title = f"HRV Monitor  [{source_label}]"
    if display_name:
        title += f"  · «{display_name}»"
    if timer_minutes is not None:
        title += f"  · автостоп {timer_minutes:g} мин"
    fig.suptitle(title, color="#ccc", fontsize=12, y=0.98)

    for ax in (ax_rr, ax_rmssd):
        ax.set_facecolor("#1a1a1a")
        ax.tick_params(colors="#777")
        ax.spines[:].set_color("#2a2a2a")

    (line_rr,) = ax_rr.plot([], [], color="#4fc3f7", lw=0.9, alpha=0.85)
    ax_rr.set_ylabel("RR interval (ms)", color="#999")
    ax_rr.set_xlabel("seconds ago", color="#777")
    ax_rr.set_ylim(400, 1200)
    ax_rr.set_xlim(-PLOT_RR_SEC, 0)
    ax_rr.grid(True, color="#1f1f1f", lw=0.5)
    ax_rr.set_title("RR intervals", color="#bbb", fontsize=10, pad=5)

    (line_rmssd,) = ax_rmssd.plot([], [], lw=1.6)
    (line_baseline,) = ax_rmssd.plot(
        [], [], color="#444", lw=1.0, linestyle="--", label="session baseline"
    )
    (line_pers_bl,) = ax_rmssd.plot(
        [], [], color="#7986cb", lw=1.0, linestyle=":", label="persistent baseline"
    )
    ax_rmssd.fill_between([-PLOT_RMSSD_SEC, 0], 0, 0, alpha=0, color="#81c784")
    ax_rmssd.set_ylabel("RMSSD (ms)", color="#999")
    ax_rmssd.set_xlabel("seconds ago", color="#777")
    ax_rmssd.set_xlim(-PLOT_RMSSD_SEC, 0)
    ax_rmssd.set_ylim(0, 100)
    ax_rmssd.grid(True, color="#1f1f1f", lw=0.5)
    ax_rmssd.set_title("RMSSD  (60s window)", color="#bbb", fontsize=10, pad=5)

    info = ax_rmssd.text(
        0.02,
        0.92,
        "waiting for data…",
        transform=ax_rmssd.transAxes,
        color="#aaa",
        fontsize=10,
        va="top",
        family="monospace",
    )

    fig.tight_layout(rect=[0, 0, 1, 0.96])

    def update(_frame):
        now = time.time()
        rr_history = state.rr_history
        rmssd_history = state.rmssd_history
        pers = state.persistent_baseline

        rr_slice = [(t, r) for t, r in rr_history if t > now - PLOT_RR_SEC]
        if rr_slice:
            ts, rrs = zip(*rr_slice)
            line_rr.set_data([t - now for t in ts], rrs)

        rm_slice = [(t, r) for t, r in rmssd_history if t > now - PLOT_RMSSD_SEC]
        if rm_slice:
            ts, rms = zip(*rm_slice)
            xs = [t - now for t in ts]
            line_rmssd.set_data(xs, rms)
            ax_rmssd.set_ylim(0, max(100, max(rms) * 1.25))
            tail = list(rms)[-BASELINE_SAMPLES:]
            baseline = float(np.mean(tail))
            current = rms[-1]
            line_baseline.set_data([-PLOT_RMSSD_SEC, 0], [baseline, baseline])
            if pers is not None:
                line_pers_bl.set_data([-PLOT_RMSSD_SEC, 0], [pers, pers])
            drifting = current < baseline * DRIFT_THRESHOLD
            line_rmssd.set_color("#e57373" if drifting else "#81c784")
            pers_str = f"  pers {pers:.1f}" if pers else ""
            status = "⚠ DRIFT" if drifting else "● OK"
            info.set_text(
                f"RMSSD {current:6.1f} ms    baseline {baseline:5.1f} ms"
                f"{pers_str}    {status}"
            )
            info.set_color("#e57373" if drifting else "#aaa")

        return line_rr, line_rmssd, line_baseline, line_pers_bl, info

    ani = animation.FuncAnimation(
        fig, update, interval=500, blit=True, cache_frame_data=False
    )
    return fig, ani


def _run_plot_until_close(fig, minutes: float | None):
    if minutes is None:
        plt.show()
        return
    deadline = time.time() + minutes * 60.0
    plt.show(block=False)
    print(
        f"\nТаймер: сессия завершится через {minutes:g} мин "
        f"({minutes * 60:.0f} с). Окно можно закрыть раньше вручную.\n"
    )
    warn_1m = minutes >= 2.0
    warned_1 = False
    try:
        while plt.fignum_exists(fig.number):
            now = time.time()
            remaining = deadline - now
            if warn_1m and remaining <= 60.0 and not warned_1:
                warned_1 = True
                print("[timer] Осталась ~1 минута.")
            if remaining <= 0:
                print("\n[timer] Время истекло — закрываю окно.")
                plt.close(fig)
                break
            plt.pause(min(0.25, max(0.01, remaining)))
    except KeyboardInterrupt:
        plt.close(fig)


async def _scan():
    from hrv_core.ble_scan import BleScanError, discover_ble_devices

    print("Scanning 10s…")
    try:
        devices = await discover_ble_devices(timeout=10.0)
    except BleScanError as e:
        raise SystemExit(str(e)) from e
    polar = [d for d in devices if d.name and "Polar" in d.name]
    print(f"\n{'Polar devices:' if polar else 'No Polar devices found.'}")
    for d in (polar or devices):
        print(f"  {d.address}   {d.name or '—'}")


def main():
    parser = argparse.ArgumentParser(description="HRV Monitor MVP (CLI)")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument(
        "--mock-verify",
        action="store_true",
        help=f"Run mock {MOCK_VERIFY_SEC / 60:.0f} min without UI/DB",
    )
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--address", metavar="MAC")
    parser.add_argument("--ant-plus", action="store_true", dest="ant_plus")
    parser.add_argument("--ant-fallback", action="store_true", dest="ant_fallback")
    parser.add_argument(
        "--session",
        default="untagged",
        choices=["meditation", "focus", "rest", "scroll", "untagged"],
    )
    parser.add_argument("--name", metavar="TITLE", default=None)
    parser.add_argument("--minutes", type=float, default=None, metavar="MIN")
    parser.add_argument("--prompt-session", action="store_true")
    args = parser.parse_args()

    if args.scan:
        asyncio.run(_scan())
        return
    if args.mock_verify:
        run_mock_verify(MOCK_VERIFY_SEC)
        return

    if args.prompt_session and args.minutes is not None:
        parser.error("--prompt-session задаёт длительность сам; уберите --minutes")
    if args.prompt_session:
        try:
            name_in = input("Имя сессии (Enter — без имени): ").strip()
        except EOFError:
            name_in = ""
        args.name = name_in if name_in else None
        while True:
            try:
                raw_min = input("Длительность, минуты: ").strip().replace(",", ".")
                mins = float(raw_min)
            except EOFError:
                parser.error("Нужна длительность в минутах (stdin оборван)")
            except ValueError:
                print("Введите положительное число минут (например 15)")
                continue
            if mins <= 0:
                print("Введите число больше 0")
                continue
            args.minutes = mins
            break

    if args.minutes is not None and args.minutes <= 0:
        parser.error("--minutes must be positive")
    if args.ant_plus and args.ant_fallback:
        parser.error("--ant-plus и --ant-fallback несовместимы")
    if args.ant_fallback and not args.address:
        parser.error("--ant-fallback требует --address MAC")
    if args.mock and (args.ant_plus or args.ant_fallback):
        parser.error("--mock несовместим с --ant-plus / --ant-fallback")

    if args.mock:
        label = (
            "mock — профиль медитации (RSA)"
            if args.session == "meditation"
            else "mock"
        )
    elif args.ant_plus:
        try:
            require_openant()
        except RuntimeError as e:
            parser.error(str(e))
        label = "Polar H10 ANT+"
    elif args.ant_fallback:
        try:
            require_openant()
        except RuntimeError as e:
            parser.error(str(e))
        label = f"Polar H10 BLE {args.address} (+ANT fallback)"
    elif args.address:
        label = f"Polar H10  {args.address}"
    else:
        parser.error(
            "Укажите --mock, --address MAC, --ant-plus или --scan "
            "(или --address … --ant-fallback)"
        )

    if args.ant_plus and args.address:
        print(
            "Примечание: при --ant-plus адрес BLE (--address) не используется "
            "(поиск HRM по ANT+).\n"
        )

    conn = init_db()
    cur = conn.execute(
        "INSERT INTO sessions (tag, source, session_name, participant, started, drift_events) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (args.session, label, args.name, "cli", time.time()),
    )
    session_id = int(cur.lastrowid)
    conn.commit()

    current_hour = datetime.datetime.now().hour
    persistent = load_hour_baseline(conn, current_hour)
    baseline_at_start = persistent

    stop_event = threading.Event()
    conn_lock = threading.Lock()
    state = HRVSessionState(persistent, desktop_notify=True)

    if args.mock:
        source_kind = "mock"
    elif args.ant_plus:
        source_kind = "ant"
    elif args.ant_fallback:
        source_kind = "ble_ant_fallback"
    else:
        source_kind = "ble"

    source = build_source(
        source_kind,
        session_stop=stop_event,
        address=args.address,
        conn=conn,
        session_id=session_id,
        mock_tag=args.session if source_kind == "mock" else None,
        conn_lock=conn_lock if source_kind == "ble_ant_fallback" else None,
    )

    def on_beat(rr_ms: float, ts: float):
        sample = state.process_beat(rr_ms, ts)
        if sample is None:
            return
        with conn_lock:
            conn.execute(
                "INSERT INTO hrv_points (session_id, ts, rr_ms, rmssd) VALUES (?, ?, ?, ?)",
                (session_id, sample.ts, sample.rr_ms, sample.rmssd),
            )
            conn.commit()

    tit_extra = ""
    if args.name:
        tit_extra = f"  · {args.name}"
        print(f"Имя сессии (display): {args.name}")
    if args.minutes is not None:
        print(f"Авто-стоп: через {args.minutes:g} мин")
    print(f"Session #{session_id}  tag={args.session}{tit_extra}  source={label}")
    if persistent:
        print(f"Persistent baseline  hour={current_hour}  rmssd={persistent:.1f} ms")
    else:
        print(f"No baseline yet for hour {current_hour} (will be created after session)")
    print(f"DB → {DB_PATH.resolve()}\n")

    source.start(on_beat)

    try:
        fig, ani = build_plots(
            state,
            label,
            display_name=args.name,
            timer_minutes=args.minutes,
        )
        _run_plot_until_close(fig, args.minutes)
    except KeyboardInterrupt:
        plt.close("all")
    finally:
        stop_event.set()
        source.stop()
        ended_ts = time.time()
        conn.execute(
            "UPDATE sessions SET ended=?, drift_events=? WHERE id=?",
            (ended_ts, state.drift_events, session_id),
        )
        conn.commit()
        print_session_summary(conn, session_id, baseline_at_start, state.drift_events)
        update_session_baseline(conn, session_id)
        conn.close()
        print(f"\nSession #{session_id} saved → {DB_PATH}")


if __name__ == "__main__":
    main()