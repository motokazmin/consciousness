/* global window, fetch */

/**
 * HRV-реактивное воспроизведение заранее записанных mp3-фраз для guided meditation.
 *
 * Файлы: /assets/phrases/{prefix}/{set}/{prefix}_{v|ya|u|z|vykh}_{NN}.mp3
 * Префикс: meditation → sit, rest (релаксация) → lay
 * Список доступных файлов загружается с GET /api/meditation/phrase-manifest.
 */
class MeditationEngine {
  static DEFAULT_MIN_INTERVAL_SEC = 90;
  static RN_WINDOW_SEC = 30;
  static EXIT_BEFORE_SEC = 60;
  static ENTRY_GAP_SEC = 2;

  constructor() {
    this._sessionId = null;
    this._sessionType = "sit";
    this._phraseSet = "directive";
    this._durationSec = null;
    this._sessionStartTs = null;
    this._running = false;
    this._ready = false;

    this._manifest = {};
    this._entryIndices = [];
    this._exitIndices = [];

    this._phase = "entry";
    this._entryIndex = 0;
    this._exitIndex = 0;
    this._currentCategory = "ya";
    this._queue = [];
    this._lastPhraseFile = null;
    this._lastPhraseEndTs = 0;

    this._rnHistory = [];
    this._lastRn = null;
    this._lastRmssd = null;

    this._audio = new Audio();
    this._playing = false;
    this._pendingTimers = [];
    this._audio.addEventListener("ended", () => {
      this._playing = false;
      this._lastPhraseEndTs = Date.now() / 1000;
    });
    this._audio.addEventListener("error", () => {
      this._playing = false;
      this._lastPhraseEndTs = Date.now() / 1000;
    });
  }

  async start(sessionId, sessionType, durationMinutes, minIntervalSec, phraseSet) {
    this.stop();
    this._sessionId = sessionId;
    this._sessionType = sessionType || "sit";
    this._phraseSet = phraseSet || "directive";
    this._minIntervalSec =
      minIntervalSec != null && minIntervalSec >= 5
        ? minIntervalSec
        : MeditationEngine.DEFAULT_MIN_INTERVAL_SEC;
    this._durationSec =
      durationMinutes != null && durationMinutes > 0 ? durationMinutes * 60 : null;
    this._sessionStartTs = Date.now() / 1000;
    this._running = true;
    this._ready = false;
    this._phase = "entry";
    this._entryIndex = 0;
    this._exitIndex = 0;
    this._currentCategory = "ya";
    this._queue = [];
    this._lastPhraseFile = null;
    this._lastPhraseEndTs = 0;
    this._rnHistory = [];
    this._lastRn = null;
    this._lastRmssd = null;

    try {
      const qs = new URLSearchParams({
        prefix: this._sessionType,
        set: this._phraseSet,
      });
      const res = await fetch(`/api/meditation/phrase-manifest?${qs}`);
      this._manifest = res.ok ? await res.json() : {};
    } catch {
      this._manifest = {};
    }

    this._entryIndices = [...(this._manifest.v || [])].sort((a, b) => a - b);
    this._exitIndices = [...(this._manifest.vykh || [])].sort((a, b) => a - b);

    if (!this._entryIndices.length) {
      this._phase = "active";
    }

    this._ready = true;
  }

  stop() {
    this._running = false;
    this._ready = false;
    this._audio.pause();
    this._audio.removeAttribute("src");
    this._playing = false;
    for (const id of this._pendingTimers) clearTimeout(id);
    this._pendingTimers = [];
  }

  processFrame(msg) {
    if (!this._running || !this._ready || msg?.type !== "beat" || !msg.t?.length) return;

    for (let i = 0; i < msg.t.length; i++) {
      const ts = msg.t[i];
      const rn = msg.rn?.[i] ?? null;
      const rmssd = msg.m?.[i] ?? null;
      if (rn != null) this._lastRn = rn;
      if (rmssd != null) this._lastRmssd = rmssd;
      if (rn != null) {
        this._rnHistory.push({ ts, rn, rmssd });
      }
    }
    this._trimRnHistory();
    this._tick();
  }

  _trimRnHistory() {
    const cutoff = Date.now() / 1000 - MeditationEngine.RN_WINDOW_SEC;
    while (this._rnHistory.length && this._rnHistory[0].ts < cutoff) {
      this._rnHistory.shift();
    }
  }

  _elapsedSec() {
    return Date.now() / 1000 - (this._sessionStartTs || 0);
  }

  _timeRemainingSec() {
    if (this._durationSec == null) return Infinity;
    return this._durationSec - this._elapsedSec();
  }

  _indices(category) {
    return this._manifest[category] || [];
  }

  _assetsBase() {
    return `/assets/phrases/${this._sessionType}/${this._phraseSet}`;
  }

  _phrasePath(category, index) {
    const num = String(index).padStart(2, "0");
    return `${this._assetsBase()}/${this._sessionType}_${category}_${num}.mp3`;
  }

  _phraseFilename(category, index) {
    const num = String(index).padStart(2, "0");
    return `${this._phraseSet}/${this._sessionType}_${category}_${num}.mp3`;
  }

