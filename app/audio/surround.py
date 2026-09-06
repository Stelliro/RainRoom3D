"""
Map room-speaker mics + wall-portal buses onto a surround device.

Physical layout (room compass): +Z = North (front), +X = East (right).

Channel counts follow Windows / WASAPI / WAVEFORMATEXTENSIBLE:

  2: L R
  4: FL FR BL BR
  6: FL FR FC LFE BL BR          (5.1)
  8: FL FR FC LFE BL BR SL SR    (7.1)

If the OS device is stereo, callers keep the existing L/R pan and this
module is not used. A 5.1/7.1 device that fails to open at that channel
count falls back to stereo in the engine — worse imaging, still plays.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np

# Mix-bus indices for outdoor wall / roof energy (negative so they never
# collide with speaker indices 0..N-1).
WALL_BUS: Dict[str, int] = {
    "north": -1,
    "east": -2,
    "south": -3,
    "west": -4,
    "roof": -5,
}
WALL_FROM_CH: Dict[int, str] = {v: k for k, v in WALL_BUS.items()}

def _layout(n_ch: int) -> List[Optional[float]]:
    """Azimuth degrees per channel, or None for LFE / unused."""
    n = int(n_ch)
    if n <= 1:
        return [None]
    if n == 2:
        return [-30.0, 30.0]
    if n == 4:
        return [-30.0, 30.0, -110.0, 110.0]
    if n == 6:
        # FL FR FC LFE BL BR
        return [-30.0, 30.0, 0.0, None, -110.0, 110.0]
    # 7.1 and anything 8+
    return [-30.0, 30.0, 0.0, None, -110.0, 110.0, -90.0, 90.0] + [None] * max(0, n - 8)


def clamp_device_channels(n: int) -> int:
    """Supported output widths (WASAPI-friendly)."""
    n = max(1, int(n))
    if n >= 8:
        return 8
    if n >= 6:
        return 6
    if n >= 4:
        return 4
    if n >= 2:
        return 2
    return 1


def channel_candidates(preferred: int) -> List[int]:
    """Try requested surround width first, then step down to stereo/mono."""
    p = clamp_device_channels(preferred)
    ordered = []
    for c in (p, 8, 6, 4, 2, 1):
        if c not in ordered and c <= max(p, 2):
            ordered.append(c)
    if 2 not in ordered:
        ordered.append(2)
    if 1 not in ordered:
        ordered.append(1)
    return ordered


def _wrap_deg(d: float) -> float:
    return (float(d) + 180.0) % 360.0 - 180.0


def azimuth_deg_from_center(x: float, z: float, cx: float, cz: float) -> float:
    """0° = north (+Z), +90° = east (+X)."""
    return math.degrees(math.atan2(float(x) - float(cx), float(z) - float(cz)))


def vbap_gains(src_az_deg: float, n_ch: int, power: float = 1.6) -> np.ndarray:
    """Positive cosine weights from a source azimuth onto the device seats."""
    n = max(1, int(n_ch))
    layout = _layout(n)
    g = np.zeros(n, dtype=np.float64)
    if n == 1:
        g[0] = 1.0
        return g
    for i, az in enumerate(layout):
        if az is None:
            continue
        delta = math.radians(_wrap_deg(src_az_deg - az))
        w = math.cos(delta)
        if w > 0.0:
            g[i] = w ** float(power)
    s = float(np.sum(g * g))
    if s < 1e-12:
        # Behind all seats: put energy on the nearest
        best_i, best_d = 0, 1e9
        for i, az in enumerate(layout):
            if az is None:
                continue
            d = abs(_wrap_deg(src_az_deg - az))
            if d < best_d:
                best_d, best_i = d, i
        g[best_i] = 1.0
        s = 1.0
    g *= math.sqrt(1.0 / s)
    return g


def equal_power_ild_pan(pan: float) -> tuple:
    """Stereo gains for pan −1 (left) … +1 (right), with extra ILD so a test is obvious."""
    pan = max(-1.0, min(1.0, float(pan)))
    gL = math.cos((pan + 1.0) * 0.25 * math.pi)
    gR = math.sin((pan + 1.0) * 0.25 * math.pi)
    ild = 9.0 * pan
    gL *= 10.0 ** ((-ild) / 20.0)
    gR *= 10.0 ** ((+ild) / 20.0)
    return float(gL), float(gR)


def peer_pan_lr(speaker, peers: Sequence, room) -> float:
    """How far left/right this speaker sits among others on the same device.

    −1 = leftmost of the group (heavy left), +1 = rightmost. +X is East / right.
    """
    peers = list(peers) or [speaker]
    xs = [float(s.x) for s in peers]
    x = float(speaker.x)
    xmin, xmax = min(xs), max(xs)
    span = xmax - xmin
    rw = max(0.5, float(getattr(room, "width", 5.0)))
    L = getattr(room, "listener", None)
    lx = float(getattr(L, "x", rw * 0.5)) if L is not None else rw * 0.5
    lz = float(getattr(L, "z", float(getattr(room, "depth", 4.0)) * 0.5)) if L is not None else 0.0
    yaw = float(getattr(L, "yaw", 0.0)) if L is not None else 0.0
    dx = x - lx
    dz = float(speaker.z) - lz
    pan_lis = max(-1.0, min(1.0, math.sin(math.atan2(dx, dz) - yaw)))

    if span >= 0.25 and len(peers) >= 2:
        t = (x - xmin) / span
        pan_peers = t * 2.0 - 1.0
        spread = min(1.0, span / max(0.6, 0.45 * rw))
        pan = pan_peers * (0.55 + 0.45 * spread)
        pan = 0.85 * pan + 0.15 * pan_lis
    else:
        pan_room = max(-1.0, min(1.0, (x / rw) * 2.0 - 1.0))
        pan = 0.65 * pan_lis + 0.35 * pan_room
    return max(-1.0, min(1.0, float(pan)))


def speakers_on_same_device(room, speaker) -> list:
    di = getattr(speaker, "audio_device", None)
    out = []
    for s in getattr(room, "speakers", []) or []:
        if not getattr(s, "enabled", True):
            continue
        if getattr(s, "audio_device", None) != di:
            continue
        out.append(s)
    if speaker not in out:
        out.append(speaker)
    return out


def speaker_test_channel_gains(speaker, room, n_ch: int) -> np.ndarray:
    """Per-channel gains for a test tone at this speaker's layout position."""
    n_ch = clamp_device_channels(n_ch)
    peers = speakers_on_same_device(room, speaker)
    if n_ch <= 1:
        return np.array([1.0], dtype=np.float64)
    if n_ch >= 4:
        cx = 0.5 * float(getattr(room, "width", 5.0))
        cz = 0.5 * float(getattr(room, "depth", 4.0))
        az = azimuth_deg_from_center(float(speaker.x), float(speaker.z), cx, cz)
        g = vbap_gains(az, n_ch, power=1.25)
        s = float(np.sqrt(np.sum(g * g))) or 1.0
        return (g / s) * 1.12
    pan = peer_pan_lr(speaker, peers, room)
    gL, gR = equal_power_ild_pan(pan)
    pk = max(abs(gL), abs(gR), 1e-9)
    return np.array([gL / pk, gR / pk], dtype=np.float64)


