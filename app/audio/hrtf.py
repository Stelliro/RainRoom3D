"""
Parametric HRTF-style binauraliser (no external SOFA dataset).

Not a measured HRTF — a compact Woodworth / pinna model that is clearly
better than equal-power pan alone:

  • Interaural time difference (head-radius geometry)
  • Frequency-tilted interaural level difference (shadow)
  • Pinna elevation notch (moves with elevation)
  • Rear darkening / shoulder shadow
  • Near-field ILD boost with distance
"""

from __future__ import annotations

import math
from typing import Tuple

import numpy as np

try:
    from scipy import signal as sp_signal
    _HAS_SCIPY = True
except Exception:  # pragma: no cover
    sp_signal = None
    _HAS_SCIPY = False

# Average adult head half-width (m) for Woodworth ITD
HEAD_RADIUS_M = 0.0875
C_SOUND = 343.0


def _wrap_pi(a: float) -> float:
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


def _one_pole_lp(x: np.ndarray, fc: float, sr: float) -> np.ndarray:
    if fc <= 0 or sr <= 0 or len(x) == 0:
        return x
    x = np.asarray(x, dtype=np.float64)
    dt = 1.0 / sr
    rc = 1.0 / (2.0 * math.pi * fc)
    a = dt / (rc + dt)
    if _HAS_SCIPY:
        return sp_signal.lfilter([a], [1.0, -(1.0 - a)], x)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = acc + a * (float(v) - acc)
        y[i] = acc
    return y


def _one_pole_hp(x: np.ndarray, fc: float, sr: float) -> np.ndarray:
    if fc <= 0 or sr <= 0 or len(x) == 0:
        return x
    x = np.asarray(x, dtype=np.float64)
    dt = 1.0 / sr
    rc = 1.0 / (2.0 * math.pi * fc)
    a = rc / (rc + dt)
    if _HAS_SCIPY:
        return sp_signal.lfilter([a, -a], [1.0, -a], x)
    y = np.empty_like(x)
    hy, hx = 0.0, float(x[0])
    for i, v in enumerate(x):
        hy = a * (hy + float(v) - hx)
        hx = float(v)
        y[i] = hy
    return y


def _biquad_notch(x: np.ndarray, f0: float, q: float, sr: float, depth: float = 0.55) -> np.ndarray:
    """Soft notch (pinna). depth 0=none, 1=full biquad notch."""
    if f0 <= 40.0 or f0 >= 0.45 * sr or len(x) < 8 or depth <= 0.02:
        return x
    x = np.asarray(x, dtype=np.float64)
    if not _HAS_SCIPY:
        # Cheap spectral hole: mix original with band-rejected version
        narrow = _one_pole_lp(_one_pole_hp(x, f0 * 0.7, sr), f0 * 1.35, sr)
        return x - narrow * (0.45 * depth)
    w0 = 2.0 * math.pi * f0 / sr
    cosw = math.cos(w0)
    sinw = math.sin(w0)
    alpha = sinw / (2.0 * max(0.4, q))
    b0 = 1.0
    b1 = -2.0 * cosw
    b2 = 1.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cosw
    a2 = 1.0 - alpha
    b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float64)
    a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64)
    y = sp_signal.lfilter(b, a, x)
    # Blend so we never dig a surgical hole
    d = max(0.0, min(1.0, depth))
    return (1.0 - d) * x + d * y


def woodworth_itd_s(azimuth_rad: float, head_r: float = HEAD_RADIUS_M) -> float:
    """Signed ITD in seconds (positive → right ear delayed, source on left)."""
    az = _wrap_pi(float(azimuth_rad))
    # Clamp to frontal sphere model
    az_c = max(-math.pi / 2, min(math.pi / 2, az))
    # Woodworth: (r/c) * (sin θ + θ)
    return (head_r / C_SOUND) * (math.sin(az_c) + az_c)


def ild_db(azimuth_rad: float, elevation_rad: float = 0.0) -> float:
    """Broadband ILD in dB (positive → louder in right ear)."""
    az = _wrap_pi(float(azimuth_rad))
    el = float(elevation_rad)
    # Sin law with reduced lateralisation overhead
    base = 12.0 * math.sin(az)
    # Elevation reduces ILD slightly (source more above head)
    base *= 0.75 + 0.25 * math.cos(el)
    # Rear hemisphere: slightly less ILD (more ambiguous)
    if math.cos(az) < 0.0:
        base *= 0.82
    return base


def pinna_notch_hz(elevation_rad: float) -> float:
    """Approx pinna notch centre: higher when source is elevated."""
    el = max(-0.6, min(1.2, float(elevation_rad)))
    # ~7.5 kHz at horizon, rises with elevation, drops slightly below
    return 7500.0 + 2200.0 * el - 400.0 * max(0.0, -el)


