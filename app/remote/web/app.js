// RainRoom phone app: connection, state, and the three tabs.
// Protocol: app/remote/PROTOCOL.md.

import { hydrateIcons, setIcon } from "./icons.js";
import { h, icon, slider, toggle, dial, stepper, segmented, Sheet, toast, throttle } from "./ui.js";
import { RainPlayer } from "./audio.js";
import { PlanView, deg, compass, wallName } from "./plan.js";

const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { const v = localStorage.getItem("rr." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("rr." + k, JSON.stringify(v)); } catch (e) {} },
};

// ---------------------------------------------------------------- identity
const params = new URLSearchParams(location.search);
const KEY = params.get("k") || "";
const ID_RE = /^[A-Za-z0-9_-]{1,40}$/;
function newId() {
  const abc = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";
  const n = new Uint8Array(16);
  try { crypto.getRandomValues(n); } catch (e) { for (let i = 0; i < n.length; i++) n[i] = Math.floor(Math.random() * 256); }
  return [...n].map((b) => abc[b % abc.length]).join("");
}
let CLIENT_ID = params.get("cid");                // testing: a second "phone" in one browser
if (!CLIENT_ID || !ID_RE.test(CLIENT_ID)) {
  CLIENT_ID = store.get("client_id", null);
  if (!CLIENT_ID || !ID_RE.test(CLIENT_ID)) { CLIENT_ID = newId(); store.set("client_id", CLIENT_ID); }
}
function deviceName() {
  const ua = navigator.userAgent || "";
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "iPad";
  if (/iPhone/.test(ua)) return "iPhone";
  if (/Android/.test(ua)) return /Mobile/.test(ua) ? "Android phone" : "Android tablet";
  return "Web browser";
}

// ---------------------------------------------------------------- state
const S = {
  conn: "connecting", gotState: false, fatal: null,
  zone: null, thick: null,
  weather: { values: {}, preset: null, display: {} },
  schema: [], presets: [], presetInfo: [], materials: null, devices: [],
  output: { mode: "off", device: null, playing: false, starting: false },
  volume: 0.75, serverName: "", audio: null,
  target: store.get("target." + CLIENT_ID, "phone") === "pc" ? "pc" : "phone",
  pcDevice: store.get("pcDevice." + CLIENT_ID, null),
  wantPhone: false, tab: "listen", pendingPlace: null, sel: -1,
  rooms: [], zoneNew: false,
};
const live = () => S.conn === "open" && S.gotState;

// ---------------------------------------------------------------- connection
let ws = null, retry = 0, retryTimer = 0, pingTimer = 0, lastFrameAt = 0, lastRestart = 0;
const BACKOFF = [1000, 2000, 5000, 10000];

let offlineToastAt = -Infinity;
function send(obj) {
  if (ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); return true; }
  if (!["ack", "ping", "get_state"].includes(obj.t)) waitingToast();
  return false;
}
function waitingToast() {
  const now = performance.now();
  if (now - offlineToastAt < 2500) return;
  offlineToastAt = now;
  toast(S.gotState ? "Not connected to the computer right now. Try again in a moment." : "Waiting for the computer…");
}
/** Guard for buttons that need the computer: false (and a gentle note) while not connected. */
function needLive() { if (live()) return true; waitingToast(); return false; }

function connect() {
  clearTimeout(retryTimer);
  if (S.fatal) return;
  setConn("connecting");
  let sock;
  try { sock = new WebSocket(`ws://${location.host}/ws`); } catch (e) { scheduleReconnect(); return; }
  ws = sock;
  sock.binaryType = "arraybuffer";
  sock.onopen = () => {
    sock.send(JSON.stringify({ t: "hello", client_id: CLIENT_ID, key: KEY, device_name: deviceName() }));
  };
  sock.onmessage = (e) => {
    if (sock !== ws) return;
    if (typeof e.data === "string") {
      let m; try { m = JSON.parse(e.data); } catch (err) { return; }
      try { onMessage(m); } catch (err) { console.error("message", m && m.t, err); }
    } else {
      lastFrameAt = performance.now();
      player.push(e.data);
    }
  };
  sock.onclose = () => {
    if (sock !== ws) return;
    ws = null;
    clearInterval(pingTimer);
    if (player.state !== "idle") player.stop(true);
    S.output = { ...S.output, mode: S.output.mode === "phone" ? "off" : S.output.mode, starting: false };
    if (S.fatal) { showGate(S.fatal); setConn("closed"); renderListen(); return; }
    setConn("closed");
    renderListen();
    scheduleReconnect();
  };
  sock.onerror = () => {};
}

function scheduleReconnect() {
  const ms = BACKOFF[Math.min(retry, BACKOFF.length - 1)];
  retry++;
  clearTimeout(retryTimer);
  retryTimer = setTimeout(connect, ms);
  $("offlineText").textContent = S.serverName ? `Reconnecting to ${S.serverName}…` : "Reconnecting to the computer…";
}

function setConn(c) {
  S.conn = c;
  $("connDot").dataset.s = c;
  // short on purpose (narrow phones); the PC's name shows under "Play on"
  $("roomSub").textContent = c === "open" ? "Connected"
    : c === "connecting" ? (S.gotState ? "Reconnecting…" : "Connecting…") : "Offline";
  $("roomTitle").title = c === "open" && S.serverName ? `Connected to ${S.serverName}` : "";
  $("offlineBanner").hidden = !(S.gotState && c !== "open") || !!S.fatal;
  const app = document.getElementById("app");
  app.setAttribute("aria-busy", String(c !== "open"));
  app.dataset.live = live() ? "1" : "0";
  $("playBtn").setAttribute("aria-disabled", String(!live()));
}

function onMessage(m) {
  switch (m.t) {
    case "state": return onState(m);
    case "weather": return applyWeather(m, true);
    case "zone": return onZone(m.room, m.thickness);
    case "output": return onOutput(m);
    case "stream":
      if (m.state === "start" && S.wantPhone) { player.beginStream(m); lastFrameAt = performance.now(); renderListen(); }
      return;
    case "volume": S.volume = Number(m.value); volCtl && volCtl.set(S.volume * 100, { animate: !volCtl.dragging }); return;
    case "devices": S.devices = m.devices || []; renderDevices(); return;
    case "rooms": S.rooms = m.rooms || []; if (roomsRefresh) roomsRefresh(); return;
    case "error": return onError(m);
    default: return;
  }
}

function onError(m) {
  let fatal = { bad_key: "bad_key", no_hello: "bad_key", bad_client_id: "bad_client_id", replaced: "replaced" }[m.code];
  if (!fatal && m.code === "busy" && !m.for) fatal = "busy";      // at hello: too many phones
  if (fatal) { S.fatal = fatal; S.fatalMsg = m.message || ""; return; }
  if (m.for === "set_output") { S.wantPhone = false; if (player.state !== "idle") player.stop(); renderListen(); }
  if (m.for === "add_window") { S.pendingPlace = null; }
  toast(m.message || "Something went wrong", "error");
}

function onState(m) {
  const first = !S.gotState;
  S.gotState = true;
  retry = 0;
  S.serverName = m.server_name || "";
  S.schema = m.weather_schema || [];
  S.presets = m.presets || [];
  S.presetInfo = m.preset_info || [];
  S.materials = m.materials || null;
  S.devices = m.devices || [];
  S.audio = m.audio || null;
  S.volume = Number(m.volume ?? 0.75);
  S.output = m.output || S.output;
  S.rooms = m.rooms || [];
  S.zoneNew = !!m.zone_new;
  setConn("open");
  clearInterval(pingTimer);
  pingTimer = setInterval(() => send({ t: "ping" }), 15000);
  if (first || !weatherBuilt) buildWeather();
  applyWeather(m.weather || {}, !first);
  onZone(m.zone, m.thickness);
  volCtl.set(S.volume * 100);
  renderPresets();
  renderDevices();
  renderListen();
  $("pcName").textContent = S.serverName || "the computer";
  // a phone stream always ends with its socket; pick it up again
  if (S.wantPhone && S.output.mode !== "phone" && player.running) requestPhone();
  if (first) maybeWelcome();
}

