"""
Outdoor wind / air movement for RainRoom3D.

Not a continuous pink bed (that reads as radio static on headphones).
Goal: soft pressure and air through open windows — like night wind
outside a house — with slow gusts, not a flat drone.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np

try:
    from scipy import signal as sp_signal

    _HAS_SCIPY = True
except Exception:  # pragma: no cover
    sp_signal = None
    _HAS_SCIPY = False

PI2 = 2.0 * math.pi


def _lp_zi(x: np.ndarray, fc: float, sr: int, state: float) -> Tuple[np.ndarray, float]:
    """One-pole low-pass with carried state."""
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    n = len(x)
    if n == 0 or fc <= 0 or sr <= 0:
        return x, float(state)
    dt = 1.0 / float(sr)
    rc = 1.0 / (PI2 * max(8.0, float(fc)))
    alpha = dt / (rc + dt)
    if _HAS_SCIPY:
        y, zf = sp_signal.lfilter([alpha], [1.0, -(1.0 - alpha)], x, zi=[float(state)])
        return np.asarray(y, dtype=np.float64), float(zf[0])
    y = np.empty(n, dtype=np.float64)
    s = float(state)
    for i, v in enumerate(x):
        s = s + alpha * (v - s)
        y[i] = s
    return y, s


def _hp_zi(
    x: np.ndarray, fc: float, sr: int, y_prev: float, x_prev: float
) -> Tuple[np.ndarray, float, float]:
    """One-pole high-pass with carried state."""
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    n = len(x)
    if n == 0 or fc <= 0 or sr <= 0:
        return x, float(y_prev), float(x_prev)
    dt = 1.0 / float(sr)
    rc = 1.0 / (PI2 * max(5.0, float(fc)))
    alpha = rc / (rc + dt)
    y = np.empty(n, dtype=np.float64)
    yp, xp = float(y_prev), float(x_prev)
    for i, v in enumerate(x):
        yn = alpha * (yp + v - xp)
        y[i] = yn
        yp, xp = yn, v
    return y, yp, xp


class WindAirSynth:
    """Stateful outdoor wind bed for continuous block rendering.

    Design (heard indoors through open windows):
      • **Body** — deep brown pressure / distant air mass (very dark)
      • **Whoosh** — soft mid-band air (not pink hiss); main “wind” cue
      • **Edge** — tiny narrow band only in strong wind (window lip), quiet
      • **Gusts** — slow random envelope so it breathes instead of drones

    ``render()`` returns mono. Use ``render_stereo()`` for headphone pan.
    """

    def __init__(self, sr: int = 48000, seed: int = 9001):
        self.sr = int(sr)
        self.rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
        self.t = 0.0
        # Filter memory
        self._b1 = 0.0
        self._b2 = 0.0
        self._whoosh_lp = 0.0
        self._whoosh_lp2 = 0.0
        self._body_lp = 0.0
        self._body_lp2 = 0.0
        self._edge_lp = 0.0
        self._edge_hp_y = 0.0
        self._edge_hp_x = 0.0
        self._mid_hp_y = 0.0
        self._mid_hp_x = 0.0
        # Gust envelope (smooth, block-continuous)
        self._gust = 0.35
        self._gust_target = 0.35
        self._gust_timer = 0.0
        # Slow “breathing” phase
        self._phase_a = float(self.rng.uniform(0.0, PI2))
        self._phase_b = float(self.rng.uniform(0.0, PI2))
        self._phase_c = float(self.rng.uniform(0.0, PI2))
        # Stereo decorrelation delay (short)
        self._decorr = np.zeros(64, dtype=np.float64)
        self._decorr_i = 0
        # Smoothed bed RMS so level is stable without block pumping
        self._bed_rms = 0.08

    def reset(self, seed: Optional[int] = None):
        s = int(seed) if seed is not None else int(self.rng.randint(1, 2**31 - 1))
        self.__init__(self.sr, s)

    def _advance_gust(self, n: int, wind: float) -> np.ndarray:
        """Smooth random gust envelope in [0.15..1.0], stronger when windy."""
        sr = self.sr
        w = max(0.0, min(1.0, float(wind)))
        env = np.empty(n, dtype=np.float64)
        # How often a new gust target is chosen (seconds)
        base_iv = 2.8 - 1.4 * w  # windier → faster changes
        base_iv = max(1.1, base_iv)
        # Slew rate: how fast envelope moves toward target
        slew = (0.35 + 0.85 * w) / max(1, sr)  # per-sample toward target

        g = self._gust
        tgt = self._gust_target
        timer = self._gust_timer
        for i in range(n):
            timer -= 1.0 / sr
            if timer <= 0.0:
                # New gust: sometimes a push, sometimes a lull
                if self.rng.rand() < 0.55 + 0.25 * w:
                    # Gust peak
                    tgt = float(self.rng.uniform(0.55 + 0.15 * w, 0.92 + 0.08 * w))
                    timer = float(self.rng.uniform(0.6, 1.8 + 0.6 * (1.0 - w)))
                else:
                    # Quiet between gusts
                    tgt = float(self.rng.uniform(0.12, 0.38 - 0.08 * w))
                    timer = float(self.rng.uniform(0.9, base_iv * 1.4))
            # Smooth approach
            g += (tgt - g) * min(1.0, slew * (14.0 + 10.0 * abs(tgt - g)))
            env[i] = g
        self._gust = float(g)
        self._gust_target = float(tgt)
        self._gust_timer = float(timer)
        return env

    def render(
        self,
        n: int,
        wind: float = 0.4,
        level: float = 0.06,
        open_avg: float = 0.6,
    ) -> np.ndarray:
        """Mono wind bed, length n.

        wind: 0..1 speed
        level: absolute mix gain (already includes mix_wind from caller)
        open_avg: mean window openness — more open → a bit more mid whoosh
        """
        n = int(n)
        if n <= 0:
            return np.zeros(0, dtype=np.float64)

        w = max(0.0, min(1.0, float(wind)))
        if w < 0.02 or level <= 1e-6:
            self.t += n / max(1, self.sr)
            # Ease gust toward calm so next start isn't a spike
            self._gust = 0.92 * self._gust + 0.08 * 0.25
            return np.zeros(n, dtype=np.float64)

        sr = self.sr
        open_k = max(0.15, min(1.0, float(open_avg)))

        # --- Excitation: brown-heavy (not white/pink hiss) ---
        white = self.rng.randn(n).astype(np.float64)
        # Dual brown for thick air mass
        a_b = 0.994 - 0.004 * w  # slightly brighter when strong
        a_b = max(0.988, min(0.996, a_b))
        if _HAS_SCIPY:
            b = np.array([1.0 - a_b])
            aa = np.array([1.0, -a_b])
            brown1, z1 = sp_signal.lfilter(b, aa, white, zi=[self._b1])
            brown2, z2 = sp_signal.lfilter(b, aa, white * 0.7 + 0.3 * np.roll(white, 1), zi=[self._b2])
            self._b1 = float(z1[0])
            self._b2 = float(z2[0])
            brown1 = np.asarray(brown1, dtype=np.float64)
            brown2 = np.asarray(brown2, dtype=np.float64)
        else:
            brown1 = np.empty(n, dtype=np.float64)
            brown2 = np.empty(n, dtype=np.float64)
            s1, s2 = self._b1, self._b2
            prev = white[0]
            for i, v in enumerate(white):
                s1 = a_b * s1 + (1.0 - a_b) * v
                s2 = a_b * s2 + (1.0 - a_b) * (0.7 * v + 0.3 * prev)
                brown1[i] = s1
                brown2[i] = s2
                prev = v
            self._b1, self._b2 = s1, s2

        air = 0.72 * brown1 + 0.28 * brown2

        # --- Body: deep pressure (felt more than "heard") ---
        body_fc = 110.0 + 90.0 * w  # ~110–200 Hz
        body, self._body_lp = _lp_zi(air, body_fc, sr, self._body_lp)
        body, self._body_lp2 = _lp_zi(body, body_fc * 1.25, sr, self._body_lp2)

        # --- Whoosh: main wind cue — soft mid air through openings ---
        # Keep out of the 2 kHz+ hiss band; centre energy ~200–700 Hz
        mid, self._mid_hp_y, self._mid_hp_x = _hp_zi(
            air, 70.0 + 30.0 * w, sr, self._mid_hp_y, self._mid_hp_x
        )
        whoosh_fc = 380.0 + 280.0 * w + 100.0 * open_k  # ~380–760 Hz top
        whoosh, self._whoosh_lp = _lp_zi(mid, whoosh_fc, sr, self._whoosh_lp)
        whoosh, self._whoosh_lp2 = _lp_zi(whoosh, whoosh_fc * 0.90, sr, self._whoosh_lp2)

        # --- Edge: quiet narrow band only when windy + windows open ---
        # (window lip / crack tone — never broadband hiss)
        edge_g = 0.0 + 0.028 * (w ** 1.5) * (0.30 + 0.70 * open_k)
        if edge_g > 1e-4:
            edge, self._edge_hp_y, self._edge_hp_x = _hp_zi(
                white * 0.25 + air * 0.75,
                850.0 + 350.0 * w,
                sr,
                self._edge_hp_y,
                self._edge_hp_x,
            )
            edge, self._edge_lp = _lp_zi(edge, 1400.0 + 500.0 * w, sr, self._edge_lp)
        else:
            edge = np.zeros(n, dtype=np.float64)

        # Whoosh is the identity; body is underlay; edge is optional accent
        body_g = 0.32 + 0.18 * w
        whoosh_g = (0.72 + 0.48 * w) * (0.50 + 0.50 * open_k)
        mix = body_g * body + whoosh_g * whoosh + edge_g * edge

        # Normalize bed so ``level`` is predictable (brown LP is tiny raw)
        bed_rms = float(np.sqrt(np.mean(mix * mix)) + 1e-12)
        self._bed_rms = 0.92 * self._bed_rms + 0.08 * bed_rms
        mix = mix * (0.12 / max(1e-6, self._bed_rms))

        # Gust envelope + slow multi-rate breathing
        gust = self._advance_gust(n, w)
        t0 = self.t
        t = t0 + np.arange(n, dtype=np.float64) / sr
        # Very slow swell + slightly faster flutter (still sub-audio AM)
        breath = (
            0.78
            + 0.14 * np.sin(PI2 * (0.07 + 0.06 * w) * t + self._phase_a)
            + 0.08 * np.sin(PI2 * (0.19 + 0.12 * w) * t + self._phase_b)
        )
        # Tiny roughness only when strong (not static)
        if w > 0.45:
            flutter = 1.0 + 0.04 * (w - 0.45) * np.sin(
                PI2 * (1.8 + 2.5 * w) * t + self._phase_c
            )
            breath = breath * flutter
        env = gust * breath
        # Wind curve: gentle at low, present at mid, full at high
        wind_curve = (w ** 0.80) * (0.40 + 0.60 * w)
        out = mix * env * float(level) * wind_curve

        # Soft peak hygiene
        pk = float(np.max(np.abs(out)) + 1e-12)
        if pk > 0.28:
            out *= 0.28 / pk

        self.t = t0 + n / max(1, sr)
        self._phase_a = (self._phase_a + PI2 * (0.07 + 0.06 * w) * n / sr) % PI2
        self._phase_b = (self._phase_b + PI2 * (0.19 + 0.12 * w) * n / sr) % PI2
        self._phase_c = (self._phase_c + PI2 * (1.8 + 2.5 * w) * n / sr) % PI2
        return out

    def to_stereo(self, mono: np.ndarray, pan: float = 0.0, level: float = 1.0) -> np.ndarray:
        """Pan + light decorrelation for headphones. Does not re-render (one state step)."""
        mono = np.asarray(mono, dtype=np.float64).reshape(-1)
        if mono.size == 0:
            return np.zeros((0, 2), dtype=np.float64)
        g = float(level)
        mono = mono * g

        p = max(-1.0, min(1.0, float(pan)))
        gL = 0.55 - 0.38 * p
        gR = 0.55 + 0.38 * p
        nrm = math.sqrt(gL * gL + gR * gR) + 1e-12
        gL *= math.sqrt(2.0) / nrm
        gR *= math.sqrt(2.0) / nrm

        # ~1.2 ms decorrelation on right for width without combing
        d = max(8, int(0.0012 * self.sr))
        if len(self._decorr) < d:
            self._decorr = np.zeros(d, dtype=np.float64)
            self._decorr_i = 0
        buf = self._decorr
        d = len(buf)
        right = np.empty_like(mono)
        idx = self._decorr_i
        for i, v in enumerate(mono):
            right[i] = buf[idx]
            buf[idx] = v
            idx = (idx + 1) % d
        self._decorr_i = idx
        right = 0.55 * right + 0.45 * mono
        return np.column_stack([mono * gL, right * gR])

    def render_stereo(
        self,
        n: int,
        wind: float = 0.4,
        level: float = 0.06,
        open_avg: float = 0.6,
        pan: float = 0.0,
    ) -> np.ndarray:
        """Stereo wind for headphones. pan −1..+1 (left..right), wind direction."""
        mono = self.render(n, wind=wind, level=level, open_avg=open_avg)
        return self.to_stereo(mono, pan=pan, level=1.0)
