"""Weather + sound-mix presets and the ready-made example houses.

Presets are plain dicts of registry keys (see settings_schema), so a preset
saved today keeps loading after knobs are added or renamed: unknown keys are
skipped and missing ones simply stay as they are.

User presets live in ``configs/presets/<kind>/<slug>.json``. Tests (and frozen
builds, whose bundle folder is temporary) point ``PRESET_DIR`` elsewhere; the
``RAINROOM_PRESET_DIR`` env var does the same without touching code.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.models import settings_schema as schema

_REPO = Path(__file__).resolve().parents[2]
CONFIGS_DIR = _REPO / "configs"
HOUSES_DIR = CONFIGS_DIR / "houses"


def _default_preset_dir() -> Path:
    env = os.environ.get("RAINROOM_PRESET_DIR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        # PyInstaller unpacks into a temp dir — keep user presets beside the exe.
        return Path(sys.executable).resolve().parent / "configs" / "presets"
    return CONFIGS_DIR / "presets"


PRESET_DIR: Path = _default_preset_dir()

KINDS = ("weather", "mix")

# Wind direction is left out on purpose: it is about how the house sits, not
# about the weather, so picking "Storm" must not spin your wind wheel.
WEATHER_KEYS: Tuple[str, ...] = (
    "droplet_density", "rain_intensity", "wind_speed",
    "wind_vary_direction", "wind_dir_range_deg", "wind_dir_interval_s", "wind_dir_slew_deg_s",
    "wind_vary_speed", "wind_speed_range", "wind_speed_interval_s", "wind_speed_slew_per_s",
    "thunder_rate", "thunder_distance",
)
MIX_KEYS: Tuple[str, ...] = ("mix_wash", "mix_droplets", "mix_reverb", "mix_wind", "mix_drips", "mix_thunder")

_KEYS = {"weather": WEATHER_KEYS, "mix": MIX_KEYS}


@dataclass
class Preset:
    name: str
    kind: str                      # "weather" | "mix"
    values: Dict[str, float] = field(default_factory=dict)
    description: str = ""
    builtin: bool = True


def _calm(**over) -> Dict[str, float]:
    """Weather values with the wind-variation knobs at their defaults, so every
    weather preset fully defines its keys (no leftovers from the last one)."""
    v = {k: schema.by_key(k).default for k in WEATHER_KEYS}
    v.update(over)
    return v


_BUILTIN: Dict[str, List[Preset]] = {
    "weather": [
        Preset("Drizzle", "weather", _calm(droplet_density=0.10, rain_intensity=0.20, wind_speed=0.05),
               "A fine, barely-there mist of drops."),
        Preset("Light rain", "weather", _calm(droplet_density=0.30, rain_intensity=0.30, wind_speed=0.10),
               "Gentle, sparse rain with a light breeze."),
        # Steady rain = the Room defaults, so a fresh house starts on a named chip.
        Preset("Steady rain", "weather", _calm(droplet_density=0.55, rain_intensity=0.35, wind_speed=0.0),
               "An even, windless rain that could go on all afternoon."),
        Preset("Heavy rain", "weather", _calm(droplet_density=0.72, rain_intensity=0.50, wind_speed=0.25),
               "Thick, loud rain drumming on the roof."),
        Preset("Downpour", "weather", _calm(droplet_density=0.92, rain_intensity=0.60, wind_speed=0.30),
               "A sudden cloudburst — rain falling in sheets."),
        Preset("Storm", "weather", _calm(
            droplet_density=0.85, rain_intensity=0.70, wind_speed=0.75,
            wind_vary_direction=True, wind_dir_range_deg=60.0, wind_dir_interval_s=8.0,
            wind_dir_slew_deg_s=20.0,
            wind_vary_speed=True, wind_speed_range=0.40, wind_speed_interval_s=5.0,
            wind_speed_slew_per_s=0.35, thunder_rate=0.25, thunder_distance=0.65),
            "Strong gusty wind that keeps changing direction, thunder far off."),
        Preset("Thunderstorm", "weather", _calm(
            droplet_density=0.88, rain_intensity=0.60, wind_speed=0.40,
            wind_vary_speed=True, wind_speed_range=0.30, wind_speed_interval_s=6.0,
            wind_speed_slew_per_s=0.30, thunder_rate=0.55, thunder_distance=0.30),
            "Heavy rain with lightning close by and thunder rolling all around."),
    ],
    "mix": [
        Preset("Recommended", "mix", {k: schema.by_key(k).default for k in MIX_KEYS},
               "The balanced mix the app ships with."),
        Preset("Cozy", "mix", dict(mix_wash=0.35, mix_droplets=1.40, mix_reverb=1.30,
                                   mix_wind=0.60, mix_drips=1.30, mix_thunder=0.80),
               "Warm and enveloping, with more drips and room."),
        Preset("Crisp", "mix", dict(mix_wash=0.0, mix_droplets=2.0, mix_reverb=0.60,
                                    mix_wind=0.70, mix_drips=0.80, mix_thunder=1.0),
               "Clear, close drops with little haze or echo."),
        Preset("Distant", "mix", dict(mix_wash=1.20, mix_droplets=0.60, mix_reverb=1.20,
                                      mix_wind=1.0, mix_drips=0.50, mix_thunder=1.0),
               "Rain heard from further away — more hush, fewer close hits."),
        Preset("Just drops", "mix", dict(mix_wash=0.0, mix_droplets=2.0, mix_reverb=0.50,
                                         mix_wind=0.0, mix_drips=0.0, mix_thunder=0.0),
               "Only the drops themselves — no wind, distant rain or gutters."),
    ],
}


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown preset kind {kind!r} (expected one of {KINDS})")
    return kind


def _dir(kind: str) -> Path:
    return Path(PRESET_DIR) / _check_kind(kind)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return s or "preset"


def _only_kind_keys(kind: str, values: Dict[str, float]) -> Dict[str, float]:
    keys = _KEYS[_check_kind(kind)]
    return {k: (bool(v) if schema.by_key(k).kind == "toggle" else float(v))
            for k, v in (values or {}).items() if k in keys}


def builtin(kind: str) -> List[Preset]:
    return [Preset(p.name, p.kind, dict(p.values), p.description, True) for p in _BUILTIN[_check_kind(kind)]]


def user(kind: str) -> List[Preset]:
    d = _dir(kind)
    out: List[Preset] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(Preset(
                name=str(data.get("name") or p.stem),
                kind=kind,
                values=_only_kind_keys(kind, data.get("values", {})),
                description=str(data.get("description", "")),
                builtin=False,
            ))
        except (OSError, ValueError, TypeError, AttributeError):
            continue  # a hand-broken file must not hide the other presets
    out.sort(key=lambda pr: pr.name.lower())
    return out


def all_presets(kind: str) -> List[Preset]:
    """Built-ins first, then the user's own; a user preset with a built-in's
    name replaces it (so "save over Storm" does what it says)."""
    mine = user(kind)
    taken = {p.name.lower() for p in mine}
    return [p for p in builtin(kind) if p.name.lower() not in taken] + mine


def save_user(kind: str, name: str, values: Dict[str, float]) -> Path:
    name = str(name).strip()
    if not name:
        raise ValueError("preset name is empty")
    d = _dir(kind)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_slug(name)}.json"
    data = {"name": name, "kind": kind, "values": _only_kind_keys(kind, values)}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def delete_user(kind: str, name: str) -> bool:
    d = _dir(kind)
    if not d.is_dir():
        return False
    removed = False
    for p in d.glob("*.json"):
        try:
            stored = json.loads(p.read_text(encoding="utf-8")).get("name") or p.stem
        except (OSError, ValueError, AttributeError):
            stored = p.stem
        if str(stored).lower() == str(name).strip().lower() or p.stem == _slug(name):
            try:
                p.unlink()
                removed = True
            except OSError:
                pass
    return removed


def apply(room, preset: Preset) -> None:
    schema.apply(room, _only_kind_keys(preset.kind, preset.values))


def _matches(room, preset: Preset, tol: float) -> bool:
    vals = preset.values
    for k in _KEYS[preset.kind]:
        s = schema.by_key(k)
        if k not in vals:
            continue
        # A vary knob behind a switched-off toggle is inaudible, so it should
        # not turn the chip to "Custom".
        if s.enabled_by and not bool(vals.get(s.enabled_by, 0)) and not bool(schema.get(room, s.enabled_by)):
            continue
        a, b = schema.get(room, k), float(vals[k])
        if s.kind == "toggle":
            if bool(a) != bool(b):
                return False
        elif abs(a - b) > tol * max(1e-9, s.hi - s.lo):
            return False
    return True


def fits(room, preset: Preset, tol: float = 0.011) -> bool:
    """True if the room's values match ``preset`` (same test as matching())."""
    return _matches(room, preset, tol)