function onOutput(m) {
  const prev = S.output;
  S.output = { mode: m.mode, device: m.device ?? null, playing: !!m.playing, starting: !!m.starting };
  if (m.error) toast(friendlyError(m.error), "error", 5000);
  if (S.output.mode !== "phone") {
    if (player.state !== "idle") player.stop();
    if (prev.mode === "phone" && !S.output.starting) S.wantPhone = false;
  }
  if (S.output.mode === "pc") { S.pcDevice = S.output.device; store.set("pcDevice." + CLIENT_ID, S.pcDevice); }
  renderListen();
  renderDevices();
}

function friendlyError(e) {
  const s = String(e || "");
  if (/OutputStream|PortAudio|device/i.test(s)) return "That sound output on the PC couldn't start. Try another one.";
  return s.length > 140 ? s.slice(0, 140) + "…" : s;
}

// ---------------------------------------------------------------- audio
const player = new RainPlayer({
  onAck: (msg) => send(msg),
  onState: () => renderListen(),
});

async function requestPhone() {
  try { await player.unlock(); } catch (e) { toast(String(e.message || e), "error"); return; }
  S.wantPhone = true;
  player.expect();
  lastFrameAt = performance.now();
  lastRestart = performance.now();
  if (!send({ t: "set_output", mode: "phone" })) { /* sent again after reconnect */ }
  keepAwake(true);
  renderListen();
}

function startListening() {
  if (S.target === "phone") {
    // the AudioContext is created right here, inside the tap (iOS rule)
    requestPhone();
  } else {
    S.wantPhone = false;
    send({ t: "set_output", mode: "pc", device: S.pcDevice });
  }
}

function stopListening() {
  S.wantPhone = false;
  if (player.state !== "idle") player.stop();
  send({ t: "set_output", mode: "off" });
  keepAwake(false);
  renderListen();
}

let wakeLock = null;
async function keepAwake(on) {
  const note = $("lockNote");
  if (!("wakeLock" in navigator)) {
    note.hidden = !on;
    note.textContent = "Tip: keep the screen on while you listen. Some phones pause the rain when the screen locks.";
    return;
  }
  try {
    if (on && !wakeLock) { wakeLock = await navigator.wakeLock.request("screen"); wakeLock.addEventListener("release", () => { wakeLock = null; }); }
    if (!on && wakeLock) { await wakeLock.release(); wakeLock = null; }
    note.hidden = true;
  } catch (e) {
    note.hidden = !on;
    note.textContent = "Tip: keep the screen on while you listen. Some phones pause the rain when the screen locks.";
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  if (player.ctx && player.ctx.state !== "running") player.ctx.resume().catch(() => {});
  if (S.wantPhone) keepAwake(true);
  if (!ws && !S.fatal) { retry = 0; connect(); }
  else if (S.conn === "open") send({ t: "get_state" });
});

// a stream that went quiet (tab was in the background) gets a fresh start
setInterval(() => {
  if (!S.wantPhone || S.conn !== "open" || S.output.mode !== "phone") return;
  const now = performance.now();
  if (player.state === "buffering" && now - lastFrameAt > 2500 && now - lastRestart > 5000) {
    lastRestart = now;
    player.expect();
    send({ t: "set_output", mode: "phone" });
  }
}, 1000);

function mediaSession(on) {
  const ms = navigator.mediaSession;
  if (!ms) return;
  try {
    if (on) {
      if (window.MediaMetadata) ms.metadata = new MediaMetadata({ title: `Rain in ${S.zone ? S.zone.name : "your room"}`, artist: "RainRoom" });
      ms.setActionHandler("pause", () => stopListening());
      ms.setActionHandler("stop", () => stopListening());
      ms.setActionHandler("play", () => startListening());
      ms.playbackState = "playing";
    } else {
      ms.playbackState = "none";
    }
  } catch (e) {}
}

// ---------------------------------------------------------------- listen tab
function listenStatus() {
  const o = S.output, p = player.state;
  const room = S.zone ? S.zone.name : "this room";
  if (S.conn !== "open") {
    return { s: S.wantPhone || o.mode === "pc" ? "busy" : "off", main: S.gotState ? "Offline" : "Connecting",
      sub: S.gotState ? "Waiting for the computer to come back…" : "Finding RainRoom on the computer…" };
  }
  if (o.mode === "phone") {
    if (p === "playing") return { s: "on", main: "Playing", sub: `The rain in ${room}, sent from the computer` };
    if (p === "buffering") return { s: "busy", main: "Buffering", sub: "Filling up a little sound so it plays smoothly…" };
    return { s: "busy", main: "Connecting", sub: "Getting the rain ready on the computer…" };
  }
  if (o.mode === "pc") {
    const dn = devName(o.device);
    if (o.starting) return { s: "busy", main: "Starting", sub: `Opening ${dn} on the PC…` };
    return { s: "on", main: "Playing on the PC", sub: `${room}'s rain on ${dn}` };
  }
  if (S.wantPhone) return { s: "busy", main: "Connecting", sub: "Getting the rain ready on the computer…" };
  return { s: "off", main: "Stopped",
    sub: S.target === "phone" ? "Tap to hear the rain in this room" : `Tap to play this room's rain on ${devName(S.pcDevice)}` };
}

function devName(idx) {
  if (idx === null || idx === undefined) return "the PC's usual output";
  const d = S.devices.find((x) => x.index === idx);
  return d ? d.name : `output ${idx}`;
}

function renderListen() {
  const st = listenStatus();
  const btn = $("playBtn");
  btn.dataset.s = st.s;
  const playing = st.s !== "off";
  setIcon($("playIcon"), playing ? "stop" : "play");
  $("playLabel").textContent = playing ? "Stop listening" : "Start listening";
  btn.setAttribute("aria-label", playing ? "Stop listening" : "Start listening");
  $("stateMain").textContent = st.main;
  $("stateSub").textContent = st.sub;
  targetSeg.set(S.target);
  $("devicesBox").hidden = S.target !== "pc";
  $("targetNote").textContent = S.target === "phone"
    ? "The computer works out how the rain sounds in this room and sends it to this phone. Headphones sound best."
    : "The computer plays this room's rain on the speaker you pick, so it can fill a room somewhere else in the house. It keeps playing if you close this page.";
  const meter = $("meter");
  // keep its row when stopped, so nothing below jumps when playing starts
  meter.style.visibility = player.state === "playing" ? "visible" : "hidden";
  if (S.output.mode === "pc" && S.output.playing) { meter.dataset.off = ""; $("meterVal").textContent = "On the PC"; }
  else if (player.state !== "playing") { meter.dataset.off = ""; $("meterVal").textContent = "—"; }
  else delete meter.dataset.off;
  mediaSession(player.state === "playing");
  if (!S.wantPhone && S.output.mode !== "phone") $("lockNote").hidden = true;
}

