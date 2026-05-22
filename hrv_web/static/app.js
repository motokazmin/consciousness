/* global uPlot */

const $ = (id) => document.getElementById(id);

let rrPlot = null;
let rmPlot = null;
let rrBuf  = [];
let rmBuf  = [];
let ws     = null;
let raf    = null;
let currentSessionId = null;

let liveMode   = "window";
let sessionT0  = 0;
let durationSec = 0;
let driftCount = 0;
let lastRmssd  = null;
let sessionBaseline = null;

// ── AWARENESS MODE ────────────────────────────────────────────────────────
const BREATH_EXPAND_MS  = 4000;
const BREATH_HOLD_MS    = 1000;
const BREATH_CONTRACT_MS = 6000;
const BREATH_CYCLE_MS   = BREATH_EXPAND_MS + BREATH_HOLD_MS + BREATH_CONTRACT_MS;
const BREATH_SCALE_MIN  = 0.45;
const BREATH_SCALE_MAX  = 1.0;

const awareness = {
  breathGuide: false,
  driftSound: false,
  ambientBg: false,
  sessionActive: false,
  breathStartMs: 0,
  breathRaf: null,
  audioCtx: null,
  lastDriftCueAt: 0,
};

function awarenessOptions() {
  const g = $("opt_breath_guide");
  const s = $("opt_drift_sound");
  const a = $("opt_ambient_bg");
  return {
    breathGuide: g ? g.checked : false,
    driftSound:  s ? s.checked : false,
    ambientBg:   a ? a.checked : false,
  };
}

async function ensureAudioContext() {
  if (!awareness.audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    awareness.audioCtx = new Ctx();
  }
  if (awareness.audioCtx.state === "suspended") {
    await awareness.audioCtx.resume();
  }
  return awareness.audioCtx;
}

async function playDriftTone() {
  if (!awareness.driftSound || !awareness.sessionActive) return;
  try {
    const ctx = await ensureAudioContext();
    if (!ctx) return;

    const t0 = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(440, t0);
    osc.frequency.exponentialRampToValueAtTime(349, t0 + 0.5);

    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(0.14, t0 + 0.04);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.65);

    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(t0);
    osc.stop(t0 + 0.7);
  } catch (e) {
    console.warn("drift tone:", e);
  }
}

function breathPhase(elapsedMs) {
  const t = elapsedMs % BREATH_CYCLE_MS;
  if (t < BREATH_EXPAND_MS) {
    const p = t / BREATH_EXPAND_MS;
    return { label: "Вдох", scale: BREATH_SCALE_MIN + (BREATH_SCALE_MAX - BREATH_SCALE_MIN) * p };
  }
  if (t < BREATH_EXPAND_MS + BREATH_HOLD_MS) {
    return { label: "Пауза", scale: BREATH_SCALE_MAX };
  }
  const p = (t - BREATH_EXPAND_MS - BREATH_HOLD_MS) / BREATH_CONTRACT_MS;
  return { label: "Выдох", scale: BREATH_SCALE_MAX - (BREATH_SCALE_MAX - BREATH_SCALE_MIN) * p };
}

function setBreathScale(scale) {
  const ring = $("breath_ring");
  const glow = $("breath_glow");
  const tf = `scale(${scale})`;
  if (ring) ring.style.transform = tf;
  if (glow) glow.style.transform = tf;
}

function updateBreathGuide() {
  if (!awareness.breathGuide || !awareness.sessionActive) {
    awareness.breathRaf = null;
    return;
  }
  const elapsed = performance.now() - awareness.breathStartMs;
  const { label, scale } = breathPhase(elapsed);
  setBreathScale(scale);
  const lbl = $("breath_label");
  if (lbl) lbl.textContent = label;
  awareness.breathRaf = requestAnimationFrame(updateBreathGuide);
}

function startBreathGuide() {
  stopBreathGuide();
  if (!awareness.breathGuide) {
    setBreathGuideVisible(false);
    return;
  }
  setBreathGuideVisible(true);
  awareness.breathStartMs = performance.now();
  awareness.breathRaf = requestAnimationFrame(updateBreathGuide);
}

