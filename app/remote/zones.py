"""Phone rooms ("zones") and the shared weather — no Qt, no sockets.

* ``ZoneHub`` holds the ONE shared weather (every value in
  ``settings_schema.SETTINGS``: rain, wind incl. direction, sound mix, drop
  character) as a thread-safe snapshot, and the zones. The desktop Room is the
  source of truth: the desktop pushes its values with ``set_shared``; a phone
  edit updates the snapshot, is broadcast to every phone and handed to the
  desktop through ``on_weather_from_phone`` callbacks.
* ``Zone`` = one phone / browser (``client_id``). It owns its own Room (size,
  materials, windows, listening spot, volume), saved to
  ``<zones_dir>/<client_id>.json``, and its own SpatialRainEngine. Output is
  "off", "phone" (blocks streamed to the browser) or "pc" (the engine plays
  binaural "You" on one of this PC's devices).

Events for the server / desktop go through ``hub.add_listener(cb)``; ``cb(event,
data)`` is called from whatever thread made the change (see EVENTS).
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import math
import os
import re
import struct
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from app.models import presets
from app.models import settings_schema as schema
from app.models.materials import ROOF_MATERIALS, WALL_MATERIALS, role_material
from app.models.room import (
    WALLS, WINDOW_OPEN_STYLES, WINDOW_STYLE_LABELS, Listener, Room, Window,
)
from app.utils.persistence import load_room, room_to_dict, save_room

log = logging.getLogger("remote")

# Every shared sound value = WEATHER_KEYS + MIX_KEYS + tone_* + wind_direction_deg,
# which is exactly the settings registry (master_volume stays per zone).
SHARED_KEYS: Tuple[str, ...] = tuple(s.key for s in schema.SETTINGS)
PHONE_GROUPS = ("rain", "wind", "thunder")          # what weather_schema describes

SAMPLE_RATE = 48000
BLOCK_FRAMES = 2048
AUDIO_MAGIC = 0x52524131                  # b"1ARR" on the wire = 'RRA1' as uint32 LE
HEADER = struct.Struct("<III")            # magic, seq, start_frame

MATERIALS = ROOF_MATERIALS + WALL_MATERIALS
_ROLE_OPTIONS = {"roof_material": ROOF_MATERIALS, "wall_material": WALL_MATERIALS}
HINGE_SIDES = ("left", "right")
MAX_WINDOWS = 8
NAME_MAX = 40
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

# Limits (meters unless named)
SIZE_MIN, SIZE_MAX = 1.5, 30.0
CEIL_MIN, CEIL_MAX = 2.0, 6.0
TERRAIN_MIN, TERRAIN_MAX = 12.0, 120.0
THICK_MAX_CM = 150.0                      # same rule as the desktop House page
WIN_W_MIN, WIN_H_MIN, WIN_H_MAX, SILL_MAX = 0.3, 0.3, 4.0, 3.5
ANGLE_MIN, ANGLE_MAX = 10.0, 90.0

EVENTS = ("weather", "zones", "output")
_API_SUFFIX = re.compile(r"\s*Windows\s+(WASAPI|DirectSound|MME|WDM-KS)\s*$", re.I)

# Caps (a phone with the key can't make the PC do unbounded work)
MAX_ACTIVE_ZONES = 4          # rooms playing at once (phone stream or PC output)
MAX_LOADED_ZONES = 32         # rooms kept in memory; idle ones are dropped first
MAX_ZONE_FILES = 64           # saved phone rooms; the oldest unused ones are pruned
BACKLOG_PAUSE_BYTES = 256 * 1024   # socket send buffer above this: audio waits
PACE_SLACK = 1.03             # never render more than real time (+3%) + the lead


def clean_device_name(name: str) -> str:
    """Same cleaning as the desktop (ui.studio.context), without Qt."""
    return _API_SUFFIX.sub("", str(name or "?")).strip()


def sanitize_client_id(cid) -> Optional[str]:
    """A safe room id, lower-cased: Windows file names ignore case, so "PhoneA"
    and "phonea" must be the same room (and the same file)."""
    s = str(cid or "").strip()
    return s.lower() if ID_RE.match(s) else None


def default_zones_dir() -> Path:
    env = os.environ.get("RAINROOM_ZONES_DIR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "configs" / "zones"
    return Path(__file__).resolve().parents[2] / "configs" / "zones"


def _num(v, lo: float, hi: float) -> Optional[float]:
    """Finite float clamped to lo..hi, or None for junk (bools/strings)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    if not math.isfinite(v):
        return None
    return max(lo, min(hi, v))


def _text(v, limit: int = NAME_MAX) -> Optional[str]:
    if not isinstance(v, str):
        return None
    s = " ".join(v.split())[:limit]
    return s or None


def _wall_len(room: Room, wall: str) -> float:
    return float(room.width) if wall in ("north", "south") else float(room.depth)


def default_zone_room(roof: str = "Metal Roof", wall: str = "Brick Wall") -> Room:
    """A new phone's room: a small bedroom, one north window half open."""
    room = Room(width=3.5, height=2.5, depth=3.2, terrain_size=36.0,
                roof_material=roof, wall_material=wall, name="Bedroom")
    room.windows = [Window(name="Window", wall="north", offset=1.25, width=1.0, height=1.2,
                           sill=0.9, open=0.5, open_style="casement", max_angle_deg=70.0)]
    room.speakers = []
    room.headphones_items = []
    room.listener = Listener(x=1.75, y=1.2, z=1.6)
    room.sync_all_windows()
    return room