const targetSeg = { el: $("targetSeg"), set(v) { this.el.querySelectorAll(".seg__btn").forEach((b) => { const on = b.dataset.target === v; b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1; }); } };
$("targetSeg").addEventListener("click", (e) => {
  const b = e.target.closest(".seg__btn"); if (!b) return;
  if (!needLive()) return;
  chooseTarget(b.dataset.target);
});
$("targetSeg").addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  e.preventDefault();
  chooseTarget(S.target === "phone" ? "pc" : "phone");
  $("targetSeg").querySelector(`[data-target="${S.target}"]`).focus();
});
function chooseTarget(t) {
  if (t === S.target) return;
  S.target = t; store.set("target." + CLIENT_ID, t);
  const active = S.output.mode !== "off" || S.wantPhone;
  if (active) {
    if (t === "phone") requestPhone();
    else { S.wantPhone = false; if (player.state !== "idle") player.stop(); send({ t: "set_output", mode: "pc", device: S.pcDevice }); }
  }
  renderListen();
}

function renderDevices() {
  const list = $("deviceList");
  const items = [{ index: null, name: "The PC's usual output", sub: "Whatever Windows plays sound on" }, ...S.devices];
  list.replaceChildren(...items.map((d) => {
    const on = d.index === S.pcDevice || (d.index === null && (S.pcDevice === null || S.pcDevice === undefined));
    const live = S.output.mode === "pc" && S.output.playing && S.output.device === d.index;
    const b = h("button.radio", { type: "button", role: "radio", "aria-checked": String(on), tabindex: on ? "0" : "-1" },
      h("span.radio__dot", { "aria-hidden": "true" }),
      h("span.radio__text", null, h("span.radio__name", null, d.name), d.sub ? h("span.radio__sub", null, d.sub) : (d.default ? h("span.radio__sub", null, "Windows default") : null)),
      live ? h("span.radio__live", null, "Playing") : null);
    b.addEventListener("click", () => {
      S.pcDevice = d.index; store.set("pcDevice." + CLIENT_ID, d.index);
      if (S.output.mode === "pc") send({ t: "set_output", mode: "pc", device: d.index });
      renderDevices(); renderListen();
    });
    return b;
  }));
}
$("refreshDevices").addEventListener("click", () => { send({ t: "refresh_devices" }); toast("Looking for sound outputs…"); });

$("playBtn").addEventListener("click", () => {
  if (!needLive()) return;
  const st = listenStatus();
  if (st.s === "off") startListening(); else stopListening();
});

// volume (this room only)
const volCtl = slider({
  label: "Volume", lo: 0, hi: 100, step: 1, value: 75, format: (v) => `${Math.round(v)}%`,
  help: "How loud this room's rain plays. Only this phone's room changes.",
  onInput: (v, final) => { S.volume = v / 100; sendVolume(v / 100); if (final) sendVolume.flush(v / 100); },
});
const sendVolume = throttle((v) => send({ t: "set_volume", value: Math.round(v * 1000) / 1000 }), 100);
$("volumeRow").replaceWith(volCtl.el);
volCtl.el.id = "volumeRow";
volCtl.el.querySelector(".slider-row__label").prepend(icon("volume"));

// level ring + meter
const ring = $("levelRing"), rg = ring.getContext("2d");
let lvl = -90, phase = 0;
function drawLevel() {
  requestAnimationFrame(drawLevel);
  if (S.tab !== "listen" || document.hidden) return;
  const dpr = Math.min(3, window.devicePixelRatio || 1);
  const W = ring.clientWidth, H = ring.clientHeight;
  if (!W) return;
  if (ring.width !== Math.round(W * dpr)) { ring.width = Math.round(W * dpr); ring.height = Math.round(H * dpr); }
  rg.setTransform(dpr, 0, 0, dpr, 0, 0);
  rg.clearRect(0, 0, W, H);
  const db = player.level();
  const pc = S.output.mode === "pc" && S.output.playing;
  const cx = W / 2, cy = H / 2, r0 = 62;
  const css = getComputedStyle(document.documentElement);
  const rain = css.getPropertyValue("--c-rain").trim();
  if (pc) {
    // Playing on a PC output: the phone hears nothing, so no level here, just
    // a slow turning dashed ring that says "running"
    phase = (phase + 0.004) % (Math.PI * 2);
    rg.save(); rg.translate(cx, cy); rg.rotate(phase);
    rg.strokeStyle = `rgba(${rain},0.45)`; rg.lineWidth = 2; rg.setLineDash([3, 9]);
    rg.beginPath(); rg.arc(0, 0, r0 + 10, 0, Math.PI * 2); rg.stroke();
    rg.restore();
    return;
  }
  let target = db === null ? -90 : db;
  lvl = target > lvl ? lvl + (target - lvl) * 0.5 : lvl + (target - lvl) * 0.12;
  const k = Math.max(0, Math.min(1, (lvl + 60) / 54));
  if (db !== null) {
    $("meterFill").style.width = `${Math.round(k * 100)}%`;
    $("meterVal").textContent = db < -80 ? "quiet" : `${Math.round(db)} dB`;
  }
  if (k <= 0.001) return;
  for (let i = 0; i < 3; i++) {
    const r = r0 + 5 + i * 7 + k * 7;
    rg.strokeStyle = `rgba(${rain},${(0.38 - i * 0.11) * k})`;
    rg.lineWidth = 3 - i * 0.7;
    rg.beginPath(); rg.arc(cx, cy, r, 0, Math.PI * 2); rg.stroke();
  }
}
requestAnimationFrame(drawLevel);

// ---------------------------------------------------------------- weather
let weatherBuilt = false;
const ctls = {};               // key -> [control api]
const pending = {};            // key -> {v, at}: our last send, until the server echoes it
const senders = {};

function sendWeather(key, v, final) {
  if (!senders[key]) senders[key] = throttle((val) => send({ t: "set_weather", key, value: val }), 100);
  pending[key] = { v, at: performance.now() };
  S.weather.values[key] = v;
  S.weather.preset = null;
  for (const c of ctls[key] || []) if (!c.dragging) c.set(c._scale ? v * c._scale : v);
  if (final) senders[key].flush(v); else senders[key](v);
  renderPresets(); renderPill(false);
  syncEnabled();
}

/** "downpour · 100 mm/h" -> "Downpour · 100 mm/h" (same case as the preset chips) */
function capWords(s) {
  return String(s).split(" · ").map((p) => /^[a-z]/.test(p) ? p[0].toUpperCase() + p.slice(1) : p).join(" · ");
}

function fmtLocal(s, v) {
  if (s.hi <= 1.0001 && s.lo >= 0) return `${Math.round(v * 100)}%`;
  return `${Math.round(v)}`;
}