function stopBreathGuide() {
  if (awareness.breathRaf) cancelAnimationFrame(awareness.breathRaf);
  awareness.breathRaf = null;
  setBreathScale(BREATH_SCALE_MIN);
}

function setBreathGuideVisible(on) {
  const stage = $("breath_stage");
  const idle = $("breath_idle_msg");
  const visual = $("breath_visual");
  const label = $("breath_label");
  const hint = $("breath_hint");
  if (idle) idle.hidden = on;
  if (visual) visual.hidden = !on;
  if (label) label.hidden = !on;
  if (hint) hint.hidden = !on;
  if (stage) stage.classList.toggle("idle", !on);
}

function updateAmbientBg() {
  const bg = $("ambient-bg");
  if (!awareness.ambientBg || !awareness.sessionActive) {
    document.body.classList.remove("awareness-ambient");
    if (bg) bg.style.background = "var(--bg)";
    return;
  }
  document.body.classList.add("awareness-ambient");

  const refHigh = sessionBaseline != null && sessionBaseline > 10 ? sessionBaseline : 55;
  const refLow = Math.max(15, refHigh * 0.45);
  const rm = lastRmssd != null ? lastRmssd : refHigh * 0.7;
  const t = Math.max(0, Math.min(1, (rm - refLow) / (refHigh - refLow)));

  const r = Math.round(6 + t * 28);
  const g = Math.round(8 + t * 52);
  const b = Math.round(14 + t * 96);
  const gr = Math.round(30 + t * 80);
  const gg = Math.round(70 + t * 90);
  const gb = Math.round(130 + t * 80);
  const glowA = (0.12 + t * 0.38).toFixed(2);

  if (bg) {
    bg.style.background =
      `radial-gradient(ellipse 85% 65% at 50% 32%, rgba(${gr},${gg},${gb},${glowA}), transparent 72%), ` +
      `rgb(${r}, ${g}, ${b})`;
  }
}

function resetAmbientBg() {
  document.body.classList.remove("awareness-ambient");
  const bg = $("ambient-bg");
  if (bg) bg.style.background = "var(--bg)";
}

function syncAwarenessStats() {
  const rmEl = $("aware_rmssd");
  const baseEl = $("aware_base");
  const driftEl = $("aware_drift");
  if (rmEl) {
    rmEl.textContent = lastRmssd !== null ? lastRmssd.toFixed(1) : "—";
    if (lastRmssd !== null && sessionBaseline !== null && sessionBaseline > 1) {
      const ratio = lastRmssd / sessionBaseline;
      rmEl.className = "stat-value" + (ratio < 0.75 ? " bad" : ratio > 0.95 ? " good" : " warn");
    } else {
      rmEl.className = "stat-value";
    }
  }
  if (baseEl) baseEl.textContent = sessionBaseline !== null ? sessionBaseline.toFixed(1) : "—";
  if (driftEl) {
    driftEl.textContent = String(driftCount);
    driftEl.className = "stat-value" + (driftCount > 0 ? " bad" : "");
  }
}

function applyAwarenessOptions(opts) {
  awareness.breathGuide = opts.breathGuide;
  awareness.driftSound = opts.driftSound;
  awareness.ambientBg = opts.ambientBg;
}

function startAwarenessSession(opts) {
  applyAwarenessOptions(opts);
  awareness.sessionActive = true;
  updateAmbientBg();
  startBreathGuide();
}

function stopAwarenessSession() {
  awareness.sessionActive = false;
  stopBreathGuide();
  setBreathGuideVisible(false);
  resetAmbientBg();
  const badge = $("awareness_drift_badge");
  if (badge) badge.classList.remove("active");
}

function triggerDriftCue(fromServer) {
  const now = Date.now();
  if (now - awareness.lastDriftCueAt < 120000) return;
  awareness.lastDriftCueAt = now;
  if (fromServer) driftCount++;
  flashDriftBadge();
  playDriftTone();
  syncAwarenessStats();
}