  _shuffle(arr) {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  _buildQueue(category) {
    let indices = [...this._indices(category)];
    if (!indices.length) {
      this._queue = [];
      return;
    }
    indices = this._shuffle(indices);
    if (this._lastPhraseFile && indices.length > 1) {
      const lastIdx = this._lastIndexFromFile(this._lastPhraseFile);
      if (lastIdx != null && indices[0] === lastIdx) {
        [indices[0], indices[1]] = [indices[1], indices[0]];
      }
    }
    this._queue = indices.map((idx) => ({ category, index: idx }));
  }

  _lastIndexFromFile(filename) {
    const m = filename.match(/_(\d+)\.mp3$/);
    return m ? parseInt(m[1], 10) : null;
  }

  _linearSlope(points) {
    if (points.length < 2) return 0;
    const n = points.length;
    let sumX = 0;
    let sumY = 0;
    let sumXY = 0;
    let sumXX = 0;
    for (const [x, y] of points) {
      sumX += x;
      sumY += y;
      sumXY += x * y;
      sumXX += x * x;
    }
    const denom = n * sumXX - sumX * sumX;
    if (Math.abs(denom) < 1e-9) return 0;
    return (n * sumXY - sumX * sumY) / denom;
  }

  _computeCategory() {
    const hist = this._rnHistory.filter((h) => h.rn != null);
    if (hist.length < 3) return this._currentCategory;

    const rns = hist.map((h) => h.rn);
    const mean = rns.reduce((a, b) => a + b, 0) / rns.length;
    const slope = this._linearSlope(hist.map((h) => [h.ts, h.rn]));

    const pick = (cat) => (this._indices(cat).length ? cat : null);

    if (mean < 0.8 || slope < -0.003) return pick("z") || pick("ya") || this._currentCategory;
    if (mean >= 1.5 && slope > 0.003) return pick("u") || pick("ya") || this._currentCategory;
    if (mean >= 0.8 && mean <= 1.5) return pick("ya") || this._currentCategory;
    if (mean >= 1.5) return pick("ya") || this._currentCategory;
    return pick("z") || pick("ya") || this._currentCategory;
  }

  _canPlayNow(minIntervalSec) {
    if (this._playing) return false;
    const now = Date.now() / 1000;
    if (this._lastPhraseEndTs > 0 && now - this._lastPhraseEndTs < minIntervalSec) {
      return false;
    }
    return true;
  }

  _tick() {
    if (!this._running || !this._ready || this._playing) return;

    const remaining = this._timeRemainingSec();
    if (
      this._durationSec != null &&
      remaining <= MeditationEngine.EXIT_BEFORE_SEC &&
      this._phase !== "exit" &&
      this._exitIndices.length
    ) {
      this._phase = "exit";
      this._exitIndex = 0;
    }

    if (this._phase === "exit") {
      this._tryPlayExit();
      return;
    }

    if (this._phase === "entry") {
      this._tryPlayEntry();
      return;
    }

    this._tryPlayReactive();
  }

  _tryPlayEntry() {
    const gap = this._lastPhraseEndTs === 0 ? 0 : MeditationEngine.ENTRY_GAP_SEC;
    if (!this._canPlayNow(gap)) return;

    if (this._entryIndex >= this._entryIndices.length) {
      this._phase = "active";
      this._currentCategory = this._computeCategory();
      this._buildQueue(this._currentCategory);
      return;
    }

    const index = this._entryIndices[this._entryIndex];
    this._entryIndex += 1;
    this._playPhrase(this._phraseFilename("v", index), "v", index);
  }

  _tryPlayExit() {
    if (!this._canPlayNow(MeditationEngine.ENTRY_GAP_SEC)) return;

    if (this._exitIndex >= this._exitIndices.length) return;

    const index = this._exitIndices[this._exitIndex];
    this._exitIndex += 1;
    this._playPhrase(this._phraseFilename("vykh", index), "vykh", index);
  }

  _tryPlayReactive() {
    if (!this._canPlayNow(this._minIntervalSec)) return;

    const nextCat = this._computeCategory();
    if (nextCat !== this._currentCategory || !this._queue.length) {
      this._currentCategory = nextCat;
      this._buildQueue(nextCat);
    }

    const item = this._queue.shift();
    if (!item) return;

    this._playPhrase(
      this._phraseFilename(item.category, item.index),
      item.category,
      item.index,
    );
  }

  _playPhrase(filename, category, index) {
    const rnBefore = this._lastRn;
    const rmssdBefore = this._lastRmssd;
    const src = this._phrasePath(category, index);

    this._playing = true;
    this._lastPhraseFile = filename;
    this._audio.src = src;
    this._audio
      .play()
      .then(() => {
        this._logPhrase(filename, rnBefore, rmssdBefore);
      })
      .catch(() => {
        this._playing = false;
        this._lastPhraseEndTs = Date.now() / 1000;
      });
  }

  _logPhrase(phraseFile, rnBefore, rmssdBefore) {
    const playedAt = Date.now() / 1000;
    const body = {
      session_id: this._sessionId,
      phrase_file: phraseFile,
      played_at: playedAt,
      rn_before: rnBefore,
      rmssd_before: rmssdBefore,
      rn_after_30s: null,
      rmssd_after_30s: null,
    };

    fetch("/api/meditation/phrase-log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((res) => {
        if (!res?.id) return;
        const timerId = setTimeout(() => {
          this._patchAfter30s(res.id);
        }, 30_000);
        this._pendingTimers.push(timerId);
      })
      .catch(() => {});
  }

  _patchAfter30s(logId) {
    fetch(`/api/meditation/phrase-log/${logId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        rn_after_30s: this._lastRn,
        rmssd_after_30s: this._lastRmssd,
      }),
    }).catch(() => {});
  }
}

window.MeditationEngine = MeditationEngine;