def fit_window(room: Room, w: Window) -> None:
    """Keep a wall window inside its wall and under the ceiling."""
    w.free_place = False
    wall = (w.wall or "north").lower()
    w.wall = wall if wall in WALLS else "north"
    length = _wall_len(room, w.wall)
    w.width = max(WIN_W_MIN, min(float(w.width), max(WIN_W_MIN, length - 0.1)))
    w.offset = max(0.0, min(float(w.offset), max(0.0, length - w.width)))
    top = float(room.height) - 0.05
    w.height = max(WIN_H_MIN, min(float(w.height), WIN_H_MAX, max(WIN_H_MIN, top - 0.05)))
    w.sill = max(0.0, min(float(w.sill), SILL_MAX, max(0.0, top - w.height)))
    w.open = max(0.0, min(1.0, float(w.open)))
    if w.open_style_norm() != w.open_style:
        w.open_style = w.open_style_norm()
    if (w.hinge_side or "left").lower() not in HINGE_SIDES:
        w.hinge_side = "left"
    w.max_angle_deg = max(ANGLE_MIN, min(ANGLE_MAX, float(w.max_angle_deg)))
    room.sync_window_coords(w)


def fit_room(room: Room) -> None:
    """Clamp everything (like the desktop clamp_contents + limits above)."""
    room.width = max(SIZE_MIN, min(SIZE_MAX, float(room.width)))
    room.depth = max(SIZE_MIN, min(SIZE_MAX, float(room.depth)))
    room.height = max(CEIL_MIN, min(CEIL_MAX, float(room.height)))
    room.terrain_size = max(TERRAIN_MIN, min(TERRAIN_MAX, float(room.terrain_size)))
    room.roof_material = role_material(room.roof_material, "roof")
    room.wall_material = role_material(room.wall_material, "wall")
    for attr in ("roof_thickness", "wall_thickness"):
        t = float(getattr(room, attr, 0.0) or 0.0)
        setattr(room, attr, max(0.0, min(THICK_MAX_CM / 100.0, t)) if math.isfinite(t) else 0.0)
    room.speakers = []                      # phone rooms are listener-only
    room.headphones_items = []
    del room.windows[MAX_WINDOWS:]
    for w in room.windows:
        fit_window(room, w)
    L = room.listener
    L.x, L.z = room.clamp_inside(float(L.x), float(L.z))
    L.y = max(0.4, min(max(0.5, float(room.height) - 0.1), float(L.y)))
    L.yaw = float(L.yaw) % (2.0 * math.pi) if math.isfinite(float(L.yaw)) else 0.0
    room.master_volume = max(0.0, min(1.0, float(room.master_volume)))


def thickness_info(room: Room) -> Dict[str, float]:
    """Thicknesses in cm for the phone UI (0 = the material's usual build)."""
    try:
        from app.audio import house_model as hm
        typ_r, typ_w = hm.typical_roof_thickness(room), hm.typical_wall_thickness(room)
    except Exception:
        typ_r = typ_w = 0.0
    return {
        "roof_cm": round(float(room.roof_thickness or 0.0) * 100.0, 1),
        "wall_cm": round(float(room.wall_thickness or 0.0) * 100.0, 1),
        "typical_roof_cm": round(typ_r * 100.0, 1),
        "typical_wall_cm": round(typ_w * 100.0, 1),
    }


class ZoneError(ValueError):
    """A rejected request; the message is plain words for the phone.
    ``code`` goes into error{code} ("rejected" unless said otherwise)."""

    def __init__(self, message: str, code: str = "rejected"):
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------------------
# Phone streaming (one thread per streaming zone)
# ----------------------------------------------------------------------------

