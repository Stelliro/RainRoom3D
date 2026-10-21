"""Run the phone server without the desktop app (for building the web app).

    python -m app.remote.devserver [--port 8765] [--key KEY] [--house PATH]
                                   [--zones-dir DIR] [--no-qr] [--fake-pc]

The shared weather lives in a plain Room loaded READ-ONLY from
configs/my_house.json (or the default house if it is missing) - that file is
never written. A phone weather change is applied to that in-memory Room and
echoed back with hub.set_shared, the same round trip the desktop does.

Phone rooms are saved to a temp folder by default (not configs/zones), so
trying things out never touches the repo. Ctrl+C stops.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import threading
import time
from pathlib import Path

from app.models import settings_schema as schema
from app.remote import qr
from app.remote.server import DEFAULT_PORT, RemoteServer
from app.remote.zones import SHARED_KEYS, ZoneHub

_REPO = Path(__file__).resolve().parents[2]


def _load_house(path: Path):
    from app.utils.persistence import load_room, new_default_room
    if path.is_file():
        try:
            return load_room(str(path)), str(path)
        except Exception as e:
            print(f"Could not read {path} ({e}); using the default house")
    return new_default_room(), "default house"


class _FakePcEngine:
    """--fake-pc: "pc" output pretends to play (no device is opened)."""

    def __init__(self, room):
        from app.audio.spatial_engine import SpatialRainEngine
        self._real = SpatialRainEngine(room)
        self.running = False

    def __getattr__(self, name):
        return getattr(self._real, name)

    @property
    def room(self):
        return self._real.room

    @room.setter
    def room(self, r):
        self._real.room = r

    def start_headphones(self, headphones_device=None):
        print(f"[fake pc] would play on device {headphones_device}")
        self.running = True

    def stop(self):
        self.running = False

    def prepare_for_stream(self, seed: int = 3):
        return self._real.prepare_for_stream(seed)

    def render_listener_block(self, frames=None):
        return self._real.render_listener_block(frames)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="RainRoom phone server without the desktop UI")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--key", default=None, help="fixed pairing key (default: random per run)")
    ap.add_argument("--house", default=str(_REPO / "configs" / "my_house.json"),
                    help="house JSON the shared weather is read from (read-only)")
    ap.add_argument("--zones-dir", default=str(Path(tempfile.gettempdir()) / "rainroom_dev_zones"))
    ap.add_argument("--no-qr", action="store_true", help="don't print the QR code")
    ap.add_argument("--fake-pc", action="store_true", help="'pc' output doesn't open a real device")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)  # URL shows at once when piped
    except Exception:
        pass

    house, src = _load_house(Path(args.house))
    lock = threading.Lock()
    hub = ZoneHub(zones_dir=args.zones_dir, engine_factory=(_FakePcEngine if args.fake_pc else None))
    hub.sync_from_room(house)

    def from_phone(changes, preset_name):
        # Stand-in for the desktop: apply to "its" Room, then push back
        with lock:
            for k, v in changes.items():
                schema.set(house, k, v)
            hub.set_shared({k: getattr(house, k) for k in SHARED_KEYS}, preset_name)
        print(f"[weather from phone] {changes} preset={preset_name}")

    hub.on_weather_from_phone(from_phone)
    srv = RemoteServer(hub, port=args.port, key=args.key)
    srv.start()
    urls = srv.lan_urls()
    print()
    print(f"Shared weather from: {src} (read-only)")
    print(f"Phone rooms saved in: {args.zones_dir}")
    print(f"Local:  {srv.url_for('127.0.0.1')}")
    for u in urls:
        print(f"Phone:  {u}")
    if urls and not args.no_qr:
        print()
        print(qr.to_text(qr.encode(urls[0], "M"), border=2, invert=True))
    print("\nCtrl+C to stop.")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
        hub.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