function maybeClientDriftCue() {
  if (!awareness.driftSound || !awareness.sessionActive) return;
  if (lastRmssd === null || sessionBaseline === null || sessionBaseline <= 1) return;
  if (rmBuf.length < 30) return;
  if (lastRmssd >= sessionBaseline * 0.80) return;
  triggerDriftCue(false);
}

function flashDriftBadge() {
  for (const id of ["drift_badge", "awareness_drift_badge"]) {
    const el = $(id);
    if (!el) continue;
    el.classList.add("active");
    setTimeout(() => el.classList.remove("active"), 4000);
  }
}

// ── TABS ──────────────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll("nav button").forEach((x) => x.classList.remove("active"));
  document.querySelectorAll("section").forEach((s) => s.classList.remove("visible"));
  const btn = document.querySelector(`nav button[data-tab="${name}"]`);
  const sec = document.getElementById(`tab-${name}`);
  if (btn) btn.classList.add("active");
  if (sec) sec.classList.add("visible");
}

document.querySelectorAll("nav button").forEach((b) => {
  b.addEventListener("click", () => switchTab(b.dataset.tab));
});

// ── API ───────────────────────────────────────────────────────────────────
function api(path, opts = {}) {
  return fetch(path, {
    headers: { "Content-Type": "application/json", ...opts.headers },
    ...opts,
  }).then(async (r) => {
    const t = await r.text();
    let j;
    try { j = t ? JSON.parse(t) : {}; } catch { j = {}; }
    if (!r.ok) {
      const d = j.detail;
      const msg = Array.isArray(d)
        ? d.map(x => x.msg || JSON.stringify(x)).join("; ")
        : d || j.error || t || r.statusText || String(r.status);
      throw new Error(msg);
    }
    return j;
  });
}

// ── TAGS ──────────────────────────────────────────────────────────────────
async function loadTags() {
  const { tags } = await api("/api/tags");
  for (const sel of [$("tag"), $("flt_tag")]) {
    if (sel.id === "flt_tag") sel.innerHTML = '<option value="">— все —</option>';
    else sel.innerHTML = "";
    for (const t of tags) sel.appendChild(new Option(t, t));
  }
}

// ── PLOT HELPERS ──────────────────────────────────────────────────────────
const xScaleLinear = { time: false, distr: 1 };

function fmtAxisSec(u, splits) {
  return splits.map(v => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "";
    return Math.abs(n) >= 100 ? String(Math.round(n)) : n.toFixed(1);
  });
}

function plotWidth(el) {
  const p = el.parentElement;
  const w = el.clientWidth || (p ? p.clientWidth : 0) || window.innerWidth - 380;
  return Math.max(720, Math.floor(w - 8));
}

const LIVE_PLOT_H = 360;
const ARCHIVE_PLOT_H = 300;

const RR_COLOR   = "#00d4ff";
const RMSSD_COLOR = "#39e085";
const BASE_COLOR  = "rgba(255,255,255,0.12)";

function rrCfg(timed, w) {
  return {
    width: w, height: LIVE_PLOT_H,
    padding: [8, 8, 0, 0],
    scales: {
      x: { ...xScaleLinear, range: timed ? [0, durationSec] : [-60, 0] },
      y: { time: false, distr: 1, range: [350, 1300] },
    },
    series: [
      {},
      { stroke: RR_COLOR, width: 1.5, fill: "rgba(0,212,255,0.04)" },
    ],
    axes: [
      {
        stroke: "#3a4050", ticks: { stroke: "#3a4050" }, grid: { stroke: "#1e242d", width: 1 },
        label: timed ? "с от начала" : "с от сейчас",
        labelFont: "11px 'DM Sans'", font: "11px 'Space Mono'",
        stroke: "#5a6478",
        values: fmtAxisSec,
      },
      {
        stroke: "#3a4050", ticks: { stroke: "#3a4050" }, grid: { stroke: "#1e242d", width: 1 },
        label: "RR, ms",
        labelFont: "11px 'DM Sans'", font: "11px 'Space Mono'",
        stroke: "#5a6478",
        size: 52,
      },
    ],
    cursor: { show: true, x: true, y: false },
    legend: { show: false },
  };
}

