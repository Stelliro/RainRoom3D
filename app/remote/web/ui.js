// Small UI building blocks: element helper, slider, toggle, compass dial,
// stepper, segmented control, bottom sheet and toast.

import { svg, hydrateIcons } from "./icons.js";

let uid = 0;
const nextId = (p) => `${p}-${++uid}`;

/** h("div.cls#id", {attrs}, ...children) */
export function h(tag, attrs, ...kids) {
  const m = /^([a-z0-9]+)((?:[.#][\w-]+)*)$/i.exec(tag) || [null, tag, ""];
  const el = document.createElement(m[1]);
  for (const part of (m[2] || "").match(/[.#][\w-]+/g) || []) {
    if (part[0] === ".") el.classList.add(part.slice(1)); else el.id = part.slice(1);
  }
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "on") { for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn); }
      else if (k === "html") el.innerHTML = v;
      else if (k === "text") el.textContent = v;
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  for (const k of kids.flat()) {
    if (k === null || k === undefined || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}
export const icon = (name, cls = "ico ico--sm") => h("span", { class: cls, "aria-hidden": "true", html: svg(name) });

const ease = (t) => 1 - Math.pow(1 - t, 3);

// ---------------------------------------------------------------- slider
export function slider(o) {
  // o: {label, lo, hi, step, left, right, help, fill, format(v), onInput(v, final), value}
  const id = nextId("sl");
  const valEl = h("span.slider-row__value", { id: id + "-v", "aria-hidden": "true" });
  const input = h("input.range", {
    type: "range", id, min: o.lo, max: o.hi, step: o.step || 0.01, value: o.value ?? o.lo,
    "aria-describedby": o.help ? id + "-h" : null,
  });
  if (o.fill === "window") input.classList.add("range--window");
  // The native range never sees the finger (pointer-events: none in CSS):
  // the wrap below decides. A touch only moves the value after a clearly
  // sideways drag, so a scroll that starts on a slider just scrolls.
  const wrap = h("div.range-wrap", null, input);
  const helpEl = o.help ? h("p.slider-row__help", { id: id + "-h", hidden: true }, o.help) : null;
  const helpBtn = o.help ? h("button.help-btn", { type: "button", "aria-label": `About ${o.label}`, "aria-expanded": "false", "aria-controls": id + "-h",
    on: { click: () => { const open = helpEl.hidden; helpEl.hidden = !open; helpBtn.setAttribute("aria-expanded", String(open)); } } }, icon("info", "ico")) : null;
  const row = h("div.slider-row",
    null,
    h("div.slider-row__head", null,
      h("label.slider-row__label", { for: id }, o.label, helpBtn), valEl),
    helpEl,
    wrap,
    (o.left || o.right) ? h("div.slider-row__ends", { "aria-hidden": "true" }, h("span", null, o.left || ""), h("span", null, o.right || "")) : null);

  const api = { el: row, input, dragging: false, value: Number(input.value), _anim: 0, _disp: null };
  const paint = () => {
    const p = (Number(input.value) - o.lo) / ((o.hi - o.lo) || 1) * 100;
    input.style.setProperty("--p", `${Math.max(0, Math.min(100, p))}%`);
    const txt = api._disp ?? (o.format ? o.format(Number(input.value)) : String(input.value));
    valEl.textContent = txt;
    input.setAttribute("aria-valuetext", txt);
  };
  const local = (final) => {
    cancelAnimationFrame(api._anim);
    api.value = Number(input.value);
    api._disp = null;                 // our own number until the server's words arrive
    paint();
    o.onInput && o.onInput(api.value, final);
  };
  // keyboard (and screen readers) still use the native input
  input.addEventListener("input", () => local(false));
  input.addEventListener("change", () => local(true));

  const SLOP = 8, THUMB = 13;              // px of sideways travel before a touch drags; thumb radius
  let gest = null;
  const valueAt = (clientX) => {
    const r = wrap.getBoundingClientRect();
    const k = Math.max(0, Math.min(1, (clientX - r.left - THUMB) / Math.max(1, r.width - 2 * THUMB)));
    const st = Number(o.step) || 0.01;
    let v = o.lo + k * (o.hi - o.lo);
    v = Math.round((v - o.lo) / st) * st + o.lo;
    return Math.max(o.lo, Math.min(o.hi, Number(v.toFixed(6))));
  };
  const moveTo = (clientX) => {
    const v = valueAt(clientX);
    if (Math.abs(v - Number(input.value)) < 1e-9) return;
    input.value = v;
    local(false);
  };
  const begin = (e) => {
    gest.active = true;
    api.dragging = true;
    cancelAnimationFrame(api._anim);
    row.classList.add("is-dragging");
    try { wrap.setPointerCapture(e.pointerId); } catch (err) {}
    try { input.focus({ preventScroll: true }); } catch (err) {}
    moveTo(e.clientX);
  };
  const finish = (commit) => {
    const g = gest; gest = null;
    row.classList.remove("is-dragging");
    if (!g) return;
    if (g.active) { api.dragging = false; if (commit) local(true); }
  };
  wrap.addEventListener("pointerdown", (e) => {
    if (input.disabled || (e.pointerType === "mouse" && e.button !== 0)) return;
    gest = { id: e.pointerId, x: e.clientX, y: e.clientY, active: false };
    if (e.pointerType === "mouse") { e.preventDefault(); begin(e); }
  });
  wrap.addEventListener("pointermove", (e) => {
    if (!gest || e.pointerId !== gest.id) return;
    if (gest.active) { moveTo(e.clientX); return; }
    const dx = Math.abs(e.clientX - gest.x), dy = Math.abs(e.clientY - gest.y);
    if (dy > SLOP && dy >= dx) { gest = null; return; }      // a scroll: never touch the value
    if (dx > SLOP && dx > dy * 1.2) begin(e);
  });
  wrap.addEventListener("pointerup", (e) => { if (gest && e.pointerId === gest.id) finish(true); });
  wrap.addEventListener("pointercancel", (e) => { if (gest && e.pointerId === gest.id) finish(gest.active); });
  wrap.addEventListener("lostpointercapture", () => { if (gest && gest.active) finish(true); });
  input.addEventListener("blur", () => { if (!gest) api.dragging = false; });

  /** Value from outside. Never moves a slider the user is dragging. */
  api.set = (v, { animate = false, display = null } = {}) => {
    if (api.dragging) return;
    if (display !== null) api._disp = display;
    v = Number(v);
    if (!Number.isFinite(v)) return;
    const from = Number(input.value);
    api.value = v;
    if (!animate || document.hidden || Math.abs(from - v) < 1e-9) { input.value = v; paint(); return; }
    cancelAnimationFrame(api._anim);
    row.classList.add("is-remote");
    const t0 = performance.now(), dur = 320;
    const stepF = (t) => {
      if (api.dragging) { row.classList.remove("is-remote"); return; }
      const k = Math.min(1, (t - t0) / dur);
      input.value = from + (v - from) * ease(k);
      paint();
      if (k < 1) api._anim = requestAnimationFrame(stepF);
      else { input.value = v; paint(); setTimeout(() => row.classList.remove("is-remote"), 500); }
    };
    api._anim = requestAnimationFrame(stepF);
  };
  api.setDisplay = (txt) => { api._disp = txt; paint(); };
  api.setDisabled = (off) => {
    row.classList.toggle("is-disabled", !!off);
    input.disabled = !!off;
    input.setAttribute("aria-disabled", String(!!off));
    if (off && gest) finish(false);
  };
  paint();
  return api;
}

// ---------------------------------------------------------------- toggle
export function toggle(o) {
  // o: {label, sub, help, value, onChange(b)}
  const id = nextId("tg");
  const sw = h("button.switch", { type: "button", role: "switch", id, "aria-checked": String(!!o.value), "aria-labelledby": id + "-l",
    "aria-describedby": o.help ? id + "-h" : null });
  const sub = h("span.toggle-row__sub", { id: id + "-h" }, o.sub || "");
  const row = h("div.toggle-row", null,
    h("span.toggle-row__text", null, h("span.toggle-row__label", { id: id + "-l" }, o.label), (o.sub || o.help) ? sub : null),
    sw);
  if (!o.sub && o.help) sub.textContent = o.help;
  const api = { el: row, value: !!o.value };
  sw.addEventListener("click", () => { api.value = !api.value; sw.setAttribute("aria-checked", String(api.value)); o.onChange && o.onChange(api.value); });
  row.addEventListener("click", (e) => { if (e.target === row || e.target.closest(".toggle-row__text")) sw.click(); });
  api.set = (b) => { api.value = !!b; sw.setAttribute("aria-checked", String(api.value)); };
  api.setSub = (t) => { sub.textContent = t; };
  return api;
}

// ---------------------------------------------------------------- dial
export function dial(o) {
  // o: {label, value (deg), snap, onInput(deg, final), arrow: "toward"|"facing"}
  const R = 62;
  const NS = "http://www.w3.org/2000/svg";
  const s = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v); return e; };
  const root = s("svg", { viewBox: "-74 -74 148 148", "aria-hidden": "true" });
  root.append(s("circle", { class: "dial__face", r: R + 8 }));
  for (let a = 0; a < 360; a += 15) {
    const major = a % 90 === 0, r1 = R + 6, r0 = major ? R - 4 : R;
    const t = a * Math.PI / 180;
    root.append(s("line", { class: "dial__tick" + (major ? " dial__tick--major" : ""), x1: Math.sin(t) * r0, y1: -Math.cos(t) * r0, x2: Math.sin(t) * r1, y2: -Math.cos(t) * r1 }));
  }
  for (const [a, txt] of [[0, "N"], [90, "E"], [180, "S"], [270, "W"]]) {
    const t = a * Math.PI / 180, r = R - 19;
    const tx = s("text", { class: "dial__lbl" + (a === 0 ? " dial__lbl--n" : ""), x: Math.sin(t) * r, y: -Math.cos(t) * r });
    tx.textContent = txt; root.append(tx);
  }
  const g = s("g", {});
  g.append(s("line", { class: "dial__tail", x1: 0, y1: 22, x2: 0, y2: 8 }));
  g.append(s("path", { class: "dial__arrow", d: "M0 -32 L9 -12 L0 -16 L-9 -12 Z" }));
  g.append(s("circle", { class: "dial__hub", r: 6 }));
  g.append(s("circle", { class: "dial__knob", cy: -R, r: 9 }));
  root.append(g);
  const el = h("div.dial", { role: "slider", tabindex: "0", "aria-label": o.label, "aria-valuemin": "0", "aria-valuemax": "359" });
  el.append(root);
  const api = { el, value: Number(o.value) || 0, dragging: false, _anim: 0 };
  const paint = (v) => {
    g.setAttribute("transform", `rotate(${v})`);
    el.setAttribute("aria-valuenow", String(Math.round(v)));
    el.setAttribute("aria-valuetext", o.valueText ? o.valueText(v) : `${Math.round(v)} degrees`);
  };
  const norm = (v) => ((Math.round(v) % 360) + 360) % 360;
  const fromEvent = (e) => {
    const r = el.getBoundingClientRect();
    const dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
    let a = Math.atan2(dx, -dy) * 180 / Math.PI;
    const snap = o.snap || 5;
    a = Math.round(a / snap) * snap;
    return norm(a);
  };
  const emit = (v, final) => { api.value = v; paint(v); o.onInput && o.onInput(v, final); };
  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    try { el.setPointerCapture(e.pointerId); } catch (err) {}
    api.dragging = true; cancelAnimationFrame(api._anim); el.focus({ preventScroll: true });
    emit(fromEvent(e), false);
  });
  el.addEventListener("pointermove", (e) => { if (api.dragging) emit(fromEvent(e), false); });
  const end = (e) => { if (!api.dragging) return; api.dragging = false; emit(api.value, true); };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
  el.addEventListener("keydown", (e) => {
    const st = e.shiftKey ? 15 : 5;
    let v = null;
    if (e.key === "ArrowRight" || e.key === "ArrowUp") v = api.value + st;
    else if (e.key === "ArrowLeft" || e.key === "ArrowDown") v = api.value - st;
    else if (e.key === "Home") v = 0;
    else if (e.key === "PageUp") v = api.value + 45;
    else if (e.key === "PageDown") v = api.value - 45;
    if (v !== null) { e.preventDefault(); emit(norm(v), true); }
  });
  api.set = (v, { animate = false } = {}) => {
    v = norm(Number(v) || 0);
    if (api.dragging) return;
    const from = api.value;
    api.value = v;
    if (!animate || document.hidden) { paint(v); return; }
    let d = v - from; if (d > 180) d -= 360; if (d < -180) d += 360;
    cancelAnimationFrame(api._anim);
    const t0 = performance.now();
    const st = (t) => {
      if (api.dragging) return;
      const k = Math.min(1, (t - t0) / 360);
      paint(from + d * ease(k));
      if (k < 1) api._anim = requestAnimationFrame(st);
    };
    api._anim = requestAnimationFrame(st);
  };
  api.setOff = (off) => el.classList.toggle("is-off", !!off);
  paint(api.value);
  return api;
}

// ---------------------------------------------------------------- stepper
export function stepper(o) {
  // o: {label, unit, min, max, step, decimals, value, onCommit(v), placeholder}
  const id = nextId("st");
  const dec = o.decimals ?? 2;
  const input = h("input.input", { id, type: "text", inputmode: "decimal", autocomplete: "off", enterkeyhint: "done",
    "aria-label": o.unit ? `${o.label} in ${o.unitLong || o.unit}` : o.label });
  const fmt = (v) => (Math.round(v * Math.pow(10, dec)) / Math.pow(10, dec)).toString();
  const parse = () => { const t = String(input.value).replace(",", ".").trim(); const v = parseFloat(t); return Number.isFinite(v) ? v : null; };
  const api = { el: null, input, value: Number(o.value) || 0 };
  const commit = (v) => {
    if (v === null) { input.value = fmt(api.value); return; }
    v = Math.max(o.min, Math.min(o.max, v));
    input.value = fmt(v);
    if (Math.abs(v - api.value) < 1e-9) return;
    api.value = v;
    o.onCommit && o.onCommit(v);
  };
  const bump = (dir) => commit(Math.round(((parse() ?? api.value) + dir * o.step) / o.step) * o.step);
  const minus = h("button", { type: "button", "aria-label": `Less ${o.label.toLowerCase()}`, on: { click: () => bump(-1) } }, icon("minus"));
  const plus = h("button", { type: "button", "aria-label": `More ${o.label.toLowerCase()}`, on: { click: () => bump(1) } }, icon("plus"));
  input.addEventListener("blur", () => commit(parse()));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); input.blur(); } });
  const box = h("div.stepper", null, minus, input, o.unit ? h("span.stepper__unit", { "aria-hidden": "true" }, o.unit) : null, plus);
  api.el = h("div.field", null, h("label.field__label", { for: id }, o.label), box);
  api.set = (v) => { api.value = Number(v); if (document.activeElement !== input) input.value = fmt(api.value); };
  api.set(api.value);
  return api;
}

