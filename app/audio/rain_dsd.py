"""
Physical rain model behind the Quantity slider.

Quantity → rain rate R (mm/h, log scale) → Marshall–Palmer drop-size
distribution → how many drops of each size land per second, and how loud
each one is. Two consequences the ear cares about:

  • Hit *count* scales ~80× from drizzle to downpour (not 10×).
  • Drop *loudness* is heavy-tailed. A 3 mm drop hits ~25 dB harder than a
    1 mm one, so even a dense downpour has big drops poking out of the sheet.
    Equal-loudness drops at high density is exactly what reads as hiss.

Impact amplitude follows peak impact force F ~ ρ D² v² (drop momentum over
contact time D/v), with Atlas terminal velocity v(D).
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

RATE_MIN_MMH = 0.2
RATE_MAX_MMH = 100.0
D_MIN_MM = 0.5  # smaller drops are inaudible on a roof
D_MAX_MM = 6.0  # larger drops break up in flight
D_REF_MM = 3.0  # impact_amp(D_REF_MM) == 1

_GRID = np.linspace(D_MIN_MM, D_MAX_MM, 1101)
_DD = float(_GRID[1] - _GRID[0])


def terminal_velocity(d_mm):
    """Atlas et al. (1973) fall speed, m/s."""
    d = np.asarray(d_mm, dtype=np.float64)
    return np.maximum(0.1, 9.65 - 10.3 * np.exp(-0.6 * d))


_V_REF = float(terminal_velocity(D_REF_MM))


def impact_amp(d_mm):
    """Relative impact pressure amplitude (1.0 at 3 mm)."""
    d = np.asarray(d_mm, dtype=np.float64)
    v = terminal_velocity(d)
    return (d / D_REF_MM) ** 2 * (v / _V_REF) ** 2


def quantity_to_rain_rate(q: float) -> float:
    """Slider 0..1 → mm/h. 0 = off; log scale so each step sounds even."""
    q = float(q or 0.0)
    if q <= 0.0005:
        return 0.0
    q = min(1.0, q)
    return RATE_MIN_MMH * (RATE_MAX_MMH / RATE_MIN_MMH) ** q


def rain_rate_class(rate_mmh: float) -> str:
    """AMS-style intensity names (drizzle split out below 0.5 mm/h)."""
    r = float(rate_mmh)
    if r <= 0.0:
        return "off"
    if r < 0.5:
        return "drizzle"
    if r < 2.5:
        return "light rain"
    if r < 7.6:
        return "moderate rain"
    if r < 50.0:
        return "heavy rain"
    return "downpour"


class DropSizeDist:
    """Marshall–Palmer N(D) = 8000·e^(−ΛD) m⁻³mm⁻¹, Λ = 4.1·R^−0.21, as a
    landing flux N(D)·v(D) (drops per m² per s per mm) on [D_MIN, D_MAX]."""

    def __init__(self, rate_mmh: float):
        self.rate = max(1e-6, float(rate_mmh))
        lam = 4.1 * self.rate ** -0.21
        flux = 8000.0 * np.exp(-lam * _GRID) * terminal_velocity(_GRID)
        self._flux = flux
        # cum_above[i] = drops/m²/s with D >= _GRID[i]
        self._above = np.concatenate([np.cumsum((flux * _DD)[::-1])[::-1][:-1], [0.0]])
        self._energy = flux * impact_amp(_GRID) ** 2 * _DD

    def flux(self, d_lo: float = D_MIN_MM, d_hi: float = D_MAX_MM) -> float:
        """Drops per m² per second with d_lo <= D < d_hi."""
        return max(0.0, self._cum(d_lo) - self._cum(d_hi))

    def _cum(self, d: float) -> float:
        return float(np.interp(float(d), _GRID, self._above, left=self._above[0], right=0.0))

    def energy(self, d_lo: float = D_MIN_MM, d_hi: float = D_MAX_MM) -> float:
        """Σ impact_amp² per m² per s over the band (incoherent loudness)."""
        m = (_GRID >= d_lo) & (_GRID < d_hi)
        return float(self._energy[m].sum())

    def split_for_rate(self, budget_per_s: float, area_m2: float) -> float:
        """Diameter above which only ``budget_per_s`` drops land on ``area_m2``."""
        per_m2 = max(1e-9, float(budget_per_s)) / max(1e-6, float(area_m2))
        if per_m2 >= self._above[0]:
            return D_MIN_MM
        # _above is decreasing; interp wants increasing x
        return float(np.interp(-per_m2, -self._above, _GRID))

    def sample(self, n: int, rng: np.random.RandomState, d_lo: float, d_hi: float) -> np.ndarray:
        """n diameters drawn from the landing flux restricted to [d_lo, d_hi)."""
        n = int(n)
        if n <= 0:
            return np.zeros(0, dtype=np.float64)
        c_lo, c_hi = self._cum(d_lo), self._cum(d_hi)
        if c_lo - c_hi <= 1e-12:
            return np.full(n, float(d_lo), dtype=np.float64)
        u = c_hi + rng.random_sample(n) * (c_lo - c_hi)
        return np.interp(-u, -self._above, _GRID)


_CACHE: Dict[int, DropSizeDist] = {}


def dsd_for_quantity(q: float) -> Tuple[float, DropSizeDist]:
    """(rain rate, distribution); cached on the slider's 0.1% steps."""
    key = int(round(max(0.0, min(1.0, float(q or 0.0))) * 1000))
    dist = _CACHE.get(key)
    rate = quantity_to_rain_rate(key / 1000.0)
    if dist is None:
        dist = DropSizeDist(max(rate, RATE_MIN_MMH))
        if len(_CACHE) > 256:
            _CACHE.clear()
        _CACHE[key] = dist
    return rate, dist


# Real drizzle→downpour spans ~38 dB. Keep 45% of that (~20 dB heard) so drizzle
# stays audible and downpour doesn't ride the limiter at a normal volume.
LOUDNESS_KEEP = 0.45
_E_REF = None


def loudness_gain(q: float) -> float:
    """Per-drop gain that compresses the physical loudness range.

    1.0 at moderate rain (~4.5 mm/h); drops above/below get pulled toward it.
    """
    global _E_REF
    if _E_REF is None:
        _E_REF = DropSizeDist(quantity_to_rain_rate(0.5)).energy()
    _r, dist = dsd_for_quantity(q)
    e = max(1e-12, dist.energy())
    return float((e / _E_REF) ** (-(1.0 - LOUDNESS_KEEP) * 0.5))


def describe(q: float) -> str:
    rate = quantity_to_rain_rate(q)
    if rate <= 0.0:
        return "off"
    return f"{rain_rate_class(rate)} · {rate:.1f} mm/h" if rate < 10 else f"{rain_rate_class(rate)} · {rate:.0f} mm/h"


__all__ = [
    "DropSizeDist",
    "D_MIN_MM",
    "D_MAX_MM",
    "describe",
    "dsd_for_quantity",
    "impact_amp",
    "loudness_gain",
    "quantity_to_rain_rate",
    "rain_rate_class",
    "terminal_velocity",
]
