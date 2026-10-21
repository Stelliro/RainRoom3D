"""
Outdoor wind / air movement for RainRoom3D.

Not a continuous pink bed (that reads as radio static on headphones).
Goal: soft pressure and air through open windows — like night wind
outside a house — with slow gusts, not a flat drone.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Sequence, Tuple

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


# ---------------------------------------------------------------------------
# WindSound: the wind outside a house, heard through its openings
# ---------------------------------------------------------------------------
#
# WindAirSynth above was a quiet mono bed under the rain. Real wind outside
# a house is three things, all driven by the gusting speed (gusts.py):
#
#   rush     air through trees, hedges and round the house: broadband, its
#            top end opening up as it blows harder, with a fast leafy flutter.
#            Comes from all around, a bit more from upwind.
#   roar     the deep moving air mass and the house shrugging it off; mostly
#            below 200 Hz, so it gets in through the walls and roof too.
#   howl     the moan of air round corners, eaves and gaps: broad resonant
#            bands (a few hundred Hz) whose pitch and level ride every gust.
#            This is what makes wind sound like wind rather than more hiss.
#   whistle  a window open just a crack (or a leaky closed one) sings: air
#            squeezed through a slot rings a narrow band whose pitch rises
#            and falls with each gust. Wide open windows don't whistle.
#
# Levels follow speed steeply (flow noise power goes as ~U^6): a gust
# swells the howl by several dB and a lull lets it drop away.

WIND_MAX_MS = 22.0  # Strength 1.0 ≈ a strong gale


def wind_ms(speed01) -> np.ndarray:
    return np.asarray(speed01, dtype=np.float64) * WIND_MAX_MS


class _Biquad:
    """Band-pass biquad with carried state; coefficients may change per call."""

    def __init__(self):
        self.zi = np.zeros(2)

    def __call__(self, x: np.ndarray, f: float, q: float, sr: int) -> np.ndarray:
        w0 = PI2 * max(20.0, min(0.45 * sr, float(f))) / sr
        alpha = math.sin(w0) / (2.0 * max(0.3, float(q)))
        a0 = 1.0 + alpha
        b = np.array([alpha, 0.0, -alpha]) / a0
        a = np.array([1.0, -2.0 * math.cos(w0) / a0, (1.0 - alpha) / a0])
        if _HAS_SCIPY:
            y, self.zi = sp_signal.lfilter(b, a, x, zi=self.zi)
            return y
        y = np.empty_like(x)
        z1, z2 = self.zi
        for i, v in enumerate(x):
            yn = b[0] * v + z1
            z1 = b[1] * v - a[1] * yn + z2
            z2 = b[2] * v - a[2] * yn
            y[i] = yn
        self.zi = np.array([z1, z2])
        return y


class _LP1:
    """One-pole low-pass, carried state (lfilter zi)."""

    def __init__(self):
        self.zi = np.zeros(1)

    def __call__(self, x: np.ndarray, fc: float, sr: int) -> np.ndarray:
        a = 1.0 - math.exp(-PI2 * max(5.0, float(fc)) / sr)
        if _HAS_SCIPY:
            y, self.zi = sp_signal.lfilter([a], [1.0, -(1.0 - a)], x, zi=self.zi)
            return y
        y = np.empty_like(x)
        s = float(self.zi[0])
        for i, v in enumerate(x):
            s += a * (v - s)
            y[i] = s
        self.zi = np.array([s])
        return y


def _lp1_gain(fc: float, sr: int) -> float:
    """RMS of white noise through _LP1 (to keep the level independent of fc)."""
    a = 1.0 - math.exp(-PI2 * max(5.0, float(fc)) / sr)
    return math.sqrt(a / (2.0 - a))


class WindSound:
    """Outdoor wind components for one block, ready for OutdoorRouter."""

    SUB = 256  # whistle pitch glides at this many samples
    # Howl resonances at 10 m/s, per stem (they glide ∝ speed^0.8)
    HOWL_HZ = ((255.0, 430.0), (310.0, 560.0), (275.0, 690.0))
    HOWL_Q = 6.0

    def __init__(self, sr: int = 48000, seed: int = 4242, n_stems: int = 2):
        self.sr = int(sr)
        self.rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
        self.n_stems = int(n_stems)
        self._rush_hp = [_LP1() for _ in range(self.n_stems)]
        self._rush_lp = [_LP1() for _ in range(self.n_stems)]
        self._rush_lp2 = [_LP1() for _ in range(self.n_stems)]
        self._flutter = _LP1()
        self._flutter2 = _LP1()
        self._roar_lp = _LP1()
        self._roar_lp2 = _LP1()
        self._roar_hp = _LP1()
        # Howl: a few broad resonances per stem (corners/eaves), each its own pitch
        self._howl = [[(_Biquad(), f) for f in self.HOWL_HZ[k % len(self.HOWL_HZ)]] for k in range(self.n_stems)]
        self._whistles: Dict[tuple, Tuple[_Biquad, _Biquad, float]] = {}

    def reset(self, seed: Optional[int] = None) -> None:
        self.__init__(self.sr, int(seed) if seed is not None else int(self.rng.randint(1, 2**31 - 1)), self.n_stems)

    # -- levels (outdoor amplitude, before mix_wind and the house) --------
    @staticmethod
    def rush_amp(u_ms: np.ndarray) -> np.ndarray:
        return (np.maximum(0.0, u_ms) / 10.0) ** 2.0

    @staticmethod
    def roar_amp(u_ms: np.ndarray) -> np.ndarray:
        return (np.maximum(0.0, u_ms) / 10.0) ** 2.2

    @staticmethod
    def howl_amp(u_ms: np.ndarray) -> np.ndarray:
        """Needs a breeze to start moaning, then rises steeply with speed."""
        on = np.clip((np.maximum(0.0, u_ms) - 1.5) / 5.0, 0.0, 1.0)
        on = on * on * (3.0 - 2.0 * on)
        return on * (np.maximum(0.0, u_ms) / 10.0) ** 2.5

    @staticmethod
    def whistle_amp(u_ms: np.ndarray, gap_m: float, open_amt: float) -> np.ndarray:
        """A slot sings once there's enough wind; tiny gaps sing best."""
        if open_amt <= 0.0:
            crack = 0.10          # leaky frame of a shut window
        else:
            g = max(0.002, float(gap_m))
            crack = math.exp(-g / 0.045) * min(1.0, g / 0.004)
        on = np.clip((u_ms - 6.0) / 9.0, 0.0, 1.0)
        on = on * on * (3.0 - 2.0 * on)
        return crack * on * (np.maximum(0.0, u_ms) / 12.0) ** 1.5

    def _render_howl(self, k: int, u: np.ndarray, n: int) -> np.ndarray:
        """Unit-level moan for stem ``k``: band-passed noise at a few broad
        resonances that glide with the gusting speed."""
        sr = self.sr
        src = self.rng.randn(n)
        out = np.zeros(n)
        bands = self._howl[k]
        for s0 in range(0, n, self.SUB):
            s1 = min(n, s0 + self.SUB)
            gl = (max(1.0, float(np.mean(u[s0:s1]))) / 10.0) ** 0.8
            seg = src[s0:s1]
            for j, (bq, f10) in enumerate(bands):
                f = f10 * gl
                # A band-pass of peak 1 passes ~sqrt(pi f / (Q sr)) of white noise
                g = 1.0 / math.sqrt(math.pi * f / (self.HOWL_Q * sr)) / math.sqrt(len(bands))
                out[s0:s1] += bq(seg, f, self.HOWL_Q, sr) * g * (1.0 if j == 0 else 0.7)
        return out

    def render(self, speed01: np.ndarray, whistlers: Sequence[Tuple[tuple, float, float, float]] = ()
               ) -> Tuple[np.ndarray, np.ndarray, Dict[tuple, np.ndarray]]:
        """(rush stems n_stems × n, roar n, {aperture key: whistle n}).

        ``whistlers``: (aperture key, gap m, open 0..1, exposure 0..1) for each
        window the wind can sing through."""
        u = wind_ms(speed01)
        n = len(u)
        sr = self.sr
        if n == 0 or float(np.max(u)) < 0.3:
            return np.zeros((self.n_stems, n)), np.zeros(n), {}
        u_blk = float(np.mean(u))
        # --- rush: white → HP 140 Hz → LP that opens with speed, leafy flutter
        fc = 600.0 + 260.0 * u_blk          # 5 m/s ≈ 1.9 kHz, 20 m/s ≈ 5.8 kHz
        norm = 1.0 / max(1e-6, _lp1_gain(fc, sr))
        fl = self.rng.randn(n)
        fl = self._flutter2(self._flutter(fl, 14.0, sr), 22.0, sr) / max(1e-6, _lp1_gain(14.0, sr)) * 1.6
        depth = 0.22 + 0.25 * min(1.0, u_blk / 15.0)
        flutter = np.clip(1.0 + depth * fl, 0.25, 2.2)
        amp = self.rush_amp(u) * flutter
        rush = np.empty((self.n_stems, n))
        h_amp = self.howl_amp(u)
        howl_on = float(np.max(h_amp)) > 1e-4
        for k in range(self.n_stems):
            w = self.rng.randn(n)
            hp = w - self._rush_hp[k](w, 140.0, sr)
            y = self._rush_lp2[k](self._rush_lp[k](hp, fc, sr), fc * 1.6, sr) * norm
            rush[k] = y * amp
            if howl_on:
                rush[k] += self._render_howl(k, u, n) * h_amp
        # --- roar: the deep moving air, 40-200 Hz (not sub-bass you can't hear)
        w = self.rng.randn(n)
        r = self._roar_lp2(self._roar_lp(w, 110.0 + 4.0 * u_blk, sr), 220.0, sr)
        r = r - self._roar_hp(r, 40.0, sr)
        roar = r / max(1e-6, _lp1_gain(110.0 + 4.0 * u_blk, sr)) * 1.4 * self.roar_amp(u)
        # --- whistles
        sing: Dict[tuple, np.ndarray] = {}
        alive = set()
        for key, gap, o, expo in whistlers:
            amp_w = self.whistle_amp(u, gap, o) * expo
            if float(np.max(amp_w)) < 1e-4:
                continue
            alive.add(key)
            st = self._whistles.get(key)
            if st is None:
                # Each slot has its own voice: narrower gaps ring higher
                h = (hash(key) & 0xFFFF) / 65535.0
                g = max(0.004, float(gap)) if o > 0 else 0.006
                f0 = 380.0 * (0.03 / g) ** 0.28 * (0.85 + 0.3 * h)
                st = (_Biquad(), _Biquad(), f0)
                self._whistles[key] = st
            bq, bq2, f0 = st
            out = np.empty(n)
            src = self.rng.randn(n)
            for s0 in range(0, n, self.SUB):
                s1 = min(n, s0 + self.SUB)
                uu = float(np.mean(u[s0:s1]))
                f = f0 * (max(1.0, uu) / 12.0) ** 0.75
                seg = src[s0:s1]
                # Main tone + a weaker overtone; Q high enough to sing, low enough to stay airy
                out[s0:s1] = bq(seg, f, 22.0, sr) + 0.35 * bq2(seg, f * 2.03, 26.0, sr)
            sing[key] = out * amp_w * 5.0
        for key in [k for k in self._whistles if k not in alive]:
            del self._whistles[key]
        return rush, roar, sing
