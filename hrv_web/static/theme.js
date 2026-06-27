/* global window, document, localStorage, CustomEvent, getComputedStyle */
/**
 * Цветовые схемы UI и графиков (тёмная / светлая).
 */
(function (global) {
  const THEME_KEY = "hrv_theme";

  function getTheme() {
    const t = document.documentElement.getAttribute("data-theme");
    return t === "dark" ? "dark" : "light";
  }

  function setTheme(name) {
    const theme = name === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (_) { /* ignore */ }
    global.dispatchEvent(new CustomEvent("hrv-theme-change", { detail: { theme } }));
  }

  function initTheme() {
    let stored = "light";
    try {
      const v = localStorage.getItem(THEME_KEY);
      if (v === "light" || v === "dark") stored = v;
    } catch (_) { /* ignore */ }
    document.documentElement.setAttribute("data-theme", stored);
  }

  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback || "";
  }

  function hexToRgba(hex, alpha) {
    const h = (hex || "").replace("#", "");
    if (h.length !== 6) return `rgba(0,0,0,${alpha})`;
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  function chartAxis() {
    return {
      stroke: cssVar("--chart-axis", "#5a6478"),
      ticks: { stroke: cssVar("--chart-ticks", "#3a4050") },
      grid: { stroke: cssVar("--chart-grid", "#1e242d"), width: 1 },
      labelFont: "11px 'DM Sans'",
      font: "11px 'Space Mono'",
    };
  }

  function chartLine(varName, fallback) {
    return cssVar(varName, fallback);
  }

  global.HrvTheme = {
    THEME_KEY,
    getTheme,
    setTheme,
    initTheme,
    cssVar,
    hexToRgba,
    chartAxis,
    chartLine,
  };
})(window);
