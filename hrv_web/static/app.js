/* global uPlot */

const $ = (id) => document.getElementById(id);

let rrPlot = null;
let rmPlot = null;
let rrBuf = [];
let rmBuf = [];
let ws = null;
let raf = null;
let currentSessionId = null;

/** "window" — скользящее окно; "timed" — полная ось 0…T от начала сессии */
let liveMode = "window";
let sessionT0 = 0;
let durationSec = 0;

function api(path, opts = {}) {
  return fetch(path, {
    headers: { "Content-Type": "application/json", ...opts.headers },
    ...opts,
  }).then(async (r) => {
    const t = await r.text();
    let j;
    try {
      j = t ? JSON.parse(t) : {};
    } catch {
      j = {};
    }
    if (!r.ok) {
      const d = j.detail;
      let msg;
      if (Array.isArray(d)) msg = d.map((x) => x.msg || JSON.stringify(x)).join("; ");
      else msg = d || j.error || t || r.statusText || String(r.status);
      throw new Error(msg);
    }
    return j;
  });
}

async function loadTags() {
  const { tags } = await api("/api/tags");
  const sel = $("tag");
  const flt = $("flt_tag");
  sel.innerHTML = "";
  flt.innerHTML = '<option value="">—</option>';
  for (const t of tags) {
    sel.appendChild(new Option(t, t));
    flt.appendChild(new Option(t, t));
  }
}

function tab(name) {
  document.querySelectorAll("nav button").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === name);
  });
  document.querySelectorAll("section").forEach((s) => {
    s.classList.toggle("visible", s.id === `tab-${name}`);
  });
}

document.querySelectorAll("nav button").forEach((b) => {
  b.addEventListener("click", () => tab(b.dataset.tab));
});

/** Ширина графика по контейнеру (без искусственного потолка 900px). */
function plotWidth(el) {
  const p = el.parentElement;
  const cw = p ? p.clientWidth : 800;
  return Math.max(320, Math.floor(cw - 8));
}

let _resizeT = null;
function resizePlotsToContainer() {
  const h = 220;
  if (rrPlot && $("rrPlot")) rrPlot.setSize({ width: plotWidth($("rrPlot")), height: h });
  if (rmPlot && $("rmPlot")) rmPlot.setSize({ width: plotWidth($("rmPlot")), height: h });
  if (archRR && $("arch_rr")) archRR.setSize({ width: plotWidth($("arch_rr")), height: h });
  if (archRM && $("arch_rm")) archRM.setSize({ width: plotWidth($("arch_rm")), height: h });
}

window.addEventListener("resize", () => {
  if (_resizeT) clearTimeout(_resizeT);
  _resizeT = setTimeout(() => {
    _resizeT = null;
    resizePlotsToContainer();
  }, 120);
});

/** uPlot иначе может трактовать X как время и писать «3:00 am» — у нас всегда секунды (число). */
const xScaleLinear = { time: false, distr: 1 };

function fmtAxisSec(u, splits) {
  return splits.map((v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "";
    return Math.abs(n) >= 100 ? String(Math.round(n)) : n.toFixed(1);
  });
}

function makeRRPlot(el, timed) {
  const w = plotWidth(el);
  if (timed) {
    return new uPlot(
      {
        width: w,
        height: 220,
        title: `RR — сек от начала сессии (0…${Math.round(durationSec)} с)`,
        scales: {
          x: { ...xScaleLinear, range: [0, durationSec] },
          y: { time: false, distr: 1, range: [350, 1300] },
        },
        series: [{}, { stroke: "rgb(79,195,247)", width: 1 }],
        axes: [
          { stroke: "#888", label: "с от начала, с", values: fmtAxisSec },
          { stroke: "#888", label: "RR, ms" },
        ],
      },
      [[], []],
      el
    );
  }
  return new uPlot(
    {
      width: w,
      height: 220,
      title: "RR — последние 60 с (сек от «сейчас»)",
      scales: {
        x: { ...xScaleLinear, range: [-60, 0] },
        y: { time: false, distr: 1, range: [350, 1300] },
      },
      series: [{}, { stroke: "rgb(79,195,247)", width: 1 }],
      axes: [
        { stroke: "#888", label: "с от «сейчас», с", values: fmtAxisSec },
        { stroke: "#888", label: "RR, ms" },
      ],
    },
    [[], []],
    el
  );
}