function buildWeather() {
  weatherBuilt = true;
  for (const k of Object.keys(ctls)) delete ctls[k];
  const rainBox = $("rainControls"), windBox = $("windControls"), more = $("moreControls");
  const thunderBox = $("thunderControls");
  rainBox.replaceChildren(); windBox.replaceChildren(); more.replaceChildren(); thunderBox.replaceChildren();
  $("thunderCard").hidden = !S.schema.some((s) => s.group === "thunder");
  const add = (key, api) => { (ctls[key] = ctls[key] || []).push(api); return api; };
  let lastGate = null;
  for (const s of S.schema) {
    const box = s.advanced ? more : (s.group === "rain" ? rainBox : s.group === "thunder" ? thunderBox : windBox);
    if (s.advanced && s.enabled_by !== lastGate) {
      lastGate = s.enabled_by;
      const gate = S.schema.find((x) => x.key === s.enabled_by);
      if (gate) more.append(h("div.sub-head", null, h("span", null, gate.label), h("span.tag", { dataset: { gateTag: gate.key } }, "On")));
    }
    let el;
    if (s.kind === "toggle") {
      const t = add(s.key, toggle({ label: s.label, help: s.help, value: !!S.weather.values[s.key],
        onChange: (b) => sendWeather(s.key, b, true) }));
      el = t.el;
    } else if (s.kind === "dial") {
      const side = h("div.dial-side", null,
        h("span.dial-side__value", { id: "dialValue" }, "—"),
        h("span.dial-side__sub", { id: "dialSub" }, ""),
        h("div.dial-steps", null,
          h("button.btn.btn--sm", { type: "button", "aria-label": "Turn the wind 15 degrees left", on: { click: () => nudgeDial(s.key, -15) } }, icon("chevron_left")),
          h("button.btn.btn--sm", { type: "button", "aria-label": "Turn the wind 15 degrees right", on: { click: () => nudgeDial(s.key, 15) } }, icon("chevron_right"))));
      const d = add(s.key, dial({ label: `Wind ${s.label.toLowerCase()}`, value: S.weather.values[s.key] || 0, snap: 5,
        valueText: (v) => `${Math.round(v)} degrees, toward ${compass(v)}`,
        onInput: (v, final) => { $("dialValue").textContent = `${Math.round(v)}° ${compass(v)}`; sendWeather(s.key, v, final); } }));
      el = h("div.ctl", null,
        h("div.slider-row__head", null, h("span.slider-row__label", null, s.label)),
        h("div.dial-row", null, d.el, side),
        h("p.slider-row__help", null, "The arrow shows where the wind blows the rain to."));
    } else {
      const sl = add(s.key, slider({ label: s.label, lo: s.lo, hi: s.hi, step: s.step, left: s.left, right: s.right, help: s.help,
        value: S.weather.values[s.key] ?? s.default, format: (v) => fmtLocal(s, v),
        onInput: (v, final) => sendWeather(s.key, v, final) }));
      el = sl.el;
    }
    el.dataset.key = s.key;
    box.append(el);
  }
  // the Amount slider again on the Listen tab
  const amt = S.schema.find((s) => s.key === "droplet_density");
  if (amt) {
    const q = add(amt.key, slider({ label: amt.label, lo: amt.lo, hi: amt.hi, step: amt.step, left: amt.left, right: amt.right,
      value: S.weather.values[amt.key] ?? amt.default, format: (v) => fmtLocal(amt, v),
      onInput: (v, final) => sendWeather(amt.key, v, final) }));
    q.el.id = "quickAmount";
    $("quickAmount").replaceWith(q.el);
  }
  $("moreBtn").hidden = !more.children.length;
  hydrateIcons($("panel-weather"));
}

function nudgeDial(key, d) {
  const v = (((Number(S.weather.values[key]) || 0) + d) % 360 + 360) % 360;
  for (const c of ctls[key] || []) c.set(v, { animate: true });
  sendWeather(key, v, true);
}

$("moreBtn").addEventListener("click", () => {
  const box = $("moreControls"), open = box.hidden;
  box.hidden = !open;
  $("moreBtn").setAttribute("aria-expanded", String(open));
  $("moreBtn").querySelector("span").textContent = open ? "Fewer wind settings" : "More wind settings";
});

function applyWeather(m, animate) {
  if (!m || !m.values) return;
  const now = performance.now();
  const before = { ...S.weather.values };
  const prevPreset = S.weather.preset;
  let changedByOthers = false;
  for (const [k, v] of Object.entries(m.values)) {
    const p = pending[k];
    if (p) {
      const same = typeof v === "number" ? Math.abs(v - p.v) < 1e-6 : v === p.v;
      if (same || now - p.at > 1500) delete pending[k];
      else continue;   // an older echo of our own drag: don't yank the control back
    }
    if (before[k] !== v && !(k in pending)) changedByOthers = changedByOthers || !p;
    S.weather.values[k] = v;
    const disp = m.display && m.display[k] != null ? capWords(m.display[k]) : null;
    for (const c of ctls[k] || []) {
      if (c.input) c.set(v, { animate: animate && before[k] !== v, display: disp ?? null });
      else c.set(v, { animate: animate && before[k] !== v });
    }
  }
  S.weather.preset = m.preset ?? null;
  S.weather.display = { ...S.weather.display, ...(m.display || {}) };
  const dv = S.weather.values.wind_direction_deg;
  if ($("dialValue") && dv !== undefined && !(ctls.wind_direction_deg || []).some((c) => c.dragging)) {
    $("dialValue").textContent = `${Math.round(dv)}° ${compass(dv)}`;
  }
  if ($("dialSub")) {
    const d = S.weather.display.wind_direction_deg || "";
    const parts = d.split("·").map((x) => x.trim()).filter(Boolean).slice(1);
    $("dialSub").textContent = (Number(S.weather.values.wind_speed) || 0) <= 0.001
      ? "Turn up Strength to hear it" : parts.join(" · ");
  }
  syncEnabled();
  renderPresets();
  renderPill(animate && (changedByOthers || prevPreset !== S.weather.preset));
}

function syncEnabled() {
  for (const s of S.schema) {
    let off = s.enabled_by ? !S.weather.values[s.enabled_by] : false;
    // Distance only matters once thunder is on
    if (s.key === "thunder_distance") off = !((S.weather.values.thunder_rate || 0) > 0.005);
    for (const c of ctls[s.key] || []) if (c.setDisabled) c.setDisabled(off);
  }
  for (const t of document.querySelectorAll("[data-gate-tag]")) {
    const on = !!S.weather.values[t.dataset.gateTag];
    t.textContent = on ? "On" : "Off, turn it on above";
    t.classList.toggle("tag--off", !on);
  }
  const calm = (Number(S.weather.values.wind_speed) || 0) <= 0.001;
  for (const c of ctls.wind_direction_deg || []) c.setOff && c.setOff(calm);
}

function rateText() {
  const d = (S.weather.display || {}).droplet_density || "";
  const m = /([\d.,]+\s*mm\/h)/.exec(d);
  return m ? m[1] : "";
}

function renderPill(flash) {
  if (!S.gotState) {                        // nothing from the computer yet
    $("pillText").textContent = "…";
    $("weatherPill").setAttribute("aria-label", "Shared weather: waiting for the computer");
    return;
  }
  const name = S.weather.preset || "Custom";
  const rate = rateText();
  $("pillText").textContent = rate ? `${name} · ${rate}` : name;
  $("weatherPill").setAttribute("aria-label", `Shared weather: ${name}${rate ? ", " + rate : ""}. Open the weather tab`);
  if (flash) { const p = $("weatherPill"); p.classList.remove("flash"); void p.offsetWidth; p.classList.add("flash"); }
}

function renderPresets() {
  const cur = S.weather.preset;
  for (const boxId of ["quickPresets", "presetChips"]) {
    const box = $(boxId);
    if (box.children.length !== S.presets.length || [...box.children].some((c, i) => c.dataset.name !== S.presets[i])) {
      box.replaceChildren(...S.presets.map((name) => {
        const b = h("button.chip", { type: "button", role: "radio", "aria-checked": "false", dataset: { name } }, name);
        b.addEventListener("click", () => {
          if (!needLive()) return;
          send({ t: "apply_preset", name });
          S.weather.preset = name;
          renderPresets(); renderPill(false);
        });
        return b;
      }));
    }
    for (const c of box.children) c.setAttribute("aria-checked", String(c.dataset.name === cur));
  }
  const info = S.presetInfo.find((p) => p.name === cur);
  $("presetDesc").textContent = info ? info.description : "Custom: your own mix of the settings below.";
  $("presetNow").textContent = cur ? "" : "Custom";
}