def speaker_test_pan_label(speaker, room) -> str:
    """Short phrase for the status bar: 'heavy left', 'centre', …"""
    peers = speakers_on_same_device(room, speaker)
    pan = peer_pan_lr(speaker, peers, room)
    shared = len(peers) >= 2
    if pan <= -0.55:
        where = "heavy left"
    elif pan <= -0.18:
        where = "left"
    elif pan >= 0.55:
        where = "heavy right"
    elif pan >= 0.18:
        where = "right"
    else:
        where = "centre"
    if shared:
        return f"{where} of {len(peers)} on this device"
    return where


def wall_channel_gains(n_ch: int) -> Dict[str, np.ndarray]:
    """Static wrap: each outdoor wall feeds the surround seats that face it."""
    n = max(1, int(n_ch))
    return {
        "north": vbap_gains(0.0, n),
        "east": vbap_gains(90.0, n),
        "south": vbap_gains(180.0, n),
        "west": vbap_gains(-90.0, n),
        # Roof: centre + a little everywhere (indoor structure)
        "roof": _roof_gains(n),
    }


def _roof_gains(n_ch: int) -> np.ndarray:
    n = max(1, int(n_ch))
    g = np.zeros(n, dtype=np.float64)
    layout = _layout(n)
    for i, az in enumerate(layout):
        if az is None:
            continue
        # Centre-ish + mild sides; not the rears
        if abs(az) <= 35.0:
            g[i] = 1.0
        elif abs(az) <= 100.0:
            g[i] = 0.35
        else:
            g[i] = 0.18
    s = float(np.sum(g * g))
    if s > 1e-12:
        g *= math.sqrt(1.0 / s)
    else:
        g[0] = 1.0
    return g


