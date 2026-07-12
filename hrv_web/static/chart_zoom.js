/* global uPlot */
/**
 * Zoom / pan controls for uPlot charts (wheel, drag, toolbar).
 */
(function (global) {
  const ZOOM_IN_FACTOR = 0.82;
  const ZOOM_OUT_FACTOR = 1 / ZOOM_IN_FACTOR;

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function span(min, max) {
    return Math.max(max - min, 0);
  }

  function mountZoomToolbar(headerEl, ctrl) {
    if (!headerEl || !ctrl) return null;
    const existing = headerEl.querySelector(".chart-zoom-toolbar");
    if (existing) existing.remove();

    const bar = document.createElement("div");
    bar.className = "chart-zoom-toolbar";
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", "Масштаб графика");

    const mkBtn = (label, title, action) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chart-zoom-btn";
      btn.textContent = label;
      btn.title = title;
      btn.setAttribute("aria-label", title);
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        action();
      });
      return btn;
    };

    bar.append(
      mkBtn("+", "Увеличить", () => ctrl.zoomIn()),
      mkBtn("−", "Уменьшить", () => ctrl.zoomOut()),
      mkBtn("⟲", "Сбросить масштаб", () => ctrl.reset())
    );
    headerEl.appendChild(bar);
    return bar;
  }

  function findPlotHeader(plotEl) {
    return plotEl?.closest(".plot-card")?.querySelector(".plot-header") || null;
  }

  function attachChartZoomPan(plot, options = {}) {
    if (!plot?.over) return null;

    const {
      getBaseline,
      minSpan = { x: 0.5, y: 20 },
      enableY = true,
      toolbarEl = null,
      showToolbar = true,
    } = options;

    let userModified = false;
    let drag = null;
    // Last range explicitly set by the user (zoom/pan). uPlot resets scales
    // that use a function `range` (rather than a static array) back to the
    // full auto-ranged extent on every plot.setData() call — this happens
    // on the live RR chart, which calls setData up to 60x/sec. Without
    // re-asserting the user's range after each data refresh, any zoom is
    // wiped out on the very next redraw frame. See reapplyZoom() below.
    let lastRange = { x: null, y: null };
    const cleanups = [];

    function on(el, type, fn, opts) {
      el.addEventListener(type, fn, opts);
      cleanups.push(() => el.removeEventListener(type, fn, opts));
    }

    function baseline() {
      const b = getBaseline?.() || {};
      const x = plot.scales.x;
      const y = plot.scales.y;
      return {
        x: Array.isArray(b.x) ? b.x : [x.min, x.max],
        y: Array.isArray(b.y) ? b.y : [y.min, y.max],
      };
    }

    function visibleYRange(minX, maxX) {
      const data = plot.data;
      const xs = data?.[0];
      const ys = data?.[1];
      if (!xs?.length || !ys?.length) return null;

      let lo = Infinity;
      let hi = -Infinity;
      for (let i = 0; i < xs.length; i++) {
        const x = xs[i];
        if (x == null || x < minX || x > maxX) continue;
        const y = ys[i];
        if (y == null) continue;
        if (y < lo) lo = y;
        if (y > hi) hi = y;
      }
      if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
      return { lo, hi };
    }

    // Recomputes the Y scale from whatever data points actually fall inside
    // [minX, maxX], with a bit of padding, instead of scaling Y by the same
    // factor as X. The old approach zoomed Y around the cursor/midpoint
    // regardless of where the RR values in that window actually were, so
    // zooming in on X routinely pushed the line outside the visible Y range.
    function autoFitY(minX, maxX) {
      if (!enableY) return;
      const b = baseline();
      const range = visibleYRange(minX, maxX);
      const minS = minSpan.y ?? 20;

      if (!range) {
        plot.setScale("y", { min: b.y[0], max: b.y[1] });
        lastRange.y = { min: b.y[0], max: b.y[1] };
        return;
      }

      const pad = Math.max((range.hi - range.lo) * 0.15, minS / 2);
      let lo = range.lo - pad;
      let hi = range.hi + pad;

      if (hi - lo < minS) {
        const mid = (lo + hi) / 2;
        lo = mid - minS / 2;
        hi = mid + minS / 2;
      }

      plot.setScale("y", { min: lo, max: hi });
      lastRange.y = { min: lo, max: hi };
    }

    function clampAxis(axis, min, max) {
      const b = baseline();
      const lo = b[axis][0];
      const hi = b[axis][1];
      const full = span(lo, hi);
      const minS = minSpan[axis] ?? (axis === "x" ? 0.5 : 20);
      let s = span(min, max);
      if (s < minS) {
        const mid = (min + max) / 2;
        min = mid - minS / 2;
        max = mid + minS / 2;
        s = minS;
      }
      if (s >= full * 0.999) {
        return { min: lo, max: hi };
      }
      if (min < lo) {
        max += lo - min;
        min = lo;
      }
      if (max > hi) {
        min -= max - hi;
        max = hi;
      }
      min = clamp(min, lo, hi - minS);
      max = clamp(max, min + minS, hi);
      return { min, max };
    }

    function setAxis(axis, min, max, markUser = true) {
      const next = clampAxis(axis, min, max);
      plot.setScale(axis, next);
      lastRange[axis] = next;
      if (markUser) userModified = true;
      if (axis === "x") autoFitY(next.min, next.max);
    }

    function zoomAt(factor, px) {
      const sc = plot.scales.x;
      const min = sc.min;
      const max = sc.max;
      const center = Number.isFinite(px) ? plot.posToVal(px, "x") : (min + max) / 2;
      const newMin = center - (center - min) * factor;
      const newMax = center + (max - center) * factor;
      setAxis("x", newMin, newMax);
    }

    function panByPixels(dx) {
      // plot.bbox.* is in device (canvas) pixels — i.e. already multiplied by
      // devicePixelRatio — while dx comes from mouse events in CSS pixels.
      // Mixing the two made panning ~pxRatio times too slow on any HiDPI
      // display (Retina, most modern laptops/phones). plot.over is the
      // interactive overlay div sized in real CSS pixels, so use its rect.
      const overRect = plot.over.getBoundingClientRect();
      const xSc = plot.scales.x;
      const xPerPx = span(xSc.min, xSc.max) / overRect.width;
      setAxis("x", xSc.min - dx * xPerPx, xSc.max - dx * xPerPx);
    }

    function reset() {
      const b = baseline();
      plot.setScale("x", { min: b.x[0], max: b.x[1] });
      if (enableY) plot.setScale("y", { min: b.y[0], max: b.y[1] });
      userModified = false;
      lastRange = { x: null, y: null };
    }

    function applyBaselineIfDefault() {
      if (userModified) return;
      reset();
    }

    // Re-asserts the last user-set zoom/pan range. Needed because uPlot's
    // x scale here uses a function `range` (for the sliding/timed window),
    // which makes uPlot treat the scale as `auto: true` — every
    // plot.setData() call then silently snaps the x scale back to the full
    // data extent, wiping out any zoom the user applied. Call this right
    // after every setData() on a live-updating chart so an active zoom
    // survives the next data refresh instead of reverting on the next frame.
    function reapplyZoom() {
      if (!userModified) return;
      if (lastRange.x) plot.setScale("x", { min: lastRange.x.min, max: lastRange.x.max });
      if (enableY && lastRange.y) plot.setScale("y", { min: lastRange.y.min, max: lastRange.y.max });
    }

    // Call this after every setData() on a live-updating chart: keeps the
    // user's zoom pinned in place if they've zoomed, otherwise keeps
    // following the moving baseline (e.g. sliding "last 60s" window).
    function syncAfterDataChange() {
      if (userModified) reapplyZoom();
      else reset();
    }

    function pointerPos(e) {
      // plot.over is uPlot's interactive overlay div — it's already
      // positioned exactly over the plotting area in CSS pixels, so its own
      // rect gives the correct local coordinate directly. The previous
      // version mixed plot.root's CSS-pixel rect with plot.bbox, which is in
      // device pixels (bbox.left is pre-multiplied by devicePixelRatio), so
      // on any HiDPI screen the computed pointer position — and therefore
      // the wheel-zoom center — was off.
      const rect = plot.over.getBoundingClientRect();
      const px = e.clientX - rect.left;
      return { px };
    }

    on(plot.over, "wheel", (e) => {
      e.preventDefault();
      if (e.shiftKey) {
        panByPixels(e.deltaY);
        return;
      }
      const { px } = pointerPos(e);
      const factor = e.deltaY < 0 ? ZOOM_IN_FACTOR : ZOOM_OUT_FACTOR;
      zoomAt(factor, px);
    }, { passive: false });

    on(plot.over, "mousedown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      drag = {
        x: e.clientX,
      };
      plot.over.classList.add("chart-panning");
    });

    on(window, "mousemove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x;
      drag.x = e.clientX;
      panByPixels(dx);
    });

    on(window, "mouseup", () => {
      if (!drag) return;
      drag = null;
      plot.over.classList.remove("chart-panning");
    });

    plot.over.classList.add("chart-zoomable");

    const ctrl = {
      reset,
      zoomIn: () => zoomAt(ZOOM_IN_FACTOR),
      zoomOut: () => zoomAt(ZOOM_OUT_FACTOR),
      isModified: () => userModified,
      applyBaselineIfDefault,
      syncAfterDataChange,
      destroy() {
        cleanups.forEach((fn) => fn());
        plot.over.classList.remove("chart-zoomable", "chart-panning");
        toolbar?.remove();
      },
    };

    let toolbar = null;
    if (showToolbar) {
      const header = toolbarEl || findPlotHeader(plot.root);
      if (header) toolbar = mountZoomToolbar(header, ctrl);
    }

    return ctrl;
  }

  global.HrvChartZoom = {
    attach: attachChartZoomPan,
    mountToolbar: mountZoomToolbar,
  };
})(window);