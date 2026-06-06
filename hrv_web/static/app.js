/* global uPlot */

const $ = (id) => document.getElementById(id);

let rrPlot = null;
let rmPlot = null;
let rrBuf  = [];
let rmBuf  = [];
let ws     = null;
let raf    = null;
let currentSessionId = null;
let _dirty = false;

let liveMode   = "window";
let sessionT0  = 0;
let durationSec = 0;
let lastRmssd  = null;
let lastRmssdNormalized = null;
let lastSmoothedRr = null;
let sessionBaseline = null;

// ── AUDIO BIOFEEDBACK ─────────────────────────────────────────────────────
let audioEngine = null;
let audioSessionActive = false;
let audioEnabled = false;
let audioMode = "smooth_rr";
let audioTexture = "space_pad";
let meditationEngine = null;

const GUIDED_PHRASE_TAGS = { meditation: "sit", rest: "lay" };

function phrasePrefixForTag(tag) {
  return GUIDED_PHRASE_TAGS[tag] ?? null;
}

function syncGuidedPhraseOptionsVisibility() {
  const wrap = $("guided_phrase_options");
  if (!wrap) return;
  wrap.hidden = !phrasePrefixForTag($("tag")?.value);
}

function guidedPhraseOptions() {
  const el = $("opt_guided_phrases");
  const intervalEl = $("guided_phrase_interval");
  let phraseMinIntervalSec = 90;
  if (intervalEl) {
    const raw = intervalEl.value.trim().replace(",", ".");
    const n = parseFloat(raw);
    if (Number.isFinite(n) && n >= 5) phraseMinIntervalSec = n;
  }
  return {
    guidedPhrases: el ? el.checked : false,
    phraseMinIntervalSec,
  };
}

function audioOptions() {
  const el = $("opt_audio_biofeedback");
  return { audioBiofeedback: el ? el.checked : false };
}

function currentAudioMode() {
  const selected = document.querySelector('input[name="audio_mode"]:checked');
  return selected ? selected.value : audioMode;
}

function currentAudioTexture() {
  const el = $("audio_texture");
  return el ? el.value : audioTexture;
}

const TEXTURE_LABELS = {
  space_pad: "Космический пэд",
  sea_wave: "Морской прибой",
  tibetan_bowl: "Тибетская чаша",
};

function setAudioStatus(text, active) {
  const pill = $("audio_status_pill");
  const label = $("audio_status_label");
  if (pill) pill.classList.toggle("active", !!active);
  if (label) label.textContent = text;
}

function syncBiofeedbackStats() {
  const rmEl = $("bf_rmssd");
  const baseEl = $("bf_base");
  const rnEl = $("bf_rn");
  const srEl = $("bf_smoothed_rr");
  const modeEl = $("bf_mode_label");
  const texEl = $("bf_texture_label");

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
  if (rnEl) {
    rnEl.textContent = lastRmssdNormalized !== null ? lastRmssdNormalized.toFixed(2) : "—";
    rnEl.className = "stat-value" + (lastRmssdNormalized != null && lastRmssdNormalized >= 2.5 ? " good" : "");
  }
  if (srEl) srEl.textContent = lastSmoothedRr !== null ? Math.round(lastSmoothedRr) + " ms" : "—";
  if (modeEl) {
    modeEl.textContent = audioMode === "smooth_rr" ? "Дышащий Эмбиент" : "Трансовый Порог";
  }
  if (texEl) {
    texEl.textContent = TEXTURE_LABELS[audioTexture] || audioTexture;
  }
}

async function startAudioEngine() {
  if (!window.HrvAudioEngine) {
    setErr("HrvAudioEngine не загружен");
    return;
  }
  if (audioEngine?.running) return;
  try {
    audioEngine = new HrvAudioEngine();
    audioTexture = currentAudioTexture();
    audioEngine.textureId = audioTexture;
    audioEngine.setMode(audioMode);
    await audioEngine.start();
    setAudioStatus("Звук активен", true);
    $("btn_audio_start").disabled = true;
  } catch (e) {
    setErr(String(e.message || e));
  }
}

