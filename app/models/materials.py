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

# What each role offers. The pane of a window is always glass; "Glass Wall"
# is a wall built of glass (a conservatory or studio).
ROOF_MATERIALS = ("Tin Roof", "Metal Roof", "Tile Roof", "Shingle Roof", "Wood Roof", "Glass Roof")
WALL_MATERIALS = ("Brick Wall", "Wood Wall", "Glass Wall", "Tin Wall")

# Acoustic key (as house_model.material_key reads a name) → the name in each role
_BY_KEY = {
    "roof": {"tin": "Tin Roof", "metal": "Metal Roof", "tile": "Tile Roof", "shingle": "Shingle Roof",
             "wood": "Wood Roof", "glass": "Glass Roof", "brick": "Tile Roof"},
    "wall": {"brick": "Brick Wall", "wood": "Wood Wall", "glass": "Glass Wall", "tin": "Tin Wall",
             "metal": "Tin Wall", "tile": "Brick Wall", "shingle": "Wood Wall"},
}
_KEYS = ("glass", "window", "tin", "metal", "tile", "shingle", "asphalt", "wood", "brick")


def role_material(name, role: str) -> str:
    """``name`` as one of the ``role`` ("roof" / "wall") options. Older names
    ("Glass Window" and "Wood" for walls, a wall material on the roof) map to
    the option that sounds the same."""
    options = ROOF_MATERIALS if role == "roof" else WALL_MATERIALS
    if name in options:
        return name
    low = str(name or "").lower()
    key = next((k for k in _KEYS if k in low), None)
    key = {"window": "glass", "asphalt": "shingle"}.get(key, key)
    if key is None:
        key = "metal" if role == "roof" else "brick"
    return _BY_KEY[role][key]
