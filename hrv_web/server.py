"""FastAPI: REST + WebSocket + статика."""

from __future__ import annotations

import asyncio
import queue
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from hrv_core.constants import DB_PATH, SESSION_TAGS
from hrv_core.db import init_db, load_hour_baseline
from hrv_core.summary import session_summary_dict
from hrv_web.session_manager import MANAGER

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="HRV Monitor")


class StartSessionBody(BaseModel):
    participant: str = Field(..., min_length=1, max_length=200)
    tag: str
    session_name: str | None = Field(None, max_length=500)
    source: str = Field(..., description="mock | ble | ant | ble_ant_fallback")
    address: str | None = None
    minutes: float | None = Field(None, gt=0)


@app.get("/api/health")
def health():
    return {"ok": True, "db": str(DB_PATH.resolve())}


@app.get("/api/tags")
def tags():
    return {"tags": list(SESSION_TAGS)}


@app.post("/api/sessions")
def start_session(body: StartSessionBody):
    if body.tag not in SESSION_TAGS:
        raise HTTPException(400, f"tag must be one of {SESSION_TAGS}")
    if body.source in ("ble", "ble_ant_fallback") and not body.address:
        raise HTTPException(400, "address required for ble / ble_ant_fallback")
    try:
        rs = MANAGER.start(
            participant=body.participant.strip(),
            tag=body.tag,
            session_name=body.session_name,
            source_kind=body.source,
            address=body.address,
            minutes=body.minutes,
        )
    except RuntimeError as e:
        if "already_running" in str(e):
            raise HTTPException(409, "Уже идёт активная сессия записи. Остановите её сначала.") from e
        raise HTTPException(400, str(e)) from e
    return {
        "id": rs.session_id,
        "started": True,
        "started_at": rs.started_at,
        "duration_minutes": rs.duration_minutes,
    }


@app.post("/api/sessions/{session_id}/stop")
def stop_session(session_id: int):
    summary = MANAGER.stop(session_id)
    if summary is None:
        raise HTTPException(404, "Сессия не найдена или уже остановлена")
    return summary


@app.get("/api/sessions")
def list_sessions(
    participant: str | None = None,
    tag: str | None = None,
    limit: int = 200,
):
    conn = init_db()
    q = "SELECT id, tag, session_name, participant, source, started, ended, drift_events FROM sessions WHERE 1=1"
    args: list = []
    if participant:
        q += " AND participant LIKE ?"
        args.append(f"%{participant}%")
    if tag:
        q += " AND tag = ?"
        args.append(tag)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(min(limit, 2000))
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return {
        "sessions": [
            {
                "id": r[0],
                "tag": r[1],
                "session_name": r[2],
                "participant": r[3],
                "source": r[4],
                "started": r[5],
                "ended": r[6],
                "drift_events": r[7],
            }
            for r in rows
        ]
    }


@app.get("/api/sessions/{session_id}")
def get_session(session_id: int):
    import datetime

    conn = init_db()
    row = conn.execute(
        "SELECT tag, session_name, participant, source, started, ended, drift_events FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    tag, session_name, participant, source, started, ended, drift_n = row
    if ended is None:
        raise HTTPException(400, "Сессия ещё не завершена — сводка после stop")
    hour = datetime.datetime.fromtimestamp(started).hour
    conn2 = init_db()
    baseline_at_start = load_hour_baseline(conn2, hour)
    summary = session_summary_dict(conn2, session_id, baseline_at_start, int(drift_n or 0))
    conn2.close()
    return summary


@app.get("/api/sessions/{session_id}/points")
def session_points(session_id: int, max_points: int = 8000):
    max_points = max(100, min(max_points, 50_000))
    conn = init_db()
    rows = conn.execute(
        "SELECT ts, rr_ms, rmssd FROM hrv_points WHERE session_id = ? ORDER BY ts",
        (session_id,),
    ).fetchall()
    conn.close()
    if len(rows) > max_points:
        step = max(1, len(rows) // max_points)
        rows = rows[::step]
    return {
        "points": [{"ts": r[0], "rr_ms": r[1], "rmssd": r[2]} for r in rows],
        "count": len(rows),
    }


@app.websocket("/api/sessions/{session_id}/stream")
async def session_stream(websocket: WebSocket, session_id: int):
    await websocket.accept()
    rs = MANAGER.get_active()
    if rs is None or rs.session_id != session_id:
        await websocket.close(code=4404)
        return

    await websocket.send_json(
        {
            "type": "meta",
            "persistent_baseline": rs.state.persistent_baseline,
            "session_id": session_id,
            "started_at": rs.started_at,
            "duration_minutes": rs.duration_minutes,
        }
    )

    def _safe_get():
        try:
            return rs.ws_queue.get(timeout=0.12)
        except queue.Empty:
            return None

    loop = asyncio.get_running_loop()
    try:
        while True:
            msg = await loop.run_in_executor(None, _safe_get)
            if msg is not None:
                await websocket.send_json(msg)
                if msg.get("type") == "ended":
                    break
            elif rs.stop_event.is_set() and rs.ws_queue.empty():
                await websocket.send_json({"type": "ended", "session_id": session_id})
                break
    except WebSocketDisconnect:
        pass


@app.get("/api/scan")
async def scan_ble():
    from hrv_core.ble_scan import BleScanError, discover_ble_devices

    try:
        devices = await discover_ble_devices(timeout=10.0)
    except BleScanError as e:
        raise HTTPException(503, str(e)) from e
    polar = [d for d in devices if d.name and "Polar" in d.name]
    out = []
    for d in polar or devices:
        out.append({"address": d.address, "name": d.name or ""})
    return {"devices": out}


if STATIC_DIR.is_dir():
    from starlette.responses import Response
    from starlette.staticfiles import StaticFiles

    class DevStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            response: Response = await super().get_response(path, scope)
            if path.endswith((".js", ".html", ".css")):
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

    app.mount("/assets", DevStaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        return JSONResponse({"error": "static not built"}, status_code=503)
    return FileResponse(
        index_path,
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )
