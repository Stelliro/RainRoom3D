from dataclasses import dataclass

@dataclass
class Material:
    name: str
    roughness: float  # 0..1, visual cue
    absorption: float # 0..1, acoustic absorption (higher = more damped)
    brightness: float # spectral tilt for impact sound (higher = brighter)

MATERIAL_PRESETS = [
    Material("Tin Roof", roughness=0.25, absorption=0.12, brightness=0.95),
    Material("Tile Roof", roughness=0.5, absorption=0.25, brightness=0.7),
    Material("Metal Roof", roughness=0.3, absorption=0.15, brightness=0.9),
    Material("Shingle Roof", roughness=0.6, absorption=0.4, brightness=0.6),
    Material("Glass Window", roughness=0.1, absorption=0.05, brightness=1.0),
    Material("Brick Wall", roughness=0.7, absorption=0.45, brightness=0.5),
    Material("Wood", roughness=0.5, absorption=0.35, brightness=0.6),
]