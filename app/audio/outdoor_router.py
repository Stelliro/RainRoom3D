"""
Weather sounds from outside the house (wind, thunder) → its openings → the
speakers, the wall buses and the ears.

The same house model as the rain (yard.py / house_model.py), but for sound
arriving through the air from a direction instead of from rain on the
ground:

  apertures  every way in: each window (open gap or closed pane, its style,
             size and how far it's open), each wall's solid part and the roof,
             with the panel's airborne leak and muffling (material and
             thickness). A one-pole low-pass above a panel's ``lp_hz`` is the
             mass law's −6 dB/octave, so a closed brick house still lets the
             low rumble of thunder in while the crack only comes through a
             window.
  facing     sound from a direction lands hardest on the walls that face it;
             walls turned away are in its shadow: quieter and much darker
             (only the lows bend round a house). ``diffuse`` blends toward
             sound coming from all around (wind in the trees).
  arrival    a plane wave reaches the near wall a few ms before the far one.
  inside     radiated from each aperture to each speaker and to the ears with
             the yard's near-field law, ILD, ITD and head shadow.

A source can also start AT an aperture (the whistle of a window open a
crack): ``local`` signals skip the outside part.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.audio import house_model as hm
from app.audio.spatial import azimuth_elevation
from app.audio.yard import (
    C_SOUND,
    HEAD_R,
    SETTLE_S,
    YardRouter,
    _Delay,
    _OnePole,
    _profile,
    indoor_gain,
)

_WALL_N = {"north": (0.0, 1.0), "south": (0.0, -1.0), "east": (1.0, 0.0), "west": (-1.0, 0.0)}
# Facing: exposed walls get the pressure doubling of a reflecting face; the
# shadow side keeps this much of the level and only its lows
_SHADOW_FLOOR = 0.30
_SHADOW_HZ = 700.0
_OPEN_HZ = 16000.0
_BUS_K = 0.42
_LP_MIN_HZ = 20.0  # yard._OnePole's floor

# Sound arriving through the AIR (not a drop hitting the panel) is held back
# by the panel's weight: the field-incidence mass law, TL = 20·log10(m·f) − 47
# dB, i.e. a one-pole low-pass at f0 = 224 / m Hz (m in kg/m²). A thin tin
# roof (≈4 kg/m², f0 ≈ 57 Hz) lets the rumble of thunder through almost whole
# and stops the crack; 23 cm of brick (≈410 kg/m²) keeps nearly all of it out.
# Weight of each material's usual build (kg/m²), scaled by the thickness you
# set against that usual build (house_model.TYPICAL_*): a corrugated tin
# sheet ≈ 4, a 6 mm pane 15, a framed wood wall 30, tiles on a deck 50,
# 23 cm of brick 410.
_MASS_USUAL = {"tin": 4.0, "metal": 6.0, "glass": 30.0, "brick": 410.0,
               "wood": 30.0, "tile": 50.0, "shingle": 20.0, "water": 10.0}
_PANE_KG_M2 = 15.0  # a shut window (heavier glazing in thick walls)


def mass_law_hz(mass_kg_m2: float) -> float:
    return max(0.3, 224.0 / max(0.05, float(mass_kg_m2)))


def panel_mass(room, which: str) -> float:
    if which == "roof":
        key = hm.material_key(getattr(room, "roof_material", None), "metal")
        return _MASS_USUAL.get(key, 20.0) * hm.roof_thickness(room) / max(1e-6, hm.typical_roof_thickness(room))
    key = hm.material_key(getattr(room, "wall_material", None), "brick")
    return _MASS_USUAL.get(key, 100.0) * hm.wall_thickness(room) / max(1e-6, hm.typical_wall_thickness(room))


@dataclass
class Aperture:
    key: tuple
    wall: str                      # north / south / east / west / roof
    trans: float                   # outside → inside amplitude (1.2 m² open window ≈ 1)
    lp_hz: float                   # gap / panel muffling
    point: Tuple[float, float, float]
    area: float                    # m² it radiates from inside
    rect: Optional[Tuple[float, float, float, float]] = None   # panel: u0, u1, y0, y1
    open_amt: float = 0.0          # windows: 0 shut .. 1 wide open
    gap_m: float = 0.0             # windows: rough width of the open gap (whistles)
    width: float = 0.0


def _window_gap(win, open_amt: float, angle_deg: float) -> float:
    """Rough width (m) of the slot air squeezes through."""
    style = str(getattr(win, "open_style", "casement") or "casement").lower()
    if open_amt <= 0.0:
        return 0.0
    if style in ("slider", "sash", "double_hung", "single_hung"):
        return open_amt * float(getattr(win, "width", 1.0)) * 0.5
    # Hinged sash: the gap at its free edge
    reach = float(getattr(win, "height", 1.2) if style in ("awning", "hopper", "tilt_turn") else getattr(win, "width", 1.0))
    return 2.0 * reach * math.sin(math.radians(max(0.0, angle_deg)) * 0.5)


def apertures(room, roof: bool = True) -> List[Aperture]:
    """Every way outside sound gets in, with its transmission."""
    w, d, h = float(room.width), float(room.depth), float(room.height)
    out: List[Aperture] = []
    glass = {wall: 0.0 for wall in _WALL_N}
    for i, win in enumerate(getattr(room, "windows", []) or []):
        wall = str(getattr(win, "wall", "north") or "north").lower()
        try:
            cx, cy, cz = room.window_center(win)
        except Exception:
            continue
        prof = _profile(win, hm.glazing_k(room))
        o = max(0.0, min(1.0, float(getattr(win, "open", 0.0) or 0.0)))
        try:
            ang = float(win.open_angle_deg())
        except Exception:
            ang = o * float(getattr(win, "max_angle_deg", 75.0) or 75.0)
        if not getattr(win, "free_place", False):
            glass[wall] = glass.get(wall, 0.0) + float(win.width) * float(win.height)
        area_w = float(win.width) * float(win.height)
        if o <= 0.01:
            # Shut: only through the pane, by its mass
            pane = _PANE_KG_M2 * max(1.0, hm.glazing_k(room) ** -1.0)
            trans, lp = math.sqrt(area_w / 1.2), mass_law_hz(pane)
            gap = area_w
        else:
            trans, lp = float(prof["gain"]), min(9000.0, float(prof["lp_fc"]))
            gap = area_w * max(0.05, prof["gain"] / max(1e-6, prof["area_k"]))
        out.append(Aperture(("win", i), wall, trans, lp, (cx, cy, cz), gap, None, o,
                            _window_gap(win, o, ang), float(win.width)))
    wall_hz = mass_law_hz(panel_mass(room, "wall"))
    for wall in _WALL_N:
        length = hm.wall_length(room, wall)
        area = max(0.0, length * h - glass[wall])
        if area <= 0.0:
            continue
        mid = hm.wall_point(room, wall, 0.5 * length, h * 0.5)
        out.append(Aperture(("wall", wall), wall, math.sqrt(area / 1.2), wall_hz, mid, area,
                            (0.0, length, 0.0, h)))
    if roof:
        area = w * d
        roof_hz = mass_law_hz(panel_mass(room, "roof"))
        out.append(Aperture(("roof",), "roof", math.sqrt(area / 1.2), roof_hz,
                            (w * 0.5, h, d * 0.5), area, (0.0, w, 0.0, d)))
    return out


def _radiate(ap: Aperture, room, recv: Tuple[float, float, float]) -> Tuple[float, Tuple[float, float, float]]:
    """(indoor distance, the point it's heard from); a panel from its nearest part."""
    x, y, z = recv
    if ap.rect is None:
        p = ap.point
    elif ap.wall == "roof":
        u0, u1, v0, v1 = ap.rect
        p = (max(u0, min(u1, x)), float(room.height), max(v0, min(v1, z)))
    else:
        u0, u1, y0, y1 = ap.rect
        u = x if ap.wall in ("north", "south") else z
        p = hm.wall_point(room, ap.wall, max(u0, min(u1, u)), max(y0, min(y1, y)))
    dd = math.sqrt(math.dist(recv, p) ** 2 + ap.area / (2.0 * math.pi))
    return max(0.12, dd), p


def facing(ap: Aperture, az_deg: Optional[float], el_deg: float, diffuse: float) -> Tuple[float, float]:
    """(gain, low-pass Hz) for sound from compass bearing ``az_deg`` (where
    it comes FROM; 0 = north, 90 = east) at elevation ``el_deg``."""
    diffuse = max(0.0, min(1.0, float(diffuse)))
    if az_deg is None:
        c = 0.0
        diffuse = 1.0
    else:
        a, e = math.radians(az_deg), math.radians(el_deg)
        ux, uz, uy = math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)
        if ap.wall == "roof":
            c = uy
        else:
            nx, nz = _WALL_N[ap.wall]
            c = ux * nx + uz * nz
    lit = max(0.0, c)
    g_dir = (_SHADOW_FLOOR + (1.0 - _SHADOW_FLOOR) * lit) * (1.0 + 0.4 * lit)
    # Diffuse sound: every face gets the average exposure
    g_dif = 0.75 if ap.wall != "roof" else 0.9
    g = diffuse * g_dif + (1.0 - diffuse) * g_dir
    s = 0.5 + 0.5 * c  # 0 = straight behind the face, 1 = square on
    fc_dir = _SHADOW_HZ * (_OPEN_HZ / _SHADOW_HZ) ** (s ** 1.5)
    fc = math.exp(diffuse * math.log(_OPEN_HZ) + (1.0 - diffuse) * math.log(fc_dir))
    return g, fc