async function stopAudioEngine() {
  if (!audioEngine) return;
  await audioEngine.stop();
  audioEngine = null;
  setAudioStatus("Звук остановлен", false);
  const btn = $("btn_audio_start");
  if (btn) btn.disabled = !audioSessionActive || !audioEnabled;
}

function setAudioMode(mode) {
  if (mode !== "smooth_rr" && mode !== "rmssd_trigger") return;
  audioMode = mode;
  audioEngine?.setMode(mode);
  syncBiofeedbackStats();
}

function setAudioTexture(textureId) {
  if (!HrvAudioEngine?.TEXTURES?.includes(textureId)) return;
  audioTexture = textureId;
  audioEngine?.setTexture(textureId);
  syncBiofeedbackStats();
}

function setBiofeedbackPanelVisible(on) {
  const stage = $("biofeedback_stage");
  const idle = $("biofeedback_idle_msg");
  const controls = $("biofeedback_controls");
  if (idle) idle.hidden = on;
  if (controls) controls.hidden = !on;
  if (stage) stage.classList.toggle("idle", !on);
}

function startBiofeedbackSession(opts) {
  audioEnabled = opts.audioBiofeedback;
  audioSessionActive = true;
  audioMode = currentAudioMode();
  setBiofeedbackPanelVisible(audioEnabled);
  setAudioStatus(audioEnabled ? "Ожидание запуска звука" : "Аудио выключено", false);
  const btn = $("btn_audio_start");
  if (btn) btn.disabled = !audioEnabled;
  syncBiofeedbackStats();

  const phrasePrefix = phrasePrefixForTag(opts.tag);
  if (opts.guidedPhrases && phrasePrefix && window.MeditationEngine) {
    meditationEngine = new MeditationEngine();
    meditationEngine.start(
      opts.sessionId,
      phrasePrefix,
      opts.durationMinutes,
      opts.phraseMinIntervalSec,
    ).catch(() => {});
  }
}

function stopBiofeedbackSession() {
  audioSessionActive = false;
  stopAudioEngine();
  meditationEngine?.stop();
  meditationEngine = null;
  setBiofeedbackPanelVisible(false);
  setAudioStatus("Нет активной сессии", false);
  const btn = $("btn_audio_start");
  if (btn) btn.disabled = true;
}

function processAudioFrame(msg, i) {
  if (!audioEngine?.running) return;
  const frame = {
    ts: msg.t[i],
    rr_ms: msg.r[i],
    rmssd: msg.m[i],
    rmssd_normalized: msg.rn?.[i] ?? null,
    smoothed_rr: msg.sr?.[i] ?? null,
  };
  audioEngine.processFrame(frame);
  audioEngine.triggerBeat(frame.rr_ms);
}

document.querySelectorAll('input[name="audio_mode"]').forEach((el) => {
  el.addEventListener("change", () => setAudioMode(el.value));
});

$("audio_texture")?.addEventListener("change", (ev) => {
  setAudioTexture(ev.target.value);
});

$("btn_audio_start")?.addEventListener("click", startAudioEngine);

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
const TAG_LABELS = {
  meditation: "Медитация",
  focus: "Фокус",
  rest: "Отдых",
  scroll: "Скролл",
  untagged: "Без тега",
};
const TAG_PRESETS = ["meditation", "focus", "rest", "scroll", "untagged"];
const CUSTOM_TAGS_KEY = "hrv_custom_tags";
const TAG_CUSTOM_VALUE = "__custom__";

function tagLabel(slug) {
  return TAG_LABELS[slug] || slug;
}

