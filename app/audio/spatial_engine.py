
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
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None

from app.audio.drop_bank import SHARP_BINS, SIZE_BINS_MM, DropSampleBank, _nearest
from app.audio.engine import synth_drop, _db, _peak_cap
from app.audio.field_bed import OutdoorFieldBed
from app.audio.field_router import OutdoorFieldRouter
from app.audio.rain_dsd import (
    D_MAX_MM,
    D_MIN_MM,
    DropSizeDist,
    dsd_for_quantity,
    impact_amp,
    loudness_gain,
    quantity_to_rain_rate,
    terminal_velocity,
)
from app.audio.rain_texture import VARIANTS_PER_SIZE, DenseRainTexture, make_variants
from app.audio import house_model as hm
from app.audio.runoff import DripSpot, RoofRunoff
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
# 3D-view hit kinds → catchment layer (see house_model.catchments)
_KIND_LAYER = {1: "ground", 2: "window", 3: "roof", 4: "wall", 5: "sill"}
_LAYER_KIND = {v: k for k, v in _KIND_LAYER.items()}
# Calibration probe spots per layer (full path vs the 3D view's cheap taps)
_CAL_POINTS = 16
_CAL_CHUNK = 4
# Drips are as loud as the voiced drops of this quantity, whatever the slider
# says now (they keep falling after it hits 0).
_DRIP_REF_Q = 0.35
# Drop size → timbre. Smaller drops ring higher and shorter, bigger ones lower
# and fuller: playback rate (D_REF/D)^exp. 0 = every size sounds the same.
_SIZE_PITCH_EXP = 0.35
_SIZE_PITCH_REF_MM = 3.0
# Dense sheet above its grain cap: 1 = louder grains, 0 = continuous roar
_DENSE_HIT_MAKEUP = 0.25
_DRIP_GAIN = 0.45
# Gutter trickle: splashes/s per downspout at full flow, and its level.
# The portal model has no structure-borne path (a downspout is bolted to the
# house), so the gain is set by measurement: ~6 dB under the eave drips just
# after the rain stops.
_GUTTER_MAX_RATE = 700.0
_GUTTER_HALF_FLOW = 10.0
_GUTTER_GAIN = 30.0
# Biggest drops/s that get their own spatial voice; the rest are dense texture.
_DISCRETE_BUDGET_PER_SEC = 150.0
# Calibrates physical impact_amp (1.0 = 3 mm drop) to the engine's mix level.
_DROP_LEVEL = 0.55
# The portal render (no 3D view) couples ~3 dB hotter than the cheap taps
# the 3D view uses; trim it so switching tabs doesn't jump the volume.
# One level for every drop, both paths. Anchored so the original tin-roof
# house kept its loudness when the per-layer gains were replaced by physics.
_LEVEL_TRIM = 2.11
# Ears vs speakers on the full path (was 1.55; −8.8 dB keeps the headphone
# level where the tin-roof house had it while speakers come up to theirs)
_HP_PATH_GAIN = 0.56


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


