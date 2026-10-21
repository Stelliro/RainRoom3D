"""
Checks the settings registry, presets and example houses.

Run:  python -m app.tools.test_settings
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import fields
from pathlib import Path


def _ok(name: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"  [{mark}] {name}{extra}")
    return bool(cond)


def _all_settings():
    from app.models import settings_schema as S
    return S.SETTINGS + [S.VOLUME]


def test_keys_exist_on_room() -> bool:
    from app.models.room import Room
    names = {f.name for f in fields(Room)}
    missing = [s.key for s in _all_settings() if s.key not in names]
    return _ok("every Setting.key is a Room field", not missing, f"missing {missing}" if missing else "")


def test_defaults_match_room() -> bool:
    from app.models.room import Room
    room = Room(width=4.0, height=2.6, depth=3.0)
    bad = [(s.key, s.default, getattr(room, s.key)) for s in _all_settings()
           if abs(float(s.default) - float(getattr(room, s.key))) > 1e-9]
    return _ok("registry defaults == Room defaults", not bad, str(bad) if bad else "")


def test_groups_and_lookup() -> bool:
    from app.models import settings_schema as S
    ids = [g[0] for g in S.GROUPS]
    ok = ids == ["rain", "wind", "thunder", "mix", "drop"]
    ok &= all(s.group in ids for s in S.SETTINGS)
    ok &= S.VOLUME.group == "output" and S.by_key("master_volume") is S.VOLUME
    ok &= len(S.in_group("wind", advanced=True)) == 6
    ok &= all(s.kind in ("slider", "toggle", "dial") for s in S.SETTINGS)
    ok &= all(s.enabled_by is None or S.by_key(s.enabled_by).kind == "toggle" for s in S.SETTINGS)
    ok &= all(s.help and s.label for s in S.SETTINGS)
    ok &= all(isinstance(s.display(s.default), str) for s in _all_settings())
    return _ok("groups, by_key, in_group, display", ok)


def test_set_clamps() -> bool:
    from app.models import settings_schema as S
    from app.models.room import default_house
    room = default_house()
    ok = S.set(room, "droplet_density", 5.0) == 1.0 and room.droplet_density == 1.0
    ok &= S.set(room, "mix_wash", -3.0) == 0.0
    ok &= S.set(room, "mix_droplets", 9.0) == 3.0
    ok &= S.set(room, "wind_dir_interval_s", 0.0) == 1.0
    ok &= abs(S.set(room, "wind_direction_deg", 370.0) - 10.0) < 1e-9
    ok &= S.set(room, "wind_vary_speed", 1) is True and room.wind_vary_speed is True
    ok &= S.set(room, "wind_vary_speed", 0.0) is False
    S.apply(room, {"tone_pitch": 0.9, "not_a_key": 3.0, "tone_ring": 7.0})
    ok &= abs(room.tone_pitch - 0.9) < 1e-9 and room.tone_ring == 1.0
    return _ok("set() / apply() clamp, wrap dial, bool toggles, skip unknown", ok)


def test_values_roundtrip() -> bool:
    from app.models import settings_schema as S
    from app.models.room import default_house
    a, b = default_house(), default_house()
    for i, s in enumerate(S.SETTINGS):
        if s.kind == "toggle":
            S.set(a, s.key, i % 2 == 0)
        else:
            S.set(a, s.key, s.lo + (s.hi - s.lo) * ((i * 0.137) % 1.0))
    S.apply(b, S.values(a))
    same = S.values(a) == S.values(b)
    S.reset(b)
    reset_ok = all(abs(S.get(b, s.key) - s.default) < 1e-9 for s in S.SETTINGS)
    S.set(b, "master_volume", 0.2)
    S.reset(b)
    vol_kept = abs(b.master_volume - 0.2) < 1e-9
    S.reset(b, "output")
    vol_reset = abs(b.master_volume - S.VOLUME.default) < 1e-9
    return _ok("values()/apply() round-trip; reset() leaves volume unless asked",
               same and reset_ok and vol_kept and vol_reset)


def test_builtin_presets_match() -> bool:
    from app.models import presets as P
    from app.models.room import default_house
    ok = True
    names = {}
    for kind in P.KINDS:
        lst = P.builtin(kind)
        ok &= len(lst) > 0
        names[kind] = [p.name for p in lst]
        for p in lst:
            room = default_house()
            P.apply(room, p)
            got = P.matching(room, kind)
            if got != p.name:
                ok = False
                print(f"      {kind}/{p.name} matched {got!r}")
    ok &= names["weather"] == ["Drizzle", "Light rain", "Steady rain", "Heavy rain", "Downpour", "Storm", "Thunderstorm"]
    ok &= names["mix"] == ["Recommended", "Cozy", "Crisp", "Distant", "Just drops"]
    ok &= all("wind_direction_deg" not in p.values for p in P.builtin("weather"))
    room = default_house()
    ok &= P.matching(room, "mix") == "Recommended"
    room.mix_wind = 0.33
    ok &= P.matching(room, "mix") is None  # → "Custom"
    return _ok("built-in presets non-empty; apply → matching() names them", ok)


def test_user_presets_tempdir() -> bool:
    from app.models import presets as P
    from app.models.room import default_house
    old = P.PRESET_DIR
    ok = True
    with tempfile.TemporaryDirectory() as td:
        P.PRESET_DIR = Path(td)
        try:
            ok &= P.user("mix") == []
            vals = dict(mix_wash=0.7, mix_droplets=0.3, mix_reverb=1.7, mix_wind=0.2,
                        mix_drips=1.9, tone_pitch=0.1)  # tone_* is not a mix key → dropped
            path = P.save_user("mix", "My Porch", vals)
            ok &= path.is_file() and path.parent == Path(td) / "mix"
            u = P.user("mix")
            ok &= len(u) == 1 and u[0].name == "My Porch" and not u[0].builtin
            ok &= "tone_pitch" not in u[0].values
            ok &= [p.name for p in P.all_presets("mix")][-1] == "My Porch"
            room = default_house()
            P.apply(room, u[0])
            ok &= P.matching(room, "mix") == "My Porch" and abs(room.mix_reverb - 1.7) < 1e-9
            P.save_user("mix", "My Porch", dict(vals, mix_wind=1.1))  # overwrite same name
            u = P.user("mix")
            ok &= len(u) == 1 and abs(u[0].values["mix_wind"] - 1.1) < 1e-9
            ok &= P.delete_user("mix", "My Porch") and P.user("mix") == []
            ok &= not P.delete_user("mix", "My Porch")
        finally:
            P.PRESET_DIR = old
    return _ok("user preset save / load / overwrite / delete in a temp dir", ok)


def _roundtrip(room, label: str) -> bool:
    from app.models import settings_schema as S
    from app.utils.persistence import load_room, save_room
    before = S.values(room, [s.key for s in _all_settings()])
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "house.json")
        save_room(room, p)
        after = S.values(load_room(p), list(before))
    diff = {k: (before[k], after[k]) for k in before if abs(before[k] - after[k]) > 1e-9}
    return _ok(f"save_room/load_room keeps every registry value ({label})", not diff, str(diff) if diff else "")


def test_persistence_roundtrip() -> bool:
    from app.models import presets as P
    from app.models import settings_schema as S
    from app.utils.persistence import load_room
    repo = Path(__file__).resolve().parents[2]
    ok = True
    mine = repo / "configs" / "my_house.json"
    if mine.is_file():
        ok &= _roundtrip(load_room(str(mine)), "my_house.json, in memory")
    houses = P.example_houses()
    if houses:
        room = load_room(str(houses[0][2]))
        # Push every knob off its default so a silently dropped key would show.
        for i, s in enumerate(S.SETTINGS):
            S.set(room, s.key, (i % 2 == 1) if s.kind == "toggle" else s.lo + (s.hi - s.lo) * 0.37)
        ok &= _roundtrip(room, houses[0][0])
    return ok and bool(houses)


def test_example_houses() -> bool:
    from app.models import presets as P
    from app.models import settings_schema as S
    from app.utils.persistence import load_room
    houses = P.example_houses()
    names = [h[0] for h in houses]
    ok = names == ["Brick house", "Glass studio", "Tile cottage", "Tin shed"]
    for name, desc, path in houses:
        r = load_room(str(path))
        ok &= bool(desc) and r.name == name and len(r.speakers) >= 2 and len(r.windows) >= 1
        ok &= r.roof_material in S.MATERIAL_HINTS and r.wall_material in S.MATERIAL_HINTS
        ok &= r.contains_point(r.listener.x, r.listener.z)
    return _ok("example_houses() lists the 4 ready-made houses", ok, ", ".join(names))


def test_material_hints() -> bool:
    from app.models.materials import ROOF_MATERIALS, WALL_MATERIALS
    from app.models import settings_schema as S
    missing = [m for m in ROOF_MATERIALS + WALL_MATERIALS if not S.MATERIAL_HINTS.get(m)]
    return _ok("every material has a plain hint", not missing, str(missing) if missing else "")


def test_review_fixes() -> bool:
    """Direction names the wall it hits, mix shows %, a user copy of a built-in
    can win the match, and role-aware material hints."""
    from app.models import presets as P
    from app.models import settings_schema as S
    from app.models.room import default_house
    d = S.by_key("wind_direction_deg")
    ok = "hits west side" in d.display(90) and "hits south side" in d.display(0)
    ok &= "hits east side" in d.display(270) and "hits north side" in d.display(180)
    mix = S.by_key("mix_droplets")
    ok &= mix.display(1.0) == "100%" and mix.display(0.0) == "off"
    ok &= mix.default < mix.hi  # Recommended is not pinned at the top of the fader
    old = P.PRESET_DIR
    with tempfile.TemporaryDirectory() as td:
        P.PRESET_DIR = Path(td)
        try:
            room = default_house()
            P.save_user("mix", "Mine", S.values(room, P.MIX_KEYS))
            ok &= P.matching(room, "mix") == "Recommended"
            ok &= P.matching(room, "mix", prefer="Mine") == "Mine"
            ok &= P.matching(room, "mix", prefer="Cozy") == "Recommended"  # prefer must still fit
        finally:
            P.PRESET_DIR = old
    ok &= S.material_hint("Tin Roof", "wall").startswith("Tin walls")
    ok &= S.material_hint("Tin Roof", "roof") == S.MATERIAL_HINTS["Tin Roof"]
    return _ok("direction wall, mix %, preferred preset, role hints", ok)


def main() -> int:
    print("RainRoom3D settings registry / presets checks")
    results = [
        test_keys_exist_on_room(),
        test_defaults_match_room(),
        test_groups_and_lookup(),
        test_set_clamps(),
        test_values_roundtrip(),
        test_builtin_presets_match(),
        test_user_presets_tempdir(),
        test_persistence_roundtrip(),
        test_example_houses(),
        test_material_hints(),
        test_review_fixes(),
    ]
    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
