"""
House → rain: one model of where rain lands and how it gets inside.

Everything the designer sets turns into sound through this module, and the
3D view spawns its rain from the same numbers, so what you see is what you
hear:

  where it lands   rain rate × area of each surface. The roof catches
                   vertical rain; walls and windows only what the wind
                   tilts into them; sills and the ground ring around the
                   house catch it flat.
  what it sounds   the bank grain of each surface's material, scaled by how
  like             loudly that material rings under a drop (`radiate`).
  how much gets    the construction: a drop *on* the roof or a wall shakes
  inside           the panel (`impact`, with its muffling `lp_hz`); rain on
                   the ground outside comes through open windows (portal
                   model in spatial.py) and through closed walls/glass
                   (`airborne`).

Numbers are relative and anchored on the original tin roof (impact 1.0), so
that house keeps its sound and other designs differ by physics, not by
hand-tuned layer gains.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

# ----- materials -----


@dataclass(frozen=True)
class MaterialAcoustics:
    surface: str      # drop-bank surface family
    radiate: float    # loudness of a drop on it (tin = 1.3, the old metal shaping gain)
    impact: float     # panel → room for drops landing on it (tin roof = 1.0)
    airborne: float   # outdoor sound → room through it when closed
    lp_hz: float      # indoor muffling of its panel path


MATERIALS: Dict[str, MaterialAcoustics] = {
    # Roofs. An uninsulated tin sheet *is* the ceiling; tile and shingle sit
    # on a deck over an attic (far quieter, much darker).
    "tin": MaterialAcoustics("metal", 1.30, 1.00, 0.050, 5600.0),
    "metal": MaterialAcoustics("metal", 1.30, 0.70, 0.035, 4800.0),
    "tile": MaterialAcoustics("tile", 0.40, 0.22, 0.012, 1800.0),
    "shingle": MaterialAcoustics("shingle", 0.28, 0.15, 0.010, 1450.0),
    # Walls / glazing
    "glass": MaterialAcoustics("glass", 0.45, 0.55, 0.060, 3800.0),
    "wood": MaterialAcoustics("wood", 0.32, 0.25, 0.020, 1300.0),
    "brick": MaterialAcoustics("brick", 0.28, 0.06, 0.004, 520.0),
    # Ground around the house: wet soil / puddles
    "water": MaterialAcoustics("water", 0.32, 0.0, 0.0, 0.0),
}

# The numbers above are for each material's usual build; these are those
# thicknesses (m). A thicker wall or roof is heavier, stiffer and damped
# more: what gets through falls ~9 dB per doubling (mass law plus the
# losses of a deeper build) and loses its top end, so a very thick one
# mutes the rain. Glazing is the pane itself.
TYPICAL_THICKNESS_M = {
    "tin": 0.01, "metal": 0.01, "tile": 0.12, "shingle": 0.10,
    "glass": 0.012, "wood": 0.12, "brick": 0.23, "water": 0.01,
}
# Roofs: the sheet / deck you hear from inside
TYPICAL_ROOF_M = {
    "tin": 0.005, "metal": 0.008, "tile": 0.15, "shingle": 0.15,
    "glass": 0.012, "wood": 0.12, "brick": 0.23, "water": 0.01,
}
THICKNESS_MIN_M, THICKNESS_MAX_M = 0.002, 1.50
THICKNESS_EXP = 1.5

# Ground ring catchment (matches the 3D view's eaves ring)
RING_IN_M = 0.12
RING_OUT_M = 0.85
SILL_DEPTH_M = 0.10
# Wind-driven rain on facades: drop tilt tanθ = U / v_fall
WIND_MAX_MS = 14.0
V_FALL_MS = 6.5
# Eddies / splash-back still wet a lee wall a little
FACADE_TAN_MIN = 0.04

_WALL_NORMALS = {"north": (0.0, 1.0), "south": (0.0, -1.0), "east": (1.0, 0.0), "west": (-1.0, 0.0)}


def material_key(name: Optional[str], default: str = "brick") -> str:
    low = str(name or "").lower()
    for key in ("glass", "window", "tin", "metal", "tile", "shingle", "asphalt", "wood", "brick", "water", "puddle"):
        if key in low:
            return {"window": "glass", "asphalt": "shingle", "puddle": "water"}.get(key, key)
    if "roof" in low:
        return "metal"
    return default


def material(name: Optional[str], default: str = "brick") -> MaterialAcoustics:
    return MATERIALS[material_key(name, default)]


def roof_material(room) -> MaterialAcoustics:
    """The roof's acoustics at the room's roof thickness."""
    return _thicker(material(getattr(room, "roof_material", None), "metal"), roof_mass_k(room))


def typical_wall_thickness(room) -> float:
    return TYPICAL_THICKNESS_M[material_key(getattr(room, "wall_material", None), "brick")]


def typical_roof_thickness(room) -> float:
    return TYPICAL_ROOF_M[material_key(getattr(room, "roof_material", None), "metal")]


def _thickness(room, attr: str, typical: float) -> float:
    t = float(getattr(room, attr, 0.0) or 0.0)
    if t <= 0.0:
        return typical
    return max(THICKNESS_MIN_M, min(THICKNESS_MAX_M, t))


def wall_thickness(room) -> float:
    """Wall thickness in m (0 / unset on the room = usual for its material)."""
    return _thickness(room, "wall_thickness", typical_wall_thickness(room))


def roof_thickness(room) -> float:
    """Roof thickness in m (0 / unset = usual for its material)."""
    return _thickness(room, "roof_thickness", typical_roof_thickness(room))


def thickness_k(typical: float, t: float) -> float:
    """Amplitude through a build ``t`` thick relative to its usual one."""
    return max(1e-4, min(4.0, (typical / max(1e-6, t)) ** THICKNESS_EXP))


def wall_mass_k(room) -> float:
    return thickness_k(typical_wall_thickness(room), wall_thickness(room))


def roof_mass_k(room) -> float:
    return thickness_k(typical_roof_thickness(room), roof_thickness(room))


def glazing_k(room) -> float:
    """Closed windows are fitted to the wall: a thick wall gets heavy
    glazing (half the wall's change, in dB)."""
    return math.sqrt(wall_mass_k(room))


def _thicker(m: MaterialAcoustics, k: float) -> MaterialAcoustics:
    if abs(k - 1.0) < 1e-9:
        return m
    # Less gets through, and what does loses its top end first
    return replace(m, impact=m.impact * k, airborne=m.airborne * k,
                   lp_hz=max(60.0, m.lp_hz * min(1.0, k) ** 0.5))


def wall_material(room) -> MaterialAcoustics:
    """The walls' acoustics at the room's wall thickness."""
    return _thicker(material(getattr(room, "wall_material", None), "brick"), wall_mass_k(room))


def glass_material(room) -> MaterialAcoustics:
    """A closed window pane, glazed to suit the wall."""
    return _thicker(MATERIALS["glass"], glazing_k(room))


def reveal_fraction(depth: float, width: float, height: float, tan_lat: float, tan_vert: float) -> float:
    """Share of an opening ``width`` × ``height`` set ``depth`` deep in the
    wall that's still in view from a direction tilted tan_lat / tan_vert off
    its axis (the reveal hides the rest)."""
    if depth <= 0.0:
        return 1.0
    fl = max(0.0, 1.0 - depth * abs(tan_lat) / max(0.05, width))
    fv = max(0.0, 1.0 - depth * abs(tan_vert) / max(0.05, height))
    return fl * fv


def surface_radiate(surface: str) -> float:
    for m in MATERIALS.values():
        if m.surface == surface:
            return m.radiate
    return 0.30


# ----- geometry -----


def wall_length(room, wall: str) -> float:
    return float(room.width) if wall in ("north", "south") else float(room.depth)


def wall_point(room, wall: str, u: float, y: float, out: float = 0.0) -> Tuple[float, float, float]:
    """Point on ``wall`` at along-wall offset ``u`` (N/S along x, E/W along z),
    ``out`` metres outside it."""
    w, d = float(room.width), float(room.depth)
    if wall == "north":
        return u, y, d + out
    if wall == "south":
        return u, y, -out
    if wall == "east":
        return w + out, y, u
    return -out, y, u


def _windows(room) -> List:
    return [w for w in (getattr(room, "windows", []) or []) if not getattr(w, "free_place", False)]


def window_span(win) -> Tuple[float, float, float, float]:
    """(lo, hi, sill, top) along its wall."""
    lo = float(getattr(win, "offset", 0.0) or 0.0)
    sill = float(getattr(win, "sill", 0.9) or 0.0)
    return lo, lo + float(getattr(win, "width", 1.0) or 1.0), sill, sill + float(getattr(win, "height", 1.2) or 1.2)


def wall_contact(room, src: Sequence[float], reach: float = 1.2) -> Optional[Tuple[str, float, float, Optional[object]]]:
    """Nearest wall a point outside the house faces: (wall, metres out,
    along-wall offset, window covering that spot or None). None when the
    point is inside the footprint, above the eaves, or farther than ``reach``."""
    x, y, z = float(src[0]), float(src[1]), float(src[2])
    w, d, h = float(room.width), float(room.depth), float(room.height)
    if y > h + 0.05:
        return None
    best = None
    for wall, out, u, length in (
        ("north", z - d, x, w), ("south", -z, x, w), ("east", x - w, z, d), ("west", -x, z, d),
    ):
        if out < -0.01 or out > reach:
            continue
        uc = max(0.0, min(length, u))
        over = abs(u - uc)  # beyond the corner
        dist = math.hypot(max(0.0, out), over)
        if best is None or dist < best[1]:
            best = (wall, dist, uc)
    if best is None:
        return None
    wall, dist, uc = best
    hit_win = None
    for win in _windows(room):
        if str(getattr(win, "wall", "")).lower() != wall:
            continue
        lo, hi, sill, top = window_span(win)
        if lo <= uc <= hi and sill - 0.05 <= y <= top + 0.05:
            hit_win = win
            break
    return wall, dist, uc, hit_win


@dataclass
class Patch:
    """One catchment: where drops land, what they hit, how many."""
    layer: str          # roof / wall / window / sill / ground
    kind: int           # 3D-view hit kind: 3 roof, 4 wall, 2 window, 5 sill, 1 ground
    surface: str        # bank surface
    rate_area: float    # m² of horizontal-equivalent catchment
    wall: str = ""
    lo: float = 0.0     # along-wall span / heights for sampling
    hi: float = 0.0
    y0: float = 0.0
    y1: float = 0.0
    window: Optional[object] = None


def facade_tilt(room, wall: str, wind_speed: float, wind_dir_deg: float) -> float:
    """tanθ of rain reaching this wall (vertical rain → FACADE_TAN_MIN)."""
    from app.models.room import Room

    wx, wz = Room.wind_push_xz(wind_speed, wind_dir_deg)
    nx, nz = _WALL_NORMALS[wall]
    toward = max(0.0, -(wx * nx + wz * nz))  # push into the wall from outside
    return FACADE_TAN_MIN + toward * WIND_MAX_MS / V_FALL_MS


def catchments(room, wind_speed: float = 0.0, wind_dir_deg: float = 90.0) -> List[Patch]:
    """Every surface rain can land on, with its catchment area."""
    w, d, h = float(room.width), float(room.depth), float(room.height)
    roof_m = roof_material(room)
    wall_m = wall_material(room)
    patches = [Patch("roof", 3, roof_m.surface, w * d)]
    for wall in ("north", "south", "east", "west"):
        length = wall_length(room, wall)
        tilt = facade_tilt(room, wall, wind_speed, wind_dir_deg)
        glass_area = 0.0
        for win in _windows(room):
            if str(getattr(win, "wall", "")).lower() != wall:
                continue
            lo, hi, sill, top = window_span(win)
            top = min(top, h)
            area = max(0.0, (hi - lo) * (top - sill))
            glass_area += area
            patches.append(Patch("window", 2, "glass", area * tilt, wall, lo, hi, sill, top, win))
            patches.append(Patch("sill", 5, "metal", (hi - lo) * SILL_DEPTH_M, wall, lo, hi, sill, sill, win))
        wall_area = max(0.0, length * h - glass_area)
        patches.append(Patch("wall", 4, wall_m.surface, wall_area * tilt, wall, 0.0, length, 0.15, h * 0.95))
    ring = 2.0 * RING_OUT_M * (w + d) + 4.0 * RING_OUT_M ** 2
    ring -= 2.0 * RING_IN_M * (w + d) + 4.0 * RING_IN_M ** 2
    patches.append(Patch("ground", 1, "water", ring))
    return [p for p in patches if p.rate_area > 0.0]


def catchment_area(patches: Sequence[Patch]) -> float:
    return float(sum(p.rate_area for p in patches))


def layer_shares(patches: Sequence[Patch]) -> Dict[str, float]:
    tot = catchment_area(patches) or 1.0
    out: Dict[str, float] = {}
    for p in patches:
        out[p.layer] = out.get(p.layer, 0.0) + p.rate_area / tot
    return out


def kind_shares(patches: Sequence[Patch]) -> Dict[int, float]:
    tot = catchment_area(patches) or 1.0
    out: Dict[int, float] = {}
    for p in patches:
        out[p.kind] = out.get(p.kind, 0.0) + p.rate_area / tot
    return out


def sample_point(room, patch: Patch, rng) -> Tuple[float, float, float]:
    """A landing spot on ``patch`` (room coordinates)."""
    w, d, h = float(room.width), float(room.depth), float(room.height)
    if patch.layer == "roof":
        return float(rng.uniform(0.05, max(0.15, w - 0.05))), h + 0.03, float(rng.uniform(0.05, max(0.15, d - 0.05)))
    if patch.layer in ("wall", "window"):
        for _ in range(8):
            u = float(rng.uniform(patch.lo, patch.hi))
            y = float(rng.uniform(patch.y0, patch.y1))
            if patch.layer == "window" or not _on_window(room, patch.wall, u, y):
                break
        return wall_point(room, patch.wall, u, y, out=0.02)
    if patch.layer == "sill":
        u = float(rng.uniform(patch.lo, patch.hi))
        return wall_point(room, patch.wall, u, patch.y0 + 0.01, out=SILL_DEPTH_M * 0.6)
    # ground ring, uniform by perimeter length at each depth
    out = float(rng.uniform(RING_IN_M, RING_OUT_M))
    pw, pd = w + 2.0 * out, d + 2.0 * out
    t = float(rng.uniform(0.0, 2.0 * (pw + pd)))
    if t < pw:
        return t - out, 0.0, d + out
    t -= pw
    if t < pd:
        return w + out, 0.0, d + out - t
    t -= pd
    if t < pw:
        return w + out - t, 0.0, -out
    t -= pw
    return -out, 0.0, t - out


def _on_window(room, wall: str, u: float, y: float) -> bool:
    for win in _windows(room):
        if str(getattr(win, "wall", "")).lower() == wall:
            lo, hi, sill, top = window_span(win)
            if lo <= u <= hi and sill <= y <= top:
                return True
    return False


def pick_patch(patches: Sequence[Patch], rng, kind: Optional[int] = None) -> Optional[Patch]:
    pool = [p for p in patches if kind is None or p.kind == kind]
    tot = sum(p.rate_area for p in pool)
    if tot <= 0.0:
        return None
    u = float(rng.uniform(0.0, tot))
    for p in pool:
        u -= p.rate_area
        if u <= 0.0:
            return p
    return pool[-1]


__all__ = [
    "MATERIALS", "MaterialAcoustics", "Patch", "catchments", "catchment_area", "facade_tilt",
    "kind_shares", "layer_shares", "material", "material_key", "pick_patch", "roof_material",
    "glass_material", "glazing_k", "reveal_fraction", "roof_mass_k", "roof_thickness", "sample_point",
    "surface_radiate", "thickness_k", "typical_roof_thickness", "typical_wall_thickness", "wall_contact",
    "wall_mass_k", "wall_material", "wall_point", "wall_thickness", "window_span",
]
