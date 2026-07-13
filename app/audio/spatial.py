
"""
3D room acoustics for RainRoom — window-portal model.

Sound model
-----------
1. Rain hits exist **outdoors** (behind the glass).
2. Each **window is a portal**: outdoor energy couples into the room
   with gain set by how open the window is.
3. Indoors, each open window acts as a **point source on the wall**.
4. Speakers / ears hear the sum of those portals with **distance**,
   **delay**, and **direction** (true 3D relative to each receiver).

Closed window  → quiet, dark leakage  
Open window    → loud, clear path from that wall into the room  
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

C_SOUND = 343.0
Vec3 = Tuple[float, float, float]


def distance_attenuation(d, ref=1.0, rolloff=1.0):
    d = max(1e-6, float(d))
    ref = max(1e-6, float(ref))
    if d <= ref:
        return 1.0
    return ref / (ref + rolloff * (d - ref))


def air_absorption_cutoff(distance_m: float, base_hz: float = 12000.0) -> float:
    d = max(0.0, float(distance_m))
    fc = base_hz / (1.0 + 0.22 * d + 0.012 * d * d)
    return float(max(500.0, min(14000.0, fc)))


def one_pole_lp(x: np.ndarray, fc: float, sr: float) -> np.ndarray:
    if fc <= 0 or sr <= 0 or len(x) == 0:
        return x
    x = np.asarray(x, dtype=np.float64)
    rc = 1.0 / (2.0 * math.pi * fc)
    dt = 1.0 / sr
    a = dt / (rc + dt)
    try:
        from scipy import signal as sp_signal
        return sp_signal.lfilter([a], [1.0, -(1.0 - a)], x)
    except Exception:
        y = np.empty_like(x)
        acc = 0.0
        for i, v in enumerate(x):
            acc = acc + a * (float(v) - acc)
            y[i] = acc
        return y


def delay_samples(sig: np.ndarray, delay_n: int) -> np.ndarray:
    delay_n = max(0, int(delay_n))
    if delay_n == 0:
        return sig
    return np.concatenate([np.zeros(delay_n, dtype=np.float64), np.asarray(sig, dtype=np.float64)])


def binaural(
    mono_src: np.ndarray,
    azimuth_rad: float,
    elevation_rad: float = 0.0,
    distance: float = 1.5,
    sr: int = 48000,
) -> np.ndarray:
    """Mono → stereo via parametric HRTF (ITD/ILD, pinna notch, rear shadow).

    az 0=front, negative=left, positive=right (listener-relative).
    """
    from app.audio.hrtf import apply_hrtf
    return apply_hrtf(
        mono_src,
        azimuth_rad=azimuth_rad,
        elevation_rad=elevation_rad,
        distance=distance,
        sr=sr,
    )


def vbap_pan(mono_src, angle):
    gL = max(0.0, np.cos(angle))
    gR = max(0.0, np.sin(angle))
    norm = max(1e-6, math.sqrt(gL * gL + gR * gR))
    gL, gR = gL / norm, gR / norm
    m = np.asarray(mono_src, dtype=np.float64)
    if m.ndim == 1:
        return np.stack([m * gL, m * gR], axis=1)
    return m


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _wall_outward_normal(wall: str) -> Vec3:
    w = (wall or "north").lower()
    if w == "north":
        return (0.0, 0.0, 1.0)
    if w == "south":
        return (0.0, 0.0, -1.0)
    if w == "east":
        return (1.0, 0.0, 0.0)
    return (-1.0, 0.0, 0.0)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _len(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _norm(a: Vec3) -> Vec3:
    L = _len(a) + 1e-12
    return (a[0] / L, a[1] / L, a[2] / L)


def exterior_point(room, win, out_dist: float = 0.4) -> Vec3:
    """Point just outside the glass (behind the window from inside)."""
    if hasattr(room, "window_exterior_point"):
        return room.window_exterior_point(win, out_dist=out_dist)
    cx, cy, cz = room.window_center(win)
    n = _wall_outward_normal(getattr(win, "wall", "north"))
    return (cx + n[0] * out_dist, cy + n[1] * out_dist, cz + n[2] * out_dist)


def aperture_point(room, win) -> Vec3:
    """Indoor-facing glass centre — where sound enters the room."""
    return room.window_center(win)


@dataclass
class WindowPortalHit:
    """Outdoor rain energy after coupling through one window."""
    window_name: str
    wall: str
    # Virtual source just outside the glass (behind the window)
    exterior: Vec3
    # Radiating point into the room (on the glass)
    aperture: Vec3
    # Mono audio already filtered/gained for this portal (at aperture)
    signal: np.ndarray
    open_amt: float
    outdoor_m: float
    delay_s: float  # outdoor leg only


@dataclass
class IndoorArrival:
    """One portal heard at an indoor receiver."""
    portal: WindowPortalHit
    delay_s: float
    gain: float
    lp_fc: float
    indoor_m: float
    # Direction of arrival for binaural = from receiver toward aperture
    aim: Vec3


def window_open_gain(open_amt: float, style: str = "casement") -> Tuple[float, float]:
    """Legacy simple API — prefer window_acoustic_profile()."""
    prof = window_acoustic_profile(open_amt, style, width=1.0, height=1.2, angle_deg=45.0 * open_amt)
    return prof["gain"], prof["lp_fc"]


def window_acoustic_profile(
    open_amt: float,
    style: str = "casement",
    width: float = 1.0,
    height: float = 1.2,
    angle_deg: float = 45.0,
    hinge_side: str = "left",
) -> dict:
    """Style + geometry dependent portal acoustics.

    Returns dict:
      gain      — linear open transmission (0..~1.2)
      lp_fc     — glass/gap low-pass
      area_k    — relative aperture area factor
      bright    — extra HF blend 0..1
      scoop_low — bias toward low outdoor sources (awning)
      scoop_high— bias toward high outdoor sources (hopper)
      crack_k   — edge-crack emphasis (casement)
      baffle    — outdoor directivity sharpness
    """
    o = max(0.0, min(1.0, float(open_amt)))
    style = (style or "casement").lower().replace("-", "_")
    leak = 0.025
    area = max(0.05, float(width) * float(height))
    area_k = math.sqrt(area / 1.2)  # soft area law

    # Defaults
    g_curve = o ** 1.25
    lp = 700.0 + 7800.0 * (o ** 1.05)
    bright = 0.35 * o
    scoop_low = 0.0
    scoop_high = 0.0
    crack_k = 0.0
    baffle = 1.0

    ang = max(0.0, float(angle_deg))
    sin_a = math.sin(math.radians(min(90.0, ang)))

    if style == "slider":
        # Area grows linearly with open; fairly bright vertical slot
        g_curve = o ** 1.05
        lp = 900.0 + 8200.0 * o
        bright = 0.55 * o
        crack_k = 0.35 * o
        baffle = 0.9

    elif style == "casement":
        # Side hinge: small angle = thin bright crack; large = open mouth
        g_curve = (0.35 * o + 0.65 * sin_a) ** 1.1
        lp = 800.0 + 5000.0 * o + 3000.0 * sin_a
        bright = 0.45 * o + 0.25 * sin_a
        crack_k = 0.7 * (1.0 - 0.6 * o)  # crack dominates when slightly open
        baffle = 1.0 + 0.8 * sin_a  # angled sash directs

    elif style == "awning":
        # Top hinge, bottom lifts out — scoops ground-level outdoor rain
        g_curve = (0.25 * o + 0.75 * sin_a) ** 1.15
        lp = 650.0 + 4500.0 * o + 2000.0 * sin_a
        bright = 0.25 * o
        scoop_low = 0.55 + 0.4 * o
        scoop_high = -0.25
        baffle = 1.1 + 0.6 * sin_a

    elif style == "hopper":
        # Bottom hinge, top tips in — favours high / canopy rain
        g_curve = (0.25 * o + 0.75 * sin_a) ** 1.15
        lp = 750.0 + 5500.0 * o + 2500.0 * sin_a
        bright = 0.4 * o
        scoop_low = -0.2
        scoop_high = 0.55 + 0.4 * o
        baffle = 1.05 + 0.5 * sin_a

    elif style == "sash":
        # Vertical slide: horizontal strip — darker, body-forward
        g_curve = o ** 1.1
        lp = 600.0 + 6500.0 * o
        bright = 0.2 * o
        scoop_low = 0.35 * (1.0 - o)  # when barely open, low strip
        scoop_high = 0.15 * o

    elif style == "pivot":
        g_curve = (0.4 * o + 0.6 * sin_a) ** 1.05
        lp = 850.0 + 7000.0 * o
        bright = 0.4 * o
        baffle = 1.0 + 0.4 * sin_a

    elif style == "tilt_turn":
        # Small open = hopper tilt; large = casement turn
        if o < 0.45:
            t = o / 0.45
            g_curve = (0.2 * t + 0.5 * math.sin(math.radians(35 * t))) ** 1.1
            lp = 700.0 + 4000.0 * t
            scoop_high = 0.5 * t
            bright = 0.25 * t
        else:
            t = (o - 0.45) / 0.55
            g_curve = (0.45 + 0.55 * t) ** 1.05
            lp = 2500.0 + 5500.0 * t
            bright = 0.3 + 0.4 * t
            crack_k = 0.4 * (1.0 - t)
            baffle = 1.0 + 0.7 * t

    elif style == "custom":
        # Approximate from custom hinge/motion if provided on call site via style only
        # (full window uses resolved_style_for_draw before this usually)
        g_curve = (0.4 * o + 0.6 * sin_a) ** 1.1
        lp = 800.0 + 7000.0 * o
        bright = 0.35 * o
        crack_k = 0.35 * (1.0 - 0.5 * o)
        baffle = 1.0 + 0.5 * sin_a

    else:
        g_curve = o ** 1.25

    gain = leak + (1.0 - leak) * g_curve * area_k
    # Slight area boost capped
    gain = min(1.35, gain)

    return {
        "gain": float(gain),
        "lp_fc": float(max(400.0, min(12000.0, lp))),
        "area_k": float(area_k),
        "bright": float(max(0.0, min(1.0, bright))),
        "scoop_low": float(scoop_low),
        "scoop_high": float(scoop_high),
        "crack_k": float(max(0.0, crack_k)),
        "baffle": float(max(0.5, baffle)),
        "style": style,
    }


def couple_outdoor_to_windows(
    room,
    mono: np.ndarray,
    src: Vec3,
    sr: int,
) -> List[WindowPortalHit]:
    """Route an outdoor drop into every window portal that can hear it.

    Geometry (sill, height, width) and open_style shape the gap, tone,
    and which outdoor heights couple best.
    """
    mono = np.asarray(mono, dtype=np.float64).reshape(-1)
    hits: List[WindowPortalHit] = []
    windows = list(getattr(room, "windows", []) or [])
    if not windows:
        return hits

    sx, sy, sz = src

    for w in windows:
        wall = str(getattr(w, "wall", "north") or "north")
        nrm = _wall_outward_normal(wall)
        ap = aperture_point(room, w)
        ext = exterior_point(room, w, out_dist=0.45)

        to_src = _sub(src, ap)
        out_align = _dot(_norm(to_src), nrm)
        if out_align < -0.05:
            continue

        d_out = max(0.2, _len(_sub(src, ext)))
        open_amt = max(0.0, min(1.0, float(getattr(w, "open", 0.7))))
        style = "casement"
        if hasattr(w, "resolved_style_for_draw"):
            style = w.resolved_style_for_draw()
        elif hasattr(w, "open_style_norm"):
            style = w.open_style_norm()
        else:
            style = str(getattr(w, "open_style", "casement") or "casement")

        angle = open_amt * float(getattr(w, "max_angle_deg", 75.0) or 75.0)
        if hasattr(w, "open_angle_deg"):
            try:
                angle = w.open_angle_deg()
            except Exception:
                pass

        hinge = "left"
        if hasattr(w, "resolved_hinge_side"):
            hinge = w.resolved_hinge_side()
        else:
            hinge = str(getattr(w, "hinge_side", "left") or "left")

        prof = window_acoustic_profile(
            open_amt, style,
            width=float(getattr(w, "width", 1.0)),
            height=float(getattr(w, "height", 1.2)),
            angle_deg=angle,
            hinge_side=hinge,
        )

        # --- Vertical geometry: does the outdoor drop align with the gap? ---
        if hasattr(w, "effective_gap_y_range"):
            gy0, gy1 = w.effective_gap_y_range()
        else:
            sill = float(getattr(w, "sill", 0.9))
            gh = float(getattr(w, "height", 1.2))
            gy0, gy1 = sill, sill + gh
        # distance from source height to gap band
        if gy0 <= sy <= gy1:
            vert_dist = 0.0
        else:
            vert_dist = min(abs(sy - gy0), abs(sy - gy1))
        vert_g = math.exp(-vert_dist * 1.15)
        # Style scoops: boost low or high outdoor sources
        mid_g = 0.5 * (gy0 + gy1)
        if sy < mid_g:
            vert_g *= 1.0 + 0.9 * max(0.0, prof["scoop_low"])
        else:
            vert_g *= 1.0 + 0.9 * max(0.0, prof["scoop_high"])
            if prof["scoop_low"] < 0:
                vert_g *= 1.0 + 0.5 * prof["scoop_low"]  # awning rejects high

        # --- Lateral geometry ---
        lat_vec = _sub(to_src, _scale(nrm, _dot(to_src, nrm)))
        lat = _len(lat_vec)
        half_w = 0.5 * float(getattr(w, "width", 1.0))
        # Crack styles: tighter lateral focus on the free edge
        edge_tight = 0.55 + 0.9 * prof["crack_k"]
        if lat <= half_w * (0.6 + 0.4 * open_amt):
            lat_g = 1.0
        else:
            lat_g = math.exp(-(lat - half_w) * edge_tight)

        # Axis / baffle: hinged sash directs outdoor sound
        axis = max(0.06, out_align) ** (0.85 / max(0.5, prof["baffle"]))

        att_out = distance_attenuation(d_out, ref=1.0, rolloff=1.1)
        depth_g = 1.0 / (1.0 + 0.08 * max(0.0, d_out - 1.0))

        gain = 2.6 * prof["gain"] * att_out * axis * lat_g * vert_g * depth_g
        if gain < 1e-5:
            continue

        fc_air = air_absorption_cutoff(d_out, base_hz=10000.0)
        fc = min(fc_air, prof["lp_fc"])
        # Bright styles keep a little more HF through the gap
        if prof["bright"] > 0:
            fc = min(12000.0, fc * (1.0 + 0.35 * prof["bright"]))

        sig = one_pole_lp(mono, fc, sr) * gain
        # Micro edge diffraction: slight HF tick for crack openings
        if prof["crack_k"] > 0.05 and open_amt > 0.02:
            crack = mono - one_pole_lp(mono, 1800.0, sr)
            sig = sig + crack * (0.12 * prof["crack_k"] * gain)

        delay_s = d_out / C_SOUND

        hits.append(
            WindowPortalHit(
                window_name=str(getattr(w, "name", wall)),
                wall=wall,
                exterior=ext,
                aperture=ap,
                signal=sig,
                open_amt=open_amt,
                outdoor_m=d_out,
                delay_s=delay_s,
            )
        )

    return hits


def indoor_arrival_from_portal(
    portal: WindowPortalHit,
    recv: Vec3,
    sr: int,
    gain_scale: float = 1.0,
) -> Optional[Tuple[IndoorArrival, np.ndarray]]:
    """Radiate from the window into the room to one receiver.

    Distance is aperture → speaker/ear. Sound is perceived as coming
    from the window (behind/at the glass).
    """
    ap = portal.aperture
    d_in = max(0.12, _len(_sub(recv, ap)))
    # Indoor 1/r — speakers near a window are much louder
    att_in = distance_attenuation(d_in, ref=0.45, rolloff=1.15)
    # Extra near-window boost so distance is obvious
    near = 1.0 + 1.4 * math.exp(-d_in * 1.8)

    # Directivity: windows radiate mostly inward (hemisphere)
    # Receiver should be inside; if somehow outside, attenuate
    # Use wall normal — indoor side is -outward
    # Approximate with whether receiver is on indoor side of aperture
    # (done simply via room containment at call site if needed)

    g = float(att_in * near * gain_scale)
    if g < 1e-6:
        return None

    # Mild indoor air / furniture muffling with distance
    fc = max(1200.0, 9000.0 / (1.0 + 0.35 * d_in))
    delay_s = portal.delay_s + d_in / C_SOUND

    arr = IndoorArrival(
        portal=portal,
        delay_s=delay_s,
        gain=g,
        lp_fc=fc,
        indoor_m=d_in,
        aim=ap,  # hear it FROM the window
    )

    sig = one_pole_lp(portal.signal, fc, sr) * g
    delay_n = int(round(delay_s * sr))
    delay_n = max(0, min(delay_n, int(0.15 * sr)))
    sig = delay_samples(sig, delay_n)
    return arr, sig


def roof_to_receiver(
    room,
    mono: np.ndarray,
    src: Vec3,
    recv: Vec3,
    sr: int,
    gain_scale: float = 1.0,
) -> Optional[np.ndarray]:
    """Muffled structure path for true roof hits (not window portals)."""
    sx, sy, sz = src
    h = float(getattr(room, "height", 2.6))
    if sy < h - 0.2:
        return None
    # Clamp to footprint
    rx = max(0.0, min(float(room.width), sx))
    rz = max(0.0, min(float(room.depth), sz))
    roof = (rx, h, rz)
    d = max(0.2, _len(_sub(recv, roof)))
    g = 0.35 * distance_attenuation(d, ref=1.0, rolloff=1.0) * float(gain_scale)
    sig = one_pole_lp(np.asarray(mono, dtype=np.float64), 1200.0, sr) * g
    delay_n = int(round((d / C_SOUND) * sr))
    return delay_samples(sig, min(delay_n, int(0.08 * sr)))


def render_drop_to_receiver_mono(
    room,
    mono: np.ndarray,
    src: Vec3,
    recv: Vec3,
    sr: int,
    gain_scale: float = 1.0,
) -> np.ndarray:
    """Full outdoor→windows→receiver mono field for one drop."""
    portals = couple_outdoor_to_windows(room, mono, src, sr)
    acc = None
    for p in portals:
        hit = indoor_arrival_from_portal(p, recv, sr, gain_scale=gain_scale)
        if hit is None:
            continue
        _arr, sig = hit
        acc = sig if acc is None else _sum_mono(acc, sig)
    # Optional roof for roof-layer hits
    roof = roof_to_receiver(room, mono, src, recv, sr, gain_scale=gain_scale * 0.8)
    if roof is not None:
        acc = roof if acc is None else _sum_mono(acc, roof)
    if acc is None:
        return np.zeros(4, dtype=np.float64)
    return acc


def render_outdoor_field_block(
    room,
    mono_field: np.ndarray,
    receivers: Sequence[Vec3],
    sr: int,
    out_depth_m: float = 6.0,
    gain_scale: float = 1.0,
) -> List[np.ndarray]:
    """Couple a continuous outdoor mono field through all windows to N receivers.

    The field is modelled as mid-yard sources along each window normal so
    every open portal contributes a steady wash (not discrete drops).
    Returns one mono buffer per receiver (same length as mono_field).
    """
    mono = np.asarray(mono_field, dtype=np.float64).reshape(-1)
    n = len(mono)
    outs = [np.zeros(n, dtype=np.float64) for _ in receivers]
    if n == 0 or not receivers:
        return outs
    windows = list(getattr(room, "windows", []) or [])
    if not windows:
        # Weak sealed-room leakage from roof / walls
        for i, recv in enumerate(receivers):
            outs[i] = one_pole_lp(mono, 900.0, sr) * (0.04 * gain_scale)
        return outs

    for w in windows:
        wall = str(getattr(w, "wall", "north") or "north")
        nrm = _wall_outward_normal(wall)
        # Virtual outdoor source: mid field outside this window
        ap = aperture_point(room, w)
        src = _add(ap, _scale(nrm, float(out_depth_m)))
        # Slight height variation for body of rain
        src = (src[0], float(getattr(w, "sill", 0.9)) + 0.4 * float(getattr(w, "height", 1.2)), src[2])
        portals = couple_outdoor_to_windows(room, mono, src, sr)
        for p in portals:
            for i, recv in enumerate(receivers):
                hit = indoor_arrival_from_portal(p, recv, sr, gain_scale=gain_scale * 0.55)
                if hit is None:
                    continue
                _arr, sig = hit
                if len(sig) < n:
                    pad = np.zeros(n, dtype=np.float64)
                    pad[: len(sig)] = sig
                    sig = pad
                else:
                    sig = sig[:n]
                outs[i] += sig
    return outs


def render_outdoor_field_binaural(
    room,
    mono_field: np.ndarray,
    listener_pos: Vec3,
    yaw: float,
    sr: int,
    out_depth_m: float = 6.0,
) -> np.ndarray:
    """Continuous outdoor field → windows → parametric HRTF at listener."""
    mono = np.asarray(mono_field, dtype=np.float64).reshape(-1)
    n = len(mono)
    if n == 0:
        return np.zeros((0, 2), dtype=np.float64)
    stereo = np.zeros((n, 2), dtype=np.float64)
    windows = list(getattr(room, "windows", []) or [])
    if not windows:
        # Dark sealed-room leakage, weakly spatial
        dark = one_pole_lp(mono, 800.0, sr) * 0.05
        stereo[:, 0] = dark * 0.95
        stereo[:, 1] = dark * 1.05
        return stereo

    for w in windows:
        wall = str(getattr(w, "wall", "north") or "north")
        nrm = _wall_outward_normal(wall)
        ap = aperture_point(room, w)
        src = _add(ap, _scale(nrm, float(out_depth_m)))
        src = (src[0], float(getattr(w, "sill", 0.9)) + 0.4 * float(getattr(w, "height", 1.2)), src[2])
        portals = couple_outdoor_to_windows(room, mono, src, sr)
        for p in portals:
            hit = indoor_arrival_from_portal(p, listener_pos, sr, gain_scale=0.55)
            if hit is None:
                continue
            arr, sig = hit
            if len(sig) < n:
                pad = np.zeros(n, dtype=np.float64)
                pad[: len(sig)] = sig
                sig = pad
            else:
                sig = sig[:n]
            az, el = azimuth_elevation(listener_pos, arr.aim, yaw=yaw)
            bi = binaural(sig, az, el, distance=max(0.35, arr.indoor_m), sr=sr)
            if bi.shape[0] < n:
                pad = np.zeros((n, 2), dtype=np.float64)
                pad[: bi.shape[0]] = bi
                bi = pad
            stereo += bi[:n]
    return stereo


def render_drop_to_listener_binaural(
    room,
    mono: np.ndarray,
    src: Vec3,
    listener_pos: Vec3,
    yaw: float,
    sr: int,
) -> np.ndarray:
    """Outdoor drop → windows → binaural at listener.

    Each window is a directional source in the room (from the glass).
    Openness controls how loud that wall's rain is.
    """
    portals = couple_outdoor_to_windows(room, mono, src, sr)
    stereo = None
    for p in portals:
        hit = indoor_arrival_from_portal(p, listener_pos, sr, gain_scale=1.0)
        if hit is None:
            continue
        arr, sig = hit
        az, el = azimuth_elevation(listener_pos, arr.aim, yaw=yaw)
        # Distance for ILD = indoor distance to the window (sound is at the glass)
        bi = binaural(sig, az, el, distance=max(0.35, arr.indoor_m), sr=sr)
        stereo = bi if stereo is None else _sum_stereo(stereo, bi)

    # Roof: slightly overhead / center-dark
    roof = roof_to_receiver(room, mono, src, listener_pos, sr, gain_scale=0.7)
    if roof is not None:
        # Aim slightly up at roof center
        aim = (listener_pos[0], float(getattr(room, "height", 2.6)), listener_pos[2])
        az, el = azimuth_elevation(listener_pos, aim, yaw=yaw)
        bi = binaural(roof, az, el, distance=1.2, sr=sr)
        stereo = bi if stereo is None else _sum_stereo(stereo, bi)

    if stereo is None:
        return np.zeros((4, 2), dtype=np.float64)
    return stereo


def _sum_mono(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == len(b):
        return a + b
    n = max(len(a), len(b))
    out = np.zeros(n, dtype=np.float64)
    out[: len(a)] += a
    out[: len(b)] += b
    return out


def _sum_stereo(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if a.shape == b.shape:
        return a + b
    n = max(a.shape[0], b.shape[0])
    out = np.zeros((n, 2), dtype=np.float64)
    out[: a.shape[0]] += a
    out[: b.shape[0]] += b
    return out


def azimuth_elevation(from_pos: Vec3, to_pos: Vec3, yaw: float = 0.0) -> Tuple[float, float]:
    dx = to_pos[0] - from_pos[0]
    dy = to_pos[1] - from_pos[1]
    dz = to_pos[2] - from_pos[2]
    c, s = math.cos(-yaw), math.sin(-yaw)
    rx = c * dx - s * dz
    rz = s * dx + c * dz
    az = math.atan2(rx, rz)
    dist = math.sqrt(dx * dx + dy * dy + dz * dz) + 1e-9
    el = math.asin(max(-1.0, min(1.0, dy / dist)))
    return az, el


# Back-compat shims for older call sites
@dataclass
class AcousticPath:
    delay_s: float
    gain: float
    lp_fc: float
    outdoor_m: float
    indoor_m: float
    via: str
    window_name: str = ""
    aperture: Optional[Vec3] = None


def best_paths(room, src, recv, max_paths=3):
    """Legacy: synthesize path list from new portal model for debug."""
    portals = couple_outdoor_to_windows(room, np.ones(8), src, 48000)
    paths = []
    for p in portals:
        hit = indoor_arrival_from_portal(p, recv, 48000)
        if hit is None:
            continue
        arr, _ = hit
        paths.append(
            AcousticPath(
                delay_s=arr.delay_s,
                gain=arr.gain * float(np.max(np.abs(p.signal)) + 1e-9),
                lp_fc=arr.lp_fc,
                outdoor_m=p.outdoor_m,
                indoor_m=arr.indoor_m,
                via="window",
                window_name=p.window_name,
                aperture=p.aperture,
            )
        )
    paths.sort(key=lambda x: x.gain, reverse=True)
    return paths[:max_paths]


def apply_path(mono, path, sr, gain_scale=1.0):
    g = float(path.gain) * float(gain_scale)
    y = one_pole_lp(np.asarray(mono, dtype=np.float64), path.lp_fc, sr) * g
    delay_n = max(0, min(int(round(path.delay_s * sr)), int(0.15 * sr)))
    return delay_samples(y, delay_n)
