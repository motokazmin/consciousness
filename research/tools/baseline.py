"""Базовые величины испытуемого по накопленным сессиям.

Считает то, что не зависит от разметки: собственный разброс, вегетативные
показатели по типам сессий, качество сигнала прибора. Результат — блок
markdown для research/subject.md.

Артефактная политика переиспользуется из hrv_core.preprocessing, чтобы цифры
здесь и в приложении считались одним критерием (Malik 20% + 300..2000 мс).

Запуск из корня репо:
    python -m research.tools.baseline [--until YYYY-MM-DD] [--db hrv_data.sqlite]
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import re
import sqlite3
from pathlib import Path

import numpy as np
from scipy.signal import detrend, welch

from hrv_core.preprocessing import artifact_mask, correct_rr_artifacts

FS = 4.0              # частота ресемплинга тахограммы, Гц
SEG_SEC = 240         # окно Welch: даёт разрешение ~0.004 Гц, хватает чтобы
                      # отличить дыхание 5/мин (0.083) от барорефлекса (0.1)
MIN_SESSION_SEC = 600
MIN_BEATS = 600
# Брак: интерполяция по индексу на таких сессиях дорисовывает несуществующие
# удары, поэтому их не чиним, а выбрасываем. 5% — обычный порог в HRV-работах.
MAX_ARTIFACT_PCT = 5.0
MIN_COVERAGE_PCT = 90.0
# Метки, при которых сессия не описывает обычное состояние испытуемого и в
# baseline не входит. Ставятся самим Романом в session_name (см. ADR-006).
PHARMA_MARKS = ("#травка",)
# Сессии, которые не описывают обычное состояние испытуемого, но метки не несут.
#   98 (28.06) и 105 (02.07) стоят внутри каннабисного окна 26.06-05.07, имеют
#     его сигнатуру (|dRR| 1.0 и 2.0 мс, ЧСС 122 и 93), а метка не проставлена.
#     Роман подтвердил, что вещество было эпизодом и вне окна не употреблялось,
#     — значит внутри окна оно было, и эти две сессии почти наверняка под ним
#     (ADR-011). В выборку "под веществом" не идут: метку ставил не он.
# 161 (26.07) стояла здесь же по той же сигнатуре, но она вне окна и потому
#   веществом не объясняется. Возвращена в baseline (ADR-011).
# 92 и 93 стояли здесь, пока авторство их хэштегов было неизвестно: они дописаны
# в хвост текста, который писал Claude. Роман подтвердил, что дописывал он
# (P-009 сбылось), — метки возвращены как его разметка, ADR-009.
DOUBTFUL = (98, 105)
BAND = (0.04, 0.40)   # вся полоса, в которой ищем доминирующее колебание
HF = (0.15, 0.40)     # классическая дыхательная полоса: 9-24 дых/мин


def marks_of(session_name: str | None) -> list[str]:
    """Хэштеги, проставленные испытуемым в session_name: разметка, а не заголовок."""
    return re.findall(r"#[^\s#]+", session_name or "")


def load_sessions(conn: sqlite3.Connection, until: float | None) -> list[dict]:
    sql = ("select id, tag, started, ended, session_name from sessions "
           "where ended - started > ?")
    args: list = [MIN_SESSION_SEC]
    if until is not None:
        sql += " and started < ?"
        args.append(until)
    return [
        dict(id=r[0], tag=r[1] or "(без тега)", started=r[2], ended=r[3],
             marks=marks_of(r[4]))
        for r in conn.execute(sql + " order by started", args)
    ]


def session_metrics(conn: sqlite3.Connection, s: dict) -> dict | None:
    rows = conn.execute(
        "select ts, rr_ms from hrv_points where session_id = ? order by ts", (s["id"],)
    ).fetchall()
    if len(rows) < MIN_BEATS:
        return None
    ts = np.array([r[0] for r in rows], dtype=float)
    raw = np.array([r[1] for r in rows], dtype=float)

    valid = artifact_mask(raw)
    rr, _, _ = correct_rr_artifacts(raw)

    duration = s["ended"] - s["started"]
    m = dict(
        id=s["id"],
        tag=s["tag"],
        marks=s["marks"],
        started=s["started"],
        minutes=duration / 60.0,
        beats=len(rr),
        artifact_pct=100.0 * (~valid).sum() / len(raw),
        coverage_pct=100.0 * rr.sum() / 1000.0 / duration,
        hr=60000.0 / float(np.mean(rr)),
        rmssd=float(np.sqrt(np.mean(np.diff(rr) ** 2))),
        sdnn=float(np.std(rr)),
        # Медиана |ΔRR| от удара к удару. Отличает живой ритм (единицы-десятки мс)
        # от почти постоянного (≈1 мс — один квант Polar, 1/1024 с). Артефактный
        # процент такой ряд не ловит: по Malik он безупречно чистый.
        drr_median=float(np.median(np.abs(np.diff(rr)))),
    )

    beat_t = np.cumsum(rr) / 1000.0
    grid = np.arange(0.0, beat_t[-1], 1.0 / FS)
    if grid.size < FS * SEG_SEC:
        return m
    sig = detrend(np.interp(grid, beat_t, rr))
    f, p = welch(sig, fs=FS, nperseg=int(FS * SEG_SEC))
    band = (f >= BAND[0]) & (f < BAND[1])
    total = p[band].sum()
    if total <= 0:
        return m
    m["hf_pct"] = 100.0 * p[(f >= HF[0]) & (f < HF[1])].sum() / total
    m["peak_per_min"] = float(f[band][np.argmax(p[band])] * 60.0)
    return m


def median(items: list[dict], key: str) -> float | None:
    vals = [x[key] for x in items if key in x]
    return float(np.median(vals)) if vals else None


def fmt(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def trend_per_month(items: list[dict], key: str) -> tuple[float, float] | None:
    pts = [(x["started"], x[key]) for x in items if key in x]
    if len(pts) < 8:
        return None
    d = np.array([p[0] for p in pts]) / 86400.0
    v = np.array([p[1] for p in pts])
    slope = float(np.polyfit(d - d.min(), v, 1)[0]) * 30.0
    r = float(np.corrcoef(d, v)[0, 1])
    return slope, r


def report(items: list[dict]) -> str:
    out: list[str] = []
    days = sorted(x["started"] for x in items)
    d0 = dt.datetime.fromtimestamp(days[0]).date()
    d1 = dt.datetime.fromtimestamp(days[-1]).date()
    out.append(f"Период: {d0} — {d1}. Сессий в расчёте: {len(items)} "
               f"(длиннее {MIN_SESSION_SEC // 60} мин и от {MIN_BEATS} ударов).")
    out.append("")

    out.append("**Разброс RMSSD — собственный шум испытуемого.** Любое изменение "
               "меньше этого диапазона неинтерпретируемо.")
    out.append("")
    rm = np.array([x["rmssd"] for x in items])
    hr = np.array([x["hr"] for x in items])
    out.append("| величина | мин | 10% | медиана | 90% | макс |")
    out.append("|---|---|---|---|---|---|")
    for name, arr, d in (("RMSSD, мс", rm, 1), ("ЧСС, уд/мин", hr, 0)):
        q = [arr.min(), np.percentile(arr, 10), np.median(arr), np.percentile(arr, 90), arr.max()]
        out.append(f"| {name} | " + " | ".join(f"{x:.{d}f}" for x in q) + " |")
    out.append("")

    out.append("**По типам сессий** (медианы). Теги ненадёжны: `relaxation` — значение "
               "по умолчанию в форме, режим дыхания и внимания нигде не помечен.")
    out.append("")
    out.append("| тег | n | ЧСС | RMSSD | SDNN | HF% | пик, цикл/мин |")
    out.append("|---|---|---|---|---|---|---|")
    by = collections.defaultdict(list)
    for x in items:
        by[x["tag"]].append(x)
    for tag, g in sorted(by.items(), key=lambda kv: -len(kv[1])):
        out.append(
            f"| {tag} | {len(g)} | {fmt(median(g,'hr'),0)} | {fmt(median(g,'rmssd'))} | "
            f"{fmt(median(g,'sdnn'),0)} | {fmt(median(g,'hf_pct'))} | {fmt(median(g,'peak_per_min'))} |"
        )
    out.append("")

    peaks = [x["peak_per_min"] for x in items if "peak_per_min" in x]
    hfs = [x["hf_pct"] for x in items if "hf_pct" in x]
    if peaks:
        slow = sum(1 for p in peaks if 4 <= p <= 7)
        fast = sum(1 for p in peaks if p > 9)
        out.append(f"**Доминирующее колебание:** медиана {np.median(peaks):.1f} цикл/мин; "
                   f"в полосе 4–7/мин — {slow} из {len(peaks)} сессий; выше 9/мин — {fast}.")
        out.append(f"**Доля HF (9–24 дых/мин) от полосы 0.04–0.40 Гц:** медиана "
                   f"{np.median(hfs):.1f}%, максимум {max(hfs):.1f}%, сессий выше 25% — "
                   f"{sum(1 for h in hfs if h > 25)}.")
        out.append("")

    out.append("**Качество сигнала прибора** (Polar H10, Malik 20% к локальной медиане + 300–2000 мс):")
    art = np.array([x["artifact_pct"] for x in items])
    cov = np.array([x["coverage_pct"] for x in items])
    out.append(f"- артефакты: медиана {np.median(art):.2f}%, максимум {art.max():.2f}% "
               f"(порог бракования в HRV-работах обычно 5%);")
    out.append(f"- покрытие сессии интервалами: медиана {np.median(cov):.1f}%, "
               f"минимум {cov.min():.1f}%.")
    drr = np.array([x["drr_median"] for x in items])
    out.append(f"- живость ритма (медиана |ΔRR| от удара к удару): медиана "
               f"{np.median(drr):.1f} мс, минимум {drr.min():.1f} мс; сессий с "
               f"|ΔRR| ≤ 3 мс — {int((drr <= 3).sum())}.")
    out.append("")

    out.append("**Тренд внутри одного типа** (там, где тип постоянен и n достаточно):")
    for tag, g in sorted(by.items(), key=lambda kv: -len(kv[1]))[:2]:
        line = [f"- `{tag}` (n={len(g)}):"]
        for key, label in (("rmssd", "RMSSD"), ("hr", "ЧСС"), ("hf_pct", "HF%")):
            t = trend_per_month(g, key)
            line.append(f"{label} {t[0]:+.2f}/мес (r={t[1]:+.2f});" if t else f"{label} —;")
        out.append(" ".join(line))
    out.append("")

    out.append("**По месяцам, самый частый тип** — проверка, что тренд не мираж:")
    top_tag, top = max(by.items(), key=lambda kv: len(kv[1]))
    mb = collections.defaultdict(list)
    for x in top:
        mb[dt.datetime.fromtimestamp(x["started"]).strftime("%Y-%m")].append(x)
    out.append("")
    out.append(f"| месяц (`{top_tag}`) | n | ЧСС | RMSSD | HF% |")
    out.append("|---|---|---|---|---|")
    for mth in sorted(mb):
        g = mb[mth]
        out.append(f"| {mth} | {len(g)} | {fmt(median(g,'hr'),0)} | "
                   f"{fmt(median(g,'rmssd'))} | {fmt(median(g,'hf_pct'))} |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="hrv_data.sqlite", type=Path)
    ap.add_argument("--until", help="считать сессии строго раньше этой даты, YYYY-MM-DD")
    args = ap.parse_args()

    until = None
    if args.until:
        until = dt.datetime.strptime(args.until, "%Y-%m-%d").timestamp()

    conn = sqlite3.connect(args.db)
    measured = [m for s in load_sessions(conn, until) if (m := session_metrics(conn, s))]
    items, rejected, pharma = [], [], []
    doubtful = []
    for m in measured:
        if m["artifact_pct"] > MAX_ARTIFACT_PCT or m["coverage_pct"] < MIN_COVERAGE_PCT:
            rejected.append(m)
        elif m["id"] in DOUBTFUL:
            doubtful.append(m)
        elif any(mark in m["marks"] for mark in PHARMA_MARKS):
            pharma.append(m)
        else:
            items.append(m)
    if not items:
        raise SystemExit("нет сессий, подходящих под критерии")
    print(report(items))

    marked = collections.Counter(
        mk for m in measured if m["id"] not in DOUBTFUL for mk in m["marks"]
    )
    if marked:
        print()
        print("**Разметка, проставленная испытуемым в `session_name`** (сессий с меткой):")
        print(", ".join(f"`{mk}` — {n}" for mk, n in marked.most_common()))

    if pharma:
        print()
        print(f"**Вынесено из baseline по метке {'/'.join(PHARMA_MARKS)}: "
              f"{len(pharma)} сессий** (ADR-006). Считаются отдельно:")
        print()
        print("| величина | под веществом | baseline |")
        print("|---|---|---|")
        for key, label, d in (("rmssd", "RMSSD, мс", 1), ("hr", "ЧСС, уд/мин", 0),
                              ("hf_pct", "HF, %", 1), ("drr_median", "медиана |ΔRR|, мс", 1)):
            print(f"| {label} | {fmt(median(pharma, key), d)} | {fmt(median(items, key), d)} |")
        days = sorted({dt.datetime.fromtimestamp(m["started"]).date() for m in pharma})
        print()
        print(f"Окно: {days[0]} — {days[-1]}, дней {len(days)}.")
    if doubtful:
        print()
        print(f"**Исключено как недостоверное: {len(doubtful)} сессий** "
              f"(разметка сомнительна, см. DOUBTFUL): "
              + ", ".join(f"#{m['id']}" for m in doubtful))
    if rejected:
        print()
        print(f"**Забраковано по качеству сигнала: {len(rejected)} из {len(measured)}** "
              f"(артефакты > {MAX_ARTIFACT_PCT:.0f}% или покрытие < {MIN_COVERAGE_PCT:.0f}%):")
        for m in sorted(rejected, key=lambda x: -x["artifact_pct"]):
            day = dt.datetime.fromtimestamp(m["started"]).date()
            print(f"- #{m['id']} {day} `{m['tag']}`, {m['minutes']:.0f} мин: "
                  f"артефакты {m['artifact_pct']:.1f}%, покрытие {m['coverage_pct']:.0f}%")


if __name__ == "__main__":
    main()
