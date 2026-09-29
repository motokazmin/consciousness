"""FastAPI: REST + WebSocket + статика."""

from __future__ import annotations

import asyncio
import datetime
import logging
import math
import queue
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import numpy as np

from hrv_core.analysis import progress_session_analysis, session_analysis, session_sd1
from hrv_core.breathing import analyze_breathing, decimate_for_transport
from hrv_core.constants import DB_PATH
from hrv_core.db import (
    delete_session,
    delete_session_explanation,
    delete_session_segments,
    ensure_session_audio_dir,
    finalize_orphaned_sessions,
    finalize_session,
    init_db,
    load_accel_samples,
    load_hour_baseline,
    load_session_explanation,
    load_session_segments,
    save_session_explanation,
    save_session_segments,
    session_audio_path,
    set_session_has_audio,
    wipe_all_history,
)
from hrv_core.summary import session_summary_dict
from hrv_core.note_tags import note_tag_sql_pattern, parse_note_tags
from hrv_core.tags import normalize_tag
from hrv_web.session_manager import MANAGER

STATIC_DIR = Path(__file__).resolve().parent / "static"
PHRASES_DIR = STATIC_DIR / "phrases"
PHRASE_SET_ID_RE = re.compile(r"^[\w\-]+$")
PHRASE_FILE_RE = re.compile(r"^(\w+)_(.+)_(\d+)\.mp3$")

log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    conn = init_db()
    try:
        finalized = finalize_orphaned_sessions(conn)
        if finalized:
            log.info("Завершены незакрытые сессии после перезапуска: %s", finalized)
    finally:
        conn.close()
    yield


app = FastAPI(title="HRV Monitor", lifespan=_lifespan)


class StartSessionBody(BaseModel):
    participant: str = Field(..., min_length=1, max_length=200)
    tag: str
    session_name: str | None = Field(None, max_length=12000)
    source: str = Field(..., description="mock | ble")
    address: str | None = None
    minutes: float | None = Field(None, gt=0)
    opt_guided_phrases: bool = False
    opt_audio_biofeedback: bool = False
    opt_mic_recording: bool = False


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
    session_name: str | None = Field(None, max_length=12000)


EXPLANATION_MAX_LEN = 20_000


class PutExplanationBody(BaseModel):
    body: str = Field(..., min_length=1, max_length=EXPLANATION_MAX_LEN)
    author: str = Field("claude", min_length=1, max_length=40)


class SegmentItem(BaseModel):
    """Отрезок разметки. kind — вид (для сна: wake/nrem/rem/unknown; для
    практики — свои), confidence — know/assume/guess (знаю/предполагаю/догадка)."""
    t0: float = Field(..., ge=0)
    t1: float = Field(..., ge=0)
    kind: str = Field(..., min_length=1, max_length=32)
    label: str = Field(..., min_length=1, max_length=120)
    confidence: Literal["know", "assume", "guess"]
    basis: str = Field("", max_length=2000)


class SegmentEvent(BaseModel):
    """Точечное событие на ленте: поворот, сбой датчика и т.п."""
    t: float = Field(..., ge=0)
    kind: str = Field(..., min_length=1, max_length=32)
    label: str = Field(..., min_length=1, max_length=200)


class PutSegmentsBody(BaseModel):
    segments: list[SegmentItem] = Field(default_factory=list, max_length=500)
    events: list[SegmentEvent] = Field(default_factory=list, max_length=1000)
    author: str = Field("claude", min_length=1, max_length=40)


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
            opt_mic_recording=body.opt_mic_recording,
        )
    except RuntimeError as e:
        if "already_running" in str(e):
            raise HTTPException(409, "Уже идёт активная сессия записи. Остановите её сначала.") from e
        raise HTTPException(400, str(e)) from e
    return {
        "id": rs.session_id,
        "started": True,
        "started_at": rs.started_at,
        "first_beat_at": rs.first_beat_at,
        "duration_minutes": rs.duration_minutes,
        "tag": tag,
        "device_state": rs.device_state,
    }


@app.get("/api/sessions/recording")
def recording_status():
    active = MANAGER.get_active()
    if active is None:
        return {"recording": False}
    return {
        "recording": True,
        "session_id": active.session_id,
        "started_at": active.started_at,
        "first_beat_at": active.first_beat_at,
        "device_state": active.device_state,
        "accel_missing": active.accel_missing,
        "last_accel_at": active.last_accel_at,
    }


