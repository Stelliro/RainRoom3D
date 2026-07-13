#!/usr/bin/env python3
"""
Generate seamless procedural rain WAV loops for OutdoorFieldBed.

These are *not* multi-drop stacks — they're continuous wet wash textures
with sparse soft accents baked in, so layering in the live engine stays clean.

Usage:
  python scripts/gen_rain_samples.py
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import numpy as np
from scipy import signal as sp

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "audio" / "rain"
SR = 48000
PI2 = 2.0 * math.pi


def _one_pole_lp(x: np.ndarray, fc: float, sr: float) -> np.ndarray:
    dt = 1.0 / sr
    rc = 1.0 / (PI2 * max(20.0, fc))
    a = dt / (rc + dt)
    b = np.array([a], dtype=np.float64)
    aa = np.array([1.0, -(1.0 - a)], dtype=np.float64)
    return sp.lfilter(b, aa, x)


def _one_pole_hp(x: np.ndarray, fc: float, sr: float) -> np.ndarray:
    dt = 1.0 / sr
    rc = 1.0 / (PI2 * max(20.0, fc))
    a = rc / (rc + dt)
    b = np.array([a, -a], dtype=np.float64)
    aa = np.array([1.0, -a], dtype=np.float64)
    return sp.lfilter(b, aa, x)


def _brown(white: np.ndarray, a: float = 0.992) -> np.ndarray:
    return sp.lfilter([1.0 - a], [1.0, -a], white)


def _pink(white: np.ndarray) -> np.ndarray:
    # Paul Kellet approx via cascaded poles (good enough for rain wash)
    b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
    a = [1.0, -2.494956002, 2.017265875, -0.522189400]
    y = sp.lfilter(b, a, white)
    return y * 0.35


def _soft_impact(n: int, sr: int, rng: np.random.RandomState, brightness: float) -> np.ndarray:
    """Single soft wet thud — brown body, long attack, no HF click."""
    attack = int(sr * (0.004 + 0.003 * rng.rand()))
    decay = int(sr * (0.035 + 0.08 * rng.rand()))
    length = min(n, attack + decay + int(0.01 * sr))
    if length < 32:
        return np.zeros(n, dtype=np.float64)
    white = rng.randn(length)
    body = 0.88 * _brown(white, 0.994 - 0.008 * brightness) + 0.12 * _pink(white)
    body = _one_pole_hp(body, 60.0 + 40.0 * brightness, sr)
    body = _one_pole_lp(body, 900.0 + 1400.0 * brightness, sr)
    env = np.zeros(length, dtype=np.float64)
    a = max(1, attack)
    t = np.linspace(0.0, 1.0, a)
    env[:a] = 0.5 - 0.5 * np.cos(math.pi * t)
    td = np.arange(length - a, dtype=np.float64) / sr
    tau = max(0.02, (length - a) / sr * 0.45)
    env[a:] = np.exp(-td / tau)
    sig = body * env
    rms = float(np.sqrt(np.mean(sig * sig)) + 1e-12)
    sig *= (0.08 + 0.06 * brightness) / rms
    out = np.zeros(n, dtype=np.float64)
    pos = int(rng.randint(0, max(1, n - length)))
    out[pos : pos + length] = sig
    return out


def _make_loop(seconds: float, density: float, brightness: float, seed: int) -> np.ndarray:
    """
    Continuous multi-depth rain wash.

    density 0..1  — how full the wash is
    brightness 0..1 — soft dark vs slightly more open top
    """
    n = int(SR * seconds)
    rng = np.random.RandomState(seed)

    # Three decorrelated noise streams for depth
    w1 = rng.randn(n)
    w2 = rng.randn(n)
    w3 = rng.randn(n)

    a_body = 0.995 - 0.012 * brightness
    brown1 = _brown(w1, a_body)
    brown2 = _brown(w2, a_body - 0.002)
    pink = _pink(w3)

    # Spectral layers of real rain (all dark-ish; no white hiss wall)
    body_fc = 420.0 + 900.0 * brightness
    mid_fc = 1100.0 + 1600.0 * brightness
    high_fc = 2200.0 + 2400.0 * brightness

    body = _one_pole_lp(0.75 * brown1 + 0.25 * brown2, body_fc, SR)
    mid = _one_pole_lp(0.45 * brown1 + 0.35 * pink + 0.20 * brown2, mid_fc, SR)
    mid = _one_pole_hp(mid, 180.0, SR)
    high = _one_pole_lp(0.55 * pink + 0.45 * brown2, high_fc, SR)
    high = _one_pole_hp(high, 400.0 + 200.0 * brightness, SR)
    high = _one_pole_lp(high, high_fc * 0.85, SR)

    # Gentle weather AM (slow) so it doesn't sound static
    t = np.arange(n, dtype=np.float64) / SR
    am = (
        0.90
        + 0.06 * np.sin(PI2 * 0.11 * t + 0.3)
        + 0.04 * np.sin(PI2 * 0.29 * t + 1.1)
        + 0.025 * np.sin(PI2 * 0.07 * t + 2.4)
    )

    d = max(0.05, min(1.0, density))
    mix = (
        (0.55 + 0.10 * d) * body
        + (0.28 + 0.12 * d) * mid
        + (0.06 + 0.16 * d * brightness) * high
    ) * am

    # Sparse soft accents only (baked into loop — not live multi-layer synth)
    # ~0.8–4 hits/sec depending on density — texture, not popcorn
    rate = 0.6 + 3.5 * d
    n_hits = max(1, int(seconds * rate))
    for i in range(n_hits):
        hit = _soft_impact(n, SR, rng, brightness * (0.6 + 0.4 * rng.rand()))
        g = 0.35 + 0.45 * rng.rand()
        # quieter when denser so they don't poke out of the wash
        g *= 0.85 / math.sqrt(0.4 + 0.6 * d)
        mix += hit * g

    # Seamlessly loopable: crossfade ends
    xf = int(0.08 * SR)
    if xf > 8 and n > xf * 3:
        fade = np.linspace(0.0, 1.0, xf)
        head = mix[:xf].copy()
        tail = mix[-xf:].copy()
        mix[:xf] = head * fade + tail * (1.0 - fade)
        mix[-xf:] = mix[:xf]  # exact match for seamless loop

    # Level: soft continuous wash, leave headroom for live drops
    target_rms = 0.10 + 0.04 * d
    rms = float(np.sqrt(np.mean(mix * mix)) + 1e-12)
    mix *= target_rms / rms
    pk = float(np.max(np.abs(mix)) + 1e-12)
    if pk > 0.55:
        mix *= 0.55 / pk

    return mix.astype(np.float64)


def _write_wav(path: Path, mono: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    x = np.clip(mono, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        ints = (x * 32767.0).astype(np.int16)
        w.writeframes(ints.tobytes())


def main() -> None:
    specs = [
        # name, seconds, density, brightness, seed
        ("rain_soft_loop.wav", 18.0, 0.28, 0.22, 101),
        ("rain_med_loop.wav", 20.0, 0.55, 0.38, 202),
        ("rain_heavy_loop.wav", 22.0, 0.85, 0.48, 303),
        ("rain_roof_dark.wav", 16.0, 0.50, 0.18, 404),
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, sec, dens, bright, seed in specs:
        print(f"Generating {name} ({sec}s, dens={dens}, bright={bright})...")
        audio = _make_loop(sec, dens, bright, seed)
        path = OUT_DIR / name
        _write_wav(path, audio)
        print(f"  wrote {path}  rms={np.sqrt(np.mean(audio*audio)):.4f}  peak={np.max(np.abs(audio)):.3f}")
    print("Done.")


if __name__ == "__main__":
    main()
