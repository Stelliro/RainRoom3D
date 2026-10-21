"""Phone-room renders in their own process.

Every playing phone room gets one worker process with its own
SpatialRainEngine, so phone rooms never compete with the desktop's own
playback for Python's GIL, and building a room's drop bank doesn't stall the
desktop's speakers. Workers run at below-normal priority, so when the CPU is
busy the desktop's own rain wins.

Parent side: ``ProcessEngine`` has the small engine API ``Zone`` uses
(``room``, ``prepare_for_stream``, ``render_pcm16`` / ``render_listener_block``,
``start_headphones``, ``stop``, ``running``) plus ``sync_room`` (push edits to a
worker playing on a PC device) and ``release`` (end the process).

Child side: ``serve()``. Started as ``python -m app.remote.worker`` from source,
or as ``RainRoom3D.exe --rainroom-zone-worker`` from a frozen build (app/main.py
hands that flag over before Qt is imported).

Wire format, both directions, over a localhost TCP socket that only the
parent's child can use (one-time random token, checked first):
``uint32 json_len, uint32 bin_len, json bytes, bin bytes`` (little-endian).
No pickle: a message is a small JSON dict plus optional raw bytes.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("remote")

WORKER_FLAG = "--rainroom-zone-worker"
ENV_ADDR = "RAINROOM_WORKER_ADDR"
ENV_TOKEN = "RAINROOM_WORKER_TOKEN"
ENV_INPROCESS = "RAINROOM_ZONE_INPROCESS"   # "1" = render phone rooms in this process
_FRAME = struct.Struct("<II")
_MAX_JSON = 4 * 1024 * 1024
_MAX_BIN = 16 * 1024 * 1024
CONNECT_TIMEOUT_S = 45.0                    # child start: Python + numpy + engine import
PREPARE_TIMEOUT_S = 120.0                   # drop bank build
CALL_TIMEOUT_S = 20.0

_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_CREATE_NO_WINDOW = 0x08000000


class WorkerError(RuntimeError):
    """The worker process could not start, or went away."""


# ----------------------------------------------------------------------------
# framing
# ----------------------------------------------------------------------------

def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(1 << 20, n - len(buf)))
        if not chunk:
            raise EOFError("worker connection closed")
        buf += chunk
    return bytes(buf)


def send_msg(sock: socket.socket, obj: Dict[str, Any], blob: bytes = b"") -> None:
    j = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    sock.sendall(_FRAME.pack(len(j), len(blob)) + j + blob)


def recv_msg(sock: socket.socket) -> Tuple[Dict[str, Any], bytes]:
    nj, nb = _FRAME.unpack(_recv_exact(sock, _FRAME.size))
    if nj > _MAX_JSON or nb > _MAX_BIN:
        raise EOFError("worker message too large")
    obj = json.loads(_recv_exact(sock, nj).decode("utf-8")) if nj else {}
    blob = _recv_exact(sock, nb) if nb else b""
    if not isinstance(obj, dict):
        raise EOFError("bad worker message")
    return obj, blob


# ----------------------------------------------------------------------------
# child
# ----------------------------------------------------------------------------

def _lower_priority() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), _BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass
    else:
        try:
            os.nice(5)
        except Exception:
            pass


def _copy_room_into(dst, src) -> None:
    """Make ``dst`` (the engine's Room) equal ``src`` field by field, the way a
    desktop edit changes the Room the engine holds."""
    import dataclasses
    for f in dataclasses.fields(src):
        setattr(dst, f.name, getattr(src, f.name))
    try:
        dst.sync_all_windows()
    except Exception:
        pass


def serve(argv=None) -> int:
    """Child entry point: connect back, then answer commands until told to quit
    or the parent goes away."""
    addr = os.environ.get(ENV_ADDR, "")
    token = os.environ.get(ENV_TOKEN, "")
    if not addr or not token:
        return 2
    host, _, port = addr.rpartition(":")
    _lower_priority()
    if sys.stderr is not None:
        logging.basicConfig(level=logging.WARNING, format="zone-worker %(levelname)s %(name)s: %(message)s")
    sock = socket.create_connection((host, int(port)), timeout=30.0)
    sock.settimeout(None)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    send_msg(sock, {"t": "hello", "token": token, "pid": os.getpid()})

    import numpy as np
    from app.audio.spatial_engine import SpatialRainEngine
    from app.utils.persistence import room_from_dict

    eng = None
    room = None
    sr, bs = 48000, 2048
    try:
        while True:
            try:
                msg, _blob = recv_msg(sock)
            except (EOFError, OSError):
                break
            op = msg.get("t")
            try:
                if op == "init":
                    sr, bs = int(msg.get("samplerate", sr)), int(msg.get("blocksize", bs))
                    send_msg(sock, {"t": "ok"})
                elif op == "room":
                    new = room_from_dict(msg["room"])
                    if eng is None:
                        room = new
                        eng = SpatialRainEngine(room, samplerate=sr, blocksize=bs)
                    else:
                        _copy_room_into(room, new)
                    if msg.get("reply"):
                        send_msg(sock, {"t": "ok"})
                elif op == "prepare":
                    eng.prepare_for_stream()
                    send_msg(sock, {"t": "ok"})
                elif op == "render":
                    n = int(msg.get("frames") or bs)
                    t0, c0 = time.perf_counter(), time.thread_time()
                    blk = eng.render_listener_block(n)
                    pcm = np.clip(np.asarray(blk, dtype=np.float32), -1.0, 1.0)
                    data = (pcm * 32767.0).astype("<i2").tobytes()
                    send_msg(sock, {"t": "block", "render_ms": (time.perf_counter() - t0) * 1000.0,
                                    "cpu_ms": (time.thread_time() - c0) * 1000.0}, data)
                elif op == "start_hp":
                    eng.start_headphones(headphones_device=msg.get("device"))
                    send_msg(sock, {"t": "ok"})
                elif op == "stop":
                    if eng is not None and eng.running:
                        eng.stop()
                    send_msg(sock, {"t": "ok"})
                elif op == "status":
                    send_msg(sock, {"t": "status", "running": bool(eng is not None and eng.running)})
                elif op == "quit":
                    break
                else:
                    send_msg(sock, {"t": "err", "message": f"unknown command {op!r}"})
            except Exception as e:  # noqa: BLE001 - reported to the parent
                if op == "room" and not msg.get("reply"):
                    # fire-and-forget: an unasked reply would answer the next call
                    logging.getLogger("remote").warning("zone worker: room update failed: %s", e)
                    continue
                try:
                    send_msg(sock, {"t": "err", "message": str(e) or e.__class__.__name__,
                                    "trace": traceback.format_exc(limit=8)})
                except OSError:
                    break
    finally:
        try:
            if eng is not None and eng.running:
                eng.stop()
        except Exception:
            pass
        try:
            sock.close()
        except OSError:
            pass
    return 0


# ----------------------------------------------------------------------------
# parent
# ----------------------------------------------------------------------------

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def worker_command() -> Tuple[list, Optional[str], Dict[str, str]]:
    """(argv, cwd, extra env) that starts a worker from source or a frozen build."""
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_FLAG], None, {}
    root = str(_repo_root())
    pp = os.environ.get("PYTHONPATH", "")
    return ([sys.executable, "-m", "app.remote.worker"], root,
            {"PYTHONPATH": root + (os.pathsep + pp if pp else "")})


def processes_enabled() -> bool:
    return os.environ.get(ENV_INPROCESS, "").strip() not in ("1", "true", "yes")


class ProcessEngine:
    """Engine stand-in whose rendering happens in a worker process.

    Falls back to an in-process SpatialRainEngine (with a warning) if the
    worker can't start, so the phone feature keeps working either way.
    """

    def __init__(self, room, samplerate: int = 48000, blocksize: int = 2048):
        self.room = room
        self.samplerate = int(samplerate)
        self.blocksize = int(blocksize)
        self._proc: Optional[subprocess.Popen] = None
        self._sock: Optional[socket.socket] = None
        self._lock = threading.RLock()
        self._last_room: Optional[str] = None
        self._local = None                    # fallback engine
        self._playing = False
        self.last_render_ms = 0.0
        self.last_cpu_ms = 0.0
        self.pid: Optional[int] = None

    # ----- process lifecycle -----
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and self._sock is not None

    def _spawn(self) -> None:
        argv, cwd, extra = worker_command()
        lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            lsock.bind(("127.0.0.1", 0))
            lsock.listen(1)
            port = lsock.getsockname()[1]
            token = os.urandom(24).hex()
            env = dict(os.environ)
            env.update(extra)
            env[ENV_ADDR] = f"127.0.0.1:{port}"
            env[ENV_TOKEN] = token
            flags = 0
            if sys.platform == "win32":
                flags = _BELOW_NORMAL_PRIORITY_CLASS | _CREATE_NO_WINDOW
            debug = os.environ.get("RAINROOM_WORKER_DEBUG")
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL,
                                    stderr=None if debug else subprocess.DEVNULL,
                                    creationflags=flags, close_fds=True)
            deadline = time.monotonic() + CONNECT_TIMEOUT_S
            sock = None
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise WorkerError(f"the sound worker exited at start (code {proc.returncode})")
                lsock.settimeout(0.25)
                try:
                    s, peer = lsock.accept()
                except socket.timeout:
                    continue
                s.settimeout(10.0)
                try:
                    hello, _ = recv_msg(s)
                except (EOFError, OSError, ValueError):
                    s.close()
                    continue
                if peer[0] != "127.0.0.1" or not hmac.compare_digest(str(hello.get("token", "")), token):
                    s.close()                 # someone else knocked: keep waiting
                    continue
                sock = s
                break
            if sock is None:
                proc.kill()
                raise WorkerError("the sound worker did not start in time")
        finally:
            lsock.close()
        sock.settimeout(None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._proc, self._sock, self.pid = proc, sock, proc.pid
        self._last_room = None
        self._call({"t": "init", "samplerate": self.samplerate, "blocksize": self.blocksize})

    def _ensure(self) -> bool:
        """True = a worker is ready; False = use the in-process fallback."""
        if self._local is not None:
            return False
        if self.alive:
            return True
        self._close_proc()
        if not processes_enabled():
            self._make_local()
            return False
        try:
            self._spawn()
            return True
        except Exception as e:      # noqa: BLE001
            log.warning("Phone room worker process failed (%s); rendering in the app instead", e)
            self._close_proc()
            self._make_local()
            return False

    def _make_local(self) -> None:
        from app.audio.spatial_engine import SpatialRainEngine
        self._local = SpatialRainEngine(self.room, samplerate=self.samplerate, blocksize=self.blocksize)

    def _close_proc(self) -> None:
        sock, proc = self._sock, self._proc
        self._sock = self._proc = None
        self._playing = False
        if sock is not None:
            try:
                send_msg(sock, {"t": "quit"})
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if proc is not None:
            try:
                proc.wait(3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(2.0)
                except subprocess.TimeoutExpired:
                    pass

    # ----- calls -----
    def _call(self, msg: Dict[str, Any], timeout: float = CALL_TIMEOUT_S) -> Tuple[Dict[str, Any], bytes]:
        with self._lock:
            sock = self._sock
            if sock is None:
                raise WorkerError("the sound worker is not running")
            try:
                sock.settimeout(timeout)
                send_msg(sock, msg)
                reply, blob = recv_msg(sock)
            except (OSError, EOFError, ValueError) as e:
                self._close_proc()
                raise WorkerError(f"the sound worker stopped ({e.__class__.__name__})") from None
            finally:
                if self._sock is not None:
                    self._sock.settimeout(None)
        if reply.get("t") == "err":
            if reply.get("trace"):
                log.warning("zone worker: %s", reply["trace"].strip().splitlines()[-1])
            raise RuntimeError(reply.get("message") or "the sound worker failed")
        return reply, blob

    def _push_room(self, force: bool = False, reply: bool = False) -> None:
        from app.utils.persistence import room_to_dict
        d = room_to_dict(self.room)
        key = json.dumps(d, sort_keys=True, separators=(",", ":"))
        if not force and key == self._last_room:
            return
        msg = {"t": "room", "room": d}
        with self._lock:
            if reply:
                msg["reply"] = True
                self._call(msg)
            else:
                sock = self._sock
                if sock is None:
                    raise WorkerError("the sound worker is not running")
                try:
                    send_msg(sock, msg)
                except OSError as e:
                    self._close_proc()
                    raise WorkerError(f"the sound worker stopped ({e.__class__.__name__})") from None
            self._last_room = key

    # ----- engine API -----
    @property
    def running(self) -> bool:
        if self._local is not None:
            return bool(self._local.running)
        return self._playing and self.alive

    def prepare_for_stream(self, seed: int = 3) -> None:
        with self._lock:
            if not self._ensure():
                self._local.room = self.room
                self._local.prepare_for_stream(seed)
                return
            self._push_room(force=True, reply=True)
            self._call({"t": "prepare"}, timeout=PREPARE_TIMEOUT_S)

    def render_pcm16(self, frames: Optional[int] = None) -> bytes:
        """Next block as little-endian int16 stereo bytes."""
        n = int(frames or self.blocksize)
        if self._local is not None:
            import numpy as np
            t0 = time.perf_counter()
            self._local.room = self.room
            blk = self._local.render_listener_block(n)
            self.last_render_ms = (time.perf_counter() - t0) * 1000.0
            pcm = np.clip(np.asarray(blk, dtype=np.float32), -1.0, 1.0)
            return (pcm * 32767.0).astype("<i2").tobytes()
        with self._lock:
            self._push_room()
            reply, blob = self._call({"t": "render", "frames": n})
        self.last_render_ms = float(reply.get("render_ms", 0.0))
        self.last_cpu_ms = float(reply.get("cpu_ms", 0.0))
        if len(blob) != n * 4:
            raise WorkerError("the sound worker sent a broken block")
        return blob

    def render_listener_block(self, frames: Optional[int] = None):
        import numpy as np
        b = self.render_pcm16(frames)
        return np.frombuffer(b, dtype="<i2").reshape(-1, 2).astype(np.float32) / 32767.0

    def start_headphones(self, headphones_device: Optional[int] = None) -> None:
        with self._lock:
            if not self._ensure():
                self._local.room = self.room
                self._local.start_headphones(headphones_device=headphones_device)
                return
            self._push_room(force=True, reply=True)
            self._call({"t": "start_hp", "device": headphones_device}, timeout=PREPARE_TIMEOUT_S)
            self._playing = True

    def sync_room(self) -> None:
        """Send the current room to a worker that is playing on a PC device."""
        if self._local is not None or not self.alive:
            return
        try:
            self._push_room()
        except WorkerError as e:
            log.warning("zone worker: %s", e)

    def stop(self) -> None:
        if self._local is not None:
            if self._local.running:
                self._local.stop()
            return
        with self._lock:
            if self.alive:
                try:
                    self._call({"t": "stop"})
                except Exception as e:  # noqa: BLE001
                    log.warning("zone worker stop: %s", e)
            self._playing = False

    def release(self) -> None:
        """End the worker process (frees its memory); the next start spawns a new one."""
        with self._lock:
            self._close_proc()
            if self._local is not None and self._local.running:
                self._local.stop()


if __name__ == "__main__":
    sys.exit(serve(sys.argv))
