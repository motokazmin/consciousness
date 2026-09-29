/**
 * Лупа для графиков истории: по оси X показывается окно заданной длины,
 * ползунок двигает его по всей сессии, шкала Y подстраивается под видимый
 * кусок — локальные колебания занимают всю высоту графика.
 * Данные не меняются: снятая галочка возвращает исходный вид.
 */
(function (global) {
  function visibleRange(u, sidx, x0, x1) {
    const xs = u.data[0];
    const ys = u.data[sidx];
    let lo = Infinity;
    let hi = -Infinity;
    for (let i = 0; i < xs.length; i++) {
      const x = xs[i];
      if (x < x0) continue;
      if (x > x1) break;
      const v = ys[i];
      if (v == null || !Number.isFinite(v)) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    return [lo, hi];
  }

  // Сохраняет исходные шкалы и подменяет range на функцию: статичный
  // массив range uPlot переприменяет при каждом setScale.
  function remember(u) {
    if (u._hrvLoupeOrig) return;
    const orig = {};
    for (const [k, sc] of Object.entries(u.scales)) {
      orig[k] = { min: sc.min, max: sc.max, range: sc.range };
      sc.range = (_u, mn, mx) => [mn, mx];
    }
    u._hrvLoupeOrig = orig;
  }

  function show(u, x0, x1) {
    remember(u);
    const byScale = {};
    u.series.forEach((s, i) => {
      if (i === 0 || !s.show) return;
      const k = s.scale || "y";
      const [lo, hi] = visibleRange(u, i, x0, x1);
      if (!Number.isFinite(lo)) return;
      const cur = byScale[k] || [Infinity, -Infinity];
      byScale[k] = [Math.min(cur[0], lo), Math.max(cur[1], hi)];
    });
    u.batch(() => {
      u.setScale("x", { min: x0, max: x1 });
      for (const [k, [lo, hi]] of Object.entries(byScale)) {
        const log = u.scales[k]?.distr === 3;
        let mn;
        let mx;
        if (log) {
          mn = Math.max(lo, 1e-3) / 1.1;
          mx = hi * 1.1;
        } else {
          const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.05 || 1;
          mn = lo - pad;
          mx = hi + pad;
        }
        u.setScale(k, { min: mn, max: mx });
      }
    });
  }

  function restore(u) {
    const orig = u?._hrvLoupeOrig;
    if (!orig) return;
    u.batch(() => {
      for (const [k, o] of Object.entries(orig)) {
        u.scales[k].range = o.range;
        if (o.min != null && o.max != null) u.setScale(k, { min: o.min, max: o.max });
      }
    });
    u._hrvLoupeOrig = null;
  }

  // Полная длина оси X графика (в секундах от начала).
  function extent(u) {
    const orig = u._hrvLoupeOrig?.x;
    const xs = u.data[0];
    const min = orig?.min ?? u.scales.x.min ?? xs[0] ?? 0;
    const max = orig?.max ?? u.scales.x.max ?? xs[xs.length - 1] ?? 0;
    return [min, max];
  }

  global.HrvChartLoupe = { show, restore, extent };
})(window);
