/* global uPlot */
/**
 * HRV analysis chart factories (Poincaré, spectrum, SDNN, progress overlays).
 */
(function (global) {
  const T = () => global.HrvTheme || {
    chartAxis: () => ({}),
    cssVar: (_n, fb) => fb || "",
    hexToRgba: (hex, a) => hex,
    chartLine: (_n, fb) => fb || "",
  };

  function axisStyle() {
    return T().chartAxis();
  }

  // Совпадает с rrCfg в app.js — правый отступ под последнюю подпись оси X.
  const CHART_PADDING = [8, 40, 4, 4];

  const xScaleLinear = { time: false, distr: 1 };

  function fmtAxisSec(u, splits) {
    return splits.map((v) => {
      const n = Number(v);
      if (!Number.isFinite(n)) return "";
      return String(Math.round(n));
    });
  }

  const SEC_AXIS_INCRS = [1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 1800, 3600];

  function fmtAxisHz(u, splits) {
    return splits.map((v) => {
      const n = Number(v);
      return Number.isFinite(n) ? n.toFixed(2) : "";
    });
  }

  function plotWidth(el, fallback) {
    const p = el?.parentElement;
    const w = el?.clientWidth || (p ? p.clientWidth : 0) || fallback || window.innerWidth - 380;
    return Math.max(280, Math.floor(w - 8));
  }

  function gradientPointColor(i, total) {
    const t = total > 1 ? i / (total - 1) : 0;
    const hue = 180 + t * 100;
    return `hsla(${hue}, 75%, 58%, 0.55)`;
  }

  function poincarePointsFromRawRr(rawRr) {
    if (!rawRr?.length || rawRr.length < 2) return [];
    const points = [];
    for (let i = 0; i < rawRr.length - 1; i++) {
      points.push({ x: rawRr[i], y: rawRr[i + 1] });
    }
    return points;
  }

  function resolvePoincareBounds(bounds, points) {
    if (bounds && Number.isFinite(bounds.min) && Number.isFinite(bounds.max)) {
      return { lo: bounds.min, hi: bounds.max };
    }
    const xs = points.map((p) => p.x);
    const ys = points.map((p) => p.y);
    const all = xs.concat(ys);
    const mn = all.reduce((a, b) => (a < b ? a : b), Infinity);
    const mx = all.reduce((a, b) => (a > b ? a : b), -Infinity);
    const pad = Math.max(30, (mx - mn) * 0.08);
    return { lo: mn - pad, hi: mx + pad };
  }

  function poincareDrawPoints(u, opts) {
    const { ctx } = u;
    const xdata = u.data[1];
    const ydata = u.data[2];
    if (!xdata?.length) return;
    const total = xdata.length;
    const radius = opts?.pointRadius ?? 2.2;
    const colorFn = opts?.pointColor || gradientPointColor;
    for (let i = 0; i < total; i++) {
      // valToPos(..., true) — canvas px от края холста (уже с bbox).
      const x = u.valToPos(xdata[i], "x", true);
      const y = u.valToPos(ydata[i], "y", true);
      ctx.beginPath();
      ctx.fillStyle = colorFn(i, total);
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fill();
    }
    const lo = u.scales.x.min;
    const hi = u.scales.x.max;
    const x0 = u.valToPos(lo, "x", true);
    const y0 = u.valToPos(lo, "y", true);
    const x1 = u.valToPos(hi, "x", true);
    const y1 = u.valToPos(hi, "y", true);
    ctx.beginPath();
    ctx.strokeStyle = T().chartLine("--chart-guide-line", "rgba(255,255,255,0.22)");
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 1;
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function hexToRgba(hex, alpha) {
    return T().hexToRgba(hex, alpha);
  }

  function seriesColor(varName, fallback, fillAlpha) {
    const stroke = T().cssVar(varName, fallback);
    return { stroke, fill: hexToRgba(stroke, fillAlpha) };
  }

  // opts для фабрик make*Plot (передаётся из CHART_PROFILES.options в app.js):
  //   makeRawRrPlot / makeSpectrumPlot / makeSdnnPlot:
  //     stroke, fillAlpha, yMax, series (поля uPlot series)
  //   makePoincarePlot:
  //     pointRadius, pointColor(i, total) → CSS color
  function applySeriesOpts(baseSeries, opts) {
    if (!opts) return baseSeries;
    const series = { ...baseSeries, ...(opts.series || {}) };
    if (opts.stroke) {
      series.stroke = opts.stroke;
      if (series.fill) {
        const alpha = opts.fillAlpha ?? 0.06;
        series.fill = hexToRgba(opts.stroke, alpha);
      }
    }
    return series;
  }

  function makePoincarePlot(el, points, height, bounds, rawRr, opts) {
    const plotPoints = rawRr?.length >= 2 ? poincarePointsFromRawRr(rawRr) : (points || []);
    if (!plotPoints.length) return null;
    const xs = plotPoints.map((p) => p.x);
    const ys = plotPoints.map((p) => p.y);
    const { lo, hi } = resolvePoincareBounds(bounds, plotPoints);
    const w = plotWidth(el);

    return new uPlot(
      {
        width: w,
        height: height || 260,
        padding: CHART_PADDING,
        scales: {
          x: { ...xScaleLinear, range: [lo, hi] },
          y: { time: false, distr: 1, range: [lo, hi] },
        },
        series: [
          {},
          { points: { show: false } },
        ],
        axes: [
          { ...axisStyle(), label: "RRₙ, ms", values: (u, s) => s.map((v) => Math.round(v)) },
          { ...axisStyle(), label: "RRₙ₊₁, ms", size: 52, values: (u, s) => s.map((v) => Math.round(v)) },
        ],
        hooks: {
          draw: [(u) => poincareDrawPoints(u, opts)],
        },
        cursor: { show: true, x: true, y: true },
        legend: { show: false },
      },
      [[], xs, ys],
      el
    );
  }

  function drawTrimBands(u, opts) {
    const trim = opts?.trim;
    if (!trim?.applied) return;
    const { ctx } = u;
    const oy = u.bbox.top;
    const h = u.bbox.height;
    const start = trim.start_sec ?? 0;
    const end = (trim.duration_sec ?? 0) - (trim.end_sec ?? 0);
    ctx.save();
    ctx.fillStyle = T().chartLine("--chart-trim-overlay", "rgba(0,0,0,0.28)");
    // valToPos(..., true) уже включает bbox.left.
    const x0 = u.valToPos(0, "x", true);
    const x1 = u.valToPos(start, "x", true);
    const x2 = u.valToPos(end, "x", true);
    const x3 = u.valToPos(trim.duration_sec ?? end, "x", true);
    if (start > 0) ctx.fillRect(x0, oy, x1 - x0, h);
    if (end < (trim.duration_sec ?? end)) ctx.fillRect(x2, oy, x3 - x2, h);
    ctx.restore();
  }

  function makeRawRrPlot(el, rawRrX, rawRr, durationSec, height, opts) {
    if (!rawRr?.length || !rawRrX?.length) return null;
    const xMax = durationSec || rawRrX[rawRrX.length - 1] || 1;
    const yMin = Math.max(300, rawRr.reduce((a, b) => (a < b ? a : b), Infinity) - 40);
    const yMax = opts?.yMax ?? (rawRr.reduce((a, b) => (a > b ? a : b), -Infinity) + 40);
    const w = plotWidth(el);
    const trimOpts = opts?.trim;
    const baseline = { x: [0, xMax], y: [yMin, yMax] };

    const plot = new uPlot(
      {
        width: w,
        height: height || 260,
        padding: CHART_PADDING,
        scales: {
          // Static range: [0, xMax] blocks X zoom — uPlot re-applies it on every
          // setScale and snaps back to the full session. A callback lets zoom/pan
          // keep a narrowed window while defaulting to the full extent.
          x: { ...xScaleLinear, range: (_u, min, max) => [min ?? 0, max ?? xMax] },
          y: { time: false, distr: 1, range: [yMin, yMax] },
        },
        series: [
          {},
          applySeriesOpts(
            { width: 1.5, points: { show: false }, ...seriesColor("--chart-rr", "#00d4ff", 0.04) },
            opts
          ),
        ],
        axes: [
          { ...axisStyle(), label: "с от начала", values: fmtAxisSec, incrs: SEC_AXIS_INCRS },
          { ...axisStyle(), label: "RR, ms", size: 52, values: (u, s) => s.map((v) => Math.round(v)) },
        ],
        hooks: trimOpts?.applied ? { draw: [(u) => drawTrimBands(u, { trim: trimOpts })] } : {},
        cursor: opts?.noCursor
          ? { show: false, x: false, y: false, points: { show: false } }
          : { show: true, x: true, y: false, drag: { setScale: false, x: false, y: false } },
        legend: { show: false },
      },
      [rawRrX, rawRr],
      el
    );

    const zoom = global.HrvChartZoom?.attach(plot, {
      getBaseline: () => baseline,
      minSpan: { x: 1, y: 20 },
    });
    if (zoom) plot._hrvZoom = zoom;

    return plot;
  }

  function makeSpectrumPlot(el, spectrum, height, opts) {
    const freqs = spectrum?.freqs || [];
    const power = spectrum?.power || [];
    if (!freqs.length) return null;
    const w = plotWidth(el);
    const yMax = opts?.yMax ?? (power.reduce((a, b) => (a > b ? a : b), 0) * 1.2 || 1);

    const plot = new uPlot(
      {
        width: w,
        height: height || 260,
        padding: CHART_PADDING,
        scales: {
          x: { ...xScaleLinear, range: [0, 0.5] },
          y: { time: false, distr: 1, range: [0, yMax] },
        },
        series: [
          {},
          applySeriesOpts(
            { width: 2, points: { show: false }, ...seriesColor("--chart-rr", "#00d4ff", 0.08) },
            opts
          ),
        ],
        axes: [
          { ...axisStyle(), label: "Частота, Гц", values: fmtAxisHz },
          { ...axisStyle(), label: "Мощность", size: 52 },
        ],
        cursor: { show: true, x: true, y: false },
        legend: { show: false },
      },
      [freqs, power],
      el
    );

    return { plot, peakFreq: spectrum.peak_freq };
  }

  function positionPeakMarker(plot, container, peakFreq) {
    const marker = container.querySelector(".peak-marker");
    if (!marker || peakFreq == null || !plot) return;
    const x = plot.valToPos(peakFreq, "x");
    if (!Number.isFinite(x)) return;
    marker.style.left = `${plot.bbox.left + x}px`;
    marker.style.display = "block";
    marker.textContent = `${peakFreq.toFixed(2)} Гц`;
  }

  // Тренды RMSSD/SDNN: у обоих есть null-точки (разрыв записи, см. hrv_core.analysis
  // find_ts_gaps) и опциональная лог-ось Y (opts.scale === "log", по умолчанию линейная).
  // Лог-ось не принимает нули/отрицательные — такие точки уходят в null отдельно от
  // разрывов, чтобы не ронять отрисовку (см. спецификацию).
  function finiteMax(ys, fallback) {
    return ys.reduce((a, b) => (b == null || !Number.isFinite(b) ? a : Math.max(a, b)), fallback);
  }

  function logSafeYs(ys) {
    return ys.map((v) => (v == null || v <= 0 || !Number.isFinite(v) ? null : v));
  }

  function rangeLogSafe(u, dataMin, dataMax) {
    const base = u.scales.y.log ?? 10;
    if (typeof uPlot.rangeLog === "function") return uPlot.rangeLog(dataMin, dataMax, base, true);
    const lo = dataMin > 0 ? dataMin : 1;
    const hi = dataMax > lo ? dataMax : lo * 10;
    return [lo, hi];
  }

  function trendYScale(rawYs, opts, fallbackMax) {
    if (opts?.scale !== "log") {
      const yMax = opts?.yMax ?? (finiteMax(rawYs, fallbackMax) * 1.15);
      return { scale: { time: false, distr: 1, range: [0, yMax] }, ys: rawYs };
    }
    return {
      scale: { time: false, distr: 3, log: 10, range: rangeLogSafe },
      ys: logSafeYs(rawYs),
    };
  }

  function makeSdnnPlot(el, trend, durationSec, height, opts) {
    if (!trend?.length) return null;
    const xs = trend.map((p) => p.x);
    const { scale: yScale, ys } = trendYScale(trend.map((p) => p.sdnn), opts, 10);
    const xMax = durationSec || xs[xs.length - 1] || 1;
    const w = plotWidth(el);

    return new uPlot(
      {
        width: w,
        height: height || 260,
        padding: CHART_PADDING,
        scales: {
          x: { ...xScaleLinear, range: [0, xMax] },
          y: yScale,
        },
        series: [
          {},
          applySeriesOpts(
            { width: 2, points: { show: false }, ...seriesColor("--chart-sdnn", "#9d8ef0", 0.08) },
            opts
          ),
        ],
        axes: [
          { ...axisStyle(), label: "с от начала", values: fmtAxisSec, incrs: SEC_AXIS_INCRS },
          { ...axisStyle(), label: "SDNN, ms", size: 52 },
        ],
        hooks: {
          draw: [(u) => drawRejectedWindows(u, opts?.gaps)],
        },
        cursor: { show: true, x: true, y: false },
        legend: { show: false },
      },
      [xs, ys],
      el
    );
  }

  function makeRmssdPlot(el, trend, durationSec, height, opts) {
    if (!trend?.length) return null;
    const xs = trend.map((p) => p.x);
    const { scale: yScale, ys } = trendYScale(trend.map((p) => p.rmssd), opts, 40);
    const xMax = durationSec || xs[xs.length - 1] || 1;
    const w = plotWidth(el);

    return new uPlot(
      {
        width: w,
        height: height || 260,
        padding: CHART_PADDING,
        scales: {
          x: { ...xScaleLinear, range: [0, xMax] },
          y: yScale,
        },
        series: [
          {},
          {
            width: 2,
            points: { show: false },
            ...seriesColor("--chart-rmssd", "#39e085", 0.07),
          },
        ],
        axes: [
          { ...axisStyle(), label: "с от начала", values: fmtAxisSec, incrs: SEC_AXIS_INCRS },
          { ...axisStyle(), label: "RMSSD, ms", size: 52 },
        ],
        hooks: {
          draw: [(u) => drawRejectedWindows(u, opts?.gaps)],
        },
        cursor: { show: true, x: true, y: false },
        legend: { show: false },
      },
      [xs, ys],
      el
    );
  }

  function buildProgressPoincarePlot(el, sessions, visible, colors, height) {
    const active = sessions.filter(
      (s) => visible.has(s.id) && ((s.raw_rr?.length >= 2) || s.poincare_outline?.length)
    );
    if (!active.length) return null;

    let lo = Infinity;
    let hi = -Infinity;
    const bounded = active.every((s) => s.poincare_bounds?.min != null && s.poincare_bounds?.max != null);
    if (bounded) {
      lo = active.reduce((a, s) => Math.min(a, s.poincare_bounds.min), Infinity);
      hi = active.reduce((a, s) => Math.max(a, s.poincare_bounds.max), -Infinity);
    } else {
      for (const s of active) {
        const pts = s.raw_rr?.length >= 2 ? poincarePointsFromRawRr(s.raw_rr) : (s.poincare_outline || []);
        for (const p of pts) {
          lo = Math.min(lo, p.x, p.y);
          hi = Math.max(hi, p.x, p.y);
        }
      }
      const pad = Math.max(30, (hi - lo) * 0.08);
      lo -= pad;
      hi += pad;
    }

    const series = [{}];
    const data = [[]];
    const drawHooks = [];

    active.forEach((s) => {
      const idx = sessions.indexOf(s);
      const color = colors[idx % colors.length];
      const pts = s.raw_rr?.length >= 2 ? poincarePointsFromRawRr(s.raw_rr) : (s.poincare_outline || []);
      const xs = pts.map((p) => p.x);
      const ys = pts.map((p) => p.y);
      const xIdx = data.length;
      data.push(xs);
      data.push(ys);
      series.push({ points: { show: false }, label: `#${s.id}` });
      drawHooks.push((u) => {
        const { ctx } = u;
        const xdata = u.data[xIdx];
        const ydata = u.data[xIdx + 1];
        if (!xdata?.length) return;
        ctx.fillStyle = hexToRgba(color, 0.35);
        for (let i = 0; i < xdata.length; i++) {
          const x = u.valToPos(xdata[i], "x", true);
          const y = u.valToPos(ydata[i], "y", true);
          ctx.beginPath();
          ctx.arc(x, y, 1.8, 0, Math.PI * 2);
          ctx.fill();
        }
      });
    });

    drawHooks.push((u) => {
      const { ctx } = u;
      const xmin = u.scales.x.min;
      const xmax = u.scales.x.max;
      ctx.beginPath();
      ctx.strokeStyle = T().chartLine("--chart-guide-line", "rgba(255,255,255,0.22)");
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 1;
      ctx.moveTo(u.valToPos(xmin, "x", true), u.valToPos(xmin, "y", true));
      ctx.lineTo(u.valToPos(xmax, "x", true), u.valToPos(xmax, "y", true));
      ctx.stroke();
      ctx.setLineDash([]);
    });

    const w = plotWidth(el);
    return new uPlot(
      {
        width: w,
        height: height || 280,
        padding: CHART_PADDING,
        scales: {
          x: { ...xScaleLinear, range: [lo, hi] },
          y: { time: false, distr: 1, range: [lo, hi] },
        },
        series,
        axes: [
          { ...axisStyle(), label: "RRₙ, ms" },
          { ...axisStyle(), label: "RRₙ₊₁, ms", size: 52 },
        ],
        hooks: { draw: drawHooks },
        cursor: { show: true, x: true, y: true },
        legend: { show: false },
      },
      data,
      el
    );
  }

  // Общая реализация — interpolateSeries (ниже, используется и графиком
  // "дыхание + RMSSD"); здесь только контракт "нет данных → нули", который
  // ждёт buildProgressSpectrumPlot.
  function interpolateSpectrum(freqs, power, grid) {
    if (!freqs?.length) return grid.map(() => 0);
    return interpolateSeries(freqs, power, grid);
  }

  function buildProgressSpectrumPlot(el, sessions, visible, colors, height) {
    const active = sessions.filter((s) => visible.has(s.id) && s.spectrum?.freqs?.length);
    if (!active.length) return null;

    if (active.length === 1) {
      const idx = sessions.indexOf(active[0]);
      const color = colors[idx % colors.length];
      return makeSpectrumPlot(el, active[0].spectrum, height, {
        stroke: color,
        fillAlpha: 0.08,
        series: { width: 2 },
      });
    }

    const grid = [];
    for (let f = 0; f <= 0.5; f += 0.005) grid.push(Number(f.toFixed(3)));

    const series = [{}];
    const data = [grid];
    let yMax = 0;

    active.forEach((s) => {
      const idx = sessions.indexOf(s);
      const color = colors[idx % colors.length];
      const resampled = interpolateSpectrum(s.spectrum.freqs, s.spectrum.power, grid);
      for (const p of s.spectrum.power) yMax = Math.max(yMax, p);
      series.push({
        stroke: color,
        width: 2,
        fill: hexToRgba(color, 0.08),
        points: { show: false },
      });
      data.push(resampled);
    });

    const w = plotWidth(el);
    const plot = new uPlot(
      {
        width: w,
        height: height || 280,
        padding: CHART_PADDING,
        scales: {
          x: { ...xScaleLinear, range: [0, 0.5] },
          y: { time: false, distr: 1, range: [0, yMax * 1.2 || 1] },
        },
        series,
        axes: [
          { ...axisStyle(), label: "Частота, Гц", values: fmtAxisHz },
          { ...axisStyle(), label: "Мощность", size: 52 },
        ],
        cursor: { show: true, x: true, y: false },
        legend: { show: false },
      },
      data,
      el
    );
    return { plot, peakFreq: null };
  }

  function progressXMax(sessions, trendKey) {
    let xMax = 1;
    for (const s of sessions) {
      xMax = Math.max(xMax, s.duration_sec || 0);
      const trend = s[trendKey];
      if (trend?.length) {
        xMax = Math.max(xMax, trend[trend.length - 1].x || 0);
      }
    }
    return xMax;
  }

  function clipPlotArea(ctx, bbox) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(bbox.left, bbox.top, bbox.width, bbox.height);
    ctx.clip();
  }

  function buildProgressSdnnPlot(el, sessions, visible, colors, height) {
    const active = sessions.filter((s) => visible.has(s.id) && s.sdnn_trend?.length);
    if (!active.length) return null;

    const xMax = progressXMax(active, "sdnn_trend");
    let yMax = 0;
    const lines = [];

    active.forEach((s) => {
      const idx = sessions.indexOf(s);
      const color = colors[idx % colors.length];
      const xs = s.sdnn_trend.map((p) => p.x);
      const ys = s.sdnn_trend.map((p) => p.sdnn);
      for (const p of s.sdnn_trend) yMax = Math.max(yMax, p.sdnn);
      lines.push({ xs, ys, color });
    });

    const w = plotWidth(el);
    return new uPlot(
      {
        width: w,
        height: height || 280,
        padding: CHART_PADDING,
        scales: {
          x: { ...xScaleLinear, range: [0, xMax] },
          y: { time: false, distr: 1, range: [0, yMax * 1.15 || 10] },
        },
        series: [{ show: false }],
        axes: [
          { ...axisStyle(), label: "с от начала", values: fmtAxisSec, incrs: SEC_AXIS_INCRS },
          { ...axisStyle(), label: "SDNN, ms", size: 52 },
        ],
        hooks: {
          draw: [(u) => {
            const { ctx } = u;
            clipPlotArea(ctx, u.bbox);
            for (const { xs, ys, color } of lines) {
              if (!xs.length) continue;
              ctx.beginPath();
              ctx.strokeStyle = color;
              ctx.lineWidth = 2;
              ctx.moveTo(u.valToPos(xs[0], "x", true), u.valToPos(ys[0], "y", true));
              for (let i = 1; i < xs.length; i++) {
                ctx.lineTo(u.valToPos(xs[i], "x", true), u.valToPos(ys[i], "y", true));
              }
              ctx.stroke();
            }
            ctx.restore();
          }],
        },
        cursor: { show: true, x: true, y: false },
        legend: { show: false },
      },
      [[0], [0]],
      el
    );
  }

  // ── ДЫХАНИЕ (акселерометр PMD) ──────────────────────────────────────────
  // Общий рецепт расчёта — hrv_core/breathing.py; здесь только отрисовка.
  // Живого графика во время записи нет (решено отдельно) — эти три графика
  // только в разборе завершённой сессии, и только если у неё вообще есть
  // данные акселерометра (has_accel в ответе /breathing).

  function drawRejectedWindows(u, windows) {
    if (!windows?.length) return;
    const { ctx } = u;
    const oy = u.bbox.top;
    const h = u.bbox.height;
    ctx.save();
    ctx.fillStyle = T().chartLine("--chart-trim-overlay", "rgba(0,0,0,0.28)");
    for (const win of windows) {
      if (!win.rejected) continue;
      const x0 = u.valToPos(win.t_start, "x", true);
      const x1 = u.valToPos(win.t_end, "x", true);
      ctx.fillRect(x0, oy, x1 - x0, h);
    }
    ctx.restore();
  }

  function makeBreathingWavePlot(el, t, waveMg, windows, durationSec, height) {
    if (!t?.length || !waveMg?.length) return null;
    const w = plotWidth(el);
    const xMax = durationSec || t[t.length - 1] || 1;
    const absMax = waveMg.reduce((a, b) => Math.max(a, Math.abs(b)), 1) * 1.15;

    return new uPlot(
      {
        width: w,
        height: height || 260,
        padding: CHART_PADDING,
        scales: {
          x: { ...xScaleLinear, range: [0, xMax] },
          y: { time: false, distr: 1, range: [-absMax, absMax] },
        },
        series: [
          {},
          { width: 1.5, points: { show: false }, ...seriesColor("--chart-breathing", "#f0a83c", 0.06) },
        ],
        axes: [
          { ...axisStyle(), label: "с от начала", values: fmtAxisSec, incrs: SEC_AXIS_INCRS },
          { ...axisStyle(), label: "мг (0.10–0.45 Гц)", size: 60 },
        ],
        hooks: {
          draw: [(u) => drawRejectedWindows(u, windows)],
        },
        cursor: { show: true, x: true, y: false },
        legend: { show: false },
      },
      [t, waveMg],
      el
    );
  }

  function makeBreathingRatePlot(el, t, rateCpm, durationSec, height) {
    if (!t?.length || !rateCpm?.length) return null;
    const w = plotWidth(el);
    const xMax = durationSec || t[t.length - 1] || 1;
    const yMax = rateCpm.reduce((a, b) => Math.max(a, b), 10) * 1.15;

    return new uPlot(
      {
        width: w,
        height: height || 260,
        padding: CHART_PADDING,
        scales: {
          x: { ...xScaleLinear, range: [0, xMax] },
          y: { time: false, distr: 1, range: [0, yMax] },
        },
        series: [
          {},
          { width: 2, points: { show: false }, ...seriesColor("--chart-breathing", "#f0a83c", 0.08) },
        ],
        axes: [
          { ...axisStyle(), label: "с от начала", values: fmtAxisSec, incrs: SEC_AXIS_INCRS },
          { ...axisStyle(), label: "дыхание, цикл/мин", size: 56 },
        ],
        cursor: { show: true, x: true, y: false },
        legend: { show: false },
      },
      [t, rateCpm],
      el
    );
  }

  // Линейная интерполяция ys(xs) на произвольную сетку grid — общая с
  // interpolateSpectrum (там же бинарный поиск), нужна тут, чтобы свести
  // RMSSD-тренд (неравномерные точки из hrv_points) на сетку дыхания
  // (равномерная, ~10 Гц до прореживания) для общего графика с двумя Y.
  function interpolateSeries(xs, ys, grid) {
    if (!xs?.length) return grid.map(() => null);
    const n = xs.length;
    return grid.map((x) => {
      if (x <= xs[0]) return ys[0];
      if (x >= xs[n - 1]) return ys[n - 1];
      let lo = 0;
      let hi = n - 1;
      while (lo + 1 < hi) {
        const mid = (lo + hi) >> 1;
        if (xs[mid] <= x) lo = mid;
        else hi = mid;
      }
      const x0 = xs[lo];
      const x1 = xs[hi];
      if (x1 === x0) return ys[lo];
      const t = (x - x0) / (x1 - x0);
      return ys[lo] + t * (ys[hi] - ys[lo]);
    });
  }

  function makeBreathingRateRmssdPlot(el, t, rateCpm, rmssdTrend, durationSec, height) {
    if (!t?.length || !rateCpm?.length || !rmssdTrend?.length) return null;
    const w = plotWidth(el);
    const xMax = durationSec || t[t.length - 1] || 1;
    const rmssdXs = rmssdTrend.map((p) => p.x);
    const rmssdYs = rmssdTrend.map((p) => p.rmssd);
    const rmssdOnGrid = interpolateSeries(rmssdXs, rmssdYs, t);
    const rateMax = rateCpm.reduce((a, b) => Math.max(a, b), 10) * 1.15;
    const rmssdMax = rmssdYs.reduce((a, b) => Math.max(a, b), 40) * 1.15;

    return new uPlot(
      {
        width: w,
        height: height || 260,
        padding: [8, 46, 4, 4],
        scales: {
          x: { ...xScaleLinear, range: [0, xMax] },
          cpm: { time: false, distr: 1, range: [0, rateMax] },
          rmssd: { time: false, distr: 1, range: [0, rmssdMax] },
        },
        series: [
          {},
          {
            scale: "cpm", width: 2, points: { show: false },
            stroke: T().cssVar("--chart-breathing", "#f0a83c"),
            label: "дыхание",
          },
          {
            scale: "rmssd", width: 1.5, points: { show: false },
            stroke: T().cssVar("--chart-rmssd", "#39e085"),
            label: "RMSSD",
          },
        ],
        axes: [
          { ...axisStyle(), label: "с от начала", values: fmtAxisSec, incrs: SEC_AXIS_INCRS },
          { ...axisStyle(), scale: "cpm", label: "дыхание, цикл/мин", size: 56 },
          { ...axisStyle(), scale: "rmssd", label: "RMSSD, ms", side: 1, size: 52 },
        ],
        cursor: { show: true, x: true, y: false },
        legend: { show: true },
      },
      [t, rateCpm, rmssdOnGrid],
      el
    );
  }

  function setChartEmpty(el, message) {
    if (!el) return;
    el.innerHTML = `<div class="chart-empty">${message}</div>`;
  }

  global.HrvAnalysisCharts = {
    plotWidth,
    poincarePointsFromRawRr,
    makePoincarePlot,
    makeRawRrPlot,
    makeSpectrumPlot,
    makeSdnnPlot,
    makeRmssdPlot,
    positionPeakMarker,
    buildProgressPoincarePlot,
    buildProgressSpectrumPlot,
    buildProgressSdnnPlot,
    setChartEmpty,
    makeBreathingWavePlot,
    makeBreathingRatePlot,
    makeBreathingRateRmssdPlot,
    interpolateSeries,
  };
})(window);