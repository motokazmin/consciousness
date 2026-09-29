/**
 * Лента отрезков над графиками архива: стадии сна, фазы практики, события.
 * Выровнена по области построения опорного графика (RR) и следует за его
 * осью X, включая лупу. Цвет — вид отрезка, насыщенность — уверенность
 * (знаю / предполагаю / догадка). Наведение — подсказка, клик — карточка
 * с основанием под лентой.
 */
(function (global) {
  const CONF_LABEL = { know: "знаю", assume: "предполагаю", guess: "догадка" };
  const KIND_LABEL = {
    wake: "бодрствование",
    nrem: "сон без сновидений (NREM)",
    rem: "быстрый сон (REM)",
    unknown: "неопределённо",
  };
  const EVENT_GLYPH = { turn: "▾", artifact: "✕" };

  function fmtClock(startedUnix, t) {
    if (!startedUnix) return `${Math.round(t)} с`;
    const d = new Date((startedUnix + t) * 1000);
    return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function create(rootEl) {
    rootEl.innerHTML = `
      <div class="seg-lane">
        <div class="seg-track"></div>
        <div class="seg-events"></div>
      </div>
      <div class="seg-tip" hidden></div>
      <div class="seg-card" hidden></div>`;
    const lane = rootEl.querySelector(".seg-lane");
    const track = rootEl.querySelector(".seg-track");
    const eventsEl = rootEl.querySelector(".seg-events");
    const tip = rootEl.querySelector(".seg-tip");
    const card = rootEl.querySelector(".seg-card");

    let state = { data: null, plot: null, started: null, confidentOnly: false, selected: null };

    function xRange() {
      const sc = state.plot?.scales?.x;
      return sc && sc.min != null ? [sc.min, sc.max] : [0, 1];
    }

    function layout() {
      const plot = state.plot;
      if (!plot?.over) return;
      const over = plot.over.getBoundingClientRect();
      const root = rootEl.getBoundingClientRect();
      lane.style.marginLeft = `${over.left - root.left}px`;
      lane.style.width = `${over.width}px`;
    }

    function pct(t) {
      const [x0, x1] = xRange();
      return ((t - x0) / (x1 - x0)) * 100;
    }

    function describe(seg) {
      const range = `${fmtClock(state.started, seg.t0)}–${fmtClock(state.started, seg.t1)}`;
      return { range, kind: KIND_LABEL[seg.kind] || seg.kind, conf: CONF_LABEL[seg.confidence] || seg.confidence };
    }

    function showTip(html, clientX) {
      tip.innerHTML = html;
      tip.hidden = false;
      const root = rootEl.getBoundingClientRect();
      const w = tip.offsetWidth;
      const x = Math.min(Math.max(0, clientX - root.left - w / 2), root.width - w);
      tip.style.left = `${x}px`;
    }

    function showCard(seg) {
      if (!seg || state.selected === seg) {
        state.selected = null;
        card.hidden = true;
        render();
        return;
      }
      state.selected = seg;
      const d = describe(seg);
      card.innerHTML = `
        <div class="seg-card-head">
          <span class="seg-chip seg-k-${escapeHtml(seg.kind)}"></span>
          <b>${escapeHtml(seg.label)}</b>
          <span class="seg-card-time">${d.range}</span>
          <span class="seg-conf seg-c-${escapeHtml(seg.confidence)}">${escapeHtml(d.conf)}</span>
        </div>
        <div class="seg-card-body">${escapeHtml(seg.basis || "Основание не записано.")}</div>`;
      card.hidden = false;
      render();
    }

    function render() {
      track.innerHTML = "";
      eventsEl.innerHTML = "";
      const data = state.data;
      if (!data || !state.plot) return;
      layout();
      const [x0, x1] = xRange();
      for (const seg of data.segments || []) {
        if (seg.t1 <= x0 || seg.t0 >= x1) continue;
        if (state.confidentOnly && seg.confidence === "guess") continue;
        const el = document.createElement("div");
        el.className = `seg seg-k-${seg.kind} seg-c-${seg.confidence}` +
          (state.selected === seg ? " seg-selected" : "");
        const a = Math.max(0, pct(seg.t0));
        const b = Math.min(100, pct(seg.t1));
        el.style.left = `${a}%`;
        el.style.width = `${b - a}%`;
        el.addEventListener("mousemove", (e) => {
          const d = describe(seg);
          showTip(
            `<b>${escapeHtml(seg.label)}</b> · ${d.range}<br>` +
            `<span class="seg-tip-dim">${escapeHtml(d.kind)} · ${escapeHtml(d.conf)}</span>`,
            e.clientX
          );
        });
        el.addEventListener("mouseleave", () => { tip.hidden = true; });
        el.addEventListener("click", () => showCard(seg));
        track.appendChild(el);
      }
      for (const ev of data.events || []) {
        if (ev.t < x0 || ev.t > x1) continue;
        const el = document.createElement("div");
        el.className = `seg-event seg-e-${ev.kind}`;
        el.textContent = EVENT_GLYPH[ev.kind] || "•";
        el.style.left = `${pct(ev.t)}%`;
        el.addEventListener("mousemove", (e) => {
          showTip(`${fmtClock(state.started, ev.t)} · ${escapeHtml(ev.label)}`, e.clientX);
        });
        el.addEventListener("mouseleave", () => { tip.hidden = true; });
        eventsEl.appendChild(el);
      }
    }

    function attach(plot) {
      state.plot = plot;
      if (plot && !plot._hrvSegHook) {
        plot._hrvSegHook = true;
        (plot.hooks.setScale ||= []).push((_u, key) => { if (key === "x") render(); });
      }
      render();
    }

    return {
      set(data, plot, startedUnix) {
        state = { ...state, data, started: startedUnix, selected: null };
        card.hidden = true;
        tip.hidden = true;
        attach(plot);
      },
      setConfidentOnly(on) {
        state.confidentOnly = !!on;
        render();
      },
      refresh: render,
    };
  }

  global.HrvSegmentStrip = { create, CONF_LABEL, KIND_LABEL };
})(window);
