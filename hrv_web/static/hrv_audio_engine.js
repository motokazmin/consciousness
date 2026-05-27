/* global window */

/**
 * Механика Web Audio API (кратко):
 *
 *   Звук в браузере строится как граф узлов (AudioNode).
 *   Каждый узел — один шаг обработки сигнала:
 *     OscillatorNode    — генерирует тон заданной частоты
 *     AudioBufferSource — воспроизводит буфер (например, шум)
 *     GainNode          — множитель громкости (0 = тишина, 1 = без изменений)
 *     BiquadFilterNode  — фильтр (lowpass, highpass и др.)
 *   Узлы соединяются через .connect(): сигнал течёт по цепочке до
 *   ctx.destination — виртуальных «колонок».
 *
 *   Ключевая идея: узлы создаются один раз и живут всё время.
 *   Управление звуком — это изменение параметров (gain, frequency и др.)
 *   на живых узлах, а не пересоздание графа. Параметры — это AudioParam,
 *   у них есть методы планирования изменений во времени:
 *     setValueAtTime(v, t)             — мгновенно установить v в момент t
 *     linearRampToValueAtTime(v, t)    — линейно дойти до v к моменту t
 *     setTargetAtTime(v, t, τ)         — экспоненциально приближаться к v
 *                                        начиная с t; τ (time constant) —
 *                                        скорость: меньше = резче
 *
 * Генеративный звуковой ландшафт HRV-биофидбека (Web Audio API).
 *
 * Два режима работы (setMode):
 *
 *  smooth_rr — «Фоновый ландшафт»
 *    Текстура (space_pad / sea_wave / tibetan_bowl) звучит непрерывно.
 *    RR-интервал управляет тембром: короткий RR → открытый фильтр (яркий звук),
 *    длинный RR → закрытый фильтр (глухой). Обновляется каждый фрейм через
 *    processFrame({ smoothed_rr }).
 *
 *  rmssd_trigger — «Трансовый Порог»
 *    Четыре синус-осциллятора (padFreqs) крутятся постоянно, но молчат —
 *    их общий padGain стоит на нуле. Как только rmssd_normalized превышает
 *    rampStart, padGain плавно нарастает до padGainMax; при падении — затухает.
 *    Скорость нарастания/затухания — padSmoothSec (time constant setTargetAtTime).
 *    Что крутить для изменения звука:
 *      тембр/аккорд → padFreqs, padDetuneCents (или тип волны "sine" в start())
 *      громкость     → padGainMax
 *      порог старта  → rampStart / rampEnd
 *      плавность     → padSmoothSec (0.08 = резко, 0.5+ = плавный fade)
 *    Текстура в этом режиме тише (textureGain 0.045 vs 0.14) — служит фоном.
 *
 * Удар сердца (triggerBeat):
 *    Вызывается отдельно при каждом R-пике. Создаёт короткий tone (sine + triangle),
 *    высота которого зависит от RR: быстрый пульс → высокая нота пентатоники,
 *    медленный → низкая. Независим от режима.
 */
class HrvAudioEngine {
  static TEXTURES = ["space_pad", "sea_wave", "tibetan_bowl"];