function makeRMPlot(el, timed) {
  const w = plotWidth(el);
  if (timed) {
    return new uPlot(
      {
        width: w,
        height: 220,
        title: `RMSSD — сек от начала сессии (0…${Math.round(durationSec)} с)`,
        scales: {
          x: { ...xScaleLinear, range: [0, durationSec] },
          y: { time: false, distr: 1, range: [0, 120] },
        },
        series: [{}, { stroke: "rgb(129,199,132)", width: 1 }],
        axes: [
          { stroke: "#888", label: "с от начала, с", values: fmtAxisSec },
          { stroke: "#888", label: "RMSSD, ms" },
        ],
      },
      [[], []],
      el
    );
  }
  return new uPlot(
    {
      width: w,
      height: 220,
      title: "RMSSD — последние ~5 мин (сек от «сейчас»)",
      scales: {
        x: { ...xScaleLinear, range: [-300, 0] },
        y: { time: false, distr: 1, range: [0, 120] },
      },
      series: [{}, { stroke: "rgb(129,199,132)", width: 1 }],
      axes: [
        { stroke: "#888", label: "с от «сейчас», с", values: fmtAxisSec },
        { stroke: "#888", label: "RMSSD, ms" },
      ],
    },
    [[], []],
    el
  );
}

function trimBuf(buf, windowSec) {
  const now = Date.now() / 1000;
  while (buf.length && buf[0][0] < now - windowSec) buf.shift();
}

function redrawLive() {
  if (liveMode === "timed") {
    const xsR = rrBuf.map((p) => Math.max(0, p[0] - sessionT0));
    const ysR = rrBuf.map((p) => p[1]);
    const xsM = rmBuf.map((p) => Math.max(0, p[0] - sessionT0));
    const ysM = rmBuf.map((p) => p[1]);
    if (rrPlot && xsR.length) {
      rrPlot.setData([xsR, ysR]);
      rrPlot.setScale("x", { min: 0, max: durationSec });
      const mn = Math.min(...ysR);
      const mx = Math.max(...ysR);
      rrPlot.setScale("y", { min: Math.max(300, mn - 40), max: mx + 40 });
    }
    if (rmPlot && xsM.length) {
      rmPlot.setData([xsM, ysM]);
      rmPlot.setScale("x", { min: 0, max: durationSec });
      const mx = Math.max(40, ...ysM) * 1.15;
      rmPlot.setScale("y", { min: 0, max: mx });
    }
  } else {
    const now = Date.now() / 1000;
    trimBuf(rrBuf, 65);
    trimBuf(rmBuf, 310);
    const xsR = rrBuf.map((p) => p[0] - now);
    const ysR = rrBuf.map((p) => p[1]);
    const xsM = rmBuf.map((p) => p[0] - now);
    const ysM = rmBuf.map((p) => p[1]);
    if (rrPlot && xsR.length) {
      rrPlot.setData([xsR, ysR]);
      const mx = Math.max(400, ...ysR, 900);
      rrPlot.setScale("y", { min: mx - 500, max: mx + 100 });
    }
    if (rmPlot && xsM.length) {
      rmPlot.setData([xsM, ysM]);
      const mx = Math.max(40, ...ysM) * 1.2;
      rmPlot.setScale("y", { min: 0, max: mx });
    }
  }
  raf = requestAnimationFrame(redrawLive);
}

function stopRaf() {
  if (raf) cancelAnimationFrame(raf);
  raf = null;
}

function onWsMessage(ev) {
  const msg = JSON.parse(ev.data);
  if (msg.type === "meta") {
    if (msg.started_at != null) sessionT0 = msg.started_at;
    return;
  }
  if (msg.type === "ended") {
    $("live_status").textContent += " Сессия завершена.";
    $("btn_stop").disabled = true;
    $("btn_start").disabled = false;
    if (ws) ws.close();
    ws = null;
    stopRaf();
    return;
  }
  if (msg.type === "beat" && msg.t && msg.t.length) {
    for (let i = 0; i < msg.t.length; i++) {
      rrBuf.push([msg.t[i], msg.r[i]]);
      rmBuf.push([msg.t[i], msg.m[i]]);
    }
  }
}

