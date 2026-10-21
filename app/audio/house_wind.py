"""
What the wind does TO the house, heard from inside (wind.py is the wind
outside; outdoor_router.py carries that in through openings and panels).

  drafts     Wind pushes on the windward wall and sucks on the others and the
             roof (pressure coefficients Cp). Air only flows through the
             house where it can get out again: the indoor pressure settles
             where inflow = outflow, and that sets the air speed in every
             open window, v = U·√|Cp − Cp_in|. One window open alone barely
             breathes (the rest leaks through cracks); windows on opposite
             sides draw a real draft, and the air rushing through the
             opening is a sound made AT the window, ∝ v³ and its size.
  buffeting  Gust pressure flexes the walls and roof: a low thump and rumble
             radiated from the panel, ∝ 1/mass (material and thickness), so
             a thin tin roof booms and thick brick barely moves.
  rattles    Shut hinged windows knock in their frames when strong gusts
             load their face.

Every signal is made at an aperture (key as in outdoor_router.apertures) and
only radiated inside, through OutdoorRouter's ``local`` path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.audio import outdoor_router as orr
from app.audio.wind import WIND_MAX_MS, _Biquad, _LP1, _lp1_gain, wind_ms

# Wall pressure from the cosine c between its outward normal and where the
# wind comes from: +0.7 square on, −0.5 side on, −0.3 in the lee; roof suction
_CP_FRONT, _CP_SIDE, _CP_LEE, _CP_ROOF = 0.7, -0.5, -0.3, -0.6
# Discharge coefficient of a sharp-edged opening
_CD = 0.6
# Background leaks (effective m² per m² of envelope): wall joints, eaves,
# the frames of shut windows
_LEAK_WALL, _LEAK_ROOF, _LEAK_FRAME = 2.0e-4, 1.0e-4, 1.0e-3
# Panel weight the buffeting is referred to (kg/m²)
_M_REF = 10.0
_HINGED = ("casement", "awning", "hopper", "tilt_turn")

# Levels (outdoor-amplitude units, like WindSound; engine scales by _WIND_LEVEL)
DRAFT_K = 0.35
BUFFET_K = 0.1
RATTLE_K = 0.35


def wall_cp(c: float) -> float:
    c = max(-1.0, min(1.0, float(c)))
    if c >= 0.0:
        return _CP_SIDE + (_CP_FRONT - _CP_SIDE) * c
    return _CP_SIDE + (_CP_LEE - _CP_SIDE) * (-c)


def _face_cos(wall: str, wind_from_deg: float) -> float:
    if wall == "roof":
        return 0.0
    a = math.radians(wind_from_deg)
    nx, nz = orr._WALL_N.get(wall, (0.0, 1.0))
    return math.sin(a) * nx + math.cos(a) * nz


def open_area(win, gap_m: float) -> float:
    """Free area (m²) of a window's opening."""
    w, h = float(getattr(win, "width", 1.0)), float(getattr(win, "height", 1.2))
    if gap_m <= 0.0:
        return 0.0
    style = str(getattr(win, "open_style", "casement") or "casement").lower()
    if style in _HINGED:
        # Slot at the free edge plus the two triangles at the sides
        return min(w * h, gap_m * (w + h))
    return min(w * h, gap_m * h)


@dataclass
class Vent:
    key: tuple
    wall: str
    area: float        # effective m² (Cd · free area)
    cp: float
    speed: float       # air speed through it per 1 m/s of wind
    inflow: bool
    shut_hinged: bool  # can rattle