  static config = {
    rampSec: 0.35,
    textureCrossfadeSec: 1.75,
    paramSmoothSec: 0.35,
    beat: {
      rrMin: 400,
      rrMax: 1200,
      pentatonic: [220, 261.63, 293.66, 329.63, 392],
      gainPeak: 0.18,
      duration: 0.22,
    },
    smoothRr: {
      rrMin: 400,
      rrMax: 1200,
      cutoffMin: 80,
      cutoffMax: 900,
      textureGain: 0.14,
    },
    rmssdTrigger: {
      threshold: 1.0,
      rampStart: 2.5,
      rampEnd: 3.5,
      // Скорость нарастания/затухания pad при скачке RMSSD (сек, time constant setTargetAtTime)
      padSmoothSec: 0.08,
      padGainMax: 0.28,
      textureGain: 0.045,
      // 8 осцилляторов в унисон: каждая частота дублируется с зеркальной расстройкой.
      // Больше разброс центов → шире и теплее; слишком много → расстроенно.
      padFreqs: [220, 220, 277.18, 277.18, 329.63, 329.63, 440, 440],
      padDetuneCents: [-12, +12, -9, +9, -7, +7, -5, +5],
    },
    // Текстура — постоянно звучащий фоновый слой. Три варианта:
    //   space_pad    — осцилляторы на пилообразной волне, фильтрованный хор
    //   sea_wave     — розовый шум, модулированный LFO (имитация волн)
    //   tibetan_bowl — синус-обертоны с вибрато, как чаша
    // В каждом варианте есть lowpass-фильтр (lp), частота среза которого
    // управляется через smoothed_rr в режиме smooth_rr.
    textures: {
      space_pad: {
        freqs: [65.41, 98.0, 130.81, 196.0],
        detuneCents: [-8, 0, 8, -4],
        waveform: "sawtooth",
        voiceGain: 0.22,
      },
      sea_wave: {
        noiseGain: 0.55,
        lfoHz: 0.05,
        lfoGainDepth: 0.1,
        lfoCutoffDepth: 120,
      },
      tibetan_bowl: {
        overtones: [
          { freq: 110, level: 0.55, lfoHz: 0.1, lfoDepth: 0.14 },
          { freq: 221, level: 0.35, lfoHz: 0.15, lfoDepth: 0.11 },
          { freq: 332, level: 0.22, lfoHz: 0.2, lfoDepth: 0.09 },
          { freq: 442, level: 0.14, lfoHz: 0.12, lfoDepth: 0.08 },
          { freq: 553, level: 0.1, lfoHz: 0.18, lfoDepth: 0.07 },
        ],
      },
    },
  };

  // Граф аудиоузлов (создаётся в start(), все поля null до вызова):
  //
  //   padOscs[] ──► padGain ──► rmssdMixGain ──┐
  //   texture   ──────────────► smoothMixGain ──┼──► masterGain ──► [колонки]
  //   triggerBeat ────────────► heartBeatGain ──┘
  //
  //   masterGain    — общая громкость; используется для fade in/out при старте и стопе
  //   smoothMixGain — шина режима smooth_rr;     setMode() выставляет gain=1 или 0
  //   rmssdMixGain  — шина режима rmssd_trigger; setMode() выставляет gain=1 или 0
  //                   (текстура подключена к обеим шинам, слышна только через активную)
  //   padGain       — громкость осцилляторов Порога; единственное что двигает processFrame()
  //   heartBeatGain — шина ударов сердца; минует режимные шины, биты слышны всегда
  constructor() {
    this.ctx = null;
    this.running = false;
    this.mode = "smooth_rr";
    this.textureId = "space_pad";
    this.activeTexture = null;
    this.masterGain = null;
    this.smoothMixGain = null;
    this.rmssdMixGain = null;
    this.padGain = null;
    this.padOscs = [];
    this.heartBeatGain = null;
    this.lastSmoothedRr = null;
    this.lastRmssdNormalized = null;
  }

  async start() {
    if (this.running) return;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) throw new Error("Web Audio API недоступен");

    this.ctx = new Ctx();
    if (this.ctx.state === "suspended") {
      await this.ctx.resume();
    }

    const t0 = this.ctx.currentTime;
    const cfg = HrvAudioEngine.config;

    // Граф узлов создаётся один раз здесь. Дальше звук управляется
    // только изменением параметров живых узлов — узлы не пересоздаются.
    // linearRampToValueAtTime — запланированный переход к значению к конкретному моменту времени.
    // setTargetAtTime (используется ниже) — экспоненциальное приближение без фиксированного конца,
    // скорость задаётся time constant: меньше = резче.
    this.masterGain = this.ctx.createGain();
    this.masterGain.gain.setValueAtTime(0.0001, t0);
    this.masterGain.gain.linearRampToValueAtTime(1, t0 + cfg.rampSec);
    this.masterGain.connect(this.ctx.destination);

    this.smoothMixGain = this.ctx.createGain();
    this.rmssdMixGain = this.ctx.createGain();
    this.smoothMixGain.connect(this.masterGain);
    this.rmssdMixGain.connect(this.masterGain);

    this.activeTexture = this._createTexture(this.textureId, t0);
    this._connectTexture(this.activeTexture, t0, 1);

