
"""Load / save house layouts as JSON."""

from __future__ import annotations

import json
from typing import Any, Dict

from app.models.room import (
    Headphones,
    Listener,
    MIX_DROPLETS_RECOMMENDED,
    MIX_REVERB_RECOMMENDED,
    MIX_WASH_RECOMMENDED,
    MIX_WIND_RECOMMENDED,
    Room,
    Speaker,
    Window,
    default_house,
)


def _speaker_from_dict(d: Dict[str, Any]) -> Speaker:
    size = float(d.get("size", 0.32))
    return Speaker(
        name=d.get("name", "Speaker"),
        x=float(d.get("x", 1.0)),
        y=float(d.get("y", 1.1)),
        z=float(d.get("z", 1.0)),
        size=size,
        width=float(d.get("width", 0.0)),
        height=float(d.get("height", 0.0)),
        depth=float(d.get("depth", 0.0)),
        material=d.get("material"),
        audio_device=d.get("audio_device"),
        gain_db=float(d.get("gain_db", 0.0)),
        enabled=bool(d.get("enabled", True)),
        notes=str(d.get("notes", "")),
    )


def _window_from_dict(d: Dict[str, Any]) -> Window:
    # Support both new (wall/offset) and legacy (x/z only) formats
    wall = d.get("wall")
    if not wall:
        # Infer wall from x/z if possible — default north
        wall = "north"
    return Window(
        name=d.get("name", "Window"),
        wall=str(wall),
        offset=float(d.get("offset", d.get("x", 0.5))),
        width=float(d.get("width", 1.0)),
        height=float(d.get("height", 1.2)),
        sill=float(d.get("sill", 0.9)),
        open=float(d.get("open", 0.7)),
        open_style=str(d.get("open_style", "casement")),
        max_angle_deg=float(d.get("max_angle_deg", 75.0)),
        hinge_side=str(d.get("hinge_side", "left")),
        material=d.get("material", "Glass Window"),
        custom_hinge=str(d.get("custom_hinge", "left")),
        custom_motion=str(d.get("custom_motion", "swing")),
        custom_outward=bool(d.get("custom_outward", True)),
        custom_notes=str(d.get("custom_notes", "")),
        free_place=bool(d.get("free_place", False)),
        free_x=float(d.get("free_x", d.get("x", 0.0))),
        free_y=float(d.get("free_y", float(d.get("sill", 0.9)) + 0.5 * float(d.get("height", 1.2)))),
        free_z=float(d.get("free_z", d.get("z", 0.0))),
        x=float(d.get("x", 0.0)),
        z=float(d.get("z", 0.0)),
    )


def load_room(path: str) -> Room:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    r = data.get("room", {})
    windows = [_window_from_dict(w) for w in data.get("windows", [])]
    speakers = [_speaker_from_dict(s) for s in data.get("speakers", [])]
    headphones_items = [
        Headphones(
            name=hp.get("name", "HP"),
            x=float(hp.get("x", 0.0)),
            y=float(hp.get("y", 1.5)),
            z=float(hp.get("z", 0.0)),
            material=hp.get("material"),
        )
        for hp in data.get("headphones_items", [])
    ]
    l = data.get("listener", {})
    rain = data.get("rain", {})
    room = Room(
        width=float(r.get("width", 4.0)),
        height=float(r.get("height", 2.6)),
        depth=float(r.get("depth", 3.2)),
        terrain_size=float(r.get("terrain_size", 40.0)),
        roof_material=str(r.get("roof_material", "Metal Roof")),
        wall_material=str(r.get("wall_material", "Brick Wall")),
        name=str(r.get("name", data.get("name", "My House"))),
        windows=windows,
        speakers=speakers,
        headphones_items=headphones_items,
        listener=Listener(
            x=float(l.get("x", 2.0)),
            y=float(l.get("y", 1.2)),
            z=float(l.get("z", 1.6)),
            yaw=float(l.get("yaw", 0.0)),
            pitch=float(l.get("pitch", 0.0)),
            roll=float(l.get("roll", 0.0)),
            audio_device=(
                int(l["audio_device"]) if l.get("audio_device") is not None else None
            ),
        ),
        headphones_mode=bool(data.get("headphones_mode", False)),
        rain_intensity=float(rain.get("intensity", 0.5)),
        thunder=float(rain.get("thunder", 0.1)),
        droplet_density=float(rain.get("droplet_density", 0.5)),
        master_volume=float(rain.get("master_volume", data.get("master_volume", 0.75))),
        wind_speed=float(rain.get("wind_speed", abs(float(rain.get("wind", 0.0))))),
        wind_direction_deg=float(rain.get("wind_direction_deg", 90.0 if float(rain.get("wind", 0.0)) >= 0 else 270.0)),
        wind_vary_direction=bool(rain.get("wind_vary_direction", False)),
        wind_dir_range_deg=float(rain.get("wind_dir_range_deg", 45.0)),
        wind_dir_interval_s=float(rain.get("wind_dir_interval_s", 10.0)),
        wind_dir_slew_deg_s=float(rain.get("wind_dir_slew_deg_s", 15.0)),
        wind_vary_speed=bool(rain.get("wind_vary_speed", False)),
        wind_speed_range=float(rain.get("wind_speed_range", 0.25)),
        wind_speed_interval_s=float(rain.get("wind_speed_interval_s", 8.0)),
        wind_speed_slew_per_s=float(rain.get("wind_speed_slew_per_s", 0.20)),
        mix_wash=float(
            rain.get("mix_wash", data.get("mix", {}).get("wash", MIX_WASH_RECOMMENDED))
        ),
        mix_droplets=float(
            rain.get(
                "mix_droplets",
                data.get("mix", {}).get("droplets", MIX_DROPLETS_RECOMMENDED),
            )
        ),
        mix_reverb=float(
            rain.get(
                "mix_reverb",
                data.get("mix", {}).get("reverb", MIX_REVERB_RECOMMENDED),
            )
        ),
        mix_wind=float(
            rain.get("mix_wind", data.get("mix", {}).get("wind", MIX_WIND_RECOMMENDED))
        ),
    )
    # Legacy-only wind field (no speed/dir keys): map signed EW
    if "wind_speed" not in rain and "wind" in rain:
        room.set_legacy_wind(float(rain.get("wind", 0.0)))
    room.sync_all_windows()
    return room


