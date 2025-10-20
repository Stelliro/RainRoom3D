import json
from app.models.room import Room, Window, Speaker, Headphones, Listener

def load_room(path: str) -> Room:
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    r = data.get('room', {})
    windows = [Window(**w) for w in data.get('windows', [])]
    speakers = [Speaker(**s) for s in data.get('speakers', [])]
    headphones_items = [Headphones(**hp) for hp in data.get('headphones_items', [])]
    l = data.get('listener', {})
    room = Room(
        width=r.get('width',4.0),
        height=r.get('height',2.6),
        depth=r.get('depth',3.2),
        windows=windows,
        speakers=speakers,
        headphones_items=headphones_items,
        listener=Listener(x=l.get('x',0.0), y=l.get('y',1.2), z=l.get('z',0.0),
                          yaw=l.get('yaw',0.0), pitch=l.get('pitch',0.0), roll=l.get('roll',0.0)),
        headphones_mode=data.get('headphones_mode', False),
        rain_intensity=data.get('rain', {}).get('intensity', 0.5),
        wind=data.get('rain', {}).get('wind', 0.0),
        thunder=data.get('rain', {}).get('thunder', 0.1),
        droplet_density=data.get('rain', {}).get('droplet_density', 0.5),
    )
    return room

def save_room(room: Room, path: str) -> None:
    data = {
        "room": {"width":room.width, "height":room.height, "depth":room.depth},
        "listener": {
            "x": room.listener.x, "y": room.listener.y, "z": room.listener.z,
            "yaw": room.listener.yaw, "pitch": room.listener.pitch, "roll": room.listener.roll
        },
        "windows": [vars(w) for w in room.windows],
        "speakers": [vars(s) for s in room.speakers],
        "headphones_items": [vars(hp) for hp in room.headphones_items],
        "headphones_mode": room.headphones_mode,
        "rain": {
            "intensity": room.rain_intensity,
            "wind": room.wind,
            "thunder": room.thunder,
            "droplet_density": room.droplet_density
        }
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)