@app.post("/api/sessions/{session_id}/stop")
def stop_session(session_id: int):
    summary = MANAGER.stop(session_id)
    if summary is not None:
        return summary
    conn = init_db()
    try:
        row = conn.execute(
            "SELECT started, ended, drift_events FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Сессия не найдена")
        if row[1] is not None:
            raise HTTPException(404, "Сессия уже остановлена")
        if not finalize_session(conn, session_id):
            raise HTTPException(404, "Сессия не найдена или уже остановлена")
        hour = datetime.datetime.fromtimestamp(row[0]).hour
        baseline_at_start = load_hour_baseline(conn, hour)
        summary = session_summary_dict(
            conn, session_id, baseline_at_start, int(row[2] or 0)
        )
    finally:
        conn.close()
    if summary is None:
        raise HTTPException(404, "Сессия не найдена")
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
        "drift_events, opt_guided_phrases, opt_audio_biofeedback, "
        "opt_mic_recording, has_audio, "
        "EXISTS(SELECT 1 FROM session_explanations e WHERE e.session_id = sessions.id)"
        + filt
        + " ORDER BY id DESC LIMIT ?"
    )
    args.append(min(limit, 2000))
    rows = conn.execute(q, args).fetchall()
    sessions = []
    for r in rows:
        sd1 = None
        if r[6] is not None:
            rr_rows = conn.execute(
                "SELECT rr_ms FROM hrv_points WHERE session_id = ? ORDER BY ts",
                (r[0],),
            ).fetchall()
            if rr_rows:
                sd1 = session_sd1(np.array([row[0] for row in rr_rows], dtype=float))
        sessions.append(
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
                "opt_mic_recording": bool(r[10]),
                "has_audio": bool(r[11]),
                "has_explanation": bool(r[12]),
                "note_tags": parse_note_tags(r[2]),
                "sd1": sd1,
            }
        )
    conn.close()
    return {"sessions": sessions}


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
        # Децимация здесь безопасна на входе: точки идут прямо в JSON как
        # {x, rr} без каких-либо производных метрик (RMSSD/SD1 тут не
        # считаются — в отличие от /api/progress/analysis). Ничего не строится
        # на разности соседних ударов после прореживания, портить нечего.
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
    active = MANAGER.get_active()
    if active is not None:
        MANAGER.stop(active.session_id)
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
        MANAGER.stop(session_id)
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
        "opt_guided_phrases, opt_audio_biofeedback, opt_mic_recording, has_audio, "
        "audio_delay_sec, opt_acc_recording "
        "FROM sessions WHERE id = ?",
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
        opt_mic,
        has_audio,
        audio_delay_sec,
        opt_acc,
    ) = row
    if ended is None:
        conn.close()
        raise HTTPException(400, "Сессия ещё не завершена — сводка после stop")
    hour = datetime.datetime.fromtimestamp(started).hour
    baseline_at_start = load_hour_baseline(conn, hour)
    summary = session_summary_dict(conn, session_id, baseline_at_start, int(drift_n or 0))
    first_rr = conn.execute(
        "SELECT MIN(ts) FROM hrv_points WHERE session_id = ?", (session_id,)
    ).fetchone()
    explanation = load_session_explanation(conn, session_id)
    conn.close()
    if summary is not None:
        summary["opt_guided_phrases"] = bool(opt_guided)
        summary["opt_audio_biofeedback"] = bool(opt_audio)
        summary["opt_mic_recording"] = bool(opt_mic)
        summary["opt_acc_recording"] = bool(opt_acc)
        summary["has_audio"] = bool(has_audio)
        summary["note_tags"] = parse_note_tags(session_name)
        summary["explanation"] = explanation
        if first_rr and first_rr[0] is not None:
            summary["first_rr_ts"] = float(first_rr[0])
            summary["timeline_skew_sec"] = round(float(first_rr[0]) - float(started), 3)
        if has_audio and audio_delay_sec is not None:
            delay = float(audio_delay_sec)
            if 0 <= delay <= 2.0:
                summary["audio_offset_sec"] = delay
    return summary


def _require_session(conn, session_id: int) -> None:
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Сессия не найдена")


@app.get("/api/sessions/{session_id}/explanation")
def get_session_explanation(session_id: int):
    """Разбор сессии: качественное объяснение графиков, написанное Claude."""
    conn = init_db()
    try:
        _require_session(conn, session_id)
        return {"explanation": load_session_explanation(conn, session_id)}
    finally:
        conn.close()


@app.put("/api/sessions/{session_id}/explanation")
async def put_session_explanation(session_id: int, request: Request):
    """Записать разбор. Тело — JSON {body, author} или сырой markdown-текст.

    Сырой текст нужен, чтобы разбор можно было положить одной командой
    (`curl --data-binary @file`), не экранируя markdown в JSON.
    """
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip()
    author = "claude"
    if ctype == "application/json":
        try:
            payload = await request.json()
        except Exception as e:
            raise HTTPException(400, "Тело не разобралось как JSON") from e
        if not isinstance(payload, dict):
            raise HTTPException(400, "Ожидался объект {body, author}")
        parsed = PutExplanationBody(**payload)
        text = parsed.body
        author = parsed.author
    else:
        raw = await request.body()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise HTTPException(400, "Текст должен быть в UTF-8") from e
        author = (request.query_params.get("author") or "claude").strip() or "claude"
    text = text.strip()
    if not text:
        raise HTTPException(400, "Пустой разбор")
    if len(text) > EXPLANATION_MAX_LEN:
        raise HTTPException(
            413, f"Разбор длиннее {EXPLANATION_MAX_LEN} символов"
        )
    conn = init_db()
    try:
        _require_session(conn, session_id)
        saved = save_session_explanation(conn, session_id, text, author[:40])
    finally:
        conn.close()
    return {"ok": True, "explanation": saved}


