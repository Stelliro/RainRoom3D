
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

from app.audio.drop_bank import DropSampleBank
from app.audio.engine import synth_drop, _db, _peak_cap
from app.audio.field_bed import OutdoorFieldBed
from app.audio.field_router import OutdoorFieldRouter
from app.audio.reverb import MonoRoomReverb, StereoRoomReverb, room_reverb_from_layout
from app.audio.spatial import (
    C_SOUND,
    binaural,
    distance_attenuation,
    render_drop_to_receiver_mono,
    render_drop_to_receivers,
)
from app.audio.surround import (
    WALL_BUS,
    channel_candidates,
    clamp_device_channels,
    mix_to_surround,
    speaker_test_channel_gains,
    speaker_test_pan_label,
)
from app.audio.wind import WindAirSynth
from app.models.room import Room, Speaker

log = logging.getLogger("audio.spatial")

# Hard caps: keep CPU / PortAudio happy at high quantity.
# Downpour needs a *sheet of ticks* (white-noise-without-white-noise), not 80 sparse hits.
_MAX_VOICE_EVENTS_PER_SEC = 170.0
_MAX_SPAWN_PER_BLOCK = 36
_DEFAULT_MAX_VOICES = 300
_MAX_STEREO_VOICES = 180


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
    """Normalize PortAudio device names so the same physical output collapses to one key."""
    import re
    n = " ".join(str(name or "?").lower().split())
    # Host-API tags PortAudio often appends (space or parentheses)
    n = re.sub(
        r"[\s\-]*(?:\()?windows\s+(?:wasapi|directsound|mme|wdm\-?ks)(?:\))?$",
        "",
        n,
    )
    for junk in (
        " (wasapi)",
        " (directsound)",
        " (mme)",
        " (windows wdm-ks)",
        " (wdm-ks)",
        " (windows directsound)",
        " (windows mme)",
        " (windows wasapi)",
        " windows wasapi",
        " windows directsound",
        " windows mme",
        " - wasapi",
        " - directsound",
        " - mme",
    ):
        n = n.replace(junk, "")
    # Mapper / primary aliases
    n = n.replace("primary sound driver", "primary")
    n = n.replace("microsoft sound mapper", "mapper")
    n = n.replace("sound mapper", "mapper")
    # Normalize Realtek-style noise: realtek(r) → realtek
    n = n.replace("(r)", "").replace("®", "")
    n = re.sub(r"\s+", " ", n).strip(" -")
    return n


def _is_virtual_mapper_name(name: str) -> bool:
    n = _norm_device_name(name)
    if n in ("primary", "mapper", "?", ""):
        return True
    if "mapper" in n and "sound" in n:
        return True
    if n.startswith("primary "):
        return True
    return False


def list_output_devices() -> List[dict]:
    """List unique physical outputs (dedupe MME/DS/WASAPI copies of the same device).

    Windows PortAudio often exposes each endpoint 3× (MME + DirectSound + WASAPI).
    Default: keep modern APIs only (WASAPI), then one entry per normalized name.
    Set env RAINROOM_ALL_AUDIO_APIS=1 to list every host API again.
    """
    if sd is None:
        return []
    try:
        import os
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
            name = str(d.get("name", "?") or "?")
            if _is_virtual_mapper_name(name):
                continue
            hai = d.get("hostapi")
            score = _hostapi_score(hai, hostapis)
            if default_out is not None and int(default_out) == i:
                score += 50
            score += min(ch, 8)
            raw.append({
                "index": i,
                "name": name,
                "hostapi": hai,
                "hostapi_name": (
                    hostapis[int(hai)].get("name", "?") if hai is not None and int(hai) < len(hostapis) else "?"
                ),
                "channels": ch,
                "default_sr": d.get("default_samplerate", 48000),
                "is_default": (default_out is not None and int(default_out) == i),
                "_score": score,
                "_key": _norm_device_name(name),
            })

        if not raw:
            return []

        # Prefer modern APIs only (WASAPI / CoreAudio / Pulse) unless user opts out
        prefer_modern = os.environ.get("RAINROOM_ALL_AUDIO_APIS", "0") != "1"
        modern = [d for d in raw if d["_score"] >= 95]  # WASAPI=100, pulse=95
        if prefer_modern and modern:
            # If default is only on a legacy API, still keep modern list (open by index)
            raw = modern

        # Keep best entry per normalized name (highest score wins)
        best: Dict[str, dict] = {}
        for d in raw:
            k = d["_key"]
            prev = best.get(k)
            if prev is None or d["_score"] > prev["_score"]:
                best[k] = d

        # Second pass: drop near-duplicates where keys only differ by leading
        # "2- " / "3- " PortAudio multi-adapter prefixes
        import re
        def _stem(k: str) -> str:
            return re.sub(r"^\d+\-\s*", "", k).strip()

        by_stem: Dict[str, dict] = {}
        for d in best.values():
            stem = _stem(d["_key"])
            prev = by_stem.get(stem)
            if prev is None or d["_score"] > prev["_score"]:
                by_stem[stem] = d

        outs = sorted(
            by_stem.values(),
            key=lambda d: (-int(d.get("is_default")), d["name"].lower(), d["index"]),
        )
        for d in outs:
            d.pop("_score", None)
            d.pop("_key", None)
        log.info(
            "Output devices: %d unique (from PortAudio; modern_api_only=%s)",
            len(outs),
            prefer_modern,
        )
        return outs
    except Exception as e:
        log.exception("device query failed: %s", e)
        return []


def _device_default_samplerate(device_index: Optional[int], fallback: int = 48000) -> int:
    """Native / default sample rate reported by PortAudio for an output device."""
    if sd is None:
        return int(fallback)
    try:
        if device_index is None:
            info = sd.query_devices(kind="output")
        else:
            info = sd.query_devices(int(device_index))
        sr = float(info.get("default_samplerate", fallback) or fallback)
        return int(round(sr)) if sr >= 8000 else int(fallback)
    except Exception:
        return int(fallback)


def _sample_rate_candidates(preferred: int, device_index: Optional[int]) -> List[int]:
    """Rates to try when opening a stream (engine rate first, then device default, then common)."""
    cands: List[int] = []
    for r in (
        int(preferred),
        _device_default_samplerate(device_index, preferred),
        48000,
        44100,
        96000,
        88200,
        64000,
        32000,
        22050,
    ):
        ri = int(round(r))
        if ri >= 8000 and ri not in cands:
            cands.append(ri)
    return cands


