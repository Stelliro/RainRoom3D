// Touch floor plan for the phone's own room (canvas, devicePixelRatio-crisp).
//
// Coordinates (PROTOCOL.md): meters, origin = south-west floor corner, x east,
// z north. On screen north is up. Window offsets run from the west end
// (north/south walls) or from the south end (east/west walls). Listener yaw:
// radians, 0 = facing north, clockwise from above.
//
// Gestures: drag "You" to move; drag the small round handle in front of You
// (or twist two fingers anywhere) to turn; drag a window along its wall, or
// over to another wall; tap a window to edit it. In "place" mode a tap on or
// near a wall asks for a new window there.

const WALLS = ["north", "east", "south", "west"];
const MARGIN = 0.15;
const TAU = Math.PI * 2;

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

export class PlanView {
  constructor(canvas, opts) {
    this.cv = canvas;
    this.g = canvas.getContext("2d");
    this.o = opts;                 // {onListener(patch, final), onWindow(i, patch, final), onTapWindow(i), onTapYou(), onPlace(wall, along), onHint(text)}
    this.zone = null;
    this.thick = null;
    this.sel = -1;
    this.placing = false;
    this.drag = null;              // {kind:'you'|'turn'|'win'|'twist', ...}
    this.pointers = new Map();
    this.colors = {};
    this.dpr = 1;
    this.W = 0; this.H = 0;
    this._raf = 0;
    this._readColors();
    this._bind();
    const ro = new ResizeObserver(() => this.resize());
    ro.observe(canvas);
    window.matchMedia("(prefers-color-scheme: light)").addEventListener?.("change", () => { this._readColors(); this.draw(); });
  }

  _readColors() {
    const c = this.colors;
    for (const k of ["backdrop", "terrain", "grid", "floor-a", "floor-b", "wall", "wall-fill", "dim", "name",
                     "window", "window-sel", "window-text", "rain", "you", "you-sel", "north"]) {
      c[k] = css(`--c-${k}`);
    }
    c.surface = css("--surface");
    c.text = css("--text");
  }

  resize() {
    const r = this.cv.getBoundingClientRect();
    this.dpr = Math.min(3, window.devicePixelRatio || 1);
    this.W = Math.max(1, r.width); this.H = Math.max(1, r.height);
    this.cv.width = Math.round(this.W * this.dpr);
    this.cv.height = Math.round(this.H * this.dpr);
    this.draw();
  }

  /** New room from the server. During a drag the dragged item keeps its local value. */
  setZone(zone, thick) {
    const d = this.drag;
    if (d && this.zone) {
      if (d.kind === "you" || d.kind === "turn" || d.kind === "twist") {
        const L = this.zone.listener;
        zone = { ...zone, listener: { ...zone.listener, x: L.x, z: L.z, yaw: L.yaw } };
      } else if (d.kind === "win" && zone.windows[d.i] && this.zone.windows[d.i]) {
        const w = this.zone.windows[d.i];
        const wins = zone.windows.slice();
        wins[d.i] = { ...wins[d.i], wall: w.wall, offset: w.offset };
        zone = { ...zone, windows: wins };
      }
    }
    this.zone = zone;
    this.thick = thick || this.thick;
    if (this.sel >= zone.windows.length) this.sel = -1;
    this.draw();
  }

  select(i) { this.sel = i; this.draw(); }
  setPlacing(on) { this.placing = !!on; this.draw(); }

  // ----- geometry -----
  _view() {
    const z = this.zone;
    const w = z.room.width, d = z.room.depth;
    const pad = Math.max(34, Math.min(this.W, this.H) * 0.09);
    const padX = pad, padTop = pad, padBot = pad + 8;
    this._pad = pad;
    const s = Math.min((this.W - 2 * padX) / w, (this.H - padTop - padBot) / d);
    const ox = (this.W - w * s) / 2;
    const oy = padTop + (this.H - padTop - padBot - d * s) / 2;
    return { w, d, s, ox, oy };
  }
  _toS(v, x, z) { return [v.ox + x * v.s, v.oy + (v.d - z) * v.s]; }
  _toM(v, px, py) { return [(px - v.ox) / v.s, v.d - (py - v.oy) / v.s]; }
  _wallLen(wall) { const r = this.zone.room; return (wall === "north" || wall === "south") ? r.width : r.depth; }

