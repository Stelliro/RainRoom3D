
import numpy as np

def _pink_noise(n, rng):
    # Voss-McCartney style approximate pink noise
    # Simple: filter white noise with 1pole low-shelf
    white = rng.standard_normal(n).astype(np.float32)
    y = np.empty_like(white)
    a = 0.985
    acc = 0.0
    for i in range(n):
        acc = a*acc + (1-a)*white[i]
        y[i] = 0.6*acc + 0.4*white[i]*0.5
    return y

def _exp_env(n, sr, t60):
    # exponential decay to -60dB at t60
    if t60 <= 1e-4:
        return np.zeros(n, np.float32)
    lam = np.power(10.0, -3.0) ** (1.0 / (t60*sr))
    # y[k] = lam^k
    k = np.arange(n, dtype=np.float32)
    return np.power(lam, k)

def synth_drop(sr, brightness, rng, D_mm):
    """Noise-based droplet impact.
    - brightness: 0..1 (spectral tilt)
    - D_mm: drop diameter in mm (~0.3..6). Larger = beefier, lower center freq.
    """
    dur = min(0.25, 0.05 + 0.02*D_mm)          # 50..250 ms
    n = int(dur*sr)
    # source noise (mix of white and pink)
    pink = _pink_noise(n, rng)
    white = rng.standard_normal(n).astype(np.float32) * 0.3
    src = 0.65*pink + 0.35*white
    # brightness tilt via simple one-pole lowpass
    # cutoff 1.5-5 kHz scaled by brightness and drop size
    fc = (1500.0 + 3500.0*brightness) * (1.0 - 0.08*D_mm)
    fc = max(400.0, min(8000.0, fc))
    # one-pole low-pass
    x = src
    y = np.empty_like(x)
    dt = 1.0/sr
    RC = 1.0/(2*np.pi*fc)
    a = dt/(RC+dt)
    acc = 0.0
    for i in range(n):
        acc = acc + a*(x[i]-acc)
        y[i] = acc
    # short pre-delay jitter to decorrelate
    pre = rng.integers(0, int(0.006*sr))
    if pre>0:
        y = np.concatenate([np.zeros(pre, np.float32), y])[:n]
    # exponential decay
    t60 = 0.06 + 0.06*(1.0 - brightness) + 0.01*D_mm
    env = _exp_env(n, sr, t60)
    sig = y * env
    # tiny 3-tap smoothing to remove ping
    sig = (sig + np.roll(sig,1) + np.roll(sig,-1)) / 3.0
    # normalize conservative
    m = float(np.max(np.abs(sig))) or 1.0
    sig = 0.6*sig/m
    return sig.astype(np.float32)

def sample_drop_diameters(n, rng, rate=0.5):
    # Marshall-Palmer-ish distribution; clamp to [0.3..6] mm
    # approximate: exponential with mean ~ 1.5 + rain_rate scaling
    lam = 1.0/(1.2 + 1.5*rate)
    x = rng.exponential(1.0/lam, size=n).astype(np.float32)
    return np.clip(0.3 + x, 0.3, 6.0)
