"""
Thunder: lightning strikes from a storm around the house.

How a strike sounds (the tortuous-channel picture, Ribner & Roy 1982):

  The lightning channel is a crooked line from the cloud (2–5 km up) to the
  ground, made of short straight pieces. Each piece bursts like a long spark
  and sends out a short pressure kick (an N-wave). Pieces lying square-on to
  you send it your way much harder than ones pointing at you, so a crooked
  channel gives a string of claps and peals rather than one bang. Pieces are
  at different distances, so their kicks arrive over seconds: the rumble is
  the channel's length unrolled in time. A near strike starts with a sharp
  tearing crack (the bottom of the channel, close and bright); a far one is
  only the low rumble (air soaks up the highs over kilometres, and the kicks
  stretch out as they travel). Several return strokes down the same channel
  50–100 ms apart smear each peal. Hills, houses and the cloud base echo it.

  Light is instant, sound takes ~3 s per km: the flash comes first.

``ThunderStorm`` decides when and where strikes happen (Thunder: How often /
Distance), renders each one on a worker thread well before its sound is
due, and hands the engine the slice of every strike that is sounding now,
with the bearing it comes from. Flashes are kept for the 3D view.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

try:
    from scipy import signal as _sps
except Exception:  # pragma: no cover
    _sps = None

C_SOUND = 343.0
MIN_KM, MAX_KM = 0.25, 14.0
# How long before a strike's flash we start rendering it (s)
_LOOKAHEAD_S = 3.0


def rate_per_min(rate01: float) -> float:
    """Thunder "How often" 0..1 → strikes per minute (0 = none)."""
    r = max(0.0, min(1.0, float(rate01)))
    if r <= 0.005:
        return 0.0
    # 0.1 ≈ one every 3 min, 0.5 ≈ every 40 s, 1.0 ≈ every 8 s
    return 60.0 / (240.0 * (0.033 ** r))


def storm_km(distance01: float) -> float:
    """Thunder "Distance" 0..1 → typical strike distance (km)."""
    d = max(0.0, min(1.0, float(distance01)))
    return 0.35 * (32.0 ** d)  # 0 → 0.35 km (overhead), 0.5 → 2 km, 1 → 11 km


def _absorb_hz(r_m: float) -> float:
    """Above this the air has soaked up the sound over ``r_m`` (and the
    kicks have stretched): ~5 kHz at 300 m, 1.4 kHz at 1 km, 250 Hz at 8 km."""
    return float(np.clip(1400.0 * (1000.0 / max(50.0, r_m)) ** 0.85, 90.0, 9000.0))


def _n_wave(tau_s: float, sr: int) -> np.ndarray:
    """One pressure kick: a sharp rise, a ramp down through zero, a recovery."""
    n = max(3, int(round(tau_s * sr)))
    x = np.linspace(1.0, -1.0, n)
    # Round the corners a little (a real shock has finite rise)
    k = max(1, n // 12)
    w = np.hanning(2 * k + 1)
    w /= w.sum()
    return np.convolve(np.concatenate([[0.0], x, [0.0]]), w, mode="same")


def _channel(rng, ground_m: float, cloud_m: float, seg_m: float) -> np.ndarray:
    """Points of a crooked channel from the ground strike point (at
    ``ground_m`` along +x from the listener) up to the cloud, plus a
    horizontal run inside the cloud. Returns (N, 3) points (x, y=up, z)."""
    pts = [np.array([ground_m, 0.0, 0.0])]
    d = np.array([0.0, 1.0, 0.0])
    p = pts[0].copy()
    # Mean ~16° between successive pieces (measured tortuosity)
    while p[1] < cloud_m:
        d = d + rng.normal(0.0, 0.30, 3)
        d[1] = abs(d[1]) + 0.45  # keep climbing
        d /= np.linalg.norm(d)
        p = p + d * seg_m * rng.uniform(0.5, 1.5)
        pts.append(p.copy())
    # Intra-cloud run: long, mostly horizontal, sometimes kilometres
    run = rng.uniform(0.3, 1.0) * cloud_m * rng.choice([0.4, 1.0, 1.8])
    h = rng.normal(0.0, 1.0, 3)
    h[1] = 0.0
    h /= np.linalg.norm(h) + 1e-9
    travelled = 0.0
    while travelled < run:
        h = h + rng.normal(0.0, 0.25, 3)
        h[1] = 0.12 * h[1] + rng.normal(0.0, 0.08)
        h /= np.linalg.norm(h)
        step = seg_m * rng.uniform(0.6, 1.6)
        p = p + h * step
        travelled += step
        pts.append(p.copy())
    return np.array(pts)


def render_strike(dist_m: float, sr: int = 48000, seed: int = 1, strokes: Optional[int] = None,
                  echo: float = 0.35) -> Tuple[np.ndarray, float]:
    """Mono thunder of one strike, heard at ground level ``dist_m`` from where
    it hits. Returns (signal starting at the first arrival, first arrival
    delay in s after the flash). Level is physical-ish (∝ 1/r inside the
    strike), scaled so a strike 1 km away peaks around 1.0."""
    rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
    dist_m = float(max(60.0, dist_m))
    cloud = float(rng.uniform(2000.0, 4500.0))
    pts = _channel(rng, dist_m, cloud, seg_m=float(rng.uniform(18.0, 40.0)))
    mids = 0.5 * (pts[1:] + pts[:-1])
    segs = pts[1:] - pts[:-1]
    seg_len = np.linalg.norm(segs, axis=1) + 1e-9
    to_obs = -mids + np.array([0.0, 1.6, 0.0])
    r = np.linalg.norm(to_obs, axis=1) + 1e-9
    cosang = np.abs(np.sum(segs * to_obs, axis=1) / (seg_len * r))
    # Pieces square-on to you shout, end-on ones whisper
    direc = 0.08 + 0.92 * np.exp(-(cosang / 0.30) ** 2)
    # Energy per piece varies (branching, current): log-normal
    energy = rng.lognormal(0.0, 0.55, len(r))
    # Low in the channel the current is strongest
    height = mids[:, 1]
    energy *= 1.0 + 0.6 * np.exp(-height / 600.0)
    amp = direc * energy * seg_len / r
    t_arr = r / C_SOUND
    t0 = float(t_arr.min())
    first_delay = t0
    # Kick length is set by how hard each piece bursts (its relaxation
    # radius, metres) and stretches a little as it travels: ~5 ms close,
    # ~9 ms after 5 km, so the rumble sits at 50-120 Hz (measured thunder),
    # not in the 250 Hz growl band. Spread wide so no one length rings.
    tau = 0.0065 * (r / 1000.0) ** 0.3
    tau = np.clip(tau * rng.lognormal(0.0, 0.45, len(r)), 0.0015, 0.035)
    n_strokes = strokes if strokes is not None else int(rng.choice([1, 2, 3, 3, 4, 5]))
    stroke_t = [0.0]
    stroke_a = [1.0]
    for _ in range(n_strokes - 1):
        stroke_t.append(stroke_t[-1] + float(rng.uniform(0.035, 0.11)))
        stroke_a.append(float(rng.uniform(0.35, 0.8)))
    span = float(t_arr.max() - t0) + stroke_t[-1] + 0.25
    tail = 2.6 if echo > 0 else 0.3
    n = int((span + tail) * sr)
    out = np.zeros(n)
    # Group pieces by distance (shared air filter) and kick length
    edges = np.geomspace(max(1.0, r.min()) * 0.999, r.max() * 1.001, 9)
    gi = np.clip(np.searchsorted(edges, r) - 1, 0, len(edges) - 2)
    t_edges = np.geomspace(0.0015 * 0.999, 0.035 * 1.001, 6)
    ti = np.clip(np.searchsorted(t_edges, tau) - 1, 0, len(t_edges) - 2)
    groups = [(g, k) for g in range(len(edges) - 1) for k in range(len(t_edges) - 1)]
    for g, k in groups:
        sel = (gi == g) & (ti == k)
        if not np.any(sel):
            continue
        imp = np.zeros(n)
        for j, (st, sa) in enumerate(zip(stroke_t, stroke_a)):
            # Each return stroke lights the channel differently and a little
            # off in time, so the strokes smear the peals instead of
            # repeating them exactly (an exact echo 35-110 ms later buzzes)
            jit = 0.0 if j == 0 else rng.normal(0.0, 0.004, int(sel.sum()))
            a_s = amp[sel] * sa * (1.0 if j == 0 else rng.lognormal(0.0, 0.6, int(sel.sum())))
            idx = ((t_arr[sel] - t0 + st + jit) * sr).astype(np.int64)
            ok = (idx >= 0) & (idx < n)
            np.add.at(imp, idx[ok], a_s[ok])
        tau_g = float(np.median(tau[sel]))
        kick = _n_wave(tau_g, sr)
        if _sps is not None:
            y = _sps.oaconvolve(imp, kick)[:n]
            r_g = float(np.median(r[sel]))
            sos = _sps.butter(2, _absorb_hz(r_g) / (0.5 * sr), "low", output="sos")
            y = _sps.sosfilt(sos, y)
        else:  # pragma: no cover
            y = np.convolve(imp, kick)[:n]
        out += y
    # Near strikes: the tearing crack of the channel's bottom (many tiny
    # branches), bright and short; gone beyond ~1.5 km
    near = math.exp(-dist_m / 650.0)
    if near > 0.02:
        # A ripping canvas: a few bursts of crackle over the first ~half
        # second, each a cluster of tiny branch snaps, as loud as the boom
        k_n = int(rng.uniform(0.35, 0.65) * sr)
        crack = np.zeros(k_n)
        t_b = 0.0
        while t_b < k_n / sr - 0.05:
            m = int(rng.uniform(0.05, 0.14) * sr)
            i0 = int(t_b * sr)
            m = min(m, k_n - i0)
            burst = rng.randn(m) * np.exp(-np.arange(m) / (rng.uniform(0.02, 0.05) * sr))
            burst *= (rng.rand(m) < 0.18)
            crack[i0:i0 + m] += burst * rng.uniform(0.4, 1.0) * math.exp(-t_b / 0.25)
            t_b += rng.uniform(0.04, 0.16)
        if _sps is not None:
            hi = min(0.95, _absorb_hz(dist_m) * 2.2 / (0.5 * sr))
            sos = _sps.butter(2, [300.0 / (0.5 * sr), hi], "band", output="sos")
            crack = _sps.sosfilt(sos, crack)
        peak = float(np.max(np.abs(out[: int(0.6 * sr)])) + 1e-9)
        out[:k_n] += crack / (np.max(np.abs(crack)) + 1e-9) * peak * 1.5 * near
    # Echoes off the land and cloud base: a dark diffuse tail
    if echo > 0 and _sps is not None:
        kn = int(tail * sr)
        t = np.arange(kn) / sr
        ir = rng.randn(kn) * np.exp(-t * 6.9 / 2.4)  # T60 ≈ 2.4 s
        ir[: int(0.04 * sr)] = 0.0
        sos = _sps.butter(2, min(600.0, _absorb_hz(dist_m)) / (0.5 * sr), "low", output="sos")
        ir = _sps.sosfilt(sos, ir)
        ir /= math.sqrt(float(np.sum(ir * ir)) + 1e-12)
        wet = _sps.oaconvolve(out, ir)[:n]
        out = out + echo * wet
    # Fade the very end
    f = min(n, int(0.2 * sr))
    out[-f:] *= np.linspace(1.0, 0.0, f)
    # 1 km reference: amp ∝ seg_len / r, so scale by a fixed constant. The
    # long kicks carry more energy the farther the strike (the air no longer
    # strips most of it): trim so loudness by distance stays as tuned.
    lr = max(0.0, math.log10(dist_m / 400.0))
    trim = 10.0 ** (-(7.4 + 4.3 * min(1.0, lr / 0.57)) / 20.0)
    return out * 22.0 * trim, first_delay


def tame(x: np.ndarray, sr: int, crest_db: float = 13.0, ratio: float = 4.0) -> np.ndarray:
    """Look-ahead peak compressor for a whole strike (rendered ahead, so it
    can see peaks coming): squeezes the loudest cracks to sit at most about
    ``crest_db`` over the body of the rumble, without pumping or clipping."""
    x = np.asarray(x, dtype=np.float64)
    if x.size < 16:
        return x
    w = max(8, int(0.004 * sr))
    a = np.abs(x)
    # Peak hold over ±4 ms (look-ahead), then a smooth release
    if _sps is not None:
        from scipy.ndimage import maximum_filter1d
        env = maximum_filter1d(a, size=2 * w + 1)
    else:  # pragma: no cover
        env = np.array([a[max(0, i - w): i + w + 1].max() for i in range(len(a))])
    body = float(np.sqrt(np.mean(x[a > 0.05 * a.max()] ** 2)) + 1e-12)
    thr = body * 10.0 ** (crest_db / 20.0 / 2.0)
    over = np.maximum(env / thr, 1.0)
    g = over ** (1.0 / ratio - 1.0)
    # Smooth the gain: fast attack is already in the look-ahead, release ~60 ms
    if _sps is not None:
        k = int(0.06 * sr)
        win = np.hanning(2 * k + 1)
        win /= win.sum()
        g = np.minimum(g, _sps.oaconvolve(g, win, mode="same"))
        g = _sps.oaconvolve(g, np.hanning(2 * w + 1) / np.hanning(2 * w + 1).sum(), mode="same")
    return x * np.clip(g, 0.0, 1.0)


@dataclass
class Strike:
    t_flash: float          # engine time of the flash (s)
    dist_m: float
    az_deg: float           # bearing it comes FROM (0 = N, 90 = E)
    el_deg: float
    seed: int
    t_sound: float = 0.0    # engine time the first sound arrives
    sig: Optional[np.ndarray] = None
    pos: int = 0
    flashes: List[Tuple[float, float]] = field(default_factory=list)  # (time, brightness)
    rendering: bool = False

    @property
    def done(self) -> bool:
        return self.sig is not None and self.pos >= len(self.sig)


class ThunderStorm:
    """Schedules, renders and plays strikes."""

    def __init__(self, sr: int = 48000, seed: int = 99):
        self.sr = int(sr)
        self.rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
        self.strikes: List[Strike] = []
        self._t_next: Optional[float] = None
        self._bearing = float(self.rng.uniform(0.0, 360.0))
        self._lock = threading.Lock()
        self.render_sync = False   # True: render on the calling thread (offline)

    def reset(self, seed: Optional[int] = None) -> None:
        s = int(seed) if seed is not None else int(self.rng.randint(1, 2**31 - 1))
        self.__init__(self.sr, s)

    def _schedule(self, now: float, rate01: float, dist01: float, wind_from: Optional[float]) -> None:
        per_min = rate_per_min(rate01)
        if per_min <= 0.0:
            self._t_next = None
            return
        if self._t_next is None:
            # First one soon-ish after thunder is switched on
            self._t_next = now + _LOOKAHEAD_S + float(self.rng.uniform(1.0, min(20.0, 60.0 / per_min)))
        while self._t_next is not None and self._t_next - now < _LOOKAHEAD_S + 0.5:
            km = storm_km(dist01) * float(self.rng.lognormal(0.0, 0.45))
            km = max(MIN_KM, min(MAX_KM, km))
            # The storm sits roughly upwind and drifts round slowly
            if wind_from is not None:
                self._bearing += 0.15 * (((wind_from - self._bearing + 540.0) % 360.0) - 180.0)
            self._bearing = (self._bearing + float(self.rng.normal(0.0, 12.0))) % 360.0
            az = (self._bearing + float(self.rng.normal(0.0, 35.0))) % 360.0
            el = math.degrees(math.atan2(1500.0, km * 1000.0)) * 0.6
            s = Strike(self._t_next, km * 1000.0, az, el, int(self.rng.randint(1, 2**31 - 1)))
            # Flicker: a flash per return stroke
            t = s.t_flash
            for k in range(int(self.rng.choice([1, 2, 3, 3, 4]))):
                s.flashes.append((t, float(self.rng.uniform(0.6, 1.0)) * (1.0 if k == 0 else 0.7)))
                t += float(self.rng.uniform(0.04, 0.12))
            with self._lock:
                self.strikes.append(s)
            gap = float(self.rng.exponential(60.0 / per_min))
            self._t_next += max(3.5, gap)

    def _render(self, s: Strike) -> None:
        try:
            sig, delay = render_strike(s.dist_m, self.sr, s.seed)
            sig = tame(sig, self.sr)
        except Exception:  # pragma: no cover - never take the audio down
            sig, delay = np.zeros(1), 0.0
        s.t_sound = s.t_flash + delay
        s.sig = sig

    def update(self, now: float, rate01: float, dist01: float, wind_from: Optional[float] = None) -> None:
        """Plan strikes and start rendering the ones coming up."""
        self._schedule(now, rate01, dist01, wind_from)
        for s in list(self.strikes):
            if s.sig is None and not s.rendering:
                s.rendering = True
                if self.render_sync:
                    self._render(s)
                else:
                    threading.Thread(target=self._render, args=(s,), name="Thunder", daemon=True).start()

    def take(self, t0: float, n: int) -> List[Tuple[Strike, np.ndarray]]:
        """The part of each strike that sounds in [t0, t0 + n/sr)."""
        out = []
        sr = self.sr
        with self._lock:
            strikes = list(self.strikes)
        keep = []
        for s in strikes:
            if s.sig is None:
                # Sound not ready yet; it'll start late rather than never
                keep.append(s)
                continue
            start = int(round((s.t_sound - t0) * sr)) if s.pos == 0 else 0
            if start >= n:
                keep.append(s)
                continue
            buf = np.zeros(n)
            if start < 0 and s.pos == 0:
                # Late (render took too long): skip what's already past
                s.pos = min(len(s.sig), -start)
                start = 0
            m = min(n - start, len(s.sig) - s.pos)
            if m > 0:
                buf[start:start + m] = s.sig[s.pos:s.pos + m]
                s.pos += m
            out.append((s, buf))
            if not s.done:
                keep.append(s)
        with self._lock:
            self.strikes = [s for s in self.strikes if s in keep]
        return out

    def flashes_between(self, t0: float, t1: float) -> List[Tuple[float, float, float, float, int]]:
        """(time, brightness, bearing, km, strike seed) of flashes in [t0, t1)."""
        with self._lock:
            strikes = list(self.strikes)
        out = []
        for s in strikes:
            for t, b in s.flashes:
                if t0 <= t < t1:
                    out.append((t, b * min(1.0, 1.6 / max(0.3, s.dist_m / 1000.0)) ** 0.5, s.az_deg, s.dist_m / 1000.0,
                                int(s.seed)))
        return out


__all__ = ["ThunderStorm", "Strike", "rate_per_min", "render_strike", "storm_km"]
