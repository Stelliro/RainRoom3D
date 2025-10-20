
"""
RainEngine v0.5.0 — Slap‑only rain + terrain interactions (deterministic, no noise)

Features
- Procedural "slap" onset synthesis (broadband burst via summed sines), no white noise.
- Terrain profiles (grass, dirt, puddle, mixed) that bias brightness/decay of the slap.
- Event generator that schedules impacts at the requested intensity and emits impacts.json
  with positions, radii, and timestamps for visual ripple systems.
- WAV renderer for quick listen tests.

CLI
    py -3 -m app.audio.rain_engine --render-rain --seconds 8 --intensity 18 --terrain mixed --out rain_demo.wav --events impacts.json

Note: Slaps are *onsets only* per your request (no oip body). The timbre is shaped by terrain.
"""

import argparse, json, math, numpy as np, wave, random
from dataclasses import dataclass

SR = 48000

def env_halfhann(n: int) -> np.ndarray:
    import numpy as _np
    return _np.hanning(n*2)[n:] if n>1 else _np.ones(1)

@dataclass
class SlapParams:
    dur_ms: float
    freqs: list
    phases: list
    amp: float
    brightness: float  # post tilt
    decay_ms: float

TERRAIN = {
    "grass":   dict(bright=0.7, decay_ms=2.0,  freq_bias=1.0),
    "dirt":    dict(bright=0.8, decay_ms=1.8,  freq_bias=1.05),
    "puddle":  dict(bright=1.0, decay_ms=1.5,  freq_bias=1.15),
    "fabric":  dict(bright=0.5, decay_ms=2.2,  freq_bias=0.95),  # kept for future use
    "mixed":   dict(bright=None, decay_ms=None, freq_bias=None),  # choose per‑hit
}

def mk_slap(dur_ms: float, amp: float, brightness: float, freq_bias: float, seed: int = 0) -> np.ndarray:
    n = max(4, int(SR*dur_ms/1000.0))
    t = np.arange(n)/SR
    rng = random.Random(seed)
    # build a small bright stack, slightly randomized phases
    base_freqs = [1800.0, 2800.0, 4000.0, 5200.0]
    freqs = [f*freq_bias for f in base_freqs]
    phases = [rng.uniform(0, 2*math.pi) for _ in freqs]
    x = np.zeros(n, dtype=np.float64)
    for f,ph in zip(freqs, phases):
        x += np.sin(2*math.pi*f*t + ph)
    # fast window (half‑Hann) + micro exponential to ensure clean stop
    x *= env_halfhann(n)
    x *= np.exp(-np.linspace(0,1.0,n)/(dur_ms/1000.0*0.8 + 1e-6))
    # brightness tilt (simple high‑shelf like: differentiate then mix)
    dx = np.concatenate([[x[0]], np.diff(x)])
    x = (1.0 - 0.35*(1.0-brightness)) * x + (0.35*(brightness)) * dx
    # normalize and scale
    peak = np.max(np.abs(x)) + 1e-12
    return amp * (x / peak)

def schedule_impacts(seconds: float, intensity: float, seed: int = 0):
    """
    Poisson-like scheduler: intensity = expected impacts per second.
    Returns list of (t_seconds).
    """
    rng = random.Random(seed)
    t = 0.0
    times = []
    while t < seconds:
        # exponential inter-arrival
        dt = rng.expovariate(intensity) if intensity > 0 else seconds
        t += dt
        if t <= seconds:
            times.append(t)
    return times

def terrain_params(name: str, rng: random.Random):
    if name != "mixed":
        P = TERRAIN[name]
        return P["bright"], P["decay_ms"], P["freq_bias"]
    # pick one per hit, weighted toward grass/puddle
    choice = rng.choices(["grass","puddle","dirt"], weights=[0.45,0.35,0.20])[0]
    P = TERRAIN[choice]
    # add slight per-hit jitter
    bright = P["bright"] * rng.uniform(0.9, 1.1)
    decay_ms = P["decay_ms"] * rng.uniform(0.9, 1.1)
    freq_bias = P["freq_bias"] * rng.uniform(0.95, 1.05)
    return bright, decay_ms, freq_bias, choice

def render_rain(seconds: float, intensity: float, terrain: str, seed: int = 0, out_path: str = "rain_demo.wav", events_path: str = "impacts.json"):
    rng = random.Random(seed)
    n = int(SR*seconds)
    y = np.zeros(n, dtype=np.float64)
    events = []
    hits = schedule_impacts(seconds, intensity, seed=seed)
    for idx, t in enumerate(hits):
        bright, decay_ms, freq_bias, chosen = terrain_params(terrain, rng) if terrain=="mixed" else (*terrain_params(terrain, rng), terrain)
        # slap params
        dur_ms = decay_ms
        amp = 0.85
        sig = mk_slap(dur_ms, amp, bright, freq_bias, seed=seed+idx*17)
        i0 = int(t*SR)
        i1 = min(n, i0 + len(sig))
        if i0 < n:
            y[i0:i1] += sig[:i1-i0]
            # make an impact event for visuals
            # choose a random XY in unit square; let renderer map to world
            x = rng.uniform(0.0, 1.0)
            z = rng.uniform(0.0, 1.0)
            radius = 0.04 * (1.1 if chosen=="puddle" else 1.0)
            events.append(dict(time=t, x=x, z=z, radius=radius, terrain=chosen))
    # mild limiter
    peak = np.max(np.abs(y)) + 1e-12
    if peak > 0.99:
        y *= 0.99/peak
    # write wav (stereo identical for now)
    write_wav(out_path, SR, y, y)
    # write events json
    with open(events_path, "w", encoding="utf-8") as f:
        json.dump(dict(sample_rate=SR, duration=seconds, events=events), f, indent=2)
    return hits, events

def write_wav(path: str, sr: int, left: np.ndarray, right: np.ndarray):
    import wave
    stereo = np.stack([left, right], axis=-1)
    stereo = np.clip(stereo, -1.0, 1.0)
    with wave.open(path, 'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        ints = (stereo * 32767.0).astype(np.int16).reshape(-1)
        w.writeframes(ints.tobytes())

def _cli():
    ap = argparse.ArgumentParser(description="RainEngine v0.5.0 — slap-only rain renderer")
    ap.add_argument("--render-rain", action="store_true")
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--intensity", type=float, default=18.0, help="impacts per second")
    ap.add_argument("--terrain", type=str, default="mixed", choices=["grass","dirt","puddle","fabric","mixed"])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=str, default="rain_demo.wav")
    ap.add_argument("--events", type=str, default="impacts.json")
    args = ap.parse_args()
    if args.render_rain:
        hits, ev = render_rain(args.seconds, args.intensity, args.terrain, seed=args.seed, out_path=args.out, events_path=args.events)
        print(f"[RainEngine] wrote {args.out} and {args.events} ({len(hits)} impacts)")

if __name__ == "__main__" or (__name__ == "app.audio.rain_engine"):
    try: _cli()
    except SystemExit: pass