// ---------------------------------------------------------------- segmented
export function segmented(o) {
  // o: {label, options:[{value,label,icon}], value, onChange(v)}
  const lid = nextId("sg");
  const box = h("div.seg", { role: "radiogroup", "aria-labelledby": o.labelId || lid });
  const api = { el: box, value: o.value };
  const btns = o.options.map((op) => {
    const b = h("button.seg__btn", { type: "button", role: "radio", "aria-checked": String(op.value === o.value), dataset: { v: op.value } },
      op.icon ? icon(op.icon) : null, op.label);
    b.addEventListener("click", () => { if (api.value === op.value) return; api.set(op.value); o.onChange && o.onChange(op.value); });
    return b;
  });
  box.append(...btns);
  box.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const i = o.options.findIndex((op) => op.value === api.value);
    const j = (i + (e.key === "ArrowRight" ? 1 : -1) + o.options.length) % o.options.length;
    btns[j].click(); btns[j].focus(); e.preventDefault();
  });
  api.set = (v) => {
    api.value = v;
    btns.forEach((b, i) => { const on = o.options[i].value === v; b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1; });
  };
  api.set(o.value);
  if (o.label) {
    api.el = h("div.field", null, h("span.field__label", { id: lid }, o.label), box);
  }
  return api;
}

