/**
 * Архивный плеер: клик по RR → seek, playhead на uPlot, Play/Pause.
 * Ось RR и audio.currentTime — секунды от arm (t₀); offset — только arm→recorder (<1 с).
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
        scrubber: null,
      };
      this._pointer = null;
      this._scrubbing = false;
      this._audioOffset = 0;
      this._prevCursor = null;
    }

    /**
     * @param {{ sessionId: number, wrapEl: HTMLElement, playBtn: HTMLElement,
     *           timeEl: HTMLElement, scrubber: HTMLInputElement }} opts
     */
    bindUi(opts) {
      this._ui.wrap = opts.wrapEl || null;
      this._ui.playBtn = opts.playBtn || null;
      this._ui.timeEl = opts.timeEl || null;
      this._ui.scrubber = opts.scrubber || null;
      if (this._ui.playBtn) {
        this._ui.playBtn.onclick = () => this.togglePlay();
      }
      if (this._ui.scrubber) {
        this._ui.scrubber.addEventListener("input", (e) => {
          this._scrubbing = true;
          const val = parseFloat(e.target.value);
          if (!Number.isFinite(val)) return;
          this.seekTo(val);
        });
        this._ui.scrubber.addEventListener("change", () => {
          this._scrubbing = false;
        });
      }
    }

    /**
     * Локальная задержка arm → MediaRecorder.start() (сек). Большие значения игнорируются.
     * @param {number} offsetSec
     */
    setAudioOffset(_offsetSec) {
      this._audioOffset = 0;
    }

    _audioNow() {
      if (!this._audio) return 0;
      const t = this._audio.currentTime;
      return Number.isFinite(t) ? t : 0;
    }

    _sessionSec() {
      return this._audioNow();
    }

    /**
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

      const onTime = () => {
        this._playheadSec = this._sessionSec();
        this._syncUi();
        this._redrawPlot();
      };

      audio.addEventListener("timeupdate", onTime);
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
      audio.addEventListener("loadedmetadata", onTime);

      this._setVisible(true);
      this._syncUi();
      return true;
    }

    attachToPlot(plot) {
      this.detachPlot();
      if (!plot?.over || !this._audio) return;
      this._plot = plot;

      this._prevCursor = plot.cursor;
      try {
        plot.setCursor({ show: false });
      } catch (_) {
        /* ignore */
      }

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
      const plot = this._plot;
      const prevCursor = this._prevCursor;
      if (plot && this._drawHook && plot.hooks?.draw) {
        const idx = plot.hooks.draw.indexOf(this._drawHook);
        if (idx >= 0) plot.hooks.draw.splice(idx, 1);
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
      if (plot && prevCursor) {
        try {
          plot.setCursor(prevCursor);
        } catch (_) {
          /* ignore */
        }
      }
      this._prevCursor = null;
    }

    seekTo(sec) {
      const audio = this._audio;
      if (!audio) return;
      const dur = Number.isFinite(audio.duration) ? audio.duration : Infinity;
      const t = Math.max(0, Math.min(sec, dur));
      this._playheadSec = sec;
      try {
        audio.currentTime = t;
      } catch (_) {
        /* ignore seek errors before metadata */
      }
      this._syncUi();
      this._redrawPlot();
    }

    seekAndPlay(sec) {
      this.seekTo(sec);
      const audio = this._audio;
      if (!audio) return;
      const playPromise = audio.play();
      if (playPromise?.catch) playPromise.catch(() => {});
      this._syncUi();
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
      const sessionSec = this._sessionSec();
      const dur =
        audio && Number.isFinite(audio.duration) ? audio.duration : null;

      if (this._ui.timeEl) {
        this._ui.timeEl.textContent =
          dur != null
            ? `${fmtClock(sessionSec)} / ${fmtClock(dur)}`
            : fmtClock(sessionSec);
      }

      if (this._ui.scrubber && !this._scrubbing && dur != null) {
        this._ui.scrubber.max = String(dur);
        this._ui.scrubber.value = String(sessionSec);
      }
    }

    _drawPlayhead(u) {
      const t = this._playheadSec;
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
        this._playheadSec = this._sessionSec();
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