function loadCustomTags() {
  try {
    const raw = localStorage.getItem(CUSTOM_TAGS_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((t) => typeof t === "string" && t.trim()) : [];
  } catch {
    return [];
  }
}

function saveCustomTag(tag) {
  const t = tag.trim();
  if (!t || TAG_PRESETS.includes(t)) return;
  const set = new Set([...loadCustomTags(), t]);
  localStorage.setItem(CUSTOM_TAGS_KEY, JSON.stringify([...set]));
}

function mergeTagList(apiTags) {
  const set = new Set([...TAG_PRESETS, ...loadCustomTags(), ...apiTags]);
  return [...set];
}

function fillFilterSelect(sel, allTags) {
  const prev = sel.value;
  sel.innerHTML = '<option value="">— все —</option>';
  const ordered = [
    ...TAG_PRESETS.filter((t) => allTags.includes(t)),
    ...allTags.filter((t) => !TAG_PRESETS.includes(t)).sort(),
  ];
  for (const t of ordered) {
    sel.appendChild(new Option(tagLabel(t), t));
  }
  if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
}

function fillTagSelect(allTags) {
  const sel = $("tag");
  const prev = sel.value;
  sel.innerHTML = "";
  for (const t of TAG_PRESETS) {
    sel.appendChild(new Option(tagLabel(t), t));
  }
  for (const t of allTags.filter((x) => !TAG_PRESETS.includes(x)).sort()) {
    sel.appendChild(new Option(tagLabel(t), t));
  }
  sel.appendChild(new Option("Другая…", TAG_CUSTOM_VALUE));
  if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
  syncTagCustomVisibility();
  syncGuidedPhraseOptionsVisibility();
}

function syncTagCustomVisibility() {
  const wrap = $("tag_custom_wrap");
  const custom = $("tag_custom");
  if (!wrap) return;
  const on = $("tag")?.value === TAG_CUSTOM_VALUE;
  wrap.hidden = !on;
  if (custom) custom.required = on;
  syncGuidedPhraseOptionsVisibility();
}

function resolveSessionTag() {
  const sel = $("tag");
  if (sel.value === TAG_CUSTOM_VALUE) {
    const raw = $("tag_custom")?.value?.trim() || "";
    if (!raw) throw new Error("Укажите название новой активности");
    if (raw.length > 64) throw new Error("Тип активности не длиннее 64 символов");
    if (!/^[\w\s\-\.а-яА-ЯёЁ]+$/u.test(raw)) {
      throw new Error("Недопустимые символы в типе активности");
    }
    saveCustomTag(raw);
    return raw;
  }
  return sel.value;
}

async function loadTags() {
  const { tags: apiTags } = await api("/api/tags");
  const allTags = mergeTagList(apiTags);
  fillTagSelect(allTags);
  fillFilterSelect($("flt_tag"), allTags);
  fillFilterSelect($("prog_tag"), allTags);
}

$("tag")?.addEventListener("change", syncTagCustomVisibility);

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
const PROGRESS_PLOT_H = 380;

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
  if (progPlot && $("progPlot")) progPlot.setSize({ width: plotWidth($("progPlot")), height: PROGRESS_PLOT_H });
}
window.addEventListener("resize", () => {
  clearTimeout(_resizeT);
  _resizeT = setTimeout(resizePlots, 120);
});

