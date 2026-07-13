
"""
Spatial rain engine — 3D outdoor rain field → indoor mics / binaural.

Simulation model
----------------
1. Drops spawn in a **depth orchestra** outside the house:
   near yard, mid yard, far field, roof, and elevated canopy.
2. Each drop is a free-field source with true (x, y, z).
3. Sound couples indoors through **window apertures** (and a weak roof path):
   outdoor distance → aperture → indoor distance.
4. Propagation adds **delay** (speed of sound), **1/r attenuation**, and
   **air absorption** (distance low-pass) so far rain is soft and late.
5. Speakers are virtual microphones at their 3D positions.
6. Headphone mode renders **binaural** at the listener through the same paths.

Intensity controls drop *rate* only, not a noise bed.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None

from app.audio.engine import synth_drop, _db, ContinuousRainBed, _peak_cap
from app.audio.field_bed import OutdoorFieldBed
from app.audio.field_router import OutdoorFieldRouter
from app.audio.reverb import MonoRoomReverb, StereoRoomReverb, room_reverb_from_layout
from app.audio.spatial import (
    render_drop_to_listener_binaural,
    render_drop_to_receiver_mono,
)
from app.models.room import Room, Speaker

log = logging.getLogger("audio.spatial")


def _soft_clip(sig: np.ndarray, ceiling: float = 0.88) -> np.ndarray:
    """Sample-wise soft clip — no per-block gain jumps (avoids crackle)."""
    x = np.asarray(sig, dtype=np.float64)
    c = max(1e-6, float(ceiling))
    # Smooth knee: linear below ~0.7*c, tanh above
    return c * np.tanh(x / c)


class _SmoothLimiter:
    """Block gain smoother: never hard-normalizes a block to a new scale.

    Tracks envelope and eases gain toward ceiling so successive blocks
    don't click when peaks differ.
    """

    def __init__(self, ceiling: float = 0.88, release_s: float = 0.12, attack_s: float = 0.003):
        self.ceiling = float(ceiling)
        self.release_s = float(release_s)
        self.attack_s = float(attack_s)
        self._gain = 1.0

    def reset(self):
        self._gain = 1.0

    def process(self, x: np.ndarray, sr: int) -> np.ndarray:
        y = np.asarray(x, dtype=np.float64)
        if y.size == 0:
            return y
        pk = float(np.max(np.abs(y)) + 1e-12)
        # Target gain so peak would sit at ceiling (never boost, only attenuate)
        target = 1.0 if pk <= self.ceiling else (self.ceiling / pk)
        # Time constants per block
        n = y.shape[0]
        dt = n / max(1, int(sr))
        if target < self._gain:
            coeff = 1.0 - math.exp(-dt / max(1e-4, self.attack_s))
        else:
            coeff = 1.0 - math.exp(-dt / max(1e-4, self.release_s))
        self._gain += (target - self._gain) * coeff
        out = y * self._gain
        # Final soft safety (sample-wise) — no hard brickwall
        return _soft_clip(out, self.ceiling)


def _hostapi_score(hostapi_index: Optional[int], hostapis: list) -> int:
    """Prefer modern APIs so the same physical device is not listed 3–4 times."""
    try:
        name = str(hostapis[int(hostapi_index)].get("name", "")).lower()
    except Exception:
        return 0
    if "wasapi" in name:
        return 100
    if "core audio" in name or "coreaudio" in name:
        return 100
    if "pulse" in name or "pipewire" in name:
        return 95
    if "alsa" in name and "pulse" not in name:
        return 85
    if "wdm" in name or "kernel streaming" in name:
        return 75
    if "directsound" in name:
        return 50
    if "mme" in name:
        return 15
    return 40


def _norm_device_name(name: str) -> str:
    n = " ".join(str(name or "?").lower().split())
    # Strip common host-api suffixes sounddevice sometimes appends
    for junk in (" (wasapi)", " (directsound)", " (mme)", " (windows wdm-ks)"):
        if n.endswith(junk):
            n = n[: -len(junk)].strip()
    return n


def list_output_devices() -> List[dict]:
    """List unique output devices (deduped by name, prefer WASAPI / CoreAudio)."""
    if sd is None:
        return []
    try:
        devs = sd.query_devices()
        try:
            hostapis = list(sd.query_hostapis())
        except Exception:
            hostapis = []
        try:
            default_out = sd.default.device[1] if isinstance(sd.default.device, (list, tuple)) else None
        except Exception:
            default_out = None

        raw: List[dict] = []
        for i, d in enumerate(devs):
            ch = int(d.get("max_output_channels", 0) or 0)
            if ch < 1:
                continue
            hai = d.get("hostapi")
            raw.append({
                "index": i,
                "name": d.get("name", "?"),
                "hostapi": hai,
                "hostapi_name": (
                    hostapis[int(hai)].get("name", "?") if hai is not None and int(hai) < len(hostapis) else "?"
                ),
                "channels": ch,
                "default_sr": d.get("default_samplerate", 48000),
                "is_default": (default_out is not None and int(default_out) == i),
                "_score": _hostapi_score(hai, hostapis) + (50 if default_out is not None and int(default_out) == i else 0) + min(ch, 8),
                "_key": _norm_device_name(d.get("name", "?")),
            })

        # Keep best entry per normalized name (WASAPI wins over MME/DirectSound)
        best: Dict[str, dict] = {}
        for d in raw:
            k = d["_key"]
            prev = best.get(k)
            if prev is None or d["_score"] > prev["_score"]:
                best[k] = d

        outs = sorted(best.values(), key=lambda d: (-int(d.get("is_default")), d["name"].lower(), d["index"]))
        for d in outs:
            d.pop("_score", None)
            d.pop("_key", None)
        return outs
    except Exception as e:
        log.exception("device query failed: %s", e)
        return []


def _material_surface(name: Optional[str]) -> str:
    if not name:
        return "water"
    low = str(name).lower()
    mapping = (
        ("glass", "glass"),
        ("window", "glass"),
        ("metal", "metal"),
        ("tin", "metal"),
        ("roof", "metal"),
        ("wood", "wood"),
        ("shingle", "shingle"),
        ("tile", "tile"),
        ("brick", "brick"),
        ("water", "water"),
        ("puddle", "water"),
    )
    for key, surf in mapping:
        if key in low:
            return surf
    return "metal"


def _add_1d(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Add two mono buffers, extending the shorter with zeros."""
    if len(a) == len(b):
        return a + b
    n = max(len(a), len(b))
    out = np.zeros(n, dtype=np.float64)
    out[: len(a)] += a
    out[: len(b)] += b
    return out


