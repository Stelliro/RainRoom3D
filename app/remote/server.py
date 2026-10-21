"""LAN server for the phone web app: static files + one WebSocket endpoint.

Standard library only (asyncio). Runs its own event loop on a daemon thread:

    hub = ZoneHub()
    srv = RemoteServer(hub)
    srv.start()                 # idempotent
    print(srv.urls())           # ["http://192.168.1.20:8765/?k=..."]
    srv.stop()                  # closes every socket, stops every zone output

* ``GET /`` (and any ``*.html``) needs ``?k=<pairing key>``; without it a
  friendly "scan the code again" page (403) is returned. Other static files
  (js / css / images) are served without the key; they hold no data.
* ``GET /ws`` upgrades to a WebSocket (RFC 6455). The first message must be
  ``hello`` with the key. Protocol: app/remote/PROTOCOL.md.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import socket
import string
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, unquote, urlsplit

from app.remote.zones import ZoneError, ZoneHub, sanitize_client_id

log = logging.getLogger("remote")

DEFAULT_PORT = 8765
PORT_TRIES = 20
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HEADER_BYTES = 16 * 1024
MAX_MESSAGE_BYTES = 256 * 1024
MAX_SEND_BUFFER = 4 * 1024 * 1024      # a client this far behind is cut off
HELLO_TIMEOUT_S = 15.0
PING_INTERVAL_S = 20.0
IDLE_TIMEOUT_S = 75.0
KEY_ALPHABET = string.ascii_letters + string.digits
PROTOCOL_VERSION = 1

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8", ".wav": "audio/wav", ".mp3": "audio/mpeg",
    ".map": "application/json; charset=utf-8",
}

_GATE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<title>RainRoom</title><style>
:root{color-scheme:dark;--bg:#0b0e13;--text:#e7ecf3;--text2:#a1abba;--accent:#4cb8ff;--soft:rgba(76,184,255,.14)}
@media (prefers-color-scheme:light){:root{color-scheme:light;--bg:#eef1f5;--text:#0f1620;--text2:#465163;
--accent:#0a6fc2;--soft:rgba(10,111,194,.10)}}
*{box-sizing:border-box}html,body{margin:0;height:100%%;background:var(--bg);color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,system-ui,sans-serif;font-size:15px;line-height:1.45}
body{display:grid;place-items:center;padding:24px}
main{max-width:380px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:12px}
.ic{width:76px;height:76px;border-radius:22px;display:grid;place-items:center;background:var(--soft);
color:var(--accent);margin-bottom:6px}.ic svg{width:38px;height:38px}
h1{margin:0;font-size:22px;letter-spacing:-.3px}p{margin:0;color:var(--text2)}
</style></head><body><main>
<div class="ic" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
stroke-linecap="round" stroke-linejoin="round">%(icon)s</svg></div>
<h1>%(title)s</h1>%(body)s
</main></body></html>"""

_ICON_LINK = ('<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/>'
              '<path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>')
_ICON_LOCK = ('<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>')


def gate_page(kind: str) -> bytes:
    """The page a browser gets without the right key: "nokey" (no key in the
    link) or "badkey" (an old or wrong link). Same look as the app's own
    notices, light or dark like the phone."""
    if kind == "badkey":
        d = {"icon": _ICON_LOCK, "title": "This link no longer works",
             "body": "<p>Someone made a new link on the computer, so this one stopped working.</p>"
                     "<p>On the PC, open RainRoom, choose <b>Phone</b>, and scan the code again.</p>"}
    else:
        d = {"icon": _ICON_LINK, "title": "Open the link from the PC",
             "body": "<p>On the PC, open RainRoom, choose <b>Phone</b>, and scan the code with this "
                     "phone's camera.</p><p>Or type the whole address it shows into this browser.</p>"}
    return (_GATE_PAGE % d).encode("utf-8")


def web_root() -> Path:
    """app/remote/web, from source or from a PyInstaller bundle."""
    cands = []
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        cands.append(Path(mei) / "app" / "remote" / "web")
    cands.append(Path(__file__).resolve().parent / "web")
    for c in cands:
        if c.is_dir():
            return c
    return cands[-1]


def new_key(n: int = 10) -> str:
    return "".join(secrets.choice(KEY_ALPHABET) for _ in range(n))


# ----- LAN addresses -----