// ── STATS ─────────────────────────────────────────────────────────────────
function updateStats() {
  syncBiofeedbackStats();
  if (lastRmssd === null) return;
  const rmssdEl = $("stat_rmssd");
  rmssdEl.textContent = lastRmssd.toFixed(1);
  if (sessionBaseline !== null && sessionBaseline > 1) {
    const ratio = lastRmssd / sessionBaseline;
    rmssdEl.className = "stat-value" + (ratio < 0.75 ? " bad" : ratio > 0.95 ? " good" : " warn");
  }
  $("stat_base").textContent = sessionBaseline !== null ? sessionBaseline.toFixed(1) : "—";
  const rnEl = $("stat_rn");
  if (rnEl) {
    rnEl.textContent = lastRmssdNormalized !== null ? lastRmssdNormalized.toFixed(2) : "—";
  }
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
  if (!_dirty) {
    raf = requestAnimationFrame(redrawLive);
    return;
  }
  _dirty = false;
  if (liveMode === "timed") {
    const xsR = rrBuf.map(p => Math.max(0, p[0] - sessionT0));
    const ysR = rrBuf.map(p => p[1]);
    const xsM = rmBuf.map(p => Math.max(0, p[0] - sessionT0));
    const ysM = rmBuf.map(p => p[1]);

    if (rrPlot && xsR.length) {
      rrPlot.setData([xsR, ysR]);
      const mn = ysR.reduce((a, b) => a < b ? a : b, Infinity);
      const mx = ysR.reduce((a, b) => a > b ? a : b, -Infinity);
      rrPlot.setScale("y", { min: Math.max(300, mn - 40), max: mx + 40 });
    }
    if (rmPlot && xsM.length) {
      const bl = sessionBaseline;
      const blXs = xsM.length ? [xsM[0], xsM[xsM.length - 1]] : [];
      const blYs = bl !== null ? [bl, bl] : [];
      rmPlot.setData([xsM, ysM, blXs.length ? blXs : [], blYs.length ? blYs : []]);
      const mx = ysM.reduce((a, b) => a > b ? a : b, 40) * 1.15;
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
      const mx = ysR.reduce((a, b) => a > b ? a : b, 400);
      rrPlot.setScale("y", { min: Math.max(300, mx - 500), max: mx + 100 });
    }
    if (rmPlot && xsM.length) {
      const bl = sessionBaseline;
      const blXs = xsM.length ? [xsM[0], xsM[xsM.length - 1]] : [];
      const blYs = bl !== null ? [bl, bl] : [];
      rmPlot.setData([xsM, ysM, blXs.length ? blXs : [], blYs.length ? blYs : []]);
      const mx = ysM.reduce((a, b) => a > b ? a : b, 40) * 1.2;
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
    setBiofeedbackControlsEnabled(true);
    if (ws) { ws.close(); ws = null; }
    stopRaf();
    stopBiofeedbackSession();
    return;
  }
  if (msg.type === "beat" && msg.t?.length) {
    _dirty = true;
    for (let i = 0; i < msg.t.length; i++) {
      rrBuf.push([msg.t[i], msg.r[i]]);
      rmBuf.push([msg.t[i], msg.m[i]]);
      lastRmssd = msg.m[i];
      if (msg.rn?.[i] != null) lastRmssdNormalized = msg.rn[i];
      if (msg.sr?.[i] != null) lastSmoothedRr = msg.sr[i];
      if (msg.bl != null) sessionBaseline = msg.bl;
      updateHR(msg.r[i]);
      if (audioEnabled) processAudioFrame(msg, i);
    }
    meditationEngine?.processFrame(msg);
    syncBiofeedbackStats();
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

function setBiofeedbackControlsEnabled(on) {
  const audioEl = $("opt_audio_biofeedback");
  const guidedEl = $("opt_guided_phrases");
  const intervalEl = $("guided_phrase_interval");
  if (audioEl) audioEl.disabled = !on;
  if (guidedEl) guidedEl.disabled = !on;
  if (intervalEl) intervalEl.disabled = !on;
}

async function startLive() {
  setErr("");
  const participant = $("participant").value.trim();
  if (!participant) { setErr("Укажите участника"); return; }

  const rawMin = $("minutes").value.trim();
  const minutes = rawMin ? parseFloat(rawMin.replace(",", ".")) : null;
  let tag;
  try {
    tag = resolveSessionTag();
  } catch (e) {
    setErr(String(e.message || e));
    return;
  }
  const body = {
    participant,
    tag,
    session_name: $("session_name").value.trim() || null,
    source: $("source").value,
    address: $("address").value.trim() || null,
    minutes: minutes != null && !Number.isNaN(minutes) && minutes > 0 ? minutes : null,
  };

  try {
    const opts = { ...audioOptions(), ...guidedPhraseOptions() };
    audioMode = currentAudioMode();

    const res = await api("/api/sessions", { method: "POST", body: JSON.stringify(body) });
    currentSessionId = res.id;
    const timed = body.minutes != null && body.minutes > 0;
    liveMode    = timed ? "timed" : "window";
    sessionT0   = typeof res.started_at === "number" ? res.started_at : Date.now() / 1000;
    durationSec = timed ? body.minutes * 60 : 0;
    lastRmssd   = null;
    lastRmssdNormalized = null;
    lastSmoothedRr = null;
    sessionBaseline = null;

    $("stat_rmssd").textContent = "—";
    $("stat_rmssd").className   = "stat-value";
    $("stat_base").textContent  = "—";
    $("stat_hr").textContent    = "—";
    $("stat_rn").textContent    = "—";
    syncBiofeedbackStats();

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

    setStatus(`Сессия #${currentSessionId} · ${tagLabel(body.tag)}${timed ? ` · ${body.minutes} мин` : " · скользящее окно"}`);
    loadTags().catch(() => {});
    $("btn_start").disabled = true;
    $("btn_stop").disabled  = false;
    setBiofeedbackControlsEnabled(false);

    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/sessions/${currentSessionId}/stream`);
    ws.onmessage = onWsMessage;
    ws.onerror   = () => setErr("WebSocket ошибка");

    stopRaf();
    raf = requestAnimationFrame(redrawLive);

    startBiofeedbackSession({
      ...opts,
      tag: body.tag,
      sessionId: currentSessionId,
      durationMinutes: body.minutes,
    });
    if (opts.audioBiofeedback) {
      await startAudioEngine();
      switchTab("biofeedback");
    }
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
  setBiofeedbackControlsEnabled(true);
  if (ws) { ws.close(); ws = null; }
  stopRaf();
  stopBiofeedbackSession();
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
  const cls = TAG_PRESETS.includes(t) ? escapeHtml(t) : "";
  return `<span class="tag-pill ${cls}">${escapeHtml(tagLabel(t))}</span>`;
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
      `<td style="text-align:right"><button type="button" class="btn btn-danger btn-delete-session" data-id="${s.id}" style="font-size:.72rem;padding:4px 10px">Удалить</button></td>`;
    tr.addEventListener("click", () => openArchiveSession(s.id));
    tr.querySelector(".btn-delete-session")?.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s.id);
    });
    tb.appendChild(tr);
  }
}

let archRR = null;
let archRM = null;

function renderSummaryGrid(sum) {
  const grid = $("arch_summary_grid");
  grid.innerHTML = "";
  const durMin = sum.duration_sec != null ? sum.duration_sec / 60 : null;
  const vsBl =
    sum.vs_baseline_pct != null
      ? (sum.vs_baseline_pct >= 0 ? "+" : "") + sum.vs_baseline_pct.toFixed(0) + "%"
      : "—";
  const fields = [
    ["RMSSD mean",  sum.rmssd_mean != null ? sum.rmssd_mean.toFixed(1) + " ms" : "—"],
    ["RMSSD min",   sum.rmssd_min  != null ? sum.rmssd_min.toFixed(1)  + " ms" : "—"],
    ["RMSSD max",   sum.rmssd_max  != null ? sum.rmssd_max.toFixed(1)  + " ms" : "—"],
    ["Длительность", durMin != null ? durMin.toFixed(1) + " мин" : "—"],
    ["vs baseline", vsBl],
    ["Drift events", sum.drift_events != null ? String(sum.drift_events) : "—"],
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
  const delBtn = $("btn_delete_arch_session");
  if (delBtn) {
    delBtn.hidden = false;
    delBtn.onclick = () => deleteSession(id);
  }

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
  const xMax = xs.reduce((a, b) => a > b ? a : b, 1);

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

  const rmax = rm.reduce((a, b) => a > b ? a : b, 50) * 1.15;
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

// ── PROGRESS ──────────────────────────────────────────────────────────────
const PROG_COLORS = [
  "#00d4ff", "#39e085", "#9d8ef0", "#f5c542", "#ff6b6b",
  "#ff9f43", "#54a0ff", "#5f27cd", "#01a3a4", "#f368e0",
  "#10ac84", "#ee5a24", "#2e86de", "#8395a7", "#222f3e",
];

let progPlot = null;
let progSessionsRaw = [];

function setProgErr(txt) {
  const el = $("prog_err");
  if (!el) return;
  el.textContent = txt;
  el.classList.toggle("visible", !!txt);
}

function smoothSessionRr(points, windowSec = 15) {
  if (!points.length) return [];
  let lo = 0;
  return points.map((p, i) => {
    const xi = p.x;
    // advance lo so window starts within windowSec of xi
    while (lo < i && xi - points[lo].x > windowSec) lo++;
    let sum = 0, n = 0;
    for (let j = lo; j <= i; j++) { sum += points[j].rr; n++; }
    return { x: xi, rr: n ? sum / n : p.rr };
  });
}

function alignProgressSessions(sessions) {
  // p.x is already seconds-from-start (0..duration_sec) for each session.
  // We build a common uniform grid [0, maxDuration] and resample each session
  // onto it with nearest-neighbor. Points outside a session's own duration
  // become null (natural end-of-line, not a mid-curve gap).
  const cap = 3500;

  const maxDuration = sessions.reduce((a, s) => {
    const last = s.points.length ? s.points[s.points.length - 1].x : 0;
    return last > a ? last : a;
  }, 1);

  // Grid density: ~1 point per second up to cap
  const gridN = Math.min(cap, Math.ceil(maxDuration) + 1);
  const step  = maxDuration / (gridN - 1);
  const xs    = Array.from({ length: gridN }, (_, i) => i * step);

  const ysList = sessions.map((s) => {
    const pts = s.points; // sorted ascending by x, x starts at 0
    if (!pts.length) return xs.map(() => null);

    // median gap between points — used to detect true end-of-data
    const medianGap = pts.length > 1
      ? (pts[pts.length - 1].x - pts[0].x) / (pts.length - 1)
      : step;
    const tolerance = medianGap * 2.5;

    return xs.map((x) => {
      // x beyond this session's range → null (end of line)
      if (x > pts[pts.length - 1].x + tolerance) return null;

      // binary search for nearest point
      let lo = 0, hi = pts.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (pts[mid].x < x) lo = mid + 1; else hi = mid;
      }
      if (lo > 0 && Math.abs(pts[lo - 1].x - x) < Math.abs(pts[lo].x - x)) lo--;
      return Math.abs(pts[lo].x - x) <= tolerance ? pts[lo].rr : null;
    });
  });

  return { xs, ysList };
}

function fmtShortDate(ts) {
  return new Date(ts * 1000).toLocaleDateString("ru-RU");
}

function destroyProgPlot() {
  if (progPlot) {
    progPlot.destroy();
    progPlot = null;
  }
  const el = $("progPlot");
  if (el) el.innerHTML = "";
}

function renderProgLegend(sessions) {
  const leg = $("prog_legend");
  if (!leg) return;
  leg.innerHTML = "";
  sessions.forEach((s, i) => {
    const item = document.createElement("div");
    item.className = "prog-legend-item";
    const sw = document.createElement("span");
    sw.className = "prog-legend-swatch";
    sw.style.background = PROG_COLORS[i % PROG_COLORS.length];
    item.appendChild(sw);
    item.appendChild(
      document.createTextNode(
        `#${s.id} · ${tagLabel(s.tag)} · ${fmtShortDate(s.started)} · ${Math.round(s.duration_sec)} с`
      )
    );
    leg.appendChild(item);
  });
}

