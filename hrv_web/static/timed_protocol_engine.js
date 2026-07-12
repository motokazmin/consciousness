/* global window, fetch */

/**
 * Последовательное воспроизведение mp3 по расписанию (файл → пауза → следующий).
 * Расписание: /assets/phrases/{prefix}/{set}/release_schedule.json
 */
class TimedProtocolEngine {
  constructor() {
    this._sessionId = null;
    this._prefix = "release";
    this._phraseSet = "soft";
    this._cues = [];
    this._cueIndex = 0;
    this._running = false;
    this._ready = false;
    this._onComplete = null;

    this._lastRn = null;
    this._lastRmssd = null;

    this._audio = new Audio();
    this._playing = false;
    this._pendingTimers = [];
    this._audio.addEventListener("ended", () => {
      this._playing = false;
      this._afterCueEnded();
    });
    this._audio.addEventListener("error", () => {
      this._playing = false;
      this._afterCueEnded();
    });
  }

  async start(sessionId, prefix, phraseSet, onComplete) {
    this.stop();
    this._sessionId = sessionId;
    this._prefix = prefix || "release";
    this._phraseSet = phraseSet || "soft";
    this._onComplete = typeof onComplete === "function" ? onComplete : null;
    this._cueIndex = 0;
    this._running = true;
    this._ready = false;
    this._lastRn = null;
    this._lastRmssd = null;

    try {
      const url = `/assets/phrases/${this._prefix}/${this._phraseSet}/release_schedule.json`;
      const res = await fetch(url);
      const data = res.ok ? await res.json() : {};
      this._cues = Array.isArray(data.cues) ? data.cues : [];
    } catch {
      this._cues = [];
    }

    this._ready = true;
    if (!this._cues.length) {
      this._finish();
      return;
    }
    this._playCurrent();
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
      const rn = msg.rn?.[i] ?? null;
      const rmssd = msg.m?.[i] ?? null;
      if (rn != null) this._lastRn = rn;
      if (rmssd != null) this._lastRmssd = rmssd;
    }
  }

  _assetsBase() {
    return `/assets/phrases/${this._prefix}/${this._phraseSet}`;
  }

  _playCurrent() {
    if (!this._running || !this._ready || this._playing) return;
    if (this._cueIndex >= this._cues.length) {
      this._finish();
      return;
    }

    const cue = this._cues[this._cueIndex];
    const file = cue?.file;
    if (!file) {
      this._cueIndex += 1;
      this._playCurrent();
      return;
    }

    const rnBefore = this._lastRn;
    const rmssdBefore = this._lastRmssd;
    this._playing = true;
    this._audio.src = `${this._assetsBase()}/${file}`;
    this._audio
      .play()
      .then(() => {
        this._logPhrase(file, rnBefore, rmssdBefore);
      })
      .catch(() => {
        this._playing = false;
        this._afterCueEnded();
      });
  }

  _afterCueEnded() {
    if (!this._running) return;

    const cue = this._cues[this._cueIndex];
    const pauseSec = cue?.pause_after_sec ?? 0;
    this._cueIndex += 1;

    if (this._cueIndex >= this._cues.length) {
      this._finish();
      return;
    }

    if (pauseSec > 0) {
      const timerId = setTimeout(() => {
        this._playCurrent();
      }, pauseSec * 1000);
      this._pendingTimers.push(timerId);
    } else {
      this._playCurrent();
    }
  }

  _finish() {
    this._running = false;
    const cb = this._onComplete;
    this._onComplete = null;
    if (cb) cb();
  }

  _logPhrase(phraseFile, rnBefore, rmssdBefore) {
    const playedAt = Date.now() / 1000;
    const body = {
      session_id: this._sessionId,
      phrase_file: `${this._phraseSet}/${phraseFile}`,
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

window.TimedProtocolEngine = TimedProtocolEngine;