@app.delete("/api/sessions/{session_id}/explanation")
def delete_session_explanation_endpoint(session_id: int):
    conn = init_db()
    try:
        _require_session(conn, session_id)
        deleted = delete_session_explanation(conn, session_id)
    finally:
        conn.close()
    return {"ok": True, "deleted": deleted}


@app.get("/api/sessions/{session_id}/segments")
def get_session_segments(session_id: int):
    """Разметка отрезков сессии для ленты над графиками архива."""
    conn = init_db()
    try:
        _require_session(conn, session_id)
        return {"segments": load_session_segments(conn, session_id)}
    finally:
        conn.close()


@app.put("/api/sessions/{session_id}/segments")
def put_session_segments(session_id: int, body: PutSegmentsBody):
    """Записать разметку целиком (JSON {segments, events, author})."""
    for s in body.segments:
        if s.t1 <= s.t0:
            raise HTTPException(400, f"Отрезок «{s.label}»: t1 должен быть больше t0")
    conn = init_db()
    try:
        _require_session(conn, session_id)
        saved = save_session_segments(
            conn,
            session_id,
            {
                "segments": [s.model_dump() for s in sorted(body.segments, key=lambda s: s.t0)],
                "events": [e.model_dump() for e in sorted(body.events, key=lambda e: e.t)],
            },
            body.author,
        )
    finally:
        conn.close()
    return {"ok": True, "segments": saved}


@app.delete("/api/sessions/{session_id}/segments")
def delete_session_segments_endpoint(session_id: int):
    conn = init_db()
    try:
        _require_session(conn, session_id)
        deleted = delete_session_segments(conn, session_id)
    finally:
        conn.close()
    return {"ok": True, "deleted": deleted}


_AUDIO_MAX_BYTES = 500 * 1024 * 1024  # 500 MiB


@app.put("/api/sessions/{session_id}/audio")
async def put_session_audio(session_id: int, request: Request):
    """Сохранить запись микрофона (raw body, webm/ogg)."""
    body = await request.body()
    if not body:
        raise HTTPException(400, "Пустое тело запроса")
    if len(body) > _AUDIO_MAX_BYTES:
        raise HTTPException(413, "Файл аудио слишком большой")
    
    delay_str = request.headers.get("X-Audio-Delay-Sec")
    delay_sec = None
    if delay_str:
        try:
            delay_sec = float(delay_str)
        except (ValueError, TypeError):
            pass
    
    conn = init_db()
    try:
        row = conn.execute(
            "SELECT ended FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Сессия не найдена")
        if row[0] is None:
            raise HTTPException(400, "Аудио можно загрузить только после завершения сессии")
        ensure_session_audio_dir()
        path = session_audio_path(session_id)
        path.write_bytes(body)
        set_session_has_audio(conn, session_id, True, delay_sec)
    finally:
        conn.close()
    return {"ok": True, "has_audio": True, "bytes": len(body)}


@app.get("/api/sessions/{session_id}/audio")
def get_session_audio(session_id: int):
    conn = init_db()
    try:
        row = conn.execute(
            "SELECT has_audio FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Сессия не найдена")
        if not row[0]:
            raise HTTPException(404, "У сессии нет аудиозаписи")
    finally:
        conn.close()
    path = session_audio_path(session_id)
    if not path.is_file():
        raise HTTPException(404, "Файл аудио не найден")
    media = "audio/webm"
    if path.suffix.lower() == ".ogg":
        media = "audio/ogg"
    return FileResponse(path, media_type=media, filename=path.name)


@app.get("/api/sessions/{session_id}/analysis")
def session_analysis_endpoint(
    session_id: int,
    max_points: int = 12_000,
):
    """max_points — сколько точек отдать в тахограмме RR (raw_rr/analysis_rr) для
    отрисовки, НЕ по скольким считать. Расчёт (RMSSD/SD1/тренды/спектр) всегда
    идёт по полному ряду сессии: он строится на разностях соседних ударов, и
    прореживание ряда ДО расчёта превращает несоседние удары в соседние — метрики
    расходятся с реальными (на записи длиннее нескольких часов — почти вдвое:
    RMSSD/SD1 задирает пропущенное время между оставшимися точками в разность).
    Остальные графики режут свой выход сами (poincare/trend_max внутри
    session_analysis, min-бакеты в quality_strip)."""
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
    return session_analysis(rows, started, ended, raw_rr_max=max_points)


