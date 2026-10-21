"""
The yard: rain on the ground all around the house, heard through each
opening from wherever the speakers and the listener are inside.

The house's drop paths (spatial.py) carry rain on the house itself and the
thin splash ring at its foot. Out past that ring the whole yard is raining
too, and that is what an open window lets in. Each window and wall is an
opening onto the yard:

  outside   how much ground rain the opening sees: Σ cosθ · att(r)² dA over
            the yard in front of it (``yard_view``), the same distance law a
            single drop gets through a window (couple_outdoor_to_windows).
  through   the window's open transmission (window_acoustic_profile, so
            style, size and how far it's open all count) or, closed, the
            wall's / glass's airborne leak; muffled by the gap or the panel.
  inside    radiated from the opening to each speaker and to the ears with
            the same 1/r + near-window law as a drop, from the opening's
            direction (ILD, ITD and head shadow on headphones).

So opening a window next to a speaker makes that speaker bring the yard in,
and moving it moves where the yard comes from. One dense texture of water
grains is shared by every opening (different stems keep them uncorrelated);
everything after it is stateful so blocks join seamlessly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.audio import house_model as hm
from app.audio.spatial import (
    _wall_outward_normal,
    azimuth_elevation,
    distance_attenuation,
    window_acoustic_profile,
)

try:
    from scipy import signal as _sps
except Exception:  # pragma: no cover
    _sps = None

C_SOUND = 343.0
# Rain this far out still adds; past it the yard is a tiny log-tail
YARD_RADIUS_M = 20.0
# Texture drops/s stand for this much yard; per-opening gains scale to the
# area it actually sees
YARD_REF_AREA_M2 = 4.0
# Same constant the per-drop portal applies (couple_outdoor_to_windows)
_PORTAL_K = 2.6
# Opening → room radiation (indoor_arrival_from_portal)
_IN_REF, _IN_ROLL = 0.45, 1.15
HEAD_R = 0.0875
# Re-route only once the room has held still this long (s of audio)
SETTLE_S = 0.3


@lru_cache(maxsize=128)
def yard_view(height_m: float, reveal: Tuple[float, float, float] = (0.0, 1.0, 1.0),
              radius_m: float = YARD_RADIUS_M) -> float:
    """∫ cosθ · att(r)² dA of the ground in front of an opening at
    ``height_m`` — ground past the splash ring, out to ``radius_m``.
    ``reveal`` = (wall depth, opening width, opening height): a deep wall
    hides the yard off to the sides and right below (reveal_fraction)."""
    h = max(0.05, float(height_m))
    # Polar grid on the ground around the opening's foot, front half only
    rr = np.geomspace(hm.RING_OUT_M, radius_m, 160)
    dr = np.gradient(rr)
    th = np.linspace(-math.pi / 2, math.pi / 2, 91)
    dth = th[1] - th[0]
    R, T = np.meshgrid(rr, th, indexing="ij")
    a = R * np.cos(T)  # out along the wall normal
    keep = a >= hm.RING_OUT_M
    dist = np.sqrt(R * R + h * h)
    cos_in = a / dist
    ref, roll = 1.0, 1.1
    att = np.where(dist <= ref, 1.0, ref / (ref + roll * (dist - ref)))
    dA = R * dr[:, None] * dth
    depth, op_w, op_h = reveal
    seen = 1.0
    if depth > 0.0:
        a_safe = np.maximum(a, 1e-6)
        fl = np.clip(1.0 - depth * np.abs(R * np.sin(T)) / a_safe / max(0.05, op_w), 0.0, 1.0)
        fv = np.clip(1.0 - depth * h / a_safe / max(0.05, op_h), 0.0, 1.0)
        seen = fl * fv
    return float(np.sum(np.where(keep, cos_in * att * att * dA * seen, 0.0)))


def _profile(win, glaze_k: float = 1.0) -> dict:
    style = win.resolved_style_for_draw() if hasattr(win, "resolved_style_for_draw") else str(
        getattr(win, "open_style", "casement") or "casement")
    open_amt = max(0.0, min(1.0, float(getattr(win, "open", 0.0) or 0.0)))
    angle = open_amt * float(getattr(win, "max_angle_deg", 75.0) or 75.0)
    if hasattr(win, "open_angle_deg"):
        try:
            angle = win.open_angle_deg()
        except Exception:
            pass
    hinge = win.resolved_hinge_side() if hasattr(win, "resolved_hinge_side") else str(
        getattr(win, "hinge_side", "left") or "left")
    return window_acoustic_profile(
        open_amt, style,
        width=float(getattr(win, "width", 1.0)),
        height=float(getattr(win, "height", 1.2)),
        angle_deg=angle, hinge_side=hinge, leak_k=glaze_k,
    )


@dataclass
class Opening:
    """One way the yard gets in: an open/closed window or a wall panel."""
    key: tuple
    wall: str
    amp: float                     # yard amplitude at the opening (× texture)
    lp_hz: float                   # gap / panel muffling
    point: Tuple[float, float, float]           # radiating centre (inside face)
    area: float                    # m² it radiates from
    rect: Optional[Tuple[float, float, float, float]] = None  # wall panel: u0, u1, y0, y1


def openings(room) -> List[Opening]:
    """Every opening onto the yard with its transmission."""
    w, d, h = float(room.width), float(room.depth), float(room.height)
    out: List[Opening] = []
    depth = hm.wall_thickness(room)

    def seen(y: float, op_w: float = 1e3, op_h: float = 1e3) -> float:
        # Big panels have no reveal; windows sit ``depth`` into the wall
        rv = (round(depth, 3), round(op_w, 1), round(op_h, 1)) if op_w < 1e3 else (0.0, 1.0, 1.0)
        return yard_view(round(y, 1), rv)

    glass = {wall: 0.0 for wall in ("north", "south", "east", "west")}
    for i, win in enumerate(getattr(room, "windows", []) or []):
        wall = str(getattr(win, "wall", "north") or "north").lower()
        try:
            cx, cy, cz = room.window_center(win)
        except Exception:
            continue
        prof = _profile(win, hm.glazing_k(room))
        if not getattr(win, "free_place", False):
            glass[wall] = glass.get(wall, 0.0) + float(win.width) * float(win.height)
        if getattr(win, "free_place", False):
            view = seen(cy)
        else:
            view = seen(cy, float(win.width), float(win.height))
        amp = _PORTAL_K * prof["gain"] * math.sqrt(view / YARD_REF_AREA_M2)
        gap = float(win.width) * float(win.height) * max(0.05, prof["gain"] / max(1e-6, prof["area_k"]))
        out.append(Opening(("win", i), wall, amp, min(9000.0, prof["lp_fc"]), (cx, cy, cz), gap))
    wm = hm.wall_material(room)
    for wall in ("north", "south", "east", "west"):
        length = hm.wall_length(room, wall)
        area = max(0.0, length * h - glass[wall])
        if area <= 0.0 or wm.airborne <= 0.0:
            continue
        # The closed panel leaks like a closed window of its size would
        leak = wm.airborne * math.sqrt(area / 1.2)
        amp = _PORTAL_K * leak * math.sqrt(seen(h * 0.5) / YARD_REF_AREA_M2)
        mid = hm.wall_point(room, wall, 0.5 * length, h * 0.5)
        out.append(Opening(("wall", wall), wall, amp, float(wm.lp_hz), mid, area, (0.0, length, 0.0, h)))
    return out


def _radiate(op: Opening, room, recv: Tuple[float, float, float]) -> Tuple[float, Tuple[float, float, float]]:
    """(indoor distance, the point it's heard from) — a wall panel is heard
    from its nearest part."""
    if op.rect is None:
        p = op.point
    else:
        u0, u1, y0, y1 = op.rect
        x, y, z = recv
        u = x if op.wall in ("north", "south") else z
        p = hm.wall_point(room, op.wall, max(u0, min(u1, u)), max(y0, min(y1, y)))
    # Close to a big opening you're in its near field: the level levels off
    # at its own intensity instead of growing like a point source
    d = math.sqrt(math.dist(recv, p) ** 2 + op.area / (2.0 * math.pi))
    return max(0.12, d), p


def indoor_gain(d_in: float) -> float:
    """Opening → receiver, as indoor_arrival_from_portal."""
    return distance_attenuation(d_in, ref=_IN_REF, rolloff=_IN_ROLL) * (1.0 + 1.4 * math.exp(-d_in * 1.8))


class _OnePole:
    """Stateful one-pole low-pass (seamless across blocks)."""

    def __init__(self):
        self.z = 0.0
        self.fc = -1.0
        self.a = 1.0

    def __call__(self, x: np.ndarray, fc: float, sr: int) -> np.ndarray:
        if fc != self.fc:
            self.fc = fc
            rc = 1.0 / (2.0 * math.pi * max(20.0, fc))
            self.a = (1.0 / sr) / (rc + 1.0 / sr)
        a = self.a
        if _sps is not None:
            y, zf = _sps.lfilter([a], [1.0, -(1.0 - a)], x, zi=[self.z * (1.0 - a)])
            self.z = float(y[-1]) if len(y) else self.z
            return y
        y = np.empty_like(x)
        acc = self.z
        for i, v in enumerate(x):
            acc += a * (float(v) - acc)
            y[i] = acc
        self.z = acc
        return y


class _Delay:
    """Fixed integer delay with history (seamless; a change jumps once)."""

    def __init__(self, max_n: int = 64):
        self.hist = np.zeros(max_n, dtype=np.float64)

    def __call__(self, x: np.ndarray, n: int) -> np.ndarray:
        n = max(0, min(int(n), len(self.hist)))
        if n == 0:
            self.hist = np.concatenate([self.hist, x])[-len(self.hist):]
            return x
        both = np.concatenate([self.hist, x])
        y = both[len(self.hist) - n: len(self.hist) - n + len(x)]
        self.hist = both[-len(self.hist):]
        return y


@dataclass
class _Route:
    op: Opening
    stem: int
    spk: Dict[int, float]
    ears: Tuple[float, float, int, int, float]  # gL, gR, delay L, delay R, shadow Hz


class YardRouter:
    """Yard texture stems → openings → speakers, wall buses and the ears."""

    def __init__(self, sr: int):
        self.sr = int(sr)
        self._fp = None
        self._pending: Optional[tuple] = None
        self._routes: List[_Route] = []
        self._prev: Dict[tuple, Dict[object, float]] = {}
        self._lp: Dict[tuple, _OnePole] = {}
        self._dl: Dict[tuple, _Delay] = {}

    @staticmethod
    def fingerprint(room) -> tuple:
        L = room.listener
        wins = tuple(
            (str(getattr(w, "wall", "")), round(float(getattr(w, "offset", 0.0) or 0.0), 2),
             round(float(getattr(w, "sill", 0.0) or 0.0), 2), round(float(getattr(w, "width", 0.0) or 0.0), 2),
             round(float(getattr(w, "height", 0.0) or 0.0), 2), round(float(getattr(w, "open", 0.0) or 0.0), 2),
             str(getattr(w, "open_style", "")), bool(getattr(w, "free_place", False)),
             round(float(getattr(w, "free_x", 0.0) or 0.0), 2), round(float(getattr(w, "free_y", 0.0) or 0.0), 2),
             round(float(getattr(w, "free_z", 0.0) or 0.0), 2), round(float(getattr(w, "max_angle_deg", 0.0) or 0.0)),
             str(getattr(w, "hinge_side", "")), str(getattr(w, "custom_hinge", "")), str(getattr(w, "custom_motion", "")))
            for w in (getattr(room, "windows", []) or [])
        )
        spk = tuple((round(float(s.x), 2), round(float(s.y), 2), round(float(s.z), 2),
                     bool(getattr(s, "enabled", True)), round(float(getattr(s, "gain_db", 0.0) or 0.0), 1))
                    for s in room.speakers)
        return (round(float(room.width), 2), round(float(room.depth), 2), round(float(room.height), 2),
                str(getattr(room, "wall_material", "")), round(hm.wall_thickness(room), 3),
                str(getattr(room, "wall_material", "")), wins, spk,
                round(float(L.x), 2), round(float(getattr(L, "y", 1.2)), 2), round(float(L.z), 2),
                round(float(getattr(L, "yaw", 0.0)), 2))

    def _build(self, room, n_stems: int, hp_gain: float) -> None:
        routes = []
        L = room.listener
        lpos = (float(L.x), float(getattr(L, "y", 1.2)), float(L.z))
        yaw = float(getattr(L, "yaw", 0.0))
        for j, op in enumerate(openings(room)):
            if op.amp <= 1e-6:
                continue
            spk = {}
            for i, s in enumerate(room.speakers):
                if not getattr(s, "enabled", True):
                    continue
                d, _p = _radiate(op, room, (float(s.x), float(s.y), float(s.z)))
                spk[i] = op.amp * indoor_gain(d) * 10.0 ** (float(getattr(s, "gain_db", 0.0) or 0.0) / 20.0)
            d, p = _radiate(op, room, lpos)
            az, el = azimuth_elevation(lpos, p, yaw=yaw)
            g = op.amp * indoor_gain(d) * hp_gain
            # ILD (sin law, power-normalised) as the light field binaural
            ild = 12.0 * math.sin(az) * (0.75 + 0.25 * math.cos(el)) * (0.82 if math.cos(az) < 0 else 1.0)
            gl, gr = 10.0 ** (-ild / 40.0), 10.0 ** (ild / 40.0)
            norm = math.sqrt(2.0) / math.sqrt(gl * gl + gr * gr)
            az_c = max(-math.pi / 2, min(math.pi / 2, math.atan2(math.sin(az), abs(math.cos(az)))))
            itd = int(round(abs(HEAD_R / C_SOUND * (math.sin(az_c) + az_c)) * self.sr))
            dl, dr = (itd, 0) if az > 0 else (0, itd)  # far ear hears it later…
            shadow = 16000.0 / (1.0 + 4.0 * abs(math.sin(az)))  # …and darker
            routes.append(_Route(op, j % max(1, n_stems), spk, (g * gl * norm, g * gr * norm, dl, dr, shadow)))
        self._routes = routes

    def process(self, room, stems: np.ndarray, mix: Dict[int, np.ndarray],
                ears: Optional[np.ndarray], wall_bus: Dict[str, int], hp_gain: float,
                now: Optional[float] = None) -> None:
        """Add the yard (stems: n_stems × frames) to ``mix`` and ``ears``.

        A changed room re-routes once it has held still SETTLE_S (``now`` is
        audio time); while a window or speaker is being dragged the old
        routes keep playing, then the gains glide over one block."""
        n_st, frames = stems.shape
        fp = (self.fingerprint(room), n_st, round(hp_gain, 4))
        if fp != self._fp:
            ready = self._fp is None or now is None
            if not ready:
                if self._pending is None or self._pending[0] != fp:
                    self._pending = (fp, now)
                ready = now - self._pending[1] >= SETTLE_S
            if ready:
                self._pending = None
                self._fp = fp
                self._build(room, n_st, hp_gain)
        ramp = np.linspace(0.0, 1.0, frames, endpoint=False) if frames > 1 else np.zeros(frames)
        seen = set()
        for r in self._routes:
            key = r.op.key
            seen.add(key)
            lp = self._lp.setdefault(key, _OnePole())
            x = lp(stems[r.stem], r.op.lp_hz, self.sr)
            gl, gr, dl, dr, shadow = r.ears
            want: Dict[object, float] = dict(r.spk)
            want["L"], want["R"] = gl, gr
            want["bus"] = r.op.amp * 0.42
            prev = self._prev.get(key, {k: 0.0 for k in want})

            def gain(k):
                g0, g1 = prev.get(k, 0.0), want.get(k, 0.0)
                return g1 if g0 == g1 else g0 + (g1 - g0) * ramp

            for i in r.spk:
                if i in mix:
                    mix[i] = mix[i] + x * gain(i)
                else:
                    mix[i] = x * gain(i)
            wid = wall_bus.get(r.op.wall)
            if wid is not None:
                b = x * gain("bus")
                mix[wid] = mix[wid] + b if wid in mix else b
            if ears is not None and (gl > 0.0 or gr > 0.0):
                far_l = dl > 0
                xs = self._lp.setdefault(key + ("shadow",), _OnePole())(x, shadow, self.sr)
                xl = self._dl.setdefault(key + ("L",), _Delay())(xs if far_l else x, dl)
                xr = self._dl.setdefault(key + ("R",), _Delay())(x if far_l else xs, dr)
                ears[:, 0] += xl * gain("L")
                ears[:, 1] += xr * gain("R")
            self._prev[key] = want
        for key in [k for k in self._prev if k not in seen]:
            del self._prev[key]

    def reset(self) -> None:
        self._fp = None
        self._pending = None
        self._prev, self._lp, self._dl = {}, {}, {}


__all__ = ["Opening", "YARD_RADIUS_M", "YardRouter", "indoor_gain", "openings", "yard_view"]