class _Stream:
    """Renders blocks for one browser, paced by its acks.

    Keeps (sent - played) at most ``max_lead_ms``; renders again once the
    client's acks bring the lead under that. A client that stops acking just
    stops getting audio (the thread waits). ``sink(obj)`` takes bytes (one
    binary WebSocket message) or a dict (one JSON text message), thread-safe,
    in order.
    """

    def __init__(self, zone: "Zone", sink: Callable[[Any], None], stream_id: int,
                 max_lead_ms: float, start_lead_ms: float,
                 backlog: Optional[Callable[[], int]] = None):
        self.zone = zone
        self.sink = sink
        self.backlog = backlog                # bytes still queued on the socket
        self.id = int(stream_id)
        self.block = BLOCK_FRAMES
        self.max_lead = int(SAMPLE_RATE * max_lead_ms / 1000.0)
        self.start_lead_ms = float(start_lead_ms)
        self.sent = 0
        self.played = 0
        self.seq = 0
        self.last_ack = time.monotonic()
        self.t_first: Optional[float] = None  # wall clock of the first block
        self.waits_backlog = 0                # times audio waited for the socket
        self.waits_pace = 0                   # times audio waited for the wall clock
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.render_ms: List[float] = []      # recent per-block render times (engine)
        self.cpu_ms: List[float] = []         # same, render thread CPU time
        self.block_ms: List[float] = []       # whole block incl. hand-over to the worker
        self.thread = threading.Thread(target=self._run, name=f"zone-stream-{zone.client_id}",
                                       daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self, join: bool = True) -> None:
        self._stop.set()
        self._wake.set()
        if join and self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=3.0)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def ack(self, played) -> None:
        p = _num(played, 0.0, float(self.sent))
        if p is None:
            return
        p = int(p)
        if p > self.played:
            self.played = p
        self.last_ack = time.monotonic()
        self._wake.set()

    def lead_frames(self) -> int:
        return self.sent - self.played

    def _run(self) -> None:
        z = self.zone
        try:
            eng = z.ensure_engine()
            with z.lock:
                z.sync_shared(force=True)
            eng.prepare_for_stream()
            if self._stop.is_set():
                return
            self.sink({"t": "stream", "state": "start", "stream": self.id,
                       "sample_rate": SAMPLE_RATE, "channels": 2, "frames": self.block,
                       "format": "s16le", "header_bytes": HEADER.size,
                       "start_lead_ms": self.start_lead_ms,
                       "max_lead_ms": round(self.max_lead * 1000.0 / SAMPLE_RATE)})
            z._stream_started(self)
            pcm16 = getattr(eng, "render_pcm16", None)
            while not self._stop.is_set():
                if self.sent - self.played + self.block > self.max_lead:
                    self._wake.wait(0.05)
                    self._wake.clear()
                    continue
                # The socket hasn't sent what we gave it: wait (a slow or
                # stuck phone must not pile audio up in memory)
                if self.backlog is not None and self.backlog() > BACKLOG_PAUSE_BYTES:
                    self.waits_backlog += 1
                    self._wake.wait(0.05)
                    self._wake.clear()
                    continue
                # Never faster than real time plus the lead, whatever the acks say
                if self.t_first is not None:
                    allowed = self.max_lead + (time.monotonic() - self.t_first) * SAMPLE_RATE * PACE_SLACK
                    if self.sent + self.block > allowed:
                        self.waits_pace += 1
                        self._wake.wait(0.02)
                        self._wake.clear()
                        continue
                t0, c0 = time.perf_counter(), time.thread_time()
                with z.lock:
                    z.sync_shared()
                    try:
                        if pcm16 is not None:
                            pcm = pcm16(self.block)
                        else:
                            blk = eng.render_listener_block(self.block)
                            pcm = np.clip(np.asarray(blk, dtype=np.float32), -1.0, 1.0)
                            pcm = (pcm * 32767.0).astype("<i2").tobytes()
                    except Exception as e:
                        if e.__class__.__name__ == "WorkerError":
                            raise
                        log.exception("zone %s render failed", z.client_id)
                        pcm = bytes(self.block * 4)
                wall = (time.perf_counter() - t0) * 1000.0
                self.block_ms = (self.block_ms + [wall])[-400:]
                self.render_ms = (self.render_ms + [float(getattr(eng, "last_render_ms", 0.0) or wall)])[-400:]
                self.cpu_ms = (self.cpu_ms + [float(getattr(eng, "last_cpu_ms", 0.0)
                                                    or (time.thread_time() - c0) * 1000.0)])[-400:]
                if self.t_first is None:
                    self.t_first = time.monotonic()
                self.sink(HEADER.pack(AUDIO_MAGIC, self.seq & 0xFFFFFFFF, self.sent & 0xFFFFFFFF) + pcm)
                self.seq += 1
                self.sent += self.block
        except Exception as e:
            log.exception("zone %s stream crashed", z.client_id)
            z._stream_failed(self, str(e) or e.__class__.__name__)


# ----------------------------------------------------------------------------
# Zone
# ----------------------------------------------------------------------------