// ---------------------------------------------------------------- room tab
const sheet = new Sheet();
let sheetRefresh = null;
const sendListener = throttle((p) => send({ t: "set_listener", ...p }), 120);
let listenerAcc = {};
const winSenders = {};
function sendWindow(i, patch, final) {
  if (!winSenders[i]) winSenders[i] = { acc: {}, t: throttle(() => { const a = winSenders[i].acc; winSenders[i].acc = {}; send({ t: "set_window", index: i, patch: a }); }, 120) };
  Object.assign(winSenders[i].acc, patch);
  if (final) winSenders[i].t.flush(); else winSenders[i].t();
}
function sendListenerPatch(p, final) {
  Object.assign(listenerAcc, p);
  const go = () => { const a = listenerAcc; listenerAcc = {}; return a; };
  if (final) sendListener.flush(go()); else sendListener(go());
}

const plan = new PlanView($("plan"), {
  onListener: (patch, final) => { sendListenerPatch(patch, final); renderFacing(); if (sheet.kind === "you" && sheetRefresh) sheetRefresh(); },
  onWindow: (i, patch, final) => { sendWindow(i, patch, final); renderWindowList(); if (sheet.kind === `win:${i}` && sheetRefresh) sheetRefresh(); },
  onTapWindow: (i) => { S.sel = i; renderWindowList(); if (i >= 0) openWindowSheet(i); },
  onTapYou: () => openYouSheet(),
  onPlace: (wall, along) => {
    setPlacing(false);
    if (!S.zone) return;
    S.pendingPlace = { wall, along, count: S.zone.windows.length };
    send({ t: "add_window", wall });
  },
  onHint: (t) => { $("planHint").textContent = t; },
});

function onZone(room, thick) {
  if (!room) return;
  const prevCount = S.zone ? S.zone.windows.length : 0;
  S.zone = room;
  if (thick) S.thick = thick;
  plan.setZone(room, S.thick);
  room.windows = plan.zone.windows;          // keep the drag overlay plan.setZone applied
  room.listener = plan.zone.listener;
  S.zone = plan.zone;
  const pp = S.pendingPlace;
  if (pp && S.zone.windows.length > pp.count) {
    S.pendingPlace = null;
    const i = S.zone.windows.length - 1, w = S.zone.windows[i];
    const len = (w.wall === "north" || w.wall === "south") ? S.zone.room.width : S.zone.room.depth;
    const off = Math.max(0, Math.min(len - w.width, pp.along - w.width / 2));
    if (w.wall === pp.wall && Math.abs(off - w.offset) > 0.01) {
      w.offset = off;
      send({ t: "set_window", index: i, patch: { offset: Math.round(off * 1000) / 1000 } });
    }
    S.sel = i; plan.select(i);
    toast(`${w.name} added on the ${wallName(w.wall).toLowerCase()} wall`);
  } else if (S.zone.windows.length < prevCount && S.sel >= S.zone.windows.length) {
    S.sel = -1; plan.select(-1);
  }
  renderRoomHeader();
  renderRoomSummary();
  renderWindowList();
  renderFacing();
  if (sheetRefresh) sheetRefresh();
}

function renderRoomHeader() {
  const n = S.zone ? S.zone.name : "Your room";
  $("roomName").textContent = n;
  $("roomTitle").setAttribute("aria-label", `Room name: ${n}. Tap to rename`);
  document.title = `${n} · RainRoom`;
}

const MAT_SHORT = (m) => String(m || "").replace(" Roof", "").replace(" Wall", "").replace(" Window", "");
/** "Glass Window" -> "Glass", "Tin Roof" -> "Tin": the field already says roof or walls */
const matLabel = (m) => { const s = MAT_SHORT(m); return s ? s[0].toUpperCase() + s.slice(1).toLowerCase() : s; };
function renderRoomSummary() {
  const z = S.zone; if (!z) return;
  const r = z.room;
  $("sumMain").textContent = `${num(r.width)} × ${num(r.depth)} m · ${num(r.height)} m ceiling`;
  const rt = S.thick && S.thick.roof_cm > 0 ? ` ${num(S.thick.roof_cm)} cm` : "";
  const wt = S.thick && S.thick.wall_cm > 0 ? ` ${num(S.thick.wall_cm)} cm` : "";
  $("sumSub").textContent = `${matLabel(r.roof_material)} roof${rt} · ${matLabel(r.wall_material)} walls${wt}`;
}
function num(x) { return (Math.round(Number(x) * 100) / 100).toString(); }

function styleLabel(key) {
  const s = (S.materials && S.materials.window_styles || []).find((x) => x.key === key);
  return s ? s.label.replace(/\s*\(.*\)$/, "") : key;
}

function renderWindowList() {
  const z = S.zone; if (!z) return;
  const box = $("windowList");
  box.replaceChildren(...z.windows.map((w, i) => {
    const b = h("button.row", { type: "button", role: "listitem", "aria-label": `${w.name}, ${wallName(w.wall)} wall, ${Math.round(w.open * 100)} percent open. Edit` },
      h("span.ico.row__icon", { "aria-hidden": "true", dataset: { icon: "window" } }),
      h("span.row__text", null, h("span.row__name", null, w.name || `Window ${i + 1}`),
        h("span.row__sub", null, `${wallName(w.wall)} wall · ${num(w.width)} m wide · ${styleLabel(w.open_style)}`)),
      h("span.row__badge", null, w.open <= 0.005 ? "Shut" : `${Math.round(w.open * 100)}%`),
      h("span.ico.ico--sm", { "aria-hidden": "true", dataset: { icon: "chevron_right" } }));
    if (i === S.sel) b.classList.add("is-sel");
    b.addEventListener("click", () => { S.sel = i; plan.select(i); renderWindowList(); openWindowSheet(i); });
    return b;
  }));
  if (!z.windows.length) {
    box.replaceChildren(h("div.row", null, h("span.row__text", null, h("span.row__name", null, "No windows"),
      h("span.row__sub", null, "With every window shut away, the rain only comes through the walls and roof."))));
  }
  hydrateIcons(box);
}

function renderFacing() {
  if (!S.zone) return;
  const d = deg(S.zone.listener.yaw);
  $("facingText").textContent = `Facing ${compass(d)}`;
}

function setPlacing(on) {
  plan.setPlacing(on);
  $("placeBar").hidden = !on;
  $("addWindowBtn").setAttribute("aria-pressed", String(!!on));
  if (on) $("planHint").textContent = "";    // the bar says what to do now
}
$("addWindowBtn").addEventListener("click", () => {
  if (!needLive() || !S.zone) return;
  const max = (S.materials && S.materials.limits && S.materials.limits.max_windows) || 8;
  if (S.zone.windows.length >= max) { toast(`A room can have up to ${max} windows`, "error"); return; }
  setPlacing(!plan.placing);
});
$("placeCancel").addEventListener("click", () => setPlacing(false));
$("roomBtn").addEventListener("click", () => { if (needLive()) openRoomSheet(); });
$("roomSummary").addEventListener("click", () => { if (needLive()) openRoomSheet(); });
$("facingBtn").addEventListener("click", () => { if (needLive()) openYouSheet(); });
$("roomTitle").addEventListener("click", () => { if (needLive()) openRenameSheet(); });