function rmssdCfg(timed, w, yMax) {
  return {
    width: w, height: LIVE_PLOT_H,
    padding: [8, 8, 0, 0],
    scales: {
      x: { ...xScaleLinear, range: timed ? [0, durationSec] : [-300, 0] },
      y: { time: false, distr: 1, range: [0, yMax || 120] },
    },
    series: [
      {},
      {
        stroke: RMSSD_COLOR,
        width: 2,
        fill: "rgba(57,224,133,0.07)",
      },
      // baseline series
      {
        stroke: BASE_COLOR,
        width: 1,
        dash: [4, 4],
      },
    ],
    axes: [
      {
        stroke: "#3a4050", ticks: { stroke: "#3a4050" }, grid: { stroke: "#1e242d", width: 1 },
        label: timed ? "с от начала" : "с от сейчас",
        labelFont: "11px 'DM Sans'", font: "11px 'Space Mono'",
        stroke: "#5a6478",
        values: fmtAxisSec,
      },
      {
        stroke: "#3a4050", ticks: { stroke: "#3a4050" }, grid: { stroke: "#1e242d", width: 1 },
        label: "RMSSD, ms",
        labelFont: "11px 'DM Sans'", font: "11px 'Space Mono'",
        stroke: "#5a6478",
        size: 52,
      },
    ],
    cursor: { show: true, x: true, y: false },
    legend: { show: false },
  };
}

function makeRRPlot(el, timed) {
  if (rrPlot) { rrPlot.destroy(); rrPlot = null; }
  el.innerHTML = "";
  rrPlot = new uPlot(rrCfg(timed, plotWidth(el)), [[], []], el);
  return rrPlot;
}

function makeRMPlot(el, timed) {
  if (rmPlot) { rmPlot.destroy(); rmPlot = null; }
  el.innerHTML = "";
  rmPlot = new uPlot(rmssdCfg(timed, plotWidth(el)), [[], [], []], el);
  return rmPlot;
}

// ── RESIZE ────────────────────────────────────────────────────────────────
let _resizeT = null;
function resizePlots() {
  if (rrPlot && $("rrPlot")) rrPlot.setSize({ width: plotWidth($("rrPlot")), height: LIVE_PLOT_H });
  if (rmPlot && $("rmPlot")) rmPlot.setSize({ width: plotWidth($("rmPlot")), height: LIVE_PLOT_H });
  if (archRR  && $("arch_rr"))  archRR.setSize({ width: plotWidth($("arch_rr")),  height: ARCHIVE_PLOT_H });
  if (archRM  && $("arch_rm"))  archRM.setSize({ width: plotWidth($("arch_rm")),  height: ARCHIVE_PLOT_H });
}
window.addEventListener("resize", () => {
  clearTimeout(_resizeT);
  _resizeT = setTimeout(resizePlots, 120);
});

// ── STATS ─────────────────────────────────────────────────────────────────
function updateStats() {
  syncAwarenessStats();
  updateAmbientBg();
  if (lastRmssd === null) return;
  const rmssdEl = $("stat_rmssd");
  rmssdEl.textContent = lastRmssd.toFixed(1);
  // colour coding vs baseline
  if (sessionBaseline !== null && sessionBaseline > 1) {
    const ratio = lastRmssd / sessionBaseline;
    rmssdEl.className = "stat-value" + (ratio < 0.75 ? " bad" : ratio > 0.95 ? " good" : " warn");
    maybeClientDriftCue();
  }
  $("stat_base").textContent = sessionBaseline !== null ? sessionBaseline.toFixed(1) : "—";
  $("stat_drift").textContent = String(driftCount);
  if (driftCount > 0) $("stat_drift").className = "stat-value bad";
}

function updateHR(rrMs) {
  if (rrMs > 100) $("stat_hr").textContent = Math.round(60000 / rrMs);
}

// ── REDRAW LOOP ───────────────────────────────────────────────────────────
function trimBuf(buf, windowSec) {
  const cutoff = Date.now() / 1000 - windowSec;
  while (buf.length && buf[0][0] < cutoff) buf.shift();
}

