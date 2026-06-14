/* global uPlot */
/**
 * HRV analysis chart factories (Poincaré, spectrum, SDNN, progress overlays).
 */
(function (global) {
  const AXIS_STYLE = {
    stroke: "#3a4050",
    ticks: { stroke: "#3a4050" },
    grid: { stroke: "#1e242d", width: 1 },
    labelFont: "11px 'DM Sans'",
    font: "11px 'Space Mono'",
    stroke: "#5a6478",
  };

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
    const ox = u.bbox.left;
    const oy = u.bbox.top;
    const total = xdata.length;
    const radius = opts?.pointRadius ?? 2.2;
    const colorFn = opts?.pointColor || gradientPointColor;
    for (let i = 0; i < total; i++) {
      const x = u.valToPos(xdata[i], "x", true);
      const y = u.valToPos(ydata[i], "y", true);
      ctx.beginPath();
      ctx.fillStyle = colorFn(i, total);
      ctx.arc(ox + x, oy + y, radius, 0, Math.PI * 2);
      ctx.fill();
    }
    const lo = u.scales.x.min;
    const hi = u.scales.x.max;
    const x0 = u.valToPos(lo, "x", true);
    const y0 = u.valToPos(lo, "y", true);
    const x1 = u.valToPos(hi, "x", true);
    const y1 = u.valToPos(hi, "y", true);
    ctx.beginPath();
    ctx.strokeStyle = "rgba(255,255,255,0.25)";
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 1;
    ctx.moveTo(ox + x0, oy + y0);
    ctx.lineTo(ox + x1, oy + y1);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
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
          { ...AXIS_STYLE, label: "RRₙ, ms", values: (u, s) => s.map((v) => Math.round(v)) },
          { ...AXIS_STYLE, label: "RRₙ₊₁, ms", size: 52, values: (u, s) => s.map((v) => Math.round(v)) },
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

  function makeRawRrPlot(el, rawRrX, rawRr, durationSec, height, opts) {
    if (!rawRr?.length || !rawRrX?.length) return null;
    const xMax = durationSec || rawRrX[rawRrX.length - 1] || 1;
    const yMin = Math.max(300, rawRr.reduce((a, b) => (a < b ? a : b), Infinity) - 40);
    const yMax = opts?.yMax ?? (rawRr.reduce((a, b) => (a > b ? a : b), -Infinity) + 40);
    const w = plotWidth(el);

    return new uPlot(
      {
        width: w,
        height: height || 260,
        padding: CHART_PADDING,
        scales: {
          x: { ...xScaleLinear, range: [0, xMax] },
          y: { time: false, distr: 1, range: [yMin, yMax] },
        },
        series: [
          {},
          applySeriesOpts(
            { stroke: "#00d4ff", width: 1.5, fill: "rgba(0,212,255,0.04)", points: { show: false } },
            opts
          ),
        ],
        axes: [
          { ...AXIS_STYLE, label: "с от начала", values: fmtAxisSec, incrs: SEC_AXIS_INCRS },
          { ...AXIS_STYLE, label: "RR, ms", size: 52, values: (u, s) => s.map((v) => Math.round(v)) },
        ],
        cursor: { show: true, x: true, y: false },
        legend: { show: false },
      },
      [rawRrX, rawRr],
      el
    );
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
            { stroke: "#00d4ff", width: 2, fill: "rgba(0,212,255,0.08)", points: { show: false } },
            opts
          ),
        ],
        axes: [
          { ...AXIS_STYLE, label: "Частота, Гц", values: fmtAxisHz },
          { ...AXIS_STYLE, label: "Мощность", size: 52 },
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

  function makeSdnnPlot(el, trend, durationSec, height, opts) {
    if (!trend?.length) return null;
    const xs = trend.map((p) => p.x);
    const ys = trend.map((p) => p.sdnn);
    const xMax = durationSec || xs[xs.length - 1] || 1;
    const yMax = opts?.yMax ?? (ys.reduce((a, b) => (a > b ? a : b), 10) * 1.15);
    const w = plotWidth(el);

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
          applySeriesOpts(
            { stroke: "#9d8ef0", width: 2, fill: "rgba(157,142,240,0.08)", points: { show: false } },
            opts
          ),
        ],
        axes: [
          { ...AXIS_STYLE, label: "с от начала", values: fmtAxisSec, incrs: SEC_AXIS_INCRS },
          { ...AXIS_STYLE, label: "SDNN, ms", size: 52 },
        ],
        cursor: { show: true, x: true, y: false },
        legend: { show: false },
      },
      [xs, ys],
      el
    );
  }

  function makeRmssdPlot(el, trend, durationSec, height) {
    if (!trend?.length) return null;
    const xs = trend.map((p) => p.x);
    const ys = trend.map((p) => p.rmssd);
    const xMax = durationSec || xs[xs.length - 1] || 1;
    const yMax = ys.reduce((a, b) => (a > b ? a : b), 40) * 1.15;
    const w = plotWidth(el);

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
          {
            stroke: "#39e085",
            width: 2,
            fill: "rgba(57,224,133,0.07)",
            points: { show: false },
          },
        ],
        axes: [
          { ...AXIS_STYLE, label: "с от начала", values: fmtAxisSec, incrs: SEC_AXIS_INCRS },
          { ...AXIS_STYLE, label: "RMSSD, ms", size: 52 },
        ],
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
        const ox = u.bbox.left;
        const oy = u.bbox.top;
        ctx.fillStyle = hexToRgba(color, 0.35);
        for (let i = 0; i < xdata.length; i++) {
          const x = u.valToPos(xdata[i], "x", true);
          const y = u.valToPos(ydata[i], "y", true);
          ctx.beginPath();
          ctx.arc(ox + x, oy + y, 1.8, 0, Math.PI * 2);
          ctx.fill();
        }
      });
    });

    drawHooks.push((u) => {
      const { ctx } = u;
      const ox = u.bbox.left;
      const oy = u.bbox.top;
      const xmin = u.scales.x.min;
      const xmax = u.scales.x.max;
      ctx.beginPath();
      ctx.strokeStyle = "rgba(255,255,255,0.2)";
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 1;
      ctx.moveTo(ox + u.valToPos(xmin, "x", true), oy + u.valToPos(xmin, "y", true));
      ctx.lineTo(ox + u.valToPos(xmax, "x", true), oy + u.valToPos(xmax, "y", true));
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
          { ...AXIS_STYLE, label: "RRₙ, ms" },
          { ...AXIS_STYLE, label: "RRₙ₊₁, ms", size: 52 },
        ],
        hooks: { draw: drawHooks },
        cursor: { show: true, x: true, y: true },
        legend: { show: false },
      },
      data,
      el
    );
  }

  function interpolateSpectrum(freqs, power, grid) {
    if (!freqs?.length) return grid.map(() => 0);
    const n = freqs.length;
    return grid.map((f) => {
      if (f <= freqs[0]) return power[0];
      if (f >= freqs[n - 1]) return power[n - 1];
      let lo = 0;
      let hi = n - 1;
      while (lo + 1 < hi) {
        const mid = (lo + hi) >> 1;
        if (freqs[mid] <= f) lo = mid;
        else hi = mid;
      }
      const f0 = freqs[lo];
      const f1 = freqs[hi];
      if (f1 === f0) return power[lo];
      const t = (f - f0) / (f1 - f0);
      return power[lo] + t * (power[hi] - power[lo]);
    });
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
          { ...AXIS_STYLE, label: "Частота, Гц", values: fmtAxisHz },
          { ...AXIS_STYLE, label: "Мощность", size: 52 },
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
          { ...AXIS_STYLE, label: "с от начала", values: fmtAxisSec, incrs: SEC_AXIS_INCRS },
          { ...AXIS_STYLE, label: "SDNN, ms", size: 52 },
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
  };
})(window);