def matching(room, kind: str, tol: float = 0.011, prefer: Optional[str] = None) -> Optional[str]:
    """Name of the preset the room currently matches, or None ("Custom").

    ``tol`` is a fraction of each setting's range (≈ one slider notch), so a
    seconds knob and a 0..1 knob are judged alike. Two presets can hold the
    same values (a user copy of a built-in); ``prefer`` names the one the user
    last picked so it stays highlighted instead of the first in the list."""
    found = None
    for p in all_presets(kind):
        if _matches(room, p, tol):
            if prefer is None or p.name.lower() == str(prefer).lower():
                return p.name
            found = found or p.name
    return found


def example_houses() -> List[Tuple[str, str, Path]]:
    """(name, description, path) for every configs/houses/*.json, sorted by name."""
    out: List[Tuple[str, str, Path]] = []
    if not HOUSES_DIR.is_dir():
        return out
    for p in HOUSES_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = str(data.get("name") or (data.get("room") or {}).get("name") or p.stem)
        out.append((name, str(data.get("description", "")), p))
    out.sort(key=lambda t: t[0].lower())
    return out


# ----- your own houses -----
# A house preset is the whole room (size, materials, windows, speakers, You
# and every sound setting) as save_room writes it, under PRESET_DIR/house.
# Loading one copies it into the room you're working on; changing that room
# never touches the preset until you save over it.