def save_room(room: Room, path: str) -> None:
    room.sync_all_windows()
    data = {
        "name": room.name,
        "room": {
            "width": room.width,
            "height": room.height,
            "depth": room.depth,
            "terrain_size": room.terrain_size,
            "roof_material": room.roof_material,
            "wall_material": room.wall_material,
            "name": room.name,
        },
        "listener": {
            "x": room.listener.x,
            "y": room.listener.y,
            "z": room.listener.z,
            "yaw": room.listener.yaw,
            "pitch": room.listener.pitch,
            "roll": room.listener.roll,
            "audio_device": getattr(room.listener, "audio_device", None),
        },
        "windows": [
            {
                "name": w.name,
                "wall": w.wall,
                "offset": w.offset,
                "width": w.width,
                "height": w.height,
                "sill": w.sill,
                "open": w.open,
                "open_style": getattr(w, "open_style", "casement"),
                "max_angle_deg": getattr(w, "max_angle_deg", 75.0),
                "hinge_side": getattr(w, "hinge_side", "left"),
                "material": w.material,
                "custom_hinge": getattr(w, "custom_hinge", "left"),
                "custom_motion": getattr(w, "custom_motion", "swing"),
                "custom_outward": bool(getattr(w, "custom_outward", True)),
                "custom_notes": getattr(w, "custom_notes", ""),
                "free_place": bool(getattr(w, "free_place", False)),
                "free_x": float(getattr(w, "free_x", w.x)),
                "free_y": float(getattr(w, "free_y", w.sill + 0.5 * w.height)),
                "free_z": float(getattr(w, "free_z", w.z)),
                "x": w.x,
                "z": w.z,
            }
            for w in room.windows
        ],
        "speakers": [
            {
                "name": s.name,
                "x": s.x,
                "y": s.y,
                "z": s.z,
                "size": float(getattr(s, "size", 0.32)),
                "width": float(getattr(s, "width", 0.0) or 0.0),
                "height": float(getattr(s, "height", 0.0) or 0.0),
                "depth": float(getattr(s, "depth", 0.0) or 0.0),
                "material": s.material,
                "audio_device": s.audio_device,
                "gain_db": s.gain_db,
                "enabled": s.enabled,
                "notes": s.notes,
            }
            for s in room.speakers
        ],
        "headphones_items": [vars(hp) for hp in room.headphones_items],
        "headphones_mode": room.headphones_mode,
        "rain": {
            "intensity": room.rain_intensity,
            "wind": float(getattr(room, "wind", 0.0)),  # legacy signed EW
            "wind_speed": float(getattr(room, "wind_speed", 0.0)),
            "wind_direction_deg": float(getattr(room, "wind_direction_deg", 90.0)),
            "wind_vary_direction": bool(getattr(room, "wind_vary_direction", False)),
            "wind_dir_range_deg": float(getattr(room, "wind_dir_range_deg", 45.0)),
            "wind_dir_interval_s": float(getattr(room, "wind_dir_interval_s", 10.0)),
            "wind_dir_slew_deg_s": float(getattr(room, "wind_dir_slew_deg_s", 15.0)),
            "wind_vary_speed": bool(getattr(room, "wind_vary_speed", False)),
            "wind_speed_range": float(getattr(room, "wind_speed_range", 0.25)),
            "wind_speed_interval_s": float(getattr(room, "wind_speed_interval_s", 8.0)),
            "wind_speed_slew_per_s": float(getattr(room, "wind_speed_slew_per_s", 0.20)),
            "thunder": room.thunder,
            "droplet_density": room.droplet_density,
            "master_volume": float(getattr(room, "master_volume", 0.75)),
            "mix_wash": float(getattr(room, "mix_wash", MIX_WASH_RECOMMENDED)),
            "mix_droplets": float(getattr(room, "mix_droplets", MIX_DROPLETS_RECOMMENDED)),
            "mix_reverb": float(getattr(room, "mix_reverb", MIX_REVERB_RECOMMENDED)),
            "mix_wind": float(getattr(room, "mix_wind", MIX_WIND_RECOMMENDED)),
        },
        "master_volume": float(getattr(room, "master_volume", 0.75)),
        # Flat mix block for easy hand-edit / share (same values as rain.*)
        "mix": {
            "wash": float(getattr(room, "mix_wash", MIX_WASH_RECOMMENDED)),
            "droplets": float(getattr(room, "mix_droplets", MIX_DROPLETS_RECOMMENDED)),
            "reverb": float(getattr(room, "mix_reverb", MIX_REVERB_RECOMMENDED)),
            "wind": float(getattr(room, "mix_wind", MIX_WIND_RECOMMENDED)),
            "master": float(getattr(room, "master_volume", 0.75)),
            "quantity": float(getattr(room, "droplet_density", 0.55)),
            "sharpness": float(getattr(room, "rain_intensity", 0.35)),
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def new_default_room() -> Room:
    return default_house()