function buildProgressPlot(sessions) {
  destroyProgPlot();
  const emptyEl = $("prog_empty");
  if (!sessions.length) {
    if (emptyEl) emptyEl.hidden = false;
    renderProgLegend([]);
    return;
  }
  if (emptyEl) emptyEl.hidden = true;

  const { xs, ysList } = alignProgressSessions(sessions);
  const xMax = Math.max(
    sessions.reduce((a, s) => Math.max(a, s.duration_sec || 0), 0),
    xs.length ? xs[xs.length - 1] : 1,
    1
  );
  const allY = ysList.flat().filter((v) => v != null);
  const yMin = allY.length ? Math.max(300, allY.reduce((a, b) => a < b ? a : b, Infinity) - 40) : 350;
  const yMax = allY.length ? allY.reduce((a, b) => a > b ? a : b, -Infinity) + 40 : 1300;

  const series = [{}];
  const data = [xs];
  for (let i = 0; i < ysList.length; i++) {
    series.push({
      stroke: PROG_COLORS[i % PROG_COLORS.length],
      width: 1.5,
      spanGaps: false,
    });
    data.push(ysList[i]);
  }

  const w = plotWidth($("progPlot"));
  progPlot = new uPlot(
    {
      width: w,
      height: PROGRESS_PLOT_H,
      padding: [8, 8, 0, 0],
      scales: {
        x: { ...xScaleLinear, range: [0, xMax] },
        y: { time: false, distr: 1, range: [yMin, yMax] },
      },
      series,
      axes: [
        {
          stroke: "#3a4050",
          ticks: { stroke: "#3a4050" },
          grid: { stroke: "#1e242d", width: 1 },
          label: "с от начала",
          labelFont: "11px 'DM Sans'",
          font: "11px 'Space Mono'",
          stroke: "#5a6478",
          values: fmtAxisSec,
        },
        {
          stroke: "#3a4050",
          ticks: { stroke: "#3a4050" },
          grid: { stroke: "#1e242d", width: 1 },
          label: "RR, ms",
          labelFont: "11px 'DM Sans'",
          font: "11px 'Space Mono'",
          stroke: "#5a6478",
          size: 52,
        },
      ],
      cursor: { show: true, x: true, y: false },
      legend: { show: false },
    },
    data,
    $("progPlot")
  );
  renderProgLegend(sessions);
  requestAnimationFrame(resizePlots);
}

