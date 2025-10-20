import numpy as np

def distance_attenuation(d, ref=1.0, rolloff=1.0):
    d = max(1e-6, float(d))
    return min(1.0, ref/(ref + rolloff*(d-ref)))

def binaural(stereo_src, azimuth_rad, elevation_rad=0.0, distance=1.5, sr=48000):
    """
    Super light ITD/ILD model:
    - ILD: angle-based channel gain
    - ITD: small interaural delay
    Input: mono or stereo (Nx1 or Nx2). Returns Nx2 stereo.
    """
    x = stereo_src
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)

    max_itd = int(0.0007 * sr)
    s = np.sin(azimuth_rad)
    itd = int((s*0.5 + 0.5) * max_itd)  # 0..max

    left_gain = 0.8 + 0.2*np.cos(azimuth_rad)
    right_gain = 0.8 + 0.2*np.cos(azimuth_rad + np.pi)

    att = distance_attenuation(distance, ref=1.0, rolloff=0.9)

    n = x.shape[0]
    y = np.zeros_like(x)
    if s > 0:
        y[itd:,0] = x[:n-itd,0]
        y[:,1] = x[:,1]
    else:
        y[:,0] = x[:,0]
        y[itd:,1] = x[:n-itd,1]

    y[:,0] *= left_gain * att
    y[:,1] *= right_gain * att
    return y

def vbap_pan(mono_src, angle):
    """Vector-base amplitude panning for stereo L/R given angle (-pi..pi)."""
    gL = max(0.0, np.cos(angle))
    gR = max(0.0, np.sin(angle))
    norm = max(1e-6, np.sqrt(gL*gL + gR*gR))
    gL, gR = gL/norm, gR/norm
    if mono_src.ndim == 1:
        m = mono_src
        return np.stack([m*gL, m*gR], axis=1)
    return mono_src
