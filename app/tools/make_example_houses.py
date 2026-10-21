"""Write the ready-made houses in configs/houses/ (the "Example houses" picker).

Generated rather than hand-written so they always match the current save
format: each one starts from default_house() and goes through save_room().

Run:  python -m app.tools.make_example_houses
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, List, Tuple

from app.models.room import Room, Window, default_house, place_speakers_evenly
from app.utils.persistence import save_room

OUT_DIR = Path(__file__).resolve().parents[2] / "configs" / "houses"


def _base(name: str, w: float, d: float, h: float, roof: str, walls: str) -> Room:
    room = default_house()
    room.name = name
    room.width, room.depth, room.height = w, d, h
    room.roof_material, room.wall_material = roof, walls
    cx, cz = w * 0.5, d * 0.5
    room.listener.x, room.listener.z = cx, cz
    for hp in room.headphones_items:
        hp.x, hp.z = cx, cz
    place_speakers_evenly(room, 3)
    return room


def tin_shed() -> Room:
    room = _base("Tin shed", 3.6, 3.0, 2.4, "Tin Roof", "Wood Wall")
    room.windows = [
        Window(name="Side Window", wall="east", offset=1.0, width=0.9, height=0.8,
               sill=1.1, open=0.3, open_style="slider"),
    ]
    return room


def tile_cottage() -> Room:
    room = _base("Tile cottage", 6.0, 4.5, 2.5, "Tile Roof", "Brick Wall")
    room.windows = [
        Window(name="Front Window", wall="south", offset=1.2, width=1.3, height=1.2,
               sill=0.9, open=0.35, open_style="casement", max_angle_deg=60.0),
        Window(name="Back Window", wall="north", offset=3.4, width=1.1, height=1.1,
               sill=1.0, open=0.25, open_style="sash"),
        Window(name="Garden Window", wall="west", offset=1.6, width=1.0, height=1.0,
               sill=1.0, open=0.30, open_style="awning", max_angle_deg=45.0),
    ]
    return room


def glass_studio() -> Room:
    room = _base("Glass studio", 7.0, 5.0, 3.2, "Metal Roof", "Glass Wall")
    room.windows = [
        Window(name="North Glazing", wall="north", offset=0.8, width=2.6, height=2.2,
               sill=0.5, open=0.5, open_style="slider"),
        Window(name="South Glazing", wall="south", offset=3.2, width=2.6, height=2.2,
               sill=0.5, open=0.2, open_style="slider", hinge_side="right"),
        Window(name="East Window", wall="east", offset=1.4, width=2.0, height=1.8,
               sill=0.7, open=0.6, open_style="pivot"),
    ]
    return room


def brick_house() -> Room:
    room = _base("Brick house", 8.0, 6.0, 2.7, "Shingle Roof", "Brick Wall")
    room.windows = [
        Window(name="Living Window", wall="south", offset=2.0, width=1.6, height=1.3,
               sill=0.9, open=0.0, open_style="casement"),
        Window(name="Kitchen Window", wall="east", offset=2.5, width=1.2, height=1.1,
               sill=1.0, open=0.0, open_style="tilt_turn"),
        Window(name="Bedroom Window", wall="north", offset=4.6, width=1.4, height=1.2,
               sill=0.9, open=0.0, open_style="sash"),
    ]
    return room


HOUSES: List[Tuple[str, str, Callable[[], Room]]] = [
    ("tin_shed", "Small wooden shed under a bare tin roof — loud, bright rain right overhead.", tin_shed),
    ("tile_cottage", "Brick cottage with a tile roof and windows left a little open.", tile_cottage),
    ("glass_studio", "Airy studio of glass walls and big windows under a metal roof.", glass_studio),
    ("brick_house", "Solid brick house, shingle roof, every window shut — muffled and calm.", brick_house),
]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for slug, desc, make in HOUSES:
        room = make()
        room.sync_all_windows()
        path = OUT_DIR / f"{slug}.json"
        save_room(room, str(path))
        # save_room has no description field; add it for the picker (load ignores it).
        data = json.loads(path.read_text(encoding="utf-8"))
        data["description"] = desc
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
