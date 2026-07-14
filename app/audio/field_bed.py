"""
Outdoor rain field — soft dark under-layer (not the main quantity control).

Quantity must primarily increase *discrete drops* (see SpatialRainEngine._ips).
This module only adds a quiet multi-depth wash so gaps between drops don't
feel empty. It must stay brown/wet — never a white-noise volume knob.
"""

from __future__ import annotations

import logging
import math
import wave
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from scipy import signal as sp_signal
    _HAS_SCIPY = True
except Exception:  # pragma: no cover
    sp_signal = None
    _HAS_SCIPY = False

log = logging.getLogger("audio.field_bed")

PI2 = 2.0 * math.pi

_ASSET_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "assets" / "audio" / "rain",
    Path(__file__).resolve().parents[1] / "assets" / "rain",
)


def default_rain_sample_dir() -> Path:
    for p in _ASSET_CANDIDATES:
        if p.is_dir():
            return p
    return _ASSET_CANDIDATES[0]


def _lp(x: np.ndarray, fc: float, sr: float, state: float) -> Tuple[np.ndarray, float]:
    dt = 1.0 / max(1.0, sr)
    rc = 1.0 / (PI2 * max(20.0, fc))
    a = dt / (rc + dt)
    if _HAS_SCIPY:
        y, zf = sp_signal.lfilter([a], [1.0, -(1.0 - a)], x, zi=[state])
        return y, float(zf[0])
    y = np.empty_like(x)
    s = state
    for i, v in enumerate(x):
        s = s + a * (float(v) - s)
        y[i] = s
    return y, s


def _hp(x: np.ndarray, fc: float, sr: float, hy: float, hx: float) -> Tuple[np.ndarray, float, float]:
    dt = 1.0 / max(1.0, sr)
    rc = 1.0 / (PI2 * max(20.0, fc))
    a = rc / (rc + dt)
    y = np.empty_like(x)
    for i, v in enumerate(x):
        v = float(v)
        hy = a * (hy + v - hx)
        hx = v
        y[i] = hy
    return y, hy, hx


def load_mono_wav(path: Path, target_sr: int) -> Optional[np.ndarray]:
    try:
        with wave.open(str(path), "rb") as w:
            nch = w.getnchannels()
            sw = w.getsampwidth()
            sr0 = w.getframerate()
            nframes = w.getnframes()
            raw = w.readframes(nframes)
    except Exception as e:
        log.warning("Could not read rain sample %s: %s", path, e)
        return None

    if sw == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    elif sw == 3:
        a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        vals = (a[:, 0].astype(np.int32) | (a[:, 1].astype(np.int32) << 8) | (a[:, 2].astype(np.int32) << 16))
        vals = np.where(vals >= 0x800000, vals - 0x1000000, vals)
        data = vals.astype(np.float64) / 8388608.0
    elif sw == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float64) / 2147483648.0
    else:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float64) / 128.0 - 1.0

    if nch > 1:
        data = data.reshape(-1, nch).mean(axis=1)

    if sr0 != target_sr and len(data) > 8:
        n_out = int(round(len(data) * float(target_sr) / float(sr0)))
        if n_out < 8:
            return None
        xp = np.linspace(0.0, 1.0, len(data), endpoint=False)
        xq = np.linspace(0.0, 1.0, n_out, endpoint=False)
        data = np.interp(xq, xp, data)

    pk = float(np.max(np.abs(data)) + 1e-12)
    if pk > 0.05:
        data = data * (0.55 / pk)
    return data.astype(np.float64)


