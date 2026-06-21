/* global uPlot */

const $ = (id) => document.getElementById(id);

let rrPlot = null;
let rrBuf  = [];
let ws     = null;
let raf    = null;
let currentSessionId = null;
let _sessionEndHandled = false;
let _notesModalSessionId = null;
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

// Динамически заполняется из /api/session-types при старте
let GUIDED_PHRASE_TAGS = {};
let PHRASE_SETS = [];
const PHRASE_SET_PREF_KEY = "hrv_phrase_set_by_prefix";

function phrasePrefixForTag(tag) {
  return GUIDED_PHRASE_TAGS[tag] ?? null;
}

function loadPhraseSetPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PHRASE_SET_PREF_KEY) || "{}");
  } catch {
    return {};
  }
}

function savePhraseSetPref(prefix, setName) {
  const prefs = loadPhraseSetPrefs();
  prefs[prefix] = setName;
  localStorage.setItem(PHRASE_SET_PREF_KEY, JSON.stringify(prefs));
}

function phraseSetsForPrefix(prefix) {
  return PHRASE_SETS.filter((item) => item.prefix === prefix);
}

function syncPhraseSetOptions() {
  const select = $("guided_phrase_set");
  if (!select) return;
  const prefix = phrasePrefixForTag($("tag")?.value);
  const prev = select.value;
  const items = prefix ? phraseSetsForPrefix(prefix) : [];
  select.innerHTML = "";
  if (!items.length) {
    select.disabled = true;
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = prefix ? "нет mp3-наборов" : "—";
    select.appendChild(opt);
    return;
  }
  select.disabled = false;
  const prefs = loadPhraseSetPrefs();
  for (const item of items) {
    const opt = document.createElement("option");
    opt.value = item.set;
    opt.textContent = `${item.set} (${item.mp3_count} mp3)`;
    select.appendChild(opt);
  }
  const preferred = prefs[prefix];
  if (preferred && items.some((item) => item.set === preferred)) {
    select.value = preferred;
  } else if (items.some((item) => item.set === prev)) {
    select.value = prev;
  } else if (items.some((item) => item.set === "directive")) {
    select.value = "directive";
  } else {
    select.value = items[0].set;
  }
}

function syncGuidedPhraseOptionsVisibility() {
  const wrap = $("guided_phrase_options");
  if (!wrap) return;
  wrap.hidden = !phrasePrefixForTag($("tag")?.value);
  syncPhraseSetOptions();
}

