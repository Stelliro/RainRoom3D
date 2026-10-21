"""One registry for every rain / wind / mix / drop-tone setting.

The UI builds its sliders FROM this list, presets store these keys, and the
tests check them against ``Room``. Adding a knob = one ``Setting`` here, so the
label, range, default and tooltip can never drift between pages.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from app.models.room import (
    MIX_DRIPS_RECOMMENDED,
    MIX_DROPLETS_RECOMMENDED,
    MIX_REVERB_RECOMMENDED,
    MIX_WASH_RECOMMENDED,
    MIX_WIND_RECOMMENDED,
    TONE_PITCH_RECOMMENDED,
    TONE_RING_RECOMMENDED,
    TONE_SOFT_RECOMMENDED,
    TONE_WEIGHT_RECOMMENDED,
    TONE_WET_RECOMMENDED,
)


@dataclass(frozen=True)
class Setting:
    key: str            # Room attribute name (e.g. "droplet_density")
    label: str
    group: str          # one of the GROUPS ids
    kind: str           # "slider" | "toggle" | "dial"   (dial = 0..359 degrees wheel)
    lo: float
    hi: float
    default: float
    step: float         # slider resolution in model units
    help: str           # one plain sentence (tooltip)
    left: str = ""
    right: str = ""
    advanced: bool = False
    fmt: Optional[Callable[[float], str]] = None
    enabled_by: Optional[str] = None

    def clamp(self, v) -> float:
        if self.kind == "toggle":
            return 1.0 if bool(v) else 0.0
        v = float(v)
        if not math.isfinite(v):
            return float(self.default)
        if self.kind == "dial":
            # A wheel wraps: 370° is 10°, not "clamped to 359°".
            return v % 360.0
        return max(float(self.lo), min(float(self.hi), v))

    def display(self, v) -> str:
        if self.fmt is not None:
            return self.fmt(v)
        if self.kind == "toggle":
            return "On" if bool(v) else "Off"
        v = float(v)
        if self.kind == "dial" or self.key.endswith("_deg"):
            return f"{int(round(v))}°"
        if self.key.endswith("_s"):
            return f"{v:.0f} s"
        if self.hi > 1.0 + 1e-9:
            return f"{v:.2f}×"
        return f"{int(round(v * 100))}%"


# ----- value formatters (plain words first, numbers second) -----

def _fmt_amount(q: float) -> str:
    try:
        from app.audio.rain_dsd import describe
        return describe(float(q))
    except Exception:  # audio deps missing — still show something useful
        return f"{int(round(float(q) * 100))}%"


def _fmt_crisp(s: float) -> str:
    s = float(s)
    tag = "soft" if s < 0.34 else ("crisp" if s > 0.66 else "balanced")
    return f"{int(round(s * 100))}% · {tag}"


def _fmt_wind(s: float) -> str:
    s = float(s)
    if s < 0.05:
        tag = "calm"
    elif s < 0.25:
        tag = "light breeze"
    elif s < 0.5:
        tag = "breeze"
    elif s < 0.75:
        tag = "strong"
    else:
        tag = "gale"
    return f"{int(round(s * 100))}% · {tag}"


_COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def compass_name(deg: float) -> str:
    return _COMPASS[int((float(deg) % 360.0 + 11.25) // 22.5) % 16]


# Wall that takes the rain for a wind blowing toward N / E / S / W.
_HIT_SIDE = ("south", "west", "north", "east")


def _fmt_dir(d: float) -> str:
    # Rain blown toward E lands on the WEST wall (house_model.facade_tilt), so
    # name the wall it hits too — aiming at the wall you want to hear is natural.
    d = float(d) % 360.0
    side = _HIT_SIDE[int((d + 45.0) // 90.0) % 4]
    return f"{int(round(d)) % 360}° · toward {compass_name(d)} · hits {side} side"


def _fmt_thunder_rate(v: float) -> str:
    try:
        from app.audio.thunder import rate_per_min
        per = rate_per_min(float(v))
    except Exception:
        per = 0.0 if float(v) <= 0.005 else 1.0
    if per <= 0.0:
        return "Off"
    every = 60.0 / per
    return f"about every {every / 60.0:.0f} min" if every >= 90.0 else f"about every {every:.0f} s"


def _fmt_thunder_km(v: float) -> str:
    try:
        from app.audio.thunder import storm_km
        km = storm_km(float(v))
    except Exception:
        km = 2.0
    if km < 0.6:
        return "Overhead"
    return f"~{km:.0f} km away" if km >= 1.5 else f"~{km:.1f} km away"


def _fmt_mix(v: float) -> str:
    # Same "%" language as the other sliders (100% = as designed).
    v = float(v)
    return "off" if v < 0.005 else f"{int(round(v * 100))}%"


# ----- groups + settings -----

GROUPS: List[Tuple[str, str, str]] = [
    ("rain", "Rain", "How much rain falls and how sharp each drop sounds"),
    ("wind", "Wind", "How hard the wind blows, which way, and how it changes"),
    ("thunder", "Thunder", "Lightning and thunder from a storm around the house"),
    ("mix", "Sound mix", "How loud each part of the rain sound is"),
    ("drop", "Drop character", "What a single drop sounds like when it lands"),
]


def _slider(key, label, group, lo, hi, default, step, help, left="", right="", **kw) -> Setting:
    return Setting(key, label, group, "slider", lo, hi, default, step, help, left, right, **kw)


def _toggle(key, label, group, help, **kw) -> Setting:
    return Setting(key, label, group, "toggle", 0.0, 1.0, 0.0, 1.0, help, **kw)


SETTINGS: List[Setting] = [
    # Rain
    _slider("droplet_density", "Amount", "rain", 0.0, 1.0, 0.55, 0.01,
            "How much rain is falling, from a light drizzle to a downpour.",
            "Drizzle", "Downpour", fmt=_fmt_amount),
    _slider("rain_intensity", "Crispness", "rain", 0.0, 1.0, 0.35, 0.01,
            "How sharp each drop sounds — soft and blurred or crisp and clear.",
            "Soft", "Crisp", fmt=_fmt_crisp),
    # Wind
    _slider("wind_speed", "Strength", "wind", 0.0, 1.0, 0.0, 0.01,
            "How hard the wind blows: you hear it rush past and whistle at windows "
            "open a crack, and it drives the rain against the house.",
            "Calm", "Gale", fmt=_fmt_wind),
    Setting("wind_direction_deg", "Direction", "wind", "dial", 0.0, 359.0, 90.0, 1.0,
            "Where the wind blows the rain to. It lands hardest on the wall facing the "
            "other way (90°/East is heard on the west side). Needs some wind Strength.",
            fmt=_fmt_dir),
    _toggle("wind_vary_direction", "Shifting direction", "wind",
            "Let the wind slowly swing around the direction you picked (needs some wind Strength)."),
    _toggle("wind_vary_speed", "Gusty", "wind",
            "Let the wind rise and fall in gusts around the strength you picked."),
    _slider("wind_dir_range_deg", "How far it swings", "wind", 0.0, 180.0, 45.0, 1.0,
            "How far the wind can drift either side of the chosen direction.",
            "Steady", "All around", advanced=True, enabled_by="wind_vary_direction",
            fmt=lambda v: f"±{int(round(float(v)))}°"),
    _slider("wind_dir_interval_s", "New direction every", "wind", 1.0, 60.0, 10.0, 1.0,
            "How often the wind picks a new direction to drift toward.",
            "Often", "Rarely", advanced=True, enabled_by="wind_vary_direction",
            fmt=lambda v: f"~{int(round(float(v)))} s"),
    _slider("wind_dir_slew_deg_s", "Turning speed", "wind", 1.0, 60.0, 15.0, 1.0,
            "How quickly the wind turns toward its new direction.",
            "Slow", "Fast", advanced=True, enabled_by="wind_vary_direction",
            fmt=lambda v: f"{int(round(float(v)))}°/s"),
    _slider("wind_speed_range", "Gust strength", "wind", 0.0, 1.0, 0.25, 0.01,
            "How much stronger or weaker a gust can be than the chosen strength.",
            "Gentle", "Wild", advanced=True, enabled_by="wind_vary_speed",
            fmt=lambda v: f"±{int(round(float(v) * 100))}%"),
    _slider("wind_speed_interval_s", "New gust every", "wind", 1.0, 60.0, 8.0, 1.0,
            "How often a new gust starts.",
            "Often", "Rarely", advanced=True, enabled_by="wind_vary_speed",
            fmt=lambda v: f"~{int(round(float(v)))} s"),
    _slider("wind_speed_slew_per_s", "Gust build-up", "wind", 0.01, 1.0, 0.20, 0.01,
            "How quickly a gust builds up and dies down.",
            "Slow", "Sudden", advanced=True, enabled_by="wind_vary_speed",
            fmt=lambda v: f"{int(round(float(v) * 100))}%/s"),
    # Thunder
    _slider("thunder_rate", "How often", "thunder", 0.0, 1.0, 0.0, 0.01,
            "How often lightning strikes. Off means no thunder at all.",
            "Never", "Constant", fmt=_fmt_thunder_rate),
    _slider("thunder_distance", "Distance", "thunder", 0.0, 1.0, 0.45, 0.01,
            "How far away the storm is. Close strikes crack and boom; far ones only rumble, "
            "and their sound comes long after the flash.",
            "Overhead", "Far away", fmt=_fmt_thunder_km),
    # Sound mix (0..2×, 1 = as designed). Nearby drops ships at 2.0, so it gets
    # headroom up to 3.0 (the engine caps it there) instead of starting maxed out.
    _slider("mix_droplets", "Nearby drops", "mix", 0.0, 3.0, MIX_DROPLETS_RECOMMENDED, 0.01,
            "Individual drops landing close to you on the roof, windows and sills.",
            "Off", "Loud", fmt=_fmt_mix),
    _slider("mix_wash", "Distant rain", "mix", 0.0, 2.0, MIX_WASH_RECOMMENDED, 0.01,
            "The soft steady hush of rain falling everywhere farther away.",
            "Off", "Loud", fmt=_fmt_mix),
    _slider("mix_reverb", "Room echo", "mix", 0.0, 2.0, MIX_REVERB_RECOMMENDED, 0.01,
            "How much the sound bounces around inside the room.",
            "Dry", "Echoey", fmt=_fmt_mix),
    _slider("mix_wind", "Wind sound", "mix", 0.0, 2.0, MIX_WIND_RECOMMENDED, 0.01,
            "The wind itself: its rush, roar and the whistle at windows open a crack.",
            "Off", "Loud", fmt=_fmt_mix),
    _slider("mix_drips", "Drips & gutters", "mix", 0.0, 2.0, MIX_DRIPS_RECOMMENDED, 0.01,
            "Water dripping off the roof edge and trickling through the gutters.",
            "Off", "Loud", fmt=_fmt_mix),
    _slider("mix_thunder", "Thunder", "mix", 0.0, 2.0, 1.0, 0.01,
            "How loud the thunder is (when there is any — see Thunder).",
            "Off", "Loud", fmt=_fmt_mix),
    # Drop character (0..1)
    _slider("tone_pitch", "Pitch", "drop", 0.0, 1.0, TONE_PITCH_RECOMMENDED, 0.01,
            "Whether drops sound deep and low or high and bright.", "Deep", "High"),
    _slider("tone_weight", "Drop size", "drop", 0.0, 1.0, TONE_WEIGHT_RECOMMENDED, 0.01,
            "Whether it sounds like a fine spray or big fat drops.", "Tiny", "Fat"),
    _slider("tone_ring", "Ring", "drop", 0.0, 1.0, TONE_RING_RECOMMENDED, 0.01,
            "Whether a hit is a dull thump or rings like a little note.", "Thump", "Plink"),
    _slider("tone_wet", "Wetness", "drop", 0.0, 1.0, TONE_WET_RECOMMENDED, 0.01,
            "Whether a hit is a dry tick or a wet splash.", "Dry tick", "Wet splash"),
    _slider("tone_soft", "Softness", "drop", 0.0, 1.0, TONE_SOFT_RECOMMENDED, 0.01,
            "Whether each hit starts sharp and snappy or soft and rounded.", "Snappy", "Rounded"),
]

# Master listen level lives in the play bar, not in a Rain section.
VOLUME = _slider("master_volume", "Volume", "output", 0.0, 1.0, 0.75, 0.01,
                 "How loud everything plays.", "Quiet", "Loud")

_BY_KEY: Dict[str, Setting] = {s.key: s for s in SETTINGS}
_BY_KEY[VOLUME.key] = VOLUME


def by_key(key: str) -> Setting:
    return _BY_KEY[key]


def in_group(group: str, advanced: Optional[bool] = None) -> List[Setting]:
    """Settings of one group in display order; ``advanced`` filters when given."""
    pool = [VOLUME] if group == VOLUME.group else SETTINGS
    return [s for s in pool if s.group == group and (advanced is None or s.advanced == advanced)]


def get(room, key: str) -> float:
    s = by_key(key)
    return float(getattr(room, key, s.default))


def set(room, key: str, value) -> float:  # noqa: A001 — contract name
    """Clamp and store; toggles are stored as real bools. Returns what was stored."""
    s = by_key(key)
    v = s.clamp(value)
    stored = bool(v) if s.kind == "toggle" else float(v)
    setattr(room, key, stored)
    return stored


def values(room, keys: Optional[Iterable[str]] = None) -> Dict[str, float]:
    keys = [s.key for s in SETTINGS] if keys is None else list(keys)
    return {k: get(room, k) for k in keys}


def apply(room, values: Dict[str, float]) -> None:
    """Write many values at once; unknown keys (old presets, typos) are skipped."""
    for k, v in (values or {}).items():
        if k in _BY_KEY:
            try:
                set(room, k, v)
            except (TypeError, ValueError):
                pass


def reset(room, group: Optional[str] = None) -> None:
    """Restore defaults. ``None`` resets every Rain-page setting but never the
    play-bar volume (that would be a nasty surprise); pass "output" for it."""
    for s in (SETTINGS if group is None else in_group(group)):
        set(room, s.key, s.default)


# One plain line per material, keyed by the roof / wall option names
# (app/models/materials.ROOF_MATERIALS, WALL_MATERIALS). Wording follows
# app/audio/house_model.MATERIALS (radiate / brightness / cutoff).
MATERIAL_HINTS: Dict[str, str] = {
    "Tin Roof": "Tin roof — loud and bright; the roof is your ceiling",
    "Metal Roof": "Metal roof — loud and ringing, a little less bright than tin",
    "Tile Roof": "Tile roof — muffled and dull; an attic sits between you and the rain",
    "Shingle Roof": "Shingle roof — the quietest and darkest roof; a soft distant patter",
    "Wood Roof": "Wood roof — a soft, warm knock of rain overhead",
    "Glass Roof": "Glass roof — rain taps clearly right above you; bright and close",
    "Brick Wall": "Brick walls — block almost all outside rain; very quiet and deep",
    "Wood Wall": "Wood walls — let a soft, warm knock of rain through",
    "Glass Wall": "Glass walls — rain taps clearly through them; bright and close",
    "Tin Wall": "Tin walls — loud and bright when rain is blown against them",
}


def material_hint(name: str, role: str) -> str:
    """One-line hint for a material used as ``role`` ("roof" or "wall")."""
    from app.models.materials import role_material

    return MATERIAL_HINTS.get(role_material(name, role), "")


__all__ = [
    "GROUPS", "MATERIAL_HINTS", "SETTINGS", "Setting", "VOLUME", "apply", "by_key",
    "compass_name", "get", "in_group", "material_hint", "reset", "set", "values",
]