// ----- sheets -----
function section(title, ...kids) { return h("div.sheet__section", null, title ? h("div.sheet__section-title", null, title) : null, ...kids); }
function textField(label, value, onCommit, opts = {}) {
  const id = "tf-" + Math.random().toString(36).slice(2, 8);
  const inp = h("input.input", { id, type: "text", value: value || "", maxlength: "40", autocomplete: "off", enterkeyhint: "done", spellcheck: "false" });
  const commit = () => { const v = inp.value.replace(/\s+/g, " ").trim(); if (v && v !== inp.dataset.last) { inp.dataset.last = v; onCommit(v); } else if (!v) inp.value = inp.dataset.last; };
  inp.dataset.last = value || "";
  inp.addEventListener("blur", commit);
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commit(); if (opts.closeOnEnter) sheet.close(); else inp.blur(); } });
  const f = h("div.field", null, h("label.field__label", { for: id }, label), inp);
  return { el: f, input: inp, set(v) { if (document.activeElement !== inp) { inp.value = v; inp.dataset.last = v; } } };
}
function selectField(label, options, value, onChange) {
  const id = "sf-" + Math.random().toString(36).slice(2, 8);
  const sel = h("select.select", { id });
  for (const o of options) sel.append(h("option", { value: o.value }, o.label));
  sel.value = value;
  sel.addEventListener("change", () => onChange(sel.value));
  const hint = h("span.field__hint");
  const f = h("div.field", null, h("label.field__label", { for: id }, label), sel, hint);
  return { el: f, sel, hint, set(v) { if (document.activeElement !== sel) sel.value = v; } };
}
function thicknessField(label, getCm, getTyp, onText) {
  const id = "th-" + Math.random().toString(36).slice(2, 8);
  const HINT = "Leave empty for the usual build (0 to 150 cm).";
  const inp = h("input.input", { id, type: "text", inputmode: "decimal", autocomplete: "off", enterkeyhint: "done",
    "aria-describedby": id + "-h" });
  const hint = h("span.field__hint", { id: id + "-h" }, HINT);
  const valid = (v) => { if (v === "") return true; const n = Number(v); return Number.isFinite(n) && n >= 0 && n <= 150; };
  const clean = () => inp.value.replace(",", ".").trim();
  let hintTimer = 0;
  const showHint = (bad) => {
    clearTimeout(hintTimer);
    hint.textContent = bad ? "Use 0 to 150 cm, or leave it empty." : HINT;
    hint.classList.toggle("is-error", bad);
    if (bad) hintTimer = setTimeout(() => showHint(false), 5000);
  };
  const t = throttle(() => { const v = clean(); if (valid(v)) onText(v); }, 450);
  inp.addEventListener("input", () => {
    const ok = valid(clean());
    inp.setAttribute("aria-invalid", String(!ok));
    if (ok) { showHint(false); t(); } else t.cancel();
  });
  inp.addEventListener("blur", () => {
    if (valid(clean())) { t.flush(); return; }
    // the room keeps its last good value: show that again, and say why
    t.cancel();
    inp.setAttribute("aria-invalid", "false");
    api.set(true);
    showHint(true);
  });
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); inp.blur(); } });
  const f = h("div.field", null, h("label.field__label", { for: id }, label),
    h("div.unit-input", null, inp, h("span.unit-input__unit", { "aria-hidden": "true" }, "cm")), hint);
  const api = { el: f, set(force = false) {
    const typ = getTyp();
    inp.placeholder = `default ${num(typ)}`;
    inp.setAttribute("aria-label", `${label} in centimetres, empty means the usual ${num(typ)} cm`);
    if (force || document.activeElement !== inp) inp.value = getCm() > 0 ? num(getCm()) : "";
  } };
  api.set();
  return api;
}

function openRenameSheet() {
  if (!S.zone) return;
  sheet.open("rename", "Name this room", (body) => {
    const tf = textField("Room name", S.zone.name, (v) => { send({ t: "set_room", patch: { name: v } }); S.zone.name = v; renderRoomHeader(); }, { closeOnEnter: true });
    body.append(section(null, tf.el,
      h("p.note", null, "For example \"Upstairs bedroom\". It shows on the PC next to this phone."),
      h("button.btn.btn--accent.btn--block", { type: "button", on: { click: () => { tf.input.blur(); sheet.close(); } } }, "Done")));
    setTimeout(() => { tf.input.focus(); tf.input.select(); }, 300);
  });
  sheetRefresh = null;
}

function openRoomSheet() {
  if (!S.zone) return;
  const M = S.materials || { roof: [], wall: [], limits: {} };
  const lim = M.limits || {};
  const [smin, smax] = lim.size || [1.5, 30], [hmin, hmax] = lim.height || [2, 6], [tmin, tmax] = lim.terrain_size || [12, 120];
  const patch = (p) => send({ t: "set_room", patch: p });
  sheet.open("room", "Room", (body) => {
    const r = () => S.zone.room;
    const name = textField("Name", S.zone.name, (v) => { patch({ name: v }); S.zone.name = v; renderRoomHeader(); });
    const w = stepper({ label: "Width (west to east)", unit: "m", unitLong: "metres", min: smin, max: smax, step: 0.1, decimals: 2, value: r().width, onCommit: (v) => patch({ width: v }) });
    const d = stepper({ label: "Depth (south to north)", unit: "m", unitLong: "metres", min: smin, max: smax, step: 0.1, decimals: 2, value: r().depth, onCommit: (v) => patch({ depth: v }) });
    const ht = stepper({ label: "Ceiling height", unit: "m", unitLong: "metres", min: hmin, max: hmax, step: 0.1, decimals: 2, value: r().height, onCommit: (v) => patch({ height: v }) });
    const yd = stepper({ label: "Yard size", unit: "m", unitLong: "metres", min: tmin, max: tmax, step: 1, decimals: 0, value: r().terrain_size, onCommit: (v) => patch({ terrain_size: v }) });
    const mats = (list) => list.map((m) => ({ value: m, label: matLabel(m) }));
    const roof = selectField("Material", mats(M.roof || []), r().roof_material, (v) => patch({ roof_material: v }));
    const roofT = thicknessField("Thickness", () => S.thick.roof_cm, () => S.thick.typical_roof_cm, (txt) => patch({ roof_thickness: txt }));
    const wall = selectField("Material", mats(M.wall || []), r().wall_material, (v) => patch({ wall_material: v }));
    const wallT = thicknessField("Thickness", () => S.thick.wall_cm, () => S.thick.typical_wall_cm, (txt) => patch({ wall_thickness: txt }));
    body.append(
      section(null, name.el),
      section("Size", h("div.grid2", null, w.el, d.el, ht.el, yd.el),
        h("p.note", null, "Windows and your listening spot stay inside when the room shrinks.")),
      section("Roof", roof.el, roofT.el),
      section("Walls", wall.el, wallT.el),
      section("Saved rooms",
        h("p.note", null, "Used this phone in another room before, or opened RainRoom from a different link? Pick that room again."),
        h("button.btn.btn--block", { type: "button", on: { click: () => openRoomsSheet() } }, icon("house"), h("span", null, "Switch to another room"))));
    sheetRefresh = () => {
      if (sheet.kind !== "room") return;
      name.set(S.zone.name); w.set(r().width); d.set(r().depth); ht.set(r().height); yd.set(r().terrain_size);
      roof.set(r().roof_material); wall.set(r().wall_material);
      roof.hint.textContent = (M.roof_hints || {})[r().roof_material] || "";
      wall.hint.textContent = (M.wall_hints || {})[r().wall_material] || "";
      roofT.set(); wallT.set();
    };
    sheetRefresh();
  }, { onClose: () => { sheetRefresh = null; } });
}