@dataclass
class _Route:
    ap: Aperture
    stem: int
    out_g: float
    fc: float
    lag: int                       # plane-wave arrival offset (samples)
    spk: Dict[int, float]
    ears: Tuple[float, float, int, int, float]
    local: bool = False


_AP_CACHE: Dict[tuple, List[Aperture]] = {}


def _cached_apertures(room) -> Tuple[tuple, List[Aperture]]:
    fp = (YardRouter.fingerprint(room), str(getattr(room, "roof_material", "")), round(hm.roof_thickness(room), 4))
    aps = _AP_CACHE.get(fp)
    if aps is None:
        if len(_AP_CACHE) > 16:
            _AP_CACHE.clear()
        aps = _AP_CACHE[fp] = apertures(room)
    return fp, aps


class OutdoorRouter:
    """One outside sound (mono or a few stems) → apertures → mix / ears."""

    def __init__(self, sr: int, roof: bool = True, max_lag_s: float = 0.12):
        self.sr = int(sr)
        self.roof = bool(roof)
        self._fp = None
        self._pending = None
        self._dir_key = None
        self._routes: List[_Route] = []
        self._prev: Dict[tuple, Dict[object, float]] = {}
        self._lp: Dict[tuple, _OnePole] = {}
        self._dl: Dict[tuple, _Delay] = {}
        self._max_lag = int(max_lag_s * self.sr)

    def reset(self) -> None:
        self.__init__(self.sr, self.roof, self._max_lag / self.sr)

    def _build(self, room, aps: Sequence[Aperture], az: Optional[float], el: float, diffuse: float,
               n_stems: int, hp_gain: float, local_keys: Sequence[tuple], local_only: bool = False) -> None:
        L = room.listener
        lpos = (float(L.x), float(getattr(L, "y", 1.2)), float(L.z))
        yaw = float(getattr(L, "yaw", 0.0))
        # Plane wave: earlier at apertures nearer the source
        if az is not None:
            a, e = math.radians(az), math.radians(el)
            u = (math.sin(a) * math.cos(e), math.sin(e), math.cos(a) * math.cos(e))
        else:
            u = None
        proj = []
        for ap in aps:
            proj.append(0.0 if u is None else (ap.point[0] * u[0] + ap.point[1] * u[1] + ap.point[2] * u[2]))
        p_max = max(proj) if proj else 0.0
        routes = []
        local = set(local_keys)
        for j, ap in enumerate(aps):
            if not self.roof and ap.wall == "roof":
                continue
            is_local = ap.key in local
            if local_only and not is_local:
                continue
            if is_local:
                g_out, fc = 1.0, _OPEN_HZ
            else:
                g_f, fc = facing(ap, az, el, diffuse)
                g_out = ap.trans * g_f
                fc = min(fc, ap.lp_hz) if ap.lp_hz > 0 else fc
                if fc < _LP_MIN_HZ:
                    # The filter stops at 20 Hz; a heavier panel's corner sits
                    # lower, and above it the level is f0/f: same slope, less level
                    g_out *= fc / _LP_MIN_HZ
                    fc = _LP_MIN_HZ
            if g_out <= 1e-6:
                continue
            lag = int(round((p_max - proj[j]) / C_SOUND * self.sr)) if u is not None else 0
            spk = {}
            for i, s in enumerate(room.speakers):
                if not getattr(s, "enabled", True):
                    continue
                dd, _p = _radiate(ap, room, (float(s.x), float(s.y), float(s.z)))
                spk[i] = g_out * indoor_gain(dd) * 10.0 ** (float(getattr(s, "gain_db", 0.0) or 0.0) / 20.0)
            dd, p = _radiate(ap, room, lpos)
            az_e, el_e = azimuth_elevation(lpos, p, yaw=yaw)
            g = g_out * indoor_gain(dd) * hp_gain
            ild = 12.0 * math.sin(az_e) * (0.75 + 0.25 * math.cos(el_e)) * (0.82 if math.cos(az_e) < 0 else 1.0)
            gl, gr = 10.0 ** (-ild / 40.0), 10.0 ** (ild / 40.0)
            norm = math.sqrt(2.0) / math.sqrt(gl * gl + gr * gr)
            az_c = max(-math.pi / 2, min(math.pi / 2, math.atan2(math.sin(az_e), abs(math.cos(az_e)))))
            itd = int(round(abs(HEAD_R / C_SOUND * (math.sin(az_c) + az_c)) * self.sr))
            dl, dr = (itd, 0) if az_e > 0 else (0, itd)
            shadow = 16000.0 / (1.0 + 4.0 * abs(math.sin(az_e)))
            routes.append(_Route(ap, j % max(1, n_stems), g_out, fc, min(lag, self._max_lag), spk,
                                 (g * gl * norm, g * gr * norm, dl, dr, shadow), is_local))
        self._routes = routes

    def process(self, room, x: np.ndarray, mix: Dict[int, np.ndarray], ears: Optional[np.ndarray],
                wall_bus: Dict[str, int], hp_gain: float, az: Optional[float] = None, el: float = 0.0,
                diffuse: float = 0.0, now: Optional[float] = None,
                local: Optional[Dict[tuple, np.ndarray]] = None, local_only: bool = False) -> None:
        """Add ``x`` (frames, or n_stems × frames) heard from bearing ``az``.

        ``local`` maps aperture keys to signals made AT that aperture (they are
        only radiated inside). Room changes re-route after SETTLE_S of audio
        time like the yard; a new direction glides in over one block."""
        stems = np.atleast_2d(np.asarray(x, dtype=np.float64))
        n_st, frames = stems.shape
        local = local or {}
        ap_fp, aps = _cached_apertures(room)
        fp = (ap_fp, n_st, round(hp_gain, 4), tuple(sorted(local)), bool(local_only))
        dir_key = (None if az is None else round(float(az) % 360.0, 0), round(float(el), 0), round(float(diffuse), 2))
        if fp != self._fp:
            ready = self._fp is None or now is None
            if not ready:
                if self._pending is None or self._pending[0] != fp:
                    self._pending = (fp, now)
                ready = now - self._pending[1] >= SETTLE_S
            if ready:
                self._pending = None
                self._fp = fp
                self._dir_key = None
        if self._dir_key != dir_key and self._fp is not None:
            self._dir_key = dir_key
            self._build(room, aps, az, el, diffuse, n_st, hp_gain, list(local), local_only)
        ramp = np.linspace(0.0, 1.0, frames, endpoint=False) if frames > 1 else np.zeros(frames)
        seen = set()
        for r in self._routes:
            key = r.ap.key
            if r.local:
                src = local.get(key)
                if src is None:
                    continue
                sig = np.asarray(src, dtype=np.float64)
            else:
                sig = stems[r.stem]
            seen.add(key)
            lp = self._lp.setdefault(key, _OnePole())
            xs = lp(sig, r.fc, self.sr)
            if r.lag > 0:
                xs = self._dl.setdefault(key + ("lag",), _Delay(self._max_lag + 1))(xs, r.lag)
            gl, gr, dl, dr, shadow = r.ears
            want: Dict[object, float] = dict(r.spk)
            want["L"], want["R"] = gl, gr
            want["bus"] = r.out_g * _BUS_K
            prev = self._prev.get(key, {k: 0.0 for k in want})

            def gain(k):
                g0, g1 = prev.get(k, 0.0), want.get(k, 0.0)
                return g1 if g0 == g1 else g0 + (g1 - g0) * ramp

            for i in r.spk:
                b = xs * gain(i)
                mix[i] = mix[i] + b if i in mix else b
            wid = wall_bus.get(r.ap.wall)
            if wid is not None:
                b = xs * gain("bus")
                mix[wid] = mix[wid] + b if wid in mix else b
            if ears is not None and (gl > 0.0 or gr > 0.0):
                far_l = dl > 0
                sh = self._lp.setdefault(key + ("shadow",), _OnePole())(xs, shadow, self.sr)
                xl = self._dl.setdefault(key + ("L",), _Delay())(sh if far_l else xs, dl)
                xr = self._dl.setdefault(key + ("R",), _Delay())(xs if far_l else sh, dr)
                ears[:, 0] += xl * gain("L")
                ears[:, 1] += xr * gain("R")
            self._prev[key] = want
        for key in [k for k in self._prev if k not in seen]:
            del self._prev[key]


__all__ = ["Aperture", "OutdoorRouter", "apertures", "facing"]