    this.padGain = this.ctx.createGain();
    this.padGain.gain.setValueAtTime(0, t0);
    this.padGain.connect(this.rmssdMixGain);

    // Осцилляторы Порога: запускаются здесь и крутятся всегда.
    // Тишина обеспечивается не остановкой, а padGain = 0.
    // processFrame() только двигает padGain при изменении rmssd_normalized.
    this.padOscs = cfg.rmssdTrigger.padFreqs.map((freq, i) => {
      const osc = this.ctx.createOscillator();
      osc.type = "triangle"; // "triangle" — мягче и теплее чем "sine" за счёт обертонов
      osc.frequency.setValueAtTime(freq, t0);
      osc.detune.setValueAtTime(cfg.rmssdTrigger.padDetuneCents[i] || 0, t0);
      osc.connect(this.padGain);
      osc.start(t0);
      return osc;
    });

    this.heartBeatGain = this.ctx.createGain();
    this.heartBeatGain.gain.setValueAtTime(1, t0);
    this.heartBeatGain.connect(this.masterGain);

    this.setMode(this.mode, t0);
    this.running = true;
  }

  async stop() {
    if (!this.ctx || !this.running) return;
    const t0 = this.ctx.currentTime;
    const cfg = HrvAudioEngine.config;

    if (this.masterGain) {
      this.masterGain.gain.cancelScheduledValues(t0);
      this.masterGain.gain.setValueAtTime(this.masterGain.gain.value, t0);
      this.masterGain.gain.linearRampToValueAtTime(0.0001, t0 + cfg.rampSec);
    }

    const ctx = this.ctx;
    this.running = false;
    setTimeout(() => {
      if (this.activeTexture) this._disposeTexture(this.activeTexture);
      for (const osc of this.padOscs) {
        try {
          osc.stop();
        } catch (_) { /* already stopped */ }
      }
      ctx.close();
    }, (cfg.rampSec + 0.15) * 1000);

    this.ctx = null;
    this.masterGain = null;
    this.smoothMixGain = null;
    this.rmssdMixGain = null;
    this.activeTexture = null;
    this.padGain = null;
    this.padOscs = [];
    this.heartBeatGain = null;
  }

  setMode(mode, when) {
    if (mode !== "smooth_rr" && mode !== "rmssd_trigger") return;
    this.mode = mode;
    if (!this.ctx || !this.smoothMixGain || !this.rmssdMixGain) return;

    const t0 = when ?? this.ctx.currentTime;
    const cfg = HrvAudioEngine.config;
    const smoothOn = mode === "smooth_rr" ? 1 : 0;
    const rmssdOn = mode === "rmssd_trigger" ? 1 : 0;

    this.smoothMixGain.gain.cancelScheduledValues(t0);
    this.smoothMixGain.gain.setValueAtTime(this.smoothMixGain.gain.value, t0);
    this.smoothMixGain.gain.linearRampToValueAtTime(smoothOn, t0 + cfg.rampSec);

    this.rmssdMixGain.gain.cancelScheduledValues(t0);
    this.rmssdMixGain.gain.setValueAtTime(this.rmssdMixGain.gain.value, t0);
    this.rmssdMixGain.gain.linearRampToValueAtTime(rmssdOn, t0 + cfg.rampSec);

    this._applyTextureModeGain(t0);
  }

  setTexture(textureId, when) {
    if (!HrvAudioEngine.TEXTURES.includes(textureId)) return;
    if (!this.ctx || !this.running) {
      this.textureId = textureId;
      return;
    }
    if (this.activeTexture?.id === textureId) {
      this.textureId = textureId;
      return;
    }

    const t0 = when ?? this.ctx.currentTime;
    const fadeSec = HrvAudioEngine.config.textureCrossfadeSec;
    const oldTexture = this.activeTexture;
    const newTexture = this._createTexture(textureId, t0);

    this._connectTexture(newTexture, t0, 0.0001);
    newTexture.busGain.gain.cancelScheduledValues(t0);
    newTexture.busGain.gain.setValueAtTime(0.0001, t0);
    newTexture.busGain.gain.linearRampToValueAtTime(1, t0 + fadeSec);

    if (oldTexture) {
      oldTexture.busGain.gain.cancelScheduledValues(t0);
      oldTexture.busGain.gain.setValueAtTime(oldTexture.busGain.gain.value, t0);
      oldTexture.busGain.gain.linearRampToValueAtTime(0.0001, t0 + fadeSec);
      setTimeout(() => this._disposeTexture(oldTexture), (fadeSec + 0.12) * 1000);
    }

    this.activeTexture = newTexture;
    this.textureId = textureId;
    this._applyTextureModeGain(t0);

    if (this.lastSmoothedRr != null) {
      this._setTextureCutoff(this.lastSmoothedRr, t0);
    }
  }

  // Единственная точка управления звуком в рантайме.
  // Не создаёт узлы — только меняет параметры живого графа.
  processFrame(data) {
    if (!this.ctx || !this.running) return;
    const t0 = this.ctx.currentTime;
    const cfg = HrvAudioEngine.config;

    if (data.smoothed_rr != null) {
      this.lastSmoothedRr = data.smoothed_rr;
      if (this.mode === "smooth_rr") {
        this._setTextureCutoff(data.smoothed_rr, t0);
      }
    }

    if (this.mode === "rmssd_trigger" && data.rmssd_normalized != null && this.padGain) {
      this.lastRmssdNormalized = data.rmssd_normalized;
      const gain = this._rmssdToPadGain(data.rmssd_normalized);
      this.padGain.gain.cancelScheduledValues(t0);
      this.padGain.gain.setTargetAtTime(gain, t0, cfg.rmssdTrigger.padSmoothSec);
    }
  }

  triggerBeat(rrMs) {
    if (!this.ctx || !this.running || !this.heartBeatGain) return;
    const cfg = HrvAudioEngine.config.beat;
    const t0 = this.ctx.currentTime;

    const oscSine = this.ctx.createOscillator();
    const oscTri = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    const mix = this.ctx.createGain();

    const pitch = this._rrToPitch(rrMs);
    oscSine.type = "sine";
    oscTri.type = "triangle";
    oscSine.frequency.setValueAtTime(pitch, t0);
    oscTri.frequency.setValueAtTime(pitch * 1.002, t0);

    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(cfg.gainPeak, t0 + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + cfg.duration);

    oscSine.connect(mix);
    oscTri.connect(mix);
    mix.connect(gain);
    gain.connect(this.heartBeatGain);

    const stopAt = t0 + cfg.duration + 0.05;
    oscSine.start(t0);
    oscTri.start(t0);
    oscSine.stop(stopAt);
    oscTri.stop(stopAt);
  }

  _createTexture(textureId, t0) {
    if (textureId === "sea_wave") return this._buildSeaWave(t0);
    if (textureId === "tibetan_bowl") return this._buildTibetanBowl(t0);
    return this._buildSpacePad(t0);
  }

  _buildSpacePad(t0) {
    const cfg = HrvAudioEngine.config.textures.space_pad;
    const smoothCfg = HrvAudioEngine.config.smoothRr;
    const busGain = this.ctx.createGain();
    const levelGain = this.ctx.createGain();
    const lp = this.ctx.createBiquadFilter();

    lp.type = "lowpass";
    lp.Q.setValueAtTime(0.85, t0);
    lp.frequency.setValueAtTime((smoothCfg.cutoffMin + smoothCfg.cutoffMax) / 2, t0);

    const oscillators = cfg.freqs.map((freq, i) => {
      const osc = this.ctx.createOscillator();
      osc.type = cfg.waveform;
      osc.frequency.setValueAtTime(freq, t0);
      osc.detune.setValueAtTime(cfg.detuneCents[i] || 0, t0);

      const voiceGain = this.ctx.createGain();
      voiceGain.gain.setValueAtTime(cfg.voiceGain / cfg.freqs.length, t0);
      osc.connect(voiceGain);
      voiceGain.connect(lp);
      osc.start(t0);
      return { osc, voiceGain };
    });

    lp.connect(levelGain);
    levelGain.connect(busGain);

    return {
      id: "space_pad",
      busGain,
      levelGain,
      lp,
      lfoNodes: [],
      sources: [],
      oscillators,
    };
  }

  _buildSeaWave(t0) {
    const cfg = HrvAudioEngine.config.textures.sea_wave;
    const smoothCfg = HrvAudioEngine.config.smoothRr;
    const busGain = this.ctx.createGain();
    const levelGain = this.ctx.createGain();
    const modGain = this.ctx.createGain();
    const lp = this.ctx.createBiquadFilter();

    modGain.gain.setValueAtTime(cfg.noiseGain, t0);
    lp.type = "lowpass";
    lp.Q.setValueAtTime(0.6, t0);
    lp.frequency.setValueAtTime((smoothCfg.cutoffMin + smoothCfg.cutoffMax) / 2, t0);

    const source = this.ctx.createBufferSource();
    source.buffer = this._createNoiseBuffer(3.0);
    source.loop = true;
    source.connect(modGain);
    modGain.connect(lp);
    lp.connect(levelGain);
    levelGain.connect(busGain);
    source.start(t0);

    const lfo = this.ctx.createOscillator();
    const lfoDepth = this.ctx.createGain();
    lfo.type = "sine";
    lfo.frequency.setValueAtTime(cfg.lfoHz, t0);
    lfoDepth.gain.setValueAtTime(cfg.lfoGainDepth, t0);
    lfo.connect(lfoDepth);
    lfoDepth.connect(modGain.gain);
    lfo.start(t0);

    const cutoffLfo = this.ctx.createOscillator();
    const cutoffLfoDepth = this.ctx.createGain();
    cutoffLfo.type = "sine";
    cutoffLfo.frequency.setValueAtTime(cfg.lfoHz, t0);
    cutoffLfoDepth.gain.setValueAtTime(cfg.lfoCutoffDepth * 0.2, t0);
    cutoffLfo.connect(cutoffLfoDepth);
    cutoffLfoDepth.connect(lp.frequency);
    cutoffLfo.start(t0);

    return {
      id: "sea_wave",
      busGain,
      levelGain,
      lp,
      lfoNodes: [lfo, lfoDepth, cutoffLfo, cutoffLfoDepth],
      sources: [source],
      oscillators: [],
    };
  }

  _buildTibetanBowl(t0) {
    const cfg = HrvAudioEngine.config.textures.tibetan_bowl;
    const smoothCfg = HrvAudioEngine.config.smoothRr;
    const busGain = this.ctx.createGain();
    const levelGain = this.ctx.createGain();
    const lp = this.ctx.createBiquadFilter();
    const merge = this.ctx.createGain();

    lp.type = "lowpass";
    lp.Q.setValueAtTime(1.1, t0);
    lp.frequency.setValueAtTime(smoothCfg.cutoffMax * 0.85, t0);
    merge.gain.setValueAtTime(1, t0);

    const oscillators = cfg.overtones.map((tone) => {
      const osc = this.ctx.createOscillator();
      const voiceGain = this.ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(tone.freq, t0);
      voiceGain.gain.setValueAtTime(tone.level, t0);
      osc.connect(voiceGain);
      voiceGain.connect(merge);
      osc.start(t0);

      const lfo = this.ctx.createOscillator();
      const lfoDepth = this.ctx.createGain();
      lfo.type = "sine";
      lfo.frequency.setValueAtTime(tone.lfoHz, t0);
      lfoDepth.gain.setValueAtTime(tone.lfoDepth, t0);
      lfo.connect(lfoDepth);
      lfoDepth.connect(voiceGain.gain);
      lfo.start(t0);

      return { osc, voiceGain, lfo, lfoDepth };
    });

    merge.connect(lp);
    lp.connect(levelGain);
    levelGain.connect(busGain);

    return {
      id: "tibetan_bowl",
      busGain,
      levelGain,
      lp,
      lfoNodes: oscillators.flatMap((o) => [o.lfo, o.lfoDepth]),
      sources: [],
      oscillators,
    };
  }

  _createNoiseBuffer(durationSec) {
    const sampleRate = this.ctx.sampleRate;
    const length = Math.floor(sampleRate * durationSec);
    const buffer = this.ctx.createBuffer(1, length, sampleRate);
    const data = buffer.getChannelData(0);

    let b0 = 0;
    let b1 = 0;
    let b2 = 0;
    let b3 = 0;
    let b4 = 0;
    let b5 = 0;
    let b6 = 0;

    for (let i = 0; i < length; i += 1) {
      const white = Math.random() * 2 - 1;
      b0 = 0.99886 * b0 + white * 0.0555179;
      b1 = 0.99332 * b1 + white * 0.0750759;
      b2 = 0.969 * b2 + white * 0.153852;
      b3 = 0.8665 * b3 + white * 0.3104856;
      b4 = 0.55 * b4 + white * 0.5329522;
      b5 = -0.7616 * b5 - white * 0.016898;
      data[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362) * 0.11;
      b6 = white * 0.115926;
    }

    return buffer;
  }

  _connectTexture(texture, t0, busLevel) {
    texture.levelGain.connect(texture.busGain);
    texture.busGain.connect(this.smoothMixGain);
    texture.busGain.connect(this.rmssdMixGain);
    texture.busGain.gain.setValueAtTime(busLevel, t0);
  }

  _applyTextureModeGain(t0) {
    if (!this.activeTexture?.levelGain) return;
    const cfg = HrvAudioEngine.config;
    const level = this.mode === "smooth_rr"
      ? cfg.smoothRr.textureGain
      : cfg.rmssdTrigger.textureGain;

    this.activeTexture.levelGain.gain.cancelScheduledValues(t0);
    this.activeTexture.levelGain.gain.setValueAtTime(this.activeTexture.levelGain.gain.value, t0);
    this.activeTexture.levelGain.gain.linearRampToValueAtTime(level, t0 + cfg.rampSec);
  }

  _setTextureCutoff(smoothedRr, t0) {
    if (!this.activeTexture?.lp) return;
    const cfg = HrvAudioEngine.config;
    const cutoff = this._mapRange(
      smoothedRr,
      cfg.smoothRr.rrMin,
      cfg.smoothRr.rrMax,
      cfg.smoothRr.cutoffMin,
      cfg.smoothRr.cutoffMax,
    );

    const lp = this.activeTexture.lp;
    lp.frequency.cancelScheduledValues(t0);
    lp.frequency.setTargetAtTime(cutoff, t0, cfg.paramSmoothSec * 0.45);
  }

  _disposeTexture(texture) {
    if (!texture) return;
    const tStop = this.ctx?.currentTime ?? 0;

    for (const item of texture.oscillators || []) {
      try {
        item.osc?.stop(tStop + 0.02);
      } catch (_) { /* already stopped */ }
      item.osc?.disconnect();
      item.voiceGain?.disconnect();
      item.lfo?.stop(tStop + 0.02);
      item.lfo?.disconnect();
      item.lfoDepth?.disconnect();
    }

    for (const lfo of texture.lfoNodes || []) {
      try {
        lfo.stop(tStop + 0.02);
      } catch (_) { /* already stopped */ }
      lfo.disconnect();
    }

    for (const source of texture.sources || []) {
      try {
        source.stop(tStop + 0.02);
      } catch (_) { /* already stopped */ }
      source.disconnect();
    }

    texture.lp?.disconnect();
    texture.levelGain?.disconnect();
    texture.busGain?.disconnect();
  }

  _mapRange(value, inMin, inMax, outMin, outMax) {
    const t = Math.max(0, Math.min(1, (value - inMin) / (inMax - inMin)));
    return outMin + t * (outMax - outMin);
  }

  _rrToPitch(rrMs) {
    const cfg = HrvAudioEngine.config.beat;
    const notes = cfg.pentatonic;
    const t = this._mapRange(rrMs, cfg.rrMin, cfg.rrMax, 0, 1);
    const idx = Math.round(t * (notes.length - 1));
    return notes[Math.max(0, Math.min(notes.length - 1, notes.length - 1 - idx))];
  }

  // Переводит rmssd_normalized → громкость pad.
  // Мёртвая зона: rn < rampStart → 0. Линейный рост: rampStart..rampEnd → 0..padGainMax.
  _rmssdToPadGain(rn) {
    const cfg = HrvAudioEngine.config.rmssdTrigger;
    if (rn <= cfg.threshold) return 0;
    if (rn <= cfg.rampStart) return 0;
    const t = this._mapRange(rn, cfg.rampStart, cfg.rampEnd, 0, 1);
    return t * cfg.padGainMax;
  }
}

window.HrvAudioEngine = HrvAudioEngine;