// ---------------------------------------------------------------- sheet
export class Sheet {
  constructor() {
    this.el = document.getElementById("sheet");
    this.scrim = document.getElementById("scrim");
    this.body = document.getElementById("sheetBody");
    this.title = document.getElementById("sheetTitle");
    this.kind = null;
    this.onClose = null;
    this._ret = null;
    document.getElementById("sheetClose").addEventListener("click", () => this.close());
    this.scrim.addEventListener("click", () => this.close());
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && this.kind) this.close(); });
    this._swipe(document.getElementById("sheetGrab"));
    this._swipe(document.getElementById("sheetHead"));
    this.el.addEventListener("keydown", (e) => this._trap(e));
  }
  open(kind, title, build, { onClose } = {}) {
    const wasOpen = !!this.kind;
    if (!wasOpen) this._ret = document.activeElement;
    this.kind = kind;
    this.onClose = onClose || null;
    this.title.textContent = title;
    this.body.replaceChildren();
    build(this.body);
    hydrateIcons(this.body);
    this.el.hidden = false; this.scrim.hidden = false;
    this.body.scrollTop = 0;                 // after un-hiding: hidden boxes keep the old offset
    document.getElementById("app").classList.add("has-sheet");
    requestAnimationFrame(() => requestAnimationFrame(() => {
      this.body.scrollTop = 0;
      this.el.classList.add("is-open"); this.scrim.classList.add("is-open");
      if (!wasOpen) this.el.focus({ preventScroll: true });
    }));
  }
  setTitle(t) { this.title.textContent = t; }
  close() {
    if (!this.kind) return;
    const cb = this.onClose;
    this.kind = null; this.onClose = null;
    if (document.activeElement && this.el.contains(document.activeElement)) document.activeElement.blur();
    this.el.classList.remove("is-open"); this.scrim.classList.remove("is-open");
    this.el.style.transform = "";
    setTimeout(() => { if (!this.kind) { this.el.hidden = true; this.scrim.hidden = true; this.body.replaceChildren(); } }, 280);
    document.getElementById("app").classList.remove("has-sheet");
    if (this._ret && this._ret.focus) { try { this._ret.focus({ preventScroll: true }); } catch (e) {} }
    cb && cb();
  }
  _swipe(handle) {
    let y0 = null, dy = 0, id = null, t0 = 0;
    handle.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button")) return;
      y0 = e.clientY; dy = 0; id = e.pointerId; t0 = performance.now();
      try { handle.setPointerCapture(id); } catch (err) {}
      this.el.classList.add("is-dragging");
    });
    handle.addEventListener("pointermove", (e) => {
      if (y0 === null || e.pointerId !== id) return;
      dy = Math.max(0, e.clientY - y0);
      this.el.style.transform = `translateY(${dy}px)`;
    });
    const end = () => {
      if (y0 === null) return;
      this.el.classList.remove("is-dragging");
      const fast = dy > 40 && performance.now() - t0 < 250;
      if (dy > 110 || fast) this.close(); else this.el.style.transform = "";
      y0 = null;
    };
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  }
  _trap(e) {
    if (e.key !== "Tab") return;
    const f = [...this.el.querySelectorAll("button, input, select, textarea, [tabindex]:not([tabindex='-1'])")]
      .filter((x) => !x.disabled && x.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
    else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
  }
}

// ---------------------------------------------------------------- toast
let toastTimer = 0;
export function toast(msg, kind = "info", ms = 3200) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.dataset.kind = kind;
  el.hidden = false;
  requestAnimationFrame(() => el.classList.add("is-on"));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.classList.remove("is-on"); setTimeout(() => { el.hidden = true; }, 220); }, ms);
}

/** Calls fn at most every `ms`; the last call always goes through. */
export function throttle(fn, ms) {
  let last = 0, timer = 0, pending = null;
  const run = () => { last = performance.now(); timer = 0; const a = pending; pending = null; fn(...a); };
  const t = (...args) => {
    pending = args;
    const wait = ms - (performance.now() - last);
    if (wait <= 0 && !timer) run();
    else if (!timer) timer = setTimeout(run, Math.max(0, wait));
  };
  t.flush = (...args) => { clearTimeout(timer); timer = 0; pending = args.length ? args : pending; if (pending) run(); };
  t.cancel = () => { clearTimeout(timer); timer = 0; pending = null; };
  return t;
}