const SWING = new Set(["casement", "awning", "hopper", "pivot", "tilt_turn"]);
const HINGED = new Set(["casement", "tilt_turn"]);
function openWindowSheet(i) {
  const W = () => S.zone && S.zone.windows[i];
  if (!W()) return;
  const M = S.materials || {};
  const lim = M.limits || {};
  const patch = (p, final = true) => { Object.assign(W(), p); sendWindow(i, p, final); plan.draw(); renderWindowList(); };
  sheet.open(`win:${i}`, W().name || "Window", (body) => {
    const name = textField("Name", W().name, (v) => { patch({ name: v }); sheet.setTitle(v); });
    const open = slider({ label: "Open", lo: 0, hi: 100, step: 1, fill: "window", value: W().open * 100, left: "Shut", right: "Wide open",
      format: (v) => v <= 0.5 ? "Shut" : `${Math.round(v)}%`,
      onInput: (v, final) => patch({ open: Math.round(v) / 100 }, final) });
    open.el.classList.add("slider-row--big");
    const wallSeg = segmented({ label: "Wall", value: W().wall,
      options: ["north", "east", "south", "west"].map((x) => ({ value: x, label: wallName(x) })),
      onChange: (v) => patch({ wall: v }) });
    const len = () => (W().wall === "north" || W().wall === "south") ? S.zone.room.width : S.zone.room.depth;
    const ceil = () => S.zone.room.height;
    const width = stepper({ label: "Width", unit: "m", unitLong: "metres", min: lim.window_width_min || 0.3, max: 30, step: 0.1, decimals: 2, value: W().width, onCommit: (v) => patch({ width: Math.min(v, len() - 0.1) }) });
    const height = stepper({ label: "Height", unit: "m", unitLong: "metres", min: 0.3, max: 4, step: 0.1, decimals: 2, value: W().height, onCommit: (v) => patch({ height: v }) });
    const sill = stepper({ label: "Sill (from floor)", unit: "m", unitLong: "metres", min: 0, max: lim.sill_max || 3.5, step: 0.1, decimals: 2, value: W().sill, onCommit: (v) => patch({ sill: v }) });
    const offset = stepper({ label: "From the corner", unit: "m", unitLong: "metres", min: 0, max: 30, step: 0.1, decimals: 2, value: W().offset, onCommit: (v) => patch({ offset: v }) });
    const style = selectField("How it opens", (M.window_styles || []).map((s) => ({ value: s.key, label: s.label })), W().open_style, (v) => { patch({ open_style: v }); sheetRefresh(); });
    const hinge = segmented({ label: "Hinge (looking out)", value: W().hinge_side || "left",
      options: [{ value: "left", label: "Left" }, { value: "right", label: "Right" }], onChange: (v) => patch({ hinge_side: v }) });
    const [amin, amax] = lim.max_angle_deg || [10, 90];
    const angle = slider({ label: "Opens up to", lo: amin, hi: amax, step: 1, fill: "window", value: W().max_angle_deg, format: (v) => `${Math.round(v)}°`,
      onInput: (v, final) => patch({ max_angle_deg: v }, final) });
    let armed = 0;
    const del = h("button.btn.btn--danger.btn--block", { type: "button" }, icon("trash"), h("span", null, "Remove window"));
    del.addEventListener("click", () => {
      if (!armed) { armed = setTimeout(() => { armed = 0; del.lastChild.textContent = "Remove window"; }, 3000); del.lastChild.textContent = "Tap again to remove"; return; }
      clearTimeout(armed); armed = 0;
      send({ t: "remove_window", index: i });
      S.sel = -1; plan.select(-1);
      sheet.close();
    });
    const offHint = h("p.note");
    body.append(
      section(null, open.el),
      section(null, name.el, wallSeg.el),
      section("Size and place", h("div.grid2", null, width.el, height.el, sill.el, offset.el), offHint),
      section("Opening", style.el, hinge.el, angle.el),
      section(null, del));
    sheetRefresh = () => {
      if (sheet.kind !== `win:${i}` || !W()) return;
      const w = W();
      name.set(w.name); open.set(w.open * 100); wallSeg.set(w.wall);
      width.set(w.width); height.set(w.height); sill.set(w.sill); offset.set(w.offset);
      style.set(w.open_style); hinge.set(w.hinge_side || "left"); angle.set(w.max_angle_deg);
      hinge.el.hidden = !HINGED.has(w.open_style);
      angle.el.hidden = !SWING.has(w.open_style);
      offHint.textContent = (w.wall === "north" || w.wall === "south") ? "Measured from the west corner. You can also drag it on the plan." : "Measured from the south corner. You can also drag it on the plan.";
      void len; void ceil;
    };
    sheetRefresh();
  }, { onClose: () => { sheetRefresh = null; } });
}

function openYouSheet() {
  if (!S.zone) return;
  const L = () => S.zone.listener;
  sheet.open("you", "Listening spot", (body) => {
    const val = h("span.dial-side__value");
    const sub = h("span.dial-side__sub", null, "Where your face points. The rain on that side sounds in front of you.");
    const dd = dial({ label: "Facing", value: deg(L().yaw), snap: 5, valueText: (v) => `Facing ${compass(v)}, ${Math.round(v)} degrees`,
      onInput: (v, final) => { L().yaw = v * Math.PI / 180; val.textContent = `${compass(v)} · ${Math.round(v)}°`; plan.draw(); renderFacing(); sendListenerPatch({ yaw: v }, final); } });
    const r = S.zone.room;
    const x = stepper({ label: "From the west wall", unit: "m", unitLong: "metres", min: 0.15, max: r.width - 0.15, step: 0.1, decimals: 2, value: L().x,
      onCommit: (v) => { L().x = v; plan.draw(); sendListenerPatch({ x: v }, true); } });
    const z = stepper({ label: "From the south wall", unit: "m", unitLong: "metres", min: 0.15, max: r.depth - 0.15, step: 0.1, decimals: 2, value: L().z,
      onCommit: (v) => { L().z = v; plan.draw(); sendListenerPatch({ z: v }, true); } });
    const y = stepper({ label: "Ear height", unit: "m", unitLong: "metres", min: 0.4, max: Math.max(0.5, r.height - 0.1), step: 0.1, decimals: 2, value: L().y,
      onCommit: (v) => { L().y = v; sendListenerPatch({ y: v }, true); } });
    const quick = h("div.seg.seg--compass", { role: "group", "aria-label": "Face a direction" },
      ...[["N", 0], ["E", 90], ["S", 180], ["W", 270]].map(([n, a]) => h("button.seg__btn", { type: "button", "aria-label": `Face ${wallName({ N: "north", E: "east", S: "south", W: "west" }[n])}`,
        on: { click: () => { dd.set(a, { animate: true }); L().yaw = a * Math.PI / 180; val.textContent = `${compass(a)} · ${a}°`; plan.draw(); renderFacing(); sendListenerPatch({ yaw: a }, true); } } }, n)));
    body.append(
      section("Facing", h("div.dial-row", null, dd.el, h("div.dial-side", null, val, sub)), quick),
      section("Position", h("div.grid2", null, x.el, z.el, y.el),
        h("p.note", null, "Tip: drag You on the plan to move, drag the small circle in front of You to turn, or twist two fingers.")));
    sheetRefresh = () => {
      if (sheet.kind !== "you") return;
      const d = deg(L().yaw);
      dd.set(d); val.textContent = `${compass(d)} · ${Math.round(d)}°`;
      [...quick.children].forEach((b, k) => b.setAttribute("aria-pressed", String(Math.abs(d - k * 90) < 0.5)));
      x.set(L().x); z.set(L().z); y.set(L().y);
    };
    sheetRefresh();
  }, { onClose: () => { sheetRefresh = null; } });
}