  /** Window ends in meters (x,z) for start and end. */
  _winEnds(win) {
    const r = this.zone.room, a = win.offset, b = win.offset + win.width;
    switch (win.wall) {
      case "north": return [[a, r.depth], [b, r.depth]];
      case "south": return [[a, 0], [b, 0]];
      case "east": return [[r.width, a], [r.width, b]];
      default: return [[0, a], [0, b]];
    }
  }
  _outward(wall) { return { north: [0, -1], south: [0, 1], east: [1, 0], west: [-1, 0] }[wall] || [0, -1]; }
  _along(wall, mx, mz) { return (wall === "north" || wall === "south") ? mx : mz; }
  _wallDist(wall, mx, mz) {
    const r = this.zone.room;
    const L = this._wallLen(wall);
    const a = clamp(this._along(wall, mx, mz), 0, L);
    const p = { north: [a, r.depth], south: [a, 0], east: [r.width, a], west: [0, a] }[wall];
    return Math.hypot(mx - p[0], mz - p[1]);
  }
  _nearestWall(mx, mz) {
    let best = null;
    for (const wl of WALLS) {
      const dd = this._wallDist(wl, mx, mz);
      if (!best || dd < best[1]) best = [wl, dd];
    }
    return best;
  }
  _youS(v) { const L = this.zone.listener; return this._toS(v, L.x, L.z); }
  _handleS(v) {
    const [x, y] = this._youS(v), yaw = this.zone.listener.yaw;
    const R = 50;
    return [x + Math.sin(yaw) * R, y - Math.cos(yaw) * R];
  }

  _hit(px, py) {
    if (!this.zone) return null;
    const v = this._view();
    const [hx, hy] = this._handleS(v);
    if (Math.hypot(px - hx, py - hy) < 24) return { kind: "turn" };
    const [yx, yy] = this._youS(v);
    if (Math.hypot(px - yx, py - yy) < 28) return { kind: "you" };
    let best = null;
    this.zone.windows.forEach((win, i) => {
      const [[ax, az], [bx, bz]] = this._winEnds(win);
      const [sx1, sy1] = this._toS(v, ax, az), [sx2, sy2] = this._toS(v, bx, bz);
      const dx = sx2 - sx1, dy = sy2 - sy1, L2 = dx * dx + dy * dy || 1;
      const t = clamp(((px - sx1) * dx + (py - sy1) * dy) / L2, 0, 1);
      const dist = Math.hypot(px - (sx1 + t * dx), py - (sy1 + t * dy));
      if (dist < 26 && (!best || dist < best.dist)) best = { kind: "win", i, dist };
    });
    return best;
  }

  // ----- input -----
  _bind() {
    const cv = this.cv;
    cv.addEventListener("pointerdown", (e) => this._down(e));
    cv.addEventListener("pointermove", (e) => this._move(e));
    cv.addEventListener("pointerup", (e) => this._up(e, false));
    cv.addEventListener("pointercancel", (e) => this._up(e, true));
    cv.addEventListener("lostpointercapture", (e) => { if (this.pointers.has(e.pointerId)) this._up(e, true); });
    // stop iOS from scrolling / zooming the page under the plan
    cv.addEventListener("touchstart", (e) => e.preventDefault(), { passive: false });
    cv.addEventListener("touchmove", (e) => e.preventDefault(), { passive: false });
    cv.addEventListener("gesturestart", (e) => e.preventDefault());
    cv.addEventListener("contextmenu", (e) => e.preventDefault());
    cv.addEventListener("keydown", (e) => this._key(e));
  }

  _pt(e) { const r = this.cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }

  _down(e) {
    if (!this.zone) return;
    try { this.cv.setPointerCapture(e.pointerId); } catch (err) {}
    const [px, py] = this._pt(e);
    this.pointers.set(e.pointerId, [px, py]);
    if (this.pointers.size === 2) {
      // two fingers: twist to turn
      const [a, b] = [...this.pointers.values()];
      this.drag = { kind: "twist", a0: Math.atan2(b[1] - a[1], b[0] - a[0]), yaw0: this.zone.listener.yaw, moved: true };
      this.o.onHint("Twist to turn");
      return;
    }
    if (this.pointers.size > 2) return;
    const hit = this.placing ? null : this._hit(px, py);
    const v = this._view();
    const d = { kind: hit ? hit.kind : "none", x0: px, y0: py, t0: performance.now(), moved: false, id: e.pointerId };
    if (hit && hit.kind === "win") {
      const win = this.zone.windows[hit.i];
      const [mx, mz] = this._toM(v, px, py);
      d.i = hit.i;
      d.grab = this._along(win.wall, mx, mz) - win.offset;
      this.sel = hit.i;
    }
    this.drag = d;
    this.draw();
  }

  _move(e) {
    if (!this.pointers.has(e.pointerId) || !this.drag || !this.zone) return;
    const [px, py] = this._pt(e);
    this.pointers.set(e.pointerId, [px, py]);
    const d = this.drag;
    const v = this._view();
    if (d.kind === "twist") {
      if (this.pointers.size < 2) return;
      const [a, b] = [...this.pointers.values()];
      const ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
      let yaw = (d.yaw0 + (ang - d.a0)) % TAU; if (yaw < 0) yaw += TAU;
      this.zone.listener.yaw = yaw;
      this.o.onListener({ yaw: deg(yaw) }, false);
      this.draw();
      return;
    }
    if (e.pointerId !== d.id) return;
    if (!d.moved && Math.hypot(px - d.x0, py - d.y0) < 7) return;
    d.moved = true;
    const [mx, mz] = this._toM(v, px, py);
    const r = this.zone.room;
    if (d.kind === "you") {
      const L = this.zone.listener;
      L.x = clamp(mx, MARGIN, r.width - MARGIN);
      L.z = clamp(mz, MARGIN, r.depth - MARGIN);
      this.o.onListener({ x: round(L.x), z: round(L.z) }, false);
      this.o.onHint(`${L.x.toFixed(1)} m from the west wall · ${L.z.toFixed(1)} m from the south wall`);
    } else if (d.kind === "turn") {
      const [yx, yy] = this._youS(v);
      let yaw = Math.atan2(px - yx, -(py - yy)); if (yaw < 0) yaw += TAU;
      // snap to the 8 compass points when close
      const step = Math.PI / 4, snap = Math.round(yaw / step) * step;
      if (Math.abs(yaw - snap) < 0.06) yaw = snap % TAU;
      this.zone.listener.yaw = yaw;
      this.o.onListener({ yaw: deg(yaw) }, false);
      this.o.onHint(`Facing ${compass(deg(yaw))} · ${Math.round(deg(yaw))}°`);
    } else if (d.kind === "win") {
      const win = this.zone.windows[d.i];
      if (!win) return;
      // stay on this wall unless another wall is clearly nearer
      const cur = this._wallDist(win.wall, mx, mz);
      const [nw, nd] = this._nearestWall(mx, mz);
      if (nw !== win.wall && nd + 0.3 < cur) {
        win.wall = nw;
        d.grab = Math.min(win.width, this._wallLen(nw) - 0.1) / 2;
      }
      const len = this._wallLen(win.wall);
      win.width = Math.min(win.width, Math.max(0.3, len - 0.1));
      win.offset = clamp(this._along(win.wall, mx, mz) - d.grab, 0, Math.max(0, len - win.width));
      this.o.onWindow(d.i, { wall: win.wall, offset: round(win.offset) }, false);
      this.o.onHint(`${win.name || "Window"} · ${wallName(win.wall)} wall`);
    }
    this.draw();
  }