async function loadProgress() {
  setProgErr("");
  const tag = $("prog_tag")?.value || "";
  const from = $("prog_from")?.value || "";
  const to = $("prog_to")?.value || "";
  let url = "/api/progress?max_sessions=40";
  if (tag) url += `&tag=${encodeURIComponent(tag)}`;
  if (from) url += `&started_after=${encodeURIComponent(from)}`;
  if (to) url += `&started_before=${encodeURIComponent(to)}`;

  try {
    const { sessions } = await api(url);
    progSessionsRaw = sessions;
    applyProgressSmooth();
  } catch (e) {
    setProgErr(String(e.message || e));
    destroyProgPlot();
  }
}

function applyProgressSmooth() {
  const smooth = $("prog_smooth")?.checked;
  const plotted = progSessionsRaw.map((s) => ({
    ...s,
    points: smooth ? smoothSessionRr(s.points) : s.points,
  }));
  buildProgressPlot(plotted);
}

$("btn_prog_build")?.addEventListener("click", loadProgress);
$("prog_smooth")?.addEventListener("change", applyProgressSmooth);

// ── DELETE SESSION ────────────────────────────────────────────────────────
async function deleteSession(id) {
  const ok = confirm(`Удалить сессию #${id}?\n\nТочки RR/RMSSD и логи фраз будут удалены. Действие необратимо.`);
  if (!ok) return;
  try {
    await api(`/api/sessions/${id}`, { method: "DELETE" });
    const openId = $("arch_id")?.textContent;
    if (openId && String(id) === openId) {
      $("arch_detail")?.classList.remove("visible");
      $("btn_delete_arch_session")?.setAttribute("hidden", "");
      if (archRR) { archRR.destroy(); archRR = null; }
      if (archRM) { archRM.destroy(); archRM = null; }
    }
    await loadArchive();
    if ($("tab-progress")?.classList.contains("active")) {
      await loadProgress();
    }
    setStatus(`Сессия #${id} удалена.`);
  } catch (e) {
    setErr(String(e.message || e));
  }
}

// ── WIPE HISTORY ──────────────────────────────────────────────────────────
async function wipeHistory() {
  const ok = confirm(
    "Удалить ВСЮ историю?\n\nБудут удалены все сессии, все точки RR/RMSSD и почасовой baseline. Действие необратимо."
  );
  if (!ok) return;
  try {
    await api("/api/history", { method: "DELETE" });
    progSessionsRaw = [];
    destroyProgPlot();
    $("arch_detail")?.classList.remove("visible");
    $("btn_delete_arch_session")?.setAttribute("hidden", "");
    $("arch_rows").innerHTML = "";
    const emptyEl = $("prog_empty");
    if (emptyEl) emptyEl.hidden = false;
    await loadTags();
    await loadArchive();
    setProgErr("");
    setStatus("История полностью очищена.");
  } catch (e) {
    setProgErr(String(e.message || e));
  }
}

$("btn_wipe_history")?.addEventListener("click", wipeHistory);
$("btn_wipe_history_prog")?.addEventListener("click", wipeHistory);

loadTags().catch(e => setErr(String(e)));
syncGuidedPhraseOptionsVisibility();