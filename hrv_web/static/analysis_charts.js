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

  function poincareDrawPoints(u) {
    const { ctx } = u;
    const xdata = u.data[1];
    const ydata = u.data[2];
    if (!xdata?.length) return;
    const ox = u.bbox.left;
    const oy = u.bbox.top;
    const total = xdata.length;
    for (let i = 0; i < total; i++) {
      const x = u.valToPos(xdata[i], "x", true);
      const y = u.valToPos(ydata[i], "y", true);
      ctx.beginPath();
      ctx.fillStyle = gradientPointColor(i, total);
      ctx.arc(ox + x, oy + y, 2.2, 0, Math.PI * 2);
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

  function overlayScatterDraw(u, seriesIdx, color, alpha) {
    const { ctx } = u;
    const xdata = u.data[seriesIdx * 2];
    const ydata = u.data[seriesIdx * 2 + 1];
    if (!xdata?.length) return;
    const ox = u.bbox.left;
    const oy = u.bbox.top;
    ctx.fillStyle = color.replace(")", `, ${alpha})`).replace("rgb", "rgba").replace("#", "");
    if (color.startsWith("#")) {
      const r = parseInt(color.slice(1, 3), 16);
      const g = parseInt(color.slice(3, 5), 16);
      const b = parseInt(color.slice(5, 7), 16);
      ctx.fillStyle = `rgba(${r},${g},${b},${alpha})`;
    }
    for (let i = 0; i < xdata.length; i++) {
      const x = u.valToPos(xdata[i], "x", true);
      const y = u.valToPos(ydata[i], "y", true);
      ctx.beginPath();
      ctx.arc(ox + x, oy + y, 1.8, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  function makePoincarePlot(el, points, height, bounds, rawRr) {
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
        padding: [8, 8, 0, 0],
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
          draw: [poincareDrawPoints],
        },
        cursor: { show: true, x: true, y: true },
        legend: { show: false },
      },
      [[], xs, ys],
      el
    );
  }

  function makeRawRrPlot(el, rawRrX, rawRr, durationSec, height) {
    if (!rawRr?.length || !rawRrX?.length) return null;
    const xMax = durationSec || rawRrX[rawRrX.length - 1] || 1;
    const yMin = Math.max(300, rawRr.reduce((a, b) => (a < b ? a : b), Infinity) - 40);
    const yMax = rawRr.reduce((a, b) => (a > b ? a : b), -Infinity) + 40;
    const w = plotWidth(el);

    return new uPlot(
      {
        width: w,
        height: height || 260,
        padding: [8, 8, 0, 0],
        scales: {
          x: { ...xScaleLinear, range: [0, xMax] },
          y: { time: false, distr: 1, range: [yMin, yMax] },
        },
        series: [
          {},
          {
            stroke: "#00d4ff",
            width: 1.5,
            fill: "rgba(0,212,255,0.04)",
            points: { show: false },
          },
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

  function makeSpectrumPlot(el, spectrum, height) {
    const freqs = spectrum?.freqs || [];
    const power = spectrum?.power || [];
    if (!freqs.length) return null;
    const w = plotWidth(el);
    const yMax = power.reduce((a, b) => (a > b ? a : b), 0) * 1.2 || 1;

    const plot = new uPlot(
      {
        width: w,
        height: height || 260,
        padding: [8, 8, 0, 0],
        scales: {
          x: { ...xScaleLinear, range: [0, 0.5] },
          y: { time: false, distr: 1, range: [0, yMax] },
        },
        series: [
          {},
          {
            stroke: "#00d4ff",
            width: 2,
            fill: "rgba(0,212,255,0.08)",
            points: { show: false },
          },
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

  function makeSdnnPlot(el, trend, durationSec, height) {
    if (!trend?.length) return null;
    const xs = trend.map((p) => p.x);
    const ys = trend.map((p) => p.sdnn);
    const xMax = durationSec || xs[xs.length - 1] || 1;
    const yMax = ys.reduce((a, b) => (a > b ? a : b), 10) * 1.15;
    const w = plotWidth(el);

    return new uPlot(
      {
        width: w,
        height: height || 260,
        padding: [8, 8, 0, 0],
        scales: {
          x: { ...xScaleLinear, range: [0, xMax] },
          y: { time: false, distr: 1, range: [0, yMax] },
        },
        series: [
          {},
          {
            stroke: "#9d8ef0",
            width: 2,
            fill: "rgba(157,142,240,0.08)",
            points: { show: false },
          },
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
        padding: [8, 8, 0, 0],
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
        padding: [8, 8, 0, 0],
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

  function resampleSpectrum(freqs, power, grid) {
    if (!freqs?.length) return grid.map(() => 0);
    return grid.map((f) => {
      let best = 0;
      let bestDist = Infinity;
      for (let i = 0; i < freqs.length; i++) {
        const d = Math.abs(freqs[i] - f);
        if (d < bestDist) {
          bestDist = d;
          best = power[i];
        }
      }
      return bestDist < 0.02 ? best : 0;
    });
  }

  function buildProgressSpectrumPlot(el, sessions, visible, colors, height) {
    const active = sessions.filter((s) => visible.has(s.id) && s.spectrum?.freqs?.length);
    if (!active.length) return null;

    const grid = [];
    for (let f = 0; f <= 0.5; f += 0.005) grid.push(Number(f.toFixed(3)));

    const series = [{}];
    const data = [grid];
    let yMax = 0;

    active.forEach((s) => {
      const idx = sessions.indexOf(s);
      const color = colors[idx % colors.length];
      const resampled = resampleSpectrum(s.spectrum.freqs, s.spectrum.power, grid);
      yMax = Math.max(yMax, ...resampled);
      series.push({
        stroke: color,
        width: 1.5,
        fill: hexToRgba(color, 0.04),
        points: { show: false },
      });
      data.push(resampled);
    });

    const w = plotWidth(el);
    return new uPlot(
      {
        width: w,
        height: height || 280,
        padding: [8, 8, 0, 0],
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
  }

  function buildProgressSdnnPlot(el, sessions, visible, colors, height) {
    const active = sessions.filter((s) => visible.has(s.id) && s.sdnn_trend?.length);
    if (!active.length) return null;

    const xMax = active.reduce((a, s) => Math.max(a, s.duration_sec || 0), 1);
    let yMax = 0;
    for (const s of active) {
      for (const p of s.sdnn_trend) yMax = Math.max(yMax, p.sdnn);
    }

    const series = [{}];
    const data = [[]];
    const drawHooks = [];

    active.forEach((s) => {
      const idx = sessions.indexOf(s);
      const color = colors[idx % colors.length];
      const xs = s.sdnn_trend.map((p) => p.x);
      const ys = s.sdnn_trend.map((p) => p.sdnn);
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
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.moveTo(ox + u.valToPos(xdata[0], "x", true), oy + u.valToPos(ydata[0], "y", true));
        for (let i = 1; i < xdata.length; i++) {
          ctx.lineTo(ox + u.valToPos(xdata[i], "x", true), oy + u.valToPos(ydata[i], "y", true));
        }
        ctx.stroke();
      });
    });

    const w = plotWidth(el);
    return new uPlot(
      {
        width: w,
        height: height || 280,
        padding: [8, 8, 0, 0],
        scales: {
          x: { ...xScaleLinear, range: [0, xMax] },
          y: { time: false, distr: 1, range: [0, yMax * 1.15 || 10] },
        },
        series,
        axes: [
          { ...AXIS_STYLE, label: "с от начала", values: fmtAxisSec, incrs: SEC_AXIS_INCRS },
          { ...AXIS_STYLE, label: "SDNN, ms", size: 52 },
        ],
        hooks: { draw: drawHooks },
        cursor: { show: true, x: true, y: false },
        legend: { show: false },
      },
      data,
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
