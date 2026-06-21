"""FastAPI: REST + WebSocket + статика."""

from __future__ import annotations

import asyncio
import datetime
import queue
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from hrv_core.analysis import progress_session_analysis, session_analysis
from hrv_core.constants import DB_PATH
from hrv_core.db import delete_session, init_db, load_hour_baseline, wipe_all_history
from hrv_core.summary import session_summary_dict
from hrv_core.note_tags import note_tag_sql_pattern, parse_note_tags
from hrv_core.tags import normalize_tag
from hrv_web.session_manager import MANAGER

STATIC_DIR = Path(__file__).resolve().parent / "static"
PHRASES_DIR = STATIC_DIR / "phrases"
PHRASE_SET_ID_RE = re.compile(r"^[\w\-]+$")
PHRASE_FILE_RE = re.compile(r"^(\w+)_(.+)_(\d+)\.mp3$")

app = FastAPI(title="HRV Monitor")


class StartSessionBody(BaseModel):
    participant: str = Field(..., min_length=1, max_length=200)
    tag: str
    session_name: str | None = Field(None, max_length=4000)
    source: str = Field(..., description="mock | ble")
    address: str | None = None
    minutes: float | None = Field(None, gt=0)
    opt_guided_phrases: bool = False
    opt_audio_biofeedback: bool = False


class PhraseLogBody(BaseModel):
    session_id: int
    phrase_file: str = Field(..., min_length=1, max_length=200)
    played_at: float
    rn_before: float | None = None
    rmssd_before: float | None = None
    rn_after_30s: float | None = None
    rmssd_after_30s: float | None = None


class PhraseLogPatchBody(BaseModel):
    rn_after_30s: float | None = None
    rmssd_after_30s: float | None = None


class PatchSessionNotesBody(BaseModel):
    session_name: str | None = Field(None, max_length=4000)