def lfe_index(n_ch: int) -> Optional[int]:
    n = int(n_ch)
    if n >= 6:
        return 3  # FL FR FC LFE ...
    return None


def mix_to_surround(
    *,
    frames: int,
    n_ch: int,
    speaker_bufs: Dict[int, np.ndarray],
    speakers: Sequence,
    wall_bufs: Dict[str, np.ndarray],
    room_width: float,
    room_depth: float,
    speaker_weight: float = 0.72,
    wall_weight: float = 0.55,
) -> np.ndarray:
    """Build an (N, C) surround block from speaker mics + wall-portal buses.

    Speaker mics are VBAP'd from their 3D position around the room centre.
    Wall buses fill seats that no speaker covers so rain still wraps.
    """
    n_ch = clamp_device_channels(n_ch)
    frames = int(frames)
    out = np.zeros((frames, n_ch), dtype=np.float64)
    if frames <= 0:
        return out

    cx = 0.5 * float(room_width)
    cz = 0.5 * float(room_depth)

    live = []
    for i, spk in enumerate(speakers):
        if not getattr(spk, "enabled", True):
            continue
        buf = speaker_bufs.get(i)
        if buf is None:
            continue
        mono = np.asarray(buf, dtype=np.float64).reshape(-1)
        if mono.size == 0:
            continue
        if len(mono) < frames:
            pad = np.zeros(frames, dtype=np.float64)
            pad[: len(mono)] = mono
            mono = pad
        else:
            mono = mono[:frames]
        live.append((spk, mono))
    n_spk = max(1, len(live))
    scale = float(speaker_weight) / math.sqrt(n_spk)
    for spk, mono in live:
        az = azimuth_deg_from_center(float(spk.x), float(spk.z), cx, cz)
        gains = vbap_gains(az, n_ch)
        span = 0.0
        if hasattr(spk, "acoustic_width"):
            try:
                span = float(spk.acoustic_width())
            except Exception:
                span = 0.0
        smear = max(0.0, min(0.25, span * 0.12))
        if smear > 0.0:
            gains = (1.0 - smear) * gains + smear * (gains > 0).astype(np.float64)
            s = float(np.sum(gains * gains))
            if s > 1e-12:
                gains *= math.sqrt(1.0 / s)
        out += mono[:, None] * (gains * scale)[None, :]

    wall_g = wall_channel_gains(n_ch)
    # If speakers already cover the ring, keep wall bus quieter so we don't
    # double the near-window hits. Sparse layouts lean on walls more.
    wall_k = float(wall_weight)
    if n_spk >= 3:
        wall_k *= 0.55
    elif n_spk == 0:
        wall_k *= 1.35

    for name, buf in wall_bufs.items():
        g = wall_g.get(str(name).lower())
        if g is None:
            continue
        mono = np.asarray(buf, dtype=np.float64).reshape(-1)
        if mono.size == 0:
            continue
        if len(mono) < frames:
            pad = np.zeros(frames, dtype=np.float64)
            pad[: len(mono)] = mono
            mono = pad
        else:
            mono = mono[:frames]
        out += mono[:, None] * (g * wall_k)[None, :]

    # LFE: dark sum of the bed (not the ticks) so the sub doesn't click
    li = lfe_index(n_ch)
    if li is not None:
        bed = np.mean(out, axis=1)
        # 2-sample leaky lowpass ~ 120 Hz @ 48k (coeff independent enough)
        lfe = np.empty_like(bed)
        acc = 0.0
        a = 0.018
        for i, v in enumerate(bed):
            acc += a * (float(v) - acc)
            lfe[i] = acc
        out[:, li] = lfe * 0.35

    return out