class SampleLoopBank:
    """Loop bank with optional density-tagged samples (soft / med / heavy)."""

    def __init__(
        self,
        samples: Sequence[np.ndarray],
        sr: int,
        seed: int = 9,
        tags: Optional[Sequence[str]] = None,
    ):
        self.sr = int(sr)
        self.samples = [np.asarray(s, dtype=np.float64).reshape(-1) for s in samples if len(s) > 64]
        self.tags = list(tags) if tags is not None else [""] * len(self.samples)
        if len(self.tags) < len(self.samples):
            self.tags.extend([""] * (len(self.samples) - len(self.tags)))
        self.rng = np.random.RandomState(seed)
        self._pos = 0
        self._which = 0
        self._pending: Optional[int] = None
        self._target_dens = 0.5
        if self.samples:
            self._which = int(self.rng.randint(0, len(self.samples)))

    @property
    def ok(self) -> bool:
        return len(self.samples) > 0

    def set_density(self, dens: float) -> None:
        """Pick the sample whose tag best matches rain quantity."""
        self._target_dens = max(0.0, min(1.0, float(dens)))
        if not self.samples:
            return
        # Prefer soft/med/heavy tags when present
        want = "soft" if dens < 0.35 else ("heavy" if dens > 0.70 else "med")
        scored = []
        for i, tag in enumerate(self.tags[: len(self.samples)]):
            t = (tag or "").lower()
            score = 0
            if want in t:
                score = 3
            elif want == "med" and ("medium" in t or "mid" in t):
                score = 3
            elif "rain" in t or "roof" in t or not t:
                score = 1
            scored.append((score, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        best = scored[0][1]
        if best != self._which and scored[0][0] >= 3:
            # Crossfade-friendly: only switch at loop boundary
            self._pending = best

    def render(self, n: int) -> np.ndarray:
        n = int(n)
        if n <= 0 or not self.samples:
            return np.zeros(max(0, n), dtype=np.float64)
        out = np.zeros(n, dtype=np.float64)
        filled = 0
        while filled < n:
            buf = self.samples[self._which]
            remain = len(buf) - self._pos
            take = min(remain, n - filled)
            out[filled : filled + take] = buf[self._pos : self._pos + take]
            self._pos += take
            filled += take
            if self._pos >= len(buf):
                self._pos = 0
                pending = getattr(self, "_pending", None)
                if pending is not None and 0 <= pending < len(self.samples):
                    self._which = int(pending)
                    self._pending = None
                elif len(self.samples) > 1 and self.rng.rand() < 0.12:
                    # Occasional same-density variety
                    self._which = int(self.rng.randint(0, len(self.samples)))
        return out


class OutdoorFieldBed:
    """Soft multi-depth outdoor wash — underlay only, never a white-noise fader."""

    def __init__(self, sr: int = 48000, seed: int = 2024, sample_dir: Optional[Path] = None):
        self.sr = int(sr)
        self.rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
        self.t = 0.0
        self._b = 0.0
        self._p = 0.0
        self._b2 = 0.0
        self._b3 = 0.0
        self._lp_body = 0.0
        self._lp_mid = 0.0
        self._lp_far = 0.0
        self._lp_near = 0.0
        self._lp_can = 0.0
        self._lp_tick = 0.0
        self._hp_y = 0.0
        self._hp_x = 0.0
        # High "sheen" chain (sharpness-driven air / glass tinkle)
        self._lp_sheen = 0.0
        self._lp_sheen2 = 0.0
        self._hp_sheen_y = 0.0
        self._hp_sheen_x = 0.0
        self._lp_spark = 0.0
        self._rms = 0.04

        self.sample_dir = Path(sample_dir) if sample_dir else default_rain_sample_dir()
        self._bank: Optional[SampleLoopBank] = None
        self._sample_wet = 0.0
        self.reload_samples()

    def reload_samples(self) -> int:
        paths: List[Path] = []
        d = self.sample_dir
        if d.is_dir():
            # Case-insensitive FS (Windows): don't double-count *.wav / *.WAV
            seen = set()
            for p in sorted(d.iterdir()):
                if not p.is_file():
                    continue
                if p.suffix.lower() != ".wav":
                    continue
                key = str(p.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                paths.append(p)
        samples = []
        tags: List[str] = []
        for p in paths:
            mono = load_mono_wav(p, self.sr)
            if mono is not None and len(mono) > self.sr // 4:
                samples.append(mono)
                tags.append(p.stem.lower())
                log.info("Loaded rain sample: %s (%.1fs)", p.name, len(mono) / self.sr)
        self._bank = SampleLoopBank(samples, self.sr, seed=11, tags=tags) if samples else None
        # Samples help mid/heavy rain; low qty uses quieter bed so splats lead
        self._sample_wet = 0.62 if self._bank and self._bank.ok else 0.0
        return len(samples)

    def reset(self, seed: int = 2024):
        self.__init__(self.sr, seed, self.sample_dir)

    def _brown(self, white: np.ndarray, state: float, a: float = 0.990) -> Tuple[np.ndarray, float]:
        if _HAS_SCIPY:
            y, zf = sp_signal.lfilter([1.0 - a], [1.0, -a], white, zi=[state])
            return y, float(zf[0])
        y = np.empty_like(white)
        s = state
        for i, w in enumerate(white):
            s = a * s + (1.0 - a) * w
            y[i] = s
        return y, s

    def render_layers(
        self,
        n: int,
        quantity: float = 0.5,
        sharpness: float = 0.35,
        level: float = 0.06,
        wall_tone: float = 0.45,
    ) -> Dict[str, np.ndarray]:
        """Soft outdoor layers. Quantity only gently fills the underlay.

        Does **not** turn into white noise at high quantity — stays dark/wet.
        Sharpness gently opens the top of the bed (still capped low).
        wall_tone 0..1: subtle house-wall colouring of the brown wash
          (brick darker, glass/metal slightly more open) — small shift only.
        """
        n = int(n)
        empty = {
            "near": np.zeros(max(0, n), dtype=np.float64),
            "mid": np.zeros(max(0, n), dtype=np.float64),
            "far": np.zeros(max(0, n), dtype=np.float64),
            "canopy": np.zeros(max(0, n), dtype=np.float64),
            "mix": np.zeros(max(0, n), dtype=np.float64),
        }
        if n <= 0:
            return empty

        q = max(0.0, min(1.0, float(quantity)))
        sh = max(0.0, min(1.0, float(sharpness)))
        wt = max(0.0, min(1.0, float(wall_tone)))
        if q < 0.008 or level <= 0:
            self.t += n / max(1, self.sr)
            return empty

        sr = self.sr
        # dens drives bed mass with Quantity (0 → almost off, 1 → full wash)
        dens = q ** 0.80

        w1 = self.rng.randn(n).astype(np.float64)
        w2 = self.rng.randn(n).astype(np.float64)
        w3 = self.rng.randn(n).astype(np.float64)

        # Brown "pitch" from sharpness + subtle wall tone (not a big jump).
        # a↑ → darker. wall_tone↑ → slightly thinner brown (glass/metal).
        a_soft = 0.996 - 0.010 * sh - 0.004 * (wt - 0.45)
        a_soft = max(0.985, min(0.997, a_soft))
        a_far = 0.997 - 0.008 * sh - 0.003 * (wt - 0.45)
        a_far = max(0.988, min(0.998, a_far))
        brown, self._b = self._brown(w1, self._b, a_soft)
        brown2, self._b2 = self._brown(w2, self._b2, a_soft - 0.002)
        far_b, self._b3 = self._brown(w3, self._b3, a_far)

        # Band pitch: sharpness primary, wall_tone a gentle nudge (~±15%)
        wall_k = 0.88 + 0.28 * wt
        body_fc = (420.0 + 1200.0 * sh) * wall_k       # stay wetter overall
        mid_fc = (750.0 + 1800.0 * sh) * wall_k
        body, self._lp_body = _lp(0.80 * brown + 0.20 * brown2, body_fc, sr, self._lp_body)
        mid_src, self._lp_mid = _lp(0.55 * brown + 0.45 * brown2, mid_fc, sr, self._lp_mid)

        # --- High sheen: sharp higher pitch mixed into the wash (not white static) ---
        # Band-limited pink air ~2–7 kHz + sparse glass sparkles. Scales with Sharpness.
        w_hi = self.rng.randn(n).astype(np.float64)
        a_p = 0.97
        if _HAS_SCIPY:
            pink_acc, zf = sp_signal.lfilter(
                [1.0 - a_p], [1.0, -a_p], w_hi, zi=[self._p]
            )
            self._p = float(zf[0])
            pink_hi = 0.55 * pink_acc + 0.45 * w_hi
        else:
            pink_hi = np.empty_like(w_hi)
            pacc = self._p
            for i, wv in enumerate(w_hi):
                pacc = a_p * pacc + (1.0 - a_p) * wv
                pink_hi[i] = 0.55 * pacc + 0.45 * wv
            self._p = pacc
        # High-pass into the "air" band, then gentle top cut so it doesn't hiss
        # Wall tone slightly opens air; keep modest so wash isn't plastic
        sheen_hp = 1400.0 + 700.0 * sh + 150.0 * wt
        sheen_lp = 3200.0 + 2200.0 * sh + 400.0 * wt
        sheen, self._hp_sheen_y, self._hp_sheen_x = _hp(
            pink_hi, sheen_hp, sr, self._hp_sheen_y, self._hp_sheen_x
        )
        sheen, self._lp_sheen = _lp(sheen, sheen_lp, sr, self._lp_sheen)
        sheen, self._lp_sheen2 = _lp(sheen, sheen_lp * 0.92, sr, self._lp_sheen2)
        # Sheen tracks density; wall_tone a small gain nudge only
        sheen_g = (0.010 + 0.12 * dens) * (0.08 + 0.75 * (sh ** 0.85)) * (0.90 + 0.18 * wt)

        # Sparse high sparkles — fewer at low dens so wet notes stay clear
        spark_rate = 1.0 + 40.0 * dens * (0.15 + 0.85 * sh) + 18.0 * sh * dens
        p_spark = min(0.03, spark_rate / sr)
        spark_mask = self.rng.rand(n) < p_spark
        sparks = np.zeros(n, dtype=np.float64)
        ns = int(spark_mask.sum())
        if ns:
            sparks[spark_mask] = self.rng.randn(ns) * self.rng.uniform(0.12, 0.45, size=ns)
        spark_lp = 3500.0 + 4000.0 * sh
        sparks, self._lp_spark = _lp(sparks, spark_lp, sr, self._lp_spark)
        sparks, self._hp_y, self._hp_x = _hp(sparks, 1800.0 + 600.0 * sh, sr, self._hp_y, self._hp_x)
        spark_g = (0.012 + 0.11 * dens) * (0.08 + 0.92 * (sh ** 1.1))

        high_mix = sheen_g * sheen + spark_g * sparks

        # Soft mid-band patter only when no samples (samples already textured)
        has_samples = bool(self._bank and self._bank.ok and self._sample_wet > 0.02)
        if has_samples:
            ticks = np.zeros(n, dtype=np.float64)
        else:
            tick_rate = 4.0 + 80.0 * dens + 40.0 * dens * sh
            p_tick = min(0.05, tick_rate / sr)
            mask = self.rng.rand(n) < p_tick
            ticks = np.zeros(n, dtype=np.float64)
            nt = int(mask.sum())
            if nt:
                ticks[mask] = self.rng.randn(nt) * self.rng.uniform(0.08, 0.32, size=nt)
            tick_fc = 700.0 + 1800.0 * sh
            ticks, self._lp_tick = _lp(ticks, tick_fc, sr, self._lp_tick)

        # Core: brown body + high sheen (sheen is the sharp pitch the ear wants)
        tick_g = 0.0 if has_samples else (0.03 + 0.10 * dens)
        core = (
            (0.72 + 0.08 * dens) * body
            + (0.12 + 0.06 * dens) * mid_src
            + tick_g * ticks
            + high_mix
        )

        # Depth colouring — near keeps more sheen; far stays darker
        near = core + (0.0 if has_samples else (0.04 + 0.08 * dens)) * ticks + 0.35 * high_mix
        near, self._lp_near = _lp(near, 1800.0 + 4200.0 * sh, sr, self._lp_near)

        mid = 0.78 * core + 0.18 * brown2 + 0.55 * high_mix
        if has_samples:
            self._bank.set_density(dens)
            samp = self._bank.render(n)
            # Open sample top with sharpness so loops don't stay dull
            samp, _ = _lp(samp, 3600.0 + 4200.0 * sh, sr, 0.0)
            w = self._sample_wet * (0.50 + 0.38 * dens)
            w = min(0.88, w)  # leave room for live sheen
            mid = (1.0 - w) * mid + w * samp + 0.45 * high_mix
            near = (1.0 - 0.50 * w) * near + 0.50 * w * samp + 0.25 * high_mix
            far_src = samp
        else:
            far_src = body

        far = (0.55 + 0.20 * dens) * far_b + (0.45 - 0.10 * dens) * far_src + 0.12 * high_mix
        far, self._lp_far = _lp(far, 500.0 + 1100.0 * sh, sr, self._lp_far)

        canopy = (
            0.55 * mid_src
            + (0.0 if has_samples else 0.22) * ticks
            + (0.25 if has_samples else 0.0) * mid
            + 0.70 * high_mix
        )
        canopy, self._lp_can = _lp(canopy, 1400.0 + 3600.0 * sh, sr, self._lp_can)

        # Slow weather AM
        t0 = self.t
        t = t0 + np.arange(n, dtype=np.float64) / sr
        am = 0.92 + 0.08 * np.sin(PI2 * 0.15 * t + 0.4)
        am *= 0.96 + 0.04 * np.sin(PI2 * 0.37 * t + 1.5)
        near *= am
        mid *= am
        far *= 0.94 + 0.06 * np.sin(PI2 * 0.09 * t + 2.0)
        canopy *= 0.90 + 0.10 * np.sin(PI2 * 0.27 * t + 0.8)

        # Depth gains scale with Quantity (more mid/far body in heavy rain)
        g_near = 0.22 + 0.55 * dens
        g_mid = 0.28 + 0.72 * dens
        g_far = 0.18 + 0.85 * dens
        g_can = 0.10 + 0.40 * dens
        near *= g_near
        mid *= g_mid
        far *= g_far
        canopy *= g_can

        mix = 0.26 * near + 0.40 * mid + 0.26 * far + 0.08 * canopy

        # Wash stays under wet key notes; low dens = almost silent bed
        dens_scale = 0.12 + 0.95 * dens if has_samples else (0.08 + 0.90 * dens)
        target = float(level) * dens_scale * (0.94 + 0.06 * sh)
        target = max(0.0015, min(0.18, target))
        rms = float(np.sqrt(np.mean(mix * mix)) + 1e-12)
        self._rms = 0.92 * self._rms + 0.08 * rms
        scale = target / max(1e-6, self._rms)
        near *= scale
        mid *= scale
        far *= scale
        canopy *= scale
        mix *= scale

        pk = float(np.max(np.abs(mix)) + 1e-12)
        if pk > 0.30:
            s = 0.30 / pk
            near *= s
            mid *= s
            far *= s
            canopy *= s
            mix *= s

        self.t += n / max(1, self.sr)
        return {"near": near, "mid": mid, "far": far, "canopy": canopy, "mix": mix}

    def render(
        self,
        n: int,
        quantity: float = 0.5,
        sharpness: float = 0.35,
        level: float = 0.06,
        wall_tone: float = 0.45,
    ) -> np.ndarray:
        return self.render_layers(n, quantity, sharpness, level, wall_tone=wall_tone)["mix"]
