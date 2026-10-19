"""
Dense rain texture — the drops too many to spatialize one by one.

A real downpour lands ~10⁵ drops/s on a house. Discrete voices carry the
biggest ~150/s; everything smaller is rendered here as shot noise: a Poisson
impulse train (one impulse per drop, amplitude from the drop-size law)
convolved with the *same* surface grains the discrete voices use.

That is "white noise without white noise": its texture is tin/glass/puddle
grains, and because drop amplitudes are heavy-tailed the sheet keeps
transients instead of collapsing into hiss.

Each stem is an independent realization of the same field, so speakers and
ears fed different stems hear a diffuse sheet, not a phantom centre.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import scipy.fft as _fft

    def _rfft(x, n):
        return _fft.rfft(x, n=n, axis=-1, workers=-1)

    def _irfft(x, n):
        return _fft.irfft(x, n=n, axis=-1, workers=-1)

    def _fast_len(n: int) -> int:
        return int(_fft.next_fast_len(int(n), real=True))
except Exception:  # pragma: no cover
    def _rfft(x, n):
        return np.fft.rfft(x, n=n, axis=-1)

    def _irfft(x, n):
        return np.fft.irfft(x, n=n, axis=-1)

    def _fast_len(n: int) -> int:
        return 1 << int(np.ceil(np.log2(max(2, int(n)))))

from app.audio.rain_dsd import DropSizeDist, impact_amp

# Past a few thousand grains/s per stem the grains stop being separable and
# the sum is Gaussian noise (the "hiss" failure). Above the cap, play fewer
# grains and raise each one so the energy still matches the physical count.
DEFAULT_MAX_GRAINS_PER_SEC = 2000.0

# Thousands of copies of one grain/s sum to that grain's *exact* spectrum: its
# random noise peaks become fixed resonances (the "hollow bucket"). Each size
# bin gets this many pitch-shifted variants so the peaks average out.
VARIANTS_PER_SIZE = 6
_VARIANT_SPREAD = 0.11  # ± playback-rate range across variants (log)


def make_variants(
    grains: Sequence[np.ndarray], n: int, rng: np.random.RandomState, base_rate: float = 1.0
) -> List[np.ndarray]:
    """n resampled variants cycling through ``grains``, spread evenly in pitch
    around ``base_rate``."""
    src = [np.asarray(g, dtype=np.float64).reshape(-1) for g in grains if len(g)]
    if not src:
        return []
    rates = float(base_rate) * np.exp(np.linspace(-_VARIANT_SPREAD, _VARIANT_SPREAD, max(1, n)))
    rng.shuffle(rates)
    out = []
    for i, rate in enumerate(rates):
        g = src[i % len(src)]
        n_out = max(16, int(round(len(g) / rate)))
        xq = np.linspace(0.0, len(g) - 1, n_out)
        v = np.interp(xq, np.arange(len(g), dtype=np.float64), g)
        # Same energy as the source grain so the variant choice is level-neutral
        v *= np.sqrt(np.sum(g * g) / max(1e-18, np.sum(v * v)))
        out.append(v)
    return out


class DenseRainTexture:
    """Per-source shot-noise stems, block-rendered with FFT convolution."""

    def __init__(
        self,
        sr: int = 48000,
        n_stems: int = 3,
        seed: int = 777,
        max_grains_per_sec: float = DEFAULT_MAX_GRAINS_PER_SEC,
    ):
        self.sr = int(sr)
        self.n_stems = max(1, int(n_stems))
        self.max_grains_per_sec = float(max_grains_per_sec)
        # Above the cap, the share of the missing grains' energy put back as
        # louder grains (1) or as a continuous roar of the same grains (0).
        self.hit_makeup = 1.0
        self.rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
        self._bed_rng = np.random.default_rng(int(seed) + 1)
        # source → (sizes_mm[S], kernels[S * V]) — V variants per size, bin-major
        self._kernels: Dict[str, Tuple[np.ndarray, List[np.ndarray]]] = {}
        self._kfft: Dict[Tuple[str, int], Tuple[int, np.ndarray]] = {}
        # (source, stem) → convolution tail carried into the next block
        self._tails: Dict[Tuple[str, int], np.ndarray] = {}

    def reset(self, seed: Optional[int] = None) -> None:
        if seed is not None:
            self.rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
        self._tails = {}

    def set_stems(self, n: int) -> None:
        n = max(1, int(n))
        if n != self.n_stems:
            self.n_stems = n
            self._tails = {}

    def set_kernels(self, source: str, sizes_mm: Sequence[float], kernels: Sequence[np.ndarray]) -> None:
        """Grains for one source: len(sizes) bins (ascending), each with the
        same number of variants, bin-major. One grain per bin is fine too."""
        sizes = np.asarray(sizes_mm, dtype=np.float64)
        ks = [np.asarray(k, dtype=np.float64).reshape(-1) for k in kernels]
        if len(sizes) == 0 or len(ks) % len(sizes):
            raise ValueError("kernels must be a whole number of variants per size")
        self._kernels[source] = (sizes, ks)
        for key in [k for k in self._kfft if k[0] == source]:
            del self._kfft[key]

    def has_source(self, source: str) -> bool:
        return source in self._kernels

    def _kernel_fft(self, source: str, nfft: int) -> np.ndarray:
        cached = self._kfft.get((source, 0))
        if cached is not None and cached[0] == nfft:
            return cached[1]
        _sizes, ks = self._kernels[source]
        mat = np.zeros((len(ks), nfft), dtype=np.float64)
        for i, k in enumerate(ks):
            n = min(len(k), nfft)
            mat[i, :n] = k[:n]
        spec = _rfft(mat, nfft)
        self._kfft[(source, 0)] = (nfft, spec)
        return spec

    def render(
        self,
        frames: int,
        plan: Dict[str, Tuple[float, DropSizeDist, float, float, float]],
    ) -> Dict[str, np.ndarray]:
        """Render every planned source.

        plan[source] = (drops_per_s, dist, d_lo_mm, d_hi_mm, amp_scale).
        Returns source → (n_stems, frames) array.
        """
        frames = int(frames)
        out: Dict[str, np.ndarray] = {}
        if frames <= 0:
            return out
        for source, (rate, dist, d_lo, d_hi, amp_scale) in plan.items():
            if source not in self._kernels:
                continue
            stems = self._render_source(source, frames, float(rate), dist, d_lo, d_hi, float(amp_scale))
            if stems is not None:
                out[source] = stems
        # Let tails ring out for sources that just stopped
        for (source, stem), tail in list(self._tails.items()):
            if source in out:
                continue
            buf = out.setdefault(source, np.zeros((self.n_stems, frames), dtype=np.float64))
            n = min(frames, len(tail))
            if stem < buf.shape[0]:
                buf[stem, :n] += tail[:n]
            rest = tail[n:]
            if len(rest) and float(np.max(np.abs(rest))) > 1e-9:
                self._tails[(source, stem)] = rest
            else:
                del self._tails[(source, stem)]
        return out

    def _render_source(
        self,
        source: str,
        frames: int,
        rate: float,
        dist: DropSizeDist,
        d_lo: float,
        d_hi: float,
        amp_scale: float,
    ) -> Optional[np.ndarray]:
        sizes, ks = self._kernels[source]
        k_len = max(len(k) for k in ks) if ks else 0
        if k_len <= 0:
            return None
        nfft = _fast_len(frames + k_len)
        if rate <= 0.0 or amp_scale <= 0.0 or d_hi <= d_lo:
            return None
        spec_k = self._kernel_fft(source, nfft)
        log_sizes = np.log(sizes)
        lam = rate * frames / float(self.sr)
        # Grain cap per source per stem
        cap = self.max_grains_per_sec * frames / float(self.sr)
        thin = 1.0
        bed = 0.0  # drops/sample carried by the roar instead of grains
        if cap > 0.0 and lam > cap:
            m = max(0.0, min(1.0, float(self.hit_makeup)))
            thin = (1.0 + m * (lam / cap - 1.0)) ** 0.5
            bed = (1.0 - m) * (lam - cap) / float(frames)
            lam = cap
        rng = self.rng
        stems = np.zeros((self.n_stems, frames), dtype=np.float64)
        n_k = len(ks)
        n_var = n_k // len(sizes)
        # All stems' impulse trains at once: (stem, kernel, time)
        imp = np.zeros((self.n_stems, n_k, frames), dtype=np.float64)
        for s in range(self.n_stems):
            n = int(rng.poisson(lam))
            if n > 0:
                d = dist.sample(n, rng, d_lo, d_hi)
                # Impact angle + distance spread across the sheet: ±~4 dB
                a = impact_amp(d) * amp_scale * thin * np.exp(rng.normal(0.0, 0.45, n))
                ki = np.abs(np.log(d)[:, None] - log_sizes[None, :]).argmin(axis=1)
                ki = ki * n_var + rng.randint(0, n_var, n)
                pos = rng.randint(0, frames, n)
                np.add.at(imp[s], (ki, pos), a)
        if bed > 0.0:
            # Infinitely dense shot noise is Gaussian with the grains' spectrum:
            # white noise in every kernel row, scaled to that size bin's energy.
            probe = dist.sample(512, rng, d_lo, d_hi)
            e_a = (impact_amp(probe) * amp_scale) ** 2 * math.exp(2.0 * 0.45 ** 2)
            bins = np.abs(np.log(probe)[:, None] - log_sizes[None, :]).argmin(axis=1)
            per_bin = np.bincount(bins, weights=e_a, minlength=len(sizes)) / len(probe)
            sd = np.sqrt(bed * np.repeat(per_bin, n_var) / n_var)
            # Only the size bins that carry the energy (≥1% of the top bin)
            rows = np.flatnonzero(np.repeat(per_bin >= 0.01 * per_bin.max(), n_var))
            noise = self._bed_rng.standard_normal((self.n_stems, len(rows), frames), dtype=np.float32)
            imp[:, rows] += noise * sd[rows][None, :, None]
        used = np.flatnonzero(imp.any(axis=(0, 2)))
        ys = None
        if len(used):
            ys = _irfft((_rfft(imp[:, used], nfft) * spec_k[used][None]).sum(axis=1), nfft)
        for s in range(self.n_stems):
            key = (source, s)
            tail = self._tails.get(key)
            if ys is None and tail is None:
                continue
            y = ys[s] if ys is not None else np.zeros(nfft, dtype=np.float64)
            if tail is not None:
                m = min(len(tail), nfft)
                y[:m] += tail[:m]
            stems[s] = y[:frames]
            rest = y[frames : frames + k_len]
            if float(np.max(np.abs(rest))) > 1e-9:
                self._tails[key] = rest
            elif key in self._tails:
                del self._tails[key]
        return stems


__all__ = ["DenseRainTexture", "VARIANTS_PER_SIZE", "make_variants"]