function computeBaseline(buf, n) {
  const tail = buf.slice(-n).map(p => p[1]);
  if (!tail.length) return null;
  return tail.reduce((a, b) => a + b, 0) / tail.length;
}

function redrawLive() {
  if (liveMode === "timed") {
    const xsR = rrBuf.map(p => Math.max(0, p[0] - sessionT0));
    const ysR = rrBuf.map(p => p[1]);
    const xsM = rmBuf.map(p => Math.max(0, p[0] - sessionT0));
    const ysM = rmBuf.map(p => p[1]);

    if (rrPlot && xsR.length) {
      rrPlot.setData([xsR, ysR]);
      const mn = Math.min(...ysR), mx = Math.max(...ysR);
      rrPlot.setScale("y", { min: Math.max(300, mn - 40), max: mx + 40 });
    }
    if (rmPlot && xsM.length) {
      const bl = computeBaseline(rmBuf, 30);
      if (bl !== null) sessionBaseline = bl;
      const blXs = xsM.length ? [xsM[0], xsM[xsM.length - 1]] : [];
      const blYs = bl !== null ? [bl, bl] : [];
      rmPlot.setData([xsM, ysM, blXs.length ? blXs : [], blYs.length ? blYs : []]);
      const mx = Math.max(40, ...ysM) * 1.15;
      rmPlot.setScale("y", { min: 0, max: mx });
    }
  } else {
    const now = Date.now() / 1000;
    trimBuf(rrBuf, 65);
    trimBuf(rmBuf, 310);

    const xsR = rrBuf.map(p => p[0] - now);
    const ysR = rrBuf.map(p => p[1]);
    const xsM = rmBuf.map(p => p[0] - now);
    const ysM = rmBuf.map(p => p[1]);

    if (rrPlot && xsR.length) {
      rrPlot.setData([xsR, ysR]);
      const mx = Math.max(400, ...ysR, 900);
      rrPlot.setScale("y", { min: mx - 500, max: mx + 100 });
    }
    if (rmPlot && xsM.length) {
      const bl = computeBaseline(rmBuf, 30);
      if (bl !== null) sessionBaseline = bl;
      const blXs = xsM.length ? [xsM[0], xsM[xsM.length - 1]] : [];
      const blYs = bl !== null ? [bl, bl] : [];
      rmPlot.setData([xsM, ysM, blXs.length ? blXs : [], blYs.length ? blYs : []]);
      const mx = Math.max(40, ...ysM) * 1.2;
      rmPlot.setScale("y", { min: 0, max: mx });
    }
  }
  updateStats();
  raf = requestAnimationFrame(redrawLive);
}

function stopRaf() {
  if (raf) cancelAnimationFrame(raf);
  raf = null;
}

// ── WS ────────────────────────────────────────────────────────────────────
function onWsMessage(ev) {
  const msg = JSON.parse(ev.data);
  if (msg.type === "meta") {
    if (msg.started_at != null) sessionT0 = msg.started_at;
    return;
  }
  if (msg.type === "ended") {
    setStatus("Сессия завершена.");
    $("btn_stop").disabled = true;
    $("btn_start").disabled = false;
    setAwarenessControlsEnabled(true);
    if (ws) { ws.close(); ws = null; }
    stopRaf();
    stopAwarenessSession();
    return;
  }
  if (msg.type === "beat" && msg.t?.length) {
    for (let i = 0; i < msg.t.length; i++) {
      rrBuf.push([msg.t[i], msg.r[i]]);
      rmBuf.push([msg.t[i], msg.m[i]]);
      lastRmssd = msg.m[i];
      updateHR(msg.r[i]);
    }
    updateAmbientBg();
    syncAwarenessStats();
    if (msg.drift) triggerDriftCue(true);
  }
}

// ── START / STOP ──────────────────────────────────────────────────────────
function setStatus(txt) {
  const el = $("live_status");
  el.textContent = txt;
  el.classList.toggle("visible", !!txt);
}
function setErr(txt) {
  const el = $("live_err");
  el.textContent = txt;
  el.classList.toggle("visible", !!txt);
}

