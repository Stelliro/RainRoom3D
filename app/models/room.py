from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class Speaker:
    name: str
    x: float
    y: float
    z: float
    material: Optional[str] = None

@dataclass
class Headphones:
    name: str
    x: float
    y: float
    z: float
    material: Optional[str] = None

@dataclass
class Window:
    name: str
    x: float
    z: float
    width: float
    height: float
    open: float  # 0..1
    material: Optional[str] = "Glass Window"

@dataclass
class Listener:
    x: float
    y: float
    z: float
    yaw: float=0.0
    pitch: float=0.0
    roll: float=0.0

@dataclass
class Room:
    width: float
    height: float
    depth: float
    windows: List[Window] = field(default_factory=list)
    speakers: List[Speaker] = field(default_factory=list)
    headphones_items: List[Headphones] = field(default_factory=list)
    listener: Listener = field(default_factory=lambda: Listener(0.0,1.2,0.0))
    headphones_mode: bool = False  # must be True AND at least one headphones exists
    rain_intensity: float = 0.5
    wind: float = 0.0
    thunder: float = 0.1
    droplet_density: float = 0.5