class Zone:
    """One phone's room, its engine and its output."""

    def __init__(self, hub: "ZoneHub", client_id: str, room: Room, path: Optional[Path]):
        self.hub = hub
        self.client_id = client_id
        self.room = room
        self.path = path
        self.lock = threading.RLock()       # room edits vs. a render block
        self.engine = None
        self.device_name = ""
        self.connected = False
        self.last_seen = time.time()
        self.mode = "off"                   # off | phone | pc
        self.device: Optional[int] = None   # pc device index (None = system default)
        self.playing = False
        self.starting = False
        self.error: Optional[str] = None
        self._stream: Optional[_Stream] = None
        self._sink: Optional[Callable[[Any], None]] = None
        self._backlog: Optional[Callable[[], int]] = None
        self.fresh = False                  # new room, never edited (no file yet)
        self._stream_id = 0
        self._shared_ver = -1
        self._dirty_at: Optional[float] = None
        self._worker = concurrent.futures.ThreadPoolExecutor(max_workers=1,
                                                             thread_name_prefix=f"zone-{client_id}")

    # ----- shared weather -----
    def sync_shared(self, force: bool = False) -> None:
        """Copy the hub's shared values into this room (cheap when unchanged)."""
        ver, snap = self.hub._shared_for_zone()
        if not force and ver == self._shared_ver:
            return
        for k, v in snap.items():
            setattr(self.room, k, v)
        self._shared_ver = ver

    # ----- engine -----
    def ensure_engine(self):
        if self.engine is None:
            self.engine = self.hub._engine_factory(self.room)
        self.engine.room = self.room
        return self.engine

    # ----- room as JSON -----
    def room_json(self) -> Dict[str, Any]:
        with self.lock:
            self.sync_shared()
            d = room_to_dict(self.room)
        return json.loads(json.dumps(d))    # plain JSON types only (a deep copy)

    def thickness(self) -> Dict[str, float]:
        with self.lock:
            return thickness_info(self.room)

    def output_info(self) -> Dict[str, Any]:
        d = {"mode": self.mode, "device": self.device, "playing": bool(self.playing),
             "starting": bool(self.starting)}
        if self.error:
            d["error"] = self.error
        return d

    def summary(self) -> Dict[str, Any]:
        return {"client_id": self.client_id, "name": self.room.name, "device_name": self.device_name,
                "connected": self.connected, "output": self.output_info(),
                "last_seen": self.last_seen}

    # ----- edits (validated; raise ZoneError) -----
    def _edited(self) -> None:
        fit_room(self.room)
        self.fresh = False
        self._dirty_at = time.monotonic()
        self.hub._wake_saver()
        self.push_to_engine()

    def push_to_engine(self) -> None:
        """A PC output plays on the engine's own mixer (in a worker process):
        hand it the room now. A phone stream picks edits up every block."""
        eng = self.engine
        if self.mode == "pc" and eng is not None and hasattr(eng, "sync_room"):
            with self.lock:
                eng.sync_room()

    def set_room(self, patch: Dict[str, Any]) -> None:
        if not isinstance(patch, dict):
            raise ZoneError("Room changes must be a list of values")
        r = self.room
        with self.lock:
            for key, lo, hi in (("width", SIZE_MIN, SIZE_MAX), ("depth", SIZE_MIN, SIZE_MAX),
                                ("height", CEIL_MIN, CEIL_MAX),
                                ("terrain_size", TERRAIN_MIN, TERRAIN_MAX)):
                if key in patch:
                    v = _num(patch[key], lo, hi)
                    if v is not None:
                        setattr(r, key, v)
            if "name" in patch:
                n = _text(patch["name"])
                if n:
                    r.name = n
            for key in ("roof_material", "wall_material"):
                if key in patch and patch[key] in _ROLE_OPTIONS[key]:
                    setattr(r, key, patch[key])
            for key in ("roof_thickness", "wall_thickness"):
                if key not in patch:
                    continue
                v = patch[key]
                if v is None or (isinstance(v, str) and not v.strip()):
                    cm = 0.0
                elif isinstance(v, str):
                    try:
                        cm = float(v.replace(",", "."))
                    except ValueError:
                        continue
                elif isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                else:
                    cm = float(v)
                if not math.isfinite(cm) or cm > THICK_MAX_CM:
                    continue                # like the desktop: keep the last good value
                setattr(r, key, max(0.0, cm) / 100.0)
            self._edited()

    def add_window(self, wall=None) -> int:
        r = self.room
        with self.lock:
            if len(r.windows) >= MAX_WINDOWS:
                raise ZoneError(f"A room can have up to {MAX_WINDOWS} windows")
            walls = [wall] if wall in WALLS else list(WALLS)
            best = None
            for wl in walls:
                length = _wall_len(r, wl)
                taken = sorted((w.offset, w.offset + w.width) for w in r.windows
                               if (w.wall or "north").lower() == wl)
                edges = [0.0] + [v for seg in taken for v in seg] + [length]
                for a, b in zip(edges[0::2], edges[1::2]):
                    if best is None or (b - a) > best[0]:
                        best = (b - a, wl, a, b)
            _gap, wl, a, b = best
            width = max(0.4, min(1.2, (b - a) - 0.4))
            offset = max(0.0, a + ((b - a) - width) * 0.5)
            n = len(r.windows)
            r.windows.append(Window(name=f"Window {n + 1}", wall=wl, offset=offset, width=width,
                                    height=1.2, sill=0.9, open=0.5, open_style="casement",
                                    max_angle_deg=70.0))
            self._edited()
            return n

    def set_window(self, index, patch: Dict[str, Any]) -> None:
        r = self.room
        if not isinstance(patch, dict):
            raise ZoneError("Window changes must be a list of values")
        with self.lock:
            if isinstance(index, bool) or not isinstance(index, int) or not (0 <= index < len(r.windows)):
                raise ZoneError("That window doesn't exist any more")
            w = r.windows[index]
            if patch.get("wall") in WALLS:
                w.wall = patch["wall"]
            for key, lo, hi in (("offset", 0.0, SIZE_MAX), ("width", WIN_W_MIN, SIZE_MAX),
                                ("height", WIN_H_MIN, WIN_H_MAX), ("sill", 0.0, SILL_MAX),
                                ("open", 0.0, 1.0), ("max_angle_deg", ANGLE_MIN, ANGLE_MAX)):
                if key in patch:
                    v = _num(patch[key], lo, hi)
                    if v is not None:
                        setattr(w, key, v)
            if patch.get("open_style") in WINDOW_OPEN_STYLES:
                w.open_style = patch["open_style"]
            if patch.get("hinge_side") in HINGE_SIDES:
                w.hinge_side = patch["hinge_side"]
            if "name" in patch:
                n = _text(patch["name"])
                if n:
                    w.name = n
            self._edited()

    def remove_window(self, index) -> None:
        r = self.room
        with self.lock:
            if isinstance(index, bool) or not isinstance(index, int) or not (0 <= index < len(r.windows)):
                raise ZoneError("That window doesn't exist any more")
            r.windows.pop(index)
            self._edited()

    def set_listener(self, x=None, z=None, yaw=None, y=None) -> None:
        L = self.room.listener
        with self.lock:
            vx = _num(x, -1e6, 1e6) if x is not None else None
            vz = _num(z, -1e6, 1e6) if z is not None else None
            vy = _num(y, 0.0, 10.0) if y is not None else None
            vyaw = _num(yaw, -1e6, 1e6) if yaw is not None else None
            if vx is not None:
                L.x = vx
            if vz is not None:
                L.z = vz
            if vy is not None:
                L.y = vy
            if vyaw is not None:
                L.yaw = math.radians(vyaw % 360.0)    # protocol: degrees
            self._edited()

    def set_volume(self, value) -> None:
        v = _num(value, 0.0, 1.0)
        if v is None:
            raise ZoneError("Volume must be a number from 0 to 1")
        with self.lock:
            self.room.master_volume = v
            self.fresh = False
            self._dirty_at = time.monotonic()
        self.hub._wake_saver()
        self.push_to_engine()

    # ----- connection -----
    def attach(self, sink: Callable[[Any], None], device_name: str = "",
               backlog: Optional[Callable[[], int]] = None) -> None:
        self._sink = sink
        self._backlog = backlog
        self.connected = True
        self.last_seen = time.time()
        if device_name:
            self.device_name = device_name
        self.hub._emit("zones", self.hub.list_zones())

    def detach(self, sink: Callable[[Any], None]) -> None:
        """The socket closed: a phone stream stops; a PC output keeps playing."""
        if self._sink is None or self._sink != sink:   # bound methods: == not is
            return
        self._sink = None
        self._backlog = None
        self.connected = False
        self.last_seen = time.time()
        if self.mode == "phone":
            self.set_output("off")
        self.hub._emit("zones", self.hub.list_zones())

    def ack(self, played, stream_id=None) -> None:
        s = self._stream
        if s is None:
            return
        if stream_id is not None and stream_id != s.id:
            return
        s.ack(played)

    # ----- output -----
    def set_output(self, mode: str, device=None) -> concurrent.futures.Future:
        """Switch output (off | phone | pc). Runs on this zone's worker thread;
        the result is announced with an "output" event."""
        if mode not in ("off", "phone", "pc"):
            raise ZoneError("Output must be off, phone or pc")
        dev = None
        if mode == "pc":
            if device is not None:
                if isinstance(device, bool) or not isinstance(device, int):
                    raise ZoneError("Pick one of the computer's sound outputs")
                if self.hub.devices() and device not in {d["index"] for d in self.hub.devices()}:
                    raise ZoneError("That sound output isn't on this computer any more")
                dev = int(device)
        if mode == "phone" and self._sink is None:
            raise ZoneError("Connect first to play on this phone")
        if mode != "off" and self.mode == "off":
            busy = [z for z in self.hub.zones() if z is not self and z.mode != "off"]
            if len(busy) >= self.hub.max_active:
                raise ZoneError(f"{len(busy)} rooms are already playing, which is as many as this "
                                f"computer runs at once. Stop one of them first.", code="busy")
        # Show "starting" at once; the switch itself may take a second or two
        self.mode, self.device, self.error = mode, dev, None
        self.playing = False
        self.starting = mode != "off"
        self.hub._emit("output", self)
        return self._worker.submit(self._switch, mode, dev, self._sink)

    def _stop_outputs(self, release: bool = True) -> None:
        s, self._stream = self._stream, None
        if s is not None:
            s.stop()
        eng = self.engine
        if eng is not None and getattr(eng, "running", False):
            try:
                eng.stop()
            except Exception:
                log.exception("zone %s engine stop", self.client_id)
        if release and eng is not None and hasattr(eng, "release"):
            try:
                eng.release()             # end the worker process, free its memory
            except Exception:
                log.exception("zone %s engine release", self.client_id)

    def _switch(self, mode: str, dev: Optional[int], sink) -> None:
        try:
            # keep the worker (and its built sound bank) when switching between outputs
            self._stop_outputs(release=(mode == "off"))
            if mode != self.mode:
                return                      # a newer switch is queued behind this one
            if mode == "off":
                self.playing = self.starting = False
            elif mode == "phone":
                if sink is None or sink != self._sink:
                    raise ZoneError("This phone is no longer connected")
                self._stream_id += 1
                s = _Stream(self, sink, self._stream_id, self.hub.max_lead_ms, self.hub.start_lead_ms,
                            backlog=self._backlog)
                self._stream = s
                s.start()
                return                      # _stream_started announces "playing"
            else:
                eng = self.ensure_engine()
                with self.lock:
                    self.sync_shared(force=True)
                eng.start_headphones(headphones_device=dev)
                if mode != self.mode:       # switched away while the device opened
                    eng.stop()
                    return
                self.playing, self.starting = True, False
            self.error = None
        except Exception as e:
            log.warning("zone %s output %s failed: %s", self.client_id, mode, e)
            self.mode, self.playing, self.starting = "off", False, False
            self.error = str(e) or "The sound output could not start"
            try:
                self._stop_outputs(release=True)
            except Exception:
                log.exception("zone %s cleanup", self.client_id)
        self.hub._emit("output", self)

    def _stream_started(self, s: _Stream) -> None:
        if s is self._stream and self.mode == "phone":
            self.playing, self.starting, self.error = True, False, None
            self.hub._emit("output", self)

    def _stream_failed(self, s: _Stream, err: str) -> None:
        if s is self._stream:
            self._stream = None
            self.mode, self.playing, self.starting = "off", False, False
            self.error = f"The rain stopped: {err}"
            eng = self.engine
            if eng is not None and hasattr(eng, "release"):
                try:
                    self._worker.submit(eng.release)
                except RuntimeError:
                    pass
            self.hub._emit("output", self)

    def stream_stats(self) -> Dict[str, Any]:
        s = self._stream
        if s is None:
            return {}
        return {"stream": s.id, "sent": s.sent, "played": s.played, "seq": s.seq,
                "lead_ms": s.lead_frames() * 1000.0 / SAMPLE_RATE,
                "render_ms": list(s.render_ms), "cpu_ms": list(s.cpu_ms),
                "block_ms": list(s.block_ms), "waits_backlog": s.waits_backlog,
                "waits_pace": s.waits_pace,
                "worker_pid": getattr(self.engine, "pid", None)}

    def close(self) -> None:
        self.mode = "off"
        try:
            self._stop_outputs()
        finally:
            self.playing = self.starting = False
            self._worker.shutdown(wait=False)