  _up(e, cancel) {
    if (!this.pointers.has(e.pointerId)) return;
    this.pointers.delete(e.pointerId);
    const d = this.drag;
    if (!d) return;
    if (d.kind === "twist") {
      if (this.pointers.size === 0) {
        this.o.onListener({ yaw: deg(this.zone.listener.yaw) }, true);
        this.drag = null; this.o.onHint("");
      }
      return;
    }
    if (e.pointerId !== d.id) return;
    this.drag = null;
    this.o.onHint("");
    if (cancel && !d.moved) { this.draw(); return; }
    const L = this.zone.listener;
    if (d.moved) {
      if (d.kind === "you") this.o.onListener({ x: round(L.x), z: round(L.z) }, true);
      else if (d.kind === "turn") this.o.onListener({ yaw: deg(L.yaw) }, true);
      else if (d.kind === "win") {
        const w = this.zone.windows[d.i];
        if (w) this.o.onWindow(d.i, { wall: w.wall, offset: round(w.offset) }, true);
      }
    } else {
      // a tap
      const [px, py] = [d.x0, d.y0];
      if (this.placing) {
        const v = this._view();
        const [mx, mz] = this._toM(v, px, py);
        const [wl] = this._nearestWall(mx, mz);
        this.o.onPlace(wl, clamp(this._along(wl, mx, mz), 0, this._wallLen(wl)));
      } else if (d.kind === "win") this.o.onTapWindow(d.i);
      else if (d.kind === "you" || d.kind === "turn") this.o.onTapYou();
      else { this.sel = -1; this.o.onTapWindow(-1); }
    }
    this.draw();
  }

  _key(e) {
    if (!this.zone) return;
    const L = this.zone.listener, r = this.zone.room, st = e.shiftKey ? 0.5 : 0.1;
    let done = true;
    if (e.key === "ArrowLeft") L.x = clamp(L.x - st, MARGIN, r.width - MARGIN);
    else if (e.key === "ArrowRight") L.x = clamp(L.x + st, MARGIN, r.width - MARGIN);
    else if (e.key === "ArrowUp") L.z = clamp(L.z + st, MARGIN, r.depth - MARGIN);
    else if (e.key === "ArrowDown") L.z = clamp(L.z - st, MARGIN, r.depth - MARGIN);
    else if (e.key === "[" || e.key === "q") L.yaw = ((L.yaw - Math.PI / 12) % TAU + TAU) % TAU;
    else if (e.key === "]" || e.key === "e") L.yaw = (L.yaw + Math.PI / 12) % TAU;
    else done = false;
    if (done) {
      e.preventDefault();
      this.o.onListener({ x: round(L.x), z: round(L.z), yaw: deg(L.yaw) }, true);
      this.draw();
    }
  }

