
"""
Droplet Audio Engine v3.2.0 — Single-body accents + continuous wash

v3.1 failure: multi-layer drops (slap/splat/splash/spray) stacked with a
tick-heavy bed and ~200 ips → muddy, broken “layering”.

v3.2 design
-----------
  • Continuous outdoor field (WAV loops preferred) is the *main* rain.
  • Each discrete drop is **one** soft brown body — no multi-layer stack.
  • Low impact rate (~50–90/s max) so accents don’t fuse into hash.
  • Drop samples / bed through window portals (see spatial_engine).

CLI:
  python -m app.audio.engine --single-drop --surface water --size-mm 3.5
  python -m app.audio.engine --render 120 --seconds 4 --out out/rain.wav
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import struct
import wave
from collections import deque

import numpy as np

try:
    from scipy import signal as sp_signal
    _HAS_SCIPY = True
except Exception:  # pragma: no cover
    sp_signal = None
    _HAS_SCIPY = False

PI2 = 2.0 * math.pi

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _db(x: float) -> float:
    """dB to linear amplitude."""
    return 10.0 ** (x / 20.0)


def _softclip(x, drive=1.0):
    return np.tanh(x * drive)


def _norm_peak(x, target=0.944):
    pk = np.max(np.abs(x)) + 1e-12
    return x * (target / pk)


def _onset_ramp(n, attack_samples, roundness=1.0):
    """Raised-cosine onset ramp."""
    if attack_samples < 1 or n < 1:
        return np.ones(max(1, n), dtype=np.float64)
    env = np.ones(n, dtype=np.float64)
    a = min(int(attack_samples), n)
    t = np.linspace(0.0, 1.0, a, dtype=np.float64)
    env[:a] = 0.5 - 0.5 * np.cos(math.pi * (t ** max(0.1, float(roundness))))
    return env


def _det_noise(n, seed):
    """Deterministic Gaussian noise from a seed."""
    h = hashlib.sha256(struct.pack(">I", int(seed) & 0xFFFFFFFF)).digest()
    rng = np.random.RandomState(int.from_bytes(h[:4], "big") & 0x7FFFFFFF)
    return rng.randn(int(n)).astype(np.float64)


def _det_u01(seed, idx=0):
    """Deterministic float in [0, 1)."""
    h = hashlib.md5(f"{seed}:{idx}".encode()).digest()
    return (h[0] + h[1] * 256) / 65536.0


def _det_phase(seed, idx):
    return _det_u01(seed, idx) * PI2


def _pinkish(white):
    """Cheap ~1/f colouring (one-pole integration mix)."""
    a = 0.97
    if _HAS_SCIPY:
        b = np.array([1.0 - a], dtype=np.float64)
        aa = np.array([1.0, -a], dtype=np.float64)
        acc = sp_signal.lfilter(b, aa, white)
        return 0.55 * acc + 0.45 * white
    y = np.empty_like(white)
    acc = 0.0
    for i, w in enumerate(white):
        acc = a * acc + (1.0 - a) * w
        y[i] = 0.55 * acc + 0.45 * w
    return y


def _brownish(white):
    """Brown-ish noise — softer, heavier, less 'plastic' than white/pink."""
    a = 0.995
    if _HAS_SCIPY:
        b = np.array([1.0 - a], dtype=np.float64)
        aa = np.array([1.0, -a], dtype=np.float64)
        return sp_signal.lfilter(b, aa, white)
    y = np.empty_like(white)
    acc = 0.0
    for i, w in enumerate(white):
        acc = a * acc + (1.0 - a) * w
        y[i] = acc
    return y


def _rms_scale(sig, target_rms=0.12):
    """Scale by RMS, capped so near-silence never explodes into distortion."""
    rms = float(np.sqrt(np.mean(sig * sig)) + 1e-12)
    scale = target_rms / rms
    # Never boost more than ~4× — silent grains were blowing up into fuzz
    scale = min(scale, 4.0)
    out = sig * scale
    pk = float(np.max(np.abs(out)) + 1e-12)
    if pk > 0.45:
        out *= 0.45 / pk
    return out


def _peak_cap(sig, peak=0.4):
    """Linear peak limit only — no soft-clip coloration."""
    pk = float(np.max(np.abs(sig)) + 1e-12)
    if pk > peak:
        return sig * (peak / pk)
    return sig


def _soft_env(n, sr, attack_ms, decay_ms, hold_ms=0.0):
    """Smooth attack + exponential decay (no hard corner)."""
    env = np.zeros(n, dtype=np.float64)
    a_n = max(1, int(sr * attack_ms / 1000.0))
    h_n = max(0, int(sr * hold_ms / 1000.0))
    a_n = min(a_n, n)
    # cosine attack
    if a_n > 0:
        t = np.linspace(0.0, 1.0, a_n, dtype=np.float64)
        env[:a_n] = 0.5 - 0.5 * np.cos(math.pi * t)
    end_hold = min(n, a_n + h_n)
    if end_hold > a_n:
        env[a_n:end_hold] = 1.0
    # decay
    if end_hold < n:
        td = np.arange(n - end_hold, dtype=np.float64) / sr
        tau = max(0.003, decay_ms / 1000.0)
        env[end_hold:] = np.exp(-td / tau)
    return env


def _lp1(x, fc, sr):
    """Vectorized one-pole low-pass (6 dB/oct)."""
    if fc <= 0 or sr <= 0 or len(x) == 0:
        return x
    if _HAS_SCIPY:
        # bilinear one-pole via butter is overkill; keep simple recurrence but
        # use numpy for speed of the loop body via lfilter
        dt = 1.0 / sr
        rc = 1.0 / (PI2 * fc)
        alpha = dt / (rc + dt)
        b = np.array([alpha], dtype=np.float64)
        a = np.array([1.0, -(1.0 - alpha)], dtype=np.float64)
        return sp_signal.lfilter(b, a, x)
    rc = 1.0 / (PI2 * fc)
    dt = 1.0 / sr
    alpha = dt / (rc + dt)
    y = np.empty_like(x)
    y[0] = x[0] * alpha
    for i in range(1, len(x)):
        y[i] = y[i - 1] + alpha * (x[i] - y[i - 1])
    return y


def _hp1(x, fc, sr):
    """Vectorized one-pole high-pass (6 dB/oct)."""
    if fc <= 0 or sr <= 0 or len(x) == 0:
        return x
    if _HAS_SCIPY:
        dt = 1.0 / sr
        rc = 1.0 / (PI2 * fc)
        alpha = rc / (rc + dt)
        b = np.array([alpha, -alpha], dtype=np.float64)
        a = np.array([1.0, -alpha], dtype=np.float64)
        return sp_signal.lfilter(b, a, x)
    rc = 1.0 / (PI2 * fc)
    dt = 1.0 / sr
    alpha = rc / (rc + dt)
    y = np.empty_like(x)
    y[0] = x[0]
    for i in range(1, len(x)):
        y[i] = alpha * (y[i - 1] + x[i] - x[i - 1])
    return y


def _bp_butter(x, fc, bw, sr, order=2):
    """Butterworth bandpass (preferred) or simple peaking fallback."""
    if fc <= 0 or sr <= 0 or bw <= 0 or len(x) < 8:
        return x
    lo = max(20.0, fc - 0.5 * bw)
    hi = min(sr * 0.45, fc + 0.5 * bw)
    if hi <= lo:
        return x
    if _HAS_SCIPY:
        try:
            sos = sp_signal.butter(order, [lo, hi], btype="band", fs=sr, output="sos")
            return sp_signal.sosfilt(sos, x)
        except Exception:
            pass
    # Fallback: cascade lp/hp
    mid = _hp1(x, lo, sr)
    return _lp1(mid, hi, sr)


def _resonator_noise(n, sr, f0, q, seed, harm=0.0, tone_mix=0.12):
    """Noise-excited damped resonator.

    tone_mix: 0 → pure filtered-noise body (wet water); higher → more ring
    (hollow tarp / shell). Keep wet tones low so they don't sound plastic.
    """
    if n < 4 or f0 <= 0:
        return np.zeros(max(1, n), dtype=np.float64)

    t = np.arange(n, dtype=np.float64) / sr
    noise = _det_noise(n, seed)
    exc_tau = max(0.0004, 2.5 / max(200.0, f0))
    exc = noise * np.exp(-t / exc_tau)

    bw = max(40.0, f0 / max(1.0, q))
    # Wider BP for wet (low tone_mix); narrower for hollow ring
    bw_scale = 3.2 - 1.4 * max(0.0, min(1.0, tone_mix / 0.45))
    body = _bp_butter(exc, f0, bw * bw_scale, sr, order=2)

    decay_s = max(0.0025, q / (PI2 * f0))
    env = np.exp(-t / decay_s)
    ph = _det_phase(seed, 7)
    sine = np.sin(PI2 * f0 * t + ph)
    if harm > 0:
        sine = sine + harm * np.sin(2.0 * PI2 * f0 * t + _det_phase(seed, 8))
    mod = _lp1(_det_noise(n, seed + 99), min(800.0, f0 * 0.4), sr)
    mpk = np.max(np.abs(mod)) + 1e-12
    mod = mod / mpk
    sine = sine * (0.55 + 0.45 * mod) * env

    body_pk = np.max(np.abs(body)) + 1e-12
    body = body / body_pk
    sine_pk = np.max(np.abs(sine)) + 1e-12
    sine = sine / sine_pk

    tm = max(0.0, min(0.55, float(tone_mix)))
    sig = (1.0 - tm) * body * env + tm * sine
    return sig


# ---------------------------------------------------------------------------
# Surface material profiles
# ---------------------------------------------------------------------------

def _minnaert_freq(bubble_r_m):
    """Minnaert resonance frequency for an air bubble in water."""
    gamma = 1.4
    P0 = 101325.0
    rho = 998.0
    return (1.0 / (PI2 * bubble_r_m)) * math.sqrt(3.0 * gamma * P0 / rho)


# brightness 0..1 only gently opens the top; all profiles stay dark enough
# to avoid plastic-tarp HF slap. bed_gain_db is relative before bed mixer.
_SURFACE = {
    # Default outdoor rain — soft wet water, NOT hollow tarp/shell ring
    "water": dict(
        tone_family="wet",
        plop="minnaert",
        splash_bp=[900.0, 1600.0],
        spray_lp=1400.0,
        q_base=2.2,
        decay_ms=16.0,
        brightness=0.22,
        slap_lp=1800.0,
        body_lp=1200.0,
        plop_chance=0.55,
        tone_mix=0.08,
        bed_lp=2200.0,
        bed_gain_db=-12.0,
    ),
    "glass": dict(
        tone_family="wet",
        plop=[1600.0, 2800.0],
        splash_bp=[1400.0, 2400.0],
        spray_lp=2200.0,
        q_base=3.2,
        decay_ms=12.0,
        brightness=0.40,
        slap_lp=2600.0,
        body_lp=1600.0,
        plop_chance=0.35,
        tone_mix=0.14,
        bed_lp=2800.0,
        bed_gain_db=-13.0,
    ),
    "metal": dict(
        tone_family="wet",
        plop=[1400.0, 2400.0, 3600.0],
        splash_bp=[1600.0, 2800.0],
        spray_lp=2400.0,
        q_base=3.5,
        decay_ms=14.0,
        brightness=0.45,
        slap_lp=2800.0,
        body_lp=1700.0,
        plop_chance=0.40,
        tone_mix=0.16,
        bed_lp=3000.0,
        bed_gain_db=-13.0,
    ),
    "wood": dict(
        tone_family="wet",
        plop=[400.0, 800.0],
        splash_bp=[700.0, 1200.0],
        spray_lp=1100.0,
        q_base=2.4,
        decay_ms=12.0,
        brightness=0.18,
        slap_lp=1400.0,
        body_lp=900.0,
        plop_chance=0.30,
        tone_mix=0.10,
        bed_lp=1600.0,
        bed_gain_db=-11.0,
    ),
    "tile": dict(
        tone_family="wet",
        plop=[900.0, 1600.0],
        splash_bp=[1100.0, 1900.0],
        spray_lp=1800.0,
        q_base=2.8,
        decay_ms=13.0,
        brightness=0.30,
        slap_lp=2000.0,
        body_lp=1300.0,
        plop_chance=0.35,
        tone_mix=0.12,
        bed_lp=2200.0,
        bed_gain_db=-12.0,
    ),
    "shingle": dict(
        tone_family="wet",
        plop=[450.0, 900.0],
        splash_bp=[800.0, 1400.0],
        spray_lp=1200.0,
        q_base=2.5,
        decay_ms=12.0,
        brightness=0.20,
        slap_lp=1500.0,
        body_lp=1000.0,
        plop_chance=0.30,
        tone_mix=0.10,
        bed_lp=1700.0,
        bed_gain_db=-11.0,
    ),
    "brick": dict(
        tone_family="wet",
        plop=[600.0, 1100.0],
        splash_bp=[900.0, 1500.0],
        spray_lp=1400.0,
        q_base=2.6,
        decay_ms=12.0,
        brightness=0.22,
        slap_lp=1600.0,
        body_lp=1100.0,
        plop_chance=0.30,
        tone_mix=0.11,
        bed_lp=1800.0,
        bed_gain_db=-11.0,
    ),
    # Special: drip off object onto tarp / plastic — hollow mid ring (rare)
    "tarp": dict(
        tone_family="hollow",
        plop=[650.0, 1100.0, 1600.0],
        splash_bp=[1200.0, 2000.0],
        spray_lp=2400.0,
        q_base=5.5,
        decay_ms=32.0,
        brightness=0.55,
        slap_lp=2800.0,
        body_lp=1800.0,
        plop_chance=0.95,
        tone_mix=0.42,
        bed_lp=3200.0,
        bed_gain_db=-14.0,
    ),
    # Special: hollow rock / shell-like cavity (rare)
    "shell": dict(
        tone_family="hollow",
        plop=[380.0, 720.0, 980.0],
        splash_bp=[700.0, 1300.0],
        spray_lp=1600.0,
        q_base=6.0,
        decay_ms=40.0,
        brightness=0.28,
        slap_lp=1600.0,
        body_lp=1000.0,
        plop_chance=0.95,
        tone_mix=0.48,
        bed_lp=2000.0,
        bed_gain_db=-13.0,
    ),
}

for _alias, _target in [
    ("roof", "metal"),
    ("tin", "metal"),
    ("puddle", "water"),
    ("plastic", "tarp"),
    ("hollow", "shell"),
    ("rock_hollow", "shell"),
]:
    _SURFACE[_alias] = _SURFACE[_target]


def _get_surface(name):
    if not name:
        return _SURFACE["water"]
    key = str(name).lower().strip()
    if key in _SURFACE:
        return _SURFACE[key]
    for k, v in _SURFACE.items():
        if k in key:
            return v
    return _SURFACE["water"]


# ---------------------------------------------------------------------------
# Layer synthesis — soft / wet (anti-plastic, anti-popcorn)
# ---------------------------------------------------------------------------

def _mk_slap(sr, surface, size_mm, slap_db, seed, antimetal, declick, roundness, attack_ms):
    """Soft contact — dark, rounded, never a needle click."""
    prof = _get_surface(surface)
    # Long enough to not read as a plastic snap
    attack = max(1.8, float(attack_ms) * 0.6)
    decay = 6.0 + 8.0 * (size_mm / 3.5)
    dur_ms = attack + decay + 2.0
    n = max(32, int(sr * dur_ms / 1000.0))

    # Mostly brown + a little pink — avoids tarp HF
    white = _det_noise(n, seed * 1000 + 1)
    noise = 0.72 * _brownish(white) + 0.28 * _pinkish(white)
    noise = _hp1(noise, 120.0, sr)
    slap_lp = float(prof.get("slap_lp", 1800.0))
    bright = float(prof.get("brightness", 0.25))
    noise = _lp1(noise, slap_lp * (0.85 + 0.35 * bright), sr)

    env = _soft_env(n, sr, attack_ms=attack, decay_ms=decay, hold_ms=0.4)
    if antimetal and antimetal > 0:
        t = np.arange(n, dtype=np.float64) / sr
        env *= np.exp(-t * float(antimetal) * 180.0)

    sig = _rms_scale(noise * env, 0.10)
    return _db(slap_db) * sig


def _mk_splat(sr, surface, size_mm, splat_db, seed, antimetal, wetness):
    """Wet body thud — the soft 'mass' of the drop, not a slap."""
    prof = _get_surface(surface)
    attack = 2.5 + 1.5 * float(wetness)
    decay = 12.0 + 18.0 * (size_mm / 3.5) * (0.5 + 0.5 * float(wetness))
    dur_ms = attack + decay
    n = max(32, int(sr * dur_ms / 1000.0))

    white = _det_noise(n, seed * 1000 + 2)
    noise = 0.85 * _brownish(white) + 0.15 * _pinkish(white)
    body_lp = float(prof.get("body_lp", 1200.0))
    sig = _lp1(noise, body_lp, sr)
    sig = _hp1(sig, 80.0, sr)

    env = _soft_env(n, sr, attack_ms=attack, decay_ms=decay, hold_ms=1.0)
    if antimetal and antimetal > 0:
        t = np.arange(n, dtype=np.float64) / sr
        env *= np.exp(-t * float(antimetal) * 60.0)

    sig = _rms_scale(sig * env, 0.11)
    return _db(splat_db) * sig


def _mk_plop(sr, surface, size_mm, plop_db, seed, wetness, antimetal, roundness, attack_ms):
    """Legacy API → off-tone splat (kept for call sites)."""
    return _mk_offtone_splat(
        sr, surface, size_mm, seed,
        wetness=0.9 if wetness is None else wetness,
        sharpness=0.35,
        amp_db=float(plop_db) if plop_db is not None else -6.0,
    )


def _offtone_freqs(surface, size_mm, seed):
    """1–2 slightly detuned / inharmonic centres — wet splat, not a musical note.

    *Off-tone* = intentional detune + mild inharmonicity so stacked drops
    don't fuse into a single bleepy pitch.
    """
    prof = _get_surface(surface)
    plop = prof.get("plop", "minnaert")
    size = max(0.4, float(size_mm))
    # Size → lower base pitch (bigger drop = deeper splat)
    size_scale = 1.0 / max(0.55, (size / 2.8) ** 0.55)

    if plop == "minnaert":
        # Bubble-ish water range, slightly off pure Minnaert
        r = max(0.0004, 0.0018 * (size / 3.0))
        f0 = _minnaert_freq(r) * (0.72 + 0.18 * _det_u01(seed, 20))
        f0 = max(180.0, min(1400.0, f0 * size_scale * 0.45))
    elif isinstance(plop, (list, tuple)) and plop:
        base = float(plop[0])
        f0 = base * size_scale * (0.88 + 0.24 * _det_u01(seed, 20))
        f0 = max(200.0, min(2800.0, f0))
    else:
        f0 = (520.0 + 180.0 * _det_u01(seed, 20)) * size_scale
        f0 = max(220.0, min(1600.0, f0))

    # Primary off-tone detune ±6%
    det = 0.94 + 0.12 * _det_u01(seed, 21)
    f1 = f0 * det

    freqs = [f1]
    # Second partial: inharmonic (not 2×) — classic “wet splat” colour
    if _det_u01(seed, 22) < 0.72:
        ratio = 1.55 + 0.55 * _det_u01(seed, 23)  # ~1.55–2.1, not exact octave
        det2 = 0.96 + 0.08 * _det_u01(seed, 24)
        f2 = f1 * ratio * det2
        if 120.0 < f2 < 3200.0:
            freqs.append(f2)
    return freqs


def _mk_wet_spit(sr, surface, size_mm, seed, wetness=0.9, sharpness=0.35, amp_db=-4.0):
    """Soft wet water hit — brown-led, dark, no high tarp/plastic.

    Avoids mid-pink “tarp slap” and bright HF tips that read as plastic.
      • heavy brown *weight* (water mass)
      • soft low-mid *wet smear* (splash, still dark)
      • tiny sharpness sheen only (very quiet)
    """
    sh = max(0.0, min(1.0, float(sharpness)))
    wet = max(0.2, min(1.0, float(wetness)))
    size = max(0.35, float(size_mm))
    prof = _get_surface(surface)
    # Surfaces that aren't water stay a touch brighter, but never tarp-bright
    bright = float(prof.get("brightness", 0.22))
    mat = max(0.0, min(1.0, bright))

    # Slow soft attack — no needle / plastic tick
    attack = 6.5 + 2.5 * wet - 1.2 * sh          # ~5–9 ms
    decay = 32.0 + 24.0 * wet * (size / 2.5) - 5.0 * sh
    decay = max(24.0, min(80.0, decay))
    n = max(192, int(sr * (attack + decay + 6.0) / 1000.0))

    white = _det_noise(n, seed * 1000 + 9)
    brown = _brownish(white)
    pink = _pinkish(white)

    # --- weight: brown water body (main character) ---
    weight = (0.92 - 0.08 * mat) * brown + (0.08 + 0.08 * mat) * pink
    weight = _hp1(weight, 45.0 + 25.0 * sh, sr)
    # Keep body dark — high LP was the “tarp” zone
    body_lp = 520.0 + 380.0 * sh + 40.0 * size + 120.0 * mat
    weight = _lp1(weight, body_lp, sr)
    w_env = _soft_env(n, sr, attack_ms=attack + 1.5, decay_ms=decay, hold_ms=2.0)
    weight = _rms_scale(weight * w_env, 0.11)

    # --- wet smear: soft low-mid only (not bright pink mid formant) ---
    smear = 0.78 * brown + 0.22 * pink
    smear = _hp1(smear, 90.0 + 40.0 * sh, sr)
    mid_lo = 160.0 + 60.0 * sh
    mid_hi = 700.0 + 550.0 * sh + 200.0 * mat   # was ~1400–3000 → tarp
    smear = _lp1(smear, mid_hi, sr)
    smear = _hp1(smear, mid_lo, sr)
    s_att = attack + 2.0
    s_dec = decay * (0.80 + 0.12 * wet)
    s_env = _soft_env(n, sr, attack_ms=s_att, decay_ms=s_dec, hold_ms=1.5)
    pre = min(n // 4, max(0, int(sr * (0.002 + 0.001 * size))))
    smear_sig = np.zeros(n, dtype=np.float64)
    body = _rms_scale(smear * s_env, 0.075)
    if pre > 0:
        smear_sig[pre:] = body[: n - pre]
    else:
        smear_sig = body

    # --- sharpness sheen: very quiet, dark-capped (not plastic tip) ---
    sheen = np.zeros(n, dtype=np.float64)
    if sh > 0.20:
        sh_n = min(n, max(48, int(sr * (0.012 + 0.016 * sh))))
        tw = _det_noise(sh_n, seed * 1000 + 11)
        tip = 0.75 * _brownish(tw) + 0.25 * _pinkish(tw)
        tip = _hp1(tip, 500.0 + 300.0 * sh, sr)
        tip = _lp1(tip, 1400.0 + 1200.0 * sh, sr)  # hard ceiling vs old 2.8–5.6 kHz
        tip *= _soft_env(sh_n, sr, attack_ms=4.0 + 1.5 * sh, decay_ms=12.0 + 10.0 * sh, hold_ms=0.8)
        tip = _rms_scale(tip, 0.018 + 0.016 * sh)
        off = min(n // 5, max(0, int(0.002 * sr)))
        take = min(sh_n, n - off)
        if take > 0:
            sheen[off : off + take] = tip[:take]

    # Weight leads — water mass, not mid formant
    sheen_g = (0.04 + 0.10 * sh) * min(1.0, 0.5 + 0.5 * size / 2.0)
    out = 0.68 * weight + 0.30 * smear_sig + sheen_g * sheen

    # Keep wet and dark overall
    out = _hp1(out, 40.0 + 15.0 * sh, sr)
    out = _lp1(out, 1100.0 + 900.0 * sh + 300.0 * mat, sr)

    edge = min(int(0.005 * sr), n // 5)
    if edge > 1:
        w = 0.5 - 0.5 * np.cos(np.linspace(0, math.pi, edge))
        out[:edge] *= w
        out[-edge:] *= w[::-1]

    out = _rms_scale(out, 0.10 + 0.015 * sh)
    out = _peak_cap(out, 0.20)
    return _db(amp_db) * (0.62 + 0.32 * (size / 2.5)) * out


def _mk_hollow_splat(sr, surface, size_mm, seed, wetness=0.9, sharpness=0.35, amp_db=-5.0):
    """Tarp / shell cavity hit — mid ring, intentionally hollow. Rare special only."""
    sh = max(0.0, min(1.0, float(sharpness)))
    wet = max(0.2, min(1.0, float(wetness)))
    size = max(0.35, float(size_mm))
    prof = _get_surface(surface)

    attack = 1.8 + 1.0 * wet - 0.5 * sh
    decay = float(prof.get("decay_ms", 32.0)) * (0.7 + 0.4 * wet) * (size / 2.8) ** 0.4
    decay = max(18.0, decay - 4.0 * sh)
    n = max(96, int(sr * (attack + max(12.0, decay)) / 1000.0))

    freqs = _offtone_freqs(surface, size_mm, seed)
    q_base = float(prof.get("q_base", 5.5))
    q = max(3.0, min(7.0, q_base * (0.7 + 0.25 * wet)))
    tone_mix = float(prof.get("tone_mix", 0.42)) * (0.9 + 0.2 * sh)

    sig = np.zeros(n, dtype=np.float64)
    for i, f0 in enumerate(freqs):
        part = _resonator_noise(
            n, sr, f0, q * (1.0 - 0.10 * i),
            seed=seed * 17 + 3 + i * 41,
            harm=0.18,
            tone_mix=tone_mix,
        )
        g = (1.0 if i == 0 else 0.55) * (0.75 + 0.35 * _det_u01(seed, 30 + i))
        sig += g * part

    white = _det_noise(n, seed * 1000 + 5)
    cushion = 0.45 * _brownish(white) + 0.55 * _pinkish(white)
    cushion = _lp1(_hp1(cushion, 120.0, sr), 1400.0 + 1800.0 * sh, sr)
    cushion = _rms_scale(cushion, 0.06)
    sig = 0.78 * _rms_scale(sig, 0.12) + 0.22 * cushion

    env = _soft_env(n, sr, attack_ms=attack, decay_ms=decay, hold_ms=0.6 + 1.2 * wet)
    sig = _rms_scale(sig * env, 0.10 + 0.02 * sh)

    edge = min(int(0.003 * sr), n // 5)
    if edge > 1:
        w = 0.5 - 0.5 * np.cos(np.linspace(0, math.pi, edge))
        sig[:edge] *= w
        sig[-edge:] *= w[::-1]

    bright = float(prof.get("brightness", 0.25))
    mat_g = 0.85 + 0.25 * (1.0 - bright)
    return _db(amp_db) * mat_g * (0.55 + 0.35 * (size / 2.8)) * sig


def _mk_offtone_splat(sr, surface, size_mm, seed, wetness=0.9, sharpness=0.35, amp_db=-5.0):
    """Dispatch: wet spit (default) vs hollow tarp/shell (rare surfaces)."""
    prof = _get_surface(surface)
    family = str(prof.get("tone_family", "wet")).lower()
    if family in ("hollow", "tarp", "shell"):
        return _mk_hollow_splat(sr, surface, size_mm, seed, wetness, sharpness, amp_db)
    return _mk_wet_spit(sr, surface, size_mm, seed, wetness, sharpness, amp_db)


def _mk_splash(sr, surface, size_mm, splash_db, seed, antimetal, wetness):
    """Soft mid spray — wide, dark, no crisp formant whistle."""
    prof = _get_surface(surface)
    predelay_ms = 2.0 + 1.5 * (size_mm / 3.5)
    attack = 3.0
    decay = 14.0 + 16.0 * (size_mm / 3.5) * (0.55 + 0.45 * float(wetness))
    n_pre = int(sr * predelay_ms / 1000.0)
    n_body = max(32, int(sr * (attack + decay) / 1000.0))

    white = _det_noise(n_body, seed * 1000 + 4)
    noise = 0.55 * _pinkish(white) + 0.45 * _brownish(white)
    # Very wide gentle band — not whistling formants
    bp_freqs = prof.get("splash_bp", [1000.0, 1800.0])
    sig = np.zeros(n_body, dtype=np.float64)
    for fc in bp_freqs:
        bw = max(1200.0, fc * 1.2)
        sig += _bp_butter(noise, fc, bw, sr, order=1) / max(1, len(bp_freqs))
    sig = _lp1(sig, 2400.0, sr)

    env = _soft_env(n_body, sr, attack_ms=attack, decay_ms=decay, hold_ms=1.0)
    if antimetal and antimetal > 0:
        t = np.arange(n_body, dtype=np.float64) / sr
        env *= np.exp(-t * float(antimetal) * 80.0)

    sig = _rms_scale(sig * env, 0.09)
    out = np.zeros(n_pre + n_body, dtype=np.float64)
    out[n_pre:] = _db(splash_db) * sig
    return out


def _mk_spray(sr, surface, size_mm, spray_db, spray_tail_ms, seed, antimetal):
    """Soft settling hiss — long, dark, quiet."""
    prof = _get_surface(surface)
    attack = 6.0
    decay = max(50.0, float(spray_tail_ms))
    n = max(32, int(sr * (attack + decay) / 1000.0))

    white = _det_noise(n, seed * 1000 + 5)
    noise = 0.7 * _brownish(white) + 0.3 * _pinkish(white)
    lp_fc = float(prof.get("spray_lp", 1400.0))
    sig = _lp1(noise, lp_fc, sr)
    sig = _hp1(sig, 100.0, sr)

    env = _soft_env(n, sr, attack_ms=attack, decay_ms=decay * 0.7, hold_ms=4.0)
    if antimetal and antimetal > 0:
        t = np.arange(n, dtype=np.float64) / sr
        env *= np.exp(-t * float(antimetal) * 20.0)

    sig = _rms_scale(sig * env, 0.07)
    return _db(spray_db) * sig


# ---------------------------------------------------------------------------
# Engine state / main drop synthesis
# ---------------------------------------------------------------------------

_ENGINE_STATE = {
    "room": None,
    "sr": 48000,
    "master_gain_db": 0.0,
    "wetness": 0.92,
    "hp_cut": 55.0,
    "antimetal": 0.55,
    "diffuse_g": 0.0,        # scatter grains off — they muddy dense rain
    "declick": 1.0,
    "roundness": 1.8,
    "attack_ms": 4.5,
    "predelay_ms": 0.0,
    # Quiet contact, wet body lead, soft spray — no pitched “note”
    "plop_db": -28.0,        # almost never lead
    "slap_db": -12.0,
    "splat_db": -3.5,
    "splash_db": -9.0,
    "spray_db": -8.0,
    "spray_tail_ms": 160.0,
    "bed_enabled": False,
    "bed_gain_db": None,
}


def synth_drop(
    sr=48000,
    surface="water",
    size_mm=3.5,
    seed=0,
    wetness=None,
    hp_cut=None,
    antimetal=None,
    declick=None,
    roundness=None,
    attack_ms=None,
    predelay_ms=None,
    plop_db=None,
    slap_db=None,
    splat_db=None,
    splash_db=None,
    spray_db=None,
    spray_tail_ms=None,
    diffuse_g=None,
    total_ms=None,
    force_full=False,
    sharpness=None,
):
    """One rain impact.

    Default **water**: soft wet spit (low bubble plop + dark body).
    **tarp/shell**: hollow cavity (rare specials only).
    Continuous field wash stays separate and quiet under these hits.
    """
    S = _ENGINE_STATE
    hp_cut = S["hp_cut"] if hp_cut is None else hp_cut
    sh = 0.35 if sharpness is None else max(0.0, min(1.0, float(sharpness)))
    size = max(0.35, float(size_mm))
    prof = _get_surface(surface)
    hollow = str(prof.get("tone_family", "wet")).lower() in ("hollow", "tarp", "shell")

    if hollow:
        mono = _mk_hollow_splat(
            sr, surface, size, seed,
            wetness=0.75 + 0.2 * (1.0 - sh),
            sharpness=sh,
            amp_db=-4.0 + 1.0 * min(1.0, size / 3.0),
        )
    else:
        mono = _mk_wet_spit(
            sr, surface, size, seed,
            wetness=0.85 + 0.12 * (1.0 - sh),
            sharpness=sh,
            amp_db=-2.5 + 1.0 * min(1.0, size / 3.0),
        )

    # Tiny drizzle: quieter, not shorter-to-pop
    if size < 0.9 and not hollow:
        mono = mono * (0.50 + 0.40 * size)

    if total_ms is not None:
        n_want = max(8, int(sr * float(total_ms) / 1000.0))
        if len(mono) < n_want:
            mono = np.pad(mono, (0, n_want - len(mono)))
        else:
            mono = mono[:n_want]

    # Wet stays dark (high final LP was re-opening tarp HF). Hollow can be brighter.
    final_lp = (1000.0 + 900.0 * sh) if not hollow else (1800.0 + 2800.0 * sh)
    mono = _lp1(mono, final_lp, sr)
    if not hollow:
        mono = _hp1(mono, 40.0 + 12.0 * sh, sr)
    if hp_cut and hp_cut > 0:
        mono = _hp1(mono, float(hp_cut), sr)

    _ = (
        wetness, antimetal, declick, roundness, attack_ms, predelay_ms,
        plop_db, slap_db, splat_db, splash_db, spray_db, spray_tail_ms,
        diffuse_g, force_full,
    )
    return _peak_cap(mono, 0.34)



# ---------------------------------------------------------------------------
# WAV I/O
# ---------------------------------------------------------------------------

def write_wav(path, sr, L, R, pad_ms=0):
    pad_n = int(sr * pad_ms / 1000.0)
    if pad_n > 0:
        L = np.concatenate([L, np.zeros(pad_n)])
        R = np.concatenate([R, np.zeros(pad_n)])
    L = np.clip(L, -1.0, 1.0)
    R = np.clip(R, -1.0, 1.0)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        stereo = np.stack([L, R], axis=-1)
        ints = (stereo * 32767.0).astype(np.int16).reshape(-1)
        w.writeframes(ints.tobytes())


# ---------------------------------------------------------------------------
# Room / Listener fallbacks
# ---------------------------------------------------------------------------

try:
    from app.models.room import Room, Listener
except ImportError:  # pragma: no cover
    from dataclasses import dataclass, field

    @dataclass
    class Listener:
        x: float = 0.0
        y: float = 1.6
        z: float = 0.0

    @dataclass
    class Room:
        width: float = 5.0
        height: float = 3.0
        depth: float = 6.0
        windows: list = field(default_factory=list)
        speakers: list = field(default_factory=list)
        headphones_items: list = field(default_factory=list)
        listener: Listener = field(default_factory=Listener)
        headphones_mode: bool = False
        rain_intensity: float = 0.5
        wind: float = 0.0
        thunder: float = 0.1
        droplet_density: float = 0.5


class _FallbackListener:
    def __init__(self, x=0.0, y=1.6, z=0.0):
        self.x, self.y, self.z = x, y, z


def configure_room(room, sr=48000, master_gain_db=0.0, wetness=0.75, hp_cut=60.0,
                   antimetal=0.15, diffuse_g=0.12, declick=0.6, roundness=1.1,
                   attack_ms=2.0, predelay_ms=0.0, **kw):
    _ENGINE_STATE.update({
        "room": room,
        "sr": sr,
        "master_gain_db": master_gain_db,
        "wetness": wetness,
        "hp_cut": hp_cut,
        "antimetal": antimetal,
        "diffuse_g": diffuse_g,
        "declick": declick,
        "roundness": roundness,
        "attack_ms": attack_ms,
        "predelay_ms": predelay_ms,
    })
    _ENGINE_STATE.update(kw)


def render_single_drop(
    out_path="out/single_drop.wav",
    sr=48000,
    surface="water",
    size_mm=3.5,
    normalize=True,
    duration=None,
    gain_db=None,
    pad_ms=120,
    loud=False,
    seed=0,
    **kw,
):
    """Render a single droplet to a stereo WAV file."""
    S = _ENGINE_STATE
    master_db = S["master_gain_db"] if gain_db is None else gain_db
    total_ms = duration * 1000.0 if duration is not None else None
    mono = synth_drop(sr=sr, surface=surface, size_mm=size_mm, seed=seed,
                      total_ms=total_ms, force_full=True, **kw)

    if master_db != 0.0:
        mono *= _db(master_db)

    if loud:
        mono = _softclip(mono, 1.1)
        mono = _norm_peak(mono, 0.988)
    elif normalize:
        mono = _norm_peak(mono, 0.944)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    write_wav(out_path, sr, mono.copy(), mono.copy(), pad_ms=pad_ms)
    return out_path


# ---------------------------------------------------------------------------
# Continuous rain bed — stateful (no per-block reseeding / filter clicks)
# ---------------------------------------------------------------------------

class ContinuousRainBed:
    """Stateful pink/brown rain wash. Call render() each audio block in order.

    Previous version reseeded noise + restarted IIR filters every block →
    crackle/distortion. This keeps filter memory and RNG continuous.
    """

    def __init__(self, sr: int = 48000, seed: int = 42):
        self.sr = int(sr)
        self.rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
        self.t = 0.0
        # one-pole states
        self._b_acc = 0.0      # brown
        self._p_acc = 0.0      # pink-ish
        self._lp1 = 0.0
        self._lp2 = 0.0
        self._hp_y = 0.0
        self._hp_x = 0.0
        self._mod = 0.0
        self._rms_ema = 0.05  # smoothed RMS for seamless gain

    def reset(self, seed: int = 42):
        self.__init__(self.sr, seed)

    def render(self, n: int, intensity: float = 0.5, level: float = 0.10) -> np.ndarray:
        """Return mono float64 of length n, continuous with previous call."""
        n = int(n)
        if n <= 0 or intensity <= 0.01 or level <= 0:
            if n > 0:
                self.t += n / max(1, self.sr)
            return np.zeros(max(0, n), dtype=np.float64)

        sr = self.sr
        white = self.rng.randn(n).astype(np.float64)

        # Mean-reverting brown-ish + pink blend (never random-walk to infinity)
        if _HAS_SCIPY:
            # lfilter with zi for continuity
            b_a, p_a = 0.985, 0.97
            b_b = np.array([1.0 - b_a])
            b_aa = np.array([1.0, -b_a])
            p_b = np.array([1.0 - p_a])
            p_aa = np.array([1.0, -p_a])
            brown, zf_b = sp_signal.lfilter(b_b, b_aa, white, zi=[self._b_acc])
            pink_l, zf_p = sp_signal.lfilter(p_b, p_aa, white, zi=[self._p_acc])
            self._b_acc = float(zf_b[0])
            self._p_acc = float(zf_p[0])
            pink = 0.55 * pink_l + 0.45 * white
        else:
            b_a, p_a = 0.985, 0.97
            brown = np.empty(n, dtype=np.float64)
            pink = np.empty(n, dtype=np.float64)
            b_acc, p_acc = self._b_acc, self._p_acc
            for i, w in enumerate(white):
                b_acc = b_a * b_acc + (1.0 - b_a) * w
                p_acc = p_a * p_acc + (1.0 - p_a) * w
                brown[i] = b_acc
                pink[i] = 0.55 * p_acc + 0.45 * w
            self._b_acc, self._p_acc = b_acc, p_acc

        src = 0.62 * brown + 0.38 * pink

        # Cascade LP via lfilter + zi
        def _lp_state(x, fc, state):
            dt = 1.0 / sr
            rc = 1.0 / (PI2 * fc)
            alpha = dt / (rc + dt)
            if _HAS_SCIPY:
                y, zf = sp_signal.lfilter([alpha], [1.0, -(1.0 - alpha)], x, zi=[state])
                return y, float(zf[0])
            y = np.empty_like(x)
            s = state
            for i, v in enumerate(x):
                s = s + alpha * (v - s)
                y[i] = s
            return y, s

        y1, self._lp1 = _lp_state(src, 1500.0, self._lp1)
        y2, self._lp2 = _lp_state(y1, 2100.0, self._lp2)

        # HP ~90 Hz with state
        dt = 1.0 / sr
        rc = 1.0 / (PI2 * 90.0)
        ah = rc / (rc + dt)
        if _HAS_SCIPY:
            # y[n] = ah*(y[n-1] + x[n] - x[n-1])
            # implement with initial conditions manually for first sample
            y = np.empty(n, dtype=np.float64)
            hy, hx = self._hp_y, self._hp_x
            for i, x in enumerate(y2):
                hy = ah * (hy + x - hx)
                hx = x
                y[i] = hy
            self._hp_y, self._hp_x = hy, hx
        else:
            y = np.empty(n, dtype=np.float64)
            hy, hx = self._hp_y, self._hp_x
            for i, x in enumerate(y2):
                hy = ah * (hy + x - hx)
                hx = x
                y[i] = hy
            self._hp_y, self._hp_x = hy, hx

        t0 = self.t
        t = t0 + np.arange(n, dtype=np.float64) / sr
        am = 0.90 + 0.10 * np.sin(PI2 * 0.23 * t + 0.4)
        am *= 0.94 + 0.06 * np.sin(PI2 * 0.71 * t + 1.3)
        y = y * am

        # Smoothed RMS gain — per-block renorm caused audible pumping/seams
        target_rms = float(level) * (0.35 + 0.65 * float(intensity))
        target_rms = max(0.01, min(0.12, target_rms))
        rms = float(np.sqrt(np.mean(y * y)) + 1e-12)
        self._rms_ema = 0.98 * self._rms_ema + 0.02 * rms
        scale = target_rms / max(1e-6, self._rms_ema)
        scale = min(scale, 6.0)
        out = y * scale
        self.t = t0 + n / sr
        return _peak_cap(out, 0.40)


# Module-level beds used by simple helpers / offline tools
_BEDS = {}


def rain_bed_mono(n, sr, surface, intensity, seed_base, phase, gain_db=None):
    """Compatibility wrapper — uses a continuous bed keyed by seed_base."""
    key = (int(sr), int(seed_base))
    bed = _BEDS.get(key)
    if bed is None or bed.sr != int(sr):
        bed = ContinuousRainBed(sr=sr, seed=int(seed_base) & 0x7FFFFFFF)
        _BEDS[key] = bed
    # gain_db≈-12 → level≈0.10 (comfortable wash, no clipping)
    g = -12.0 if gain_db is None else float(gain_db)
    level = 0.10 * (10.0 ** ((g + 12.0) / 20.0))
    level = max(0.02, min(0.16, level))
    return bed.render(int(n), intensity=float(intensity), level=level)


def _rain_bed_block(n, sr, surface, intensity, seed_base, phase):
    """Stereo bed wrapper for RainEngine."""
    bed = rain_bed_mono(n, sr, surface, intensity, seed_base, phase, gain_db=-12.0)
    # Decorrelated R via short allpass-ish delay from continuous bed history:
    # simple sample delay is fine within block
    shift = max(1, int(0.001 * sr))
    L = bed
    R = np.concatenate([bed[shift:], bed[:shift]]) * 0.97
    return np.stack([L, R], axis=1)


# ---------------------------------------------------------------------------
# RainEngine — multi-voice streaming + offline render
# ---------------------------------------------------------------------------

class _Voice:
    __slots__ = ("L", "R", "pos")

    def __init__(self, mono, pan):
        # Equal-power pan
        g_l = math.cos((pan + 1.0) * 0.25 * math.pi)
        g_r = math.sin((pan + 1.0) * 0.25 * math.pi)
        self.L = mono * g_l
        self.R = mono * g_r
        self.pos = 0

    @property
    def remaining(self):
        return len(self.L) - self.pos


class RainEngine:
    """Streaming rain engine with concurrent droplet voices."""

    def __init__(self, room, samplerate=48000, blocksize=1024, max_voices=80):
        self.room = room
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize)
        self.max_voices = int(max_voices)
        self._stream = None
        self._running = False
        self._roof_mat = None
        self._win_mat = None
        self._voices: deque[_Voice] = deque()
        self._time = 0.0
        self._next_event = 0.0
        self._evt_id = 0
        self._rng = np.random.RandomState(7)
        self._master = 0.95

    def set_materials(self, roof_name=None, window_name=None):
        self._roof_mat = roof_name
        self._win_mat = window_name

    def _material_to_surface(self, mat_name):
        if mat_name is None:
            return "water"
        low = str(mat_name).lower()
        for key in _SURFACE:
            if key in low:
                return key
        if "glass" in low or "window" in low:
            return "glass"
        if "metal" in low or "tin" in low or "roof" in low:
            return "metal"
        if "wood" in low or "shingle" in low:
            return "wood" if "wood" in low else "shingle"
        if "tile" in low:
            return "tile"
        if "brick" in low:
            return "brick"
        return "water"

    def _impacts_per_sec(self):
        """Intensity = drop frequency (not noise level)."""
        intensity = float(getattr(self.room, "rain_intensity", 0.5))
        density = float(getattr(self.room, "droplet_density", 0.5))
        if intensity <= 0.0005:
            return 0.0
        return float(intensity * 160.0 * (0.55 + 0.45 * density))

    def _spawn_drop(self):
        sr = self.samplerate
        surface = self._material_to_surface(self._roof_mat)
        u = float(self._rng.rand())
        if u < 0.75:
            size_mm = float(self._rng.uniform(0.8, 2.2))
        elif u < 0.94:
            size_mm = float(self._rng.uniform(2.2, 3.5))
        else:
            size_mm = float(self._rng.uniform(3.5, 5.0))

        # Drop loudness independent of intensity
        amp = float(self._rng.uniform(0.55, 1.0)) * self._master
        wind = float(getattr(self.room, "wind", 0.0))
        pan = float(np.clip(self._rng.normal(0.35 * wind, 0.45), -0.95, 0.95))

        mono = synth_drop(
            sr=sr,
            surface=surface,
            size_mm=size_mm,
            seed=self._evt_id,
        )
        mono = mono * amp
        self._evt_id += 1

        if len(self._voices) >= self.max_voices:
            self._voices.popleft()
        self._voices.append(_Voice(mono, pan))

    def _mix_voices(self, frames):
        out = np.zeros((frames, 2), dtype=np.float64)
        alive = deque()
        for v in self._voices:
            n = min(frames, v.remaining)
            if n > 0:
                out[:n, 0] += v.L[v.pos : v.pos + n]
                out[:n, 1] += v.R[v.pos : v.pos + n]
                v.pos += n
            if v.remaining > 0:
                alive.append(v)
        self._voices = alive
        return out

    def _schedule_into(self, out, frames):
        """Advance time, spawn impacts that fall inside this block, mix."""
        sr = self.samplerate
        ips = self._impacts_per_sec()
        t0 = self._time
        t1 = t0 + frames / sr

        spawned = 0
        max_spawn = max(8, int(ips * frames / sr) + 16) if ips > 0 else 0
        while ips > 0 and self._next_event < t1 and spawned < max_spawn:
            if self._next_event >= t0:
                delay = int((self._next_event - t0) * sr)
                self._spawn_drop()
                if delay > 0 and self._voices:
                    v = self._voices[-1]
                    pad = np.zeros(delay, dtype=np.float64)
                    v.L = np.concatenate([pad, v.L])
                    v.R = np.concatenate([pad, v.R])
                spawned += 1
            dt = float(self._rng.exponential(1.0 / max(1e-6, ips)))
            self._next_event += max(dt, 1.0 / sr)
        if ips <= 0:
            self._next_event = t1 + 1.0

        self._time = t1
        mixed = self._mix_voices(frames)
        out += mixed

        # Noise bed OFF by default — intensity is drop rate only
        if _ENGINE_STATE.get("bed_enabled", False):
            intensity = float(getattr(self.room, "rain_intensity", 0.5))
            surface = self._material_to_surface(self._roof_mat)
            bed = _rain_bed_block(frames, sr, surface, intensity, seed_base=12345, phase=t0)
            out += bed * 0.15

        pk = np.max(np.abs(out)) + 1e-12
        if pk > 0.90:
            out *= 0.90 / pk
        return out

    def _emit_block(self, frames):
        """Public block renderer used by MultiDeviceEngine."""
        out = np.zeros((int(frames), 2), dtype=np.float64)
        self._schedule_into(out, int(frames))
        return out.astype(np.float32)

    def render_offline(self, seconds=8.0, mode="headphones"):
        """Render a rain sequence offline. Returns stereo float64 (N, 2)."""
        sr = self.samplerate
        n_total = int(sr * float(seconds))
        out = np.zeros((n_total, 2), dtype=np.float64)

        # Reset scheduler state for deterministic-ish offline renders
        self._voices = deque()
        self._time = 0.0
        self._next_event = 0.0
        self._evt_id = 0
        self._rng = np.random.RandomState(42)

        block = max(256, self.blocksize)
        pos = 0
        while pos < n_total:
            n = min(block, n_total - pos)
            chunk = np.zeros((n, 2), dtype=np.float64)
            self._schedule_into(chunk, n)
            out[pos : pos + n] += chunk
            pos += n

        pk = np.max(np.abs(out)) + 1e-12
        if pk > 0.95:
            out *= 0.95 / pk
        return out

    def start(self):
        """Start streaming audio via sounddevice."""
        if self._running:
            return
        try:
            import sounddevice as sd
        except ImportError as e:
            raise RuntimeError("sounddevice not installed — cannot stream audio") from e

        sr = self.samplerate
        block = self.blocksize
        self._voices = deque()
        self._time = 0.0
        self._next_event = 0.0
        self._evt_id = 0
        self._rng = np.random.RandomState(7)
        self._running = True

        def _callback(outdata, frames, time_info, status):
            try:
                buf = self._emit_block(frames)
                outdata[:] = buf
            except Exception:
                outdata[:] = 0

        self._stream = sd.OutputStream(
            samplerate=sr,
            channels=2,
            blocksize=block,
            dtype="float32",
            callback=_callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._running = False
        self._voices = deque()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli():
    ap = argparse.ArgumentParser(description="Droplet Audio Engine v3.0.0")
    ap.add_argument("--single-drop", action="store_true")
    ap.add_argument("--sr", type=int, default=48000)
    ap.add_argument("--surface", type=str, default="water")
    ap.add_argument("--size-mm", type=float, default=3.5)
    ap.add_argument("--master-gain-db", type=float, default=None)
    ap.add_argument("--normalize", action="store_true")
    ap.add_argument("--duration", type=float, default=None, help="total duration in seconds")
    ap.add_argument("--gain-db", type=float, default=None)
    ap.add_argument("--pad-ms", type=int, default=120)
    ap.add_argument("--loud", action="store_true")
    ap.add_argument("--out", type=str, default="out/single_drop.wav")
    ap.add_argument("--plop-db", type=float, default=None)
    ap.add_argument("--slap-db", type=float, default=None)
    ap.add_argument("--splat-db", type=float, default=None)
    ap.add_argument("--splash-db", type=float, default=None)
    ap.add_argument("--spray-db", type=float, default=None)
    ap.add_argument("--spray-tail-ms", type=float, default=None)
    ap.add_argument("--wetness", type=float, default=None)
    ap.add_argument("--hp-cut", type=float, default=None)
    ap.add_argument("--antimetal", type=float, default=None)
    ap.add_argument("--diffuse-g", type=float, default=None)
    ap.add_argument("--declick", type=float, default=None)
    ap.add_argument("--roundness", type=float, default=None)
    ap.add_argument("--attack-ms", type=float, default=None)
    ap.add_argument("--predelay-ms", type=float, default=None)
    ap.add_argument("--no-bed", action="store_true", help="disable continuous rain bed")
    ap.add_argument("--render", type=int, default=0, help="render ~N impacts worth of rain")
    ap.add_argument("--seconds", type=float, default=None, help="offline rain length (with --render)")
    ap.add_argument("--intensity", type=float, default=0.6, help="rain intensity 0..1 for --render")
    args = ap.parse_args()

    cli_map = {
        "wetness": args.wetness,
        "hp_cut": args.hp_cut,
        "antimetal": args.antimetal,
        "diffuse_g": args.diffuse_g,
        "declick": args.declick,
        "roundness": args.roundness,
        "attack_ms": args.attack_ms,
        "predelay_ms": args.predelay_ms,
        "plop_db": args.plop_db,
        "slap_db": args.slap_db,
        "splat_db": args.splat_db,
        "splash_db": args.splash_db,
        "spray_db": args.spray_db,
        "spray_tail_ms": args.spray_tail_ms,
    }
    if args.master_gain_db is not None:
        _ENGINE_STATE["master_gain_db"] = args.master_gain_db
    if args.no_bed:
        _ENGINE_STATE["bed_enabled"] = False
    for k, v in cli_map.items():
        if v is not None:
            _ENGINE_STATE[k] = v

    if args.single_drop:
        kw = {k: v for k, v in cli_map.items() if v is not None}
        out = render_single_drop(
            out_path=args.out,
            sr=args.sr,
            surface=args.surface,
            size_mm=args.size_mm,
            normalize=True if args.normalize or not args.loud else False,
            duration=args.duration,
            gain_db=args.gain_db or args.master_gain_db,
            pad_ms=args.pad_ms,
            loud=args.loud,
            **kw,
        )
        print(
            f"[Engine v3.0] wrote {out} @ {args.sr} Hz "
            f"(surface={args.surface}, size={args.size_mm}mm, pad={args.pad_ms}ms)"
        )

    elif args.render > 0 or args.seconds:
        room = Room(5.0, 3.0, 6.0)
        room.rain_intensity = float(args.intensity)
        room.droplet_density = 0.7
        engine = RainEngine(room, samplerate=args.sr)
        engine.set_materials(roof_name=args.surface)
        # If --render N without seconds, estimate length from intensity
        if args.seconds is not None:
            seconds = float(args.seconds)
        else:
            ips = max(10.0, args.intensity * 120.0)
            seconds = max(2.0, float(args.render) / ips + 1.5)
        stereo = engine.render_offline(seconds=seconds)
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        write_wav(args.out, args.sr, stereo[:, 0], stereo[:, 1])
        print(
            f"[Engine v3.0] wrote {args.out} "
            f"({stereo.shape[0] / args.sr:.1f}s, intensity={args.intensity}, surface={args.surface})"
        )
    else:
        ap.print_help()


if __name__ == "__main__":
    try:
        _cli()
    except SystemExit:
        pass