function guidedPhraseOptions() {
  const el = $("opt_guided_phrases");
  const intervalEl = $("guided_phrase_interval");
  const setEl = $("guided_phrase_set");
  let phraseMinIntervalSec = 20;
  if (intervalEl) {
    const raw = intervalEl.value.trim().replace(",", ".");
    const n = parseFloat(raw);
    if (Number.isFinite(n) && n >= 5) phraseMinIntervalSec = n;
  }
  const phraseSet = setEl?.value || "directive";
  const prefix = phrasePrefixForTag($("tag")?.value);
  if (prefix && phraseSet) savePhraseSetPref(prefix, phraseSet);
  return {
    guidedPhrases: el ? el.checked : false,
    phraseMinIntervalSec,
    phraseSet,
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
  const bioTabVisible = $("tab-biofeedback")?.classList.contains("visible");
  const rmEl = $("bf_rmssd");
  const baseEl = $("bf_base");
  const rnEl = $("bf_rn");
  const srEl = $("bf_smoothed_rr");
  const modeEl = $("bf_mode_label");
  const texEl = $("bf_texture_label");

  if (rmEl && bioTabVisible) {
    rmEl.textContent = lastRmssd !== null ? lastRmssd.toFixed(1) : "—";
    if (lastRmssd !== null && sessionBaseline !== null && sessionBaseline > 1) {
      const ratio = lastRmssd / sessionBaseline;
      rmEl.className = "stat-value" + (ratio < 0.75 ? " bad" : ratio > 0.95 ? " good" : " warn");
    } else {
      rmEl.className = "stat-value";
    }
  }
  if (baseEl && bioTabVisible) baseEl.textContent = sessionBaseline !== null ? sessionBaseline.toFixed(1) : "—";
  if (rnEl && bioTabVisible) {
    rnEl.textContent = lastRmssdNormalized !== null ? lastRmssdNormalized.toFixed(2) : "—";
    rnEl.className = "stat-value" + (lastRmssdNormalized != null && lastRmssdNormalized >= 2.5 ? " good" : "");
  }
  if (srEl && bioTabVisible) srEl.textContent = lastSmoothedRr !== null ? Math.round(lastSmoothedRr) + " ms" : "—";
  if (modeEl && bioTabVisible) {
    modeEl.textContent = audioMode === "smooth_rr" ? "Дышащий Эмбиент" : "Трансовый Порог";
  }
  if (texEl && bioTabVisible) {
    texEl.textContent = TEXTURE_LABELS[audioTexture] || audioTexture;
  }

  // mirror to live tab placeholder mini-stats (only when live tab visible to avoid RAF/DOM churn affecting other tabs)
  const liveTabVisible = $("tab-live")?.classList.contains("visible");
  if (liveTabVisible) {
    const liveRm = $("live_bf_rmssd");
    const liveRn = $("live_bf_rn");
    if (liveRm) {
      liveRm.textContent = lastRmssd !== null ? lastRmssd.toFixed(1) : "—";
      liveRm.className = rmEl ? rmEl.className : "stat-value";
    }
    if (liveRn) {
      liveRn.textContent = lastRmssdNormalized !== null ? lastRmssdNormalized.toFixed(2) : "—";
      liveRn.className = rnEl ? rnEl.className : "stat-value";
    }
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
      opts.phraseSet,
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
  nextFrame(resizePlots);
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
// Единственный источник правды — /api/session-types (таблица в БД).
// Кастомные теги создаются через POST /api/session-types и сразу сохраняются в БД.
let TAG_LABELS = {};
let TAG_PRESETS = [];   // системные slugs (is_custom=false)
let CHART_PROFILE_BY_TAG = {};   // slug → chart_profile (см. CHART_PROFILES)
const TAG_CUSTOM_VALUE = "__custom__";

function tagLabel(slug) {
  return TAG_LABELS[slug] || slug;
}

function fillFilterSelect(sel, allTypes) {
  const prev = sel.value;
  sel.innerHTML = '<option value="">— все —</option>';
  for (const st of allTypes) {
    sel.appendChild(new Option(st.label, st.slug));
  }
  if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
}

function fillTagSelect(allTypes) {
  const sel = $("tag");
  const prev = sel.value;
  sel.innerHTML = "";
  for (const st of allTypes) {
    sel.appendChild(new Option(st.label, st.slug));
  }
  sel.appendChild(new Option("Новая активность…", TAG_CUSTOM_VALUE));
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

async function resolveSessionTag() {
  const sel = $("tag");
  if (sel.value !== TAG_CUSTOM_VALUE) return sel.value;

  const raw = $("tag_custom")?.value?.trim() || "";
  if (!raw) throw new Error("Укажите название новой активности");
  if (raw.length > 64) throw new Error("Тип активности не длиннее 64 символов");
  if (!/^[\w\s\-\.а-яА-ЯёЁ]+$/u.test(raw)) {
    throw new Error("Недопустимые символы в типе активности");
  }

  // Slug = lowercase, пробелы → '_'
  const slug = raw.toLowerCase().replace(/\s+/g, "_");

  // Сохраняем в БД если новый
  const { session_types: existing } = await api("/api/session-types");
  if (!existing.some((s) => s.slug === slug)) {
    await api("/api/session-types", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug, label: raw }),
    });
  }
  await loadSessionTypes();
  return slug;
}

// ── TAG CHIP INPUT (autocomplete + chips) ──────────────────────────────────
// Generic component: a box with removable tag chips + text input with
// autocomplete suggestions. Used for note-tag filters (archive/progress) and
// for editing tags on a session's notes.
function createTagChipInput(container, { onChange, allowCreate = true } = {}) {
  if (!container) return null;
  const box = container.querySelector(".tag-chip-box");
  const input = container.querySelector("input");
  const suggBox = container.querySelector(".tag-chip-suggestions");
  let tags = [];
  let allSuggestions = [];
  let activeIndex = -1;

  function emitChange() {
    if (onChange) onChange([...tags]);
  }

  function renderChips() {
    [...box.querySelectorAll(".tag-chip")].forEach((el) => el.remove());
    for (const tag of tags) {
      const chip = document.createElement("span");
      chip.className = "tag-chip";
      chip.textContent = `#${tag}`;
      const rm = document.createElement("span");
      rm.className = "tag-chip-remove";
      rm.textContent = "×";
      rm.title = "Удалить тег";
      rm.addEventListener("click", (e) => {
        e.stopPropagation();
        tags = tags.filter((t) => t !== tag);
        renderChips();
        emitChange();
      });
      chip.appendChild(rm);
      box.insertBefore(chip, input);
    }
  }

  function closeSuggestions() {
    suggBox.hidden = true;
    suggBox.innerHTML = "";
    activeIndex = -1;
  }

  function currentMatches() {
    const q = input.value.trim().toLowerCase();
    let matches = allSuggestions.filter(
      (t) => !tags.includes(t) && (!q || t.toLowerCase().includes(q))
    );
    const canCreate = allowCreate && q && !allSuggestions.includes(q) && !tags.includes(q);
    return { matches, canCreate, q };
  }

  function renderSuggestions() {
    const { matches, canCreate, q } = currentMatches();
    suggBox.innerHTML = "";
    if (!matches.length && !canCreate) {
      closeSuggestions();
      return;
    }
    for (const tag of matches) {
      const item = document.createElement("div");
      item.className = "tag-chip-suggestion";
      item.textContent = `#${tag}`;
      item.addEventListener("mousedown", (e) => {
        e.preventDefault();
        addTag(tag);
      });
      suggBox.appendChild(item);
    }
    if (canCreate) {
      const item = document.createElement("div");
      item.className = "tag-chip-suggestion tag-chip-suggestion-create";
      item.textContent = `+ добавить «#${q}»`;
      item.addEventListener("mousedown", (e) => {
        e.preventDefault();
        addTag(q);
      });
      suggBox.appendChild(item);
    }
    activeIndex = -1;
    suggBox.hidden = false;
  }

  const TAG_CHAR_RE = /^[\w\-а-яА-ЯёЁ]+$/u;

  function addTag(rawTag) {
    const tag = (rawTag || "").trim().toLowerCase().replace(/^#/, "");
    if (!tag || tags.includes(tag) || !TAG_CHAR_RE.test(tag)) {
      input.value = "";
      closeSuggestions();
      return;
    }
    if (!allowCreate && !allSuggestions.includes(tag)) {
      input.value = "";
      closeSuggestions();
      return;
    }
    tags.push(tag);
    input.value = "";
    renderChips();
    closeSuggestions();
    emitChange();
    input.focus();
  }

  input.addEventListener("input", () => {
    // Теги не могут содержать пробелы — допустимы только буквы, цифры, дефис и подчёркивание.
    if (/\s/.test(input.value)) {
      input.value = input.value.replace(/\s+/g, "");
    }
    renderSuggestions();
  });
  input.addEventListener("focus", renderSuggestions);
  input.addEventListener("keydown", (e) => {
    if (e.key === " " || e.code === "Space") {
      e.preventDefault();
      return;
    }
    const items = [...suggBox.querySelectorAll(".tag-chip-suggestion")];
    if (e.key === "ArrowDown" && items.length) {
      e.preventDefault();
      activeIndex = Math.min(activeIndex + 1, items.length - 1);
      items.forEach((it, i) => it.classList.toggle("active", i === activeIndex));
      items[activeIndex]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "ArrowUp" && items.length) {
      e.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      items.forEach((it, i) => it.classList.toggle("active", i === activeIndex));
      items[activeIndex]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (activeIndex >= 0 && items[activeIndex]) {
        items[activeIndex].dispatchEvent(new Event("mousedown"));
      } else if (input.value.trim()) {
        addTag(input.value);
      }
    } else if (e.key === "Backspace" && !input.value && tags.length) {
      tags.pop();
      renderChips();
      closeSuggestions();
      emitChange();
    } else if (e.key === "Escape") {
      closeSuggestions();
    }
  });
  box.addEventListener("click", (e) => {
    if (e.target === box) input.focus();
  });
  document.addEventListener("click", (e) => {
    if (!container.contains(e.target)) closeSuggestions();
  });

  return {
    setSuggestions(list) {
      allSuggestions = list || [];
      if (document.activeElement === input) renderSuggestions();
    },
    getTags() {
      return [...tags];
    },
    setTags(list) {
      tags = [...new Set((list || []).map((t) => String(t).toLowerCase()))];
      renderChips();
    },
  };
}

let fltNoteTagsInput = null;
let progNoteTagsInput = null;
let sessionNotesTagsInput = null;

function ensureTagChipInputs() {
  if (!fltNoteTagsInput) {
    fltNoteTagsInput = createTagChipInput($("flt_note_tags"), {
      allowCreate: false,
      onChange: () => loadArchive().catch((e) => setErr(String(e.message || e))),
    });
  }
  if (!progNoteTagsInput) {
    progNoteTagsInput = createTagChipInput($("prog_note_tags"), { allowCreate: false });
  }
  if (!sessionNotesTagsInput) {
    sessionNotesTagsInput = createTagChipInput($("session_notes_tags"));
  }
}

function appendNoteTagFilters(url, chipInput) {
  for (const tag of (chipInput?.getTags() || [])) {
    url += `&note_tag=${encodeURIComponent(tag)}`;
  }
  return url;
}

async function loadNoteTags() {
  ensureTagChipInputs();
  try {
    const { tags } = await api("/api/note-tags");
    fltNoteTagsInput?.setSuggestions(tags);
    progNoteTagsInput?.setSuggestions(tags);
    sessionNotesTagsInput?.setSuggestions(tags);
  } catch (e) {
    console.warn("loadNoteTags failed:", e);
  }
}

function parseNoteTagsClient(text) {
  if (!text) return [];
  const re = /#([\w\-а-яА-ЯёЁ]+)/gu;
  const seen = new Set();
  const out = [];
  let m;
  while ((m = re.exec(text)) !== null) {
    const tag = m[1].toLowerCase();
    if (!seen.has(tag)) {
      seen.add(tag);
      out.push(tag);
    }
  }
  return out;
}

// Объединяет свободный текст заметки с набором тегов из chip-инпута:
// удаляет существующие #теги из текста и добавляет актуальный набор в конец.
function mergeNotesWithTags(text, tags) {
  const body = (text || "").replace(/#([\w\-а-яА-ЯёЁ]+)/gu, "").replace(/\s+/g, " ").trim();
  const tagsStr = (tags || []).map((t) => `#${t}`).join(" ");
  if (body && tagsStr) return `${body} ${tagsStr}`;
  return body || tagsStr;
}

function noteTagsHtml(tags) {
  if (!tags?.length) return '<span style="color:var(--text-muted)">—</span>';
  return tags.map((t) => `<span class="note-tag-pill">#${escapeHtml(t)}</span>`).join("");
}

function isoLocalDate(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function dateRangeFromPreset(preset) {
  if (!preset) return { started_after: "", started_before: "" };
  const today = new Date();
  const startOfDay = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  if (preset === "today") {
    const iso = isoLocalDate(startOfDay);
    return { started_after: iso, started_before: iso };
  }
  if (preset === "yesterday") {
    const y = new Date(startOfDay);
    y.setDate(y.getDate() - 1);
    const iso = isoLocalDate(y);
    return { started_after: iso, started_before: iso };
  }
  const days = parseInt(preset, 10);
  if (Number.isFinite(days) && days > 0) {
    const from = new Date(startOfDay);
    from.setDate(from.getDate() - (days - 1));
    return { started_after: isoLocalDate(from), started_before: isoLocalDate(startOfDay) };
  }
  return { started_after: "", started_before: "" };
}

function appendDateFilters(url, periodEl) {
  const { started_after, started_before } = dateRangeFromPreset(periodEl?.value || "");
  if (started_after) url += `&started_after=${encodeURIComponent(started_after)}`;
  if (started_before) url += `&started_before=${encodeURIComponent(started_before)}`;
  return url;
}

async function loadPhraseSets() {
  try {
    const { sets } = await api("/api/meditation/phrase-sets");
    PHRASE_SETS = Array.isArray(sets) ? sets : [];
    syncPhraseSetOptions();
  } catch (e) {
    console.warn("loadPhraseSets failed:", e);
    PHRASE_SETS = [];
    syncPhraseSetOptions();
  }
}

async function loadSessionTypes() {
  try {
    const { session_types } = await api("/api/session-types");
    TAG_LABELS = {};
    TAG_PRESETS = [];
    GUIDED_PHRASE_TAGS = {};
    CHART_PROFILE_BY_TAG = {};
    for (const st of session_types) {
      TAG_LABELS[st.slug] = st.label;
      if (!st.is_custom) TAG_PRESETS.push(st.slug);
      if (st.phrase_prefix) GUIDED_PHRASE_TAGS[st.slug] = st.phrase_prefix;
      CHART_PROFILE_BY_TAG[st.slug] = st.chart_profile || "default";
    }
    fillTagSelect(session_types);
    fillFilterSelect($("flt_tag"), session_types);
    fillFilterSelect($("prog_tag"), session_types);
    syncGuidedPhraseOptionsVisibility();
    await Promise.all([loadNoteTags(), loadPhraseSets()]);
  } catch (e) {
    console.warn("loadSessionTypes failed:", e);
  }
}

$("tag")?.addEventListener("change", syncTagCustomVisibility);

// ── PLOT HELPERS ──────────────────────────────────────────────────────────
const xScaleLinear = { time: false, distr: 1 };

function fmtAxisSec(u, splits) {
  return splits.map((v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "";
    return String(Math.round(n));
  });
}

const SEC_AXIS_INCRS = [1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 1800, 3600];

function liveRRPlotWidth(el) {
  const grid = el?.closest(".live-view-grid");
  if (!grid) return null;

  const body = (el?.id === "rrPlot" ? el : null) || grid.querySelector("#rrPlot");
  if (body?.clientWidth > 0) {
    const st = getComputedStyle(body);
    const pad = parseFloat(st.paddingLeft) + parseFloat(st.paddingRight);
    return Math.max(200, Math.floor(body.clientWidth - pad - 4));
  }

  const plotCard = grid.querySelector(".plot-card");
  const cardW = plotCard?.clientWidth;
  if (cardW > 0) return Math.max(200, Math.floor(cardW - 16));

  const gap = parseFloat(getComputedStyle(grid).columnGap) || 16;
  const bio = grid.querySelector(".biofeedback-placeholder");
  const bioW = bio ? (parseFloat(getComputedStyle(grid).gridTemplateColumns.split(" ")[1]) || 240) : 240;

  const panel = grid.closest(".plots-panel");
  const panelW = panel?.clientWidth;
  if (panelW > 0) return Math.max(200, Math.floor(panelW - bioW - gap - 16));

  const mainGrid = grid.closest(".grid-2");
  if (mainGrid?.clientWidth > 0) {
    const g2 = getComputedStyle(mainGrid);
    const g2Gap = parseFloat(g2.columnGap) || 16;
    const cols = g2.gridTemplateColumns.split(" ").map(parseFloat).filter(Number.isFinite);
    const rightCol = cols.length >= 2 ? cols[1] : Math.floor(mainGrid.clientWidth * 0.62);
    return Math.max(200, Math.floor(rightCol - bioW - gap - 16));
  }

  return 400;
}

function plotWidth(el) {
  const liveW = liveRRPlotWidth(el);
  if (liveW != null) return liveW;

  const wrap = el?.closest(".plot-wrap");
  const card = el?.closest(".plot-card");
  const measure = wrap || card;
  let w = measure?.clientWidth || 0;
  if (!w) w = Math.min(900, window.innerWidth - 380);
  return Math.max(200, Math.floor(w - 8));
}

function syncLivePlotXScale() {
  if (!rrPlot || liveMode !== "timed" || !(durationSec > 0)) return;
  rrPlot.setScale("x", { min: 0, max: durationSec });
}

const LIVE_PLOT_H = 360;
const ARCHIVE_PLOT_H = 260;
const PROGRESS_PLOT_H = 280;

const AC = () => window.HrvAnalysisCharts;

const RR_COLOR   = "#00d4ff";

// ── ARCHIVE CHART PROFILES ──────────────────────────────────────────────
// Профиль определяет, какие панели архива показывать (состав) и какие
// опции передать в фабрики графиков (analysis_charts.js).
// Тип сессии → профиль задаётся полем chart_profile в session_types
// (см. CHART_PROFILE_BY_TAG, заполняется из /api/session-types).
//
// panels — подмножество ключей ARCHIVE_PANEL_IDS (порядок в массиве не
// влияет на расположение в DOM — компоновка фиксирована в index.html).
// options[panelKey] передаётся как доп. аргумент opts в соответствующую
// фабрику make*Plot — конкретные поля см. в analysis_charts.js.
const ARCHIVE_PANEL_IDS = {
  rr: "arch_rr",
  sdnn: "arch_sdnn",
  poincare: "arch_poincare",
  spectrum: "arch_spectrum",
};

const CHART_PROFILES = {
  default: {
    panels: ["rr", "sdnn", "poincare", "spectrum"],
    options: {},
  },
  // Пример кастомного профиля (привязка: chart_profile="my_profile" в SESSION_TYPES):
  // my_profile: {
  //   panels: ["rr", "sdnn", "poincare"],   // без спектра, порядок панелей не меняется
  //   options: {
  //     rr:       { stroke: "#39e085", fillAlpha: 0.08 },
  //     poincare: { pointRadius: 3 },
  //   },
  // },
};

function chartProfileFor(tag) {
  return CHART_PROFILES[CHART_PROFILE_BY_TAG[tag]] || CHART_PROFILES.default;
}

function rrCfg(timed, w) {
  const xRange = timed && durationSec > 0
    ? (_u, _mn, _mx) => [0, durationSec]
    : (_u, _mn, _mx) => [-60, 0];
  return {
    width: w, height: LIVE_PLOT_H,
    padding: [8, 40, 4, 4],
    scales: {
      x: { ...xScaleLinear, range: xRange },
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
        incrs: SEC_AXIS_INCRS,
        gap: 4,
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

function makeRRPlot(el, timed) {
  if (rrPlot) { rrPlot.destroy(); rrPlot = null; }
  el.innerHTML = "";
  rrPlot = new uPlot(rrCfg(timed, plotWidth(el)), [[], []], el);
  syncLivePlotXScale();
  return rrPlot;
}

function setLiveEmptyState(mode) {
  const empty = $("live_rr_empty");
  if (!empty) return;
  if (mode === "idle") {
    empty.textContent = "Служба готова к запуску";
    empty.classList.remove("hidden");
  } else if (mode === "waiting") {
    empty.textContent = "Ожидание данных";
    empty.classList.remove("hidden");
  } else {
    empty.classList.add("hidden");
  }
}

// ── RESIZE ────────────────────────────────────────────────────────────────
let _resizeT = null;

// Двойной RAF: ждём, пока браузер завершит layout после показа секции
// (display:none → visible), иначе clientWidth может быть устаревшим —
// из-за этого графики обрезались справа и/или сжимались при возврате
// на вкладку без повторного открытия сессии.
function nextFrame(fn) {
  requestAnimationFrame(() => requestAnimationFrame(fn));
}

function resizePlots() {
  const liveVisible = $("tab-live")?.classList.contains("visible");
  const archiveVisible = $("tab-archive")?.classList.contains("visible");
  const progressVisible = $("tab-progress")?.classList.contains("visible");

  const rrEl = $("rrPlot");
  if (liveVisible && rrPlot && rrEl) {
    rrPlot.setSize({ width: plotWidth(rrEl), height: LIVE_PLOT_H });
    syncLivePlotXScale();
  }
  if (archiveVisible) {
    if (archRR && $("arch_rr")) archRR.setSize({ width: plotWidth($("arch_rr")), height: ARCHIVE_PLOT_H });
    if (archPoincare && $("arch_poincare")) archPoincare.setSize({ width: plotWidth($("arch_poincare")), height: ARCHIVE_PLOT_H });
    if (archSpectrum?.plot && $("arch_spectrum")) {
      archSpectrum.plot.setSize({ width: plotWidth($("arch_spectrum")), height: ARCHIVE_PLOT_H });
      AC()?.positionPeakMarker(archSpectrum.plot, $("arch_spectrum")?.parentElement, archSpectrum.peakFreq);
    }
    if (archSdnn && $("arch_sdnn")) archSdnn.setSize({ width: plotWidth($("arch_sdnn")), height: ARCHIVE_PLOT_H });
    if (archRM && $("arch_rm")) archRM.setSize({ width: plotWidth($("arch_rm")), height: ARCHIVE_PLOT_H });
  }
  if (progressVisible) {
    if (progPoincare && $("prog_poincare")) progPoincare.setSize({ width: plotWidth($("prog_poincare")), height: PROGRESS_PLOT_H });
    if (progSpectrum && $("prog_spectrum")) {
      progSpectrum.setSize({ width: plotWidth($("prog_spectrum")), height: PROGRESS_PLOT_H });
      AC()?.positionPeakMarker(progSpectrum, $("prog_spectrum")?.parentElement, progSpectrumPeakFreq);
    }
    if (progSdnn && $("prog_sdnn")) progSdnn.setSize({ width: plotWidth($("prog_sdnn")), height: PROGRESS_PLOT_H });
  }
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

    if (rrPlot && xsR.length) {
      setLiveEmptyState("hidden");
      rrPlot.setData([xsR, ysR]);
      syncLivePlotXScale();
      const mn = ysR.reduce((a, b) => a < b ? a : b, Infinity);
      const mx = ysR.reduce((a, b) => a > b ? a : b, -Infinity);
      rrPlot.setScale("y", { min: Math.max(300, mn - 40), max: mx + 40 });
    }
  } else {
    const now = Date.now() / 1000;
    trimBuf(rrBuf, 65);

    const xsR = rrBuf.map(p => p[0] - now);
    const ysR = rrBuf.map(p => p[1]);

    if (rrPlot && xsR.length) {
      setLiveEmptyState("hidden");
      rrPlot.setData([xsR, ysR]);
      const mx = ysR.reduce((a, b) => a > b ? a : b, 400);
      rrPlot.setScale("y", { min: Math.max(300, mx - 500), max: mx + 100 });
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
    onSessionEnded("Сессия завершена.");
    return;
  }
  if (msg.type === "beat" && msg.t?.length) {
    _dirty = true;
    for (let i = 0; i < msg.t.length; i++) {
      rrBuf.push([msg.t[i], msg.r[i]]);
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

function finalizeLiveSession() {
  $("btn_stop").disabled = true;
  $("btn_start").disabled = false;
  setBiofeedbackControlsEnabled(true);
  if (ws) { ws.close(); ws = null; }
  stopRaf();
  stopBiofeedbackSession();
}

function onSessionEnded(statusText) {
  if (_sessionEndHandled) return;
  _sessionEndHandled = true;
  setStatus(statusText);
  finalizeLiveSession();
  const sid = currentSessionId;
  if (sid) showSessionNotesModal(sid);
}

function closeSessionNotesModal() {
  const modal = $("session_notes_modal");
  if (modal) modal.classList.remove("visible");
  _notesModalSessionId = null;
}

function showSessionNotesModal(sessionId) {
  const modal = $("session_notes_modal");
  const input = $("session_notes_input");
  const idEl = $("session_notes_id");
  if (!modal || !input) return;
  ensureTagChipInputs();
  _notesModalSessionId = sessionId;
  if (idEl) idEl.textContent = String(sessionId);
  const rawText = $("session_name")?.value?.trim() || "";
  const tags = parseNoteTagsClient(rawText);
  input.value = rawText.replace(/#([\w\-а-яА-ЯёЁ]+)/gu, "").replace(/\s+/g, " ").trim();
  sessionNotesTagsInput?.setTags(tags);
  modal.classList.add("visible");
  // do not auto-focus to avoid stealing focus from inputs on other tabs (e.g. archive filters)

  // char counter
  const countEl = $("notes_char_count");
  const updateCount = () => { if (countEl) countEl.textContent = String(input.value.length); };
  updateCount();
  input.oninput = updateCount;
}

async function saveSessionNotes() {
  const sessionId = _notesModalSessionId;
  if (!sessionId) {
    closeSessionNotesModal();
    return;
  }
  const rawText = ($("session_notes_input")?.value || "").trim();
  const tags = sessionNotesTagsInput?.getTags() || [];
  const text = mergeNotesWithTags(rawText, tags);
  try {
    await api(`/api/sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify({ session_name: text || null }),
    });
    if ($("session_name")) $("session_name").value = text;
    await loadNoteTags();
    if (text) {
      setStatus(`Заметки к сессии #${sessionId} сохранены.`);
    }
  } catch (e) {
    setErr(String(e.message || e));
    return;
  }
  closeSessionNotesModal();
}


$("session_notes_save")?.addEventListener("click", () => { saveSessionNotes(); });
$("session_notes_skip")?.addEventListener("click", () => { closeSessionNotesModal(); });
$("session_notes_modal")?.addEventListener("click", (e) => {
  if (e.target === $("session_notes_modal")) closeSessionNotesModal();
});

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
  const setEl = $("guided_phrase_set");
  if (audioEl) audioEl.disabled = !on;
  if (guidedEl) guidedEl.disabled = !on;
  if (intervalEl) intervalEl.disabled = !on;
  if (setEl) setEl.disabled = !on || !phraseSetsForPrefix(phrasePrefixForTag($("tag")?.value)).length;
}

function syncSourceFields() {
  const isBle = $("source")?.value === "ble";
  const wrap = $("address_wrap");
  if (wrap) wrap.hidden = !isBle;
}

async function startLive() {
  setErr("");
  const participant = $("participant").value.trim();
  if (!participant) { setErr("Укажите участника"); return; }

  const source = $("source").value;
  const address = $("address").value.trim() || null;
  if (source === "ble" && !address) {
    setErr("Укажите MAC адрес для BLE Polar H10");
    return;
  }

  const rawMin = $("minutes").value.trim();
  const minutes = rawMin ? parseFloat(rawMin.replace(",", ".")) : null;
  let tag;
  try {
    tag = await resolveSessionTag();
  } catch (e) {
    setErr(String(e.message || e));
    return;
  }
  const opts = { ...audioOptions(), ...guidedPhraseOptions() };
  const body = {
    participant,
    tag,
    session_name: $("session_name").value.trim() || null,
    source,
    address: source === "ble" ? address : null,
    minutes: minutes != null && !Number.isNaN(minutes) && minutes > 0 ? minutes : null,
    opt_guided_phrases: opts.guidedPhrases,
    opt_audio_biofeedback: opts.audioBiofeedback,
  };

  try {
    audioMode = currentAudioMode();

    const res = await api("/api/sessions", { method: "POST", body: JSON.stringify(body) });
    currentSessionId = res.id;
    _sessionEndHandled = false;
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

    if (timed) {
      $("rr_plot_title").textContent = `RR — 0 … ${Math.round(durationSec)} с от старта`;
    } else {
      $("rr_plot_title").textContent = "RR — последние 60 с";
    }

    rrBuf = [];
    makeRRPlot($("rrPlot"), timed);
    setLiveEmptyState("waiting");
    nextFrame(resizePlots);

    setStatus(`Сессия #${currentSessionId} · ${tagLabel(body.tag)}${timed ? ` · ${body.minutes} мин` : " · скользящее окно"}`);
    loadSessionTypes().catch(() => {});
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
    onSessionEnded("Сессия остановлена.");
  } catch (e) {
    setErr(String(e.message || e));
    finalizeLiveSession();
  }
}

$("source")?.addEventListener("change", syncSourceFields);

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
  url = appendNoteTagFilters(url, fltNoteTagsInput);
  url = appendDateFilters(url, $("flt_period"));
  const { sessions } = await api(url);
  const tb = $("arch_rows");
  tb.innerHTML = "";
  for (const s of sessions) {
    const dur = s.ended ? Math.round((s.ended - s.started) / 60) : null;
    const tags = s.note_tags?.length ? s.note_tags : parseNoteTagsClient(s.session_name);
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td style="color:var(--text-dim);font-family:var(--mono);font-size:.8rem">${s.id}</td>` +
      `<td>${escapeHtml(s.participant || "")}</td>` +
      `<td>${tagPill(s.tag)}</td>` +
      `<td>${noteTagsHtml(tags)}</td>` +
      `<td style="color:var(--text-dim);font-size:.78rem">${escapeHtml(String(s.source).slice(0, 38))}</td>` +
      `<td style="font-size:.82rem">${fmtTime(s.started)}</td>` +
      `<td style="font-size:.82rem">${s.ended ? fmtTime(s.ended) + (dur ? ` <span style="color:var(--text-muted)">(${dur} мин)</span>` : "") : "<span style='color:var(--yellow)'>в процессе…</span>"}</td>` +
      `<td style="text-align:right"><button type="button" class="btn btn-danger btn-delete-session" data-id="${s.id}" style="font-size:.72rem;padding:4px 10px">Удалить</button></td>`;
    const isActive = !s.ended || s.id === currentSessionId;
    if (isActive) {
      tr.style.opacity = "0.6";
      tr.style.cursor = "not-allowed";
    } else {
      tr.addEventListener("click", () => openArchiveSession(s.id));
    }
    tr.querySelector(".btn-delete-session")?.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s.id);
    });
    tb.appendChild(tr);
  }
}

let archRR = null;
let archPoincare = null;
let archSpectrum = null;
let archSdnn = null;
let archRM = null;
let archAnalysisCache = null;
let archSummaryCache = null;

const STABLE_ZONE_KEY = "hrv_stable_zone";
const STABLE_ZONE_TRIM_SEC = 60;

function stableZoneEnabled() {
  const arch = $("arch_stable_zone");
  const prog = $("prog_stable_zone");
  if (arch) return arch.checked;
  if (prog) return prog.checked;
  if (localStorage.getItem(STABLE_ZONE_KEY) != null) {
    return localStorage.getItem(STABLE_ZONE_KEY) === "1";
  }
  return localStorage.getItem("hrv_chart_smooth") === "1";
}

function setStableZone(on) {
  localStorage.setItem(STABLE_ZONE_KEY, on ? "1" : "0");
  for (const id of ["arch_stable_zone", "prog_stable_zone"]) {
    const el = $(id);
    if (el) el.checked = on;
  }
}

function syncStableZoneCheckboxes() {
  const on = stableZoneEnabled();
  for (const id of ["arch_stable_zone", "prog_stable_zone"]) {
    const el = $(id);
    if (el) el.checked = on;
  }
}

function sessionAnalysisUrl(sessionId) {
  return `/api/sessions/${sessionId}/analysis?${stableZoneEnabled() ? "stable_zone=true" : "stable_zone=false"}`;
}

function rrPlotTrimOpts(analysis) {
  if (!analysis?.stable_zone || !analysis?.trim?.applied) return {};
  return {
    trim: {
      applied: true,
      start_sec: analysis.trim.start_sec ?? STABLE_ZONE_TRIM_SEC,
      end_sec: analysis.trim.end_sec ?? STABLE_ZONE_TRIM_SEC,
      duration_sec: analysis.duration_sec,
    },
  };
}

function renderArchNotes(sum) {
  const block = $("arch_notes_block");
  const textEl = $("arch_notes_text");
  if (!block || !textEl) return;
  const notes = (sum?.session_name || "").trim();
  const tags = sum?.note_tags?.length ? sum.note_tags : parseNoteTagsClient(notes);
  if (!notes) {
    block.hidden = true;
    textEl.textContent = "";
    return;
  }
  block.hidden = false;
  const tagsRow = tags.length
    ? `<div style="margin-bottom:8px">${noteTagsHtml(tags)}</div>`
    : "";
  textEl.innerHTML = tagsRow + escapeHtml(notes).replace(/\n/g, "<br>");
}

function renderSummaryGrid(sum) {
  const grid = $("arch_summary_grid");
  grid.innerHTML = "";
  const durMin = sum.duration_sec != null ? sum.duration_sec / 60 : null;
  const vsBl =
    sum.vs_baseline_pct != null
      ? (sum.vs_baseline_pct >= 0 ? "+" : "") + sum.vs_baseline_pct.toFixed(0) + "%"
      : "—";
  const stable = !!archAnalysisCache?.stable_zone;
  const meanRr = stable && archAnalysisCache?.mean_rr != null
    ? archAnalysisCache.mean_rr
    : sum.mean_rr;
  const coherence = stable && archAnalysisCache?.coherence_score != null
    ? archAnalysisCache.coherence_score
    : sum.coherence_score;
  const fields = [
    ["RMSSD mean",  sum.rmssd_mean != null ? sum.rmssd_mean.toFixed(1) + " ms" : "—"],
    ["RMSSD min",   sum.rmssd_min  != null ? sum.rmssd_min.toFixed(1)  + " ms" : "—"],
    ["RMSSD max",   sum.rmssd_max  != null ? sum.rmssd_max.toFixed(1)  + " ms" : "—"],
    ["Mean RR",     meanRr != null ? Number(meanRr).toFixed(1) + " ms" : "—"],
    ["Coherence",   coherence != null ? Number(coherence).toFixed(1) : "—"],
    ["Длительность", durMin != null ? durMin.toFixed(1) + " мин" : "—"],
    ["vs baseline", vsBl],
    ["Drift events", sum.drift_events != null ? String(sum.drift_events) : "—"],
    ["Guided meditation", sum.opt_guided_phrases ? "да" : "нет"],
    ["Аудио-биофидбек", sum.opt_audio_biofeedback ? "да" : "нет"],
  ];
  for (const [label, value] of fields) {
    const cell = document.createElement("div");
    cell.className = "summary-cell";
    cell.innerHTML = `<div class="s-label">${label}</div><div class="s-value">${value}</div>`;
    grid.appendChild(cell);
  }

  const metricsRow = $("arch_metrics_row");
  if (metricsRow) {
    metricsRow.innerHTML = "";
    const metrics = [
      ["Mean RR", meanRr != null ? Number(meanRr).toFixed(1) + " ms" : "—"],
      ["Coherence", coherence != null ? Number(coherence).toFixed(1) : "—"],
      ["SD1", archAnalysisCache?.poincare?.sd1 != null ? archAnalysisCache.poincare.sd1 + " ms" : "—"],
      ["Peak Hz", archAnalysisCache?.spectrum?.peak_freq != null ? archAnalysisCache.spectrum.peak_freq + " Гц" : "—"],
    ];
    for (const [label, value] of metrics) {
      const cell = document.createElement("div");
      cell.innerHTML = `<div class="s-label">${label}</div><div class="s-value">${value}</div>`;
      metricsRow.appendChild(cell);
    }
  }
}

function destroyArchPlots() {
  if (archRR) { archRR.destroy(); archRR = null; }
  if (archPoincare) { archPoincare.destroy(); archPoincare = null; }
  if (archSpectrum?.plot) { archSpectrum.plot.destroy(); archSpectrum = null; }
  if (archSdnn) { archSdnn.destroy(); archSdnn = null; }
  if (archRM) { archRM.destroy(); archRM = null; }
}

function renderArchRmssd(analysis) {
  const panel = $("arch_rmssd_panel");
  const mode = $("arch_rmssd_mode")?.value || "hidden";
  if (!panel) return;
  if (mode !== "show") {
    panel.classList.remove("visible");
    if (archRM) { archRM.destroy(); archRM = null; }
    return;
  }
  panel.classList.add("visible");
  const el = $("arch_rm");
  if (!el) return;
  el.innerHTML = "";
  if (archRM) { archRM.destroy(); archRM = null; }
  const charts = AC();
  if (!charts || !analysis?.rmssd_trend?.length) {
    charts?.setChartEmpty(el, "Недостаточно данных");
    return;
  }
  archRM = charts.makeRmssdPlot(el, analysis.rmssd_trend, analysis.duration_sec, ARCHIVE_PLOT_H);
}

function renderArchiveAnalysisCharts(analysis, sum) {
  const charts = AC();
  if (!charts || !analysis) return;

  const profile = chartProfileFor(sum?.tag);
  const activePanels = new Set(profile.panels);
  const stable = !!analysis.stable_zone;

  const rrEl = $("arch_rr");
  const pEl = $("arch_poincare");
  const sEl = $("arch_spectrum");
  const dEl = $("arch_sdnn");
  const panelEls = { rr: rrEl, sdnn: dEl, poincare: pEl, spectrum: sEl };

  for (const [panelKey, id] of Object.entries(ARCHIVE_PANEL_IDS)) {
    const el = panelEls[panelKey] || $(id);
    const card = el?.closest(".plot-card");
    if (card) card.hidden = !activePanels.has(panelKey);
    if (el) el.innerHTML = "";
  }

  const rrTitle = rrEl?.closest(".plot-card")?.querySelector(".plot-title");
  if (rrTitle) {
    rrTitle.textContent = stable
      ? `RR — полная сессия (анализ: ${STABLE_ZONE_TRIM_SEC}…${Math.round(Math.max(0, analysis.duration_sec - STABLE_ZONE_TRIM_SEC))} с)`
      : "RR — от начала сессии (raw)";
  }

  const rrOpts = {
    ...(profile.options.rr || {}),
    ...rrPlotTrimOpts(analysis),
  };

  if (activePanels.has("rr")) {
    const xs = analysis.raw_rr_x || [];
    const ys = analysis.raw_rr || [];
    if (ys.length && xs.length) {
      archRR = charts.makeRawRrPlot(
        rrEl, xs, ys, analysis.duration_sec,
        ARCHIVE_PLOT_H, rrOpts
      );
    } else if (rrEl) {
      charts.setChartEmpty(rrEl, "Нет данных RR");
    }
  }

  if (activePanels.has("poincare")) {
    const poincareRr = stable ? null : analysis?.raw_rr;
    const hasRawPoincare = !stable && poincareRr?.length >= 2;
    if (!hasRawPoincare && (analysis?.poincare?.insufficient_data || !analysis?.poincare?.points?.length)) {
      charts.setChartEmpty(pEl, analysis?.poincare?.message || "Недостаточно данных");
    } else {
      archPoincare = charts.makePoincarePlot(
        pEl,
        analysis?.poincare?.points,
        ARCHIVE_PLOT_H,
        analysis?.poincare?.bounds,
        poincareRr,
        profile.options.poincare
      );
    }
  }

  if (activePanels.has("spectrum")) {
    if (analysis?.spectrum?.insufficient_data || !analysis?.spectrum?.freqs?.length) {
      charts.setChartEmpty(sEl, analysis?.spectrum?.message || "Недостаточно данных");
      const marker = $("arch_peak_marker");
      if (marker) marker.style.display = "none";
    } else {
      archSpectrum = charts.makeSpectrumPlot(sEl, analysis.spectrum, ARCHIVE_PLOT_H, profile.options.spectrum);
      charts.positionPeakMarker(archSpectrum.plot, sEl.parentElement, archSpectrum.peakFreq);
    }
  }

  if (activePanels.has("sdnn")) {
    if (!analysis?.sdnn_trend?.length) {
      charts.setChartEmpty(dEl, "Недостаточно данных");
    } else {
      archSdnn = charts.makeSdnnPlot(dEl, analysis.sdnn_trend, analysis.duration_sec, ARCHIVE_PLOT_H, profile.options.sdnn);
    }
  }

  renderArchRmssd(analysis);
  nextFrame(resizePlots);
}

async function openArchiveSession(id) {
  if (id === currentSessionId) {
    setErr("Построение графиков для активной сессии недоступно. Завершите сессию сначала.");
    return;
  }
  const detail = $("arch_detail");
  detail.classList.add("visible");
  $("arch_id").textContent = String(id);
  const delBtn = $("btn_delete_arch_session");
  if (delBtn) {
    delBtn.hidden = false;
    delBtn.onclick = () => deleteSession(id);
  }

  destroyArchPlots();
  archAnalysisCache = null;
  archSummaryCache = null;

  let sum = null;
  try {
    sum = await api(`/api/sessions/${id}`);
  } catch {
    $("arch_summary_grid").innerHTML = "<p style='color:var(--text-dim);font-size:.8rem'>Сводка недоступна (сессия ещё идёт?)</p>";
  }

  let analysis = null;
  try {
    analysis = await api(sessionAnalysisUrl(id));
    archAnalysisCache = analysis;
  } catch (e) {
    setErr(String(e.message || e));
  }

  if (sum) {
    archSummaryCache = sum;
    renderSummaryGrid(sum);
    renderArchNotes(sum);
  }

  if (analysis) {
    renderArchiveAnalysisCharts(analysis, sum);
  }

  detail.scrollIntoView({ behavior: "smooth", block: "start" });
}

function rerenderArchiveCharts() {
  const id = Number($("arch_id")?.textContent);
  if (!id || !archSummaryCache) return;
  api(sessionAnalysisUrl(id))
    .then((analysis) => {
      archAnalysisCache = analysis;
      destroyArchPlots();
      renderSummaryGrid(archSummaryCache);
      renderArchiveAnalysisCharts(analysis, archSummaryCache);
    })
    .catch((e) => setErr(String(e.message || e)));
}

$("arch_stable_zone")?.addEventListener("change", (ev) => {
  setStableZone(ev.target.checked);
  rerenderArchiveCharts();
});

$("arch_rmssd_mode")?.addEventListener("change", () => {
  renderArchRmssd(archAnalysisCache);
  nextFrame(resizePlots);
});

$("btn_reload").addEventListener("click", loadArchive);
["flt_participant", "flt_tag", "flt_period"].forEach((id) => {
  $(id)?.addEventListener("change", () => { loadArchive().catch((e) => setErr(String(e.message || e))); });
  if (id === "flt_participant") {
    $(id)?.addEventListener("input", () => {
      clearTimeout(window._fltArchT);
      window._fltArchT = setTimeout(() => {
        loadArchive().catch((e) => setErr(String(e.message || e)));
      }, 350);
    });
  }
});

// ── PROGRESS ──────────────────────────────────────────────────────────────
const PROG_COLORS = [
  "#00d4ff", "#39e085", "#9d8ef0", "#f5c542", "#ff6b6b",
  "#ff9f43", "#54a0ff", "#5f27cd", "#01a3a4", "#f368e0",
  "#10ac84", "#ee5a24", "#2e86de", "#8395a7", "#222f3e",
];

let progPoincare = null;
let progSpectrum = null;
let progSpectrumPeakFreq = null;
let progSdnn = null;
let progSessionsRaw = [];
let progVisible = new Set();

function setProgErr(txt) {
  const el = $("prog_err");
  if (!el) return;
  el.textContent = txt;
  el.classList.toggle("visible", !!txt);
}

function destroyProgPlots() {
  if (progPoincare) { progPoincare.destroy(); progPoincare = null; }
  if (progSpectrum) { progSpectrum.destroy(); progSpectrum = null; }
  progSpectrumPeakFreq = null;
  const progMarker = $("prog_peak_marker");
  if (progMarker) progMarker.style.display = "none";
  if (progSdnn) { progSdnn.destroy(); progSdnn = null; }
}

function fmtShortDate(ts) {
  return new Date(ts * 1000).toLocaleDateString("ru-RU");
}

function renderProgCompareTable(sessions) {
  const tb = $("prog_compare_rows");
  if (!tb) return;
  tb.innerHTML = "";
  progVisible = new Set(sessions.map((s) => s.id));

  sessions.forEach((s, i) => {
    const color = PROG_COLORS[i % PROG_COLORS.length];
    const tr = document.createElement("tr");
    const checked = progVisible.has(s.id);
    tr.innerHTML =
      `<td><input type="checkbox" class="prog-session-cb" data-id="${s.id}" ${checked ? "checked" : ""} /></td>` +
      `<td><span class="compare-swatch" style="background:${color}"></span>#${s.id}</td>` +
      `<td>${fmtShortDate(s.started)}</td>` +
      `<td>${s.mean_rr != null ? s.mean_rr.toFixed(1) : "—"}</td>` +
      `<td>${s.rmssd_mean != null ? s.rmssd_mean.toFixed(1) : "—"}</td>` +
      `<td>${s.coherence_score != null ? s.coherence_score.toFixed(1) : "—"}</td>`;
    tr.querySelector(".prog-session-cb")?.addEventListener("change", (ev) => {
      const sid = Number(ev.target.dataset.id);
      if (ev.target.checked) progVisible.add(sid);
      else progVisible.delete(sid);
      buildProgressPlots();
    });
    tb.appendChild(tr);
  });
}

function buildProgressPlots() {
  destroyProgPlots();
  const charts = AC();
  const emptyEl = $("prog_empty");
  const sessions = progSessionsRaw;
  if (!sessions.length) {
    if (emptyEl) emptyEl.hidden = false;
    return;
  }
  if (emptyEl) emptyEl.hidden = true;
  if (!charts) return;

  const pEl = $("prog_poincare");
  const sEl = $("prog_spectrum");
  const dEl = $("prog_sdnn");
  if (pEl) pEl.innerHTML = "";
  if (sEl) sEl.innerHTML = "";
  if (dEl) dEl.innerHTML = "";

  const visible = progVisible;
  if (!visible.size) {
    charts.setChartEmpty(pEl, "Выберите сессии в таблице");
    charts.setChartEmpty(sEl, "Выберите сессии в таблице");
    charts.setChartEmpty(dEl, "Выберите сессии в таблице");
    return;
  }

  progPoincare = charts.buildProgressPoincarePlot(pEl, sessions, visible, PROG_COLORS, PROGRESS_PLOT_H);
  if (!progPoincare) charts.setChartEmpty(pEl, "Нет данных Poincaré");

  const spectrumResult = charts.buildProgressSpectrumPlot(sEl, sessions, visible, PROG_COLORS, PROGRESS_PLOT_H);
  if (!spectrumResult) {
    charts.setChartEmpty(sEl, "Нет спектральных данных");
    const marker = $("prog_peak_marker");
    if (marker) marker.style.display = "none";
  } else {
    progSpectrum = spectrumResult.plot;
    progSpectrumPeakFreq = spectrumResult.peakFreq ?? null;
    charts.positionPeakMarker(progSpectrum, sEl.parentElement, progSpectrumPeakFreq);
  }

  progSdnn = charts.buildProgressSdnnPlot(dEl, sessions, visible, PROG_COLORS, PROGRESS_PLOT_H);
  if (!progSdnn) charts.setChartEmpty(dEl, "Нет тренда SDNN");

  nextFrame(resizePlots);
}

async function loadProgress() {
  setProgErr("");
  const tag = $("prog_tag")?.value || "";
  const participant = $("prog_participant")?.value?.trim() || "";
  let url = "/api/progress/analysis?max_sessions=40&max_points_per_session=12000";
  if (stableZoneEnabled()) url += "&stable_zone=true";
  if (tag) url += `&tag=${encodeURIComponent(tag)}`;
  url = appendNoteTagFilters(url, progNoteTagsInput);
  url = appendDateFilters(url, $("prog_period"));
  if (participant) url += `&participant=${encodeURIComponent(participant)}`;

  try {
    const { sessions } = await api(url);
    progSessionsRaw = sessions;
    renderProgCompareTable(sessions);
    buildProgressPlots();
  } catch (e) {
    setProgErr(String(e.message || e));
    destroyProgPlots();
  }
}

$("btn_prog_build")?.addEventListener("click", loadProgress);

$("prog_stable_zone")?.addEventListener("change", (ev) => {
  setStableZone(ev.target.checked);
  if (progSessionsRaw.length) loadProgress().catch((e) => setProgErr(String(e.message || e)));
});

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
      destroyArchPlots();
      archAnalysisCache = null;
      archSummaryCache = null;
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
    renderProgCompareTable([]);
    buildProgressPlots();
    $("arch_detail")?.classList.remove("visible");
    $("btn_delete_arch_session")?.setAttribute("hidden", "");
    $("arch_rows").innerHTML = "";
    await loadSessionTypes();
    await loadArchive();
    setProgErr("");
    setStatus("История полностью очищена.");
  } catch (e) {
    setProgErr(String(e.message || e));
  }
}

$("btn_wipe_history")?.addEventListener("click", wipeHistory);
$("btn_wipe_history_prog")?.addEventListener("click", wipeHistory);

loadSessionTypes().catch(e => setErr(String(e)));
syncSourceFields();
syncGuidedPhraseOptionsVisibility();
syncStableZoneCheckboxes();
setLiveEmptyState("idle");