  // ----- drawing -----
  draw() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this._paint(); });
  }

  _paint() {
    const g = this.g, c = this.colors;
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.clearRect(0, 0, this.W, this.H);
    g.fillStyle = c.backdrop; g.fillRect(0, 0, this.W, this.H);
    if (!this.zone) return;
    const v = this._view(), r = this.zone.room;
    const [x0, y0] = this._toS(v, 0, r.depth), [x1, y1] = this._toS(v, r.width, 0);

    // yard grid (1 m) behind everything
    g.strokeStyle = c.grid; g.lineWidth = 1;
    g.beginPath();
    for (let m = -Math.ceil(x0 / v.s) - 1; x0 + m * v.s < this.W; m++) { const x = Math.round(x0 + m * v.s) + 0.5; g.moveTo(x, 0); g.lineTo(x, this.H); }
    for (let m = -Math.ceil((this.H - y1) / v.s) - 1; y1 - m * v.s > 0; m++) { const y = Math.round(y1 - m * v.s) + 0.5; g.moveTo(0, y); g.lineTo(this.W, y); }
    g.globalAlpha = 0.5; g.stroke(); g.globalAlpha = 1;

    // rain glow outside open windows
    for (const w of this.zone.windows) {
      if (w.open <= 0.01) continue;
      const [[ax, az], [bx, bz]] = this._winEnds(w);
      const [sx, sy] = this._toS(v, (ax + bx) / 2, (az + bz) / 2);
      const [nx, ny] = this._outward(w.wall);
      const R = Math.max(30, w.width * v.s * 1.1) * (0.6 + 0.6 * w.open);
      const gr = g.createRadialGradient(sx, sy, 0, sx, sy, R);
      gr.addColorStop(0, `rgba(${c.rain},${0.30 * w.open})`);
      gr.addColorStop(1, `rgba(${c.rain},0)`);
      g.fillStyle = gr;
      g.beginPath(); g.arc(sx + nx * 4, sy + ny * 4, R, 0, TAU); g.fill();
    }

    // floor with metre grid
    const fg = g.createLinearGradient(0, y0, 0, y1);
    fg.addColorStop(0, c["floor-a"]); fg.addColorStop(1, c["floor-b"]);
    g.fillStyle = fg; g.fillRect(x0, y0, x1 - x0, y1 - y0);
    g.save(); g.beginPath(); g.rect(x0, y0, x1 - x0, y1 - y0); g.clip();
    g.strokeStyle = c.grid; g.lineWidth = 1; g.beginPath();
    for (let m = 1; m < r.width; m++) { const x = Math.round(x0 + m * v.s) + 0.5; g.moveTo(x, y0); g.lineTo(x, y1); }
    for (let m = 1; m < r.depth; m++) { const y = Math.round(y1 - m * v.s) + 0.5; g.moveTo(x0, y); g.lineTo(x1, y); }
    g.stroke(); g.restore();

    // walls (band outside the floor; at least 6 px so it reads)
    const tcm = this.thick ? (this.thick.wall_cm || this.thick.typical_wall_cm || 12) : 12;
    const tw = clamp(tcm / 100 * v.s, 6, 26);
    g.fillStyle = c["wall-fill"];
    g.fillRect(x0 - tw, y0 - tw, x1 - x0 + 2 * tw, tw);
    g.fillRect(x0 - tw, y1, x1 - x0 + 2 * tw, tw);
    g.fillRect(x0 - tw, y0, tw, y1 - y0);
    g.fillRect(x1, y0, tw, y1 - y0);
    g.strokeStyle = c.wall; g.lineWidth = 1.5;
    g.strokeRect(x0 - 0.75, y0 - 0.75, x1 - x0 + 1.5, y1 - y0 + 1.5);
    g.globalAlpha = 0.6; g.strokeRect(x0 - tw, y0 - tw, x1 - x0 + 2 * tw, y1 - y0 + 2 * tw); g.globalAlpha = 1;

    // windows
    this.zone.windows.forEach((w, i) => this._paintWindow(v, w, i, tw));

    // dimensions, on a small plate so a window's swing drawn there can't hide them
    g.font = `600 12px ${css("--font")}`; g.textAlign = "center"; g.textBaseline = "middle";
    const dimLabel = (txt, cx, cy) => {
      const w = g.measureText(txt).width + 10;
      g.fillStyle = c.surface; g.globalAlpha = 0.88;
      roundRect(g, cx - w / 2, cy - 9, w, 18, 9); g.fill(); g.globalAlpha = 1;
      g.fillStyle = c.dim; g.fillText(txt, cx, cy + 0.5);
    };
    dimLabel(`${fmt(r.width)} m`, (x0 + x1) / 2, y1 + tw + 14);
    g.save(); g.translate(x1 + tw + 14, (y0 + y1) / 2); g.rotate(Math.PI / 2);
    dimLabel(`${fmt(r.depth)} m`, 0, 0); g.restore();

    // north marker
    this._paintNorth(this.W - 22, 22);

    // listener
    this._paintYou(v);

    if (this.placing) {
      g.strokeStyle = c.window; g.lineWidth = 3; g.setLineDash([8, 6]);
      g.strokeRect(x0 - tw / 2, y0 - tw / 2, x1 - x0 + tw, y1 - y0 + tw);
      g.setLineDash([]);
    }
  }

  _paintWindow(v, w, i, tw) {
    const g = this.g, c = this.colors;
    const [[ax, az], [bx, bz]] = this._winEnds(w);
    const [sx1, sy1] = this._toS(v, ax, az), [sx2, sy2] = this._toS(v, bx, bz);
    const [nx, ny] = this._outward(w.wall);
    const sel = i === this.sel;
    // gap in the wall band
    const bx0 = Math.min(sx1, sx2, sx1 + nx * tw), by0 = Math.min(sy1, sy2, sy1 + ny * tw);
    const bx1 = Math.max(sx1, sx2, sx1 + nx * tw), by1 = Math.max(sy1, sy2, sy1 + ny * tw);
    g.fillStyle = c.backdrop; g.fillRect(bx0, by0, bx1 - bx0, by1 - by0);
    g.globalAlpha = 0.22 + 0.6 * w.open;
    g.fillStyle = sel ? c["window-sel"] : c.window;
    g.fillRect(bx0, by0, bx1 - bx0, by1 - by0);
    g.globalAlpha = 1;
    // glass line
    g.strokeStyle = sel ? c["window-sel"] : c.window; g.lineWidth = sel ? 4 : 3; g.lineCap = "round";
    g.beginPath(); g.moveTo(sx1 + nx * tw / 2, sy1 + ny * tw / 2); g.lineTo(sx2 + nx * tw / 2, sy2 + ny * tw / 2); g.stroke();

    // how it opens
    const len = Math.hypot(sx2 - sx1, sy2 - sy1);
    const space = nx < 0 ? sx1 : nx > 0 ? this.W - sx1 : ny < 0 ? sy1 : this.H - sy1;
    const swing = Math.min(len, Math.max(18, space - tw - 8));
    const ox = nx * tw, oy = ny * tw;
    const style = w.open_style;
    if ((style === "casement" || style === "tilt_turn" || style === "custom") && w.open > 0.01) {
      // hinge on the left as seen from inside looking out
      const leftIsStart = (w.wall === "north" || w.wall === "west");
      const hl = (w.hinge_side || "left") === "left";
      const atStart = leftIsStart ? hl : !hl;
      const [hx, hy] = atStart ? [sx1 + ox, sy1 + oy] : [sx2 + ox, sy2 + oy];
      const [fx, fy] = atStart ? [sx2 + ox, sy2 + oy] : [sx1 + ox, sy1 + oy];
      const base = Math.atan2(fy - hy, fx - hx);
      const outA = Math.atan2(ny, nx);
      let diff = outA - base; while (diff > Math.PI) diff -= TAU; while (diff < -Math.PI) diff += TAU;
      const ang = Math.sign(diff) * (w.open * (w.max_angle_deg || 70)) * Math.PI / 180;
      g.strokeStyle = sel ? c["window-sel"] : c.window; g.lineWidth = 2;
      g.beginPath(); g.moveTo(hx, hy); g.lineTo(hx + Math.cos(base + ang) * swing, hy + Math.sin(base + ang) * swing); g.stroke();
      g.globalAlpha = 0.5; g.setLineDash([3, 4]); g.lineWidth = 1.25;
      g.beginPath(); g.arc(hx, hy, swing, Math.min(base, base + ang), Math.max(base, base + ang)); g.stroke();
      g.setLineDash([]); g.globalAlpha = 1;
    } else if (w.open > 0.01) {
      // other styles: a short outward "air" bar sized by how open it is
      const mx = (sx1 + sx2) / 2 + ox, my = (sy1 + sy2) / 2 + oy;
      const l = 6 + 14 * w.open;
      g.strokeStyle = sel ? c["window-sel"] : c.window; g.lineWidth = 2;
      g.beginPath(); g.moveTo(mx, my); g.lineTo(mx + nx * l, my + ny * l); g.stroke();
      g.beginPath(); g.moveTo(mx + nx * l - ny * 4, my + ny * l + nx * 4);
      g.lineTo(mx + nx * (l + 5), my + ny * (l + 5)); g.lineTo(mx + nx * l + ny * 4, my + ny * l - nx * 4); g.stroke();
    }

    // label: open %
    const lx = (sx1 + sx2) / 2 - nx * 16, ly = (sy1 + sy2) / 2 - ny * 16;
    const txt = w.open <= 0.005 ? "Shut" : `${Math.round(w.open * 100)}%`;
    g.font = `700 11.5px ${css("--font")}`; g.textAlign = "center"; g.textBaseline = "middle";
    const tw2 = g.measureText(txt).width + 12;
    g.fillStyle = sel ? c["window-sel"] : c.surface;
    g.globalAlpha = sel ? 1 : 0.92;
    roundRect(g, lx - tw2 / 2, ly - 10, tw2, 20, 10); g.fill(); g.globalAlpha = 1;
    g.fillStyle = sel ? c.backdrop : c["window-text"];
    g.fillText(txt, lx, ly + 0.5);
  }

  _paintNorth(x, y) {
    const g = this.g, c = this.colors;
    g.fillStyle = c.surface; g.globalAlpha = 0.9;
    g.beginPath(); g.arc(x, y, 14, 0, TAU); g.fill(); g.globalAlpha = 1;
    g.fillStyle = c.north;
    g.beginPath(); g.moveTo(x, y - 9); g.lineTo(x + 5, y + 3); g.lineTo(x, y + 1); g.lineTo(x - 5, y + 3); g.closePath(); g.fill();
    g.font = `800 9px ${css("--font")}`; g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText("N", x, y + 9);
  }

  _paintYou(v) {
    const g = this.g, c = this.colors;
    const [x, y] = this._youS(v), yaw = this.zone.listener.yaw;
    const active = this.drag && (this.drag.kind === "you" || this.drag.kind === "turn" || this.drag.kind === "twist");
    const col = active ? c["you-sel"] : c.you;
    // facing wedge
    const a = yaw - Math.PI / 2, half = Math.PI / 5, R = 64;
    const gr = g.createRadialGradient(x, y, 6, x, y, R);
    gr.addColorStop(0, hexA(col, 0.42)); gr.addColorStop(1, hexA(col, 0));
    g.fillStyle = gr;
    g.beginPath(); g.moveTo(x, y); g.arc(x, y, R, a - half, a + half); g.closePath(); g.fill();
    // turn handle
    const [hx, hy] = this._handleS(v);
    g.strokeStyle = hexA(col, 0.7); g.lineWidth = 1.5; g.setLineDash([2, 3]);
    g.beginPath(); g.moveTo(x + Math.sin(yaw) * 14, y - Math.cos(yaw) * 14); g.lineTo(hx, hy); g.stroke(); g.setLineDash([]);
    g.fillStyle = c.surface; g.strokeStyle = col; g.lineWidth = 2.5;
    g.beginPath(); g.arc(hx, hy, 8, 0, TAU); g.fill(); g.stroke();
    // body
    g.fillStyle = col;
    g.beginPath(); g.arc(x, y, 12, 0, TAU); g.fill();
    g.strokeStyle = c.surface; g.lineWidth = 2.5;
    g.beginPath(); g.arc(x, y, 12, 0, TAU); g.stroke();
    // nose dot
    g.fillStyle = c.surface;
    g.beginPath(); g.arc(x + Math.sin(yaw) * 6, y - Math.cos(yaw) * 6, 2.6, 0, TAU); g.fill();
    // label
    g.font = `700 11.5px ${css("--font")}`; g.textAlign = "center"; g.textBaseline = "top";
    const back = yaw + Math.PI;
    const lx = x + Math.sin(back) * 24, ly = y - Math.cos(back) * 24 - 7;
    g.fillStyle = c.name; g.fillText("You", lx, ly);
  }
}

function roundRect(g, x, y, w, h, r) {
  g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
}
function hexA(col, a) {
  const s = String(col).trim();
  if (s.startsWith("#") && (s.length === 7 || s.length === 4)) {
    const h = s.length === 4 ? s.slice(1).split("").map((ch) => ch + ch).join("") : s.slice(1);
    const n = parseInt(h, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  return s;
}
function round(x) { return Math.round(x * 1000) / 1000; }
function fmt(x) { return (Math.round(x * 100) / 100).toString(); }
export function deg(rad) { let d = (rad * 180 / Math.PI) % 360; if (d < 0) d += 360; return Math.round(d * 10) / 10; }
export function compass(d) {
  const names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return names[Math.round(((d % 360) + 360) % 360 / 45) % 8];
}
export function wallName(w) { return { north: "North", south: "South", east: "East", west: "West" }[w] || w; }