// ---------------------------------------------------------------- tabs
function showTab(t, focus = false) {
  S.tab = t;
  document.getElementById("app").dataset.tab = t;
  for (const b of document.querySelectorAll(".tab")) {
    const on = b.dataset.tab === t;
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;
    if (on && focus) b.focus();
  }
  for (const p of document.querySelectorAll(".panel")) p.hidden = p.id !== `panel-${t}`;
  if (t === "room") {
    plan.resize();
    if (!showTab._hinted && S.gotState) { showTab._hinted = true; $("planHint").textContent = "Drag to move · tap a window to edit"; setTimeout(() => { if (!plan.drag && !plan.placing) $("planHint").textContent = ""; }, 5000); }
  } else setPlacing(false);
  store.set("tab", t);
}
document.querySelector(".tabbar").addEventListener("click", (e) => { const b = e.target.closest(".tab"); if (b) showTab(b.dataset.tab); });
document.querySelector(".tabbar").addEventListener("keydown", (e) => {
  const order = ["listen", "room", "weather"];
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  const i = order.indexOf(S.tab);
  showTab(order[(i + (e.key === "ArrowRight" ? 1 : 2)) % 3], true);
  e.preventDefault();
});
$("weatherPill").addEventListener("click", () => showTab("weather"));
document.addEventListener("click", (e) => { const g = e.target.closest("[data-goto]"); if (g) showTab(g.dataset.goto); });

// ---------------------------------------------------------------- gate
function showGate(kind) {
  const gate = $("gate"), btn = $("gateBtn");
  const T = {
    nokey: ["Open the link from the PC",
      "This page needs the link from RainRoom on your computer.\nOn the PC, open the phone panel and scan the code with this phone's camera, or type the address it shows.", null],
    bad_key: ["This link no longer works",
      "Someone made a new link on the computer, so this one stopped working.\nOn the PC, open RainRoom, choose Phone, and scan the code again.", null],
    busy: ["The computer is busy",
      (S.fatalMsg || "Too many phones are using RainRoom right now.") + "\nTry again in a little while.", "Try again"],
    bad_client_id: ["Something is off with this browser",
      "Reload the page. If it keeps happening, clear this site's data and scan the code again.", "Reload"],
    replaced: ["Open somewhere else",
      "This room is open in another tab or browser right now. Only one place can use it at a time.", "Use it here"],
  }[kind] || ["Can't connect", "", "Try again"];
  $("gateTitle").textContent = T[0];
  $("gateText").textContent = T[1];
  setIcon($("gateIcon").firstElementChild, kind === "replaced" ? "phone" : kind === "nokey" ? "link" : "lock");
  btn.hidden = !T[2];
  btn.textContent = T[2] || "";
  btn.onclick = () => {
    if (kind === "replaced" || kind === "busy") { S.fatal = null; gate.hidden = true; retry = 0; connect(); }
    else location.reload();
  };
  gate.hidden = false;
  $("offlineBanner").hidden = true;
}

// ---------------------------------------------------------------- saved rooms
// A phone's room is keyed by an id kept in this browser. Another address for
// the PC (or clearing site data) means a new id, so let the user pick a room
// they already set up instead of starting over.
let roomsRefresh = null;
function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - Number(t || 0));
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400 * 2) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}
function useRoom(id) {
  if (!ID_RE.test(id) || id === CLIENT_ID) return;
  CLIENT_ID = id;
  store.set("client_id", id);
  store.set("welcomed." + id, true);
  S.target = store.get("target." + id, "phone") === "pc" ? "pc" : "phone";
  S.pcDevice = store.get("pcDevice." + id, null);
  S.wantPhone = false;
  if (player.state !== "idle") player.stop();
  S.sel = -1; S.pendingPlace = null;
  sheet.close();
  if (params.get("cid")) { params.delete("cid"); history.replaceState(null, "", "?" + params.toString()); }
  toast("Switching rooms…");
  retry = 0;
  if (ws) { const old = ws; ws = null; try { old.close(); } catch (e) {} }
  connect();
}
function openRoomsSheet(welcome = false) {
  sheet.open("rooms", welcome ? "Welcome back?" : "Your rooms", (body) => {
    const list = h("div.list", { role: "list", "aria-label": "Saved rooms" });
    const intro = welcome
      ? h("p.note", null, "This computer already has rooms set up from phones. If one of them is this room, pick it to keep its size, windows and listening spot.")
      : h("p.note", null, "Rooms set up from phones on this computer. Pick one to use it on this phone.");
    const fresh = h("button.btn.btn--block", { type: "button", on: { click: () => { store.set("welcomed." + CLIENT_ID, true); sheet.close(); } } },
      welcome ? "Start a new room here" : "Keep this room");
    body.append(section(null, intro, list, fresh));
    roomsRefresh = () => {
      if (sheet.kind !== "rooms") { roomsRefresh = null; return; }
      const rows = (S.rooms || []).filter((r) => r.id !== CLIENT_ID);
      if (!rows.length) {
        list.replaceChildren(h("div.row", null, h("span.row__text", null, h("span.row__name", null, "No other rooms yet"),
          h("span.row__sub", null, "Rooms appear here once a phone has changed something in its room."))));
        return;
      }
      list.replaceChildren(...rows.map((r) => {
        let armed = 0;
        const sub = h("span.row__sub" + (r.in_use ? ".row__sub--live" : ""), null,
          `${num(r.width)} × ${num(r.depth)} m · ${r.windows} window${r.windows === 1 ? "" : "s"} · ` +
          (r.in_use ? "open on another phone now" : `used ${ago(r.updated)}`));
        const pick = h("button.row", { type: "button", role: "listitem", "aria-label": `Use ${r.name}` },
          h("span.ico.row__icon", { "aria-hidden": "true", dataset: { icon: "house" } }),
          h("span.row__text", null, h("span.row__name", null, r.name), sub),
          h("span.ico.ico--sm", { "aria-hidden": "true", dataset: { icon: "chevron_right" } }));
        pick.addEventListener("click", () => {
          if (!needLive()) return;
          if (r.in_use && !armed) {
            armed = setTimeout(() => { armed = 0; sub.textContent = "open on another phone now"; }, 3500);
            sub.textContent = "Tap again to move it to this phone";
            return;
          }
          clearTimeout(armed);
          useRoom(r.id);
        });
        const kids = [pick];
        if (!r.in_use) {
          let armedDel = 0;
          const del = h("button.icon-btn.room-pick__forget", { type: "button", "aria-label": `Remove ${r.name}` }, icon("trash"));
          del.addEventListener("click", () => {
            if (!needLive()) return;
            if (!armedDel) {
              armedDel = setTimeout(() => { armedDel = 0; sub.textContent = `used ${ago(r.updated)}`; }, 3000);
              sub.textContent = "Tap the bin again to remove this room";
              return;
            }
            clearTimeout(armedDel);
            send({ t: "forget_room", room_id: r.id });
          });
          kids.push(del);
        }
        return h("div.room-pick", null, ...kids);
      }));
      hydrateIcons(list);
    };
    roomsRefresh();
    send({ t: "list_rooms" });
  }, { onClose: () => { roomsRefresh = null; } });
  sheetRefresh = null;
}
function maybeWelcome() {
  if (!S.zoneNew || store.get("welcomed." + CLIENT_ID, false)) return;
  const others = (S.rooms || []).filter((r) => r.id !== CLIENT_ID && !r.in_use);
  if (!others.length) { store.set("welcomed." + CLIENT_ID, true); return; }
  openRoomsSheet(true);
}

// ---------------------------------------------------------------- start
hydrateIcons(document);
renderListen();
renderPill(false);
showTab(["listen", "room", "weather"].includes(store.get("tab", "listen")) ? store.get("tab", "listen") : "listen");
if (!KEY) { S.fatal = "nokey"; showGate("nokey"); }
else connect();

// debug / test hook
window.__rr = {
  get stats() { return { ...player.stats, bufferedMs: Math.round(player.bufferedSeconds() * 1000), wsOpen: S.conn === "open" }; },
  S, player, plan, send, showTab, showGate, openWindowSheet, openRoomSheet, openYouSheet, openRenameSheet, sheet,
  openRoomsSheet, useRoom,
  get clientId() { return CLIENT_ID; },
  dropSocket: () => { if (ws) ws.close(); },
};
