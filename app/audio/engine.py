
"""
Umbra Patch v0.3.1 — Tri‑stage droplet (crash • splash • bounce), LOUD hotfix

- Output normalization to ~-0.5 dBFS (or ~-0.1 dBFS with --loud)
- Default duration 0.18 s for better audibility
- Optional silence tail (--pad-ms) to avoid media players clipping the tail
"""

import argparse, numpy as np, wave, math

def softclip(x, drive=1.0):
    return np.tanh(x*drive)

def norm_to_dbfs(x, target_peak=0.944):  # ~ -0.5 dBFS
    peak = np.max(np.abs(x)) + 1e-12
    return x * (target_peak/peak)

def env_halfhann(n):
    return np.hanning(n*2)[n:]

def mk_tick(sr, dur_ms=1.5, amp=0.35, f0=2000.0):
    n = int(sr*dur_ms/1000.0); 
    if n<=0: return np.zeros(1)
    t = np.arange(n)/sr
    sig = (np.sin(2*np.pi*f0*t) + 0.6*np.sin(2*np.pi*2.2*f0*t + 0.7) + 0.35*np.sin(2*np.pi*3.7*f0*t + 1.4))
    sig *= env_halfhann(n)
    return amp*sig

def mk_splash(sr, dur_ms=18.0, amp=0.9):
    n = int(sr*dur_ms/1000.0); 
    if n<=0: return np.zeros(1)
    t = np.arange(n)/sr
    freqs = [480.0, 620.0, 820.0, 980.0, 1180.0]  # lowered brightness
    phases = [0.0, 0.6, 1.2, 1.8, 2.5]
    out = np.zeros(n, dtype=np.float64)
    base_env = np.exp(-np.linspace(0, 1.0, n)/0.018)
    for f, ph in zip(freqs, phases):
        out += np.sin(2*np.pi*f*t + ph) * base_env
    out *= np.linspace(1.0, 0.93, n)
    return amp * out * env_halfhann(n)

def mk_bounce(sr, dur_ms=24.0, amp=0.55, f_start=420.0, f_end=760.0, delay_ms=28.0):
    n = int(sr*dur_ms/1000.0); d = int(sr*delay_ms/1000.0)
    if n<=0: return np.zeros(d+1)
    t = np.arange(n)/sr
    f = f_start * (f_end/f_start) ** (t/(dur_ms/1000.0))
    phase = 2*np.pi*np.cumsum(f)/sr
    env = np.exp(-np.linspace(0, 1.0, n)/0.030)
    y = np.sin(phase) * env * env_halfhann(n)
    out = np.zeros(d+n, dtype=np.float64)
    out[d:d+n] = amp * y
    return out

def synth_drop(sr=48000, total_ms=180.0):
    crash = mk_tick(sr, 1.5, 0.32, 2000.0)
    splash = mk_splash(sr, 18.0, 0.92)
    bounce = mk_bounce(sr, 24.0, 0.58, 420.0, 760.0, 28.0)
    n = int(sr*total_ms/1000.0)
    out = np.zeros(n, dtype=np.float64)
    out[:len(crash)] += crash
    out[:len(splash)] += splash
    out[:min(len(bounce), n)] += bounce[:min(len(bounce), n)]
    # normalize to -0.5 dBFS
    out = norm_to_dbfs(out, target_peak=0.944)
    return out

def write_wav(path, sr, L, R, pad_ms=0):
    import wave
    pad_n = int(sr*pad_ms/1000.0)
    if pad_n>0:
        L = np.concatenate([L, np.zeros(pad_n)])
        R = np.concatenate([R, np.zeros(pad_n)])
    with wave.open(path,'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        ints = (np.stack([L,R],axis=-1)*32767.0).astype(np.int16).reshape(-1)
        w.writeframes(ints.tobytes())

def render_single_drop(out_path, sr=48000, duration=0.18, gain_db=0.0, pad_ms=120, loud=False):
    mono = synth_drop(sr=sr, total_ms=duration*1000.0)
    if loud:
        # Soft limiting and normalize hotter (~-0.1 dBFS)
        mono = softclip(mono, 1.1)
        peak = np.max(np.abs(mono)) + 1e-12
        mono *= (0.988 / peak)
    L = mono.copy(); R = mono.copy()
    write_wav(out_path, sr, L, R, pad_ms=pad_ms)

def _cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--single-drop", action="store_true")
    ap.add_argument("--sr", type=int, default=48000)
    ap.add_argument("--duration", type=float, default=0.18)  # seconds
    ap.add_argument("--gain-db", type=float, default=0.0)    # kept for compatibility (unused in v0.3.1)
    ap.add_argument("--pad-ms", type=int, default=120)
    ap.add_argument("--loud", action="store_true")
    ap.add_argument("--out", type=str, default="single_drop.wav")
    args = ap.parse_args()
    if args.single_drop:
        render_single_drop(args.out, sr=args.sr, duration=args.duration, gain_db=args.gain_db, pad_ms=args.pad_ms, loud=args.loud)
        flags = "LOUD" if args.loud else "norm"
        print(f"[Umbra] wrote {args.out} @ {args.sr} Hz ({flags}, pad={args.pad_ms}ms)")

if __name__=="__main__":
    try:_cli()
    except SystemExit:pass
