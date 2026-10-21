"""
Natural gusts: the wind speed right now, around the Strength you set.

Real wind is never steady. Near the ground it is turbulent: slow swells of a
few tens of seconds, gusts of a few seconds and quick flutter, about 15–25 %
of the mean speed (more with "Gusty" on), and now and then a proper gust
that builds for a couple of seconds, holds and dies away.

``GustField.advance`` returns the instantaneous speed (same 0..1 scale as
``Room.wind_speed``, may go a little over 1 in a gust) for every sample of a
block. The wind sound follows it, and so does the rain driven against the
windward walls (more drops in a gust, fewer in a lull). The rain *geometry*
keeps using the slow mean, so a gust never re-plans the house.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

CONTROL_HZ = 120.0
# (time constant s, share of the turbulence) — slow swell, gusts, flutter
_BANDS = ((22.0, 0.45), (4.5, 0.40), (0.7, 0.15))
TURBULENCE = 0.18        # σ of speed / mean
GUSTY_TURBULENCE = 0.30  # with "Gusty" on


class GustField:
    """Stateful turbulent wind speed, sample-rate output, block-seamless."""

    def __init__(self, sr: int = 48000, seed: int = 515):
        self.sr = int(sr)
        self.rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
        self._ar = np.zeros(len(_BANDS))
        self._last = 0.0           # last output speed
        self._last_ratio = 1.0     # last output / mean
        self._phase = 0.0          # control-rate carry (fraction of a control step)
        self._prev_ctrl: Optional[float] = None
        self._carry: list = []
        # Big gust event: (t left in rise, hold, fall, peak)
        self._event = None
        self._t_next_event = float(self.rng.uniform(6.0, 18.0))
        self._t = 0.0

    def reset(self, seed: Optional[int] = None) -> None:
        self.__init__(self.sr, int(seed) if seed is not None else int(self.rng.randint(1, 2**31 - 1)))

    @property
    def speed(self) -> float:
        return self._last

    @property
    def ratio(self) -> float:
        """Speed now / mean speed (1 = average)."""
        return self._last_ratio

    def _event_gain(self, dt: float, mean: float, gusty: bool) -> float:
        if self._event is None:
            if self._t >= self._t_next_event and mean > 0.04:
                rise = float(self.rng.uniform(1.2, 3.0))
                hold = float(self.rng.uniform(0.6, 2.5))
                fall = float(self.rng.uniform(2.0, 5.0))
                peak = float(self.rng.uniform(0.25, 0.55) * (1.5 if gusty else 1.0))
                self._event = [0.0, rise, hold, fall, peak]
            return 0.0
        e = self._event
        e[0] += dt
        t, rise, hold, fall, peak = e
        if t < rise:
            g = 0.5 - 0.5 * math.cos(math.pi * t / rise)
        elif t < rise + hold:
            g = 1.0
        elif t < rise + hold + fall:
            g = 0.5 + 0.5 * math.cos(math.pi * (t - rise - hold) / fall)
        else:
            self._event = None
            gap = (8.0, 25.0) if gusty else (15.0, 45.0)
            self._t_next_event = self._t + float(self.rng.uniform(*gap))
            return 0.0
        return peak * g

    def advance(self, n: int, mean: float, gusty: bool = False) -> np.ndarray:
        """Instantaneous speed for the next ``n`` samples (mean = slow wind 0..1)."""
        n = int(n)
        mean = max(0.0, float(mean))
        if n <= 0:
            return np.zeros(0)
        dt_c = 1.0 / CONTROL_HZ
        span = n / self.sr
        n_ctrl = int(math.floor(self._phase + span * CONTROL_HZ)) + 1
        sigma = GUSTY_TURBULENCE if gusty else TURBULENCE
        # ctrl[0] is the origin; points already made for the last block's tail
        # are reused so the curve carries on exactly where it was heading
        ctrl = [self._prev_ctrl if self._prev_ctrl is not None else mean] + self._carry
        while len(ctrl) < n_ctrl + 1:
            self._t += dt_c
            turb = 0.0
            for b, (tau, share) in enumerate(_BANDS):
                a = math.exp(-dt_c / tau)
                self._ar[b] = a * self._ar[b] + math.sqrt(1.0 - a * a) * float(self.rng.randn())
                turb += math.sqrt(share) * self._ar[b]
            ev = self._event_gain(dt_c, mean, gusty)
            ctrl.append(mean * max(0.08, 1.0 + sigma * turb + ev))
        # Control points sit at (phase + k) / CONTROL_HZ; interpolate to samples
        t_s = (np.arange(n) / self.sr) * CONTROL_HZ + self._phase
        out = np.interp(t_s, np.arange(len(ctrl), dtype=np.float64), ctrl)
        adv = self._phase + span * CONTROL_HZ
        k = int(math.floor(adv))
        self._phase = adv - k
        self._prev_ctrl = float(ctrl[k])
        self._carry = list(ctrl[k + 1:])
        self._last = float(out[-1])
        self._last_ratio = self._last / mean if mean > 1e-6 else 1.0
        return out


__all__ = ["GustField", "TURBULENCE", "GUSTY_TURBULENCE"]
