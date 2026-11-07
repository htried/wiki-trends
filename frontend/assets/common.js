// Shared helpers: API calls, formatting, URL state, charts.
const API = () => (window.WIKI_TRENDS_API || "").replace(/\/$/, "");
const PROJECTS = {enwiki: "English", dewiki: "German", eswiki: "Spanish", frwiki: "French", itwiki: "Italian", nlwiki: "Dutch", ptwiki: "Portuguese"};
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c]);
const nice = t => String(t).replace(/_/g, " ");
const fmt = n => n == null ? "–" : Math.round(n).toLocaleString("en-US");
const fmtX = r => r == null ? "–" : (r >= 10 ? Math.round(r) : r.toFixed(1)) + "×";
const pct = p => p == null ? "–" : (p >= 0.1 ? (100 * p).toFixed(0) : (100 * p).toFixed(1)) + "%";
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// "2026-11-16T15:00Z" or "2026-11-16T15" -> epoch ms of that UTC hour
const hourMs = s => Date.parse(s.slice(0, 13) + ":00:00Z");
const hourLabel = ms => {const d = new Date(ms); return `${MON[d.getUTCMonth()]} ${d.getUTCDate()}, ${String(d.getUTCHours()).padStart(2, "0")}:00 UTC`};
const hourParam = ms => new Date(ms).toISOString().slice(0, 13);

async function api(path) {
  const r = await fetch(API() + path);
  let body = null;
  try { body = await r.json() } catch (e) { /* not JSON */ }
  if (!r.ok) throw new Error((body && body.error) || `HTTP ${r.status}`);
  return body;
}

function params() { return Object.fromEntries(new URLSearchParams(location.search)) }
function setParams(p) {
  const q = new URLSearchParams(Object.entries(p).filter(([, v]) => v != null && v !== ""));
  history.replaceState(null, "", "?" + q.toString());
}
function projectOptions(sel, current) {
  sel.innerHTML = Object.entries(PROJECTS).map(([k, v]) => `<option value="${k}" ${k === current ? "selected" : ""}>${v} Wikipedia</option>`).join("");
}

// ---- charts on canvas; lines drawn as filled columns so they render the same everywhere
function canvasCtx(el, h) {
  const dpr = window.devicePixelRatio || 1, w = el.clientWidth;
  el.width = w * dpr; el.height = h * dpr; el.style.height = h + "px";
  const c = el.getContext("2d"); c.setTransform(dpr, 0, 0, dpr, 0, 0); return [c, w];
}
function drawLine(c, xs, ys, color, lw) {
  c.fillStyle = color;
  for (let i = 0; i < xs.length - 1; i++) {
    if (ys[i] == null || ys[i + 1] == null) continue;
    const x0 = xs[i], x1 = xs[i + 1], y0 = ys[i], y1 = ys[i + 1];
    for (let px = Math.floor(x0); px <= Math.ceil(x1); px++) {
      const t = Math.min(1, Math.max(0, (px - x0) / (x1 - x0 || 1)));
      const ya = y0 + (y1 - y0) * Math.max(0, t - 1 / (x1 - x0 || 1)), yb = y0 + (y1 - y0) * t;
      c.fillRect(px, Math.min(ya, yb) - lw / 2, 1, Math.abs(yb - ya) + lw);
    }
  }
}
// sparkline: log scale, the last point marked; null = below the batch's storage threshold
function sparkline(el, values, color, tries = 0) {
  if (!el.clientWidth) { if (tries < 30) requestAnimationFrame(() => sparkline(el, values, color, tries + 1)); return }
  const [c, w] = canvasCtx(el, 32), n = values.length, pad = 2;
  const f = v => Math.log10((v || 0) + 1), vals = values.map(v => v == null ? null : f(v));
  const lo = Math.min(...vals.filter(v => v != null), 0), hi = Math.max(...vals.filter(v => v != null), 1);
  const xs = values.map((_, i) => pad + (w - 2 * pad) * i / Math.max(1, n - 1));
  const ys = vals.map(v => v == null ? null : 32 - pad - (32 - 2 * pad) * (v - lo) / ((hi - lo) || 1));
  drawLine(c, xs, ys, color || css("--ink"), 1.4);
  if (ys[n - 1] != null) { c.fillStyle = css("--hot"); c.fillRect(xs[n - 1] - 2, ys[n - 1] - 2, 4, 4) }
}
// time-series chart with a shared y axis (log or linear) and an hour tooltip
const AX = {left: 56, right: 10, top: 8, bottom: 22};
function axisChart(el, tip, opts, tries = 0) {
  if (!el.clientWidth) { if (tries < 30) requestAnimationFrame(() => axisChart(el, tip, opts, tries + 1)); return }
  const {lines, startMs, log, height = 240} = opts, [c, w] = canvasCtx(el, height);
  const n = lines[0].values.length, pw = w - AX.left - AX.right, ph = height - AX.top - AX.bottom;
  const f = v => log ? Math.log10(Math.max(v, 0) + 1) : v;
  let hi = 1, lo = log ? Infinity : 0;
  for (const l of lines) for (const v of l.values) if (v != null) { hi = Math.max(hi, f(v)); if (log) lo = Math.min(lo, f(v)) }
  lo = log ? Math.max(0, Math.floor(lo === Infinity ? 0 : lo)) : 0;
  const X = i => AX.left + pw * i / Math.max(1, n - 1), Y = v => AX.top + ph - ph * (f(v) - lo) / ((hi - lo) || 1);
  const ticks = [];
  if (log) { for (let k = Math.ceil(lo); k <= Math.floor(hi); k++) ticks.push(10 ** k) }
  else { const st = 10 ** Math.floor(Math.log10(hi)), step = hi / st > 5 ? 2 * st : hi / st > 2 ? st : st / 2; for (let v = 0; v <= hi; v += step) ticks.push(v) }
  c.font = "11px system-ui"; c.textAlign = "right";
  for (const v of ticks) {
    const yy = Math.round(Y(v)) + .5; c.fillStyle = css("--line"); c.fillRect(AX.left, yy, pw, 1);
    c.fillStyle = css("--mute"); c.fillText(v >= 1e6 ? `${v / 1e6}M` : v >= 1e3 ? `${v / 1e3}k` : `${v}`, AX.left - 6, yy + 4);
  }
  c.textAlign = "center";  // day ticks at 00:00 UTC
  for (let i = 0; i < n; i++) {
    const d = new Date(startMs + i * 3600e3);
    if (d.getUTCHours() === 0) { c.fillStyle = css("--line"); c.fillRect(X(i), AX.top, 1, ph); c.fillStyle = css("--mute"); c.fillText(`${MON[d.getUTCMonth()]} ${d.getUTCDate()}`, X(i), height - 6) }
  }
  const xs = [...Array(n).keys()].map(X);
  for (const l of lines) drawLine(c, xs, l.values.map(v => v == null ? null : Y(v)), l.color, l.width || 1.8);
  el.onmousemove = e => {
    const r = el.getBoundingClientRect(), i = Math.round((e.clientX - r.left - AX.left) / (pw / Math.max(1, n - 1)));
    if (i < 0 || i >= n) { tip.style.display = "none"; return }
    tip.innerHTML = `<b>${hourLabel(startMs + i * 3600e3)}</b>` + lines.map(l => `<div style="color:${l.color}">${esc(l.name)}: <b>${fmt(l.values[i])}</b></div>`).join("");
    tip.style.display = "block"; tip.style.left = Math.min(e.clientX - r.left + 12, r.width - tip.offsetWidth) + "px"; tip.style.top = "6px";
  };
  el.onmouseleave = () => { tip.style.display = "none" };
}