function setAwarenessControlsEnabled(on) {
  for (const id of ["opt_breath_guide", "opt_drift_sound", "opt_ambient_bg"]) {
    $(id).disabled = !on;
  }
}

async function startLive() {
  setErr("");
  const participant = $("participant").value.trim();
  if (!participant) { setErr("Укажите участника"); return; }

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
    const opts = awarenessOptions();
    if (opts.driftSound || opts.breathGuide) await ensureAudioContext();

    const res = await api("/api/sessions", { method: "POST", body: JSON.stringify(body) });
    currentSessionId = res.id;
    const timed = body.minutes != null && body.minutes > 0;
    liveMode    = timed ? "timed" : "window";
    sessionT0   = typeof res.started_at === "number" ? res.started_at : Date.now() / 1000;
    durationSec = timed ? body.minutes * 60 : 0;
    driftCount  = 0;
    awareness.lastDriftCueAt = 0;
    lastRmssd   = null;
    sessionBaseline = null;

    $("stat_rmssd").textContent = "—";
    $("stat_rmssd").className   = "stat-value";
    $("stat_base").textContent  = "—";
    $("stat_hr").textContent    = "—";
    $("stat_drift").textContent = "0";
    $("stat_drift").className   = "stat-value";
    syncAwarenessStats();

    $("stats_strip").classList.add("visible");
    $("live_plots").classList.add("visible");

    if (timed) {
      $("rr_plot_title").textContent = `RR — 0 … ${Math.round(durationSec)} с от старта`;
      $("rm_plot_title").textContent = `RMSSD — 0 … ${Math.round(durationSec)} с от старта`;
    } else {
      $("rr_plot_title").textContent = "RR — последние 60 с";
      $("rm_plot_title").textContent = "RMSSD — последние 5 мин";
    }

    rrBuf = []; rmBuf = [];
    makeRRPlot($("rrPlot"), timed);
    makeRMPlot($("rmPlot"), timed);
    requestAnimationFrame(resizePlots);

    setStatus(`Сессия #${currentSessionId} · ${body.tag}${timed ? ` · ${body.minutes} мин` : " · скользящее окно"}`);
    $("btn_start").disabled = true;
    $("btn_stop").disabled  = false;
    setAwarenessControlsEnabled(false);

    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/sessions/${currentSessionId}/stream`);
    ws.onmessage = onWsMessage;
    ws.onerror   = () => setErr("WebSocket ошибка");

    stopRaf();
    raf = requestAnimationFrame(redrawLive);

    startAwarenessSession(opts);
    if (opts.breathGuide) switchTab("awareness");
  } catch (e) {
    setErr(String(e.message || e));
  }
}

async function stopLive() {
  setErr("");
  if (!currentSessionId) return;
  try {
    await api(`/api/sessions/${currentSessionId}/stop`, { method: "POST" });
    setStatus("Сессия остановлена.");
  } catch (e) {
    setErr(String(e.message || e));
  }
  $("btn_stop").disabled  = true;
  $("btn_start").disabled = false;
  setAwarenessControlsEnabled(true);
  if (ws) { ws.close(); ws = null; }
  stopRaf();
  stopAwarenessSession();
}

$("btn_start").addEventListener("click", startLive);
$("btn_stop").addEventListener("click",  stopLive);

// ── ARCHIVE ───────────────────────────────────────────────────────────────
function escapeHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function fmtTime(ts) {
  if (ts == null) return "";
  return new Date(ts * 1000).toLocaleString("ru-RU");
}
function tagPill(t) {
  return `<span class="tag-pill ${escapeHtml(t)}">${escapeHtml(t)}</span>`;
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
    const dur = s.ended ? Math.round((s.ended - s.started) / 60) : null;
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td style="color:var(--text-dim);font-family:var(--mono);font-size:.8rem">${s.id}</td>` +
      `<td>${escapeHtml(s.participant || "")}</td>` +
      `<td>${tagPill(s.tag)}</td>` +
      `<td style="color:var(--text-dim);font-size:.78rem">${escapeHtml(String(s.source).slice(0, 38))}</td>` +
      `<td style="font-size:.82rem">${fmtTime(s.started)}</td>` +
      `<td style="font-size:.82rem">${s.ended ? fmtTime(s.ended) + (dur ? ` <span style="color:var(--text-muted)">(${dur} мин)</span>` : "") : "<span style='color:var(--text-muted)'>…</span>"}</td>` +
      `<td style="font-family:var(--mono);font-size:.82rem;color:${s.drift_events > 0 ? "var(--yellow)" : "var(--text-muted)"}">${s.drift_events ?? 0}</td>`;
    tr.addEventListener("click", () => openArchiveSession(s.id));
    tb.appendChild(tr);
  }
}

