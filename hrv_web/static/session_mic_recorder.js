/**
 * Запись микрофона сессии (MediaRecorder) с момента arm.
 * Не связан с hrv_audio_engine (синтез биофидбека).
 */
(function (global) {
  const MIME_CANDIDATES = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/ogg",
  ];

  function pickMimeType() {
    if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) {
      return "";
    }
    for (const mime of MIME_CANDIDATES) {
      if (MediaRecorder.isTypeSupported(mime)) return mime;
    }
    return "";
  }

  class SessionMicRecorder {
    constructor() {
      this._stream = null;
      this._recorder = null;
      this._chunks = [];
      this._mimeType = "";
      this._started = false;
      this._status = "off"; // off | pending | recording | denied | error | stopped
    }

    get status() {
      return this._status;
    }

    get recording() {
      return this._started && this._recorder?.state === "recording";
    }

    /**
     * Запросить микрофон на user gesture (кнопка Старт).
     * @returns {Promise<"pending"|"denied"|"error">}
     */
    async prepare() {
      this.reset();
      if (!navigator.mediaDevices?.getUserMedia) {
        this._status = "error";
        return this._status;
      }
      try {
        this._stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
          video: false,
        });
        this._status = "pending";
        return this._status;
      } catch (e) {
        this._status = e?.name === "NotAllowedError" ? "denied" : "error";
        this._stream = null;
        return this._status;
      }
    }

    /** Старт MediaRecorder в момент arm (t₀ сессии). */
    start() {
      if (this._started || !this._stream) return false;
      if (typeof MediaRecorder === "undefined") {
        this._status = "error";
        return false;
      }
      this._mimeType = pickMimeType();
      this._chunks = [];
      try {
        const opts = this._mimeType ? { mimeType: this._mimeType } : undefined;
        this._recorder = new MediaRecorder(this._stream, opts);
      } catch (e) {
        this._status = "error";
        return false;
      }
      this._recorder.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) this._chunks.push(ev.data);
      };
      this._recorder.start(5000);
      this._started = true;
      this._status = "recording";
      return true;
    }

    /**
     * Остановить запись и вернуть Blob (или null).
     * @returns {Promise<Blob|null>}
     */
    stop() {
      return new Promise((resolve) => {
        const rec = this._recorder;
        if (!rec || rec.state === "inactive") {
          this._releaseStream();
          this._status = this._chunks.length ? "stopped" : this._status;
          resolve(this._buildBlob());
          return;
        }
        rec.onstop = () => {
          this._releaseStream();
          this._status = "stopped";
          resolve(this._buildBlob());
        };
        try {
          if (rec.state === "recording") rec.requestData();
          rec.stop();
        } catch (e) {
          this._releaseStream();
          resolve(this._buildBlob());
        }
      });
    }

    reset() {
      try {
        if (this._recorder && this._recorder.state !== "inactive") {
          this._recorder.ondataavailable = null;
          this._recorder.onstop = null;
          this._recorder.stop();
        }
      } catch (_) {
        /* ignore */
      }
      this._recorder = null;
      this._chunks = [];
      this._started = false;
      this._mimeType = "";
      this._releaseStream();
      this._status = "off";
    }

    _buildBlob() {
      if (!this._chunks.length) return null;
      const type = this._mimeType || this._chunks[0]?.type || "audio/webm";
      return new Blob(this._chunks, { type });
    }

    _releaseStream() {
      if (this._stream) {
        for (const track of this._stream.getTracks()) {
          try {
            track.stop();
          } catch (_) {
            /* ignore */
          }
        }
      }
      this._stream = null;
    }
  }

  global.SessionMicRecorder = SessionMicRecorder;
})(typeof window !== "undefined" ? window : globalThis);