def apply_hrtf(
    mono_src: np.ndarray,
    azimuth_rad: float,
    elevation_rad: float = 0.0,
    distance: float = 1.5,
    sr: int = 48000,
) -> np.ndarray:
    """Mono → stereo binaural using parametric HRTF cues.

    az 0 = front, negative = left, positive = right (listener-relative).
    """
    x = np.asarray(mono_src, dtype=np.float64).reshape(-1)
    n = x.shape[0]
    if n == 0:
        return np.zeros((0, 2), dtype=np.float64)

    az = _wrap_pi(float(azimuth_rad))
    el = float(elevation_rad)
    dist = max(0.2, float(distance))
    sr = int(sr)

    # --- Front / rear factor ---
    front = math.cos(az)  # +1 front, -1 rear
    rear = max(0.0, -front)

    # --- ILD (linear gains) + near-field boost ---
    ild = ild_db(az, el)
    # Near sources exaggerate ILD a little
    near = 1.0 + 0.55 * max(0.0, (1.2 - dist) / 1.2)
    ild *= near
    gL = 10.0 ** ((-ild) / 20.0)
    gR = 10.0 ** ((+ild) / 20.0)
    # Equal-power floor so centre stays solid
    pan = math.sin(az)
    eqL = math.cos((pan + 1.0) * 0.25 * math.pi)
    eqR = math.sin((pan + 1.0) * 0.25 * math.pi)
    gL = 0.55 * gL + 0.45 * eqL
    gR = 0.55 * gR + 0.45 * eqR
    # Keep power ~1 so loud lateral sources don't clip the bus
    pnorm = math.sqrt(gL * gL + gR * gR) + 1e-12
    gL *= math.sqrt(2.0) / pnorm
    gR *= math.sqrt(2.0) / pnorm

    # --- Head shadow (contralateral darken) ---
    # Contralateral ear gets more HF cut
    if az >= 0.0:
        # source right → left is contralateral
        fc_ipsi, fc_contra = 11000.0, max(900.0, 3800.0 - 2200.0 * abs(math.sin(az)))
        x_r = _one_pole_lp(x, fc_ipsi, sr)
        x_l = _one_pole_lp(x, fc_contra, sr)
    else:
        fc_ipsi, fc_contra = 11000.0, max(900.0, 3800.0 - 2200.0 * abs(math.sin(az)))
        x_l = _one_pole_lp(x, fc_ipsi, sr)
        x_r = _one_pole_lp(x, fc_contra, sr)

    # --- Pinna elevation notch (both ears, slight L/R offset) ---
    f_notch = pinna_notch_hz(el)
    depth = 0.35 + 0.35 * abs(math.sin(el + 0.15))
    x_l = _biquad_notch(x_l, f_notch * 0.97, q=2.2, sr=sr, depth=depth)
    x_r = _biquad_notch(x_r, f_notch * 1.03, q=2.2, sr=sr, depth=depth)

    # --- Rear / behind head: darken + slight muffling ---
    if rear > 0.05:
        dark = 1.0 - 0.28 * rear
        x_l = _one_pole_lp(x_l, 4500.0 + 2500.0 * (1.0 - rear), sr) * dark
        x_r = _one_pole_lp(x_r, 4500.0 + 2500.0 * (1.0 - rear), sr) * dark
        # Ambiguous rear: pull slightly toward centre
        mid = 0.5 * (x_l + x_r)
        blend = 0.18 * rear
        x_l = (1.0 - blend) * x_l + blend * mid
        x_r = (1.0 - blend) * x_r + blend * mid

    # --- Elevation overall brightness ---
    # High sources a bit brighter; low sources darker
    elev_fc = 9000.0 + 3500.0 * max(-0.4, min(1.0, el))
    x_l = _one_pole_lp(x_l, elev_fc, sr)
    x_r = _one_pole_lp(x_r, elev_fc, sr)

    # --- ITD (fractional delay via integer + linear interp) ---
    itd_s = woodworth_itd_s(az)
    # Positive itd_s → delay right (source left)
    delay_r = max(0.0, itd_s) * sr
    delay_l = max(0.0, -itd_s) * sr

    def _frac_delay(sig: np.ndarray, d: float) -> np.ndarray:
        if d < 0.01 or len(sig) == 0:
            return sig
        di = int(d)
        frac = d - di
        if di >= len(sig):
            return np.zeros_like(sig)
        y = np.zeros_like(sig)
        if di > 0:
            y[di:] = sig[: len(sig) - di]
        else:
            y[:] = sig
        if frac > 1e-4 and di + 1 < len(sig):
            y2 = np.zeros_like(sig)
            y2[di + 1 :] = sig[: len(sig) - (di + 1)]
            y = (1.0 - frac) * y + frac * y2
        return y

    x_l = _frac_delay(x_l, delay_l)
    x_r = _frac_delay(x_r, delay_r)

    # --- Distance attenuation (gentle) ---
    if dist <= 0.8:
        att = 1.0
    else:
        att = 0.8 / (0.8 + 1.0 * (dist - 0.8))
    # Air-ish HF roll for distant indoor windows
    if dist > 1.5:
        fc_air = max(1800.0, 10000.0 / (1.0 + 0.25 * (dist - 1.5)))
        x_l = _one_pole_lp(x_l, fc_air, sr)
        x_r = _one_pole_lp(x_r, fc_air, sr)

    elev_k = 0.88 + 0.12 * math.cos(el)
    y = np.column_stack([x_l * gL * att * elev_k, x_r * gR * att * elev_k])
    return y


def hrtf_gains_preview(azimuth_rad: float, elevation_rad: float = 0.0) -> Tuple[float, float, float]:
    """Debug helper: (gL, gR, itd_ms)."""
    ild = ild_db(azimuth_rad, elevation_rad)
    gL = 10.0 ** ((-ild) / 20.0)
    gR = 10.0 ** ((+ild) / 20.0)
    return gL, gR, woodworth_itd_s(azimuth_rad) * 1000.0