let archRR = null;
let archRM = null;

function renderSummaryGrid(sum) {
  const grid = $("arch_summary_grid");
  grid.innerHTML = "";
  const fields = [
    ["RMSSD mean",  sum.rmssd_mean != null ? sum.rmssd_mean.toFixed(1) + " ms" : "—"],
    ["RMSSD min",   sum.rmssd_min  != null ? sum.rmssd_min.toFixed(1)  + " ms" : "—"],
    ["RMSSD max",   sum.rmssd_max  != null ? sum.rmssd_max.toFixed(1)  + " ms" : "—"],
    ["RR mean",     sum.rr_mean    != null ? sum.rr_mean.toFixed(0)    + " ms" : "—"],
    ["Длительность", sum.duration_min != null ? sum.duration_min.toFixed(1) + " мин" : "—"],
    ["Drift",       sum.drift_events != null ? String(sum.drift_events) : "—"],
    ["Baseline",    sum.baseline_at_start != null ? sum.baseline_at_start.toFixed(1) + " ms" : "нет"],
  ];
  for (const [label, value] of fields) {
    const cell = document.createElement("div");
    cell.className = "summary-cell";
    cell.innerHTML = `<div class="s-label">${label}</div><div class="s-value">${value}</div>`;
    grid.appendChild(cell);
  }
}

async function openArchiveSession(id) {
  const detail = $("arch_detail");
  detail.classList.add("visible");
  $("arch_id").textContent = String(id);

  try {
    const sum = await api(`/api/sessions/${id}`);
    renderSummaryGrid(sum);
  } catch {
    $("arch_summary_grid").innerHTML = "<p style='color:var(--text-dim);font-size:.8rem'>Сводка недоступна (сессия ещё идёт?)</p>";
  }

  const { points } = await api(`/api/sessions/${id}/points?max_points=12000`);
  if (!points.length) return;

  const t0   = points[0].ts;
  const xs   = points.map(p => p.ts - t0);
  const rr   = points.map(p => p.rr_ms);
  const rm   = points.map(p => p.rmssd);
  const xMax = Math.max(...xs, 1);

  // baseline for archive RMSSD
  const blVal = rm.reduce((a, b) => a + b, 0) / rm.length;
  const blXs  = [0, xMax];
  const blYs  = [blVal, blVal];

  $("arch_rr").innerHTML = "";
  $("arch_rm").innerHTML = "";

  if (archRR) { archRR.destroy(); archRR = null; }
  if (archRM) { archRM.destroy(); archRM = null; }

  const wR = plotWidth($("arch_rr"));
  archRR = new uPlot(
    {
      ...rrCfg(true, wR),
      height: ARCHIVE_PLOT_H,
      scales: { x: { ...xScaleLinear, range: [0, xMax] }, y: { time:false, distr:1, range:[350,1300] } },
    },
    [xs, rr],
    $("arch_rr")
  );

  const rmax = Math.max(50, ...rm) * 1.15;
  const wM   = plotWidth($("arch_rm"));
  archRM = new uPlot(
    {
      ...rmssdCfg(true, wM, rmax),
      height: ARCHIVE_PLOT_H,
      scales: { x: { ...xScaleLinear, range: [0, xMax] }, y: { time:false, distr:1, range:[0, rmax] } },
    },
    [xs, rm, blXs, blYs],
    $("arch_rm")
  );

  requestAnimationFrame(resizePlots);
  detail.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("btn_reload").addEventListener("click", loadArchive);

loadTags().catch(e => setErr(String(e)));