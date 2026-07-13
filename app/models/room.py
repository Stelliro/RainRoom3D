
"""Room / house layout model for RainRoom3D.

Coordinates are meters. Origin is the SW corner of the floor plan (x right, z up on the
top-down map). The house is a rectangular footprint on a larger outdoor terrain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# Wall names: north = +Z, south = -Z, east = +X, west = -X
WALLS = ("north", "south", "east", "west")


@dataclass
class Speaker:
    """Physical output mapped to a point inside the house."""
    name: str
    x: float
    y: float
    z: float
    # Visual / placement size (cube edge length in meters) — editable in 3D
    size: float = 0.32
    material: Optional[str] = None
    audio_device: Optional[int] = None  # sounddevice device index
    gain_db: float = 0.0
    enabled: bool = True
    notes: str = ""


@dataclass
class Headphones:
    name: str
    x: float
    y: float
    z: float
    material: Optional[str] = None


# How the sash/vent opens — each style has different acoustic behaviour
WINDOW_OPEN_STYLES = (
    "slider",      # horizontal slide — vertical gap grows with open %
    "casement",    # side-hinged, swings out — angled baffle + crack
    "awning",      # top-hinged, tilts out at bottom — scoops low outdoor rain
    "hopper",      # bottom-hinged, tilts in at top — favours high/air rain
    "sash",        # vertical slide — horizontal strip aperture
    "pivot",       # centre pivot — wide mid opening
    "tilt_turn",   # tilt (hopper-like) then turn (casement-like)
    "custom",      # user-defined hinge + motion
)

WINDOW_STYLE_LABELS = {
    "slider": "Slider (side gap)",
    "casement": "Casement (side hinge)",
    "awning": "Awning (top hinge)",
    "hopper": "Hopper (bottom hinge)",
    "sash": "Sash (up/down)",
    "pivot": "Pivot (centre)",
    "tilt_turn": "Tilt & turn",
    "custom": "Custom (design your own)",
}

# Custom open design options
CUSTOM_HINGES = ("left", "right", "top", "bottom", "center")
CUSTOM_MOTIONS = ("swing", "slide", "tilt", "fixed")


@dataclass
class Window:
    """Exterior opening. Anchored to a wall; geometry drives acoustics.

    Coordinates:
      wall / offset / width — plan placement on the wall
      sill                 — bottom of glass above floor (m)
      height               — vertical size of the glass (m)
      open                 — 0 closed .. 1 fully open
      open_style           — how it opens (slider, casement, awning, …)
      max_angle_deg        — max swing/tilt for hinged styles
    """
    name: str
    wall: str = "north"          # north/south/east/west
    offset: float = 0.5          # along-wall position from west (for N/S) or south (for E/W)
    width: float = 1.0           # horizontal size along the wall (m)
    height: float = 1.2          # vertical size of opening (m)
    sill: float = 0.9            # sill height above floor (m)
    open: float = 0.7            # 0 closed .. 1 fully open
    open_style: str = "casement"
    max_angle_deg: float = 75.0  # hinged styles: open * max_angle
    hinge_side: str = "left"     # casement / tilt_turn: left|right along wall
    material: Optional[str] = "Glass Window"
    # Custom open design (when open_style == "custom")
    custom_hinge: str = "left"       # left|right|top|bottom|center
    custom_motion: str = "swing"     # swing|slide|tilt|fixed
    custom_outward: bool = True      # swing/tilt direction (out vs in)
    custom_notes: str = ""           # free text description
    # Legacy fields kept for older render paths / JSON
    x: float = 0.0
    z: float = 0.0

    def open_style_norm(self) -> str:
        s = (self.open_style or "casement").lower().strip().replace("-", "_").replace(" ", "_")
        if s not in WINDOW_OPEN_STYLES:
            return "casement"
        return s

    def open_amount(self) -> float:
        return max(0.0, min(1.0, float(self.open)))

    def open_angle_deg(self) -> float:
        """Effective swing/tilt angle for hinged styles."""
        return self.open_amount() * max(5.0, float(self.max_angle_deg))

    def resolved_style_for_draw(self) -> str:
        """Map custom design onto a concrete open animation style for 3D."""
        style = self.open_style_norm()
        if style != "custom":
            return style
        motion = (self.custom_motion or "swing").lower()
        hinge = (self.custom_hinge or "left").lower()
        if motion == "slide":
            return "sash" if hinge in ("top", "bottom") else "slider"
        if motion == "tilt":
            if hinge == "top":
                return "awning"
            if hinge == "bottom":
                return "hopper"
            return "awning"
        if motion == "fixed":
            return "slider"  # flat gap visual only
        # swing
        if hinge == "center":
            return "pivot"
        if hinge == "top":
            return "awning"
        if hinge == "bottom":
            return "hopper"
        return "casement"

    def resolved_hinge_side(self) -> str:
        if self.open_style_norm() == "custom":
            h = (self.custom_hinge or "left").lower()
            if h in ("left", "right"):
                return h
            return (self.hinge_side or "left").lower()
        return (self.hinge_side or "left").lower()

    def aperture_area_m2(self) -> float:
        return max(0.05, float(self.width) * float(self.height))

    def glass_y_range(self) -> Tuple[float, float]:
        """(sill, head) absolute heights of the glass rectangle."""
        y0 = max(0.0, float(self.sill))
        y1 = y0 + max(0.15, float(self.height))
        return y0, y1

    def effective_gap_y_range(self) -> Tuple[float, float]:
        """Vertical band of the *air gap* when partially open (style-dependent)."""
        y0, y1 = self.glass_y_range()
        h = y1 - y0
        o = self.open_amount()
        style = self.resolved_style_for_draw()
        if o <= 0.001:
            # sealed — no real gap; leak uses full glass for muffled path
            return y0, y1
        if style == "awning":
            # gap opens from the sill upward
            return y0, y0 + h * (0.12 + 0.55 * o)
        if style == "hopper":
            # gap opens from the head downward
            return y1 - h * (0.12 + 0.55 * o), y1
        if style == "sash":
            # bottom sash rises → opening at bottom
            return y0, y0 + h * max(0.08, o)
        if style == "pivot":
            # centre band grows
            mid = 0.5 * (y0 + y1)
            half = 0.5 * h * (0.2 + 0.8 * o)
            return mid - half, mid + half
        if style == "tilt_turn":
            # mostly hopper-like until half open, then casement-like full height
            if o < 0.45:
                t = o / 0.45
                return y1 - h * (0.1 + 0.4 * t), y1
            return y0, y1
        # slider / casement: full glass height available
        return y0, y1


@dataclass
class Listener:
    x: float
    y: float
    z: float
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    # OS output for binaural “You” / headphones (None = system default)
    audio_device: Optional[int] = None


@dataclass
class Room:
    """House footprint + outdoor terrain + audio layout."""
    width: float
    height: float
    depth: float
    terrain_size: float = 40.0
    roof_material: str = "Metal Roof"
    wall_material: str = "Brick Wall"
    windows: List[Window] = field(default_factory=list)
    speakers: List[Speaker] = field(default_factory=list)
    headphones_items: List[Headphones] = field(default_factory=list)
    listener: Listener = field(default_factory=lambda: Listener(2.0, 1.2, 1.6))
    headphones_mode: bool = False
    rain_intensity: float = 0.35   # sharpness (soft→crisp), not drop count
    thunder: float = 0.1
    droplet_density: float = 0.55  # quantity — how many drops / s
    master_volume: float = 0.75    # 0..1 user listen level (1 = calibrated full scale)
    name: str = "My House"

    # --- Wind (rain is blown TOWARD this direction) ---
    # 0° = North (+Z), 90° = East (+X), 180° = South (−Z), 270° = West (−X)
    wind_speed: float = 0.0              # 0..1
    wind_direction_deg: float = 90.0     # base heading (degrees)

    # Optional automatic variation (toggles + ranges)
    wind_vary_direction: bool = False
    wind_dir_range_deg: float = 45.0     # ± degrees around base
    wind_dir_interval_s: float = 10.0    # how often a new direction target is chosen
    wind_dir_slew_deg_s: float = 15.0    # max turn rate toward target (°/s)

    wind_vary_speed: bool = False
    wind_speed_range: float = 0.25       # ± of base speed (0..1 units)
    wind_speed_interval_s: float = 8.0   # how often a new speed target is chosen
    wind_speed_slew_per_s: float = 0.20  # max speed change rate (units/s)

    # ------------------------------------------------------------------
    # Wind helpers
    # ------------------------------------------------------------------
    def set_legacy_wind(self, signed_ew: float) -> None:
        """Map old −1..+1 east-west wind onto speed + direction."""
        v = float(signed_ew)
        self.wind_speed = max(0.0, min(1.0, abs(v)))
        if abs(v) > 1e-6:
            self.wind_direction_deg = 90.0 if v >= 0.0 else 270.0

    @property
    def wind(self) -> float:
        """Legacy signed east component (−1..+1) for older callers."""
        return float(self.wind_speed) * math.sin(math.radians(float(self.wind_direction_deg)))

    @wind.setter
    def wind(self, v: float) -> None:
        self.set_legacy_wind(v)

    @staticmethod
    def wind_push_xz(speed: float, direction_deg: float) -> Tuple[float, float]:
        """Unitless push vector (wx, wz): rain is blown toward this heading."""
        sp = max(0.0, min(1.0, float(speed)))
        rad = math.radians(float(direction_deg) % 360.0)
        # 0° → +Z (north), 90° → +X (east)
        return sp * math.sin(rad), sp * math.cos(rad)

    def base_wind_push_xz(self) -> Tuple[float, float]:
        return self.wind_push_xz(self.wind_speed, self.wind_direction_deg)

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    def center(self) -> Tuple[float, float]:
        return self.width * 0.5, self.depth * 0.5

    def contains_point(self, x: float, z: float, margin: float = 0.0) -> bool:
        return (margin <= x <= self.width - margin) and (margin <= z <= self.depth - margin)

    def clamp_inside(self, x: float, z: float, margin: float = 0.15) -> Tuple[float, float]:
        return (
            max(margin, min(self.width - margin, x)),
            max(margin, min(self.depth - margin, z)),
        )

    def sync_window_coords(self, win: Window) -> None:
        """Write legacy x/z from wall + offset for GL / older code."""
        wall = (win.wall or "north").lower()
        off = max(0.0, min(win.offset, _wall_length(self, wall) - win.width))
        win.offset = off
        if wall == "north":
            win.x = off
            win.z = self.depth
        elif wall == "south":
            win.x = off
            win.z = 0.0
        elif wall == "east":
            win.x = self.width
            win.z = off
        else:  # west
            win.x = 0.0
            win.z = off

    def sync_all_windows(self) -> None:
        for w in self.windows:
            self.sync_window_coords(w)

    def window_center(self, win: Window) -> Tuple[float, float, float]:
        """World center of the *effective open gap* (inside face)."""
        self.sync_window_coords(win)
        gy0, gy1 = win.effective_gap_y_range()
        y = 0.5 * (gy0 + gy1)
        # Lateral centre: casement/slider may bias toward the open edge
        lat = win.offset + win.width * 0.5
        style = win.open_style_norm()
        o = win.open_amount()
        if style in ("casement", "tilt_turn", "slider") and o > 0.02:
            # Opening concentrates near hinge-opposite edge as it opens
            side = (getattr(win, "hinge_side", "left") or "left").lower()
            if style == "slider":
                # sliding panel reveals gap at one end
                edge = win.offset + (win.width * (1.0 - 0.5 * o) if side == "left"
                                    else win.width * (0.5 * o))
                lat = edge
            else:
                # casement swings — acoustic gap along free edge
                if side == "left":
                    lat = win.offset + win.width * (0.15 + 0.7 * (1.0 - 0.5 * o))
                else:
                    lat = win.offset + win.width * (0.85 - 0.7 * (1.0 - 0.5 * o))
        wall = (win.wall or "north").lower()
        if wall == "north":
            return lat, y, self.depth
        if wall == "south":
            return lat, y, 0.0
        if wall == "east":
            return self.width, y, lat
        return 0.0, y, lat

    def window_exterior_point(self, win: Window, out_dist: float = 0.35) -> Tuple[float, float, float]:
        """Point just outside the glass — rain source near the window.

        Hinged styles push the virtual outdoor source slightly with open angle
        so the 'mouth' of the opening is not flush with the wall.
        """
        cx, cy, cz = self.window_center(win)
        wall = (win.wall or "north").lower()
        style = win.open_style_norm()
        o = win.open_amount()
        # Awning/casement stick out more when open
        if style in ("awning", "casement", "tilt_turn", "pivot"):
            ang = math.radians(win.open_angle_deg())
            out_dist = out_dist + 0.35 * math.sin(ang) * max(0.15, win.height * 0.4)
        elif style == "slider":
            out_dist = out_dist + 0.05 * o
        if wall == "north":
            return cx, cy, cz + out_dist
        if wall == "south":
            return cx, cy, cz - out_dist
        if wall == "east":
            return cx + out_dist, cy, cz
        return cx - out_dist, cy, cz

    def outdoor_rain_sources(self) -> List[Tuple[str, float, float, float, float]]:
        """Sampling anchors for outdoor rain: (kind, x, y, z, weight)."""
        sources: List[Tuple[str, float, float, float, float]] = []
        # Roof plane
        sources.append(("roof", self.width * 0.5, self.height + 0.05, self.depth * 0.5, 1.0))
        # Corners of roof for spread
        for fx, fz in ((0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8)):
            sources.append(("roof", self.width * fx, self.height + 0.05, self.depth * fz, 0.55))
        # Windows
        for w in self.windows:
            ex, ey, ez = self.window_exterior_point(w)
            weight = 0.4 + 1.4 * max(0.0, min(1.0, w.open))
            sources.append(("window", ex, ey, ez, weight))
        # Ground just outside each wall (ambient outdoor)
        margin = 0.8
        sources.append(("ground", self.width * 0.5, 0.0, self.depth + margin, 0.35))
        sources.append(("ground", self.width * 0.5, 0.0, -margin, 0.35))
        sources.append(("ground", self.width + margin, 0.0, self.depth * 0.5, 0.35))
        sources.append(("ground", -margin, 0.0, self.depth * 0.5, 0.35))
        return sources

    def assigned_speakers(self) -> List[Speaker]:
        return [
            s for s in self.speakers
            if s.enabled and getattr(s, "audio_device", None) is not None
        ]


def _wall_length(room: Room, wall: str) -> float:
    wall = wall.lower()
    if wall in ("north", "south"):
        return room.width
    return room.depth


def default_house() -> Room:
    """Starter layout: modest room with two windows and two speakers."""
    room = Room(
        width=5.0,
        height=2.6,
        depth=4.0,
        terrain_size=36.0,
        roof_material="Metal Roof",
        wall_material="Brick Wall",
        name="Living Room",
        rain_intensity=0.35,
        droplet_density=0.55,
        listener=Listener(x=2.5, y=1.2, z=2.0),
    )
    room.windows = [
        Window(
            name="North Window", wall="north", offset=1.5, width=1.4, height=1.3,
            sill=0.9, open=0.85, open_style="casement", max_angle_deg=70.0, hinge_side="left",
        ),
        Window(
            name="East Window", wall="east", offset=1.0, width=1.1, height=1.15,
            sill=1.0, open=0.45, open_style="awning", max_angle_deg=45.0,
        ),
    ]
    room.sync_all_windows()
    # Three listening positions, spaced around the room for even spatial coverage
    room.speakers = [
        Speaker(name="North", x=2.5, y=1.1, z=3.5, notes="Near north window"),
        Speaker(name="East", x=4.2, y=1.1, z=2.0, notes="Near east wall / window"),
        Speaker(name="South-West", x=1.0, y=1.1, z=0.9, notes="Opposite corner"),
    ]
    room.headphones_items = [
        Headphones(name="You", x=2.5, y=1.5, z=2.0),
    ]
    room.listener = Listener(x=2.5, y=1.2, z=2.0)
    return room


def place_speakers_evenly(room: Room, count: int = 3, y: float = 1.1) -> None:
    """Replace speakers with evenly spaced listening points around the room."""
    count = max(1, min(8, int(count)))
    cx, cz = room.width * 0.5, room.depth * 0.5
    # Radius ~ 35% of half-diagonal so points stay inside
    rx = max(0.4, room.width * 0.32)
    rz = max(0.4, room.depth * 0.32)
    room.speakers.clear()
    labels = ["A", "B", "C", "D", "E", "F", "G", "H"]
    for i in range(count):
        ang = -math.pi * 0.5 + (2.0 * math.pi * i / count)  # start toward north
        x = cx + rx * math.cos(ang)
        z = cz + rz * math.sin(ang)
        x = max(0.25, min(room.width - 0.25, x))
        z = max(0.25, min(room.depth - 0.25, z))
        room.speakers.append(
            Speaker(
                name=f"Speaker {labels[i]}",
                x=x, y=y, z=z,
                notes=f"Even layout {i+1}/{count}",
            )
        )
