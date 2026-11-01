/* Shared helpers: formatting, the tooltip, the fee-rank ramp. No dependencies. */
(function () {
  "use strict";

  const SVGNS = "http://www.w3.org/2000/svg";

  function isNum(x) {
    return typeof x === "number" && isFinite(x);
  }
  function pct(x, d) {
    return isNum(x) ? (x * 100).toFixed(d === undefined ? 1 : d) + "%" : "–";
  }
  function ci(c, d) {
    if (!c || !isNum(c[0]) || !isNum(c[1])) return "";
    const k = d === undefined ? 1 : d;
    return "[" + (c[0] * 100).toFixed(k) + ", " + (c[1] * 100).toFixed(k) + "]";
  }
  function int(x) {
    return isNum(x) ? Math.round(x).toLocaleString("en-US") : "–";
  }
  function dec(x, d) {
    return isNum(x) ? x.toFixed(d === undefined ? 2 : d) : "–";
  }
  function theta(x) {
    if (!isNum(x)) return "–";
    return x >= 12 - 1e-9 ? "≥ 12" : x.toFixed(2);
  }
  function short(key) {
    if (typeof key !== "string") return "–";
    return key.length > 10 ? key.slice(0, 4) + "…" + key.slice(-4) : key;
  }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }
  function plural(n, word) {
    return int(n) + " " + (n === 1 ? word : word + "s");
  }
  function el(tag, attrs, html) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (html !== undefined) e.innerHTML = html;
    return e;
  }
  function svg(tag, attrs) {
    const e = document.createElementNS(SVGNS, tag);
    if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  /* -- tooltip: any element with data-tip (HTML) shows it on hover ---------------- */
  const tip = document.getElementById("tip");
  let tipOwner = null;
  function showTip(html, x, y) {
    if (!tip) return;
    tip.innerHTML = html;
    tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    const vw = window.innerWidth, vh = window.innerHeight;
    let left = x + 14, top = y + 14;
    if (left + w > vw - 8) left = Math.max(8, x - w - 14);
    if (top + h > vh - 8) top = Math.max(8, y - h - 14);
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  function hideTip() {
    if (tip) tip.hidden = true;
    tipOwner = null;
  }
  document.addEventListener("pointermove", (ev) => {
    const t = ev.target.closest && ev.target.closest("[data-tip]");
    if (t) {
      tipOwner = t;
      showTip(t.getAttribute("data-tip"), ev.clientX, ev.clientY);
    } else if (tipOwner) {
      hideTip();
    }
  });
  document.addEventListener("focusin", (ev) => {
    const t = ev.target.closest && ev.target.closest("[data-tip]");
    if (!t) return;
    const r = t.getBoundingClientRect();
    tipOwner = t;
    showTip(t.getAttribute("data-tip"), r.left, r.bottom);
  });
  document.addEventListener("focusout", hideTip);
  window.addEventListener("scroll", hideTip, { passive: true });

  /* -- fee-rank ramp: one hue in OKLCH, endpoints from CSS custom properties ------- */
  function cssNum(name) {
    return parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name));
  }
  function cssStr(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function oklchToRgb(L, C, h) {
    const a = C * Math.cos((h * Math.PI) / 180), b = C * Math.sin((h * Math.PI) / 180);
    const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
    const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
    const s_ = L - 0.0894841775 * a - 1.291485548 * b;
    const l = l_ ** 3, m = m_ ** 3, s = s_ ** 3;
    const lin = [
      4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
      -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
      -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
    ];
    return lin.map((x) => {
      x = Math.min(1, Math.max(0, x));
      return x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055;
    });
  }
  function hex(rgb) {
    return "#" + rgb.map((v) => Math.round(v * 255).toString(16).padStart(2, "0")).join("");
  }
  function lum(rgb) {
    const [r, g, b] = rgb.map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }
  function hexRgb(h) {
    h = h.replace("#", "");
    if (h.length === 3) h = h.split("").map((c) => c + c).join("");
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  }
  function contrast(a, b) {
    const la = lum(a), lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }
  let rampCache = null;
  function ramp(n) {
    /* n colours, index 0 = highest fee. Each with the ink that reads best on it. */
    const key = n + "|" + cssStr("--ramp-l0") + cssStr("--ramp-h");
    if (rampCache && rampCache.key === key) return rampCache.steps;
    const l0 = cssNum("--ramp-l0"), l1 = cssNum("--ramp-l1"), c0 = cssNum("--ramp-c0"), c1 = cssNum("--ramp-c1"), h = cssNum("--ramp-h");
    const inks = [cssStr("--ramp-ink-dark"), cssStr("--ramp-ink-light")];
    const steps = [];
    for (let i = 0; i < n; i++) {
      const t = n > 1 ? i / (n - 1) : 0;
      const rgb = oklchToRgb(l0 + (l1 - l0) * t, c0 + (c1 - c0) * t, h);
      const ink = contrast(rgb, hexRgb(inks[0])) >= contrast(rgb, hexRgb(inks[1])) ? inks[0] : inks[1];
      steps.push({ fill: hex(rgb), ink: ink });
    }
    rampCache = { key: key, steps: steps };
    return steps;
  }
  function onScheme(fn) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      rampCache = null;
      fn();
    };
    if (mq.addEventListener) mq.addEventListener("change", handler);
    else if (mq.addListener) mq.addListener(handler);
  }

  window.PREC = { isNum, pct, ci, int, dec, theta, short, esc, plural, el, svg, ramp, onScheme, hideTip };
})();
