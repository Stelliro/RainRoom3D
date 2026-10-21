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
        # Low-band fusing: a dense sheet's low end is one smooth rumble, not
        # separate plops. Below ``fuse_hz`` the grains' share fades into a
        # roar as the per-stem rate climbs from ``fuse_from`` to ``fuse_full``
        # drops/s; above it they stay separate ticks. Stacked water "blubs"
        # (a few dozen grains, a few pitches) otherwise boil. 0 = off.
        self.fuse_hz = 0.0
        self.fuse_from = 150.0
        self.fuse_full = 600.0
        self.rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
        self._bed_rng = np.random.default_rng(int(seed) + 1)
        # source → (sizes_mm[S], kernels[S * V]) — V variants per size, bin-major
        self._kernels: Dict[str, Tuple[np.ndarray, List[np.ndarray]]] = {}
        # (source, stem) → convolution tail carried into the next block
        self._tails: Dict[Tuple[str, int], np.ndarray] = {}
        # alias source → source whose grains (and their FFTs) it reuses
        self._alias: Dict[str, str] = {}
        # source → padded kernel matrix; roar filters by (source, sizes, dist)
        self._kmat: Dict[object, np.ndarray] = {}
        self._bed_cache: Dict[tuple, dict] = {}

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
        self._alias.pop(source, None)
        self._forget(source)

    def share_kernels(self, alias: str, source: str) -> None:
        """``alias`` renders with ``source``'s grains, sharing their FFTs."""
        self._kernels[alias] = self._kernels[source]
        self._alias[alias] = source
        self._forget(alias)

    def _forget(self, source: str) -> None:
        for key in [k for k in self._kmat if k == source or (isinstance(k, tuple) and k[0] == source)]:
            del self._kmat[key]
        for key in [k for k in self._bed_cache if k[0] == source]:
            del self._bed_cache[key]

    def has_source(self, source: str) -> bool:
        return source in self._kernels

    def _kernel_matrix(self, source: str) -> np.ndarray:
        """All of a source's grains, zero-padded to one length: (kernels, len)."""
        source = self._alias.get(source, source)
        cached = self._kmat.get(source)
        if cached is not None:
            return cached
        _sizes, ks = self._kernels[source]
        k_len = max(len(k) for k in ks)
        mat = np.zeros((len(ks), k_len), dtype=np.float64)
        for i, k in enumerate(ks):
            mat[i, : len(k)] = k
        self._kmat[source] = mat
        return mat

    # Grain lead-in so the zero-phase crossover's pre-ring isn't cut off
    _FUSE_PAD = 256

    def _band_matrices(self, source: str) -> Tuple[np.ndarray, np.ndarray]:
        """The grains split at ``fuse_hz``: (high, low), power-complementary
        (|H|² + |L|² = 1) over an octave, both delayed by _FUSE_PAD."""
        source = self._alias.get(source, source)
        key = (source, "bands", float(self.fuse_hz))
        cached = self._kmat.get(key)
        if cached is not None:
            return cached
        mat = self._kernel_matrix(source)
        pad = self._FUSE_PAD
        n = _fast_len(mat.shape[1] + 2 * pad)
        x = np.zeros((mat.shape[0], n), dtype=np.float64)
        x[:, pad: pad + mat.shape[1]] = mat
        f = np.fft.rfftfreq(n, 1.0 / self.sr)
        # 0 below fc/√2 → 1 above fc·√2, a quarter-sine in log frequency
        t = np.clip((np.log2(np.maximum(f, 1e-9) / self.fuse_hz) + 0.5), 0.0, 1.0)
        w_hi = np.sin(0.5 * math.pi * t)
        w_lo = np.cos(0.5 * math.pi * t)
        X = _rfft(x, n)
        out = (_irfft(X * w_hi[None], n), _irfft(X * w_lo[None], n))
        self._kmat[key] = out
        return out

    def _grain_matrix(self, source: str, g_lo: float) -> np.ndarray:
        """Grains with their low band scaled by ``g_lo`` (1 = untouched)."""
        if self.fuse_hz <= 0.0 or g_lo >= 0.999:
            return self._kernel_matrix(source)
        g_lo = round(float(g_lo), 2)
        canon = self._alias.get(source, source)
        key = (canon, "mix", float(self.fuse_hz), g_lo)
        cached = self._kmat.get(key)
        if cached is None:
            hi, lo = self._band_matrices(canon)
            cached = hi + g_lo * lo
            stale = [k for k in self._kmat if isinstance(k, tuple) and k[0] == canon and k[1] == "mix"]
            if len(stale) > 8:
                for k in stale:
                    del self._kmat[k]
            self._kmat[key] = cached
        return cached

    def _bed_filter(self, source: str, dist: DropSizeDist, d_lo: float, d_hi: float, nfft: int,
                    low_only: bool = False):
        """Spectrum (at ``nfft``) and length of the roar's filter.

        Infinitely dense shot noise is Gaussian with the grains' power
        spectrum, Σ_rows w_r |K_r|², w_r the share of drop energy in that
        size bin. One white noise through a filter with that magnitude is
        the same random process as white noise in every kernel row — for
        one FFT instead of one per row. ``low_only``: the grains' band
        under ``fuse_hz`` (see _band_matrices).
        """
        canon = self._alias.get(source, source)
        key = (canon, round(float(d_lo), 3), round(float(d_hi), 3),
               float("%.4g" % dist.flux(d_lo, d_hi)), float("%.4g" % dist.energy()),
               float(self.fuse_hz) if low_only else 0.0)
        filt = self._bed_cache.get(key)
        if filt is None:
            sizes, ks = self._kernels[canon]
            n_var = len(ks) // len(sizes)
            probe = dist.sample(4096, np.random.RandomState(1), d_lo, d_hi)
            e_a = impact_amp(probe) ** 2 * math.exp(2.0 * 0.45 ** 2)
            bins = np.abs(np.log(probe)[:, None] - np.log(sizes)[None, :]).argmin(axis=1)
            per_bin = np.bincount(bins, weights=e_a, minlength=len(sizes)) / len(probe)
            # Only the size bins that carry the energy (≥1% of the top bin)
            per_bin = np.where(per_bin >= 0.01 * per_bin.max(), per_bin, 0.0)
            w = np.repeat(per_bin, n_var) / n_var
            mat = self._band_matrices(canon)[1] if low_only else self._kernel_matrix(canon)
            n_h = 2 * mat.shape[1]
            power = (w[:, None] * np.abs(_rfft(mat, n_h)) ** 2).sum(axis=0)
            # Zero-phase, centred, tapered so it doesn't wrap
            h = np.roll(_irfft(np.sqrt(power), n_h), n_h // 2) * np.hanning(n_h)
            e_want = float(np.sum(w * np.sum(mat * mat, axis=1)))
            h *= math.sqrt(e_want / max(1e-30, float(np.sum(h * h))))
            filt = {"h": h}
            if len(self._bed_cache) > 64:
                self._bed_cache.clear()
            self._bed_cache[key] = filt
        spec = filt.get(nfft)
        if spec is None:
            spec = filt[nfft] = _rfft(filt["h"], nfft)
        return spec, len(filt["h"])

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
        if not ks or rate <= 0.0 or amp_scale <= 0.0 or d_hi <= d_lo:
            return None
        # Low band: grains keep √(1-β) of it, the roar carries β
        beta = 0.0
        if self.fuse_hz > 0.0 and self.fuse_full > self.fuse_from:
            beta = min(1.0, max(0.0, (rate - self.fuse_from) / (self.fuse_full - self.fuse_from)))
        kmat = self._grain_matrix(source, math.sqrt(1.0 - beta))
        k_len = kmat.shape[1]
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
        n_var = len(ks) // len(sizes)
        n_out = frames + k_len
        beds = []  # (spectrum, drops/sample)
        n_fft = _fast_len(frames + 2 * k_len)
        if bed > 0.0:
            spec, n_h = self._bed_filter(source, dist, d_lo, d_hi, n_fft)
            beds.append((spec, bed))
            n_out = max(n_out, frames + n_h)
        if beta > 0.0:
            spec, n_h = self._bed_filter(source, dist, d_lo, d_hi, n_fft, low_only=True)
            beds.append((spec, beta * rate / float(self.sr)))
            n_out = max(n_out, frames + n_h)
        # A longer tail from last block (roar on, now off) must not be cut
        for st in range(self.n_stems):
            tail = self._tails.get((source, st))
            if tail is not None:
                n_out = max(n_out, len(tail))
        ys = np.zeros((self.n_stems, n_out), dtype=np.float64)
        for s in range(self.n_stems):
            n = int(rng.poisson(lam))
            if n > 0:
                d = dist.sample(n, rng, d_lo, d_hi)
                # Impact angle + distance spread across the sheet: ±~4 dB
                a = impact_amp(d) * amp_scale * thin * np.exp(rng.normal(0.0, 0.45, n))
                ki = np.abs(np.log(d)[:, None] - log_sizes[None, :]).argmin(axis=1)
                ki = ki * n_var + rng.randint(0, n_var, n)
                pos = rng.randint(0, frames, n)
                # A few dozen grains a block: overlap-add them directly
                # (cheaper than an FFT per kernel row)
                row = ys[s]
                for p_, k_, a_ in zip(pos.tolist(), ki.tolist(), a.tolist()):
                    row[p_: p_ + k_len] += a_ * kmat[k_]
        for spec, rate_s in beds:
            noise = self._bed_rng.standard_normal((self.n_stems, frames))
            roar = _irfft(_rfft(noise, n_fft) * spec[None], n_fft)
            m = min(n_out, n_fft)
            ys[:, :m] += roar[:, :m] * (math.sqrt(rate_s) * amp_scale)
        stems = np.zeros((self.n_stems, frames), dtype=np.float64)
        for s in range(self.n_stems):
            key = (source, s)
            y = ys[s]
            tail = self._tails.get(key)
            if tail is not None:
                m = min(len(tail), n_out)
                y[:m] += tail[:m]
            stems[s] = y[:frames]
            rest = y[frames:]
            if float(np.max(np.abs(rest))) > 1e-9:
                self._tails[key] = rest
            elif key in self._tails:
                del self._tails[key]
        return stems


__all__ = ["DenseRainTexture", "VARIANTS_PER_SIZE", "make_variants"]
