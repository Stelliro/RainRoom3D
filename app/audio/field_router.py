"""
Stateful outdoor-field → window → receiver routing.

Unlike discrete drops (which can prepend a one-shot delay), a continuous
field must use circular delay lines so block boundaries stay seamless.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.audio.spatial import (
    aperture_point,
    air_absorption_cutoff,
    distance_attenuation,
    exterior_point,
    one_pole_lp,
    window_acoustic_profile,
    azimuth_elevation,
    _wall_outward_normal,
    _dot,
    _sub,
    _add,
    _scale,
    _len,
    _norm,
)
from app.audio.hrtf import woodworth_itd_s, ild_db

Vec3 = Tuple[float, float, float]
C_SOUND = 343.0


class _DelayLine:
    """Streaming delay using a trailing history buffer (vectorized)."""

    def __init__(self, max_n: int):
        self.max_n = max(4, int(max_n) + 4)
        self.hist = np.zeros(self.max_n, dtype=np.float64)

    def process(self, x: np.ndarray, delay_n: int) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64).reshape(-1)
        n = len(x)
        if n == 0:
            return x
        delay_n = int(max(0, min(delay_n, self.max_n - 1)))
        combined = np.concatenate([self.hist, x])
        # read from delay_n samples behind the current write position
        start = len(self.hist) - delay_n
        y = combined[start : start + n].copy()
        self.hist = combined[-self.max_n :]
        return y


def _light_binaural(mono: np.ndarray, az: float, el: float, dist: float, sr: int) -> np.ndarray:
    """Cheap continuous-field spatialisation (ILD only).

    No per-block ITD: shifting samples every block inserts zeros at the
    start of each buffer and crackles. Discrete drops use full HRTF once.
    """
    x = np.asarray(mono, dtype=np.float64).reshape(-1)
    n = len(x)
    if n == 0:
        return np.zeros((0, 2), dtype=np.float64)
    ild = ild_db(az, el)
    gL = 10.0 ** ((-ild) / 20.0)
    gR = 10.0 ** ((+ild) / 20.0)
    pnorm = math.sqrt(gL * gL + gR * gR) + 1e-12
    gL *= math.sqrt(2.0) / pnorm
    gR *= math.sqrt(2.0) / pnorm
    if dist > 0.8:
        att = 0.8 / (0.8 + (dist - 0.8))
    else:
        att = 1.0
    gL *= att
    gR *= att
    return np.column_stack([x * gL, x * gR])


# Depth layers → outdoor source distance along window normal
_LAYER_DEPTH_M = {
    "near": 1.8,
    "mid": 6.5,
    "far": 16.0,
    "canopy": 4.0,
}
_LAYER_GAIN = {
    "near": 1.15,
    "mid": 1.0,
    "far": 0.85,
    "canopy": 0.75,
}
_LAYER_HEIGHT = {
    "near": 0.35,   # fraction of window height above sill
    "mid": 0.45,
    "far": 0.40,
    "canopy": 1.35,  # above window → elevated
}


def _lp_zi(x: np.ndarray, fc: float, sr: float, state: float) -> Tuple[np.ndarray, float]:
    """Stateful one-pole LP — continuous across blocks (avoids filter clicks)."""
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    if len(x) == 0 or fc <= 0 or sr <= 0:
        return x, state
    dt = 1.0 / sr
    rc = 1.0 / (2.0 * math.pi * max(20.0, fc))
    a = dt / (rc + dt)
    try:
        from scipy import signal as sp_signal
        y, zf = sp_signal.lfilter([a], [1.0, -(1.0 - a)], x, zi=[state])
        return y, float(zf[0])
    except Exception:
        y = np.empty_like(x)
        s = float(state)
        for i, v in enumerate(x):
            s = s + a * (float(v) - s)
            y[i] = s
        return y, s


class OutdoorFieldRouter:
    """Caches portal gains; streams multi-depth outdoor fields to speakers + binaural."""

    def __init__(self, sr: int = 48000):
        self.sr = int(sr)
        # layer → list of path dicts
        self._speaker_paths: Dict[str, List[dict]] = {}
        self._listener_paths: Dict[str, List[dict]] = {}
        self._fingerprint: Optional[tuple] = None
        self._max_delay = int(0.18 * self.sr)
        self._lp_state: Dict[str, float] = {"near": 0.0, "mid": 0.0, "far": 0.0, "leak": 0.0}

    def _room_fingerprint(self, room) -> tuple:
        wins = []
        for w in getattr(room, "windows", []) or []:
            wins.append((
                str(getattr(w, "wall", "")),
                round(float(getattr(w, "open", 0)), 3),
                round(float(getattr(w, "width", 1)), 2),
                round(float(getattr(w, "height", 1)), 2),
                round(float(getattr(w, "sill", 0.9)), 2),
                str(getattr(w, "open_style", "")),
                str(getattr(w, "hinge_side", "")),
            ))
        spk = []
        for s in getattr(room, "speakers", []) or []:
            spk.append((
                round(float(s.x), 2), round(float(s.y), 2), round(float(s.z), 2),
                bool(getattr(s, "enabled", True)),
            ))
        L = room.listener
        return (
            round(float(room.width), 2),
            round(float(room.depth), 2),
            round(float(room.height), 2),
            tuple(wins),
            tuple(spk),
            round(float(L.x), 2),
            round(float(L.z), 2),
            round(float(getattr(L, "yaw", 0.0)), 3),
        )

    def _build_paths(self, room):
        sr = self.sr
        self._speaker_paths = {k: [] for k in _LAYER_DEPTH_M}
        self._listener_paths = {k: [] for k in _LAYER_DEPTH_M}
        windows = list(getattr(room, "windows", []) or [])
        speakers = list(getattr(room, "speakers", []) or [])
        L = room.listener
        listener = (float(L.x), float(getattr(L, "y", 1.2)), float(L.z))
        yaw = float(getattr(L, "yaw", 0.0))

        for layer, out_depth_m in _LAYER_DEPTH_M.items():
            lg = _LAYER_GAIN.get(layer, 1.0)
            hfrac = _LAYER_HEIGHT.get(layer, 0.4)
            for w in windows:
                wall = str(getattr(w, "wall", "north") or "north")
                nrm = _wall_outward_normal(wall)
                ap = aperture_point(room, w)
                ext = exterior_point(room, w, out_dist=0.45)
                src = _add(ap, _scale(nrm, float(out_depth_m)))
                sill = float(getattr(w, "sill", 0.9))
                wh = float(getattr(w, "height", 1.2))
                src = (src[0], sill + hfrac * wh, src[2])
                open_amt = max(0.0, min(1.0, float(getattr(w, "open", 0.7))))
                style = "casement"
                if hasattr(w, "open_style_norm"):
                    style = w.open_style_norm()
                else:
                    style = str(getattr(w, "open_style", "casement") or "casement")
                angle = open_amt * float(getattr(w, "max_angle_deg", 75.0) or 75.0)
                if hasattr(w, "open_angle_deg"):
                    try:
                        angle = w.open_angle_deg()
                    except Exception:
                        pass
                prof = window_acoustic_profile(
                    open_amt, style,
                    width=float(getattr(w, "width", 1.0)),
                    height=float(getattr(w, "height", 1.2)),
                    angle_deg=angle,
                    hinge_side=str(getattr(w, "hinge_side", "left") or "left"),
                )
                d_out = max(0.2, _len(_sub(src, ext)))
                to_src = _sub(src, ap)
                out_align = _dot(_norm(to_src), nrm)
                if out_align < -0.05:
                    continue
                axis = max(0.06, out_align) ** (0.85 / max(0.5, prof["baffle"]))
                att_out = distance_attenuation(d_out, ref=1.0, rolloff=1.05)
                depth_g = 1.0 / (1.0 + 0.06 * max(0.0, d_out - 1.0))
                portal_g = 2.8 * prof["gain"] * att_out * axis * depth_g * 0.62 * lg
                if portal_g < 1e-5:
                    continue
                fc_air = air_absorption_cutoff(d_out, base_hz=10000.0)
                fc_portal = min(fc_air, prof["lp_fc"])
                if layer == "far":
                    fc_portal = min(fc_portal, 2200.0)
                elif layer == "near":
                    fc_portal = min(12000.0, fc_portal * 1.15)
                if prof["bright"] > 0:
                    fc_portal = min(12000.0, fc_portal * (1.0 + 0.35 * prof["bright"]))
                delay_out_n = int(round((d_out / C_SOUND) * sr))

                for si, spk in enumerate(speakers):
                    if not getattr(spk, "enabled", True):
                        continue
                    recv = (float(spk.x), float(spk.y), float(spk.z))
                    d_in = max(0.12, _len(_sub(recv, ap)))
                    att_in = distance_attenuation(d_in, ref=0.45, rolloff=1.15)
                    near_b = 1.0 + 1.4 * math.exp(-d_in * 1.8)
                    g = portal_g * att_in * near_b
                    if g < 1e-6:
                        continue
                    fc_in = max(1200.0, 9000.0 / (1.0 + 0.35 * d_in))
                    delay_n = min(self._max_delay, delay_out_n + int(round((d_in / C_SOUND) * sr)))
                    self._speaker_paths[layer].append({
                        "si": si,
                        "gain": g,
                        "fc": min(fc_portal, fc_in),
                        "delay": delay_n,
                        "dl": _DelayLine(self._max_delay + 8),
                    })

                d_in = max(0.12, _len(_sub(listener, ap)))
                att_in = distance_attenuation(d_in, ref=0.45, rolloff=1.15)
                # Almost no near boost — multi-window stack was drowning drops on headphones
                near_b = 1.0 + 0.25 * math.exp(-d_in * 2.2)
                g = portal_g * att_in * near_b * 0.28  # You wash << speaker mics
                if g >= 1e-6:
                    # Darker indoor path: kills hiss/static that speakers hide in the room
                    fc_in = max(800.0, 4800.0 / (1.0 + 0.45 * d_in))
                    delay_n = min(self._max_delay, delay_out_n + int(round((d_in / C_SOUND) * sr)))
                    az, el = azimuth_elevation(listener, ap, yaw=yaw)
                    if layer == "canopy":
                        el = max(el, 0.35)
                    self._listener_paths[layer].append({
                        "gain": g,
                        "fc": min(fc_portal, fc_in),
                        "delay": delay_n,
                        "az": az,
                        "el": el,
                        "dist": d_in,
                        "dl": _DelayLine(self._max_delay + 8),
                    })

    def ensure(self, room):
        fp = self._room_fingerprint(room)
        if fp != self._fingerprint:
            self._fingerprint = fp
            self._build_paths(room)

    def process(
        self,
        room,
        outdoor_mono: np.ndarray,
        n_speakers: int,
        render_listener: bool = True,
    ) -> Tuple[Dict[int, np.ndarray], Optional[np.ndarray]]:
        """Back-compat: single mono outdoor field as 'mid' layer."""
        return self.process_layers(
            room,
            {"mid": outdoor_mono},
            n_speakers=n_speakers,
            render_listener=render_listener,
        )

    def process_layers(
        self,
        room,
        layers: Dict[str, np.ndarray],
        n_speakers: int,
        render_listener: bool = True,
    ) -> Tuple[Dict[int, np.ndarray], Optional[np.ndarray]]:
        """Route multi-depth outdoor layers through window portals."""
        # determine frames
        frames = 0
        for v in layers.values():
            frames = max(frames, len(np.asarray(v).reshape(-1)))
        spk_out: Dict[int, np.ndarray] = {
            i: np.zeros(frames, dtype=np.float64) for i in range(max(1, n_speakers))
        }
        bi = np.zeros((frames, 2), dtype=np.float64) if render_listener else None
        if frames == 0:
            return spk_out, bi

        self.ensure(room)
        any_paths = any(self._speaker_paths.get(k) or self._listener_paths.get(k) for k in _LAYER_DEPTH_M)
        if not any_paths:
            mix = layers.get("mix")
            if mix is None:
                acc = np.zeros(frames, dtype=np.float64)
                for v in layers.values():
                    vv = np.asarray(v, dtype=np.float64).reshape(-1)
                    acc[: min(frames, len(vv))] += vv[: min(frames, len(vv))]
                mix = acc
            dark, self._lp_state["leak"] = _lp_zi(
                np.asarray(mix, dtype=np.float64)[:frames], 900.0, self.sr, self._lp_state["leak"]
            )
            dark = dark * 0.04
            for i in spk_out:
                spk_out[i] = dark.copy()
            if bi is not None:
                bi[:, 0] = dark * 0.95
                bi[:, 1] = dark * 1.05
            return spk_out, bi

        # Fold canopy→near and keep mid/far so we route 3 depth streams max
        # (depth colour already baked into the layer audio).
        folded: Dict[str, np.ndarray] = {}
        for layer in ("near", "mid", "far", "canopy"):
            x = layers.get(layer)
            if x is None:
                continue
            x = np.asarray(x, dtype=np.float64).reshape(-1)
            if len(x) < frames:
                pad = np.zeros(frames, dtype=np.float64)
                pad[: len(x)] = x
                x = pad
            else:
                x = x[:frames]
            key = "near" if layer == "canopy" else layer
            if key in folded:
                folded[key] = folded[key] + x * (0.65 if layer == "canopy" else 1.0)
            else:
                folded[key] = x if layer != "canopy" else x * 0.65

        # Stateful LP per layer (continuous) — paths only delay + gain
        # Speakers keep air; binaural is much darker (hiss reads as static on cans)
        layer_lp_spk = {
            "near": 3600.0,
            "mid": 2400.0,
            "far": 1200.0,
        }
        layer_lp_bi = {
            "near": 1500.0,
            "mid": 1000.0,
            "far": 650.0,
        }
        for layer, x in folded.items():
            if float(np.max(np.abs(x))) < 1e-9:
                continue
            st = self._lp_state.get(layer, 0.0)
            x_spk, st = _lp_zi(x, layer_lp_spk.get(layer, 2400.0), self.sr, st)
            self._lp_state[layer] = st
            # Extra darkening for headphones only (don't mutate speaker state twice hard)
            st_bi = self._lp_state.get(layer + "_bi", 0.0)
            x_bi, st_bi = _lp_zi(x, layer_lp_bi.get(layer, 1000.0), self.sr, st_bi)
            self._lp_state[layer + "_bi"] = st_bi

            for p in self._speaker_paths.get(layer, []):
                y = p["dl"].process(x_spk * p["gain"], p["delay"])
                si = p["si"]
                if si not in spk_out:
                    spk_out[si] = np.zeros(frames, dtype=np.float64)
                spk_out[si] += y

            if bi is not None:
                for p in self._listener_paths.get(layer, []):
                    y = p["dl"].process(x_bi * p["gain"], p["delay"])
                    bi += _light_binaural(
                        y, p["az"], p["el"], max(0.35, p["dist"]), self.sr
                    )

        # Cap binaural wash peak + RMS so multi-window stack stays underlay
        # (peak alone left continuous ocean energy that buried droplets)
        if bi is not None:
            pk = float(np.max(np.abs(bi)) + 1e-12)
            if pk > 0.055:
                bi *= 0.055 / pk
            rms = float(np.sqrt(np.mean(bi * bi)) + 1e-12)
            if rms > 0.016:
                bi *= 0.016 / rms

        return spk_out, bi

    def reset(self):
        self._fingerprint = None
        self._speaker_paths = {}
        self._listener_paths = {}
        self._lp_state = {
            "near": 0.0, "mid": 0.0, "far": 0.0, "leak": 0.0,
            "near_bi": 0.0, "mid_bi": 0.0, "far_bi": 0.0,
        }