# ----------------------------------------------------------------------------
# Hub
# ----------------------------------------------------------------------------

def _default_engine_factory(room: Room):
    """Each playing phone room renders in its own worker process (see
    worker.py); RAINROOM_ZONE_INPROCESS=1 renders in this process instead."""
    from app.remote.worker import ProcessEngine
    return ProcessEngine(room, samplerate=SAMPLE_RATE, blocksize=BLOCK_FRAMES)


def _default_device_lister() -> List[dict]:
    from app.audio.spatial_engine import list_output_devices
    return list_output_devices()


class ZoneHub:
    """Shared weather + every phone zone. Qt-free and thread-safe."""

    def __init__(self, zones_dir: Optional[os.PathLike] = None,
                 engine_factory: Optional[Callable[[Room], Any]] = None,
                 device_lister: Optional[Callable[[], List[dict]]] = None,
                 save_delay_s: float = 1.0, max_lead_ms: float = 600.0,
                 start_lead_ms: float = 250.0, max_active: int = MAX_ACTIVE_ZONES,
                 max_loaded: int = MAX_LOADED_ZONES, max_files: int = MAX_ZONE_FILES):
        self.zones_dir = Path(zones_dir) if zones_dir is not None else default_zones_dir()
        self._engine_factory = engine_factory or _default_engine_factory
        self._device_lister = device_lister or _default_device_lister
        self.save_delay_s = float(save_delay_s)
        self.max_lead_ms = float(max_lead_ms)
        self.start_lead_ms = float(start_lead_ms)
        self.max_active = max(1, int(max_active))
        self.max_loaded = max(2, int(max_loaded))
        self.max_files = max(2, int(max_files))
        self._lock = threading.RLock()
        defaults = Room(width=4.0, height=2.6, depth=3.2)
        self._shared: Dict[str, Any] = {k: getattr(defaults, k) for k in SHARED_KEYS}
        self._shared_ver = 0
        self._preset: Optional[str] = presets.matching(self._ns(), "weather")
        self._preset_pick: Optional[str] = None
        self._defaults = ("Metal Roof", "Brick Wall")
        self._zones: Dict[str, Zone] = {}
        self._listeners: List[Callable[[str, Any], None]] = []
        self._phone_cbs: List[Callable[[Dict[str, Any], Optional[str]], None]] = []
        self._devices: List[dict] = []
        self._devices_t = -1e9
        self._saver: Optional[threading.Thread] = None
        self._saver_wake = threading.Event()
        self._closing = False

    # ----- hooks for the desktop / server -----
    def add_listener(self, cb: Callable[[str, Any], None]) -> None:
        """``cb(event, data)``: "weather" ({values, preset, display}), "zones"
        (list_zones()), "output" (the Zone). Called from any thread."""
        self._listeners.append(cb)

    def remove_listener(self, cb) -> None:
        try:
            self._listeners.remove(cb)
        except ValueError:
            pass

    def on_weather_from_phone(self, cb: Callable[[Dict[str, Any], Optional[str]], None]) -> None:
        """``cb(changes, preset_name)`` after a phone changed the shared weather
        (``changes`` = clamped {key: value}; preset_name set for apply_preset).
        The hub snapshot is already updated and broadcast; the desktop should
        apply ``changes`` to its Room and later call set_shared (an unchanged
        echo is not re-broadcast)."""
        self._phone_cbs.append(cb)

    def _emit(self, event: str, data) -> None:
        for cb in list(self._listeners):
            try:
                cb(event, data)
            except Exception:
                log.exception("hub listener failed (%s)", event)

    # ----- shared weather -----
    def _ns(self):
        return types.SimpleNamespace(**self._shared)

    def _shared_for_zone(self) -> Tuple[int, Dict[str, Any]]:
        with self._lock:
            return self._shared_ver, dict(self._shared)

    def shared_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._shared)

    def preset(self) -> Optional[str]:
        return self._preset

    def weather_message(self) -> Dict[str, Any]:
        with self._lock:
            vals = dict(self._shared)
            preset = self._preset
        display = {}
        for k, v in vals.items():
            try:
                display[k] = schema.by_key(k).display(v)
            except Exception:
                display[k] = str(v)
        return {"values": vals, "preset": preset, "display": display}

    def _store(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """Clamp + store shared values; returns what changed (lock held)."""
        changed = {}
        for k, v in (values or {}).items():
            if k not in SHARED_KEYS:
                continue
            s = schema.by_key(k)
            if s.kind == "toggle":
                if not isinstance(v, (bool, int, float)):
                    continue
                nv: Any = bool(v)
            else:
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                nv = float(s.clamp(v))
            if self._shared.get(k) != nv:
                self._shared[k] = nv
                changed[k] = nv
        return changed

    def set_shared(self, values: Dict[str, Any], preset_name: Optional[str] = None) -> bool:
        """Desktop -> hub: the desktop Room's values (all or some shared keys)
        and its preset chip (None = work it out). Broadcasts if anything changed."""
        with self._lock:
            changed = self._store(values)
            preset = preset_name if preset_name is not None else presets.matching(
                self._ns(), "weather", prefer=self._preset_pick)
            moved = bool(changed) or preset != self._preset
            self._preset = preset
            if changed:
                self._shared_ver += 1
        if moved:
            self._after_shared_change()
        return moved

    def sync_from_room(self, room: Room, preset_name: Optional[str] = None) -> bool:
        """Convenience: set_shared from a whole Room + remember its materials as
        the default for new phone rooms."""
        self.set_house_defaults(room.roof_material, room.wall_material)
        return self.set_shared({k: getattr(room, k) for k in SHARED_KEYS if hasattr(room, k)}, preset_name)

    def set_house_defaults(self, roof_material: str, wall_material: str) -> None:
        roof = role_material(roof_material, "roof")
        wall = role_material(wall_material, "wall")
        self._defaults = (roof, wall)

    def weather_from_phone(self, key, value) -> Dict[str, Any]:
        if key not in SHARED_KEYS:
            raise ZoneError("That isn't a weather setting")
        with self._lock:
            changed = self._store({key: value})
            if key not in changed and key in self._shared:
                if not isinstance(value, (bool, int, float)):
                    raise ZoneError("Weather values must be numbers")
            if changed:
                self._shared_ver += 1
                self._preset_pick = None
                self._preset = presets.matching(self._ns(), "weather")
        if changed:
            self._after_shared_change()
            self._tell_desktop(changed, None)
        return changed

    def preset_names(self) -> List[str]:
        return [p.name for p in presets.all_presets("weather")]

    def preset_info(self) -> List[Dict[str, Any]]:
        return [{"name": p.name, "description": p.description, "builtin": p.builtin}
                for p in presets.all_presets("weather")]

    def apply_preset(self, name) -> Dict[str, Any]:
        found = next((p for p in presets.all_presets("weather")
                      if isinstance(name, str) and p.name.lower() == name.strip().lower()), None)
        if found is None:
            raise ZoneError("That weather preset isn't available")
        with self._lock:
            changed = self._store(found.values)
            self._preset_pick = found.name
            moved = bool(changed) or self._preset != found.name
            self._preset = found.name
            if changed:
                self._shared_ver += 1
        if moved:
            self._after_shared_change()
        self._tell_desktop(changed, found.name)
        return changed

    def _tell_desktop(self, changed, preset_name) -> None:
        for cb in list(self._phone_cbs):
            try:
                cb(dict(changed), preset_name)
            except Exception:
                log.exception("on_weather_from_phone callback failed")

    def _after_shared_change(self) -> None:
        # A PC-output zone renders on the engine's own mixer: push values now
        for z in self.zones():
            if z.mode == "pc":
                with z.lock:
                    z.sync_shared()
                try:
                    z.push_to_engine()
                except Exception:
                    log.exception("zone %s weather push", z.client_id)
        self._emit("weather", self.weather_message())

    # ----- schema for the phone -----
    @staticmethod
    def weather_schema() -> List[Dict[str, Any]]:
        out = []
        for g in PHONE_GROUPS:
            for s in schema.in_group(g):
                out.append({"key": s.key, "label": s.label, "group": s.group, "lo": s.lo,
                            "hi": s.hi, "step": s.step, "kind": s.kind, "left": s.left,
                            "right": s.right, "help": s.help, "advanced": s.advanced,
                            "default": s.default, "enabled_by": s.enabled_by})
        return out

    @staticmethod
    def materials_info() -> Dict[str, Any]:
        return {
            "roof": list(ROOF_MATERIALS), "wall": list(WALL_MATERIALS),
            "roof_hints": {m: schema.material_hint(m, "roof") for m in ROOF_MATERIALS},
            "wall_hints": {m: schema.material_hint(m, "wall") for m in WALL_MATERIALS},
            "window_styles": [{"key": k, "label": WINDOW_STYLE_LABELS.get(k, k)}
                              for k in WINDOW_OPEN_STYLES],
            "walls": list(WALLS), "hinge_sides": list(HINGE_SIDES),
            "limits": {"size": [SIZE_MIN, SIZE_MAX], "height": [CEIL_MIN, CEIL_MAX],
                       "terrain_size": [TERRAIN_MIN, TERRAIN_MAX], "thickness_cm": [0.0, THICK_MAX_CM],
                       "window_width_min": WIN_W_MIN, "window_height": [WIN_H_MIN, WIN_H_MAX],
                       "sill_max": SILL_MAX, "max_angle_deg": [ANGLE_MIN, ANGLE_MAX],
                       "max_windows": MAX_WINDOWS},
        }

    # ----- devices -----
    def devices(self, refresh: bool = False) -> List[dict]:
        with self._lock:
            if refresh or time.monotonic() - self._devices_t > 30.0:
                try:
                    raw = list(self._device_lister() or [])
                except Exception as e:
                    log.warning("device list failed: %s", e)
                    raw = []
                self._devices = [{"index": int(d["index"]), "name": clean_device_name(d.get("name", "?")),
                                  "default": bool(d.get("is_default", False))}
                                 for d in raw if "index" in d]
                self._devices_t = time.monotonic()
            return [dict(d) for d in self._devices]

    # ----- zones -----
    def zones(self) -> List[Zone]:
        with self._lock:
            return list(self._zones.values())

    def get_zone(self, client_id: str) -> Optional[Zone]:
        with self._lock:
            return self._zones.get(client_id)

    def list_zones(self) -> List[Dict[str, Any]]:
        return [z.summary() for z in self.zones()]

    def _zone_path(self, cid: str) -> Path:
        path = self.zones_dir / f"{cid}.json"
        if not path.is_file() and self.zones_dir.is_dir():
            # a file saved before ids were lower-cased (case-sensitive file systems)
            for f in self.zones_dir.glob("*.json"):
                if f.stem.lower() == cid:
                    return f
        return path

    def _evict_idle(self) -> None:
        """Drop idle rooms from memory (saved first) when too many are loaded:
        not connected, not playing, longest unseen first."""
        idle = sorted((z for z in self._zones.values() if not z.connected and z.mode == "off"),
                      key=lambda z: z.last_seen)
        while len(self._zones) >= self.max_loaded and idle:
            z = idle.pop(0)
            if z._dirty_at is not None:
                self._save_zone(z)
            self._zones.pop(z.client_id, None)
            try:
                z.close()
            except Exception:
                log.exception("zone %s close", z.client_id)

    def zone_for(self, client_id) -> Zone:
        """The zone for ``client_id`` (loaded from disk or made new). A new room
        is only written to disk once it is edited."""
        cid = sanitize_client_id(client_id)
        if cid is None:
            raise ZoneError("This phone's id is not valid")
        with self._lock:
            z = self._zones.get(cid)
            if z is not None:
                return z
            if len(self._zones) >= self.max_loaded:
                self._evict_idle()
                if len(self._zones) >= self.max_loaded:
                    raise ZoneError("Too many phones are using RainRoom right now. Try again later.",
                                    code="busy")
            path = self._zone_path(cid)
            room = None
            if path.is_file():
                try:
                    room = load_room(str(path))
                except Exception as e:
                    log.warning("zone file %s unreadable (%s) — starting fresh", path, e)
            fresh = room is None
            if fresh:
                room = default_zone_room(*self._defaults)
            fit_room(room)
            z = Zone(self, cid, room, path)
            z.fresh = fresh
            self._zones[cid] = z
        z.sync_shared(force=True)
        return z

    # ----- saved rooms (so a phone can pick its room again) -----
    def saved_rooms(self) -> List[Dict[str, Any]]:
        """Every phone room on disk (and rooms in use that aren't saved yet),
        newest first: {id, name, width, depth, windows, updated, in_use}."""
        out: Dict[str, Dict[str, Any]] = {}
        files = []
        try:
            files = list(self.zones_dir.glob("*.json")) if self.zones_dir.is_dir() else []
        except OSError:
            pass
        for f in files[: self.max_files * 2]:
            cid = sanitize_client_id(f.stem)
            if cid is None:
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                r = d.get("room", {}) if isinstance(d, dict) else {}
                out[cid] = {"id": cid, "name": str(r.get("name") or d.get("name") or "Room")[:NAME_MAX],
                            "width": float(r.get("width", 0.0)), "depth": float(r.get("depth", 0.0)),
                            "windows": len(d.get("windows", []) or []),
                            "updated": f.stat().st_mtime, "in_use": False}
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        for z in self.zones():
            if z.fresh and not z.connected:
                continue
            with z.lock:
                out[z.client_id] = {"id": z.client_id, "name": z.room.name, "width": float(z.room.width),
                                    "depth": float(z.room.depth), "windows": len(z.room.windows),
                                    "updated": out.get(z.client_id, {}).get("updated", time.time()),
                                    "in_use": bool(z.connected)}
        return sorted(out.values(), key=lambda r: -float(r["updated"]))

    def forget_room(self, client_id, by: Optional[str] = None) -> bool:
        """Delete a saved phone room that nobody is using (not the asker's own)."""
        cid = sanitize_client_id(client_id)
        if cid is None or cid == by:
            raise ZoneError("That room can't be removed")
        with self._lock:
            z = self._zones.get(cid)
            if z is not None:
                if z.connected or z.mode != "off":
                    raise ZoneError("That room is in use right now")
                self._zones.pop(cid, None)
                z._dirty_at = None
                z.close()
            path = self._zone_path(cid)
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            except OSError as e:
                raise ZoneError(f"Couldn't remove that room ({e.__class__.__name__})")
        self._emit("zones", self.list_zones())
        return True

    def stop_zone_output(self, client_id: str) -> bool:
        z = self.get_zone(client_id)
        if z is None:
            return False
        z.set_output("off")
        return True

    # ----- saving (debounced) -----
    def _wake_saver(self) -> None:
        with self._lock:
            if self._saver is None or not self._saver.is_alive():
                self._saver = threading.Thread(target=self._save_loop, name="zone-saver", daemon=True)
                self._saver.start()
        self._saver_wake.set()

    def _save_loop(self) -> None:
        while not self._closing:
            self._saver_wake.wait(0.25)
            self._saver_wake.clear()
            now = time.monotonic()
            for z in self.zones():
                if z._dirty_at is not None and now - z._dirty_at >= self.save_delay_s:
                    self._save_zone(z)

    def _save_zone(self, z: Zone) -> None:
        with z.lock:
            z._dirty_at = None
            try:
                self.zones_dir.mkdir(parents=True, exist_ok=True)
                new_file = not z.path.is_file()
                tmp = z.path.with_suffix(".json.tmp")
                save_room(z.room, str(tmp))
                os.replace(tmp, z.path)
            except Exception:
                log.exception("could not save zone %s", z.client_id)
                return
        if new_file:
            self._prune_files()

    def _prune_files(self) -> None:
        """Keep at most max_files saved rooms: remove the oldest ones nobody has loaded."""
        try:
            files = [f for f in self.zones_dir.glob("*.json") if f.is_file()]
            if len(files) <= self.max_files:
                return
            loaded = {z.client_id for z in self.zones()}
            old = sorted((f for f in files if f.stem.lower() not in loaded), key=lambda f: f.stat().st_mtime)
            for f in old[: len(files) - self.max_files]:
                try:
                    f.unlink()
                    log.info("pruned old phone room %s", f.name)
                except OSError:
                    pass
        except OSError:
            log.exception("prune phone rooms")

    def flush(self) -> None:
        for z in self.zones():
            if z._dirty_at is not None:
                self._save_zone(z)

    def stop_all(self) -> None:
        """Stop every zone's output (streams and PC devices) and save."""
        for z in self.zones():
            try:
                z.close()
            except Exception:
                log.exception("zone %s close", z.client_id)
        self.flush()
        with self._lock:
            self._zones.clear()

    def close(self) -> None:
        self._closing = True
        self.stop_all()
        self._saver_wake.set()


__all__ = [
    "AUDIO_MAGIC", "BLOCK_FRAMES", "HEADER", "SAMPLE_RATE", "SHARED_KEYS", "Zone", "ZoneError",
    "ZoneHub", "clean_device_name", "default_zone_room", "default_zones_dir", "fit_room",
    "sanitize_client_id",
]
