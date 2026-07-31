/**
 * Архивный плеер: клик по RR → seek, playhead на uPlot, Play/Pause.
 */
(function (global) {
  const CLICK_MAX_MOVE_PX = 6;

  function fmtClock(sec) {
    if (!Number.isFinite(sec) || sec < 0) sec = 0;
    const s = Math.floor(sec);
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, "0")}`;
  }

  class SessionAudioPlayer {
    constructor() {
      this._audio = null;
      this._plot = null;
      this._playheadSec = 0;
      this._raf = null;
      this._drawHook = null;
      this._cleanups = [];
      this._ui = {
        wrap: null,
        playBtn: null,
        timeEl: null,
      };
      this._pointer = null;
    }

    /**
     * @param {{ sessionId: number, wrapEl: HTMLElement, playBtn: HTMLElement,
     *           timeEl: HTMLElement }} opts
     */
    bindUi(opts) {
      this._ui.wrap = opts.wrapEl || null;
      this._ui.playBtn = opts.playBtn || null;
      this._ui.timeEl = opts.timeEl || null;
      if (this._ui.playBtn) {
        this._ui.playBtn.onclick = () => this.togglePlay();
      }
    }

    /**
     * Показать/загрузить аудио для сессии. Скрыть, если hasAudio=false.
     * @returns {Promise<boolean>} true если плеер активен
     */
    async load(sessionId, hasAudio) {
      this.detachPlot();
      this._teardownAudio();
      this._playheadSec = 0;
      if (!hasAudio || !sessionId) {
        this._setVisible(false);
        this._syncUi();
        return false;
      }
      const audio = new Audio();
      audio.preload = "metadata";
      audio.src = `/api/sessions/${sessionId}/audio`;
      this._audio = audio;

      audio.addEventListener("timeupdate", () => {
        this._playheadSec = audio.currentTime || 0;
        this._syncUi();
        this._redrawPlot();
      });
      audio.addEventListener("play", () => {
        this._startRaf();
        this._syncUi();
      });
      audio.addEventListener("pause", () => {
        this._stopRaf();
        this._syncUi();
      });
      audio.addEventListener("ended", () => {
        this._stopRaf();
        this._syncUi();
      });
      audio.addEventListener("loadedmetadata", () => this._syncUi());

      this._setVisible(true);
      this._syncUi();
      return true;
    }

    /** Привязать click-to-seek и playhead к архивному RR-плоту. */
    attachToPlot(plot) {
      this.detachPlot();
      if (!plot?.over || !this._audio) return;
      this._plot = plot;

      this._drawHook = (u) => this._drawPlayhead(u);
      if (!plot.hooks.draw) plot.hooks.draw = [];
      plot.hooks.draw.push(this._drawHook);

      const over = plot.over;
      const onDown = (e) => {
        if (e.button != null && e.button !== 0) return;
        this._pointer = { x: e.clientX, y: e.clientY, moved: false };
      };
      const onMove = (e) => {
        if (!this._pointer) return;
        const dx = e.clientX - this._pointer.x;
        const dy = e.clientY - this._pointer.y;
        if (dx * dx + dy * dy > CLICK_MAX_MOVE_PX * CLICK_MAX_MOVE_PX) {
          this._pointer.moved = true;
        }
      };
      const onUp = (e) => {
        const p = this._pointer;
        this._pointer = null;
        if (!p || p.moved) return;
        if (e.button != null && e.button !== 0) return;
        const rect = over.getBoundingClientRect();
        const left = e.clientX - rect.left;
        if (left < 0 || left > rect.width) return;
        const xVal = plot.posToVal(left, "x");
        if (!Number.isFinite(xVal)) return;
        this.seekAndPlay(xVal);
      };

      over.addEventListener("pointerdown", onDown);
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      this._cleanups.push(() => over.removeEventListener("pointerdown", onDown));
      this._cleanups.push(() => window.removeEventListener("pointermove", onMove));
      this._cleanups.push(() => window.removeEventListener("pointerup", onUp));

      this._redrawPlot();
    }

    detachPlot() {
      this._stopRaf();
      if (this._plot && this._drawHook && this._plot.hooks?.draw) {
        const idx = this._plot.hooks.draw.indexOf(this._drawHook);
        if (idx >= 0) this._plot.hooks.draw.splice(idx, 1);
      }
      for (const fn of this._cleanups) {
        try {
          fn();
        } catch (_) {
          /* ignore */
        }
      }
      this._cleanups = [];
      this._drawHook = null;
      this._plot = null;
      this._pointer = null;
    }

    seekAndPlay(sec) {
      const audio = this._audio;
      if (!audio) return;
      const dur = Number.isFinite(audio.duration) ? audio.duration : Infinity;
      const t = Math.max(0, Math.min(sec, dur));
      this._playheadSec = t;
      try {
        audio.currentTime = t;
      } catch (_) {
        /* ignore seek errors before metadata */
      }
      const playPromise = audio.play();
      if (playPromise?.catch) playPromise.catch(() => {});
      this._syncUi();
      this._redrawPlot();
    }

    togglePlay() {
      const audio = this._audio;
      if (!audio) return;
      if (audio.paused) {
        const playPromise = audio.play();
        if (playPromise?.catch) playPromise.catch(() => {});
      } else {
        audio.pause();
      }
      this._syncUi();
    }

    destroy() {
      this.detachPlot();
      this._teardownAudio();
      this._setVisible(false);
      this._syncUi();
    }

    _teardownAudio() {
      this._stopRaf();
      if (this._audio) {
        try {
          this._audio.pause();
        } catch (_) {
          /* ignore */
        }
        this._audio.removeAttribute("src");
        this._audio.load();
      }
      this._audio = null;
    }

    _setVisible(on) {
      if (this._ui.wrap) this._ui.wrap.hidden = !on;
    }

    _syncUi() {
      const audio = this._audio;
      const playing = !!(audio && !audio.paused && !audio.ended);
      if (this._ui.playBtn) {
        this._ui.playBtn.textContent = playing ? "⏸ Пауза" : "▶ Слушать";
        this._ui.playBtn.setAttribute(
          "aria-label",
          playing ? "Пауза" : "Воспроизвести"
        );
      }
      if (this._ui.timeEl) {
        const cur = audio ? audio.currentTime || this._playheadSec : this._playheadSec;
        const dur = audio && Number.isFinite(audio.duration) ? audio.duration : null;
        this._ui.timeEl.textContent = dur != null
          ? `${fmtClock(cur)} / ${fmtClock(dur)}`
          : fmtClock(cur);
      }
    }

    _drawPlayhead(u) {
      const t = this._audio ? this._audio.currentTime || this._playheadSec : this._playheadSec;
      if (!Number.isFinite(t)) return;
      const xMin = u.scales.x.min;
      const xMax = u.scales.x.max;
      if (t < xMin || t > xMax) return;
      const cx = u.valToPos(t, "x", true);
      const { left, top, width, height } = u.bbox;
      const ctx = u.ctx;
      ctx.save();
      ctx.beginPath();
      ctx.strokeStyle = "rgba(220, 80, 60, 0.9)";
      ctx.lineWidth = 2 * (devicePixelRatio || 1);
      ctx.moveTo(left + cx, top);
      ctx.lineTo(left + cx, top + height);
      ctx.stroke();
      ctx.restore();
      void width;
    }

    _redrawPlot() {
      if (this._plot) {
        try {
          this._plot.redraw(false, false);
        } catch (_) {
          /* ignore */
        }
      }
    }

    _startRaf() {
      if (this._raf) return;
      const tick = () => {
        this._raf = null;
        if (!this._audio || this._audio.paused) return;
        this._playheadSec = this._audio.currentTime || 0;
        this._syncUi();
        this._redrawPlot();
        this._raf = requestAnimationFrame(tick);
      };
      this._raf = requestAnimationFrame(tick);
    }

    _stopRaf() {
      if (this._raf) {
        cancelAnimationFrame(this._raf);
        this._raf = null;
      }
    }
  }

  global.SessionAudioPlayer = SessionAudioPlayer;
})(typeof window !== "undefined" ? window : globalThis);