def _ip_rank(ip: str) -> Optional[int]:
    try:
        a, b, _c, _d = (int(p) for p in ip.split("."))  # noqa: F841
    except ValueError:
        return None
    if a == 127 or (a == 169 and b == 254) or a == 0 or a >= 224:
        return None
    if a == 192 and b == 168:
        return 1 if _c == 56 else 0     # 192.168.56.x = VirtualBox host-only
    if a == 10:
        return 1
    if a == 172 and 16 <= b <= 31:
        return 2
    if a == 100 and 64 <= b <= 127:     # CGNAT / some VPNs: last resort
        return 4
    return 3                            # public address on the LAN card


def lan_ips() -> List[str]:
    """Best LAN IPv4 addresses first: 192.168.x, then 10.x, then 172.16-31.x;
    within a range the primary interface (UDP-connect trick toward the default
    route, nothing is sent) comes first. Range wins over "primary" because a
    VPN (often 10.x) or WSL / Hyper-V switch (172.x) can own the default route
    while the phone is on the home Wi-Fi. Loopback only if nothing else."""
    found: List[str] = []
    for target in ("8.8.8.8", "10.255.255.255", "192.168.255.255"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 1))
            ip = s.getsockname()[0]
            if ip not in found:
                found.append(ip)
        except OSError:
            pass
        finally:
            s.close()
    primary = found[0] if found else None
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    ranked = [(ip, _ip_rank(ip)) for ip in found]
    ranked = [(ip, r) for ip, r in ranked if r is not None]
    ranked.sort(key=lambda t: (t[1], 0 if t[0] == primary else 1))
    out = [ip for ip, _r in ranked]
    return out or ["127.0.0.1"]


# ----- WebSocket connection -----

class _WsClosed(Exception):
    pass