def _resample_audio(x: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Linear resample mono or (N, C) audio between sample rates."""
    src_sr = int(src_sr)
    dst_sr = int(dst_sr)
    if src_sr <= 0 or dst_sr <= 0 or src_sr == dst_sr:
        return np.asarray(x, dtype=np.float32)
    x = np.asarray(x, dtype=np.float32)
    squeeze = False
    if x.ndim == 1:
        x = x.reshape(-1, 1)
        squeeze = True
    n_src = x.shape[0]
    if n_src == 0:
        return x[:, 0] if squeeze else x
    n_dst = max(1, int(round(n_src * float(dst_sr) / float(src_sr))))
    t_src = np.linspace(0.0, 1.0, n_src, endpoint=False)
    t_dst = np.linspace(0.0, 1.0, n_dst, endpoint=False)
    out = np.empty((n_dst, x.shape[1]), dtype=np.float32)
    for c in range(x.shape[1]):
        out[:, c] = np.interp(t_dst, t_src, x[:, c]).astype(np.float32)
    return out[:, 0] if squeeze else out


def _open_output_stream(
    *,
    device_index: Optional[int],
    channels: int,
    preferred_sr: int,
    preferred_blocksize: int,
    callback,
):
    """Open an OutputStream, trying channel counts then sample rates.

    Returns (stream, actual_sr, actual_blocksize, actual_channels).
    Surround (6/8 ch) is tried when requested; stereo is the fallback so a
    5.1 card that rejects the full layout still plays (just flatter).
    Does **not** fall back to a different device.
    """
    if sd is None:
        raise RuntimeError("sounddevice is not available")
    last_err: Optional[BaseException] = None
    ch_list = channel_candidates(channels)
    for ch in ch_list:
        for sr in _sample_rate_candidates(preferred_sr, device_index):
            bs = max(64, int(round(preferred_blocksize * float(sr) / max(1, preferred_sr))))
            kwargs = dict(
                device=int(device_index) if device_index is not None else None,
                channels=int(ch),
                samplerate=int(sr),
                blocksize=int(bs),
                dtype="float32",
                callback=callback,
            )
            try:
                try:
                    stream = sd.OutputStream(latency="high", **kwargs)
                except TypeError:
                    stream = sd.OutputStream(**kwargs)
                stream.start()
                if sr != preferred_sr or ch != int(channels):
                    log.info(
                        "Device %s opened at %s Hz / %s ch (wanted %s Hz / %s ch)",
                        device_index,
                        sr,
                        ch,
                        preferred_sr,
                        channels,
                    )
                return stream, int(sr), int(bs), int(ch)
            except Exception as e:
                last_err = e
                log.warning(
                    "Open device %s at %s Hz / %s ch failed: %s",
                    device_index, sr, ch, e,
                )
    raise RuntimeError(
        f"Failed to open output device {device_index} "
        f"(tried ch={ch_list}, sr={_sample_rate_candidates(preferred_sr, device_index)})"
    ) from last_err


def _material_surface(name: Optional[str]) -> str:
    if not name:
        return "water"
    low = str(name).lower()
    mapping = (
        ("glass", "glass"),
        ("window", "glass"),
        ("tin", "metal"),
        ("metal", "metal"),
        ("shingle", "shingle"),
        ("asphalt", "shingle"),
        ("wood", "wood"),
        ("tile", "tile"),
        ("brick", "brick"),
        ("water", "water"),
        ("puddle", "water"),
    )
    for key, surf in mapping:
        if key in low:
            return surf
    if "roof" in low:
        return "metal"
    return "metal"


def _wall_tone(name: Optional[str]) -> float:
    """0..1 subtle brown-wash colour from house wall material (not a big EQ jump).

    Lower = darker/heavier brown (brick, shingle). Higher = slightly more open
    (glass, metal). Default mid.
    """
    if not name:
        return 0.45
    low = str(name).lower()
    if "brick" in low:
        return 0.22
    if "shingle" in low:
        return 0.30
    if "wood" in low:
        return 0.38
    if "tile" in low:
        return 0.42
    if "glass" in low or "window" in low:
        return 0.72
    if "tin" in low or "metal" in low:
        return 0.58
    return 0.45


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

    __slots__ = ("taps", "kind")  # taps: (ch_index, mono_buf, pos); kind 1 yard / 2 window / 3 roof

    def __init__(self, taps: List[Tuple[int, np.ndarray, int]], kind: int = 3):
        self.taps = taps
        self.kind = int(kind)

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
    """Output bus for one OS device. Queue items are (frames, channels) float32 at stream rate."""

    def __init__(self, device_index: int, samplerate: int, blocksize: int, channels: int, q: queue.Queue):
        self.device_index = device_index
        self.engine_sr = int(samplerate)
        self.samplerate = int(samplerate)  # actual stream rate after start()
        self.engine_blocksize = int(blocksize)
        self.blocksize = int(blocksize)
        self.channels = clamp_device_channels(channels)
        self.q = q
        self.stream = None
        # leftover stereo/mono frames from previous callback
        self._carry = np.zeros((0, self.channels), dtype=np.float32)

    def start(self):
        self.stream, self.samplerate, self.blocksize, opened_ch = _open_output_stream(
            device_index=self.device_index,
            channels=self.channels,
            preferred_sr=self.engine_sr,
            preferred_blocksize=self.engine_blocksize,
            callback=self._cb,
        )
        self.channels = int(opened_ch)
        self._carry = np.zeros((0, self.channels), dtype=np.float32)
        log.info(
            "Output bus started: device=%s channels=%s sr=%s block=%s",
            self.device_index,
            self.channels,
            self.samplerate,
            self.blocksize,
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

    def __init__(
        self,
        room: Room,
        samplerate: int = 48000,
        blocksize: int = 2048,
        max_voices: int = _DEFAULT_MAX_VOICES,
    ):
        self.room = room
        self.samplerate = int(samplerate)
        # Larger blocks = fewer callbacks + more CPU margin (less underrun crackle)
        self.blocksize = int(blocksize)
        self.max_voices = int(max_voices)
        self._voices: deque[_TapVoice] = deque()
        self._stereo_voices: deque[_StereoVoice] = deque()
        self._hit_lock = threading.Lock()
        self._pending_hits: List[Tuple[float, float, float, int]] = []
        self._visual_seen_t = 0.0
        self._time = 0.0
        self._next_event = 0.0
        self._evt_id = 0
        self._rng = np.random.RandomState(11)
        self._buses: Dict[int, _DeviceBus] = {}
        self._queues: Dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self.running = False
        self._devices_cache = list_output_devices()
        # Pre-baked hit grains + multi-hit chains (built on first play)
        self._drop_bank = DropSampleBank(samplerate=self.samplerate, seed=2026)
        self.use_drop_bank = True
        # Internal synth level (before user volume).
        self._master = 0.85
        # Comfortable at vol ~75–85% without riding the limiter (crackle source)
        self._output_fs_gain = 3.2
        self._output_ceiling = 0.88
        self._limiter = _SmoothLimiter(ceiling=0.88, release_s=0.14, attack_s=0.002)
        self._mixer_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._hp_stream = None
        self._hp_queue: Optional[queue.Queue] = None
        self._hp_stream_sr = int(samplerate)
        self._hp_carry = np.zeros((0, 2), dtype=np.float32)
        self._hp_last = np.zeros((1, 2), dtype=np.float32)
        self._mode = "stopped"  # multi | headphones
        self.use_noise_bed = False
        # Outdoor wind / air (layered gusts — not pink static)
        self._wind_air = WindAirSynth(sr=self.samplerate, seed=4242)
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
        self._last_wall: Dict[str, np.ndarray] = {}

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

    def submit_visual_impacts(self, hits) -> None:
        """OpenGL droplets that just landed. Each hit is (x, y, z, kind).

        kind: 1 yard, 2 window, 3 roof. Called from the UI thread; mixed later.
        """
        if not hits:
            return
        with self._hit_lock:
            self._pending_hits.extend(hits)
            self._visual_seen_t = time.perf_counter()

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
        """*Perceived* discrete droplet impacts per second (Quantity).

        This is how dense the rain *feels*. Actual spatialized voices are
        lower — see ``_chain_hits`` / ``_voice_ips`` (multi-hit chains).
        """
        quantity = float(getattr(self.room, "droplet_density", 0.5))
        sh = self._sharpness()
        wabs = self._wind_speed()
        if quantity <= 0.0005:
            return 0.0
        q = max(0.0, min(1.0, quantity))
        # Fallback when OpenGL isn't driving hits (floor-plan / headphones-only).
        # q=0.08 → ~14, q=0.22 → ~40, q=1 → ~160
        soft_boost = 1.04 - 0.06 * sh
        body = 148.0 * (q ** 1.05)
        floor = 9.0 + 4.0 * q
        wind_boost = 1.0 + 0.10 * wabs
        return float(min(_MAX_VOICE_EVENTS_PER_SEC, (floor + body) * soft_boost * wind_boost))

    def _chain_hits(self) -> int:
        """1 = single drop. 2 = tight double-tap at downpour only."""
        q = max(0.0, min(1.0, float(getattr(self.room, "droplet_density", 0.5) or 0.0)))
        return 2 if q >= 0.78 else 1

    def _voice_ips(self) -> float:
        perc = self._ips()
        if perc <= 0.0:
            return 0.0
        ch = float(self._chain_hits())
        return float(min(_MAX_VOICE_EVENTS_PER_SEC, max(6.0, perc / ch)))

    def _droplet_playback_rate(self, quantity: float) -> float:
        """Subtle speed-up of each droplet grain as quantity rises.

        ~1.00 at q=0, ~1.04 at mid, ~1.09 at full — noticeable, not cartoon.
        """
        q = max(0.0, min(1.0, float(quantity)))
        return 1.0 + 0.09 * (q ** 0.85)

    def _ring_point(self, out_dist: float) -> Tuple[float, float, float, float, str]:
        """Uniform sample along the perimeter of a rectangle expanded by out_dist.

        Equal rain per metre of facade (N/S get more hits on a wide house).
        Returns (x, y, z, depth, wall).
        """
        rng = self._rng
        rw = max(0.5, float(self.room.width))
        rd = max(0.5, float(self.room.depth))
        d = max(0.25, float(out_dist))
        pw = rw + 2.0 * d
        pd = rd + 2.0 * d
        per = 2.0 * (pw + pd)
        t = float(rng.uniform(0.0, per))
        if t < pw:
            return float(t - d), 0.02, rd + d, d, "north"
        t -= pw
        if t < pd:
            return rw + d, 0.02, float(t - d), d, "east"
        t -= pd
        if t < pw:
            return float(rw + d - t), 0.02, -d, d, "south"
        t -= pw
        return -d, 0.02, float(rd + d - t), d, "west"

    def _pick_source_3d(self) -> Tuple[str, float, float, float, float]:
        """Outdoor impacts on the room footprint plus a thin eaves ring.

        Quantity is rain *on the house*, not a 40 m field. A little overshoot
        (gutters / drip line / window lip) keeps the four facades fed.

        Returns (layer, x, y, z, depth_m_from_house).
        """
        r = self.room
        rng = self._rng
        wx, wz = self._wind_push()
        windows = list(getattr(r, "windows", []) or [])
        rw = max(0.5, float(r.width))
        rd = max(0.5, float(r.depth))
        rh = max(1.5, float(r.height))
        q_amt = max(0.0, min(1.0, float(getattr(r, "droplet_density", 0.5) or 0.0)))
        roof_mat = str(getattr(r, "roof_material", "") or "").lower()
        tin = ("tin" in roof_mat) or ("metal" in roof_mat)
        p_roof = (0.82 + 0.06 * q_amt) if tin else (0.70 + 0.08 * q_amt)
        p_wall = 0.06
        p_near = 0.08 if windows else 0.0

        def _along_window(win, out_dist: float):
            wall = (getattr(win, "wall", "north") or "north").lower()
            cx, cy, cz = r.window_center(win)
            lat = float(rng.uniform(-0.55, 0.55)) * float(getattr(win, "width", 1.0))
            vert = float(rng.normal(0.0, 0.35))
            y = max(0.0, cy + vert)
            if wall == "north":
                return cx + lat, y, cz + out_dist
            if wall == "south":
                return cx + lat, y, cz - out_dist
            if wall == "east":
                return cx + out_dist, y, cz + lat
            return cx - out_dist, y, cz + lat

        u = float(rng.rand())
        t_wall = p_roof + p_wall
        t_near = t_wall + p_near
        if u < p_roof:
            layer = "roof"
            x = float(rng.uniform(0.05, max(0.15, rw - 0.05)))
            z = float(rng.uniform(0.05, max(0.15, rd - 0.05)))
            y = rh + 0.03
            depth = 0.0
        elif u < t_wall:
            layer = "wall"
            x, _y, z, depth, _w = self._ring_point(float(rng.uniform(0.05, 0.45)))
            y = float(rng.uniform(0.15, rh * 0.92))
        elif windows and u < t_near:
            layer = "near"
            weights = np.array(
                [max(0.05, float(getattr(w, "open", 0.7))) for w in windows],
                dtype=np.float64,
            )
            weights /= weights.sum()
            win = windows[int(rng.choice(len(windows), p=weights))]
            depth = float(rng.uniform(0.12, 0.70))
            x, y, z = _along_window(win, depth)
            y = float(rng.uniform(0.0, max(0.15, getattr(win, "sill", 0.9) * 0.45)))
        else:
            layer = "yard"
            depth = float(rng.uniform(0.12, 0.85))
            x, y, z, depth, _w = self._ring_point(depth)

        # Wind is a drift, not a spawn filter — keep it small so hits stay on the house
        x += wx * float(rng.uniform(0.02, 0.18)) * (0.25 + 0.04 * depth)
        z += wz * float(rng.uniform(0.02, 0.18)) * (0.25 + 0.04 * depth)
        return layer, float(x), float(y), float(z), float(depth)

    def _surface_for_layer(self, layer: str) -> str:
        if layer == "roof":
            return _material_surface(getattr(self.room, "roof_material", "Metal Roof"))
        if layer == "near":
            return "glass"
        if layer == "wall":
            base = _material_surface(getattr(self.room, "wall_material", "Brick Wall"))
            if base in ("metal", "glass"):
                return "brick"
            return base if base in ("wood", "brick", "tile", "shingle") else "brick"
        # Yard / mid / far / canopy: wet water mass; rare hollow accents
        u = float(self._rng.rand())
        if layer == "canopy" and u < 0.008:
            return "shell"
        if layer == "yard" and u < 0.04:
            return "wood"  # occasional deck / cover
        return "water"

    def _speaker_receive(
        self,
        mono: np.ndarray,
        src: Tuple[float, float, float],
        spk: Speaker,
        sr: int,
        gain_scale: float,
    ) -> np.ndarray:
        """Mic field at a speaker; wide units sample along their width (soundbar)."""
        cx, cy, cz = float(spk.x), float(spk.y), float(spk.z)
        if hasattr(spk, "box_dims"):
            bw, bh, bd = spk.box_dims()
        else:
            s = float(getattr(spk, "size", 0.32) or 0.32)
            bw, bh, bd = s, s, min(s, 0.22)
        # Horizontal range = max width/depth; tall thin boxes stay near point-source
        span = max(bw, bd)
        busy = (
            len(self._voices) > self.max_voices * 0.45
            or self._chain_hits() >= 2
        )
        n = 1 if (span < 0.28 or busy) else min(3, max(2, int(round(span / 0.35))))
        if n <= 1:
            return render_drop_to_receiver_mono(
                self.room, mono, src, (cx, cy, cz), sr, gain_scale=gain_scale
            )
        # Sample along width (X) if width is the long axis, else along Z
        along_x = bw >= bd
        parts = []
        max_len = 0
        for i in range(n):
            t = (i / (n - 1) - 0.5)  # -0.5 .. +0.5
            if along_x:
                px, py, pz = cx + t * bw, cy, cz
            else:
                px, py, pz = cx, cy, cz + t * bd
            part = render_drop_to_receiver_mono(
                self.room, mono, src, (px, py, pz), sr, gain_scale=gain_scale
            )
            parts.append(part)
            max_len = max(max_len, len(part))
        if max_len <= 0:
            return np.zeros(0, dtype=np.float64)
        acc = np.zeros(max_len, dtype=np.float64)
        for part in parts:
            if len(part) < max_len:
                padded = np.zeros(max_len, dtype=np.float64)
                padded[: len(part)] = part
                acc += padded
            else:
                acc += part
        return acc * (1.0 / float(n))

    def _evict_voice(self) -> None:
        """Drop a voice to make room. Prefer yard/window so roof ticks stay."""
        n = len(self._voices)
        if n <= 0:
            return
        for _ in range(n):
            v = self._voices.popleft()
            if getattr(v, "kind", 3) != 3:
                return
            self._voices.append(v)
        if self._voices:
            self._voices.popleft()

    def _short_grain(
        self, mono: np.ndarray, ms: float = 42.0, fade_ms: float = 8.0
    ) -> np.ndarray:
        """Keep the attack. Tiny edge fade only — a long linear dump is a thud falloff."""
        sr = self.samplerate
        g = np.asarray(mono, dtype=np.float64).reshape(-1)
        n = min(len(g), max(48, int(sr * ms / 1000.0)))
        g = np.array(g[:n], copy=True)
        fade = min(n // 8, max(4, int(sr * max(0.0015, min(0.012, fade_ms / 1000.0)))))
        if fade > 3:
            g[-fade:] *= np.linspace(1.0, 0.0, fade)
        return g

    def _portal_gain_cheap(self, sx: float, sy: float, sz: float, kind: int) -> float:
        """Open-window / roof couple without per-drop filter banks."""
        if kind == 3:
            leak = 0.70
        else:
            leak = 0.05
        best = leak
        for w in getattr(self.room, "windows", []) or []:
            o = max(0.0, min(1.0, float(getattr(w, "open", 0.7) or 0.0)))
            if o <= 0.02:
                continue
            try:
                cx, cy, cz = self.room.window_center(w)
            except Exception:
                continue
            d = math.sqrt((sx - cx) ** 2 + (sy - cy) ** 2 + (sz - cz) ** 2)
            g = (0.12 + 1.05 * o) / (1.0 + 0.18 * d)
            if g > best:
                best = g
        return float(best)

    def _cheap_taps_for_hit(
        self, mono: np.ndarray, src: Tuple[float, float, float], kind: int
    ) -> List[Tuple[int, np.ndarray, int]]:
        sx, sy, sz = src
        couple = self._portal_gain_cheap(sx, sy, sz, kind)
        sr = self.samplerate
        taps: List[Tuple[int, np.ndarray, int]] = []
        for i, spk in enumerate(self.room.speakers):
            if not getattr(spk, "enabled", True):
                continue
            dx = float(spk.x) - sx
            dy = float(spk.y) - sy
            dz = float(spk.z) - sz
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            g = distance_attenuation(d, ref=0.55, rolloff=1.05) * couple
            g *= _db(float(getattr(spk, "gain_db", 0.0) or 0.0))
            if kind == 3:
                g *= 1.55
            elif kind == 1:
                g *= 0.16
            elif kind == 2:
                g *= 0.28
            if g < 1e-5:
                continue
            delay_n = int(round((d / C_SOUND) * sr))
            delay_n = max(0, min(delay_n, int(0.12 * sr)))
            sig = mono * g
            if delay_n > 0:
                sig = np.concatenate([np.zeros(delay_n), sig])
            taps.append((i, sig, 0))
        return taps

    def _spawn_visual_hit(self, hit: Tuple[float, float, float, int]) -> None:
        """One OpenGL droplet landing → its own stacked grain."""
        try:
            sx, sy, sz, kind = float(hit[0]), float(hit[1]), float(hit[2]), int(hit[3])
        except Exception:
            return
        q = max(0.0, min(1.0, float(getattr(self.room, "droplet_density", 0.5) or 0.0)))
        if q <= 0.0005:
            return
        if kind == 3:
            surface = self._surface_for_layer("roof")
        elif kind == 2:
            surface = "glass"
        else:
            surface = self._surface_for_layer("yard")
        sharp = self._sharpness()
        size = 1.3 + 2.4 * q + float(self._rng.uniform(-0.35, 0.45))
        if kind == 3:
            size *= 1.12
        mix_d = max(0.0, min(2.5, float(getattr(self.room, "mix_droplets", 1.0))))
        amp = (
            float(self._rng.uniform(0.70, 1.08))
            * self._master
            * (0.62 + 0.55 * q)
            * (mix_d ** 0.85)
            * (0.70 + 0.22 * sharp)
        )
        nv = max(1, len(self._voices))
        amp *= 1.0 / (0.86 + 0.024 * math.sqrt(nv))
        # Stay on the user's sharpness bin. Forcing 0.88 at downpour made plastic clacks.
        hit_sharp = max(0.12, min(0.95, sharp + 0.06 * q))
        try:
            self._drop_bank.ensure_built()
            mono = self._drop_bank.pick(
                surface=surface,
                size_mm=size,
                sharpness=hit_sharp,
                chain_n=1,
                rng=self._rng,
            )
        except Exception:
            from app.audio.engine import synth_drop
            mono = synth_drop(
                sr=self.samplerate,
                surface=surface,
                size_mm=size,
                seed=self._evt_id,
                sharpness=hit_sharp,
                tone_ring=0.0 if surface == "metal" else None,
            )
        self._evt_id += 1
        if surface == "metal":
            # Slightly shorter at downpour so new attacks aren't buried in tails.
            grain_ms = 52.0 - 8.0 * q
            amp *= 1.18 + 0.22 * q
            mono = self._short_grain(mono, ms=grain_ms, fade_ms=4.0) * amp
        else:
            grain_ms = 26.0 + 4.0 * q
            amp *= 0.32
            mono = self._short_grain(mono, ms=grain_ms, fade_ms=8.0) * amp
        src = (sx, sy, sz)
        taps = self._cheap_taps_for_hit(mono, src, kind)
        if taps:
            if len(self._voices) >= self.max_voices:
                self._evict_voice()
            self._voices.append(_TapVoice(taps, kind=kind))
        max_bi = min(self.max_voices, _MAX_STEREO_VOICES)
        if self.render_listener_binaural and len(self._stereo_voices) < max_bi:
            L = self.room.listener
            lx, ly, lz = float(L.x), float(getattr(L, "y", 1.2)), float(L.z)
            yaw = float(getattr(L, "yaw", 0.0))
            dx, dy, dz = sx - lx, sy - ly, sz - lz
            dist = max(0.25, math.sqrt(dx * dx + dy * dy + dz * dz))
            az = math.atan2(dx, dz) - yaw
            el = math.asin(max(-1.0, min(1.0, dy / dist)))
            g = self._portal_gain_cheap(sx, sy, sz, kind)
            g *= distance_attenuation(dist, ref=0.6, rolloff=1.1)
            stereo = binaural(mono * g * 1.35, az, el, distance=dist, sr=self.samplerate, quality="fast")
            if float(np.max(np.abs(stereo))) > 1e-7:
                self._stereo_voices.append(_StereoVoice(stereo))

    def _spawn_event(self, schedule_delay_n: int = 0):
        """Spawn outdoor impact chain → couple indoors → speakers / ears."""
        layer, x, y, z, depth = self._pick_source_3d()
        surface = self._surface_for_layer(layer)
        sharp = self._sharpness()
        wabs = self._wind_speed()
        q = float(getattr(self.room, "droplet_density", 0.5) or 0.5)
        chain_n = self._chain_hits()

        # Size: sharpness + quantity → soft/light drizzle vs sharp/heavy hits
        u = float(self._rng.rand())
        power = 1.85 - 0.55 * sharp
        t = u ** power
        size_max = 2.2 + 2.8 * sharp
        size = 0.45 + t * size_max
        size *= 1.0 - 0.18 * q * (1.0 - 0.5 * sharp)
        if q < 0.22:
            # Drizzle hits need body or they vanish under the bed
            size = max(size, 1.35 + 0.55 * (1.0 - q / 0.22))
        twt = max(0.0, min(1.0, float(getattr(self.room, "tone_weight", 0.5) or 0.5)))
        size *= 0.55 + 0.90 * twt
        # Roof / wall impacts run a bit larger (mass of surface hit)
        if layer == "roof":
            size *= 1.12
        elif layer == "wall":
            size *= 1.05

        hit_sharp = max(0.0, min(1.0, sharp + 0.35 * wabs * (0.5 + 0.5 * sharp)))
        hit_sharp *= 0.72 + 0.28 * max(0.0, min(1.0, q)) ** 0.6

        if len(self._voices) >= self.max_voices and len(self._stereo_voices) >= self.max_voices:
            self._evt_id += 1
            return

        # Prefer pre-baked bank (cheap). Live synth only if bank off / fail.
        if self.use_drop_bank:
            try:
                self._drop_bank.ensure_built()
                mono = self._drop_bank.pick(
                    surface=surface,
                    size_mm=size,
                    sharpness=hit_sharp,
                    chain_n=chain_n,
                    rng=self._rng,
                )
            except Exception:
                log.exception("drop bank pick failed — live synth fallback")
                mono = synth_drop(
                    sr=self.samplerate,
                    surface=surface,
                    size_mm=size,
                    seed=self._evt_id,
                    sharpness=hit_sharp,
                    tone_pitch=float(getattr(self.room, "tone_pitch", 0.5) or 0.5),
                    tone_ring=float(getattr(self.room, "tone_ring", 0.3) or 0.3),
                    tone_wet=float(getattr(self.room, "tone_wet", 0.85) or 0.85),
                    tone_soft=float(getattr(self.room, "tone_soft", 0.55) or 0.55),
                )
        else:
            mono = synth_drop(
                sr=self.samplerate,
                surface=surface,
                size_mm=size,
                seed=self._evt_id,
                sharpness=hit_sharp,
                tone_pitch=float(getattr(self.room, "tone_pitch", 0.5) or 0.5),
                tone_ring=float(getattr(self.room, "tone_ring", 0.3) or 0.3),
                tone_wet=float(getattr(self.room, "tone_wet", 0.85) or 0.85),
                tone_soft=float(getattr(self.room, "tone_soft", 0.55) or 0.55),
            )

        # Light live tone pitch via rate (bank is fixed; ear-lab still works)
        tp = max(0.0, min(1.0, float(getattr(self.room, "tone_pitch", 0.5) or 0.5)))
        # pitch 0 → slower/deeper (~0.88×), 1 → faster/higher (~1.12×)
        pitch_rate = 0.88 + 0.24 * tp
        rate = self._droplet_playback_rate(q) * pitch_rate
        if abs(rate - 1.0) > 0.008 and len(mono) > 32:
            n_out = max(24, int(round(len(mono) / rate)))
            if n_out != len(mono):
                xp = np.arange(len(mono), dtype=np.float64)
                xq = np.linspace(0.0, len(mono) - 1, n_out)
                mono = np.interp(xq, xp, mono).astype(np.float64)

        fade_ms = 2.5 + 3.5 * (1.0 - max(0.0, min(1.0, q)))
        fade_n = max(8, int(fade_ms * 0.001 * self.samplerate))
        if len(mono) > fade_n:
            mono = mono.copy()
            mono[:fade_n] *= np.linspace(0.0, 1.0, fade_n, dtype=np.float64)
            if q < 0.35 and len(mono) > fade_n * 2:
                mono[-fade_n:] *= np.linspace(1.0, 0.0, fade_n, dtype=np.float64)

        qq = max(0.02, min(1.0, q))
        # Heavier rain = louder hits (was inverted: drizzle boosted, downpour cut)
        dens_bal = 0.70 + 0.70 * qq
        size_k = 0.55 + 0.50 * min(1.0, size / max(0.5, size_max))
        mix_d = max(0.0, min(2.5, float(getattr(self.room, "mix_droplets", 1.0))))
        mix_d_eff = mix_d ** (0.92 if qq > 0.4 else 0.78)
        # Chains already pack energy — don't let long chains clip the bus
        chain_k = 1.0 / (0.55 + 0.45 * math.sqrt(float(chain_n)))
        amp = (
            float(self._rng.uniform(0.65, 1.05))
            * self._master
            * dens_bal
            * size_k
            * mix_d_eff
            * chain_k
        )
        if surface in ("tarp", "shell", "plastic", "hollow"):
            amp *= 0.78
        if layer == "far":
            amp *= 0.30
        elif layer == "mid":
            amp *= 0.68
        elif layer == "yard":
            amp *= 0.18 + 0.06 * wabs if surface != "metal" else 0.22
        elif layer == "wall":
            amp *= 0.70 + 0.12 * wabs
        elif layer == "roof":
            amp *= 1.40 + 0.18 * wabs if surface == "metal" else (0.85 + 0.12 * wabs)
        elif layer == "canopy":
            amp *= 0.42 + 0.10 * wabs
        elif layer == "near":
            amp *= 1.05 + 0.15 * wabs
        amp *= 0.65 + 0.25 * sharp
        mix_wind = max(0.0, min(2.5, float(getattr(self.room, "mix_wind", 1.0))))
        amp *= 0.78 + 0.35 * wabs * (0.35 + 0.65 * min(1.5, mix_wind))
        mono = mono * amp
        self._evt_id += 1
        src = (x, y, z)
        sr = self.samplerate

        busy = len(self._voices) > self.max_voices * 0.40
        binaural_q = "fast" if busy else "full"

        recvs: List[Tuple[int, Tuple[float, float, float], float]] = []
        want_spk = len(self._voices) < self.max_voices
        if want_spk:
            for i, spk in enumerate(self.room.speakers):
                if not getattr(spk, "enabled", True):
                    continue
                g_user = _db(float(getattr(spk, "gain_db", 0.0) or 0.0))
                recvs.append((i, (float(spk.x), float(spk.y), float(spk.z)), g_user))

        lis = None
        want_bi = (
            self.render_listener_binaural
            and len(self._stereo_voices) < min(self.max_voices, _MAX_STEREO_VOICES)
        )
        if want_bi:
            L = self.room.listener
            lis = (
                (float(L.x), float(getattr(L, "y", 1.2)), float(L.z)),
                float(getattr(L, "yaw", 0.0)),
            )

        if not recvs and lis is None:
            return

        spk_bufs, stereo, portals = render_drop_to_receivers(
            self.room,
            mono,
            src,
            recvs,
            sr,
            listener=lis,
            fast=busy,
            binaural_quality=binaural_q,
        )

        taps: List[Tuple[int, np.ndarray, int]] = []
        delay0 = max(0, int(schedule_delay_n))
        for i, acc in spk_bufs.items():
            if float(np.max(np.abs(acc))) < 1e-7:
                continue
            if delay0 > 0:
                acc = np.concatenate([np.zeros(delay0), acc])
            taps.append((i, acc, 0))

        wall_k = 0.45 * chain_k
        for p in portals:
            wid = WALL_BUS.get(str(p.wall).lower())
            if wid is None:
                continue
            if float(np.max(np.abs(p.signal))) < 1e-7:
                continue
            sig = p.signal * wall_k
            delay_n = max(0, min(int(round(p.delay_s * sr)), int(0.15 * sr))) + delay0
            if delay_n > 0:
                sig = np.concatenate([np.zeros(delay_n), sig])
            taps.append((wid, sig, 0))
        if layer == "roof":
            rb = mono * (0.32 * wall_k)
            if delay0 > 0:
                rb = np.concatenate([np.zeros(delay0), rb])
            taps.append((WALL_BUS["roof"], rb, 0))

        if taps and want_spk:
            self._voices.append(_TapVoice(taps))

        if stereo is not None and want_bi:
            if float(np.max(np.abs(stereo))) > 1e-7:
                stereo = stereo * 1.55
                if delay0 > 0:
                    pad = np.zeros((delay0, 2), dtype=np.float64)
                    stereo = np.vstack([pad, stereo])
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
        mix_r = max(0.0, min(2.5, float(getattr(self.room, "mix_reverb", 1.0))))
        wet = max(0.0, min(0.85, wet * mix_r))
        # Headphones: light reverb only — wet field + heavy reverb = ocean
        self._reverb_stereo.set_params(
            room_size=size, damping=min(0.97, damp + 0.12), wet=wet * 0.32
        )
        for rev in self._reverb_mono.values():
            rev.set_params(room_size=size, damping=damp, wet=wet * 0.85)

    def _mix_outdoor_field(self, frames: int, mix: Dict[int, np.ndarray]) -> None:
        """Add multi-depth outdoor field (procedural ± WAV) through window portals."""
        if not self.use_outdoor_field or frames <= 0:
            return
        quantity = float(getattr(self.room, "droplet_density", 0.5) or 0.0)
        sharp = self._sharpness()
        # Wash under wet drops — not a loud noise blanket.
        # At high quantity discrete hits are chained; wash carries density.
        q = max(0.0, min(1.0, quantity))
        mix_w = max(0.0, min(2.5, float(getattr(self.room, "mix_wash", 1.0))))
        # mix_wash=0 → no bed. q=0 already returned empty from the field.
        # Floor the *shape* so 4% isn't ~0; still rises into downpour.
        # Quantity is outdoor mass. mix_wash=0 silences the bed; 0.05 still
        # leaves a real downpour body (dark WAV), not a whisper.
        if mix_w <= 0.001:
            field_level = 0.0
        else:
            # Quantity should add *taps*, not a louder brown blanket.
            mass = 0.010 + 0.022 * q
            roof_mat = str(getattr(self.room, "roof_material", "") or "").lower()
            if "tin" in roof_mat or "metal" in roof_mat:
                mass *= 0.70 - 0.28 * q
            fader = 0.45 + 0.55 * min(1.2, mix_w)
            field_level = mass * float(self._master) * fader
        wt = _wall_tone(getattr(self.room, "wall_material", None))
        # Roof contributes a little if walls are mid (outdoor mass includes roof plane)
        rt = _wall_tone(getattr(self.room, "roof_material", None))
        wall_tone = 0.72 * wt + 0.28 * rt
        layers = self._field.render_layers(
            frames,
            quantity=quantity,
            sharpness=sharp,
            level=field_level,
            wall_tone=wall_tone,
        )
        if float(np.max(np.abs(layers.get("mix", np.zeros(1))))) < 1e-8:
            return

        routed = self._field_router.process_layers(
            self.room,
            layers,
            n_speakers=max(1, len(self.room.speakers)),
            render_listener=self.render_listener_binaural,
        )
        if len(routed) == 3:
            spk_add, bi_add, wall_add = routed
        else:
            spk_add, bi_add = routed
            wall_add = getattr(self._field_router, "last_wall", {}) or {}
        for name, buf in (wall_add or {}).items():
            wid = WALL_BUS.get(str(name).lower())
            if wid is None or buf is None:
                continue
            if wid not in mix:
                mix[wid] = np.zeros(frames, dtype=np.float64)
            mix[wid] = mix[wid] + buf
        for i, buf in spk_add.items():
            if i >= len(self.room.speakers):
                continue
            spk = self.room.speakers[i]
            if not getattr(spk, "enabled", True):
                continue
            if i not in mix:
                mix[i] = np.zeros(frames, dtype=np.float64)
            g_user = _db(float(getattr(spk, "gain_db", 0.0) or 0.0))
            # Quiet underlay — do not fuse with drops (that fills gaps → hiss)
            mix[i] = mix[i] + buf * g_user

        if bi_add is not None:
            if self._last_binaural is None or self._last_binaural.shape[0] != frames:
                self._last_binaural = np.zeros((frames, 2), dtype=np.float64)
            if bi_add.shape[0] == frames:
                wash_g = 0.07 + 0.06 * q
                roof_mat = str(getattr(self.room, "roof_material", "") or "").lower()
                if "tin" in roof_mat or "metal" in roof_mat:
                    wash_g *= 0.48
                wash = bi_add * wash_g
                cap = 0.004 + 0.007 * q
                w_rms = float(np.sqrt(np.mean(wash * wash)) + 1e-12)
                if w_rms > cap:
                    wash = wash * (cap / w_rms)
                self._last_binaural = self._last_binaural + wash

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
        """Schedule chained outdoor impacts; return per-speaker mono field."""
        sr = self.samplerate
        dt = frames / float(sr)
        self._update_wind(dt)
        t0 = self._time
        t1 = t0 + dt
        now = time.perf_counter()
        with self._hit_lock:
            vis_hits = self._pending_hits
            self._pending_hits = []
            visual = (now - self._visual_seen_t) < 0.28
        if visual:
            for h in vis_hits:
                self._spawn_visual_hit(h)
            self._next_event = t1
        else:
            vips = self._voice_ips()
            spawned = 0
            max_spawn = min(
                _MAX_SPAWN_PER_BLOCK,
                max(4, int(vips * frames / sr) + 4),
            ) if vips > 0 else 0
            while vips > 0 and self._next_event < t1 and spawned < max_spawn:
                if self._next_event >= t0:
                    delay = int((self._next_event - t0) * sr)
                    self._spawn_event(schedule_delay_n=max(0, delay))
                    spawned += 1
                gap = float(self._rng.exponential(1.0 / max(1e-6, vips)))
                self._next_event += max(gap, 1.0 / sr)
            if vips <= 0:
                self._next_event = t1 + 1.0
        self._time = t1

        mix = self._mix_speakers(frames)
        # Stash binaural block for headphone path
        self._last_binaural = self._mix_binaural(frames)

        # Continuous outdoor field through portals (procedural ± WAV samples)
        self._mix_outdoor_field(frames, mix)

        # Wind air — layered body/whoosh/gusts (WindAirSynth), not pink static
        wabs = self._wind_speed()
        mix_wind = max(0.0, min(2.5, float(getattr(self.room, "mix_wind", 1.0))))
        if mix_wind > 0.02 and wabs > 0.03:
            open_avg = self._open_avg()
            # Audible under rain without drowning droplets; mix_wind is gain
            wind_level = (0.055 + 0.070 * wabs) * mix_wind
            mono_wind = self._wind_air.render(
                frames, wind=wabs, level=wind_level, open_avg=open_avg
            )
            if mono_wind is not None and float(np.max(np.abs(mono_wind))) > 1e-8:
                if self._last_binaural is not None and self._last_binaural.shape[0] == frames:
                    wx, wz = self._wind_push()
                    yaw = float(getattr(self.room.listener, "yaw", 0.0))
                    c, s = math.cos(yaw), math.sin(yaw)
                    right = wx * c - wz * s
                    pan = max(-1.0, min(1.0, right / max(0.08, wabs)))
                    # Headphones a touch quieter (close to ears)
                    bi = self._wind_air.to_stereo(mono_wind, pan=pan, level=0.50)
                    if bi.shape[0] == frames:
                        self._last_binaural = self._last_binaural + bi
                spk_g = 0.70 * (0.40 + 0.60 * wabs) * (0.40 + 0.60 * open_avg)
                for i, spk in enumerate(self.room.speakers):
                    if i not in mix or not getattr(spk, "enabled", True):
                        continue
                    mix[i] = mix[i] + mono_wind * spk_g

        # Wall buses stay dry (outdoor portal energy). Speakers get room.
        spk_mix: Dict[int, np.ndarray] = {}
        wall_mix: Dict[str, np.ndarray] = {}
        rev_keys = set(WALL_BUS.values())
        name_of = {v: k for k, v in WALL_BUS.items()}
        for k, buf in mix.items():
            if k in rev_keys:
                wall_mix[name_of[k]] = buf
            else:
                spk_mix[k] = buf
        self._last_wall = {
            k: _soft_clip(v, 0.75) for k, v in wall_mix.items()
        }

        # Mild indoor room reverb (size / open windows shape wetness)
        mix = self._apply_reverb(spk_mix, frames)

        # Soft bus makeup — sample-wise soft clip (never hard block peak-norm)
        q = float(getattr(self.room, "droplet_density", 0.5) or 0.0)
        spk_make = 1.08 + 0.06 * q
        bi_make = 1.00 + 0.04 * q
        out = {}
        for i, buf in mix.items():
            out[i] = _soft_clip(buf * spk_make, 0.75)
        if self._last_binaural is not None and self._last_binaural.shape[0] == frames:
            # Higher ceiling so droplet transients aren't squashed into wash
            self._last_binaural = _soft_clip(self._last_binaural * bi_make, 0.88)
        return out

    def _reset_sim(self, seed: int = 11):
        self._voices = deque()
        self._stereo_voices = deque()
        with self._hit_lock:
            self._pending_hits = []
        self._last_binaural = np.zeros((0, 2), dtype=np.float64)
        self._time = 0.0
        self._next_event = 0.0
        self._evt_id = 0
        self._rng = np.random.RandomState(seed)
        self._wind_air = WindAirSynth(sr=self.samplerate, seed=4242 + seed)
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
        self._last_wall = {}
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

        Stereo device: speakers sharing the DAC are panned L/R from 3D layout.
        4.0 / 5.1 / 7.1 device: VBAP of speaker mics + wall-portal buses so
        rain wraps the room. If the card can't open surround, the opener
        already fell back to stereo.
        """
        groups: Dict[int, List[Tuple[int, Speaker]]] = {}
        for i, spk in enumerate(self.room.speakers):
            di = getattr(spk, "audio_device", None)
            if di is None or not getattr(spk, "enabled", True):
                continue
            groups.setdefault(int(di), []).append((i, spk))

        L = self.room.listener
        yaw = float(getattr(L, "yaw", 0.0))
        wall_bufs = getattr(self, "_last_wall", {}) or {}
        out: Dict[int, np.ndarray] = {}

        for di, group in groups.items():
            ch = clamp_device_channels(int(self._device_channels.get(di, 2) or 2))
            bus = self._buses.get(di)
            if bus is not None:
                ch = clamp_device_channels(int(bus.channels))

            if ch >= 4:
                group_spk = [spk for _i, spk in group]
                spk_only = {li: mix.get(orig) for li, (orig, _spk) in enumerate(group)}
                block = mix_to_surround(
                    frames=frames,
                    n_ch=ch,
                    speaker_bufs=spk_only,
                    speakers=group_spk,
                    wall_bufs=wall_bufs,
                    room_width=float(self.room.width),
                    room_depth=float(self.room.depth),
                )
                out[di] = self._apply_master(block).astype(np.float32)
                continue

            if len(group) == 1 or ch == 1:
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

            left = np.zeros(frames, dtype=np.float64)
            right = np.zeros(frames, dtype=np.float64)
            n_sp = len(group)
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
                        bus = self._buses.get(di)
                        ch = int(getattr(bus, "channels", self._device_channels.get(di, 2)) or 2)
                        ch = clamp_device_channels(ch)
                        data = dev_blocks.get(di)
                        if data is None:
                            data = np.zeros((block, ch), dtype=np.float32)
                        bus = self._buses.get(di)
                        if bus is not None and int(bus.samplerate) != int(self.samplerate):
                            data = _resample_audio(data, self.samplerate, bus.samplerate)
                        if q.qsize() < 10:
                            try:
                                q.put_nowait(data)
                            except queue.Full:
                                pass

                if want_you and you_block is not None and self._hp_queue is not None:
                    hp_sr = int(getattr(self, "_hp_stream_sr", self.samplerate) or self.samplerate)
                    if hp_sr != int(self.samplerate):
                        you_block = _resample_audio(you_block, self.samplerate, hp_sr)
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
        self._hp_carry = np.zeros((0, 2), dtype=np.float32)
        self._hp_last = np.zeros((1, 2), dtype=np.float32)
        self._hp_stream_sr = int(self.samplerate)

        def cb(outdata, frames, time_info, status):
            try:
                if status:
                    log.warning("You stream status: %s", status)
                q = self._hp_queue
                if q is None:
                    outdata[:] = 0
                    return
                need = frames
                parts = []
                carry = getattr(self, "_hp_carry", None)
                if carry is not None and carry.shape[0] > 0:
                    take = min(need, carry.shape[0])
                    parts.append(carry[:take])
                    self._hp_carry = carry[take:]
                    need -= take
                while need > 0:
                    try:
                        raw = q.get_nowait()
                    except queue.Empty:
                        if parts:
                            last = parts[-1][-1:]
                        elif self._hp_last.shape[0] > 0:
                            last = self._hp_last[-1:]
                        else:
                            last = np.zeros((1, 2), dtype=np.float32)
                        parts.append(np.repeat(last, need, axis=0))
                        need = 0
                        break
                    b = np.asarray(raw, dtype=np.float32)
                    if b.ndim == 1:
                        b = np.stack([b, b], axis=1)
                    if b.shape[1] < 2:
                        pad = np.zeros((b.shape[0], 2), dtype=np.float32)
                        pad[:, : b.shape[1]] = b
                        b = pad
                    else:
                        b = b[:, :2]
                    if b.shape[0] <= need:
                        parts.append(b)
                        need -= b.shape[0]
                    else:
                        parts.append(b[:need])
                        self._hp_carry = b[need:]
                        need = 0
                buf = np.concatenate(parts, axis=0) if parts else np.zeros((frames, 2), dtype=np.float32)
                outdata[:] = buf[:frames]
                self._hp_last = np.array(outdata[-1:], copy=True)
            except Exception:
                log.exception("You stream callback")
                outdata[:] = 0

        self._hp_stream, self._hp_stream_sr, hp_bs, _hp_ch = _open_output_stream(
            device_index=int(device_index) if device_index is not None else None,
            channels=2,
            preferred_sr=self.samplerate,
            preferred_blocksize=block,
            callback=cb,
        )
        # Prefill silence at stream rate so callback never starves before mixer runs
        silent = np.zeros((hp_bs, 2), dtype=np.float32)
        for _ in range(6):
            try:
                self._hp_queue.put_nowait(silent.copy())
            except queue.Full:
                break
        log.info(
            "You (binaural) stream on device %s sr=%s block=%s",
            device_index,
            self._hp_stream_sr,
            hp_bs,
        )

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

        # Build hit bank once before streaming so the mixer never hitch-builds
        if self.use_drop_bank:
            try:
                self._drop_bank.ensure_built()
            except Exception:
                log.exception("drop bank build failed — will live-synth")

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
            ch = clamp_device_channels(ch)
            if len(group) > 1:
                ch = max(2, ch)
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
        self._hp_stream_sr = int(self.samplerate)
        self._hp_carry = np.zeros((0, 2), dtype=np.float32)
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
        if self.use_drop_bank:
            try:
                self._drop_bank.ensure_built()
            except Exception:
                log.exception("drop bank build failed — will live-synth")
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
        channel_gains: Optional[np.ndarray] = None,
    ):
        """Play a short tone. device_index=None uses the OS default output.

        ``channel_gains`` is a vector of per-channel amplitudes (stereo pan or
        surround VBAP). If omitted, the tone is dual-mono / centre.
        """
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
        last_err: Optional[BaseException] = None
        gains = None
        if channel_gains is not None:
            gains = np.asarray(channel_gains, dtype=np.float64).reshape(-1)
            if gains.size < 1:
                gains = None
        try:
            ch_try = [int(gains.size)] if gains is not None else [2]
            if 2 not in ch_try:
                ch_try.append(2)
            played = False
            for ch in ch_try:
                g_use = gains
                if g_use is None or g_use.size != ch:
                    if ch == 2 and g_use is not None and g_use.size >= 2:
                        g_use = g_use[:2]
                    elif ch == 2:
                        g_use = np.array([1.0, 1.0], dtype=np.float64)
                    else:
                        continue
                for sr in _sample_rate_candidates(self.samplerate, device_index):
                    n = max(1, int(sr * seconds))
                    t = np.arange(n, dtype=np.float64) / sr
                    env = np.ones(n)
                    mid = n // 2
                    gap = max(1, int(0.04 * sr))
                    env[max(0, mid - gap) : mid + gap] = 0.0
                    fade = max(1, int(0.01 * sr))
                    env[:fade] *= np.linspace(0, 1, fade)
                    env[-fade:] *= np.linspace(1, 0, fade)
                    sig = (gain * np.sin(2 * math.pi * frequency * t) * env).astype(np.float64)
                    audio = (sig[:, None] * g_use[None, :]).astype(np.float32)
                    try:
                        sd.play(audio, sr, device=device_index, blocking=True)
                        last_err = None
                        played = True
                        break
                    except Exception as e:
                        last_err = e
                        log.warning(
                            "Test tone on device %s at %s Hz / %s ch failed: %s",
                            device_index, sr, ch, e,
                        )
                if played:
                    break
            if last_err is not None:
                raise RuntimeError(
                    f"Test tone failed on device {device_index}: {last_err}"
                ) from last_err
        finally:
            if was_running:
                if mode == "multi":
                    self.start(include_you=False)
                elif mode == "headphones":
                    self.start_headphones(headphones_device=hp_dev)
                elif mode == "all":
                    self.start(include_you=True, headphones_device=hp_dev)

    def play_speaker_test(self, speaker: Speaker, index_hint: int = 0):
        """Chirp this room speaker, panned by its place among others on the same device."""
        if speaker.audio_device is None:
            raise RuntimeError(f"Speaker '{speaker.name}' has no output device assigned")
        di = int(speaker.audio_device)
        ch = 2
        for d in self.devices:
            if int(d.get("index", -1)) == di:
                ch = clamp_device_channels(int(d.get("channels", 2) or 2))
                break
        gains = speaker_test_channel_gains(speaker, self.room, n_ch=ch)
        freq = 520.0 + 70.0 * (index_hint % 8)
        self.play_test_tone(di, frequency=freq, channel_gains=gains)
        return speaker_test_pan_label(speaker, self.room)

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