def ventilation(room, wind_from_deg: float, aps: Optional[Sequence[orr.Aperture]] = None
                ) -> Tuple[List[Vent], float]:
    """Open windows with their air speed per m/s of wind, and the indoor Cp."""
    if aps is None:
        aps = orr.apertures(room)
    wins = list(getattr(room, "windows", []) or [])
    openings: List[Tuple[Optional[tuple], str, float, float, bool]] = []
    for ap in aps:
        cp = _CP_ROOF if ap.wall == "roof" else wall_cp(_face_cos(ap.wall, wind_from_deg))
        if ap.key[0] == "win":
            win = wins[ap.key[1]]
            a = _CD * open_area(win, ap.gap_m) if ap.open_amt > 0.01 else 0.0
            style = str(getattr(win, "open_style", "casement") or "casement").lower()
            frame = _LEAK_FRAME * float(win.width) * float(win.height)
            openings.append((ap.key, ap.wall, a + frame, cp, ap.open_amt <= 0.05 and style in _HINGED))
        else:
            leak = (_LEAK_ROOF if ap.wall == "roof" else _LEAK_WALL) * ap.area
            openings.append((None, ap.wall, leak, cp, False))
    if not openings:
        return [], 0.0

    def net(p_in: float) -> float:
        return sum(a * math.copysign(math.sqrt(abs(cp - p_in)), cp - p_in) for _k, _w, a, cp, _s in openings)

    lo = min(o[3] for o in openings)
    hi = max(o[3] for o in openings)
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if net(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    p_in = 0.5 * (lo + hi)
    vents = []
    for key, wall, a, cp, shut_h in openings:
        if key is None:
            continue
        vents.append(Vent(key, wall, a, cp, math.sqrt(abs(cp - p_in)), cp > p_in, shut_h))
    return vents, p_in


def panel_buffet_gain(room, ap: orr.Aperture) -> float:
    """How much a solid panel moves under the same gust load (amplitude)."""
    which = "roof" if ap.wall == "roof" else "wall"
    m = max(0.5, orr.panel_mass(room, which))
    return (_M_REF / m) * math.sqrt(ap.area / 20.0)


def panel_resonance_hz(room, ap: orr.Aperture) -> float:
    """Lowest bending mode, roughly: light sheets low and boomy, heavy walls
    stiff (and quiet anyway)."""
    which = "roof" if ap.wall == "roof" else "wall"
    m = max(0.5, orr.panel_mass(room, which))
    return max(22.0, min(90.0, 28.0 * (m / 4.0) ** 0.25))


class HouseWind:
    """Per-block draft, buffeting and rattle signals at the apertures."""

    SUB = 256

    def __init__(self, sr: int = 48000, seed: int = 5150):
        self.sr = int(sr)
        self.rng = np.random.RandomState(int(seed) & 0x7FFFFFFF)
        self._key = None
        self._vents: List[Vent] = []
        self._panels: List[Tuple[tuple, float, float, float]] = []   # key, gain, f_res, exposure
        self._draft: Dict[tuple, Tuple[_Biquad, _LP1]] = {}
        self._buffet: Dict[tuple, Tuple[_LP1, _LP1, _Biquad, _LP1, _LP1]] = {}
        self._rattle_tail: Dict[tuple, np.ndarray] = {}
        self._q_lp = _LP1()
        self._q_prev: Optional[float] = None
        self.p_in = 0.0

    def reset(self, seed: Optional[int] = None) -> None:
        self.__init__(self.sr, int(seed) if seed is not None else int(self.rng.randint(1, 2**31 - 1)))

    def keys(self, room, aps: Sequence[orr.Aperture]) -> List[tuple]:
        """Every aperture key this can sound at (a stable set for routing)."""
        return [ap.key for ap in aps]

    def _plan(self, room, aps: Sequence[orr.Aperture], wind_from: float, fp) -> None:
        key = (fp, round(wind_from / 3.0))
        if key == self._key:
            return
        self._key = key
        self._vents, self.p_in = ventilation(room, wind_from, aps)
        panels = []
        for ap in aps:
            if ap.key[0] == "win":
                continue
            c = _face_cos(ap.wall, wind_from)
            # Gust load: windward pressure, roof and side suction both flex it
            expo = 1.0 if ap.wall == "roof" else 0.55 + 0.45 * max(0.0, c)
            panels.append((ap.key, panel_buffet_gain(room, ap), panel_resonance_hz(room, ap), expo))
        self._panels = panels

    def render(self, room, aps: Sequence[orr.Aperture], fp, speed01: np.ndarray, mean01: float,
               wind_from: float) -> Dict[tuple, np.ndarray]:
        n = len(speed01)
        out: Dict[tuple, np.ndarray] = {}
        if n == 0:
            return out
        self._plan(room, aps, wind_from, fp)
        u = wind_ms(speed01)
        if float(np.max(u)) < 0.5:
            return out
        sr = self.sr
        u_blk = float(np.mean(u))
        # --- drafts
        for v in self._vents:
            vel = v.speed * u                       # m/s through the opening
            if v.area <= 5e-3 or float(np.max(vel)) < 0.3:
                continue
            st = self._draft.get(v.key)
            if st is None:
                st = self._draft[v.key] = (_Biquad(), _LP1())
            bq, lp = st
            vb = float(np.mean(vel))
            fc = max(200.0, min(3000.0, 700.0 * (max(0.5, vb) / 8.0) ** 0.7))
            # Band-pass of peak 1 at Q passes ~sqrt(pi fc / (Q sr)) of white noise
            q = 0.8
            g_n = 1.0 / math.sqrt(math.pi * fc / (q * sr))
            y = bq(self.rng.randn(n), fc, q, sr) * g_n
            y = lp(y, fc * 2.5, sr) / max(1e-6, _lp1_gain(fc * 2.5, sr)) * 0.7 + 0.3 * y
            # Jet noise into the room; air leaving radiates mostly outside
            amp = DRAFT_K * (vel / 10.0) ** 3 * math.sqrt(v.area / 0.1) * (1.0 if v.inflow else 0.4)
            out[v.key] = y * amp
        # --- buffeting: gust load (q, and how fast it rises) through each panel
        qn = (u / 10.0) ** 2
        if self._q_prev is None:
            # Start from where the wind is (no thump at Play)
            self._q_lp.zi[:] = qn[0]
            self._q_prev = float(qn[0])
        # A gust coming on loads the panels harder than a steady blow: rise
        # rate of the smoothed load, over about a third of a second
        qs = self._q_lp(qn, 3.0, sr)
        dq = np.diff(np.concatenate(([self._q_prev], qs))) * sr
        self._q_prev = float(qs[-1])
        load = qn + 0.35 * np.maximum(0.0, dq)
        for key, g, f_res, expo in self._panels:
            if g * expo < 1e-3:
                continue
            st = self._buffet.get(key)
            if st is None:
                st = self._buffet[key] = (_LP1(), _LP1(), _Biquad(), _LP1(), _LP1())
            lp1, hp1, bq, lo1, lo2 = st
            w = self.rng.randn(n)
            body = lp1(w, f_res * 1.6, sr)
            body = body - hp1(body, 16.0, sr)
            ring = bq(w, f_res, 2.2, sr) / math.sqrt(math.pi * f_res / (2.2 * sr))
            y = body / max(1e-6, _lp1_gain(f_res * 1.6, sr)) + 0.6 * ring
            # A flexing panel only moves air low down: nothing above a few hundred Hz
            y = lo2(lo1(y, f_res * 2.5, sr), f_res * 2.5, sr)
            out[key] = out.get(key, 0.0) + y * (BUFFET_K * g * expo) * load
        # --- rattles: shut hinged sashes knock when strong gusts load the face
        on = max(0.0, min(1.0, (u_blk - 11.0) / 8.0))
        for v in self._vents:
            if not v.shut_hinged:
                tail = self._rattle_tail.pop(v.key, None)
                continue
            tail = self._rattle_tail.get(v.key)
            y = np.zeros(n + 512)
            if tail is not None:
                y[: len(tail)] += tail
            if on > 0.0:
                c = abs(v.cp)
                lam = 14.0 * on * on * c * n / sr * float(np.mean(load)) / max(1e-3, (u_blk / 10.0) ** 2)
                k = int(self.rng.poisson(lam))
                if k:
                    h = (hash(v.key) & 0xFFFF) / 65535.0
                    f0 = 260.0 + 380.0 * h
                    t = np.arange(512) / sr
                    for p in self.rng.randint(0, n, k):
                        f = f0 * math.exp(self.rng.normal(0.0, 0.12))
                        click = np.sin(2 * math.pi * f * t) * np.exp(-t / (0.004 + 0.003 * self.rng.rand()))
                        y[p: p + 512] += click * (RATTLE_K * (u_blk / 15.0) ** 2 * math.exp(self.rng.normal(0.0, 0.4)))
            rest = y[n:]
            if float(np.max(np.abs(rest))) > 1e-7:
                self._rattle_tail[v.key] = rest
            else:
                self._rattle_tail.pop(v.key, None)
            if float(np.max(np.abs(y[:n]))) > 0.0:
                out[v.key] = out.get(v.key, 0.0) + y[:n]
        return out


__all__ = ["HouseWind", "Vent", "ventilation", "wall_cp", "open_area", "WIND_MAX_MS"]