class WsConn:
    """One WebSocket. Every method runs on the server loop thread except
    ``post`` / ``post_json``, which may be called from any thread."""

    def __init__(self, server: "RemoteServer", reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter, peer: str):
        self.server = server
        self.reader = reader
        self.writer = writer
        self.peer = peer
        self.zone = None
        self.closed = False
        self.last_rx = time.monotonic()

    def backlog(self) -> int:
        """Bytes written but not yet sent (read from any thread; approximate)."""
        try:
            return int(self.writer.transport.get_write_buffer_size())
        except Exception:
            return 0

    # thread-safe senders
    def post(self, obj) -> None:
        """bytes -> binary message, dict -> JSON text message (thread-safe, in order)."""
        loop = self.server._loop
        if loop is None or self.closed:
            return
        try:
            loop.call_soon_threadsafe(self._send_now, obj)
        except RuntimeError:
            pass                                    # loop already closed

    def _send_now(self, obj) -> None:
        if self.closed:
            return
        if isinstance(obj, (bytes, bytearray, memoryview)):
            self._frame(0x2, bytes(obj))
        else:
            self._frame(0x1, json.dumps(obj, separators=(",", ":")).encode("utf-8"))

    def send(self, obj: Dict[str, Any]) -> None:
        self._send_now(obj)

    def _frame(self, opcode: int, payload: bytes) -> None:
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
        try:
            if self.backlog() > MAX_SEND_BUFFER:
                # not reading what we send: drop it instead of buffering forever
                log.warning("phone %s is not keeping up; disconnecting", self.peer)
                self.closed = True
                self.writer.transport.abort()
                return
            self.writer.write(head + payload)
        except Exception:
            self.closed = True

    async def read_message(self):
        """(opcode, payload) of the next whole data message; answers pings."""
        parts: List[bytes] = []
        first_op = None
        size = 0
        while True:
            b0, b1 = await self.reader.readexactly(2)
            fin, rsv, op = b0 & 0x80, b0 & 0x70, b0 & 0x0F
            masked, n = b1 & 0x80, b1 & 0x7F
            if rsv:
                raise _WsClosed(1002)
            if not masked:
                raise _WsClosed(1002)               # clients must mask
            if n == 126:
                n = struct.unpack("!H", await self.reader.readexactly(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            if op >= 0x8 and (n > 125 or not fin):
                raise _WsClosed(1002)
            if op < 0x8 and size + n > MAX_MESSAGE_BYTES:
                raise _WsClosed(1009)
            mask = await self.reader.readexactly(4)
            data = await self.reader.readexactly(n) if n else b""
            if n:
                data = _unmask(data, mask)
            self.last_rx = time.monotonic()
            if op == 0x8:                           # close
                code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else 1000
                raise _WsClosed(code)
            if op == 0x9:                           # ping -> pong
                self._frame(0xA, data)
                continue
            if op == 0xA:                           # pong
                continue
            if op == 0x0:
                if first_op is None:
                    raise _WsClosed(1002)
            elif op in (0x1, 0x2):
                if first_op is not None:
                    raise _WsClosed(1002)
                first_op = op
            else:
                raise _WsClosed(1002)
            parts.append(data)
            size += n
            if fin:
                return first_op, b"".join(parts)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed:
            return
        payload = struct.pack("!H", code) + reason.encode("utf-8")[:120]
        self._frame(0x8, payload)
        self.closed = True
        try:
            await asyncio.wait_for(self.writer.drain(), 2.0)
        except Exception:
            pass
        try:
            self.writer.close()
        except Exception:
            pass


def _unmask(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    m = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "little") ^ int.from_bytes(m, "little")).to_bytes(n, "little")


# ----- the server -----

class RemoteServer:
    def __init__(self, hub: ZoneHub, port: int = DEFAULT_PORT, host: str = "0.0.0.0",
                 key: Optional[str] = None, root: Optional[os.PathLike] = None,
                 server_name: Optional[str] = None):
        self.hub = hub
        self.want_port = int(port)
        self.host = host
        self.key = key or new_key()
        self.root = Path(root) if root is not None else web_root()
        self.server_name = server_name or socket.gethostname()
        self.port: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[asyncio.base_events.Server] = None
        self._conns: Set[WsConn] = set()
        self._ready = threading.Event()
        self._start_error: Optional[BaseException] = None
        self._lock = threading.Lock()
        self.hub.add_listener(self._on_hub_event)

    # ----- lifecycle -----
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self.port is not None

    def start(self) -> int:
        """Start (idempotent). Returns the port. Raises OSError if no port in
        port..port+19 can be bound."""
        with self._lock:
            if self.running:
                return int(self.port)
            self._ready.clear()
            self._start_error = None
            self._thread = threading.Thread(target=self._thread_main, name="RemoteServer", daemon=True)
            self._thread.start()
            self._ready.wait(10.0)
            if self._start_error is not None:
                err, self._start_error = self._start_error, None
                self._thread.join(2.0)
                self._thread = None
                raise err
            log.info("Phone server on port %s: %s", self.port, ", ".join(self.urls()))
            return int(self.port)

    def stop(self) -> None:
        """Close every socket, stop every zone render / device (idempotent)."""
        with self._lock:
            loop, th = self._loop, self._thread
            if loop is not None and th is not None and th.is_alive():
                fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
                try:
                    fut.result(5.0)
                except Exception:
                    log.exception("server shutdown")
                loop.call_soon_threadsafe(loop.stop)
                th.join(5.0)
            self._thread = None
            self._loop = None
            self.port = None
        self.hub.stop_all()

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._bind())
        except BaseException as e:
            self._start_error = e
            self._ready.set()
            loop.close()
            self._loop = None
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            loop.close()

    async def _bind(self) -> None:
        last: Optional[BaseException] = None
        tries = 1 if self.want_port == 0 else PORT_TRIES
        for p in range(self.want_port, self.want_port + tries):
            try:
                self._server = await asyncio.start_server(
                    self._handle, self.host, p, reuse_address=False, limit=MAX_HEADER_BYTES * 2)
                self.port = self._server.sockets[0].getsockname()[1]
                return
            except OSError as e:
                last = e
        raise OSError(f"No free port in {self.want_port}-{self.want_port + tries - 1}: {last}")

    async def _shutdown(self) -> None:
        if self._server is not None:
            self._server.close()
        for c in list(self._conns):
            if c.zone is not None:
                c.zone.detach(c.post)
            await c.close(1001, "RainRoom closed")
        self._conns.clear()
        if self._server is not None:
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2.0)
            except Exception:
                pass
            self._server = None

    # ----- pairing key -----
    def rotate_key(self, key: Optional[str] = None) -> str:
        """Switch to a new pairing key (random unless given) and disconnect
        every phone on the old link: each gets ``error{code:"bad_key"}`` and a
        1008 close. Zones keep their outputs (a PC output keeps playing).
        Blocks for at most ~5 s; call it off the UI thread."""
        self.key = key or new_key()
        loop = self._loop
        if loop is not None and self.running:
            fut = asyncio.run_coroutine_threadsafe(self._drop_all(
                "This link was replaced by a new one. Scan the new code on the computer."), loop)
            try:
                fut.result(5.0)
            except Exception:
                log.exception("rotate key")
        return self.key

    async def _drop_all(self, message: str) -> None:
        for c in list(self._conns):
            try:
                c.send({"t": "error", "code": "bad_key", "message": message})
            except Exception:
                pass
            z, c.zone = c.zone, None
            if z is not None:
                z.detach(c.post)
            await c.close(1008, "new link")
            self._conns.discard(c)

    # ----- URLs -----
    def url_for(self, host: str) -> str:
        return f"http://{host}:{self.port or self.want_port}/?k={self.key}"

    def lan_urls(self) -> List[str]:
        return [self.url_for(ip) for ip in lan_ips()]

    urls = lan_urls

    def connections(self) -> int:
        return len(self._conns)

    # ----- HTTP -----
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = "?"
        try:
            peer = str(writer.get_extra_info("peername")[0])
        except Exception:
            pass
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10.0)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError,
                ConnectionError):
            writer.close()
            return
        except Exception:
            writer.close()
            return
        if len(head) > MAX_HEADER_BYTES:
            await self._respond(writer, 431, b"Request header too large", "text/plain")
            return
        try:
            lines = head.decode("latin-1").split("\r\n")
            method, target, version = lines[0].split(" ", 2)
            headers: Dict[str, str] = {}
            for ln in lines[1:]:
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
        except ValueError:
            await self._respond(writer, 400, b"Bad request", "text/plain")
            return
        if method != "GET":
            await self._respond(writer, 405, b"Only GET is supported", "text/plain",
                                extra={"Allow": "GET"})
            return
        parts = urlsplit(target)
        path = parts.path or "/"
        query = parse_qs(parts.query)
        if path == "/ws":
            await self._websocket(reader, writer, headers, peer)
            return
        await self._static(writer, path, query)

    async def _respond(self, writer, status: int, body: bytes, ctype: str,
                       extra: Optional[Dict[str, str]] = None) -> None:
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
                  405: "Method Not Allowed", 426: "Upgrade Required",
                  431: "Request Header Fields Too Large", 500: "Internal Server Error"}.get(status, "OK")
        hdr = [f"HTTP/1.1 {status} {reason}", f"Content-Type: {ctype}",
               f"Content-Length: {len(body)}", "Cache-Control: no-cache",
               "X-Content-Type-Options: nosniff", "Referrer-Policy: no-referrer",
               "Connection: close"]
        for k, v in (extra or {}).items():
            hdr.append(f"{k}: {v}")
        try:
            writer.write(("\r\n".join(hdr) + "\r\n\r\n").encode("latin-1") + body)
            await asyncio.wait_for(writer.drain(), 10.0)
        except Exception:
            pass
        try:
            writer.close()
        except Exception:
            pass

    def key_ok(self, k) -> bool:
        return isinstance(k, str) and hmac.compare_digest(k.encode("utf-8"), self.key.encode("utf-8"))

    def resolve(self, url_path: str) -> Optional[Path]:
        """Map a URL path to a file under the web root, or None (traversal,
        hidden files, missing)."""
        p = unquote(url_path)
        if "\x00" in p or "\\" in p:
            return None
        if p.endswith("/"):
            p += "index.html"
        segs = [s for s in p.split("/") if s]
        if any(s in (".", "..") or s.startswith(".") or ":" in s for s in segs):
            return None
        root = self.root.resolve()
        f = root.joinpath(*segs).resolve() if segs else root / "index.html"
        try:
            f.relative_to(root)
        except ValueError:
            return None
        return f if f.is_file() else None

    async def _static(self, writer, path: str, query) -> None:
        f = self.resolve(path)
        if f is None:
            await self._respond(writer, 404, b"Not found", "text/plain; charset=utf-8")
            return
        ext = f.suffix.lower()
        k = (query.get("k") or [""])[0]
        if ext in (".html", ".htm") and not self.key_ok(k):
            await self._respond(writer, 403, gate_page("badkey" if k else "nokey"), CONTENT_TYPES[".html"])
            return
        ctype = CONTENT_TYPES.get(ext) or mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        try:
            body = await asyncio.get_running_loop().run_in_executor(None, f.read_bytes)
        except OSError:
            await self._respond(writer, 404, b"Not found", "text/plain; charset=utf-8")
            return
        await self._respond(writer, 200, body, ctype)

    # ----- WebSocket -----
    async def _websocket(self, reader, writer, headers: Dict[str, str], peer: str) -> None:
        key = headers.get("sec-websocket-key", "")
        if ("websocket" not in headers.get("upgrade", "").lower()
                or "upgrade" not in headers.get("connection", "").lower() or not key):
            await self._respond(writer, 426, b"WebSocket upgrade required", "text/plain",
                                extra={"Upgrade": "websocket"})
            return
        if headers.get("sec-websocket-version", "") != "13":
            await self._respond(writer, 426, b"WebSocket version 13 required", "text/plain",
                                extra={"Sec-WebSocket-Version": "13"})
            return
        try:
            raw = base64.b64decode(key, validate=True)
        except Exception:
            raw = b""
        if len(raw) != 16:
            await self._respond(writer, 400, b"Bad WebSocket key", "text/plain")
            return
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode("ascii"))
        try:
            sock = writer.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        conn = WsConn(self, reader, writer, peer)
        self._conns.add(conn)
        pinger = asyncio.ensure_future(self._pinger(conn))
        code, reason = 1000, ""
        try:
            await self._ws_loop(conn)
        except _WsClosed as e:
            code = int(e.args[0]) if e.args else 1000
            code = code if code in (1000, 1001, 1002, 1007, 1008, 1009, 1011) else 1000
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            code = 1001
        except Exception:
            log.exception("websocket handler")
            code = 1011
        finally:
            pinger.cancel()
            self._conns.discard(conn)
            if conn.zone is not None:
                conn.zone.detach(conn.post)
            await conn.close(code, reason)

    async def _pinger(self, conn: WsConn) -> None:
        try:
            while not conn.closed:
                await asyncio.sleep(PING_INTERVAL_S)
                if time.monotonic() - conn.last_rx > IDLE_TIMEOUT_S:
                    conn.writer.close()
                    return
                conn._frame(0x9, b"rr")
        except asyncio.CancelledError:
            pass

    async def _ws_loop(self, conn: WsConn) -> None:
        # hello first
        try:
            op, data = await asyncio.wait_for(conn.read_message(), HELLO_TIMEOUT_S)
        except asyncio.TimeoutError:
            conn.send({"t": "error", "code": "no_hello", "message": "Say hello first"})
            raise _WsClosed(1008)
        msg = _parse(op, data)
        if not msg or msg.get("t") != "hello":
            conn.send({"t": "error", "code": "no_hello", "message": "Say hello first"})
            raise _WsClosed(1008)
        if not self.key_ok(msg.get("key")):
            conn.send({"t": "error", "code": "bad_key",
                       "message": "This link's key is out of date. Scan the code on the computer again."})
            raise _WsClosed(1008)
        cid = sanitize_client_id(msg.get("client_id"))
        if cid is None:
            conn.send({"t": "error", "code": "bad_client_id",
                       "message": "This phone's id is not valid (letters, digits, - and _, up to 40)."})
            raise _WsClosed(1008)
        try:
            zone = await asyncio.get_running_loop().run_in_executor(None, self.hub.zone_for, cid)
        except ZoneError as e:
            conn.send({"t": "error", "code": e.code, "message": str(e)})
            raise _WsClosed(1008)
        for other in list(self._conns):
            if other is not conn and other.zone is zone:
                other.send({"t": "error", "code": "replaced",
                            "message": "This room was opened in another tab or on another phone."})
                other.zone = None
                zone.detach(other.post)
                await other.close(1008, "replaced")
                self._conns.discard(other)
        dn = msg.get("device_name")
        conn.zone = zone
        zone.attach(conn.post, dn.strip()[:60] if isinstance(dn, str) else "", backlog=conn.backlog)
        conn.send(self.state_message(zone))
        while True:
            op, data = await conn.read_message()
            if op != 0x1:
                continue                            # binary from the client: ignored
            msg = _parse(op, data)
            if msg is None:
                conn.send({"t": "error", "code": "bad_json", "message": "That message was not valid JSON"})
                continue
            try:
                await self._dispatch(conn, zone, msg)
            except ZoneError as e:
                conn.send({"t": "error", "code": e.code, "message": str(e), "for": msg.get("t")})
            except Exception:
                log.exception("phone message %r", msg.get("t"))
                conn.send({"t": "error", "code": "server", "message": "Something went wrong on the computer",
                           "for": msg.get("t")})

    def state_message(self, zone) -> Dict[str, Any]:
        hub = self.hub
        return {
            "t": "state", "protocol": PROTOCOL_VERSION, "client_id": zone.client_id,
            "zone": zone.room_json(), "thickness": zone.thickness(),
            "weather": hub.weather_message(), "weather_schema": hub.weather_schema(),
            "presets": hub.preset_names(), "preset_info": hub.preset_info(),
            "materials": hub.materials_info(), "devices": hub.devices(),
            "output": zone.output_info(), "volume": float(zone.room.master_volume),
            "server_name": self.server_name,
            "zone_new": bool(zone.fresh), "rooms": hub.saved_rooms(),
            "max_active": hub.max_active,
            "audio": {"sample_rate": 48000, "channels": 2, "frames": 2048, "format": "s16le",
                      "header_bytes": 12, "magic": 0x52524131,
                      "max_lead_ms": hub.max_lead_ms, "start_lead_ms": hub.start_lead_ms},
        }

    async def _dispatch(self, conn: WsConn, zone, msg: Dict[str, Any]) -> None:
        t = msg.get("t")
        loop = asyncio.get_running_loop()
        if t == "ack":
            zone.ack(msg.get("played"), msg.get("stream"))
        elif t == "ping":
            conn.send({"t": "pong", "time": time.time()})
        elif t == "set_weather":
            # broadcast happens through the hub "weather" event
            self.hub.weather_from_phone(msg.get("key"), msg.get("value"))
        elif t == "apply_preset":
            self.hub.apply_preset(msg.get("name"))
        elif t in ("set_room", "add_window", "set_window", "remove_window", "set_listener"):
            if t == "set_room":
                zone.set_room(msg.get("patch"))
            elif t == "add_window":
                zone.add_window(msg.get("wall"))
            elif t == "set_window":
                zone.set_window(msg.get("index"), msg.get("patch"))
            elif t == "remove_window":
                zone.remove_window(msg.get("index"))
            else:
                zone.set_listener(msg.get("x"), msg.get("z"), msg.get("yaw"), msg.get("y"))
            conn.send({"t": "zone", "room": zone.room_json(), "thickness": zone.thickness()})
            self.hub._emit("zones", self.hub.list_zones())
        elif t == "set_output":
            zone.set_output(msg.get("mode"), msg.get("device"))
        elif t == "set_volume":
            zone.set_volume(msg.get("value"))
            conn.send({"t": "volume", "value": float(zone.room.master_volume)})
        elif t == "refresh_devices":
            devs = await loop.run_in_executor(None, lambda: self.hub.devices(refresh=True))
            conn.send({"t": "devices", "devices": devs})
        elif t == "get_state":
            conn.send(self.state_message(zone))
        elif t == "list_rooms":
            rooms = await loop.run_in_executor(None, self.hub.saved_rooms)
            conn.send({"t": "rooms", "rooms": rooms, "current": zone.client_id})
        elif t == "forget_room":
            await loop.run_in_executor(None, self.hub.forget_room, msg.get("room_id"), zone.client_id)
            rooms = await loop.run_in_executor(None, self.hub.saved_rooms)
            conn.send({"t": "rooms", "rooms": rooms, "current": zone.client_id})
        elif t == "hello":
            conn.send({"t": "error", "code": "rejected", "message": "Already connected", "for": t})
        else:
            conn.send({"t": "error", "code": "unknown_type", "message": f"Unknown message {str(t)[:40]!r}",
                       "for": t if isinstance(t, str) else None})

    # ----- hub events -> phones -----
    def _on_hub_event(self, event: str, data) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            if event == "weather":
                msg = {"t": "weather", **data}
                loop.call_soon_threadsafe(self._broadcast, msg)
            elif event == "output":
                zone = data
                msg = {"t": "output", **zone.output_info()}
                loop.call_soon_threadsafe(self._to_zone, zone, msg)
        except RuntimeError:
            pass

    def _broadcast(self, msg) -> None:
        for c in list(self._conns):
            if c.zone is not None:
                c.send(msg)

    def _to_zone(self, zone, msg) -> None:
        for c in list(self._conns):
            if c.zone is zone:
                c.send(msg)


def _parse(op: int, data: bytes) -> Optional[Dict[str, Any]]:
    if op != 0x1:
        return None
    try:
        msg = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None                         # incl. absurdly deep nesting
    except Exception:
        return None
    return msg if isinstance(msg, dict) else None


__all__ = ["DEFAULT_PORT", "RemoteServer", "gate_page", "lan_ips", "new_key", "web_root"]