class CreateSessionTypeBody(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[\w\-\.а-яА-ЯёЁ]+$")
    label: str = Field(..., min_length=1, max_length=100)


def _parse_date_start(iso_date: str | None) -> float | None:
    """YYYY-MM-DD → unix начала дня (local)."""
    if not iso_date or not iso_date.strip():
        return None
    try:
        d = datetime.date.fromisoformat(iso_date.strip()[:10])
    except ValueError as e:
        raise HTTPException(400, f"Неверная дата: {iso_date}") from e
    return datetime.datetime.combine(d, datetime.time.min).timestamp()


def _parse_date_end(iso_date: str | None) -> float | None:
    """YYYY-MM-DD → unix конца дня (exclusive upper: start of next day)."""
    if not iso_date or not iso_date.strip():
        return None
    try:
        d = datetime.date.fromisoformat(iso_date.strip()[:10])
    except ValueError as e:
        raise HTTPException(400, f"Неверная дата: {iso_date}") from e
    next_day = d + datetime.timedelta(days=1)
    return datetime.datetime.combine(next_day, datetime.time.min).timestamp()


def _session_filters(
    *,
    participant: str | None,
    tag: str | None,
    note_tags: list[str] | None,
    started_after: str | None,
    started_before: str | None,
    ended_only: bool = False,
) -> tuple[str, list]:
    q = " FROM sessions WHERE 1=1"
    args: list = []
    if ended_only:
        q += " AND ended IS NOT NULL"
    if participant:
        q += " AND participant LIKE ?"
        args.append(f"%{participant}%")
    if tag:
        q += " AND tag = ?"
        args.append(tag)
    if note_tags:
        like_parts: list[str] = []
        for raw in note_tags:
            try:
                pattern = note_tag_sql_pattern(raw).lower()
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            like_parts.append("LOWER(session_name) LIKE ? ESCAPE '\\'")
            args.append(pattern)
        q += " AND (" + " OR ".join(like_parts) + ")"
    t0 = _parse_date_start(started_after)
    t1 = _parse_date_end(started_before)
    if t0 is not None:
        q += " AND started >= ?"
        args.append(t0)
    if t1 is not None:
        q += " AND started < ?"
        args.append(t1)
    return q, args


def _decimate_rows(rows: list, max_points: int) -> list:
    if len(rows) <= max_points:
        return rows
    # Time-based bucketing: divide the time range into max_points equal buckets
    # and keep the first row in each bucket. Preserves temporal distribution
    # and avoids discarding peaks in sparse regions.
    t_start = rows[0][0]
    t_end   = rows[-1][0]
    duration = t_end - t_start
    if duration <= 0:
        return rows[::max(1, len(rows) // max_points)]
    bucket_sec = duration / max_points
    result: list = []
    next_boundary = t_start
    for row in rows:
        if row[0] >= next_boundary:
            result.append(row)
            next_boundary = row[0] + bucket_sec
    return result


def _all_session_types(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT slug, label, phrase_prefix, mock_profile, chart_profile, is_custom "
        "FROM session_types ORDER BY is_custom ASC, slug ASC"
    ).fetchall()
    return [
        {"slug": r[0], "label": r[1], "phrase_prefix": r[2],
         "mock_profile": r[3], "chart_profile": r[4], "is_custom": bool(r[5])}
        for r in rows
    ]


@app.get("/api/health")
def health():
    return {"ok": True, "db": str(DB_PATH.resolve())}


@app.get("/api/note-tags")
def list_note_tags():
    """Уникальные теги из заметок (#утро, #глубоко) для фильтров."""
    conn = init_db()
    rows = conn.execute(
        "SELECT session_name FROM sessions WHERE session_name IS NOT NULL"
    ).fetchall()
    conn.close()
    tags: set[str] = set()
    for (text,) in rows:
        tags.update(parse_note_tags(text))
    return {"tags": sorted(tags)}


@app.get("/api/session-types")
def get_session_types():
    """Все типы сессий из БД (системные + пользовательские)."""
    conn = init_db()
    out = _all_session_types(conn)
    conn.close()
    return {"session_types": out}


@app.post("/api/session-types")
def create_session_type(body: CreateSessionTypeBody):
    """Создать пользовательский тип сессии."""
    conn = init_db()
    try:
        existing = conn.execute(
            "SELECT slug FROM session_types WHERE slug = ?", (body.slug,)
        ).fetchone()
        if existing:
            raise HTTPException(409, f"Тип '{body.slug}' уже существует")
        conn.execute(
            "INSERT INTO session_types "
            "(slug, label, phrase_prefix, mock_profile, chart_profile, is_custom) "
            "VALUES (?, ?, NULL, 'default', 'default', 1)",
            (body.slug, body.label),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "slug": body.slug, "label": body.label}


@app.delete("/api/session-types/{slug}")
def delete_session_type(slug: str):
    """Удалить пользовательский тип сессии (системные — нельзя)."""
    conn = init_db()
    try:
        row = conn.execute(
            "SELECT is_custom FROM session_types WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Тип не найден")
        if not row[0]:
            raise HTTPException(403, "Системные типы нельзя удалять")
        conn.execute("DELETE FROM session_types WHERE slug = ?", (slug,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "deleted": slug}


@app.post("/api/sessions")
def start_session(body: StartSessionBody):
    try:
        tag = normalize_tag(body.tag)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if body.source not in ("mock", "ble"):
        raise HTTPException(400, "source must be mock or ble")
    if body.source == "ble" and not body.address:
        raise HTTPException(400, "address required for ble")
    try:
        rs = MANAGER.start(
            participant=body.participant.strip(),
            tag=tag,
            session_name=body.session_name,
            source_kind=body.source,
            address=body.address,
            minutes=body.minutes,
            opt_guided_phrases=body.opt_guided_phrases,
            opt_audio_biofeedback=body.opt_audio_biofeedback,
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
        "tag": tag,
    }


@app.post("/api/sessions/{session_id}/stop")
def stop_session(session_id: int):
    summary = MANAGER.stop(session_id)
    if summary is None:
        raise HTTPException(404, "Сессия не найдена или уже остановлена")
    return summary


@app.patch("/api/sessions/{session_id}")
def patch_session(session_id: int, body: PatchSessionNotesBody):
    conn = init_db()
    try:
        row = conn.execute(
            "SELECT ended FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Сессия не найдена")
        if row[0] is None:
            raise HTTPException(400, "Заметки можно сохранить только после завершения сессии")
        notes = (body.session_name or "").strip() or None
        conn.execute(
            "UPDATE sessions SET session_name = ? WHERE id = ?",
            (notes, session_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "session_name": notes}


@app.get("/api/sessions")
def list_sessions(
    participant: str | None = None,
    tag: str | None = None,
    note_tag: list[str] = Query(default=[]),
    started_after: str | None = None,
    started_before: str | None = None,
    limit: int = 200,
):
    conn = init_db()
    filt, args = _session_filters(
        participant=participant,
        tag=tag,
        note_tags=note_tag or None,
        started_after=started_after,
        started_before=started_before,
    )
    q = (
        "SELECT id, tag, session_name, participant, source, started, ended, "
        "drift_events, opt_guided_phrases, opt_audio_biofeedback"
        + filt
        + " ORDER BY id DESC LIMIT ?"
    )
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
                "opt_guided_phrases": bool(r[8]),
                "opt_audio_biofeedback": bool(r[9]),
                "note_tags": parse_note_tags(r[2]),
            }
            for r in rows
        ]
    }


@app.get("/api/progress")
def progress_data(
    tag: str | None = None,
    note_tag: list[str] = Query(default=[]),
    started_after: str | None = None,
    started_before: str | None = None,
    max_sessions: int = 40,
    max_points_per_session: int = 4000,
):
    max_sessions = max(1, min(max_sessions, 80))
    max_points_per_session = max(100, min(max_points_per_session, 12_000))

    conn = init_db()
    filt, args = _session_filters(
        participant=None,
        tag=tag,
        note_tags=note_tag or None,
        started_after=started_after,
        started_before=started_before,
        ended_only=True,
    )
    q = (
        "SELECT id, tag, started, ended"
        + filt
        + " ORDER BY started ASC LIMIT ?"
    )
    args.append(max_sessions)
    sessions = conn.execute(q, args).fetchall()

    out_sessions = []
    for sid, stag, started, ended in sessions:
        rows = conn.execute(
            "SELECT ts, rr_ms FROM hrv_points WHERE session_id = ? ORDER BY ts",
            (sid,),
        ).fetchall()
        rows = _decimate_rows(rows, max_points_per_session)
        if not rows:
            continue
        duration_sec = float(ended - started) if ended and started else 0.0
        if duration_sec <= 0 and rows:
            duration_sec = float(rows[-1][0] - started)
        points = [
            {"x": round(float(ts - started), 3), "rr": float(rr)}
            for ts, rr in rows
        ]
        out_sessions.append(
            {
                "id": sid,
                "tag": stag,
                "started": started,
                "duration_sec": duration_sec,
                "points": points,
            }
        )
    conn.close()
    return {"sessions": out_sessions}


@app.delete("/api/history")
def wipe_history():
    if MANAGER.get_active() is not None:
        raise HTTPException(
            409,
            "Сначала остановите активную запись сессии.",
        )
    conn = init_db()
    try:
        n_sessions = wipe_all_history(conn)
    finally:
        conn.close()
    return {"ok": True, "deleted_sessions": n_sessions}


@app.delete("/api/sessions/{session_id}")
def delete_one_session(session_id: int):
    active = MANAGER.get_active()
    if active is not None and active.session_id == session_id:
        raise HTTPException(
            409,
            "Нельзя удалить активную сессию — сначала остановите запись.",
        )
    conn = init_db()
    try:
        if not delete_session(conn, session_id):
            raise HTTPException(404, "Сессия не найдена")
    finally:
        conn.close()
    return {"ok": True, "deleted_session_id": session_id}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: int):
    conn = init_db()
    row = conn.execute(
        "SELECT tag, session_name, participant, source, started, ended, drift_events, "
        "opt_guided_phrases, opt_audio_biofeedback FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404)
    (
        tag,
        session_name,
        participant,
        source,
        started,
        ended,
        drift_n,
        opt_guided,
        opt_audio,
    ) = row
    if ended is None:
        conn.close()
        raise HTTPException(400, "Сессия ещё не завершена — сводка после stop")
    hour = datetime.datetime.fromtimestamp(started).hour
    baseline_at_start = load_hour_baseline(conn, hour)
    summary = session_summary_dict(conn, session_id, baseline_at_start, int(drift_n or 0))
    conn.close()
    if summary is not None:
        summary["opt_guided_phrases"] = bool(opt_guided)
        summary["opt_audio_biofeedback"] = bool(opt_audio)
        summary["note_tags"] = parse_note_tags(session_name)
    return summary


@app.get("/api/sessions/{session_id}/analysis")
def session_analysis_endpoint(
    session_id: int,
    max_points: int = 12_000,
    stable_zone: bool = False,
    smooth: bool = False,
):
    max_points = max(100, min(max_points, 50_000))
    conn = init_db()
    row = conn.execute(
        "SELECT started, ended FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404)
    started, ended = row
    if ended is None:
        conn.close()
        raise HTTPException(400, "Сессия ещё не завершена — анализ после stop")
    rows = conn.execute(
        "SELECT ts, rr_ms, rmssd FROM hrv_points WHERE session_id = ? ORDER BY ts",
        (session_id,),
    ).fetchall()
    conn.close()
    rows = _decimate_rows(rows, max_points)
    return session_analysis(
        rows, started, ended, stable_zone=stable_zone or smooth
    )


@app.get("/api/progress/analysis")
def progress_analysis(
    tag: str | None = None,
    participant: str | None = None,
    note_tag: list[str] = Query(default=[]),
    started_after: str | None = None,
    started_before: str | None = None,
    max_sessions: int = 40,
    max_points_per_session: int = 4000,
    stable_zone: bool = False,
    smooth: bool = False,
):
    max_sessions = max(1, min(max_sessions, 80))
    max_points_per_session = max(100, min(max_points_per_session, 12_000))

    conn = init_db()
    filt, args = _session_filters(
        participant=participant,
        tag=tag,
        note_tags=note_tag or None,
        started_after=started_after,
        started_before=started_before,
        ended_only=True,
    )
    q = (
        "SELECT id, tag, started, ended"
        + filt
        + " ORDER BY started ASC LIMIT ?"
    )
    args.append(max_sessions)
    sessions = conn.execute(q, args).fetchall()

    out_sessions = []
    for sid, stag, started, ended in sessions:
        rows = conn.execute(
            "SELECT ts, rr_ms, rmssd FROM hrv_points WHERE session_id = ? ORDER BY ts",
            (sid,),
        ).fetchall()
        if not rows:
            continue
        rows_dec = _decimate_rows(rows, max_points_per_session)
        stats = conn.execute(
            "SELECT AVG(rmssd) FROM hrv_points WHERE session_id = ?",
            (sid,),
        ).fetchone()
        rmssd_mean = float(stats[0]) if stats and stats[0] is not None else None
        analysis = progress_session_analysis(
            rows_dec,
            started,
            ended,
            rmssd_mean,
            stable_zone=stable_zone or smooth,
        )
        out_sessions.append(
            {
                "id": sid,
                "tag": stag,
                "started": started,
                **analysis,
            }
        )
    conn.close()
    return {"sessions": out_sessions}


@app.get("/api/sessions/{session_id}/points")
def session_points(session_id: int, max_points: int = 8000):
    max_points = max(100, min(max_points, 50_000))
    conn = init_db()
    rows = conn.execute(
        "SELECT ts, rr_ms, rmssd FROM hrv_points WHERE session_id = ? ORDER BY ts",
        (session_id,),
    ).fetchall()
    conn.close()
    rows = _decimate_rows(rows, max_points)
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


def _phrase_set_dir(prefix: str, phrase_set: str) -> Path:
    if not PHRASE_SET_ID_RE.match(prefix) or not PHRASE_SET_ID_RE.match(phrase_set):
        raise HTTPException(400, "Недопустимый prefix или set")
    target = (PHRASES_DIR / prefix / phrase_set).resolve()
    if not target.is_dir() or PHRASES_DIR.resolve() not in target.parents:
        raise HTTPException(404, "Набор фраз не найден")
    return target


def _build_phrase_manifest(prefix: str, phrase_set: str) -> dict[str, list[int]]:
    manifest: dict[str, list[int]] = {}
    for path in _phrase_set_dir(prefix, phrase_set).glob("*.mp3"):
        m = PHRASE_FILE_RE.match(path.name)
        if not m or m.group(1) != prefix:
            continue
        category, num_s = m.group(2), m.group(3)
        manifest.setdefault(category, []).append(int(num_s))
    for category in manifest:
        manifest[category] = sorted(manifest[category])
    return manifest


@app.get("/api/meditation/phrase-sets")
def phrase_sets(prefix: str | None = Query(None)):
    """Доступные наборы mp3: phrases/{prefix}/{set}/."""
    if prefix is not None and not PHRASE_SET_ID_RE.match(prefix):
        raise HTTPException(400, "Недопустимый prefix")
    sets: list[dict] = []
    if not PHRASES_DIR.is_dir():
        return {"sets": sets}
    for prefix_dir in sorted(PHRASES_DIR.iterdir()):
        if not prefix_dir.is_dir() or prefix_dir.name.startswith("."):
            continue
        if prefix is not None and prefix_dir.name != prefix:
            continue
        for set_dir in sorted(prefix_dir.iterdir()):
            if not set_dir.is_dir() or set_dir.name.startswith("."):
                continue
            mp3_count = sum(1 for _ in set_dir.glob("*.mp3"))
            if not mp3_count:
                continue
            sets.append(
                {
                    "id": f"{prefix_dir.name}/{set_dir.name}",
                    "prefix": prefix_dir.name,
                    "set": set_dir.name,
                    "label": f"{prefix_dir.name}/{set_dir.name}",
                    "mp3_count": mp3_count,
                }
            )
    return {"sets": sets}


@app.get("/api/meditation/phrase-manifest")
def phrase_manifest(
    prefix: str = Query(..., min_length=1),
    phrase_set: str = Query("directive", alias="set", min_length=1),
):
    """Список mp3-фраз в phrases/{prefix}/{set}/."""
    return _build_phrase_manifest(prefix, phrase_set)


@app.post("/api/meditation/phrase-log")
def create_phrase_log(body: PhraseLogBody):
    conn = init_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO meditation_phrase_log
                (session_id, phrase_file, played_at, rn_before, rmssd_before,
                 rn_after_30s, rmssd_after_30s)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body.session_id,
                body.phrase_file,
                body.played_at,
                body.rn_before,
                body.rmssd_before,
                body.rn_after_30s,
                body.rmssd_after_30s,
            ),
        )
        conn.commit()
        log_id = cur.lastrowid
    finally:
        conn.close()
    return {"id": log_id, "ok": True}


@app.patch("/api/meditation/phrase-log/{log_id}")
def patch_phrase_log(log_id: int, body: PhraseLogPatchBody):
    conn = init_db()
    try:
        row = conn.execute(
            "SELECT id FROM meditation_phrase_log WHERE id = ?", (log_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Запись не найдена")
        conn.execute(
            """
            UPDATE meditation_phrase_log
            SET rn_after_30s = ?, rmssd_after_30s = ?
            WHERE id = ?
            """,
            (body.rn_after_30s, body.rmssd_after_30s, log_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": log_id, "ok": True}


@app.get("/api/meditation/phrase-stats")
def phrase_stats(session_id: int):
    conn = init_db()
    try:
        rows = conn.execute(
            """
            SELECT id, session_id, phrase_file, played_at,
                   rn_before, rmssd_before, rn_after_30s, rmssd_after_30s
            FROM meditation_phrase_log
            WHERE session_id = ?
            ORDER BY played_at
            """,
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "session_id": session_id,
        "phrases": [
            {
                "id": r[0],
                "session_id": r[1],
                "phrase_file": r[2],
                "played_at": r[3],
                "rn_before": r[4],
                "rmssd_before": r[5],
                "rn_after_30s": r[6],
                "rmssd_after_30s": r[7],
            }
            for r in rows
        ],
    }


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