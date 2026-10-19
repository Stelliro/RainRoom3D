"""
Roof runoff: the water that keeps falling after the rain does.

A wet roof is a store. Rain fills it; it drains into the gutters and off the
eaves. Stop the rain and the gutters keep running for ~20 s while the eaves
drip on, slower and slower, for a minute or more.

Drips come from fixed spots (corrugation valleys, gutter seams). Each spot
collects its share of the eave flow and lets go when a hanging drop is full,
so one spot drips on a near-regular beat — plink… plink… plink — that slows
as the roof drains. That repeat is what makes drips read as drips.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

# Roof flow follows the rain with a lag: slow to start (the roof wets first),
# slower to stop (it drains).
TAU_WET_S = 6.0
TAU_DRAIN_S = 20.0
# Film left in corrugations and gutter seams: the last, sparse drips
TAU_FILM_S = 75.0
FILM_SHARE = 0.18

# Drips/s from one spot at eave flow F (mm/h-equivalent). Saturates where a
# drip line turns into a stream; ~one drip every 10–15 s near the end.
SPOT_MAX_RATE = 5.0
SPOT_HALF_FLOW = 8.0
SPOT_MIN_RATE = 1.0 / 25.0

# A pendant drop lets go at ~4.5–5.5 mm
DRIP_D_MM = (4.4, 5.6)

EAVE_SPACING_M = 1.3
EAVE_OUT_M = 0.28  # drip line just outside the wall


def spot_rate(flow: float, share: float = 1.0) -> float:
    """Drips/s from one spot with ``share`` of the mean eave flow."""
    f = max(0.0, float(flow)) * max(0.0, float(share))
    return SPOT_MAX_RATE * (1.0 - math.exp(-f / SPOT_HALF_FLOW))


@dataclass
class DripSpot:
    x: float
    z: float
    land_y: float      # where the drop lands (ground or a window sill)
    fall_m: float      # free-fall height
    surface: str       # "water" puddle, "brick" path, "metal" sill
    wall: str
    share: float       # this spot's part of the eave flow (mean 1)
    size_mm: float     # its drop size — same spot, same drop
    pitch: float       # small fixed playback-rate offset per spot
    phase: float = 0.0
    threshold: float = 1.0


class RoofRunoff:
    """Roof water store → gutter flow and per-spot drip times."""

    def __init__(self, seed: int = 31):
        self.rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
        self.flow = 0.0   # fast drainage, mm/h-equivalent
        self.film = 0.0   # slow residual film
        self.spots: List[DripSpot] = []
        self._layout: Optional[tuple] = None

    def reset(self, seed: Optional[int] = None) -> None:
        if seed is not None:
            self.rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
        self.flow = 0.0
        self.film = 0.0
        for s in self.spots:
            s.phase = float(self.rng.rand())

    # ----- layout -----
    def layout(
        self,
        width: float,
        depth: float,
        height: float,
        windows: Sequence[Tuple[str, float, float, float]] = (),
    ) -> None:
        """Place drip spots along the eaves.

        ``windows``: (wall, lo, hi, sill_y) — the span along that wall a
        window covers and its sill height. A drip above one lands on the sill.
        """
        key = (round(width, 2), round(depth, 2), round(height, 2), tuple(windows))
        if key == self._layout:
            return
        self._layout = key
        rng = np.random.RandomState(9001)
        w, d, h = float(width), float(depth), float(height)
        spots: List[DripSpot] = []
        # Room coordinates: north wall at z = depth, south at z = 0;
        # offsets run along x for N/S and along z for E/W.
        walls = (
            ("north", w, lambda u: (u, d + EAVE_OUT_M)),
            ("south", w, lambda u: (u, -EAVE_OUT_M)),
            ("west", d, lambda u: (-EAVE_OUT_M, u)),
            ("east", d, lambda u: (w + EAVE_OUT_M, u)),
        )
        for wall, length, at in walls:
            n = max(1, int(round(length / EAVE_SPACING_M)))
            for k in range(n):
                u = (k + 0.5 + rng.uniform(-0.3, 0.3)) * length / n
                x, z = at(u)
                land_y, surface = 0.0, ("water" if rng.rand() < 0.7 else "brick")
                for wwall, lo, hi, sill_y in windows:
                    if wwall == wall and lo <= u <= hi:
                        land_y, surface = float(sill_y), "metal"
                        break
                spots.append(
                    DripSpot(
                        x=float(x), z=float(z), land_y=land_y,
                        fall_m=max(0.3, h - land_y), surface=surface, wall=wall,
                        share=float(np.exp(rng.normal(0.0, 0.6))),
                        size_mm=float(rng.uniform(*DRIP_D_MM)),
                        pitch=float(np.exp(rng.uniform(-0.06, 0.06))),
                        phase=float(rng.rand()),
                    )
                )
        mean = sum(s.share for s in spots) / max(1, len(spots))
        for s in spots:
            s.share /= mean
        self.spots = spots

    # ----- state -----
    def update(self, dt: float, rain_mmh: float) -> None:
        dt = max(0.0, float(dt))
        r = max(0.0, float(rain_mmh))
        tau = TAU_WET_S if r > self.flow else TAU_DRAIN_S
        self.flow += (r - self.flow) * (1.0 - math.exp(-dt / tau))
        self.film += (self.flow - self.film) * (1.0 - math.exp(-dt / TAU_FILM_S))

    @property
    def eave_flow(self) -> float:
        return (1.0 - FILM_SHARE) * self.flow + FILM_SHARE * self.film

    @property
    def gutter_flow(self) -> float:
        return self.flow

    def drips(self, dt: float) -> List[Tuple[DripSpot, float]]:
        """(spot, seconds into this block) for every drip that lets go."""
        out: List[Tuple[DripSpot, float]] = []
        flow = self.eave_flow
        if flow <= 0.0 or not self.spots:
            return out
        for s in self.spots:
            rate = spot_rate(flow, s.share)
            if rate < SPOT_MIN_RATE:
                continue
            t = 0.0
            while True:
                need = (s.threshold - s.phase) / rate
                if t + need > dt:
                    s.phase += (dt - t) * rate
                    break
                t += need
                out.append((s, t))
                s.phase = 0.0
                # A hanging drop never fills quite the same: ±~12% per drip
                s.threshold = float(np.exp(self.rng.normal(0.0, 0.12)))
        return out


__all__ = ["DripSpot", "RoofRunoff", "spot_rate"]