def _add_2d(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Add stereo (N,2) buffers."""
    if a.shape == b.shape:
        return a + b
    n = max(a.shape[0], b.shape[0])
    out = np.zeros((n, 2), dtype=np.float64)
    out[: a.shape[0]] += a
    out[: b.shape[0]] += b
    return out


class _TapVoice:
    """One outdoor drop, already path-traced to each receiver channel."""

    __slots__ = ("taps",)  # list of (ch_index, mono_buf, pos)

    def __init__(self, taps: List[Tuple[int, np.ndarray, int]]):
        self.taps = taps

    @property
    def remaining(self) -> int:
        if not self.taps:
            return 0
        return max(len(buf) - pos for _, buf, pos in self.taps)


class _StereoVoice:
    """Binaural (N,2) drop for headphone / offline fold-down."""

    __slots__ = ("buf", "pos")

    def __init__(self, stereo: np.ndarray):
        self.buf = np.asarray(stereo, dtype=np.float64)
        if self.buf.ndim == 1:
            self.buf = np.stack([self.buf, self.buf], axis=1)
        self.pos = 0

    @property
    def remaining(self) -> int:
        return self.buf.shape[0] - self.pos


class _DeviceBus:
    """Output bus for one OS device. Queue items are (frames, channels) float32."""

    def __init__(self, device_index: int, samplerate: int, blocksize: int, channels: int, q: queue.Queue):
        self.device_index = device_index
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.channels = max(1, min(2, int(channels)))
        self.q = q
        self.stream = None
        # leftover stereo/mono frames from previous callback
        self._carry = np.zeros((0, self.channels), dtype=np.float32)

    def start(self):
        if sd is None:
            raise RuntimeError("sounddevice is not available")
        kwargs = dict(
            device=int(self.device_index) if self.device_index is not None else None,
            channels=self.channels,
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            dtype="float32",
            callback=self._cb,
        )
        try:
            self.stream = sd.OutputStream(latency="high", **kwargs)
        except TypeError:
            self.stream = sd.OutputStream(**kwargs)
        except Exception:
            # Fallback: try without explicit device if index is stale
            log.exception("Failed to open device %s — retrying default", self.device_index)
            kwargs["device"] = None
            self.stream = sd.OutputStream(**kwargs)
        self.stream.start()
        log.info(
            "Output bus started: device=%s channels=%s block=%s",
            self.device_index, self.channels, self.blocksize,
        )

    def _normalize_block(self, block: np.ndarray) -> np.ndarray:
        """Ensure shape (N, channels)."""
        block = np.asarray(block, dtype=np.float32)
        if block.ndim == 1:
            if self.channels == 1:
                return block.reshape(-1, 1)
            return np.stack([block, block], axis=1)
        # (N, C)
        if block.shape[1] >= self.channels:
            return block[:, : self.channels]
        # pad channels
        out = np.zeros((block.shape[0], self.channels), dtype=np.float32)
        out[:, : block.shape[1]] = block
        return out

    def _cb(self, outdata, frames, time_info, status):
        try:
            if status:
                log.warning("device %s status: %s", self.device_index, status)
            need = frames
            parts = []
            if self._carry.shape[0] > 0:
                take = min(need, self._carry.shape[0])
                parts.append(self._carry[:take])
                self._carry = self._carry[take:]
                need -= take
            while need > 0:
                try:
                    raw = self.q.get_nowait()
                except queue.Empty:
                    parts.append(np.zeros((need, self.channels), dtype=np.float32))
                    need = 0
                    break
                block = self._normalize_block(raw)
                if block.shape[0] <= need:
                    parts.append(block)
                    need -= block.shape[0]
                else:
                    parts.append(block[:need])
                    self._carry = block[need:]
                    need = 0
            buf = np.concatenate(parts, axis=0) if parts else np.zeros((frames, self.channels), dtype=np.float32)
            outdata[:] = buf[:frames]
        except Exception:
            log.exception("bus callback error")
            outdata[:] = 0

    def stop(self):
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        finally:
            self.stream = None


class SpatialRainEngine:
    """Outdoor rain sim → per-speaker mono → multi-device output."""

    def __init__(self, room: Room, samplerate: int = 48000, blocksize: int = 2048, max_voices: int = 320):
        self.room = room
        self.samplerate = int(samplerate)
        # Larger blocks = fewer callbacks + more CPU margin (less underrun crackle)
        self.blocksize = int(blocksize)
        self.max_voices = int(max_voices)
        self._voices: deque[_TapVoice] = deque()
        self._stereo_voices: deque[_StereoVoice] = deque()
        self._time = 0.0
        self._next_event = 0.0
        self._evt_id = 0
        self._rng = np.random.RandomState(11)
        self._buses: Dict[int, _DeviceBus] = {}
        self._queues: Dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self.running = False
        self._devices_cache = list_output_devices()
        # Internal synth level (before user volume).
        self._master = 0.85
        # Comfortable at vol ~75–85% without riding the limiter (crackle source)
        self._output_fs_gain = 4.0
        self._output_ceiling = 0.88
        self._limiter = _SmoothLimiter(ceiling=0.88, release_s=0.14, attack_s=0.002)
        self._mixer_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._hp_stream = None
        self._hp_queue: Optional[queue.Queue] = None
        self._mode = "stopped"  # multi | headphones
        self.use_noise_bed = False
        self._bed_water = ContinuousRainBed(sr=self.samplerate, seed=4242)
        self._bed_roof = ContinuousRainBed(sr=self.samplerate, seed=7771)
        # Continuous outdoor field (procedural + optional WAV samples)
        self.use_outdoor_field = True
        self._field = OutdoorFieldBed(sr=self.samplerate, seed=2024)
        self._field_router = OutdoorFieldRouter(sr=self.samplerate)
        # Mild indoor reverb (stateful, block-rate)
        self.use_reverb = True
        self._reverb_mono: Dict[int, MonoRoomReverb] = {}
        self._reverb_stereo = StereoRoomReverb(sr=self.samplerate, wet=0.18)
        # Always build listener binaural for headphone / offline preview
        self.render_listener_binaural = True
        self._device_channels: Dict[int, int] = {}
        # Effective wind (after optional variation) — updated each audio block
        self._wind_speed_eff = float(getattr(room, "wind_speed", abs(getattr(room, "wind", 0.0))))
        self._wind_dir_eff = float(getattr(room, "wind_direction_deg", 90.0))
        self._wind_speed_target = self._wind_speed_eff
        self._wind_dir_target = self._wind_dir_eff
        self._wind_t_next_dir = 0.0
        self._wind_t_next_speed = 0.0
        self._include_you = False
        self._hp_queue = None

    # ----- devices -----
    @property
    def devices(self) -> List[dict]:
        return list(self._devices_cache)

    def refresh_devices(self) -> List[dict]:
        self._devices_cache = list_output_devices()
        return self.devices

    def set_speaker_device(self, speaker: Speaker, device_index: Optional[int]):
        speaker.audio_device = device_index

    # ----- master volume -----
    def get_volume(self) -> float:
        """User volume 0..1 (1 = calibrated full scale)."""
        return max(0.0, min(1.0, float(getattr(self.room, "master_volume", 0.75))))

    def set_volume(self, v: float):
        """Live-safe volume set (takes effect next audio block)."""
        self.room.master_volume = max(0.0, min(1.0, float(v)))

    def _apply_master(self, x: np.ndarray) -> np.ndarray:
        """Apply user volume + soft limiter (no hard per-block peak-norm)."""
        vol = self.get_volume()
        # Perceptual volume curve; keep gain modest to avoid constant limiting
        g = (vol ** 0.85) * float(self._output_fs_gain)
        y = np.asarray(x, dtype=np.float64) * g
        return self._limiter.process(y, self.samplerate)

    # ----- scheduling -----
    def _sharpness(self) -> float:
        """0 soft/muffled rain · 1 hard/crisp — from rain_intensity."""
        return max(0.0, min(1.0, float(getattr(self.room, "rain_intensity", 0.45))))

    def _wind_speed(self) -> float:
        return max(0.0, min(1.0, float(self._wind_speed_eff)))

    def _wind_dir_deg(self) -> float:
        return float(self._wind_dir_eff) % 360.0

    def _wind_push(self) -> Tuple[float, float]:
        return Room.wind_push_xz(self._wind_speed(), self._wind_dir_deg())

    def _update_wind(self, dt: float) -> None:
        """Slew effective wind toward targets; pick new targets on intervals."""
        r = self.room
        dt = max(0.0, float(dt))
        base_spd = max(0.0, min(1.0, float(getattr(r, "wind_speed", 0.0))))
        base_dir = float(getattr(r, "wind_direction_deg", 90.0)) % 360.0

        # If variation off, snap to base (smoothly)
        if not getattr(r, "wind_vary_direction", False):
            self._wind_dir_target = base_dir
        if not getattr(r, "wind_vary_speed", False):
            self._wind_speed_target = base_spd

        t = self._time
        # --- direction targets ---
        if getattr(r, "wind_vary_direction", False) and base_spd > 0.02:
            if t >= self._wind_t_next_dir:
                rng = max(0.0, float(getattr(r, "wind_dir_range_deg", 45.0)))
                self._wind_dir_target = (base_dir + float(self._rng.uniform(-rng, rng))) % 360.0
                iv = max(0.5, float(getattr(r, "wind_dir_interval_s", 10.0)))
                # slight randomize interval so it isn't metronomic
                self._wind_t_next_dir = t + iv * float(self._rng.uniform(0.7, 1.35))
        else:
            self._wind_t_next_dir = t + 1.0

        if getattr(r, "wind_vary_speed", False):
            if t >= self._wind_t_next_speed:
                span = max(0.0, float(getattr(r, "wind_speed_range", 0.25)))
                self._wind_speed_target = max(0.0, min(1.0, base_spd + float(self._rng.uniform(-span, span))))
                iv = max(0.5, float(getattr(r, "wind_speed_interval_s", 8.0)))
                self._wind_t_next_speed = t + iv * float(self._rng.uniform(0.7, 1.35))
        else:
            self._wind_t_next_speed = t + 1.0

        # --- slew direction (shortest arc) ---
        slew_d = max(1.0, float(getattr(r, "wind_dir_slew_deg_s", 15.0)))
        cur = self._wind_dir_eff % 360.0
        tgt = self._wind_dir_target % 360.0
        delta = (tgt - cur + 540.0) % 360.0 - 180.0
        step = max(-slew_d * dt, min(slew_d * dt, delta))
        self._wind_dir_eff = (cur + step) % 360.0

        # --- slew speed ---
        slew_s = max(0.02, float(getattr(r, "wind_speed_slew_per_s", 0.2)))
        ds = self._wind_speed_target - self._wind_speed_eff
        self._wind_speed_eff += max(-slew_s * dt, min(slew_s * dt, ds))
        self._wind_speed_eff = max(0.0, min(1.0, self._wind_speed_eff))

    def _ips(self) -> float:
        """Discrete impacts per second — quantity primary, sharpness shapes density.

        Soft rain → more light micro hits. Sharp → harder hits, still dense enough
        to read as rain (not sparse taps). Wind modestly increases rate.
        """
        quantity = float(getattr(self.room, "droplet_density", 0.5))
        sh = self._sharpness()
        wabs = self._wind_speed()
        if quantity <= 0.0005:
            return 0.0
        # Low quantity still needs a lively spit rate (user sweet-spot ~4%).
        # Old power curve left ~3–4 hits/s at q=0.04 — felt “slow”.
        # Floor + mild curve: ~18–25/s at 4%, ~55 at mid, cap ~95.
        soft_boost = 1.08 - 0.12 * sh
        floor = 10.0 + 6.0 * (1.0 - sh)   # soft rain → slightly denser light hits
        body = 72.0 * (quantity ** 0.55)
        win_boost = 1.0 + 0.03 * len(getattr(self.room, "windows", []) or [])
        wind_boost = 1.0 + 0.28 * wabs
        return float(min(95.0, (floor + body) * soft_boost * win_boost * wind_boost))

    def _pick_source_3d(self) -> Tuple[str, float, float, float, float]:
        """Orchestra of outdoor depth layers.

        Returns (layer, x, y, z, depth_m_from_house).
        Layers:
          near   — 0.6–3 m outside a window (clear, close)
          mid    — 3–10 m yard
          far    — 10–28 m field (soft, delayed)
          roof   — roof plane hits
          canopy — elevated falling rain column outside
        """
        r = self.room
        rng = self._rng
        wx, wz = self._wind_push()
        wabs = self._wind_speed()
        terrain = float(getattr(r, "terrain_size", 36.0))
        far_max = min(28.0, max(12.0, terrain * 0.45))
        windows = list(getattr(r, "windows", []) or [])

        u = float(rng.rand())
        # Bias toward near/mid window rain (what you actually hear indoors).
        # Far/canopy stay for depth but shouldn't dominate the mix.
        if windows and u < 0.34 + 0.10 * wabs:
            layer = "near"
            depth = float(rng.uniform(0.6, 2.8))
        elif u < 0.68 + 0.04 * wabs:
            layer = "mid"
            depth = float(rng.uniform(2.8, 9.0))
        elif u < 0.84:
            layer = "far"
            depth = float(rng.uniform(9.0, far_max))
        elif u < 0.93 - 0.04 * wabs:
            layer = "roof"
            depth = 0.0
        else:
            layer = "canopy"
            depth = float(rng.uniform(2.0, 12.0))

        def _along_window(win, out_dist: float):
            """Point outside a window with lateral spread along the wall."""
            wall = (getattr(win, "wall", "north") or "north").lower()
            cx, cy, cz = r.window_center(win)
            lat = float(rng.uniform(-0.55, 0.55)) * float(getattr(win, "width", 1.0))
            # Wind shears along facade (use dominant tangential push)
            shear = (wx if wall in ("north", "south") else wz)
            lat += shear * float(rng.uniform(0.1, 0.5)) * float(getattr(win, "width", 1.0))
            vert = float(rng.normal(0.0, 0.35))
            y = max(0.0, cy + vert)
            if wall == "north":
                return cx + lat, y, cz + out_dist
            if wall == "south":
                return cx + lat, y, cz - out_dist
            if wall == "east":
                return cx + out_dist, y, cz + lat
            return cx - out_dist, y, cz + lat

        def _outside_wall(wall: str, out_dist: float):
            wall = wall.lower()
            if wall == "north":
                return (
                    float(rng.uniform(0.0, r.width)),
                    0.0,
                    r.depth + out_dist,
                )
            if wall == "south":
                return (
                    float(rng.uniform(0.0, r.width)),
                    0.0,
                    -out_dist,
                )
            if wall == "east":
                return (
                    r.width + out_dist,
                    0.0,
                    float(rng.uniform(0.0, r.depth)),
                )
            return (
                -out_dist,
                0.0,
                float(rng.uniform(0.0, r.depth)),
            )

        def _windward_walls():
            """Facades rain is driven into (along push vector)."""
            walls = []
            if wx > 0.12:
                walls.append("east")
            if wx < -0.12:
                walls.append("west")
            if wz > 0.12:
                walls.append("north")
            if wz < -0.12:
                walls.append("south")
            if not walls:
                return ["north", "south", "east", "west"]
            for side in ("north", "south", "east", "west"):
                if side not in walls:
                    walls.append(side)
            return walls[:3]

        if layer == "roof":
            x = float(rng.uniform(0.1, max(0.2, r.width - 0.1)))
            z = float(rng.uniform(0.1, max(0.2, r.depth - 0.1)))
            x = max(0.1, min(r.width - 0.1, x + wx * r.width * 0.25))
            z = max(0.1, min(r.depth - 0.1, z + wz * r.depth * 0.25))
            y = float(r.height) + 0.02
            depth = 0.0
        elif layer == "canopy":
            if windows and float(rng.rand()) < 0.7:
                ww = _windward_walls()
                cands = [w for w in windows if (getattr(w, "wall", "") or "").lower() in ww] or windows
                w = cands[int(rng.randint(0, len(cands)))]
                x, _y0, z = _along_window(w, depth)
            else:
                walls = _windward_walls()
                wall = walls[int(rng.randint(0, len(walls)))]
                x, _y0, z = _outside_wall(wall, depth)
            y = float(rng.uniform(1.5, max(2.5, r.height + 4.0)))
            x += wx * float(rng.uniform(0.3, 1.2))
            z += wz * float(rng.uniform(0.3, 1.2))
        else:
            if windows and float(rng.rand()) < 0.85 + 0.1 * wabs:
                ww = _windward_walls()
                cands = [w for w in windows if (getattr(w, "wall", "") or "").lower() in ww] or windows
                weights = []
                for w in cands:
                    wall = (getattr(w, "wall", "north") or "north").lower()
                    align = 1.0
                    if wall == "east":
                        align = 1.0 + 1.4 * max(0.0, wx)
                    elif wall == "west":
                        align = 1.0 + 1.4 * max(0.0, -wx)
                    elif wall == "north":
                        align = 1.0 + 1.4 * max(0.0, wz)
                    elif wall == "south":
                        align = 1.0 + 1.4 * max(0.0, -wz)
                    weights.append(max(0.05, float(getattr(w, "open", 0.7)) * align))
                weights = np.array(weights, dtype=np.float64)
                weights /= weights.sum()
                w = cands[int(rng.choice(len(cands), p=weights))]
                x, y, z = _along_window(w, depth)
                if layer != "near":
                    y = float(rng.uniform(0.0, 0.35))
                else:
                    y = float(rng.uniform(0.0, max(0.2, getattr(w, "sill", 0.9) * 0.3)))
            else:
                walls = _windward_walls()
                wall = walls[int(rng.randint(0, len(walls)))]
                x, y, z = _outside_wall(wall, depth)
                y = float(rng.uniform(0.0, 0.25))

        x += wx * float(rng.uniform(0.2, 1.0)) * (0.5 + 0.08 * depth)
        z += wz * float(rng.uniform(0.2, 1.0)) * (0.5 + 0.08 * depth)
        return layer, float(x), float(y), float(z), float(depth)

    def _surface_for_layer(self, layer: str) -> str:
        if layer == "roof":
            base = _material_surface(getattr(self.room, "roof_material", "Metal Roof"))
            return "tile" if base == "metal" else base
        # Default: soft wet water. Hollow tarp/shell are rare specials only.
        u = float(self._rng.rand())
        if layer in ("canopy", "mid"):
            tarp_p = 0.020 if layer == "canopy" else 0.010
            shell_p = 0.010 if layer == "canopy" else 0.005
            if u < tarp_p:
                return "tarp"
            if u < tarp_p + shell_p:
                return "shell"
        return "water"

    def _spawn_event(self, schedule_delay_n: int = 0):
        """Spawn outdoor drop → couple through windows → speakers / ears.

        Windows are portals: openness sets volume; indoor distance from
        each speaker to each window sets relative loudness and delay.
        Sound is perceived as coming from the windows (behind the glass).
        """
        layer, x, y, z, depth = self._pick_source_3d()
        surface = self._surface_for_layer(layer)
        sharp = self._sharpness()
        wabs = self._wind_speed()
        q = float(getattr(self.room, "droplet_density", 0.5) or 0.5)

        # Size: sharpness + quantity → soft/light drizzle vs sharp/heavy hits
        # Soft rain stays small even at high quantity; sharp allows larger drops.
        u = float(self._rng.rand())
        power = 1.85 - 0.55 * sharp          # soft → many micros
        t = u ** power
        size_max = 2.2 + 2.8 * sharp         # soft max ~2.2 mm, sharp ~5.0 mm
        size = 0.45 + t * size_max
        # High quantity slightly favors smaller drops (spray) unless sharp
        size *= 1.0 - 0.18 * q * (1.0 - 0.5 * sharp)

        # Wind hardens impact timbre (not drop count alone)
        hit_sharp = max(0.0, min(1.0, sharp + 0.35 * wabs * (0.5 + 0.5 * sharp)))

        # Skip new hits when voice pool is full — never hard-cut a playing voice
        voices_full = (
            len(self._voices) >= self.max_voices
            and len(self._stereo_voices) >= self.max_voices
        )
        if voices_full:
            self._evt_id += 1
            return

        mono = synth_drop(
            sr=self.samplerate, surface=surface, size_mm=size,
            seed=self._evt_id, sharpness=hit_sharp,
        )
        # De-click: minimum fade-in so path/HRTF edges never hard-start
        fade_n = max(8, int(0.003 * self.samplerate))  # ~3 ms
        if len(mono) > fade_n:
            mono = mono.copy()
            mono[:fade_n] *= np.linspace(0.0, 1.0, fade_n, dtype=np.float64)

        # Discrete wet spits are the rain texture; field wash stays under them
        dens_bal = 1.35 / math.sqrt(0.22 + 0.78 * max(0.04, q))
        size_k = 0.60 + 0.55 * min(1.0, size / max(0.5, size_max))
        amp = float(self._rng.uniform(0.70, 1.15)) * self._master * dens_bal * size_k
        # Hollow specials a touch quieter so they don't steal the mix
        if surface in ("tarp", "shell", "plastic", "hollow"):
            amp *= 0.78
        if layer == "far":
            amp *= 0.35
        elif layer == "mid":
            amp *= 0.78
        elif layer == "roof":
            amp *= 0.58 + 0.12 * wabs
        elif layer == "canopy":
            amp *= 0.48 + 0.10 * wabs
        elif layer == "near":
            amp *= 1.15 + 0.18 * wabs
        amp *= 0.68 + 0.28 * sharp
        amp *= 0.75 + 0.40 * wabs
        mono = mono * amp
        self._evt_id += 1
        src = (x, y, z)
        sr = self.samplerate

        # ---- Each speaker = mic in the room relative to the windows ----
        taps: List[Tuple[int, np.ndarray, int]] = []
        if len(self._voices) < self.max_voices:
            for i, spk in enumerate(self.room.speakers):
                if not getattr(spk, "enabled", True):
                    continue
                recv = (float(spk.x), float(spk.y), float(spk.z))
                g_user = _db(float(getattr(spk, "gain_db", 0.0) or 0.0))
                acc = render_drop_to_receiver_mono(
                    self.room, mono, src, recv, sr, gain_scale=g_user
                )
                if float(np.max(np.abs(acc))) < 1e-7:
                    continue
                if schedule_delay_n > 0:
                    acc = np.concatenate([np.zeros(schedule_delay_n), acc])
                taps.append((i, acc, 0))
            if taps:
                self._voices.append(_TapVoice(taps))

        # ---- Listener binaural: each window is a directional source ----
        if self.render_listener_binaural and len(self._stereo_voices) < self.max_voices:
            L = self.room.listener
            recv = (float(L.x), float(getattr(L, "y", 1.2)), float(L.z))
            yaw = float(getattr(L, "yaw", 0.0))
            stereo = render_drop_to_listener_binaural(
                self.room, mono, src, recv, yaw, sr
            )
            if float(np.max(np.abs(stereo))) > 1e-7:
                if schedule_delay_n > 0:
                    pad = np.zeros((schedule_delay_n, 2), dtype=np.float64)
                    stereo = np.vstack([pad, stereo])
                # Stereo onset fade (covers HRTF delay edge cases)
                n0 = min(fade_n, stereo.shape[0])
                if n0 > 1:
                    stereo = stereo.copy()
                    stereo[:n0] *= np.linspace(0.0, 1.0, n0, dtype=np.float64)[:, None]
                self._stereo_voices.append(_StereoVoice(stereo))

    def _mix_speakers(self, frames: int) -> Dict[int, np.ndarray]:
        n_spk = max(1, len(self.room.speakers))
        bufs = {i: np.zeros(frames, dtype=np.float64) for i in range(n_spk)}
        alive: deque[_TapVoice] = deque()
        for v in self._voices:
            new_taps = []
            for ch, buf, pos in v.taps:
                n = min(frames, len(buf) - pos)
                if n > 0:
                    if ch not in bufs:
                        bufs[ch] = np.zeros(frames, dtype=np.float64)
                    bufs[ch][:n] += buf[pos : pos + n]
                    pos += n
                if pos < len(buf):
                    new_taps.append((ch, buf, pos))
            if new_taps:
                v.taps = new_taps
                alive.append(v)
        self._voices = alive
        return bufs

    def _mix_binaural(self, frames: int) -> np.ndarray:
        out = np.zeros((frames, 2), dtype=np.float64)
        alive: deque[_StereoVoice] = deque()
        for v in self._stereo_voices:
            n = min(frames, v.remaining)
            if n > 0:
                out[:n] += v.buf[v.pos : v.pos + n]
                v.pos += n
            if v.remaining > 0:
                alive.append(v)
        self._stereo_voices = alive
        return out

    def _open_avg(self) -> float:
        wins = list(getattr(self.room, "windows", []) or [])
        if not wins:
            return 0.0
        return float(np.mean([float(getattr(w, "open", 0.5) or 0.0) for w in wins]))

    def _sync_reverb_params(self):
        size, damp, wet = room_reverb_from_layout(
            float(getattr(self.room, "width", 5.0)),
            float(getattr(self.room, "depth", 4.0)),
            float(getattr(self.room, "height", 2.6)),
            open_avg=self._open_avg(),
            sr=self.samplerate,
        )
        self._reverb_stereo.set_params(room_size=size, damping=damp, wet=wet)
        for rev in self._reverb_mono.values():
            rev.set_params(room_size=size, damping=damp, wet=wet * 0.85)

    def _mix_outdoor_field(self, frames: int, mix: Dict[int, np.ndarray]) -> None:
        """Add multi-depth outdoor field (procedural ± WAV) through window portals."""
        if not self.use_outdoor_field or frames <= 0:
            return
        quantity = float(getattr(self.room, "droplet_density", 0.5) or 0.0)
        sharp = self._sharpness()
        # Wash under the wet key notes (splats) — not a loud noise blanket.
        # Low quantity: very quiet field so off-tone spits read clearly.
        q = max(0.0, min(1.0, quantity))
        field_level = (0.006 + 0.11 * (q ** 0.95)) * self._master
        layers = self._field.render_layers(
            frames, quantity=quantity, sharpness=sharp, level=field_level
        )
        if float(np.max(np.abs(layers.get("mix", np.zeros(1))))) < 1e-8:
            return

        spk_add, bi_add = self._field_router.process_layers(
            self.room,
            layers,
            n_speakers=max(1, len(self.room.speakers)),
            render_listener=self.render_listener_binaural,
        )
        for i, buf in spk_add.items():
            if i >= len(self.room.speakers):
                continue
            spk = self.room.speakers[i]
            if not getattr(spk, "enabled", True):
                continue
            if i not in mix:
                mix[i] = np.zeros(frames, dtype=np.float64)
            g_user = _db(float(getattr(spk, "gain_db", 0.0) or 0.0))
            mix[i] = mix[i] + buf * g_user

        if bi_add is not None:
            if self._last_binaural is None or self._last_binaural.shape[0] != frames:
                self._last_binaural = np.zeros((frames, 2), dtype=np.float64)
            if bi_add.shape[0] == frames:
                self._last_binaural = self._last_binaural + bi_add

    def _apply_reverb(self, mix: Dict[int, np.ndarray], frames: int) -> Dict[int, np.ndarray]:
        if not self.use_reverb:
            return mix
        self._sync_reverb_params()
        out: Dict[int, np.ndarray] = {}
        for i, buf in mix.items():
            if i not in self._reverb_mono:
                size, damp, wet = room_reverb_from_layout(
                    float(getattr(self.room, "width", 5.0)),
                    float(getattr(self.room, "depth", 4.0)),
                    float(getattr(self.room, "height", 2.6)),
                    open_avg=self._open_avg(),
                    sr=self.samplerate,
                )
                self._reverb_mono[i] = MonoRoomReverb(
                    self.samplerate, room_size=size, damping=damp, wet=wet * 0.85
                )
            y = self._reverb_mono[i].process(buf)
            out[i] = y
        if self._last_binaural is not None and self._last_binaural.shape[0] == frames:
            self._last_binaural = self._reverb_stereo.process(self._last_binaural)
        return out

    def _advance(self, frames: int) -> Dict[int, np.ndarray]:
        """Schedule 3D drops by rate; return per-speaker mono field."""
        sr = self.samplerate
        dt = frames / float(sr)
        self._update_wind(dt)
        ips = self._ips()
        t0 = self._time
        t1 = t0 + dt
        spawned = 0
        # Prefer discrete drops (rain texture); field is only soft underlay
        max_spawn = max(32, int(ips * frames / sr) + 96) if ips > 0 else 0
        while ips > 0 and self._next_event < t1 and spawned < max_spawn:
            if self._next_event >= t0:
                delay = int((self._next_event - t0) * sr)
                self._spawn_event(schedule_delay_n=max(0, delay))
                spawned += 1
            dt = float(self._rng.exponential(1.0 / max(1e-6, ips)))
            self._next_event += max(dt, 1.0 / sr)
        if ips <= 0:
            self._next_event = t1 + 1.0
        self._time = t1

        mix = self._mix_speakers(frames)
        # Stash binaural block for headphone path
        self._last_binaural = self._mix_binaural(frames)

        # Continuous outdoor field through portals (procedural ± WAV samples)
        self._mix_outdoor_field(frames, mix)

        # Soft directional wind whoosh — strength from speed, pan from heading
        wabs = self._wind_speed()
        if wabs > 0.06:
            whoosh = self._wind_whoosh_block(frames, wabs)
            if whoosh is not None and self._last_binaural is not None:
                if self._last_binaural.shape[0] == frames:
                    wx, wz = self._wind_push()
                    # Listener-relative: +right from east component vs yaw
                    yaw = float(getattr(self.room.listener, "yaw", 0.0))
                    # rotate push into listener frame
                    c, s = math.cos(yaw), math.sin(yaw)
                    right = wx * c - wz * s
                    pan = max(-1.0, min(1.0, right))
                    gL = 0.55 - 0.35 * pan
                    gR = 0.55 + 0.35 * pan
                    self._last_binaural[:, 0] += whoosh * gL
                    self._last_binaural[:, 1] += whoosh * gR
            for i, spk in enumerate(self.room.speakers):
                if i not in mix or not getattr(spk, "enabled", True):
                    continue
                open_avg = self._open_avg()
                mix[i] = mix[i] + whoosh * (0.12 * wabs * (0.4 + 0.6 * open_avg))

        # Mild indoor room reverb (size / open windows shape wetness)
        mix = self._apply_reverb(mix, frames)

        # Soft bus makeup — sample-wise soft clip (never hard block peak-norm)
        q = float(getattr(self.room, "droplet_density", 0.5) or 0.0)
        spk_make = 1.45 + 0.15 * q
        bi_make = 1.55 + 0.18 * q
        out = {}
        for i, buf in mix.items():
            out[i] = _soft_clip(buf * spk_make, 0.75)
        if self._last_binaural is not None and self._last_binaural.shape[0] == frames:
            self._last_binaural = _soft_clip(self._last_binaural * bi_make, 0.75)
        return out

    def _wind_whoosh_block(self, frames: int, wind: float) -> np.ndarray:
        """Very soft filtered air noise — wind character, not rain bed."""
        wabs = abs(float(wind))
        if wabs < 0.05 or frames <= 0:
            return np.zeros(max(0, frames), dtype=np.float64)
        # Reuse continuous bed generator at tiny level, extra dark LP
        level = 0.018 * wabs  # stays subtle even at full wind
        raw = self._bed_water.render(frames, intensity=0.6 + 0.4 * wabs, level=level)
        # Band-limit to airy mid whoosh
        from app.audio.engine import _hp1, _lp1
        y = _hp1(raw, 180.0, self.samplerate)
        y = _lp1(y, 900.0 + 700.0 * wabs, self.samplerate)
        # Slow amplitude flutter
        t0 = self._time
        t = t0 + np.arange(frames, dtype=np.float64) / self.samplerate
        am = 0.75 + 0.25 * np.sin(2 * math.pi * (0.15 + 0.4 * wabs) * t + wind)
        return y * am

    def _reset_sim(self, seed: int = 11):
        self._voices = deque()
        self._stereo_voices = deque()
        self._last_binaural = np.zeros((0, 2), dtype=np.float64)
        self._time = 0.0
        self._next_event = 0.0
        self._evt_id = 0
        self._rng = np.random.RandomState(seed)
        self._bed_water = ContinuousRainBed(sr=self.samplerate, seed=4242 + seed)
        self._bed_roof = ContinuousRainBed(sr=self.samplerate, seed=7771 + seed)
        self._field = OutdoorFieldBed(sr=self.samplerate, seed=2024 + seed)
        self._field_router = OutdoorFieldRouter(sr=self.samplerate)
        self._reverb_mono = {}
        self._reverb_stereo = StereoRoomReverb(sr=self.samplerate, wet=0.18)
        self._limiter.reset()
        self._wind_speed_eff = float(getattr(self.room, "wind_speed", abs(getattr(self.room, "wind", 0.0))))
        self._wind_dir_eff = float(getattr(self.room, "wind_direction_deg", 90.0)) % 360.0
        self._wind_speed_target = self._wind_speed_eff
        self._wind_dir_target = self._wind_dir_eff
        self._wind_t_next_dir = 0.0
        self._wind_t_next_speed = 0.0
        self._sync_reverb_params()

    @staticmethod
    def _pan_lr_from_offset(dx: float, dz: float, yaw: float = 0.0):
        """Equal-power + ILD pan from listener-relative offset (−1 left … +1 right)."""
        ang = math.atan2(dx, dz) - float(yaw)
        pan = max(-1.0, min(1.0, math.sin(ang)))
        gL = math.cos((pan + 1.0) * 0.25 * math.pi)
        gR = math.sin((pan + 1.0) * 0.25 * math.pi)
        ild = 7.0 * pan
        gL *= 10.0 ** ((-ild) / 20.0)
        gR *= 10.0 ** ((+ild) / 20.0)
        return gL, gR

    def _device_blocks_from_mix(self, mix: Dict[int, np.ndarray], frames: int) -> Dict[int, np.ndarray]:
        """Build per-device audio from speaker mics.

        If several room speakers share the same OS device, their mono fields
        are **not** summed flat to mono — they are panned in stereo by their
        3D positions so e.g. 3 speakers on one stereo DAC still image left /
        centre / right according to the layout.
        """
        # Group enabled speakers by device index
        groups: Dict[int, List[Tuple[int, Speaker]]] = {}
        for i, spk in enumerate(self.room.speakers):
            di = getattr(spk, "audio_device", None)
            if di is None or not getattr(spk, "enabled", True):
                continue
            groups.setdefault(int(di), []).append((i, spk))

        L = self.room.listener
        yaw = float(getattr(L, "yaw", 0.0))
        out: Dict[int, np.ndarray] = {}

        for di, group in groups.items():
            ch = int(self._device_channels.get(di, 2) or 2)
            ch = max(1, min(2, ch))

            if len(group) == 1 or ch == 1:
                # One speaker (or mono device): send its field dual-mono / mono
                i, spk = group[0]
                mono = mix.get(i)
                if mono is None:
                    mono = np.zeros(frames, dtype=np.float64)
                elif len(mono) < frames:
                    pad = np.zeros(frames, dtype=np.float64)
                    pad[: len(mono)] = mono
                    mono = pad
                else:
                    mono = mono[:frames]
                g = _db(float(getattr(spk, "gain_db", 0.0) or 0.0))
                mono = mono * g
                if ch == 1:
                    out[di] = self._apply_master(mono).astype(np.float32)
                else:
                    stereo = np.stack([mono, mono], axis=1)
                    out[di] = self._apply_master(stereo).astype(np.float32)
                continue

            # --- Multiple speakers on one stereo device → 3D spatial mix ---
            left = np.zeros(frames, dtype=np.float64)
            right = np.zeros(frames, dtype=np.float64)
            n_sp = len(group)
            # Spread: if speakers cluster, still use absolute positions vs listener
            for i, spk in group:
                mono = mix.get(i)
                if mono is None:
                    continue
                if len(mono) < frames:
                    pad = np.zeros(frames, dtype=np.float64)
                    pad[: len(mono)] = mono
                    mono = pad
                else:
                    mono = mono[:frames]
                dx = float(spk.x) - float(L.x)
                dz = float(spk.z) - float(L.z)
                dist = max(0.35, math.hypot(dx, dz))
                att = 1.0 / (1.0 + 0.3 * (dist - 0.35))
                g_user = _db(float(getattr(spk, "gain_db", 0.0) or 0.0))
                gL, gR = self._pan_lr_from_offset(dx, dz, yaw)
                scale = att * g_user / math.sqrt(n_sp)
                left += mono * gL * scale
                right += mono * gR * scale

            stereo = np.stack([left, right], axis=1)
            out[di] = self._apply_master(stereo * 1.05).astype(np.float32)

        return out

    def _mixer_loop(self):
        """Realtime producer: advance sim once, push speaker buses + optional You bus."""
        block = self.blocksize
        period = block / self.samplerate
        next_t = time.perf_counter()
        want_spk = bool(self._queues)
        want_you = bool(getattr(self, "_include_you", False) or self._mode == "headphones")
        while not self._stop_flag.is_set():
            try:
                with self._lock:
                    mix = self._advance(block)
                    dev_blocks = self._device_blocks_from_mix(mix, block) if want_spk else {}
                    bi = getattr(self, "_last_binaural", None)
                    if want_you:
                        if bi is None or bi.shape[0] != block:
                            bi = np.zeros((block, 2), dtype=np.float64)
                        you_block = self._apply_master(bi).astype(np.float32)
                    else:
                        you_block = None

                if want_spk:
                    for di, q in self._queues.items():
                        ch = int(self._device_channels.get(di, 2) or 2)
                        ch = max(1, min(2, ch))
                        data = dev_blocks.get(di)
                        if data is None:
                            data = np.zeros((block, ch), dtype=np.float32)
                        if q.qsize() < 10:
                            try:
                                q.put_nowait(data)
                            except queue.Full:
                                pass

                if want_you and you_block is not None and self._hp_queue is not None:
                    if self._hp_queue.qsize() < 12:
                        try:
                            self._hp_queue.put_nowait(you_block)
                        except queue.Full:
                            pass

                next_t += period
                sleep = next_t - time.perf_counter()
                if sleep > 0:
                    time.sleep(sleep)
                else:
                    next_t = time.perf_counter()
            except Exception:
                log.exception("mixer loop error")
                time.sleep(0.01)

    def _open_you_stream(self, device_index: Optional[int] = None):
        """Open binaural output for You (default device if None)."""
        block = int(self.blocksize)
        self._hp_queue = queue.Queue(maxsize=24)
        self._hp_last = np.zeros((block, 2), dtype=np.float32)
        # Prefill silence so callback never starves before mixer runs
        silent = np.zeros((block, 2), dtype=np.float32)
        for _ in range(6):
            try:
                self._hp_queue.put_nowait(silent.copy())
            except queue.Full:
                break

        def cb(outdata, frames, time_info, status):
            try:
                if status:
                    log.warning("You stream status: %s", status)
                q = self._hp_queue
                if q is None:
                    outdata[:] = 0
                    return
                try:
                    block_data = q.get_nowait()
                except queue.Empty:
                    if self._hp_last.shape[0] > 0:
                        outdata[:] = self._hp_last[-1]
                    else:
                        outdata[:] = 0
                    return
                b = np.asarray(block_data, dtype=np.float32)
                if b.ndim == 1:
                    b = np.stack([b, b], axis=1)
                if b.shape[0] == frames and b.shape[1] >= 2:
                    outdata[:] = b[:, :2]
                elif b.shape[0] >= frames:
                    outdata[:] = b[:frames, :2]
                else:
                    outdata[: b.shape[0]] = b[:, :2]
                    outdata[b.shape[0] :] = b[-1] if b.shape[0] else 0
                self._hp_last = np.array(outdata, copy=True)
            except Exception:
                log.exception("You stream callback")
                outdata[:] = 0

        kwargs = dict(
            samplerate=self.samplerate,
            channels=2,
            blocksize=block,
            dtype="float32",
            callback=cb,
            device=int(device_index) if device_index is not None else None,
        )
        try:
            self._hp_stream = sd.OutputStream(latency="high", **kwargs)
        except TypeError:
            self._hp_stream = sd.OutputStream(**kwargs)
        self._hp_stream.start()
        log.info("You (binaural) stream on device %s", device_index)

    # ----- lifecycle -----
    def start(self, include_you: bool = False, headphones_device: Optional[int] = None):
        """Start multi-device speaker rain (optionally also You on headphones).

        include_you=True feeds binaural to headphones_device (or OS default)
        in the *same* mixer as the mapped speakers — so rain is not stuck on HP only.
        """
        if self.running:
            return
        if sd is None:
            raise RuntimeError("sounddevice not installed")

        dev_map: Dict[int, List[Speaker]] = {}
        for spk in self.room.speakers:
            di = getattr(spk, "audio_device", None)
            if di is None or not spk.enabled:
                continue
            dev_map.setdefault(int(di), []).append(spk)
        if not dev_map and not include_you:
            raise RuntimeError(
                "No speakers assigned to output devices.\n\n"
                "1) Simulate → Place 3 speakers evenly\n"
                "2) Speakers step → assign a real OS output to each\n"
                "3) Play mapped speakers (or Play You + speakers)"
            )

        self.stop_all()
        block = max(1024, int(self.blocksize))
        self.blocksize = block
        self._reset_sim(11)
        self._limiter.reset()
        self._stop_flag.clear()
        self._include_you = bool(include_you)
        self._queues = {}
        self._device_channels = {}
        self._buses = {}

        info = {d["index"]: d for d in self.refresh_devices()}
        failed = []
        for di, group in dev_map.items():
            ch = int(info.get(di, {}).get("channels", 2) or 2)
            if len(group) > 1:
                ch = max(2, ch)
            ch = max(1, min(2, ch))
            self._device_channels[di] = ch
            self._queues[di] = queue.Queue(maxsize=16)
            try:
                bus = _DeviceBus(di, self.samplerate, self.blocksize, ch, self._queues[di])
                bus.start()
                self._buses[di] = bus
                names = ", ".join(s.name for s in group)
                log.info("Speaker bus device %s ← %s", di, names)
            except Exception as e:
                failed.append(f"device {di}: {e}")
                log.exception("Could not open speaker device %s", di)
                self._queues.pop(di, None)

        if not self._buses and not include_you:
            raise RuntimeError(
                "Could not open any speaker devices:\n" + "\n".join(failed or ["unknown"])
            )

        if include_you or not self._buses:
            # Always allow You when requested, or as last-resort if speakers failed
            self._include_you = True
            self._open_you_stream(headphones_device)

        self._mixer_thread = threading.Thread(target=self._mixer_loop, name="RainMixer", daemon=True)
        self._mixer_thread.start()
        self.running = True
        if self._buses and self._include_you:
            self._mode = "all"
        elif self._buses:
            self._mode = "multi"
        else:
            self._mode = "headphones"
        log.info(
            "Playback started mode=%s speakers=%s you=%s",
            self._mode, list(self._buses.keys()), self._include_you,
        )

    def stop(self):
        self._stop_flag.set()
        if self._mixer_thread is not None:
            self._mixer_thread.join(timeout=1.5)
            self._mixer_thread = None
        for b in list(self._buses.values()):
            b.stop()
        self._buses.clear()
        self._queues.clear()
        if self._hp_stream is not None:
            try:
                self._hp_stream.stop()
                self._hp_stream.close()
            except Exception:
                pass
            self._hp_stream = None
        self._hp_queue = None
        self.running = False
        self._mode = "stopped"
        self._include_you = False
        with self._lock:
            self._voices = deque()
            self._stereo_voices = deque()
        self._limiter.reset()

    def start_headphones(self, headphones_device: Optional[int] = None):
        """Play rain from You only (binaural) on default or chosen device."""
        if self.running:
            return
        if sd is None:
            raise RuntimeError("sounddevice not installed")
        self.stop_all()
        self.blocksize = max(2048, int(self.blocksize))
        self._reset_sim(3)
        self._limiter.reset()
        self._stop_flag.clear()
        self._include_you = True
        self._queues = {}
        self._device_channels = {}
        self._buses = {}
        self._open_you_stream(headphones_device)
        self._mixer_thread = threading.Thread(target=self._mixer_loop, name="RainHP", daemon=True)
        self._mixer_thread.start()
        self.running = True
        self._mode = "headphones"
        log.info("Headphones-only (You) started device=%s", headphones_device)

    def stop_headphones(self):
        """Back-compat alias — full stop."""
        self.stop()

    def stop_all(self):
        self.stop()

    # ----- test tone -----
    def play_test_tone(
        self,
        device_index: Optional[int] = None,
        frequency: float = 880.0,
        seconds: float = 0.55,
        gain: float = 0.22,
    ):
        """Play a short tone. device_index=None uses the OS default output."""
        if sd is None:
            raise RuntimeError("sounddevice not installed")
        was_running = self.running
        mode = self._mode
        hp_dev = None
        try:
            hp_dev = getattr(self.room.listener, "audio_device", None)
        except Exception:
            pass
        if was_running:
            self.stop_all()
        sr = self.samplerate
        n = int(sr * seconds)
        t = np.arange(n, dtype=np.float64) / sr
        env = np.ones(n)
        mid = n // 2
        gap = int(0.04 * sr)
        env[mid - gap : mid + gap] = 0.0
        fade = int(0.01 * sr)
        env[:fade] *= np.linspace(0, 1, fade)
        env[-fade:] *= np.linspace(1, 0, fade)
        sig = (gain * np.sin(2 * math.pi * frequency * t) * env).astype(np.float32)
        stereo = np.stack([sig, sig], axis=1)
        try:
            # sounddevice: device=None → system default
            sd.play(stereo, sr, device=device_index, blocking=True)
        finally:
            if was_running:
                if mode == "multi":
                    self.start(include_you=False)
                elif mode == "headphones":
                    self.start_headphones(headphones_device=hp_dev)
                elif mode == "all":
                    self.start(include_you=True, headphones_device=hp_dev)

    def play_speaker_test(self, speaker: Speaker, index_hint: int = 0):
        if speaker.audio_device is None:
            raise RuntimeError(f"Speaker '{speaker.name}' has no output device assigned")
        freq = 520.0 + 70.0 * (index_hint % 8)
        self.play_test_tone(int(speaker.audio_device), frequency=freq)

    # ----- offline preview -----
    def render_offline_stereo(self, seconds: float = 4.0) -> np.ndarray:
        """Listener-perspective binaural of the 3D outdoor rain field."""
        n = int(self.samplerate * seconds)
        out = np.zeros((n, 2), dtype=np.float64)
        self._reset_sim(42)
        pos = 0
        block = self.blocksize
        while pos < n:
            frames = min(block, n - pos)
            self._advance(frames)
            bi = getattr(self, "_last_binaural", None)
            if bi is not None and bi.shape[0] == frames:
                out[pos : pos + frames] += bi
            pos += frames
        return self._apply_master(out).astype(np.float64)

    def _emit_block(self, n: int):
        """Legacy stereo block = listener binaural."""
        frames = int(n)
        self._advance(frames)
        bi = getattr(self, "_last_binaural", None)
        if bi is None or bi.shape[0] != frames:
            bi = np.zeros((frames, 2), dtype=np.float64)
        return self._apply_master(bi).astype(np.float32)
