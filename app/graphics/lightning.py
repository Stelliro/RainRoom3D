"""
Lightning for the 3D views: the bolt's shape, where it sits in the sky and
how much it lights the house.

The canvas turns the storm's flashes (engine time, brightness, bearing,
km, strike seed) into one entry per live strike:

  bolt   0..1  how bright the channel is right now (each return stroke
               re-lights it for ~0.1–0.2 s)
  glow   0..1  the cloud / sky glow, which lingers a little longer

Both views draw the same bolt at the strike's compass bearing (0 = north =
+Z, 90 = east = +X, as Room.wind_push_xz) on a ring ``RING_M`` from the
house. The real cloud base sits ~1.6 km up, so the bolt's top is at the
elevation it would be seen at: near strikes tower over the house, far ones
sit low on the horizon. Past ``SHEET_KM`` rain and cloud hide the channel
and only a glow flickers (sheet lightning).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

RING_M = 60.0          # how far out the views draw the bolt (m)
CLOUD_KM = 1.6         # cloud base height the bolt drops from
SHEET_KM = 8.0         # farther than this: glow only, no channel
HIDE_KM = 4.0          # channel starts fading behind rain from here
# The views look down on the house and show little sky, so a near bolt's
# top is held to 40° up (a real one at 0.5 km climbs to ~70°)
EL_MIN, EL_MAX = math.radians(5.0), math.radians(40.0)

# Channel colour (core) and glow colour, linear 0..1
CORE_RGB = (0.92, 0.95, 1.0)
GLOW_RGB = (0.62, 0.70, 1.0)
LIGHT_RGB = (0.86, 0.90, 1.0)


class Strike:
    """One live strike as the views draw it."""

    __slots__ = ("seed", "az", "km", "bolt", "glow")

    def __init__(self, seed: int, az: float, km: float, bolt: float, glow: float):
        self.seed = int(seed)
        self.az = float(az)
        self.km = float(km)
        self.bolt = float(bolt)
        self.glow = float(glow)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Strike(seed={self.seed}, az={self.az:.0f}, km={self.km:.1f}, bolt={self.bolt:.2f}, glow={self.glow:.2f})"


def _stroke_env(dt: float) -> Tuple[float, float]:
    """(channel, glow) of one return stroke ``dt`` s after it."""
    if dt < 0.0:
        return 0.0, 0.0
    # Channel: full for 40 ms, gone ~0.2 s later
    bolt = 1.0 if dt < 0.04 else math.exp(-(dt - 0.04) / 0.055)
    glow = math.exp(-dt / 0.09) + 0.25 * math.exp(-dt / 0.35)
    return bolt, glow


def active_strikes(flashes: Sequence[tuple], t: float) -> List[Strike]:
    """Group flashes (time, brightness, az, km[, seed]) by strike, at time ``t``."""
    by: Dict[object, list] = {}
    for f in flashes:
        tf, b, az, km = f[0], f[1], f[2], f[3]
        seed = f[4] if len(f) > 4 else hash((round(az, 1), round(km, 2)))
        if tf > t:
            continue
        bolt, glow = _stroke_env(t - tf)
        s = by.get(seed)
        if s is None:
            by[seed] = [az, km, b * bolt, b * glow]
        else:
            s[2] = max(s[2], b * bolt)
            s[3] = min(1.0, s[3] + b * glow * 0.6)
    out = []
    for seed, (az, km, bolt, glow) in by.items():
        if bolt > 0.01 or glow > 0.01:
            out.append(Strike(seed, az, km, min(1.0, bolt), min(1.0, glow)))
    return out


# ---------------- bolt shape ----------------

_BOLTS: Dict[int, list] = {}


def _walk(rng, start: Tuple[float, float], length: float, heading: float, n: int,
          jag: float) -> np.ndarray:
    """A jagged path down from ``start``: ``length`` (in bolt heights) at
    ``heading`` (radians from straight down; + = toward +u)."""
    pts = [start]
    u, v = start
    step = length / max(1, n)
    for _ in range(n):
        # Lightning zig-zags: mostly small kinks, now and then a sharp one
        kink = rng.normal(0.0, 0.42) + (rng.normal(0.0, 0.9) if rng.rand() < 0.12 else 0.0)
        a = heading + kink
        a = max(-1.25, min(1.25, a))
        s = step * (0.6 + 0.8 * rng.rand())
        u += math.sin(a) * s * (1.0 + jag)
        v -= math.cos(a) * s
        pts.append((u, v))
        if v <= 0.0:
            break
    return np.asarray(pts, dtype=np.float64)


def bolt_shape(seed: int) -> List[Tuple[np.ndarray, float, float]]:
    """The strike's channel and branches, built once per seed.

    Returns (points [N, 2], brightness, width) per stroke of the shape, in
    units of the bolt's height: u across (0 = under the cloud spot), v up
    (1 = cloud base, 0 = ground)."""
    key = int(seed) & 0x7FFFFFFF
    shape = _BOLTS.get(key)
    if shape is not None:
        return shape
    rng = np.random.RandomState(key)
    lean = rng.uniform(-0.22, 0.22)
    main = _walk(rng, (0.0, 1.0), 1.15, lean, 46, 0.1)
    # End exactly on the ground
    if main[-1, 1] > 0.0:
        main = np.vstack([main, [main[-1, 0] + rng.normal(0.0, 0.02), 0.0]])
    else:
        main[-1, 1] = 0.0
    shape = [(main, 1.0, 1.0)]
    n_br = rng.randint(4, 9)
    for _ in range(n_br):
        i = rng.randint(2, max(3, int(len(main) * 0.75)))
        side = 1.0 if rng.rand() < 0.5 else -1.0
        br = _walk(rng, tuple(main[i]), rng.uniform(0.12, 0.38), lean + side * rng.uniform(0.45, 0.95),
                   rng.randint(7, 15), 0.25)
        shape.append((br, 0.45, 0.6))
        if rng.rand() < 0.55 and len(br) > 4:
            j = rng.randint(1, len(br) - 2)
            sub = _walk(rng, tuple(br[j]), rng.uniform(0.05, 0.16), lean + side * rng.uniform(0.7, 1.2),
                        rng.randint(4, 8), 0.3)
            shape.append((sub, 0.25, 0.4))
    if len(_BOLTS) > 48:
        _BOLTS.clear()
    _BOLTS[key] = shape
    return shape


# ---------------- placement ----------------

def layout(az_deg: float, km: float, ring: float = RING_M):
    """Where a strike sits in a view's world (house centre at the origin).

    Returns dict: base (x, 0, z) on the ring, ``side`` unit vector across the
    bolt (x, 0, z), ``height`` of the cloud base there (m), ``channel``
    0..1 how much of the channel shows, ``sheet`` True when only a glow."""
    a = math.radians(float(az_deg) % 360.0)
    dx, dz = math.sin(a), math.cos(a)
    el = max(EL_MIN, min(EL_MAX, math.atan2(CLOUD_KM, max(0.05, float(km)))))
    height = ring * math.tan(el)
    if km <= HIDE_KM:
        channel = 1.0
    else:
        channel = max(0.0, 1.0 - (km - HIDE_KM) / (SHEET_KM - HIDE_KM))
    return {
        "base": (dx * ring, 0.0, dz * ring),
        "dir": (dx, 0.0, dz),
        "side": (dz, 0.0, -dx),
        "height": height,
        "channel": channel,
        "sheet": km > SHEET_KM,
        # Near strikes are bigger and brighter; far ones dimmer
        "near": min(1.0, (2.0 / max(0.3, float(km))) ** 0.6),
    }


def world_points(pts: np.ndarray, lay: dict) -> np.ndarray:
    """Bolt points (u, v) → world (x, y, z) for a layout."""
    bx, _by, bz = lay["base"]
    sx, _sy, sz = lay["side"]
    H = lay["height"]
    u = pts[:, 0] * H
    out = np.empty((len(pts), 3), dtype=np.float64)
    out[:, 0] = bx + sx * u
    out[:, 1] = pts[:, 1] * H
    out[:, 2] = bz + sz * u
    return out


def sky_glow(s: Strike, lay: dict) -> Tuple[Tuple[float, float, float], float, float]:
    """Where the sky brightens on the strike's side of the view: (world point
    to centre it on, alpha 0..1, radius as a share of the view width)."""
    bx, _by, bz = lay["base"]
    a = min(1.0, (0.55 * s.glow + 0.35 * s.bolt) * (0.35 + 0.65 * lay["near"]))
    return (bx, lay["height"] * 0.5, bz), a, 0.35 + 0.35 * lay["near"]


# ---------------- lighting the scene ----------------

def scene_light(strikes: Sequence[Strike]) -> Optional[Tuple[float, Tuple[float, float, float]]]:
    """(strength 0..1, unit direction toward the brightest strike) or None."""
    best = None
    total = 0.0
    for s in strikes:
        prox = min(1.0, (1.5 / max(0.3, s.km)) ** 0.7)
        k = (0.65 * s.glow + 0.55 * s.bolt * (1.0 if s.km <= SHEET_KM else 0.0)) * prox
        total += k
        if best is None or k > best[0]:
            best = (k, s)
    if best is None or total < 0.01:
        return None
    s = best[1]
    a = math.radians(s.az % 360.0)
    el = max(EL_MIN, min(EL_MAX, math.atan2(CLOUD_KM, max(0.05, s.km))))
    d = (math.sin(a) * math.cos(el), math.sin(el), math.cos(a) * math.cos(el))
    return min(1.0, total), d


def face_boost(light, normal: Tuple[float, float, float], ambient: float = 0.3) -> float:
    """How much a face with outward ``normal`` lights up (0..1)."""
    if light is None:
        return 0.0
    k, d = light
    lit = max(0.0, normal[0] * d[0] + normal[1] * d[1] + normal[2] * d[2])
    return k * (ambient + (1.0 - ambient) * lit)


def lit_rgb(rgb: Sequence[float], boost: float, reach: float = 0.75) -> Tuple[float, float, float]:
    """Colour washed toward the flash's cold white by ``boost``."""
    t = max(0.0, min(1.0, boost * reach))
    return tuple(c + (w - c) * t for c, w in zip(rgb[:3], LIGHT_RGB))


__all__ = ["Strike", "active_strikes", "bolt_shape", "layout", "world_points", "sky_glow", "scene_light",
           "face_boost", "lit_rgb", "RING_M", "SHEET_KM", "CORE_RGB", "GLOW_RGB"]
