"""
Pre-rendered rain-hit sample bank.

Synthesizing every discrete drop live is expensive at high quantity and
causes voice thrash / crackle. This bank:

  • builds mono grains once (surface × size × sharpness × variants)
  • packs multi-hit **chains** (several grains with micro-delays) so one
    spatialized voice can stand in for many physical impacts
  • serves O(1) lookups + a cheap gain/rate tweak at play time

The continuous outdoor wash stays in field_bed; this bank is for accents.

The Ring / Wetness / Softness sliders are baked into the grains, so a tone
change means a new bank. ToneBankSwitcher builds it off the audio thread
and hands it over between blocks.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.audio.engine import synth_drop
from app.models.room import (
    TONE_RING_RECOMMENDED,
    TONE_SOFT_RECOMMENDED,
    TONE_WET_RECOMMENDED,
)

log = logging.getLogger("audio.drop_bank")

# Surfaces we pre-bake (maps from spatial_engine material names)
BANK_SURFACES: Tuple[str, ...] = (
    "water",
    "shingle",
    "tile",
    "wood",
    "brick",
    "glass",
    "shell",
    "metal",
)
BANK_VERSION = 9
SIZE_BINS_MM: Tuple[float, ...] = (0.9, 1.8, 3.0, 4.5)
SHARP_BINS: Tuple[float, ...] = (0.18, 0.40, 0.65, 0.88)
# Chain length = how many micro-hits are baked into one playable grain
CHAIN_BINS: Tuple[int, ...] = (1, 2, 4, 7, 11)
VARIANTS = 3

# (ring, wet, soft) the bank was voiced at; slider moves are offsets from here.
TONE_DEFAULT: Tuple[float, float, float] = (
    TONE_RING_RECOMMENDED,
    TONE_WET_RECOMMENDED,
    TONE_SOFT_RECOMMENDED,
)
TONE_STEP = 0.05

# Per surface: synth value at the default slider and how far one slider unit
# moves it. Metal ring is tiny on purpose (synth caps tin at 0.08 — the
# "jewelry" ping); glass and the wet surfaces may ring more.
_TONE_MAP = {
    #        ring (base, k)   wet (base, k)   soft (base, k)
    "metal": ((0.0, 0.12), (0.62, 0.90), (0.28, 0.60)),
    "glass": ((0.06, 0.35), (0.52, 0.80), (0.22, 0.40)),
}
# Same caps synth_drop applies; kept here so the bank key says what you hear
_RING_CAP = {"metal": 0.08, "glass": 0.30}

_bank_gen = itertools.count(1)


def quantize_tone(ring: float, wet: float, soft: float) -> Tuple[float, float, float]:
    """Snap to 0.05 steps so a slider drag only builds a handful of banks."""
    def q(v: float) -> float:
        v = max(0.0, min(1.0, float(v)))
        return round(round(v / TONE_STEP) * TONE_STEP, 2)
    return (q(ring), q(wet), q(soft))


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _nearest(bins: Sequence[float], value: float) -> float:
    best = bins[0]
    best_d = abs(float(value) - best)
    for b in bins[1:]:
        d = abs(float(value) - float(b))
        if d < best_d:
            best, best_d = float(b), d
    return float(best)


def _nearest_int(bins: Sequence[int], value: int) -> int:
    best = bins[0]
    best_d = abs(int(value) - best)
    for b in bins[1:]:
        d = abs(int(value) - int(b))
        if d < best_d:
            best, best_d = int(b), d
    return int(best)


def _norm_surface(name: Optional[str]) -> str:
    s = str(name or "water").lower().strip()
    if s in BANK_SURFACES:
        return s
    if "shingle" in s or "asphalt" in s:
        return "shingle"
    if "tile" in s:
        return "tile"
    if "wood" in s:
        return "wood"
    if "brick" in s or "concrete" in s or "stone" in s:
        return "brick"
    if "glass" in s:
        return "glass"
    if "tin" in s or "metal" in s:
        return "metal"
    if "shell" in s or "tarp" in s or "plastic" in s or "hollow" in s:
        return "shell"
    return "water"


def surface_tone_kw(surface: str, sharpness: float, tone: Optional[Sequence[float]] = None) -> dict:
    """synth_drop tone_* kwargs for one surface at slider ``tone``.

    At TONE_DEFAULT this is exactly what the bank always used (fixed metal
    and glass values, synth defaults elsewhere), so default grains don't move.
    """
    tone = TONE_DEFAULT if tone is None else tuple(float(t) for t in tone)
    dr, dw, ds = (tone[i] - TONE_DEFAULT[i] for i in range(3))
    surf = _norm_surface(surface)
    if surf in _TONE_MAP:
        (rb, rk), (wb, wk), (sb, sk) = _TONE_MAP[surf]
        return {
            "tone_ring": rb if dr == 0 else min(_RING_CAP[surf], _clamp01(rb + rk * dr)),
            "tone_wet": wb if dw == 0 else _clamp01(wb + wk * dw),
            "tone_soft": sb if ds == 0 else _clamp01(sb + sk * ds),
        }
    # Wet-spit / hollow surfaces: the slider maps 1:1 onto the synth default
    # (whose wetness leans on sharpness). Untouched sliders → synth defaults.
    kw = {}
    if dr != 0:
        kw["tone_ring"] = _clamp01(0.3 + dr)
    if dw != 0:
        sh = max(0.0, min(1.0, float(sharpness)))
        kw["tone_wet"] = max(0.2, min(1.0, 0.85 + 0.12 * (1.0 - sh) + dw))
    if ds != 0:
        kw["tone_soft"] = _clamp01(0.55 + ds)
    return kw


class DropSampleBank:
    """Thread-safe pre-baked mono hit + chain database."""

    def __init__(
        self,
        samplerate: int = 48000,
        seed: int = 2026,
        tone: Optional[Sequence[float]] = None,
    ):
        self.samplerate = int(samplerate)
        self._seed = int(seed)
        self.tone = quantize_tone(*(TONE_DEFAULT if tone is None else tone))
        # Unique per bank: dense kernels key on it so they rebuild on a swap
        self.generation = next(_bank_gen)
        self.build_s = 0.0
        # Called between grains. A background build waits here for the audio
        # thread to finish its block: both are GIL-bound Python, so building
        # *during* a block roughly doubles that block's time.
        self.pace: Optional[Callable[[], None]] = None
        self._lock = threading.Lock()
        self._built = False
        self._version = 0
        # key → list of mono float64 arrays
        self._grains: Dict[Tuple[str, float, float, int], List[np.ndarray]] = {}
        # singles only, used to assemble chains on demand if missing
        self._singles: Dict[Tuple[str, float, float], List[np.ndarray]] = {}

    @property
    def ready(self) -> bool:
        return self._built

    def _surface_kw(self, surface: str, sharpness: float) -> dict:
        return surface_tone_kw(surface, sharpness, self.tone)

    def ensure_built(self, progress_cb=None) -> None:
        """Build the full bank once (safe to call from any thread)."""
        if self._built and self._version == BANK_VERSION:
            return
        with self._lock:
            if self._built and self._version == BANK_VERSION:
                return
            t_build = time.perf_counter()
            self._grains = {}
            self._singles = {}
            self._built = False
            log.info(
                "Building drop sample bank (surfaces=%d sizes=%d sharp=%d chains=%d vars=%d)…",
                len(BANK_SURFACES),
                len(SIZE_BINS_MM),
                len(SHARP_BINS),
                len(CHAIN_BINS),
                VARIANTS,
            )
            seed = self._seed
            total = (
                len(BANK_SURFACES)
                * len(SIZE_BINS_MM)
                * len(SHARP_BINS)
                * VARIANTS
            )
            done = 0
            # 1) singles
            for surf in BANK_SURFACES:
                for size in SIZE_BINS_MM:
                    for sharp in SHARP_BINS:
                        key_s = (surf, float(size), float(sharp))
                        variants: List[np.ndarray] = []
                        for v in range(VARIANTS):
                            kw = self._surface_kw(surf, float(sharp))
                            if self.pace is not None:
                                self.pace()
                            mono = synth_drop(
                                sr=self.samplerate,
                                surface=surf,
                                size_mm=float(size),
                                seed=seed + done * 17 + v * 91,
                                sharpness=float(sharp),
                                **kw,
                            )
                            mono = np.asarray(mono, dtype=np.float64).reshape(-1)
                            # Normalize peak so mixing chains is predictable
                            pk = float(np.max(np.abs(mono)) + 1e-12)
                            if pk > 1e-9:
                                mono = mono * (0.22 / pk)
                            variants.append(mono)
                            done += 1
                            if progress_cb and done % 8 == 0:
                                try:
                                    progress_cb(done, total)
                                except Exception:
                                    pass
                        self._singles[key_s] = variants
            # 2) chains from singles
            rng = np.random.RandomState(self._seed + 99)
            for surf in BANK_SURFACES:
                for size in SIZE_BINS_MM:
                    for sharp in SHARP_BINS:
                        singles = self._singles[(surf, float(size), float(sharp))]
                        if self.pace is not None:
                            self.pace()
                        for chain_n in CHAIN_BINS:
                            key = (surf, float(size), float(sharp), int(chain_n))
                            packed: List[np.ndarray] = []
                            for v in range(VARIANTS):
                                packed.append(
                                    self._pack_chain(
                                        singles,
                                        chain_n=int(chain_n),
                                        rng=rng,
                                        variant=v,
                                    )
                                )
                            self._grains[key] = packed
            self._built = True
            self._version = BANK_VERSION
            self.build_s = time.perf_counter() - t_build
            n_keys = len(self._grains)
            log.info(
                "Drop sample bank ready: %d keys × %d variants, tone=%s, %.2f s",
                n_keys, VARIANTS, self.tone, self.build_s,
            )

    def _pack_chain(
        self,
        singles: List[np.ndarray],
        chain_n: int,
        rng: np.random.RandomState,
        variant: int,
    ) -> np.ndarray:
        """Stack chain_n micro-hits with jittered delays into one mono buffer."""
        n_hits = max(1, int(chain_n))
        if n_hits == 1:
            g = singles[variant % len(singles)]
            return np.array(g, copy=True)

        sr = self.samplerate
        # Sequential pitter-patter: attacks separated so a chain is drops, not hiss.
        # Slight tail overlap keeps it wet.
        min_gap = max(int(sr * 0.026), 64)  # ~26 ms between attacks
        max_single = max(len(s) for s in singles)
        out = np.zeros(min_gap * n_hits + max_single + int(0.04 * sr), dtype=np.float64)
        cursor = int(rng.randint(0, max(1, int(0.006 * sr))))
        for i in range(n_hits):
            grain = singles[int(rng.randint(0, len(singles)))]
            rate = float(rng.uniform(0.94, 1.06))
            if abs(rate - 1.0) > 0.008 and len(grain) > 24:
                n_out = max(16, int(round(len(grain) / rate)))
                xp = np.arange(len(grain), dtype=np.float64)
                xq = np.linspace(0.0, len(grain) - 1, n_out)
                g = np.interp(xq, xp, grain).astype(np.float64)
            else:
                g = grain
            amp = (1.0 if i == 0 else float(rng.uniform(0.50, 0.88))) * float(
                rng.uniform(0.88, 1.06)
            )
            pos = int(cursor)
            end = pos + len(g)
            if end > len(out):
                out = np.pad(out, (0, end - len(out)))
            out[pos:end] += g * amp
            # Next attack after this one, with a little jitter — tails may kiss
            cursor = pos + min_gap + int(rng.uniform(0.0, 0.012 * sr))

        # Soft edges so chains don't click when looped into the mixer
        edge = min(int(0.004 * sr), max(4, len(out) // 20))
        if edge > 1:
            w = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, edge))
            out[:edge] *= w
            out[-edge:] *= w[::-1]
        pk = float(np.max(np.abs(out)) + 1e-12)
        if pk > 0.28:
            out *= 0.28 / pk
        return out

    def pick(
        self,
        surface: str,
        size_mm: float,
        sharpness: float,
        chain_n: int = 1,
        rng: Optional[np.random.RandomState] = None,
    ) -> np.ndarray:
        """Return a mono buffer (copy-safe) for the nearest bank entry."""
        self.ensure_built()
        surf = _norm_surface(surface)
        size = _nearest(SIZE_BINS_MM, float(size_mm))
        sharp = _nearest(SHARP_BINS, float(sharpness))
        chain = _nearest_int(CHAIN_BINS, max(1, int(chain_n)))
        key = (surf, size, sharp, chain)
        variants = self._grains.get(key)
        if not variants:
            # Fallback: live synth single (should be rare)
            return synth_drop(
                sr=self.samplerate,
                surface=surf,
                size_mm=size,
                seed=int(rng.randint(0, 1_000_000)) if rng is not None else 0,
                sharpness=sharp,
                **self._surface_kw(surf, sharp),
            )
        if rng is None:
            idx = 0
        else:
            idx = int(rng.randint(0, len(variants)))
        # Copy so callers can scale in place without mutating bank
        return np.array(variants[idx], copy=True)

    def variants(self, surface: str, size_mm: float, sharpness: float) -> List[np.ndarray]:
        """Every stored single-hit variant for the nearest bank entry (copies)."""
        self.ensure_built()
        key = (_norm_surface(surface), _nearest(SIZE_BINS_MM, float(size_mm)),
               _nearest(SHARP_BINS, float(sharpness)), 1)
        return [np.array(g, copy=True) for g in self._grains.get(key, [])]

    def stats(self) -> dict:
        return {
            "ready": self._built,
            "keys": len(self._grains),
            "variants": VARIANTS,
            "surfaces": list(BANK_SURFACES),
            "chains": list(CHAIN_BINS),
            "sr": self.samplerate,
            "tone": self.tone,
            "generation": self.generation,
            "build_s": round(self.build_s, 3),
        }


class ToneBankSwitcher:
    """Follows the tone sliders with a fresh bank, never on the audio thread.

    poll() runs once per audio block: it debounces slider drags, starts one
    background build at a time, and returns a bank only once it is fully
    built, so the caller swaps it in between blocks. Voices already playing
    hold their own buffers, so a swap never cuts a sound off.
    """

    DEBOUNCE_S = 0.4
    CACHE = 3
    RETRY_S = 30.0      # wait before retrying a tone whose build threw

    def __init__(self, samplerate: int = 48000, seed: int = 2026):
        self.samplerate = int(samplerate)
        self._seed = int(seed)
        self._lock = threading.Lock()
        self._cache: "OrderedDict[Tuple[float, float, float], DropSampleBank]" = OrderedDict()
        self._want: Optional[Tuple[float, float, float]] = None
        self._want_t = 0.0
        self._building: Optional[Tuple[float, float, float]] = None
        self._failed: Optional[Tuple[float, float, float]] = None
        self._failed_t: Optional[float] = None
        self._thread: Optional[threading.Thread] = None
        self.last_build_s = 0.0
        self.swaps = 0
        # Set while no audio block is rendering; the builder only runs then
        self._audio_idle = threading.Event()
        self._audio_idle.set()

    # Audio thread brackets each block with these (cheap Event flips)
    def block_begin(self) -> None:
        self._audio_idle.clear()

    def block_end(self) -> None:
        self._audio_idle.set()

    def _wait_audio_idle(self) -> None:
        # Timeout so back-to-back offline renders still let the build finish
        self._audio_idle.wait(0.05)

    def make_bank(self, tone: Sequence[float]) -> DropSampleBank:
        """An unbuilt bank for ``tone`` (the startup bank builds lazily, as before)."""
        bank = DropSampleBank(samplerate=self.samplerate, seed=self._seed, tone=tone)
        self._remember(bank)
        return bank

    def _remember(self, bank: DropSampleBank) -> None:
        with self._lock:
            self._cache[bank.tone] = bank
            self._cache.move_to_end(bank.tone)
            while len(self._cache) > self.CACHE:
                self._cache.popitem(last=False)

    def busy(self) -> bool:
        return self._building is not None

    def poll(self, tone: Sequence[float], now: float, current: DropSampleBank) -> Optional[DropSampleBank]:
        """Cheap per-block check. Returns a built bank to swap in, else None."""
        q = quantize_tone(*tone)
        if q == current.tone:
            self._want = None
            return None
        if q != self._want or float(now) < self._want_t:
            # Slider still moving (or the sim clock was reset): restart the debounce
            self._want, self._want_t = q, float(now)
            return None
        if float(now) - self._want_t < self.DEBOUNCE_S:
            return None
        with self._lock:
            ready = self._cache.get(q)
            if ready is not None and ready.ready:
                self._cache.move_to_end(q)
                self._want = None
                self.swaps += 1
                return ready
        if self._failed == q:
            # A build that throws would otherwise restart every block, forever.
            if self._failed_t is None or float(now) < self._failed_t:
                self._failed_t = float(now)
            if float(now) - self._failed_t < self.RETRY_S:
                return None
            self._failed, self._failed_t = None, None
        if self._building is None:
            self._start_build(q)
        return None

    def bank_for(self, tone: Sequence[float]) -> DropSampleBank:
        """The cached bank for ``tone`` (built or not), else a new unbuilt one.
        Used at stream start so the first block already has the right tone."""
        q = quantize_tone(*tone)
        with self._lock:
            bank = self._cache.get(q)
            if bank is not None:
                self._cache.move_to_end(q)
                return bank
        return self.make_bank(q)

    def _start_build(self, tone: Tuple[float, float, float]) -> None:
        self._building = tone
        bank = DropSampleBank(samplerate=self.samplerate, seed=self._seed, tone=tone)
        bank.pace = self._wait_audio_idle

        def run() -> None:
            try:
                bank.ensure_built()
                self.last_build_s = bank.build_s
                self._remember(bank)
            except Exception:
                log.exception("Tone bank build failed for %s", tone)
                self._failed, self._failed_t = tone, None
            finally:
                self._building = None

        self._thread = threading.Thread(target=run, name="drop-bank-tone", daemon=True)
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)