@app.get("/api/sessions/{session_id}/breathing")
def session_breathing_endpoint(
    session_id: int,
    max_points: int = 4000,
):
    """Дыхание из акселерометра PMD — только post-session (см. ARCHITECTURE.md:
    живого графика во время записи нет, решено отдельно). Ось времени та же,
    что у /analysis: `ts - sessions.started` (started уже приведён к моменту
    взведения — своей коррекции здесь не нужно, см. hrv_web/session_manager.py).

    Явно отличает «акселерометра в сессии нет» (все сессии до Части A) от
    «есть, но короткая/шумная» — фронт не должен рисовать это как нулевые
    графики (см. ARCHITECTURE.md).

    Потолок max_points — 200 000: лупа в архиве запрашивает волну в полном
    разрешении (4 Гц × 8 ч ≈ 115 000 точек), иначе на длинной записи между
    точками выходит больше периода дыхания и форма вдоха теряется."""
    max_points = max(100, min(max_points, 200_000))
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
        raise HTTPException(400, "Сессия ещё не завершена — дыхание после stop")
    samples = load_accel_samples(conn, session_id)
    conn.close()

    if not samples:
        return {
            "has_accel": False,
            "insufficient_data": True,
            "message": "В этой сессии нет данных акселерометра",
        }

    result = analyze_breathing(samples)
    if result is None:
        return {
            "has_accel": True,
            "insufficient_data": True,
            "message": "Данных акселерометра мало для оценки дыхания",
        }

    t = result["t"] - float(started)
    t_dec, (wave_dec, rate_dec) = decimate_for_transport(
        t, [result["wave"], result["rate_cpm"]], max_points
    )
    windows = [
        {
            "t_start": round(w["t_start"] - float(started), 2),
            "t_end": round(w["t_end"] - float(started), 2),
            "amp_mg": round(w["amp_mg"], 2),
            "rejected": bool(w["rejected"]),
        }
        for w in result["windows"]
    ]
    summary = result["summary"]
    return {
        "has_accel": True,
        "insufficient_data": False,
        "t": [round(float(x), 2) for x in t_dec],
        "wave_mg": [round(float(x), 2) for x in wave_dec],
        # Края ряда частоты приходят как NaN (переходный процесс фильтра, см.
        # hrv_core/breathing.py) — отдаём null: JSON не знает NaN, а uPlot
        # рисует null разрывом, что здесь и требуется.
        "rate_cpm": [
            None if not math.isfinite(float(x)) else round(float(x), 2)
            for x in rate_dec
        ],
        "windows": windows,
        "summary": {
            "cpm_median": round(summary["cpm_median"], 1) if summary["cpm_median"] is not None else None,
            "good_fraction": round(summary["good_fraction"], 3) if summary["good_fraction"] is not None else None,
            "amp_median_mg": round(summary["amp_median_mg"], 2) if summary["amp_median_mg"] is not None else None,
            "axis": summary["axis"],
        },
    }


@app.get("/api/progress/analysis")
def progress_analysis(
    tag: str | None = None,
    participant: str | None = None,
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
        # Полный ряд, без децимации на входе: SD1/coherence/sdnn_trend в
        # progress_session_analysis считаются на разностях соседних ударов —
        # тот же класс бага, что был в session_analysis_endpoint (см. commit
        # "тренд считается по исправленному ряду и по всем ударам"). Резать
        # нужно только то, что реально уходит в JSON — raw_rr (max_points_per_session).
        rows = conn.execute(
            "SELECT ts, rr_ms, rmssd FROM hrv_points WHERE session_id = ? ORDER BY ts",
            (sid,),
        ).fetchall()
        if not rows:
            continue
        stats = conn.execute(
            "SELECT AVG(rmssd) FROM hrv_points WHERE session_id = ?",
            (sid,),
        ).fetchone()
        rmssd_mean = float(stats[0]) if stats and stats[0] is not None else None
        analysis = progress_session_analysis(
            rows,
            started,
            ended,
            rmssd_mean,
            raw_rr_max=max_points_per_session,
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
    """Точки как лежат в БД (ts, rr_ms, rmssd — живой расчёт), без пересчёта
    чего-либо на клиенте. Децимация на входе тут безвредна по тому же
    рассуждению, что и в /api/progress: ничего не выводится из разности
    соседних ударов, отдаём просто ряд как есть."""
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
            "first_beat_at": rs.first_beat_at,
            "duration_minutes": rs.duration_minutes,
            "device_state": rs.device_state,
            "accel_missing": rs.accel_missing,
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