async function startLive() {
  $("live_err").textContent = "";
  const participant = $("participant").value.trim();
  if (!participant) {
    $("live_err").textContent = "Укажите участника";
    return;
  }
  const rawMin = $("minutes").value.trim();
  const minutes = rawMin ? parseFloat(rawMin.replace(",", ".")) : null;
  const body = {
    participant,
    tag: $("tag").value,
    session_name: $("session_name").value.trim() || null,
    source: $("source").value,
    address: $("address").value.trim() || null,
    minutes: minutes != null && !Number.isNaN(minutes) && minutes > 0 ? minutes : null,
    desktop_notify: $("desktop_notify").checked,
  };
  try {
    const res = await api("/api/sessions", { method: "POST", body: JSON.stringify(body) });
    currentSessionId = res.id;
    const timed = body.minutes != null && body.minutes > 0;
    liveMode = timed ? "timed" : "window";
    sessionT0 = typeof res.started_at === "number" ? res.started_at : Date.now() / 1000;
    durationSec = timed ? body.minutes * 60 : 0;

    $("live_plots").style.display = "block";
    $("live_status").textContent = timed
      ? `Сессия #${currentSessionId} — ось времени 0…${Math.round(durationSec)} с от старта (полные кривые).`
      : `Сессия #${currentSessionId} — скользящее окно (укажите длительность для оси от начала).`;
    $("btn_start").disabled = true;
    $("btn_stop").disabled = false;
    rrBuf = [];
    rmBuf = [];
    $("rrPlot").innerHTML = "";
    $("rmPlot").innerHTML = "";
    rrPlot = makeRRPlot($("rrPlot"), timed);
    rmPlot = makeRMPlot($("rmPlot"), timed);
    requestAnimationFrame(() => resizePlotsToContainer());
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/sessions/${currentSessionId}/stream`);
    ws.onmessage = onWsMessage;
    ws.onerror = () => {
      $("live_err").textContent = "WebSocket ошибка";
    };
    stopRaf();
    raf = requestAnimationFrame(redrawLive);
  } catch (e) {
    $("live_err").textContent = String(e.message || e);
  }
}

async function stopLive() {
  $("live_err").textContent = "";
  if (!currentSessionId) return;
  try {
    const s = await api(`/api/sessions/${currentSessionId}/stop`, { method: "POST" });
    $("live_status").textContent = JSON.stringify(s, null, 2);
  } catch (e) {
    $("live_err").textContent = String(e.message || e);
  }
  $("btn_stop").disabled = true;
  $("btn_start").disabled = false;
  if (ws) ws.close();
  ws = null;
  stopRaf();
}

async function loadArchive() {
  const p = $("flt_participant").value.trim();
  const t = $("flt_tag").value;
  let url = "/api/sessions?limit=100";
  if (p) url += `&participant=${encodeURIComponent(p)}`;
  if (t) url += `&tag=${encodeURIComponent(t)}`;
  const { sessions } = await api(url);
  const tb = $("arch_rows");
  tb.innerHTML = "";
  for (const s of sessions) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${s.id}</td><td>${escapeHtml(s.participant || "")}</td><td>${escapeHtml(s.tag)}</td><td>${escapeHtml(String(s.source).slice(0, 40))}</td><td>${fmtTime(s.started)}</td><td>${s.ended ? fmtTime(s.ended) : "…"}</td>`;
    tr.addEventListener("click", () => openArchiveSession(s.id));
    tb.appendChild(tr);
  }
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function fmtTime(ts) {
  if (ts == null) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

let archRR = null;
let archRM = null;

async function openArchiveSession(id) {
  $("arch_detail").style.display = "block";
  $("arch_id").textContent = id;
  const sum = await api(`/api/sessions/${id}`);
  $("arch_summary").textContent = JSON.stringify(sum, null, 2);
  const { points } = await api(`/api/sessions/${id}/points?max_points=12000`);
  if (!points.length) return;
  const t0 = points[0].ts;
  const xs = points.map((p) => p.ts - t0);
  const rr = points.map((p) => p.rr_ms);
  const rm = points.map((p) => p.rmssd);
  $("arch_rr").innerHTML = "";
  $("arch_rm").innerHTML = "";
  const xMax = Math.max(...xs, 1);
  const wR = plotWidth($("arch_rr"));
  archRR = new uPlot(
    {
      width: wR,
      height: 220,
      title: "RR (сек от начала сессии)",
      scales: {
        x: { ...xScaleLinear, range: [0, xMax] },
        y: { time: false, distr: 1, range: [350, 1300] },
      },
      series: [{}, { stroke: "rgb(79,195,247)", width: 1 }],
      axes: [
        { stroke: "#888", label: "с от начала, с", values: fmtAxisSec },
        { stroke: "#888", label: "RR, ms" },
      ],
    },
    [xs, rr],
    $("arch_rr")
  );
  const rmax = Math.max(50, ...rm) * 1.15;
  const wM = plotWidth($("arch_rm"));
  archRM = new uPlot(
    {
      width: wM,
      height: 220,
      title: "RMSSD (сек от начала сессии)",
      scales: {
        x: { ...xScaleLinear, range: [0, xMax] },
        y: { time: false, distr: 1, range: [0, rmax] },
      },
      series: [{}, { stroke: "rgb(129,199,132)", width: 1 }],
      axes: [
        { stroke: "#888", label: "с от начала, с", values: fmtAxisSec },
        { stroke: "#888", label: "RMSSD, ms" },
      ],
    },
    [xs, rm],
    $("arch_rm")
  );
  requestAnimationFrame(() => resizePlotsToContainer());
}

$("btn_start").addEventListener("click", startLive);
$("btn_stop").addEventListener("click", stopLive);
$("btn_reload").addEventListener("click", loadArchive);

loadTags().catch((e) => {
  $("live_err").textContent = String(e);
});
