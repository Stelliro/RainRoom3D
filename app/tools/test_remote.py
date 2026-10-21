"""
Checks the phone remote: QR encoder, LAN server (HTTP + WebSocket), shared
weather, phone rooms (validation + saving) and the audio stream.

Run:  python -m app.tools.test_remote

Headless (no Qt). Phone rooms are saved to a temp folder, never configs/zones.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

RESULTS = []


def _ok(name: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    extra = f" — {detail}" if detail else ""
    print(f"  [{mark}] {name}{extra}")
    RESULTS.append(bool(cond))
    return bool(cond)


# ----------------------------------------------------------------------------
# A tiny blocking WebSocket client (stdlib only)
# ----------------------------------------------------------------------------

class WsClient:
    def __init__(self, port: int, path: str = "/ws", timeout: float = 10.0):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("closed during handshake")
            buf += chunk
        head, self.buf = buf.split(b"\r\n\r\n", 1)
        self.status = head.split(b"\r\n")[0].decode()
        want = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode())
                                .digest()).decode()
        self.accept_ok = f"Sec-WebSocket-Accept: {want}".encode() in head
        self.closed_code = None

    def _raw(self, opcode: int, payload: bytes, fin: bool = True) -> None:
        mask = os.urandom(4)
        n = len(payload)
        b0 = (0x80 if fin else 0) | opcode
        if n < 126:
            head = struct.pack("!BB", b0, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", b0, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", b0, 0x80 | 127, n)
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    def send(self, obj) -> None:
        self._raw(0x1, json.dumps(obj).encode())

    def _need(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self, timeout: float = 5.0):
        """(opcode, payload) of the next frame; (None, None) on timeout."""
        self.sock.settimeout(timeout)
        try:
            b0, b1 = self._need(2)
        except socket.timeout:
            return None, None
        self.sock.settimeout(10.0)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._need(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._need(8))[0]
        data = self._need(n) if n else b""
        op = b0 & 0x0F
        if op == 0x8:
            self.closed_code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else 1005
        return op, data

    def recv_json(self, want=None, timeout: float = 8.0, keep_binary=None):
        """Next JSON message (of type ``want`` if given); binaries go to keep_binary."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            op, data = self.recv(max(0.05, end - time.monotonic()))
            if op is None:
                return None
            if op == 0x2:
                if keep_binary is not None:
                    keep_binary.append(data)
                continue
            if op == 0x8:
                return {"t": "_closed", "code": self.closed_code}
            if op == 0x1:
                msg = json.loads(data)
                if want is None or msg.get("t") == want:
                    return msg
        return None

    def close(self) -> None:
        try:
            self._raw(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def _http(port: int, raw: str):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(raw.encode("latin-1"))
    buf = b""
    while True:
        c = s.recv(65536)
        if not c:
            break
        buf += c
    s.close()
    head, _, body = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    hdrs = {ln.split(":", 1)[0].lower(): ln.split(":", 1)[1].strip() for ln in lines[1:] if ":" in ln}
    return status, hdrs, body


def _get(port: int, path: str):
    return _http(port, f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n")


def _rms_db(x: np.ndarray) -> float:
    return 20.0 * np.log10(max(1e-12, float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))))


# ----------------------------------------------------------------------------
# QR
# ----------------------------------------------------------------------------

# Matrix hash of encode(QR_URL, "M") — decoded by jsQR during development
# (version 3, ECC M). Any change to the encoder output must be re-verified.
QR_URL = "http://192.168.1.23:8765/?k=Ab3dE6gH9k"
QR_SHA256 = "bf43a098c04c32a3978d1fc33e091907ec6b233174d1ef24e33d1a863c111b76"


def _format_copies(m):
    n = len(m)
    a = [m[i][8] for i in range(6)] + [m[7][8], m[8][8], m[8][7]] + [m[8][14 - i] for i in range(9, 15)]
    b = [m[8][n - 1 - i] for i in range(8)] + [m[n - 15 + i][8] for i in range(8, 15)]
    return a, b


def test_qr() -> None:
    from app.remote import qr
    print("QR encoder")
    m = qr.encode(QR_URL, "M")
    h = hashlib.sha256("".join("1" if c else "0" for r in m for c in r).encode()).hexdigest()
    _ok("known URL matches the jsQR-verified matrix", h == QR_SHA256, f"v{qr.version_of(m)}")
    ok = True
    for text in (QR_URL, "http://10.0.0.5:8766/?k=Zx81Qw0pLm", "x" * 300, "a"):
        for ecc in "LMQH":
            for mask in (None, 0, 3, 7):
                m = qr.encode(text, ecc, mask=mask)
                n = len(m)
                v = qr.version_of(m)
                ok &= n == 4 * v + 17 and all(len(r) == n for r in m)
                ok &= qr.capacity_bytes(v, ecc) >= len(text.encode())
                # finders: dark ring, light ring, dark 3x3 centre
                for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
                    ok &= m[cy][cx] and m[cy - 3][cx - 3] and not m[cy - 2][cx - 2] and m[cy + 1][cx + 1]
                ok &= all(m[6][i] == (i % 2 == 0) for i in range(8, n - 8))
                ok &= all(m[i][6] == (i % 2 == 0) for i in range(8, n - 8))
                ok &= m[n - 8][8]
                a, b = _format_copies(m)
                ok &= a == b
    _ok("structure: size, finders, timing, dark module, both format copies agree", ok)
    try:
        qr.encode("x" * 3000, "M")
        too_big = False
    except ValueError:
        too_big = True
    _ok("too much data raises ValueError", too_big)
    _ok("capacity M v10 = 213 bytes (spec table)", qr.capacity_bytes(10, "M") == 213)


