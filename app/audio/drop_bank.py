"""
Pre-rendered rain-hit sample bank.

Synthesizing every discrete drop live is expensive at high quantity and
causes voice thrash / crackle. This bank:

  • builds mono grains once (surface × size × sharpness × variants)
  • packs multi-hit **chains** (several grains with micro-delays) so one
    spatialized voice can stand in for many physical impacts
  • serves O(1) lookups + a cheap gain/rate tweak at play time

The continuous outdoor wash stays in field_bed; this bank is for accents.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.audio.engine import synth_drop

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


class DropSampleBank:
    """Thread-safe pre-baked mono hit + chain database."""

    def __init__(self, samplerate: int = 48000, seed: int = 2026):
        self.samplerate = int(samplerate)
        self._seed = int(seed)
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

    def ensure_built(self, progress_cb=None) -> None:
        """Build the full bank once (safe to call from any thread)."""
        if self._built and self._version == BANK_VERSION:
            return
        with self._lock:
            if self._built and self._version == BANK_VERSION:
                return
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
                            kw = {}
                            if surf == "metal":
                                kw["tone_ring"] = 0.0
                                kw["tone_soft"] = 0.28
                                kw["tone_wet"] = 0.62
                            elif surf == "glass":
                                kw["tone_ring"] = 0.06
                                kw["tone_soft"] = 0.22
                                kw["tone_wet"] = 0.52
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
            n_keys = len(self._grains)
            log.info("Drop sample bank ready: %d keys × %d variants", n_keys, VARIANTS)

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
            )
        if rng is None:
            idx = 0
        else:
            idx = int(rng.randint(0, len(variants)))
        # Copy so callers can scale in place without mutating bank
        return np.array(variants[idx], copy=True)

    def stats(self) -> dict:
        return {
            "ready": self._built,
            "keys": len(self._grains),
            "variants": VARIANTS,
            "surfaces": list(BANK_SURFACES),
            "chains": list(CHAIN_BINS),
            "sr": self.samplerate,
        }