def house_preset_dir() -> Path:
    return PRESET_DIR / "house"


def house_presets() -> List[Tuple[str, Path]]:
    """(name, path) of your saved houses, sorted by name."""
    d = house_preset_dir()
    out: List[Tuple[str, Path]] = []
    if not d.is_dir():
        return out
    for p in d.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append((str(data.get("preset_name") or data.get("name") or p.stem), p))
    out.sort(key=lambda t: t[0].lower())
    return out


def house_preset_path(name: str) -> Optional[Path]:
    want = str(name).strip().lower()
    for n, p in house_presets():
        if n.lower() == want:
            return p
    return None


def save_house_preset(room, name: str) -> Path:
    """Save ``room`` as your house preset ``name`` (over one of that name)."""
    from app.utils.persistence import room_to_dict

    name = str(name).strip()
    if not name:
        raise ValueError("preset name is empty")
    d = house_preset_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = house_preset_path(name) or d / f"{_slug(name)}.json"
    data = room_to_dict(room)
    data["preset_name"] = name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def delete_house_preset(name: str) -> bool:
    p = house_preset_path(name)
    if p is None:
        return False
    try:
        p.unlink()
    except OSError:
        return False
    return True


def autosave_path() -> Path:
    """Where the room you're working on is kept between sessions."""
    env = os.environ.get("RAINROOM_AUTOSAVE")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "configs" / "my_house.json"
    return CONFIGS_DIR / "my_house.json"


def previous_room_path() -> Path:
    """The room you were working on before the last New / Open / preset."""
    auto = autosave_path()
    return auto.with_name(auto.stem + ".previous.json")


__all__ = [
    "KINDS", "MIX_KEYS", "PRESET_DIR", "Preset", "WEATHER_KEYS", "all_presets", "apply",
    "autosave_path", "builtin", "delete_house_preset", "delete_user", "example_houses", "fits",
    "house_preset_dir", "house_preset_path", "house_presets", "matching", "previous_room_path",
    "save_house_preset",
    "save_user", "user",
]