# ----------------------------------------------------------------------------
# Server fixtures
# ----------------------------------------------------------------------------

class FakeEngine:
    """Stands in for SpatialRainEngine where a real device would open."""
    started = []

    def __init__(self, room):
        self.room = room
        self.running = False

    def start_headphones(self, headphones_device=None):
        FakeEngine.started.append(headphones_device)
        self.running = True

    def stop(self):
        self.running = False

    def prepare_for_stream(self, seed=3):
        pass

    def render_listener_block(self, frames=None):
        return np.full((int(frames or 2048), 2), 0.1, dtype=np.float32)


def _devices():
    return [{"index": 5, "name": "Speakers (Realtek) Windows WASAPI", "is_default": True},
            {"index": 7, "name": "Bedroom TV", "is_default": False}]


def _connect(srv, cid, device_name="Test phone"):
    c = WsClient(srv.port)
    c.send({"t": "hello", "client_id": cid, "key": srv.key, "device_name": device_name})
    st = c.recv_json("state")
    return c, st


def test_http(tmp: Path) -> None:
    from app.remote.server import RemoteServer, lan_ips
    from app.remote.zones import ZoneHub
    print("HTTP static server")
    root = tmp / "web"
    (root / "js").mkdir(parents=True)
    (root / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    (root / "js" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (root / "style.css").write_text("body{}", encoding="utf-8")
    (root / ".hidden").write_text("no", encoding="utf-8")
    (tmp / "secret.txt").write_text("SECRET", encoding="utf-8")
    hub = ZoneHub(zones_dir=tmp / "zones_http", device_lister=_devices)
    srv = RemoteServer(hub, port=0, key="TESTKEY123", root=root)
    try:
        port = srv.start()
        _ok("start() binds a port and is idempotent", port > 0 and srv.start() == port)
        st, h, body = _get(port, "/?k=TESTKEY123")
        _ok("index with key -> 200 html, no-cache", st == 200 and h.get("content-type", "").startswith("text/html")
            and h.get("cache-control") == "no-cache" and body == b"<p>hi</p>")
        st, h, body = _get(port, "/")
        _ok("index without key -> 403 friendly page", st == 403 and b"Open the link from the PC" in body
            and b"prefers-color-scheme:light" in body and b"?k=" not in body)
        st, _h, body = _get(port, "/index.html?k=WRONG")
        _ok("wrong key -> 403 'no longer works' page", st == 403 and b"no longer works" in body)
        st, h, body = _get(port, "/js/app.js")
        _ok("js served with text/javascript", st == 200 and h.get("content-type", "").startswith("text/javascript"))
        st, h, _b = _get(port, "/style.css")
        _ok("css served with text/css", st == 200 and h.get("content-type", "").startswith("text/css"))
        bad = ["/../secret.txt", "/%2e%2e/secret.txt", "/js/..%2f..%2fsecret.txt", "/..%5csecret.txt",
               "/.hidden", "/js/../../secret.txt", "/C:%5cWindows%5cwin.ini", "/%00index.html"]
        leaks = []
        for p in bad:
            st, _h, body = _get(port, p)
            if st == 200 or b"SECRET" in body:
                leaks.append(p)
        _ok("directory traversal / hidden files rejected", not leaks, str(leaks) if leaks else f"{len(bad)} tries")
        st, _h, _b = _get(port, "/nope.js")
        _ok("missing file -> 404", st == 404)
        st, _h, _b = _http(port, "POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n")
        _ok("POST -> 405", st == 405)
        st, _h, _b = _get(port, "/ws")
        _ok("plain GET /ws -> 426", st == 426)
        ips = lan_ips()
        _ok("lan_ips() gives IPv4 addresses, no link-local", bool(ips) and all(
            ip.count(".") == 3 and not ip.startswith("169.254.") for ip in ips), ", ".join(ips))
        _ok("lan_urls() carry the key", all(u.endswith("/?k=TESTKEY123") for u in srv.lan_urls()))
    finally:
        srv.stop()
        srv.stop()
    _ok("stop() is idempotent and frees the server", not srv.running)


def test_ws_and_weather(tmp: Path) -> None:
    from app.remote.server import RemoteServer
    from app.remote.zones import SHARED_KEYS, ZoneHub
    from app.models import presets
    print("WebSocket, hello/state, shared weather")
    want = set(presets.WEATHER_KEYS) | set(presets.MIX_KEYS) | {"wind_direction_deg"} | {
        "tone_pitch", "tone_ring", "tone_wet", "tone_soft", "tone_weight"}
    _ok("SHARED_KEYS = weather + mix + tone_* + wind direction", set(SHARED_KEYS) == want)
    hub = ZoneHub(zones_dir=tmp / "zones_ws", engine_factory=FakeEngine, device_lister=_devices)
    got = []
    hub.on_weather_from_phone(lambda ch, p: got.append((ch, p)))
    srv = RemoteServer(hub, port=0, key="K3Y")
    srv.start()
    try:
        c = WsClient(srv.port)
        _ok("handshake 101 + correct Sec-WebSocket-Accept", c.status.startswith("HTTP/1.1 101") and c.accept_ok)
        c.send({"t": "hello", "client_id": "phoneA", "key": "nope"})
        m = c.recv_json()
        m2 = c.recv_json()
        _ok("hello with a wrong key -> error bad_key, then close", m and m.get("code") == "bad_key"
            and m2 and m2.get("t") == "_closed")
        c.close()
        c = WsClient(srv.port)
        c.send({"t": "set_volume", "value": 1})
        m = c.recv_json()
        _ok("message before hello -> error no_hello", m and m.get("code") == "no_hello")
        c.close()
        c = WsClient(srv.port)
        c.send({"t": "hello", "client_id": "../../etc", "key": "K3Y"})
        m = c.recv_json()
        _ok("unsafe client_id rejected", m and m.get("code") == "bad_client_id")
        c.close()

        a, st = _connect(srv, "phoneA", "Anna's phone")
        keys = ("zone", "weather", "weather_schema", "presets", "materials", "devices", "output",
                "volume", "server_name", "thickness", "audio")
        _ok("hello -> state with every field", st is not None and all(k in st for k in keys),
            "" if st else "no state")
        if st is None:
            return
        z = st["zone"]
        _ok("new zone = Bedroom 3.5 x 3.2 m, 2.5 m ceiling, 1 north window 50% open, no speakers",
            z["room"]["name"] == "Bedroom" and z["room"]["width"] == 3.5 and z["room"]["depth"] == 3.2
            and z["room"]["height"] == 2.5 and len(z["windows"]) == 1 and z["windows"][0]["wall"] == "north"
            and z["windows"][0]["open"] == 0.5 and z["speakers"] == [])
        _ok("devices cleaned for the phone", st["devices"][0] == {"index": 5, "name": "Speakers (Realtek)",
                                                                  "default": True})
        _ok("weather_schema = rain + wind + thunder groups",
            {s["group"] for s in st["weather_schema"]} == {"rain", "wind", "thunder"}
            and any(s["key"] == "droplet_density" and s["label"] == "Amount" for s in st["weather_schema"]))
        _ok("presets list built-ins", "Storm" in st["presets"] and "Drizzle" in st["presets"])
        _ok("output starts off", st["output"]["mode"] == "off" and st["output"]["playing"] is False)

        a.send({"t": "ping"})
        _ok("ping -> pong", (a.recv_json("pong") or {}).get("t") == "pong")
        # fragmented text message with a ping in between
        payload = json.dumps({"t": "set_volume", "value": 0.4}).encode()
        a._raw(0x1, payload[:7], fin=False)
        a._raw(0x9, b"hey")
        a._raw(0x0, payload[7:], fin=True)
        pong_seen = False
        vol = None
        end = time.monotonic() + 5
        while time.monotonic() < end and vol is None:
            op, data = a.recv(5)
            if op == 0xA and data == b"hey":
                pong_seen = True
            elif op == 0x1 and json.loads(data).get("t") == "volume":
                vol = json.loads(data)["value"]
        _ok("fragmented message + interleaved ping handled", pong_seen and vol == 0.4)
        a.send({"t": "set_volume", "value": "loud"})
        _ok("bad value -> error rejected (connection stays)",
            (a.recv_json("error") or {}).get("code") == "rejected")
        a._raw(0x1, b"{not json")
        _ok("bad JSON -> error bad_json", (a.recv_json("error") or {}).get("code") == "bad_json")
        a.send({"t": "fly_to_moon"})
        _ok("unknown type -> error unknown_type", (a.recv_json("error") or {}).get("code") == "unknown_type")

        b, stb = _connect(srv, "phoneB")
        a.send({"t": "set_weather", "key": "droplet_density", "value": 0.83})
        wa, wb = a.recv_json("weather"), b.recv_json("weather")
        _ok("set_weather on A broadcast to A and B",
            wa and wb and abs(wb["values"]["droplet_density"] - 0.83) < 1e-9)
        _ok("hub callback fired for the desktop", got and got[-1][0] == {"droplet_density": 0.83})
        a.send({"t": "set_weather", "key": "wind_speed", "value": 7})
        wb = b.recv_json("weather")
        _ok("weather values clamped (wind 7 -> 1.0)", wb and wb["values"]["wind_speed"] == 1.0)
        a.send({"t": "set_weather", "key": "master_volume", "value": 0.1})
        _ok("non-shared key rejected", (a.recv_json("error") or {}).get("code") == "rejected")
        b.send({"t": "apply_preset", "name": "Storm"})
        wa = a.recv_json("weather")
        _ok("apply_preset Storm -> weather{preset: Storm} to everyone",
            wa and wa["preset"] == "Storm" and wa["values"]["wind_vary_direction"] is True)
        _ok("hub callback got the preset", got and got[-1][1] == "Storm")
        # desktop -> phones
        changed = hub.set_shared({"droplet_density": 0.2, "mix_wind": 1.5})
        wb = None
        for _ in range(5):                  # skip broadcasts B hasn't read yet
            wb = b.recv_json("weather")
            if wb is None or wb["values"]["droplet_density"] == 0.2:
                break
        _ok("desktop set_shared -> phones get it", changed and wb and wb["values"]["droplet_density"] == 0.2
            and wb["values"]["mix_wind"] == 1.5)
        _ok("an unchanged echo is not re-broadcast", hub.set_shared({"droplet_density": 0.2}) is False)
        _ok("weather display text included", isinstance((wb or {}).get("display", {}).get("droplet_density"), str))
        # a second tab with the same id replaces the first
        a2, _st = _connect(srv, "phoneA")
        m = a.recv_json("error")
        _ok("same phone id in a new tab replaces the old tab", m and m.get("code") == "replaced")
        a2.close()
        b.close()
        a.close()
    finally:
        srv.stop()
        hub.close()


def test_zone_edits(tmp: Path) -> None:
    from app.remote.server import RemoteServer
    from app.remote.zones import ZoneHub
    from app.utils.persistence import load_room
    print("Phone room edits (validated + saved)")
    zdir = tmp / "zones_edit"
    hub = ZoneHub(zones_dir=zdir, engine_factory=FakeEngine, device_lister=_devices, save_delay_s=0.3)
    hub.set_house_defaults("Tin Roof", "Wood")
    srv = RemoteServer(hub, port=0, key="K")
    srv.start()
    try:
        c, st = _connect(srv, "edit-1")
        _ok("new zone takes the house materials", st["zone"]["room"]["roof_material"] == "Tin Roof"
            and st["zone"]["room"]["wall_material"] == "Wood Wall")  # old name → its wall option
        c.send({"t": "set_room", "patch": {"width": 100, "depth": 0.2, "height": 1, "name": "  Attic   room ",
                                           "roof_material": "Cheese", "wall_material": "Brick Wall",
                                           "roof_thickness": 25, "wall_thickness": 900, "bogus": 1}})
        z = c.recv_json("zone")
        r = z["room"]["room"]
        _ok("sizes clamped (30 m, 1.5 m, ceiling 2 m), name tidied", r["width"] == 30.0 and r["depth"] == 1.5
            and r["height"] == 2.0 and r["name"] == "Attic room")
        _ok("bad material ignored, good one applied", r["roof_material"] == "Tin Roof" and r["wall_material"] == "Brick Wall")
        _ok("thickness: 25 cm stored as 0.25 m, 900 cm ignored (desktop rule)",
            abs(r["roof_thickness"] - 0.25) < 1e-9 and r["wall_thickness"] == 0.0
            and z["thickness"]["roof_cm"] == 25.0)
        c.send({"t": "set_room", "patch": {"width": 4.0, "depth": 3.0, "height": 2.6, "roof_thickness": ""}})
        r = c.recv_json("zone")["room"]["room"]
        _ok("empty thickness = usual build (0)", r["roof_thickness"] == 0.0)
        c.send({"t": "add_window", "wall": "east"})
        z = c.recv_json("zone")
        _ok("add_window on the east wall", len(z["room"]["windows"]) == 2 and z["room"]["windows"][1]["wall"] == "east")
        c.send({"t": "set_window", "index": 1, "patch": {"offset": 50, "width": 9, "open": 3, "open_style": "awning",
                                                         "hinge_side": "up", "sill": 3.4, "height": 2.0}})
        w = c.recv_json("zone")["room"]["windows"][1]
        _ok("window kept inside its wall and under the ceiling",
            w["width"] <= 3.0 - 0.1 + 1e-9 and w["offset"] + w["width"] <= 3.0 + 1e-9 and w["open"] == 1.0
            and w["sill"] + w["height"] <= 2.6 + 1e-9 and w["open_style"] == "awning" and w["hinge_side"] == "left")
        c.send({"t": "set_window", "index": 9, "patch": {"open": 0}})
        _ok("bad window index -> error", (c.recv_json("error") or {}).get("code") == "rejected")
        c.send({"t": "remove_window", "index": 0})
        z = c.recv_json("zone")
        _ok("remove_window", len(z["room"]["windows"]) == 1 and z["room"]["windows"][0]["wall"] == "east")
        c.send({"t": "set_listener", "x": -5, "z": 2.0, "yaw": 90})
        L = c.recv_json("zone")["room"]["listener"]
        _ok("listener clamped inside, yaw degrees -> radians", abs(L["x"] - 0.15) < 1e-9 and L["z"] == 2.0
            and abs(L["yaw"] - np.pi / 2) < 1e-9)
        c.send({"t": "set_volume", "value": 0.33})
        c.recv_json("volume")
        time.sleep(1.2)
        f = zdir / "edit-1.json"
        ok = f.is_file()
        if ok:
            room = load_room(str(f))
            ok = (room.width == 4.0 and len(room.windows) == 1 and room.windows[0].wall == "east"
                  and abs(room.master_volume - 0.33) < 1e-9 and room.name == "Attic room")
        _ok("zone saved (debounced) to the injected dir", ok, str(f))
        _ok("nothing written to configs/zones by the test",
            not (Path(__file__).resolve().parents[2] / "configs" / "zones" / "edit-1.json").exists())
        c.close()
    finally:
        srv.stop()
        hub.close()
    # a new hub (= app restart) loads the saved room
    hub2 = ZoneHub(zones_dir=zdir, engine_factory=FakeEngine, device_lister=_devices)
    z = hub2.zone_for("edit-1")
    _ok("saved room comes back after a restart", z.room.name == "Attic room" and z.room.width == 4.0)
    hub2.close()


def test_pc_output(tmp: Path) -> None:
    from app.remote.server import RemoteServer
    from app.remote.zones import ZoneHub
    print("PC output (engine start mocked)")
    FakeEngine.started.clear()
    hub = ZoneHub(zones_dir=tmp / "zones_pc", engine_factory=FakeEngine, device_lister=_devices)
    srv = RemoteServer(hub, port=0, key="K")
    srv.start()
    try:
        c, _st = _connect(srv, "pc-1")
        c.send({"t": "set_output", "mode": "pc", "device": 99})
        _ok("unknown device -> error", (c.recv_json("error") or {}).get("code") == "rejected")
        c.send({"t": "set_output", "mode": "pc", "device": 7})
        msgs = [c.recv_json("output"), c.recv_json("output")]
        last = msgs[-1] or {}
        _ok("set_output pc accepted -> output{mode: pc, playing: true}",
            last.get("mode") == "pc" and last.get("device") == 7 and last.get("playing") is True)
        _ok("engine.start_headphones(headphones_device=7)", FakeEngine.started == [7])
        z = hub.get_zone("pc-1")
        hub.set_shared({"droplet_density": 0.9})
        _ok("shared weather pushed into a PC-output zone's room", abs(z.room.droplet_density - 0.9) < 1e-9)
        c.close()
        time.sleep(0.4)
        _ok("PC output keeps playing after the phone disconnects", z.mode == "pc" and z.engine.running
            and not z.connected)
        hub.stop_zone_output("pc-1")
        time.sleep(0.3)
        _ok("stop from the desktop side", z.mode == "off" and not z.engine.running)
        c, st = _connect(srv, "pc-1")
        c.send({"t": "set_output", "mode": "phone"})
        m = c.recv_json("output")
        c.send({"t": "set_output", "mode": "off"})
        end = time.monotonic() + 3
        while time.monotonic() < end:
            m = c.recv_json("output", timeout=1) or m
            if m and m.get("mode") == "off" and not m.get("starting"):
                break
        # "off" is announced at once; the stream is torn down on the zone's
        # worker right after, so give it a moment
        end = time.monotonic() + 3
        while z._stream is not None and time.monotonic() < end:
            time.sleep(0.05)
        _ok("switch phone -> off cleanly", m and m.get("mode") == "off" and z._stream is None)
        c.close()
    finally:
        srv.stop()
        hub.close()


def _collect(c: WsClient, seconds: float, ack: bool, frames=None, t0=None):
    """Receive audio for ``seconds``; when ``ack``, play back in real time."""
    frames = [] if frames is None else frames
    t0 = time.monotonic() if t0 is None else t0
    end = time.monotonic() + seconds
    last_ack = 0.0
    while time.monotonic() < end:
        op, data = c.recv(0.05)
        if op == 0x2:
            frames.append(data)
        now = time.monotonic()
        if ack and now - last_ack > 0.2:
            received = len(frames) * 2048
            played = min(received, int((now - t0) * 48000))
            c.send({"t": "ack", "played": played})
            last_ack = now
    return frames


def _decode(frames):
    ok, seqs, blocks = True, [], []
    for f in frames:
        magic, seq, start = struct.unpack("<III", f[:12])
        ok &= magic == 0x52524131 and len(f) == 12 + 2048 * 4 and start == seq * 2048
        seqs.append(seq)
        blocks.append(np.frombuffer(f[12:], dtype="<i2").reshape(-1, 2))
    pcm = np.concatenate(blocks) if blocks else np.zeros((0, 2), dtype=np.int16)
    return ok, seqs, pcm


def test_streaming(tmp: Path) -> None:
    from app.remote.server import RemoteServer
    from app.remote.zones import ZoneHub
    print("Phone audio stream (real engine)")
    hub = ZoneHub(zones_dir=tmp / "zones_stream", device_lister=_devices)
    hub.set_house_defaults("Metal Roof", "Brick Wall")
    srv = RemoteServer(hub, port=0, key="K")
    srv.start()
    try:
        a, _ = _connect(srv, "open-room")
        b, _ = _connect(srv, "closed-room")
        a.send({"t": "set_window", "index": 0, "patch": {"open": 1.0}})
        a.recv_json("zone")
        b.send({"t": "set_window", "index": 0, "patch": {"open": 0.0}})
        b.recv_json("zone")
        for c in (a, b):
            c.send({"t": "set_output", "mode": "phone"})
        sa = a.recv_json("stream", timeout=30)
        sb = b.recv_json("stream", timeout=30)
        _ok("stream start message (48 kHz, 2 ch, 2048 frames, s16le)",
            sa is not None and sa["sample_rate"] == 48000 and sa["channels"] == 2 and sa["frames"] == 2048
            and sb is not None)
        # no acks yet: the server must stop after the lead
        fa = _collect(a, 1.0, ack=False)
        lead_ms = len(fa) * 2048 / 48.0
        _ok("without acks the server stops at the lead (<= 600 ms)", 0 < lead_ms <= 600.0 + 1e-6,
            f"{len(fa)} blocks = {lead_ms:.0f} ms")
        fb = _collect(b, 0.05, ack=False)
        t0 = time.monotonic()
        fa = _collect(a, 0.0, ack=False, frames=fa)
        # now play both in real time for 2.5 s
        done = {}

        def run(c, frames, name):
            done[name] = _collect(c, 2.6, ack=True, frames=frames, t0=t0)

        ta = threading.Thread(target=run, args=(a, fa, "a"))
        tb = threading.Thread(target=run, args=(b, fb, "b"))
        ta.start(); tb.start(); ta.join(); tb.join()
        ok_a, seqs_a, pcm_a = _decode(done["a"])
        ok_b, seqs_b, pcm_b = _decode(done["b"])
        _ok(">= 1 s of audio received", len(pcm_a) >= 48000 and len(pcm_b) >= 48000,
            f"{len(pcm_a) / 48000:.2f} s / {len(pcm_b) / 48000:.2f} s")
        _ok("frame header + size correct, seq consecutive from 0",
            ok_a and ok_b and seqs_a == list(range(len(seqs_a))) and seqs_b == list(range(len(seqs_b))))
        da, db = _rms_db(pcm_a[24000:] / 32768.0), _rms_db(pcm_b[24000:] / 32768.0)
        _ok("audio is not silent", da > -60.0 and db > -70.0, f"open {da:.1f} dBFS, closed {db:.1f} dBFS")
        _ok("own room: open window louder than closed", da > db + 1.0, f"+{da - db:.1f} dB")
        z = hub.get_zone("open-room")
        stats = z.stream_stats()
        _ok("each playing room renders in its own worker process",
            stats.get("worker_pid") not in (None, os.getpid()) and
            stats.get("worker_pid") != (hub.get_zone("closed-room").stream_stats() or {}).get("worker_pid"),
            f"pids {stats.get('worker_pid')} / {(hub.get_zone('closed-room').stream_stats() or {}).get('worker_pid')}")
        _ok("lead stays bounded while playing", stats and stats["lead_ms"] <= 600.0 + 1e-6,
            f"{stats.get('lead_ms', 0):.0f} ms")
        rm = stats.get("render_ms") or [0]
        cpu = stats.get("cpu_ms") or [0]
        print(f"      render per 2048-frame block: median {np.median(rm):.1f} ms, max {np.max(rm):.1f} ms;"
              f" CPU mean {np.mean(cpu):.1f} ms (Windows counts thread CPU in 15.6 ms ticks;"
              f" block = 42.7 ms)")
        # stall: stop acking, nothing beyond the lead
        n0 = len(done["a"])
        sent0 = stats["sent"]
        played0 = stats["played"]
        more = _collect(a, 1.0, ack=False)
        lead_after = (sent0 + len(more) * 2048 - played0) / 48.0
        _ok("client stalls -> server stops sending after the lead", lead_after <= 600.0 + 1e-6,
            f"{len(more)} more blocks, lead {lead_after:.0f} ms")
        del n0
        a.close()
        time.sleep(0.5)
        _ok("socket closed -> phone stream stops", z._stream is None and z.mode == "off")
        time.sleep(0.5)
        _ok("…and its worker process ends", getattr(z.engine, "_proc", None) is None)
        b.close()
    finally:
        srv.stop()
        hub.close()


def test_rotate_key(tmp: Path) -> None:
    from app.remote.server import RemoteServer
    from app.remote.zones import ZoneHub
    print("New link (rotate_key)")
    hub = ZoneHub(zones_dir=tmp / "zones_rot", engine_factory=FakeEngine, device_lister=_devices)
    srv = RemoteServer(hub, port=0, key="OLDKEY1")
    srv.start()
    try:
        c = WsClient(srv.port)
        c.send({"t": "hello", "client_id": "rotphone", "key": "OLDKEY1"})
        st = c.recv_json("state")
        z = hub.get_zone("rotphone")
        z.set_output("pc", None).result(5)
        new = srv.rotate_key()
        m = c.recv_json("error")
        m2 = c.recv_json("_none_")          # skips other messages; returns at the close frame
        _ok("connected phone gets bad_key, then a 1008 close",
            st is not None and new != "OLDKEY1" and m and m.get("code") == "bad_key"
            and m2 and m2.get("t") == "_closed" and m2.get("code") == 1008, f"{m} {m2}")
        time.sleep(0.2)
        _ok("the zone is detached but its PC output keeps playing",
            not z.connected and z.mode == "pc" and srv.connections() == 0)
        c.close()
        c = WsClient(srv.port)
        c.send({"t": "hello", "client_id": "rotphone", "key": "OLDKEY1"})
        m = c.recv_json()
        _ok("the old key no longer works", m and m.get("code") == "bad_key")
        c.close()
        c = WsClient(srv.port)
        c.send({"t": "hello", "client_id": "rotphone", "key": new})
        m = c.recv_json("state")
        _ok("the new key works", m is not None and m.get("output", {}).get("mode") == "pc")
        c.close()
        st, _h, _b = _get(srv.port, f"/?k={new}")
        st_old, _h, _b = _get(srv.port, "/?k=OLDKEY1")
        _ok("page needs the new key", st == 200 and st_old == 403, f"{st} {st_old}")
    finally:
        srv.stop()
        hub.close()


class FastEngine(FakeEngine):
    """Renders instantly: shows whether pacing, not render speed, limits a stream."""

    def render_pcm16(self, frames=None):
        return bytes(int(frames or 2048) * 4)


def test_limits(tmp: Path) -> None:
    from app.remote.server import RemoteServer
    from app.remote.zones import ZoneHub
    print("Limits: busy, ids, deep JSON, caps, saved rooms")
    zdir = tmp / "zones_limits"
    hub = ZoneHub(zones_dir=zdir, engine_factory=FakeEngine, device_lister=_devices, save_delay_s=0.2,
                  max_active=2, max_loaded=4, max_files=5)
    srv = RemoteServer(hub, port=0, key="K")
    srv.start()
    try:
        # deep nesting must not kill the handler (RecursionError in json)
        c = WsClient(srv.port)
        c._raw(0x1, b"[" * 100000 + b"]" * 100000)
        m = c.recv_json()
        m2 = c.recv_json()
        _ok("deep JSON before hello -> no_hello + 1008, no crash",
            m and m.get("code") == "no_hello" and m2 and m2.get("code") == 1008, f"{m} {m2}")
        c.close()
        a, st = _connect(srv, "LimitA")
        _ok("client ids are lower-cased (one room per id on Windows)",
            st and st["client_id"] == "limita" and hub.get_zone("limita") is not None)
        a._raw(0x1, b"[" * 100000 + b"]" * 100000)
        _ok("deep JSON after hello -> bad_json, socket stays", (a.recv_json("error") or {}).get("code") == "bad_json")
        a.send({"t": "ping"})
        _ok("…still answering", (a.recv_json("pong") or {}).get("t") == "pong")
        a2, _st = _connect(srv, "limita")
        _ok("same id in other letter case = the same room (replaces the old tab)",
            (a.recv_json("error") or {}).get("code") == "replaced")
        a.close()
        _ok("new phone room: zone_new, nothing written until it is edited",
            st["zone_new"] is True and not (zdir / "limita.json").exists() and isinstance(st["rooms"], list))
        a2.send({"t": "set_room", "patch": {"name": "Attic"}})
        a2.recv_json("zone")
        time.sleep(0.6)
        _ok("…saved after the first edit", (zdir / "limita.json").is_file())
        b, stb = _connect(srv, "limitb")
        _ok("state lists saved rooms (another phone can pick Attic)",
            any(r["id"] == "limita" and r["name"] == "Attic" and r["in_use"] for r in stb["rooms"]))
        b.send({"t": "list_rooms"})
        rm = b.recv_json("rooms")
        _ok("list_rooms -> rooms", rm and rm["current"] == "limitb" and any(r["id"] == "limita" for r in rm["rooms"]))
        b.send({"t": "forget_room", "room_id": "limita"})
        _ok("forget_room refuses a room in use", (b.recv_json("error") or {}).get("code") == "rejected")
        # busy: max_active = 2
        c3, _ = _connect(srv, "limitc")
        for cl in (a2, b):
            cl.send({"t": "set_output", "mode": "pc", "device": 7})
        time.sleep(0.4)
        c3.send({"t": "set_output", "mode": "pc", "device": 7})
        e = c3.recv_json("error") or {}
        _ok("a third room playing at once -> error busy (max_active 2)",
            e.get("code") == "busy" and e.get("for") == "set_output", e.get("message", ""))
        b.send({"t": "set_output", "mode": "pc", "device": 5})
        time.sleep(0.4)
        _ok("…switching a room that already plays still works",
            hub.get_zone("limitb").mode == "pc" and hub.get_zone("limitb").device == 5)
        a2.close(); b.close(); c3.close()
        time.sleep(0.3)
        hub.stop_zone_output("limita"); hub.stop_zone_output("limitb")
        time.sleep(0.3)
        # caps: max_loaded 4 -> idle rooms are dropped (saved first); files <= 5
        for i in range(8):
            cl, _ = _connect(srv, f"flood{i}")
            cl.send({"t": "set_volume", "value": 0.5})
            cl.recv_json("volume")
            cl.close()
            time.sleep(0.05)
        time.sleep(0.8)
        n_files = len(list(zdir.glob("*.json")))
        _ok("rooms in memory stay capped (idle ones dropped)", len(hub.zones()) <= 4, str(len(hub.zones())))
        _ok("saved files stay capped (oldest unused pruned)", n_files <= 5, str(n_files))
        c4, _ = _connect(srv, "limitd")
        c4.send({"t": "forget_room", "room_id": "flood7"})
        rm = c4.recv_json("rooms")
        _ok("forget_room removes an unused saved room",
            rm and not any(r["id"] == "flood7" for r in rm["rooms"]) and not (zdir / "flood7.json").exists())
        c4.close()
    finally:
        srv.stop()
        hub.close()


def test_lying_acks(tmp: Path) -> None:
    from app.remote.server import RemoteServer
    from app.remote.zones import ZoneHub
    print("Flow control can't be fooled")
    hub = ZoneHub(zones_dir=tmp / "zones_lie", engine_factory=FastEngine, device_lister=_devices)
    srv = RemoteServer(hub, port=0, key="K")
    srv.start()
    try:
        c, _ = _connect(srv, "liar")
        c.send({"t": "set_output", "mode": "phone"})
        s = c.recv_json("stream", timeout=10)
        z = hub.get_zone("liar")
        t0 = time.monotonic()
        # ack far more than was ever sent, and never read the audio
        while time.monotonic() - t0 < 2.0:
            c.send({"t": "ack", "played": 1e15, "stream": s["stream"]})
            time.sleep(0.02)
        st = z.stream_stats()
        el = time.monotonic() - t0
        limit = 28800 + el * 48000 * 1.03 + 2 * 2048
        _ok("acks for frames never received can't make it render faster than real time",
            st["sent"] <= limit, f"{st['sent'] / 48000:.2f} s sent in {el:.2f} s")
        buf = 0
        for conn in list(srv._conns):
            buf = max(buf, conn.backlog())
        _ok("the socket's send buffer stays small", buf <= 1024 * 1024, f"{buf / 1024:.0f} KiB")
        c.close()
    finally:
        srv.stop()
        hub.close()


def test_desktop_not_starved(tmp: Path) -> None:
    """A phone pressing Play must not stall the desktop's own rain: the desktop
    renders in this process with the real mixer's 4-block lead while a phone
    room starts (drop bank build) and streams."""
    from app.audio.spatial_engine import SpatialRainEngine
    from app.models.room import default_house
    from app.remote.zones import ZoneHub
    print("Desktop playback while a phone starts")
    hub = ZoneHub(zones_dir=tmp / "zones_desk", device_lister=_devices)
    eng = SpatialRainEngine(default_house(), samplerate=48000, blocksize=2048)
    eng.prepare_for_stream()
    lead = 4 * 2048 / 48000
    gaps = []
    stop = [False]
    t0 = time.perf_counter()

    def desk():
        t_dev = time.perf_counter() + lead
        while not stop[0]:
            eng.render_listener_block(2048)
            now = time.perf_counter()
            if now > t_dev:
                gaps.append((now - t0, (now - t_dev) * 1000.0))
                t_dev = now
            t_dev += 2048 / 48000
            d = t_dev - lead - time.perf_counter()
            if d > 0:
                time.sleep(d)

    th = threading.Thread(target=desk)
    th.start()
    try:
        time.sleep(1.5)
        z = hub.zone_for("desk-starter")
        got = []
        z.attach(got.append)
        t_play = time.perf_counter() - t0
        z.set_output("phone")
        end = time.monotonic() + 8.0
        while time.monotonic() < end and not z.playing:
            time.sleep(0.05)
        ts = time.monotonic()
        while time.monotonic() - ts < 2.0:          # a phone acking in real time
            n = sum(1 for m in got if isinstance(m, bytes)) * 2048
            z.ack(min(n, int((time.monotonic() - ts) * 48000)))
            time.sleep(0.1)
    finally:
        stop[0] = True
        th.join(10)
        hub.close()
    after = [(t, ms) for t, ms in gaps if t > t_play]
    short = sum(ms for _t, ms in after)
    _ok("desktop rain keeps playing while a phone room starts", short < 40.0,
        f"{len(after)} underruns, {short:.0f} ms short in total")
def main() -> int:
    t_start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="rr_remote_") as d:
        tmp = Path(d)
        os.environ.setdefault("RAINROOM_PRESET_DIR", str(tmp / "presets"))
        from app.models import presets
        presets.PRESET_DIR = tmp / "presets"   # never read/write the user's presets
        test_qr()
        test_http(tmp)
        test_ws_and_weather(tmp)
        test_rotate_key(tmp)
        test_zone_edits(tmp)
        test_pc_output(tmp)
        test_limits(tmp)
        test_lying_acks(tmp)
        test_streaming(tmp)
        test_desktop_not_starved(tmp)
    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed in {time.monotonic() - t_start:.1f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