def _wide_full(cal: Dict[str, Dict[object, float]]) -> Dict[str, Dict[object, float]]:
    """Speaker entries of "full" := full/cheap ratio × wide-averaged cheap gain."""
    full, cheap, wide = cal.get("full", {}), cal.get("cheap", {}), cal.pop("wide", {})
    for k, w in wide.items():
        c = cheap.get(k, 0.0)
        if c > 1e-12 and k in full:
            full[k] = full[k] / c * w
            cheap[k] = w
    return cal


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
        # Dense texture: drops below the discrete split (see rain_texture.py)
        self.use_dense_texture = True
        self._dense = DenseRainTexture(sr=self.samplerate, n_stems=3, seed=777)
        self._dense_kernel_id: Optional[tuple] = None
        self._dense_kernel_gain: Dict[str, float] = {}
        self._last_dense_gains: Dict[str, Dict[object, float]] = {}
        # Path calibration per catchment layer: {"full": {recv: g}, "cheap": {recv: g}}
        self._path_cal: Dict[str, Dict[str, Dict[object, float]]] = {}
        self._cal_fp: Optional[tuple] = None
        self._cal_pending: List[str] = []
        self._cal_new: Dict[str, Dict[str, Dict[object, float]]] = {}
        self._cal_t = -1e9
        self._patch_key: Optional[tuple] = None
        self._patch_cache: List[hm.Patch] = []
        # Roof runoff: eave drips + gutter trickle that outlast the rain
        self.use_drips = True
        # Scheduled drops (no 3D view) use the same calibrated taps as the view;
        # True renders each through the full wall/window path (offline quality).
        self.full_path_events = False
        self._runoff = RoofRunoff(seed=31)
        self._visual_active = False
        self._visual_rate = 0.0

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

    def _patches(self) -> List[hm.Patch]:
        """Where rain lands on this house, from its geometry, materials and wind."""
        r = self.room
        wins = tuple(
            (str(getattr(w, "wall", "")), round(float(getattr(w, "offset", 0.0) or 0.0), 2),
             round(float(getattr(w, "width", 1.0) or 1.0), 2), round(float(getattr(w, "sill", 0.9) or 0.0), 2),
             round(float(getattr(w, "height", 1.2) or 1.2), 2))
            for w in (getattr(r, "windows", []) or [])
        )
        key = (
            round(float(r.width), 2), round(float(r.depth), 2), round(float(r.height), 2),
            str(getattr(r, "roof_material", "")), str(getattr(r, "wall_material", "")), wins,
            round(self._wind_speed(), 2), round(self._wind_dir_deg() / 3.0),
        )
        if key != self._patch_key:
            self._patch_cache = hm.catchments(r, self._wind_speed(), self._wind_dir_deg())
            self._patch_key = key
        return self._patch_cache

    def _rain_area(self) -> float:
        """Horizontal-equivalent catchment of every surface (m²)."""
        return hm.catchment_area(self._patches())

    def _discrete_budget(self) -> float:
        """Voiced drops/s. With the 3D view driving, never more than it lands."""
        if self._visual_active and self._visual_rate > 0.0:
            return max(1.0, min(_DISCRETE_BUDGET_PER_SEC, self._visual_rate))
        return _DISCRETE_BUDGET_PER_SEC

    def _rain_state(self) -> Tuple[float, Optional[DropSizeDist], float]:
        """(drops/s on the house with D >= split, distribution, split mm).

        Drops above ``split`` get their own spatial voice (budget-capped);
        the rest are the dense texture. Wind tilts more rain into the facades.
        """
        q = float(getattr(self.room, "droplet_density", 0.5) or 0.0)
        rate, dist = dsd_for_quantity(q)
        if rate <= 0.0:
            return 0.0, None, D_MAX_MM
        area = self._rain_area()
        split = dist.split_for_rate(self._discrete_budget(), area)
        return float(dist.flux(split) * area), dist, float(split)

    def _ips(self) -> float:
        """Discrete (individually spatialized) drop impacts per second.

        Physical: the largest drops landing on the house, up to the voice
        budget. Everything smaller is the dense texture (``_mix_dense``).
        """
        n, _dist, _split = self._rain_state()
        return float(min(_MAX_VOICE_EVENTS_PER_SEC, n))

    def _chain_hits(self) -> int:
        """Always single drops — density now comes from the dense texture."""
        return 1

    def _voice_ips(self) -> float:
        return self._ips()

    def _drop_amp(self, d_mm: float) -> float:
        """Physical per-drop amplitude (shared by discrete and dense paths)."""
        q = float(getattr(self.room, "droplet_density", 0.5) or 0.0)
        return float(impact_amp(d_mm)) * loudness_gain(q) * _DROP_LEVEL

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

    def _layer_probs(self) -> Dict[str, float]:
        """Share of landings per catchment layer (roof / wall / window / sill / ground)."""
        return hm.layer_shares(self._patches())

    def _pick_source_3d(self) -> Tuple[str, float, float, float, float]:
        """One landing, by catchment area. Returns (layer, x, y, z, metres outside)."""
        patch = hm.pick_patch(self._patches(), self._rng)
        if patch is None:
            return "roof", float(self.room.width) * 0.5, float(self.room.height), float(self.room.depth) * 0.5, 0.0
        x, y, z = hm.sample_point(self.room, patch, self._rng)
        w, d = float(self.room.width), float(self.room.depth)
        out = max(0.0, -x, x - w, -z, z - d)
        return patch.layer, float(x), float(y), float(z), float(out)

    def _point_for_layer(self, layer: str) -> Optional[Tuple[float, float, float]]:
        patch = hm.pick_patch([p for p in self._patches() if p.layer == layer], self._rng)
        if patch is None:
            return None
        return hm.sample_point(self.room, patch, self._rng)

    def _surface_for_layer(self, layer: str) -> str:
        if layer == "roof":
            return hm.roof_material(self.room).surface
        if layer == "wall":
            return hm.wall_material(self.room).surface
        if layer in ("window", "near"):
            return "glass"
        if layer == "sill":
            return "metal"
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

    def _grain_rate_jitter(self) -> float:
        """Per-hit playback rate. The bank holds 3 variants per key; at a
        downpour the same ones repeat and their noise peaks line up into a
        hollow ring. Small random rates keep every hit's spectrum different."""
        return math.exp(float(self._rng.uniform(-0.09, 0.09)))

    def _size_rate(self, d_mm: float) -> float:
        if _SIZE_PITCH_EXP <= 0.0:
            return 1.0
        r = (_SIZE_PITCH_REF_MM / max(0.3, float(d_mm))) ** _SIZE_PITCH_EXP
        return max(0.70, min(1.50, r))

    def _jitter_grain(self, mono: np.ndarray, base_rate: float = 1.0) -> np.ndarray:
        rate = self._grain_rate_jitter() * base_rate
        if len(mono) <= 32:
            return mono
        n_out = max(24, int(round(len(mono) / rate)))
        xq = np.linspace(0.0, len(mono) - 1, n_out)
        out = np.interp(xq, np.arange(len(mono), dtype=np.float64), mono)
        # Level-neutral: pitch varies, loudness stays on the drop-size law
        return out * math.sqrt(float(np.sum(mono * mono)) / max(1e-18, float(np.sum(out * out))))

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

    def _cheap_spk_gains(self, src: Tuple[float, float, float], kind: int) -> List[Tuple[int, float, int]]:
        """(speaker, gain, delay samples) of the cheap tap path."""
        out = []
        for i, sig, delay_n in self._cheap_taps_for_hit(None, src, kind):
            out.append((i, sig, delay_n))
        return out

    def _cheap_taps_for_hit(
        self, mono: Optional[np.ndarray], src: Tuple[float, float, float], kind: int
    ) -> List[Tuple[int, np.ndarray, int]]:
        """Taps for ``mono``; with ``mono=None`` returns (speaker, gain, delay)."""
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
            if mono is None:
                taps.append((i, g, delay_n))
                continue
            sig = mono * g
            if delay_n > 0:
                sig = np.concatenate([np.zeros(delay_n), sig])
            taps.append((i, sig, 0))
        return taps

    def _drop_grain(self, surface: str, drop_d: float, layer: str, rng=None) -> np.ndarray:
        """The sound of one drop on ``surface`` — the same recipe for the 3D
        view, the scheduler and the dense texture. Scaled by how loudly that
        material rings (house_model.surface_radiate)."""
        rng = self._rng if rng is None else rng
        q = max(0.0, min(1.0, float(getattr(self.room, "droplet_density", 0.5) or 0.0)))
        hit_sharp = max(0.12, min(0.95, self._sharpness() + 0.06 * q))
        twt = max(0.0, min(1.0, float(getattr(self.room, "tone_weight", 0.5) or 0.5)))
        size = drop_d * (0.55 + 0.90 * twt) * (1.12 if layer == "roof" else 1.0)
        try:
            self._drop_bank.ensure_built()
            mono = self._drop_bank.pick(surface=surface, size_mm=size, sharpness=hit_sharp, chain_n=1, rng=rng)
        except Exception:
            mono = synth_drop(sr=self.samplerate, surface=surface, size_mm=size, seed=self._evt_id,
                              sharpness=hit_sharp, tone_ring=0.0 if surface == "metal" else None)
        mono = self._jitter_grain(mono, self._size_rate(drop_d) * self._tone_rate())
        return self._shape_grain(mono, surface) * hm.surface_radiate(surface)

    def _tone_rate(self) -> float:
        """Ear-lab pitch: 0 → ~0.88× (deeper), 1 → ~1.12× (higher)."""
        tp = max(0.0, min(1.0, float(getattr(self.room, "tone_pitch", 0.5) or 0.5)))
        return 0.88 + 0.24 * tp

    def _shape_grain(self, mono: np.ndarray, surface: str) -> np.ndarray:
        q = max(0.0, min(1.0, float(getattr(self.room, "droplet_density", 0.5) or 0.0)))
        if surface == "metal":
            # Slightly shorter at downpour so new attacks aren't buried in tails.
            return self._short_grain(mono, ms=52.0 - 8.0 * q, fade_ms=4.0)
        return self._short_grain(mono, ms=26.0 + 4.0 * q, fade_ms=8.0)

    def _hit_amp(self, drop_d: float) -> float:
        mix_d = max(0.0, min(2.5, float(getattr(self.room, "mix_droplets", 1.0))))
        return (
            self._drop_amp(drop_d)
            * math.exp(float(self._rng.normal(0.0, 0.25)))
            * self._master
            * (mix_d ** 0.85)
            * (0.70 + 0.22 * self._sharpness())
            * _LEVEL_TRIM
        )

    def _cheap_cal(self, layer: str) -> Dict[object, float]:
        """Per-receiver factor that brings the 3D view's cheap taps up/down to
        the full wall/window path for this layer."""
        cal = self._path_cal.get(layer)
        if not cal:
            return {}
        full, cheap = cal.get("full", {}), cal.get("cheap", {})
        return {k: (full.get(k, 0.0) / cheap[k]) if cheap.get(k, 0.0) > 1e-9 else 0.0 for k in set(full) | set(cheap)}

    def _spawn_visual_hit(self, hit: Tuple[float, float, float, int], delay_n: int = 0) -> None:
        """One OpenGL droplet landing → its own stacked grain, ``delay_n``
        samples into the next block."""
        try:
            sx, sy, sz, kind = float(hit[0]), float(hit[1]), float(hit[2]), int(hit[3])
        except Exception:
            return
        q = max(0.0, min(1.0, float(getattr(self.room, "droplet_density", 0.5) or 0.0)))
        if q <= 0.0005:
            return
        # The view lands thousands of streaks/s at downpour; only the biggest
        # drops are voiced. The rest are already in the dense texture.
        if self._visual_rate > 0.0:
            keep = self._discrete_budget() / self._visual_rate
            if keep < 1.0 and float(self._rng.rand()) > keep:
                return
        layer = _KIND_LAYER.get(kind, "ground")
        surface = self._surface_for_layer(layer)
        # A drawn droplet stands for one of the big drops; the dense texture
        # carries the ones too small / many to draw.
        _n, dist, split = self._rain_state()
        drop_d = float(dist.sample(1, self._rng, split, D_MAX_MM)[0]) if dist is not None else 2.0
        mono = self._drop_grain(surface, drop_d, layer) * self._hit_amp(drop_d)
        self._evt_id += 1
        self._queue_cheap(mono, (sx, sy, sz), kind, layer, delay_n)

    def _queue_cheap(self, mono: np.ndarray, src: Tuple[float, float, float], kind: int,
                     layer: str, delay_n: int = 0) -> None:
        """Delay/distance taps to each speaker and a fast HRTF to the ears,
        levelled per layer to the full wall/window path (_path_cal)."""
        cal = self._cheap_cal(layer)
        taps = self._cheap_taps_for_hit(mono, src, kind)
        if cal:
            taps = [(ch, buf * cal.get(ch, 0.0), pos) for ch, buf, pos in taps if cal.get(ch, 0.0) > 0.0]
        delay_n = max(0, int(delay_n))
        if taps:
            if delay_n:
                taps = [(ch, np.concatenate([np.zeros(delay_n), buf[pos:]]), 0) for ch, buf, pos in taps]
            if len(self._voices) >= self.max_voices:
                self._evict_voice()
            self._voices.append(_TapVoice(taps, kind=kind))
        max_bi = min(self.max_voices, _MAX_STEREO_VOICES)
        if self.render_listener_binaural and len(self._stereo_voices) < max_bi:
            stereo = self._cheap_binaural(mono, src, kind)
            if cal and stereo is not None:
                stereo = stereo * np.array([cal.get("L", 0.0), cal.get("R", 0.0)])[None, :]
            if stereo is not None and float(np.max(np.abs(stereo))) > 1e-7:
                if delay_n:
                    stereo = np.concatenate([np.zeros((delay_n, stereo.shape[1])), stereo])
                self._stereo_voices.append(_StereoVoice(stereo))

    def _cheap_binaural(self, mono: np.ndarray, src: Tuple[float, float, float], kind: int) -> Optional[np.ndarray]:
        L = self.room.listener
        sx, sy, sz = src
        lx, ly, lz = float(L.x), float(getattr(L, "y", 1.2)), float(L.z)
        dx, dy, dz = sx - lx, sy - ly, sz - lz
        dist = max(0.25, math.sqrt(dx * dx + dy * dy + dz * dz))
        az = math.atan2(dx, dz) - float(getattr(L, "yaw", 0.0))
        el = math.asin(max(-1.0, min(1.0, dy / dist)))
        g = self._portal_gain_cheap(sx, sy, sz, kind) * distance_attenuation(dist, ref=0.6, rolloff=1.1)
        return binaural(mono * g * 1.35, az, el, distance=dist, sr=self.samplerate, quality="fast")

    def _spawn_event(self, schedule_delay_n: int = 0):
        """One scheduled landing (no 3D view) → full wall/window path."""
        if len(self._voices) >= self.max_voices and len(self._stereo_voices) >= self.max_voices:
            self._evt_id += 1
            return
        _n, dist, split = self._rain_state()
        if dist is None:
            self._evt_id += 1
            return
        layer, x, y, z, _out = self._pick_source_3d()
        drop_d = float(dist.sample(1, self._rng, split, D_MAX_MM)[0])
        surface = self._surface_for_layer(layer)
        mono = self._drop_grain(surface, drop_d, layer) * self._hit_amp(drop_d)
        self._evt_id += 1
        if self.full_path_events or not self._path_cal:
            self._render_and_queue(mono, (x, y, z), layer, schedule_delay_n, 0)
        else:
            self._queue_cheap(mono, (x, y, z), _LAYER_KIND.get(layer, 1), layer, schedule_delay_n)

    def _render_and_queue(
        self,
        mono: np.ndarray,
        src: Tuple[float, float, float],
        layer: str,
        schedule_delay_n: int = 0,
        fade_n: int = 0,
    ) -> None:
        """Full wall/window path to every speaker, wall bus and the ears."""
        sr = self.samplerate
        chain_k = 1.0

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
                stereo = stereo * _HP_PATH_GAIN
                if delay0 > 0:
                    pad = np.zeros((delay0, 2), dtype=np.float64)
                    stereo = np.vstack([pad, stereo])
                n0 = min(fade_n, stereo.shape[0])
                if n0 > 1:
                    stereo = stereo.copy()
                    stereo[:n0] *= np.linspace(0.0, 1.0, n0, dtype=np.float64)[:, None]
                self._stereo_voices.append(_StereoVoice(stereo))

    # ----- roof runoff: eave drips -----
    def _runoff_windows(self) -> Tuple[Tuple[str, float, float, float], ...]:
        out = []
        for w in getattr(self.room, "windows", []) or []:
            if getattr(w, "free_place", False):
                continue
            lo = float(getattr(w, "offset", 0.0) or 0.0)
            out.append((
                str(getattr(w, "wall", "north") or "north").lower(),
                lo, lo + float(getattr(w, "width", 1.0) or 1.0),
                float(getattr(w, "sill", 0.9) or 0.0),
            ))
        return tuple(out)

    def _update_runoff(self, frames: int) -> None:
        """Fill/drain the roof store and voice every drip that lets go."""
        if not self.use_drips:
            return
        r = self.room
        self._runoff.layout(float(r.width), float(r.depth), float(r.height), self._runoff_windows())
        q = float(getattr(r, "droplet_density", 0.5) or 0.0)
        dt = frames / float(self.samplerate)
        rain = quantity_to_rain_rate(q)
        self._runoff.update(dt, rain)
        mix_k = max(0.0, min(2.5, float(getattr(r, "mix_drips", 1.0))))
        if mix_k <= 0.01:
            return
        # Heavy rain buries the drips; voice fewer of them (each is a full render)
        keep = 1.0 / (1.0 + rain / 5.0)
        for spot, t in self._runoff.drips(dt):
            if keep < 1.0 and float(self._rng.rand()) > keep:
                continue
            self._spawn_drip(spot, int(t * self.samplerate), mix_k)

    def _drip_amp(self, d_mm: float, fall_m: float) -> float:
        """Impact ∝ D²v², with v from the fall (eaves are below terminal speed)."""
        v_t = float(terminal_velocity(d_mm))
        v = min(v_t, math.sqrt(2.0 * 9.81 * max(0.05, fall_m)))
        return float(impact_amp(d_mm)) * (v / max(1e-6, v_t)) ** 2

    def _spawn_drip(self, spot: DripSpot, delay_n: int, mix_k: float) -> None:
        if len(self._voices) >= self.max_voices:
            return
        try:
            self._drop_bank.ensure_built()
            mono = self._drop_bank.pick(
                surface=spot.surface, size_mm=spot.size_mm,
                sharpness=self._sharpness(), chain_n=1, rng=self._rng,
            )
        except Exception:
            return
        # Same spot, same drop: fixed pitch per spot, a little drift per drip
        rate = (spot.pitch * self._size_rate(spot.size_mm) * self._tone_rate()
                * math.exp(float(self._rng.uniform(-0.025, 0.025))))
        if len(mono) > 32:
            n_out = max(24, int(round(len(mono) / rate)))
            xq = np.linspace(0.0, len(mono) - 1, n_out)
            mono = np.interp(xq, np.arange(len(mono), dtype=np.float64), mono)
        if spot.surface == "metal":
            mono = self._short_grain(mono, ms=52.0, fade_ms=4.0)
        layer = "sill" if spot.surface == "metal" else "ground"
        amp = (
            self._drip_amp(spot.size_mm, spot.fall_m)
            * loudness_gain(_DRIP_REF_Q) * _DROP_LEVEL
            * math.exp(float(self._rng.normal(0.0, 0.15)))
            * self._master * (mix_k ** 0.85) * _LEVEL_TRIM * _DRIP_GAIN
            * hm.surface_radiate(spot.surface)
        )
        self._evt_id += 1
        fade_n = max(8, int(0.0025 * self.samplerate))
        self._render_and_queue(mono * amp, (spot.x, spot.land_y + 0.02, spot.z), layer,
                               schedule_delay_n=delay_n, fade_n=fade_n)

    def _gutter_points(self) -> List[Tuple[float, float, float]]:
        """Two downspouts on opposite corners."""
        w, d = float(self.room.width), float(self.room.depth)
        return [(-0.15, 0.05, -0.15), (w + 0.15, 0.05, d + 0.15)]

    # ----- dense texture (drops below the discrete split) -----
    def _dense_sources(self) -> Dict[str, Tuple[str, Tuple[str, ...]]]:
        """Dense texture streams: name → (surface, catchment layers it carries)."""
        wall_s = hm.wall_material(self.room).surface
        srcs: Dict[str, Tuple[str, Tuple[str, ...]]] = {
            "roof": (hm.roof_material(self.room).surface, ("roof",)),
            "ground": ("water", ("ground",)),
        }
        if wall_s == "glass":
            srcs["facade"] = ("glass", ("wall", "window", "sill"))
        else:
            srcs["facade"] = (wall_s, ("wall",))
            srcs["glass"] = ("glass", ("window", "sill"))
        return srcs

    def _dense_kernel_key(self) -> tuple:
        q = float(getattr(self.room, "droplet_density", 0.5) or 0.0)
        hit_sharp = max(0.12, min(0.95, self._sharpness() + 0.06 * q))
        return (
            tuple(sorted((k, v[0]) for k, v in self._dense_sources().items())),
            _nearest(SHARP_BINS, hit_sharp),
            int(round(q * 10)),
            _SIZE_PITCH_EXP,
            round(self._tone_rate(), 3),
        )

    def _ensure_dense_kernels(self) -> None:
        """Bank grains made with the same recipe as a single drop (_drop_grain)."""
        key = self._dense_kernel_key()
        if key == self._dense_kernel_id:
            return
        self._drop_bank.ensure_built()
        sources, sharp, _q10, _exp, tone = key
        vrng = np.random.RandomState(4111)
        for source, surface in list(sources) + [("gutter", "water")]:
            grains = []
            for size in SIZE_BINS_MM:
                bank = self._drop_bank.variants(surface, size, sharp) or [
                    self._drop_bank.pick(surface=surface, size_mm=size, sharpness=sharp, chain_n=1)
                ]
                for g in make_variants(bank, VARIANTS_PER_SIZE, vrng, self._size_rate(size) * tone):
                    grains.append(self._shape_grain(g, surface) * hm.surface_radiate(surface))
            self._dense.set_kernels(source, SIZE_BINS_MM, grains)
            self._dense_kernel_gain[source] = hm.surface_radiate(surface)
        self._dense_kernel_id = key

    def _dense_fingerprint(self, visual: bool = False) -> tuple:
        r = self.room
        L = r.listener
        spk = tuple(
            (round(float(s.x), 2), round(float(s.y), 2), round(float(s.z), 2),
             bool(getattr(s, "enabled", True)), round(float(getattr(s, "gain_db", 0.0) or 0.0), 1))
            for s in r.speakers
        )
        wins = tuple(
            (str(getattr(w, "wall", "")), round(float(getattr(w, "offset", 0.0) or 0.0), 2),
             round(float(getattr(w, "open", 0.0) or 0.0), 2), str(getattr(w, "open_style", "")))
            for w in (getattr(r, "windows", []) or [])
        )
        return (
            self._dense_kernel_key()[:2], self._patch_key,
            round(float(L.x), 2), round(float(getattr(L, "y", 1.2)), 2), round(float(L.z), 2),
            round(float(getattr(L, "yaw", 0.0)), 2), spk, wins, self.render_listener_binaural,
        )

    def _cal_layers(self) -> List[str]:
        present = {p.layer for p in self._patches()}
        return [l for l in ("roof", "wall", "window", "sill", "ground") if l in present] + ["gutter"]

    def _measure_layer(self, layer: str, part: Optional[int] = None) -> Dict[str, Dict[object, float]]:
        """Mean energy gain of the full wall/window path and of the 3D view's
        cheap taps, from probe drops on ``layer`` to every speaker and ear.
        ``part`` measures one chunk of the probe spots only (raw sums + "n")."""
        sr = self.samplerate
        main_rng = self._rng
        self._rng = np.random.RandomState(9173 + 31 * len(layer) + 7 * (part or 0))
        full: Dict[object, float] = {}
        cheap: Dict[object, float] = {}
        try:
            L = self.room.listener
            lis = ((float(L.x), float(getattr(L, "y", 1.2)), float(L.z)), float(getattr(L, "yaw", 0.0)))
            recvs = [
                (i, (float(sp.x), float(sp.y), float(sp.z)), _db(float(getattr(sp, "gain_db", 0.0) or 0.0)))
                for i, sp in enumerate(self.room.speakers)
                if getattr(sp, "enabled", True)
            ]
            surface = "water" if layer == "gutter" else self._surface_for_layer(layer)
            probe = self._drop_grain(surface, 2.0, "roof" if layer == "roof" else layer, rng=self._rng)
            e_in = float(np.sum(probe * probe)) + 1e-18
            kind = _LAYER_KIND.get(layer, 1)
            n = 0
            ks = range(_CAL_POINTS) if part is None else range(part * _CAL_CHUNK, (part + 1) * _CAL_CHUNK)
            for k in ks:
                if layer == "gutter":
                    pts = self._gutter_points()
                    src = pts[k % len(pts)]
                else:
                    src = self._point_for_layer(layer)
                    if src is None:
                        break
                n += 1
                spk_bufs, st, _portals = render_drop_to_receivers(
                    self.room, probe, src, recvs, sr,
                    listener=lis if self.render_listener_binaural else None,
                    fast=True, binaural_quality="fast",
                )
                for i, sig in spk_bufs.items():
                    full[i] = full.get(i, 0.0) + float(np.sum(sig * sig))
                if st is not None and st.size:
                    st = st * _HP_PATH_GAIN
                    full["L"] = full.get("L", 0.0) + float(np.sum(st[:, 0] ** 2))
                    full["R"] = full.get("R", 0.0) + float(np.sum(st[:, 1] ** 2))
                for i, sig, _p in self._cheap_taps_for_hit(probe, src, kind):
                    cheap[i] = cheap.get(i, 0.0) + float(np.sum(sig * sig))
                if self.render_listener_binaural:
                    cb = self._cheap_binaural(probe, src, kind)
                    if cb is not None:
                        cheap["L"] = cheap.get("L", 0.0) + float(np.sum(cb[:, 0] ** 2))
                        cheap["R"] = cheap.get("R", 0.0) + float(np.sum(cb[:, 1] ** 2))
            if n == 0:
                return {}
            # Speaker gains swing with how close each probe lands to a speaker;
            # the cheap gain is plain distance math, so average it over many
            # spots ("wide") and scale by the full/cheap ratio of the probes.
            wide: Dict[object, float] = {}
            n_wide = 48 if part is not None else 96
            for _ in range(n_wide):
                src = self._gutter_points()[_ % 2] if layer == "gutter" else self._point_for_layer(layer)
                for i, g, _d in self._cheap_spk_gains(src, kind):
                    wide[i] = wide.get(i, 0.0) + g * g
            if part is not None:
                return {"full": {k: v / e_in for k, v in full.items()},
                        "cheap": {k: v / e_in for k, v in cheap.items()},
                        "wide": {k: v * n / n_wide for k, v in wide.items()}, "n": {"n": float(n)}}
            norm = lambda acc: {k: math.sqrt(v / (n * e_in)) for k, v in acc.items()}
            res = {"full": norm(full), "cheap": norm(cheap)}
            res["wide"] = {k: math.sqrt(v / n_wide) for k, v in wide.items()}
            return _wide_full(res)
        except Exception:
            log.exception("path calibration failed (%s)", layer)
            return {}
        finally:
            self._rng = main_rng

    def _calibrate_paths(self, all_now: bool = False) -> None:
        """Keep the per-layer path calibration current. A change re-measures
        one chunk of probe spots per block (~5 ms) so dragging a speaker never
        stalls the audio; the very first calibration runs at once."""
        fp = self._dense_fingerprint()
        if fp != self._cal_fp and (self._time - self._cal_t) > 0.5:
            self._cal_fp = fp
            self._cal_t = self._time
            n_parts = max(1, _CAL_POINTS // _CAL_CHUNK)
            self._cal_pending = [(l, k) for l in self._cal_layers() for k in range(n_parts)]
            self._cal_new = {}
            all_now = all_now or not self._path_cal
        while self._cal_pending:
            layer, part = self._cal_pending.pop(0)
            got = self._measure_layer(layer, part)
            acc = self._cal_new.setdefault(layer, {"full": {}, "cheap": {}, "wide": {}, "n": {"n": 0.0}})
            for side in ("full", "cheap", "wide"):
                for k, v in got.get(side, {}).items():
                    acc[side][k] = acc[side].get(k, 0.0) + v
            acc["n"]["n"] += got.get("n", {}).get("n", 0.0)
            if not all_now:
                break
        if not self._cal_pending and self._cal_new:
            cal = {}
            for layer, acc in self._cal_new.items():
                n = acc["n"]["n"]
                if n > 0:
                    cal[layer] = _wide_full({side: {k: math.sqrt(v / n) for k, v in acc[side].items()}
                                             for side in ("full", "cheap", "wide")})
            self._path_cal = cal
            self._cal_new = {}

    def _source_gains(self, layers: Sequence[str], shares: Dict[str, float]) -> Dict[object, float]:
        """Energy-weighted full-path gain of a dense stream over its layers."""
        acc: Dict[object, float] = {}
        tot = 0.0
        for layer in layers:
            w = shares.get(layer, 0.0) if layer != "gutter" else 1.0
            full = (self._path_cal.get(layer) or {}).get("full", {})
            if w <= 0.0 or not full:
                continue
            tot += w
            for k, g in full.items():
                acc[k] = acc.get(k, 0.0) + w * g * g
        return {k: math.sqrt(v / tot) for k, v in acc.items()} if tot > 0 else {}

    def _mix_dense(self, frames: int, mix: Dict[int, np.ndarray], visual: bool) -> None:
        """Add the dense drop texture to speakers, roof bus and the ears."""
        if not self.use_dense_texture or frames <= 0:
            return
        self._dense.hit_makeup = _DENSE_HIT_MAKEUP
        q = float(getattr(self.room, "droplet_density", 0.5) or 0.0)
        _n, dist, split = self._rain_state()
        plan = {}
        gains: Dict[str, Dict[object, float]] = {}
        g_flow = self._runoff.gutter_flow if self.use_drips else 0.0
        mix_k = max(0.0, min(2.5, float(getattr(self.room, "mix_drips", 1.0))))
        raining = dist is not None and split > D_MIN_MM
        gutter = g_flow > 0.02 and mix_k > 0.01
        if raining or gutter:
            self._ensure_dense_kernels()
            self._calibrate_paths()
        if gutter:
            # Spill out of the downspouts: small splashes, short fall
            n_spouts = len(self._gutter_points())
            rate = _GUTTER_MAX_RATE * n_spouts * (1.0 - math.exp(-g_flow / _GUTTER_HALF_FLOW))
            _r, g_dist = dsd_for_quantity(0.5)
            k = (loudness_gain(_DRIP_REF_Q) * _DROP_LEVEL * self._master * (mix_k ** 0.85)
                 * _GUTTER_GAIN * _LEVEL_TRIM / self._dense_kernel_gain["gutter"])
            plan["gutter"] = (rate, g_dist, 0.6, 2.5, k)
            gains["gutter"] = self._source_gains(("gutter",), {})
        if raining:
            mix_d = max(0.0, min(2.5, float(getattr(self.room, "mix_droplets", 1.0))))
            # Drizzle: keep the sheet ~6 dB under the ticks; physical by moderate rain.
            trim = 10.0 ** (-6.0 * (1.0 - min(1.0, q / 0.5)) / 20.0)
            k = (loudness_gain(q) * _DROP_LEVEL * self._master * (mix_d ** 0.85) * trim
                 * (0.70 + 0.22 * self._sharpness()) * _LEVEL_TRIM)
            total = dist.flux(D_MIN_MM, split) * self._rain_area()
            shares = self._layer_probs()
            streams = []
            for source, (_surface, layers) in self._dense_sources().items():
                share = sum(shares.get(l, 0.0) for l in layers)
                g = self._source_gains(layers, shares)
                loud = share * (self._dense_kernel_gain.get(source, 0.3) ** 2) * sum(v * v for v in g.values())
                streams.append((loud, source, share, g))
            top = max((s[0] for s in streams), default=0.0)
            for loud, source, share, g in streams:
                # Streams 30 dB under the loudest are masked by it; don't render them
                if share <= 0.0 or not g or loud < 1e-3 * top:
                    continue
                plan[source] = (total * share, dist, D_MIN_MM, split, k)
                gains[source] = g
        n_on = sum(1 for sp in self.room.speakers if getattr(sp, "enabled", True))
        self._dense.set_stems(min(4, max(2, n_on)))
        stems = self._dense.render(frames, plan)
        if not stems:
            return
        n_st = self._dense.n_stems
        for source, st in stems.items():
            g_src = gains.get(source) or self._last_dense_gains.get(source, {})
            self._last_dense_gains[source] = g_src
            for i, sp in enumerate(self.room.speakers):
                g = g_src.get(i, 0.0)
                if g <= 0.0 or not getattr(sp, "enabled", True):
                    continue
                buf = st[i % n_st] * g
                mix[i] = mix[i] + buf if i in mix else buf
            if source == "roof" and not visual:
                rid = WALL_BUS["roof"]
                rb = st[0] * 0.144
                mix[rid] = mix[rid] + rb if rid in mix else rb
            if self._last_binaural is not None and self._last_binaural.shape[0] == frames:
                gl, gr = g_src.get("L", 0.0), g_src.get("R", 0.0)
                if gl > 0.0 or gr > 0.0:
                    self._last_binaural[:, 0] += st[0] * gl
                    self._last_binaural[:, 1] += st[1 % n_st] * gr

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

    def _field_energy_k(self, q: float) -> float:
        """Amplitude of the rain field at ``q`` relative to 35%: √energy × the
        per-drop loudness compression (what the drops themselves follow)."""
        if q <= 0.0005:
            return 0.0
        _r, dist = dsd_for_quantity(q)
        _r0, dist0 = dsd_for_quantity(0.35)
        return math.sqrt(dist.energy() / max(1e-18, dist0.energy())) * loudness_gain(q) / loudness_gain(0.35)

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
            # The far field is the same rain as the drops: its level follows
            # the rain's energy under the same loudness compression, anchored
            # at 35% to the old hand curve (0.0177; ×0.602 under a metal roof,
            # which masks it).
            mass = 0.0177 * self._field_energy_k(q)
            if hm.roof_material(self.room).surface == "metal":
                mass *= 0.602
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
        self._visual_active = visual
        if visual:
            # ~1 s smoothing of how many drawn drops land per second
            inst = len(vis_hits) / max(1e-6, dt)
            a = min(1.0, dt / 1.0)
            self._visual_rate = inst if self._visual_rate <= 0.0 else self._visual_rate + a * (inst - self._visual_rate)
        else:
            self._visual_rate = 0.0
        self._calibrate_paths()
        if visual:
            # The view hands over a block's landings at once. Starting them all
            # on sample 0 put the loudest drops on a 23 Hz grid (a drum roll);
            # spread them across the block like the Poisson scheduler does.
            for h in vis_hits:
                self._spawn_visual_hit(h, delay_n=int(self._rng.randint(0, frames)))
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
        self._update_runoff(frames)
        self._time = t1

        mix = self._mix_speakers(frames)
        # Stash binaural block for headphone path
        self._last_binaural = self._mix_binaural(frames)

        # Drops too many to voice one by one: shot-noise sheet of the same grains
        self._mix_dense(frames, mix, visual)

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
        self._dense.reset(777 + seed)
        self._cal_t = -1e9
        self._runoff.reset(31 + seed)
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
