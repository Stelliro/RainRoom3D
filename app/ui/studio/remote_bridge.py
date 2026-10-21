"""Desktop side of the phone feature: ZoneHub + RemoteServer <-> StudioContext.

* The desktop Room (``ctx.room``) owns the shared weather. Desktop edits
  ("sound" / "rain", a new / loaded house) are pushed to the hub at most
  ~10 times a second (``PUSH_MS``) from a small worker thread, so the preset
  look-up never touches the UI thread.
* A phone's weather edit arrives on the server thread (``hub.on_weather_from_phone``)
  and is handed to the Qt main thread through a queued signal, then written
  with ``settings_schema.set`` + ``ctx.edited(area, source=self)``. Edits whose
  source is this bridge are not pushed back (no loop): the hub already holds
  the phone's newer values, so only the dock's preset name is sent afterwards
  (sending the whole room back could overwrite a phone that kept dragging).
* The server starts / stops / changes its key on a worker thread; the UI hears
  about it through ``stateChanged``. ``shutdown()`` (app close) never waits:
  its worker is a normal (non-daemon) thread, so Python finishes closing
  sockets, zone renders and PC outputs after the window is gone.
* The on / off choice, the pairing key and the port are remembered in
  ``configs/remote.json`` (never in the house file). ``RAINROOM_REMOTE_CONFIG``
  overrides the path (tests). The file is tied to this PC and user: copied to
  another machine (e.g. inside a release zip) it is ignored, so sharing never
  turns itself on there with someone else's key.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6 import QtCore

from app.models import presets
from app.models import settings_schema as schema
from app.remote.server import DEFAULT_PORT, RemoteServer, new_key
from app.remote.zones import SHARED_KEYS, ZoneHub
from app.ui.studio.context import StudioContext

log = logging.getLogger("ui")

_REPO = Path(__file__).resolve().parents[3]
_KEY_RE = re.compile(r"^[A-Za-z0-9]{6,64}$")
RAIN_REDRAW_KEYS = ("droplet_density", "rain_intensity")   # same as weather_dock


def machine_id() -> str:
    """A short fingerprint of this PC + user (not secret, just "same place?")."""
    who = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    raw = f"{socket.gethostname()}|{who}|{Path.home()}".lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def default_settings_path() -> Path:
    env = os.environ.get("RAINROOM_REMOTE_CONFIG")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "configs" / "remote.json"
    return _REPO / "configs" / "remote.json"


class RemoteBridge(QtCore.QObject):
    """Owns the phone server for the studio window."""

    stateChanged = QtCore.Signal()                  # status / urls / key changed
    zonesChanged = QtCore.Signal()                  # a phone joined, left, or changed output
    weatherApplied = QtCore.Signal(object, object)  # (changes, preset name) now in ctx.room

    _phoneWeather = QtCore.Signal(object, object)   # server thread -> main thread
    _hubEvent = QtCore.Signal(str)
    _jobDone = QtCore.Signal(str, object)

    PUSH_MS = 100                                   # desktop -> phones: at most ~10 / s

    def __init__(self, ctx: StudioContext, parent=None, settings_path: Optional[os.PathLike] = None,
                 hub: Optional[ZoneHub] = None, zones_dir: Optional[os.PathLike] = None):
        super().__init__(parent)
        self.ctx = ctx
        self.settings_path = Path(settings_path) if settings_path else default_settings_path()
        self.settings: Dict[str, Any] = self._load_settings()
        p = self.settings.get("port")
        self.port: int = int(p) if isinstance(p, int) and 1024 <= p <= 65535 else DEFAULT_PORT
        self.hub = hub or ZoneHub(zones_dir=zones_dir, device_lister=self._list_devices)
        self.server: Optional[RemoteServer] = None
        self.status = "off"                         # off | starting | on | stopping | error
        self.error = ""
        self.urls: List[str] = []
        # Which weather preset the desktop shows (the window points this at the dock)
        self.preset_getter: Callable[[], Optional[str]] = lambda: presets.matching(self.ctx.room, "weather")
        self._closing = False
        self._jobs = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="phone-server")
        self._pusher = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="phone-push")
        self._last_push: Optional[concurrent.futures.Future] = None
        self._push_timer = QtCore.QTimer(self)
        self._push_timer.setSingleShot(True)
        self._push_timer.setInterval(self.PUSH_MS)
        self._push_timer.timeout.connect(self.push_now)

        Q = QtCore.Qt.QueuedConnection
        self._phoneWeather.connect(self._apply_phone_weather, Q)
        self._hubEvent.connect(self._on_hub_event_main, Q)
        self._jobDone.connect(self._on_job_done, Q)
        self.hub.on_weather_from_phone(self._from_phone_any_thread)
        self.hub.add_listener(self._on_hub_event_any)
        ctx.roomEdited.connect(self._on_room_edited)
        ctx.roomReplaced.connect(self._on_room_replaced)
        ctx.devicesChanged.connect(self._on_devices)
        self.hub.set_house_defaults(ctx.room.roof_material, ctx.room.wall_material)
        self.push_now()

    # ------------------------------------------------------------ settings
    def _load_settings(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        if data.get("machine") != machine_id():
            # Made on another PC (or before this check existed): don't switch
            # sharing on by itself or reuse that key; the port is harmless.
            log.info("Phone settings %s are from another PC; starting with sharing off", self.settings_path)
            return {k: v for k, v in data.items() if k == "port"}
        return data

    def _save_settings(self) -> None:
        self.settings["machine"] = machine_id()
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.settings_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
            os.replace(tmp, self.settings_path)
        except OSError as e:
            log.warning("Could not save phone settings %s: %s", self.settings_path, e)

    def remembered_on(self) -> bool:
        return bool(self.settings.get("enabled", False))

    def _key(self) -> Optional[str]:
        k = self.settings.get("key")
        return k if isinstance(k, str) and _KEY_RE.match(k) else None

    # ------------------------------------------------------------ server lifecycle
    @property
    def running(self) -> bool:
        return self.status == "on"

    @property
    def key(self) -> str:
        return self.server.key if self.server is not None else (self._key() or "")

    def autostart(self) -> None:
        """Start the server if the user left it on last time."""
        if self.remembered_on():
            self.start()

    def set_enabled(self, on: bool, remember: bool = True) -> None:
        if remember:
            self.settings["enabled"] = bool(on)
            self._save_settings()
        if on:
            self.start()
        else:
            self.stop()

    def start(self) -> None:
        if self._closing or self.status in ("starting", "on"):
            return
        self.status, self.error = "starting", ""
        self.stateChanged.emit()
        self._jobs.submit(self._job, "started", self._start_job)

    def stop(self) -> None:
        if self.status in ("off", "stopping") and (self.server is None or not self.server.running):
            return
        self.status = "stopping"
        self.stateChanged.emit()
        self._jobs.submit(self._job, "stopped", self._stop_job)

    def new_link(self) -> None:
        """A new pairing key: phones on the old link are disconnected."""
        if self._closing:
            return
        self._jobs.submit(self._job, "rotated", self._rotate_job)

    def _job(self, kind: str, fn) -> None:
        try:
            data = fn()
        except BaseException as e:      # noqa: BLE001 - reported to the UI
            log.warning("Phone server %s failed: %s", kind, e)
            kind, data = "error", e
        try:
            self._jobDone.emit(kind, data)
        except RuntimeError:            # the window is already gone
            pass

    def _start_job(self):
        if self.server is None:
            self.server = RemoteServer(self.hub, port=self.port, key=self._key())
        port = self.server.start()
        return {"port": port, "key": self.server.key, "urls": self.server.lan_urls()}

    def _stop_job(self):
        if self.server is not None:
            self.server.stop()          # closes sockets, stops every zone render / PC output
        return None

    def _rotate_job(self):
        if self.server is None:
            self.settings["key"] = new_key()
            return {"key": self.settings["key"], "urls": []}
        key = self.server.rotate_key()
        return {"key": key, "urls": self.server.lan_urls() if self.server.running else []}

    def _on_job_done(self, kind: str, data) -> None:
        if kind == "started":
            self.status, self.error = "on", ""
            self.urls = list(data["urls"])
            self.settings["key"] = data["key"]
            if self.port != 0:
                self.settings["port"] = int(data["port"])
            self._save_settings()
        elif kind == "stopped":
            self.status, self.urls = "off", []
        elif kind == "rotated":
            self.settings["key"] = data["key"]
            if data["urls"]:
                self.urls = list(data["urls"])
            self._save_settings()
            self.ctx.say("New phone link made — phones on the old link were disconnected", 5000)
        else:                           # error
            running = self.server is not None and self.server.running
            self.status = "on" if running else "error"
            self.error = _friendly_error(data)
        self.stateChanged.emit()
        self.zonesChanged.emit()

    def shutdown(self) -> None:
        """App close: stop the server and every zone, without waiting."""
        if self._closing:
            return
        self._closing = True
        self._push_timer.stop()

        def _bye():
            try:
                if self.server is not None:
                    self.server.stop()
            finally:
                self.hub.close()
        self._jobs.submit(_bye)
        self._jobs.shutdown(wait=False)
        self._pusher.shutdown(wait=False)

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """Tests: wait until queued start / stop / push jobs have run."""
        ok = True
        for ex in (self._jobs, self._pusher):
            try:
                ex.submit(lambda: None).result(timeout)
            except RuntimeError:        # already shut down: wait for its thread
                for t in list(getattr(ex, "_threads", ())):
                    t.join(timeout)
                    ok &= not t.is_alive()
            except concurrent.futures.TimeoutError:
                ok = False
        return ok

    # ------------------------------------------------------------ desktop -> hub
    def _on_room_edited(self, area: str, source) -> None:
        if source is self:
            return
        if area in ("sound", "rain"):
            if not self._push_timer.isActive():
                self._push_timer.start()
        elif area == "materials":
            r = self.ctx.room
            self.hub.set_house_defaults(r.roof_material, r.wall_material)

    def _on_room_replaced(self) -> None:
        r = self.ctx.room
        self.hub.set_house_defaults(r.roof_material, r.wall_material)
        self._push_timer.stop()
        self.push_now()

    def push_now(self) -> None:
        """Send the desktop's shared values + preset to the hub (off the UI thread)."""
        if self._closing:
            return
        self._push_timer.stop()
        room = self.ctx.room
        values = {k: getattr(room, k) for k in SHARED_KEYS if hasattr(room, k)}
        try:
            preset = self.preset_getter()
        except Exception:
            preset = None
        try:
            self._last_push = self._pusher.submit(self.hub.set_shared, values, preset)
        except RuntimeError:
            pass

    # ------------------------------------------------------------ hub -> desktop
    def _from_phone_any_thread(self, changes: Dict[str, Any], preset_name: Optional[str]) -> None:
        try:
            self._phoneWeather.emit(dict(changes), preset_name)
        except RuntimeError:
            pass

    def _apply_phone_weather(self, changes, preset_name) -> None:
        if self._closing:
            return
        room = self.ctx.room
        for k, v in (changes or {}).items():
            try:
                schema.set(room, k, v)
            except Exception:
                log.warning("phone weather %r=%r not applied", k, v)
        self.weatherApplied.emit(dict(changes or {}), preset_name)
        if changes:
            area = "rain" if any(k in RAIN_REDRAW_KEYS for k in changes) else "sound"
            self.ctx.edited(area, self)
        if preset_name:
            self.ctx.say(f"Weather: {preset_name} (picked on a phone)", 4000)
        self.push_preset()              # line the hub's preset name up with the dock

    def push_preset(self) -> None:
        """Only the dock's preset name to the hub. The values stay as the hub
        has them: they came from a phone and may already be newer than this
        Room when the UI thread is busy."""
        if self._closing:
            return
        try:
            preset = self.preset_getter()
        except Exception:
            preset = None
        if preset is None:
            return                      # the hub works "Custom" out by itself
        try:
            self._last_push = self._pusher.submit(self.hub.set_shared, {}, preset)
        except RuntimeError:
            pass

    def _on_hub_event_any(self, event: str, _data) -> None:
        if event in ("zones", "output"):
            try:
                self._hubEvent.emit(event)
            except RuntimeError:
                pass

    def _on_hub_event_main(self, _event: str) -> None:
        self.zonesChanged.emit()

    def _list_devices(self) -> List[dict]:
        return list(self.ctx.devices or getattr(self.ctx.engine, "devices", []) or [])

    def _on_devices(self) -> None:
        try:
            self._pusher.submit(self.hub.devices, True)
        except RuntimeError:
            pass

    # ------------------------------------------------------------ for the UI
    def zone_rows(self) -> List[Dict[str, Any]]:
        """Phones to list: connected ones, and any still playing on this PC."""
        rows = [z for z in self.hub.list_zones()
                if z.get("connected") or z.get("output", {}).get("mode") != "off"]
        rows.sort(key=lambda z: (not z.get("connected"), str(z.get("name", "")).lower()))
        return rows

    def connected_count(self) -> int:
        return sum(1 for z in self.hub.list_zones() if z.get("connected"))

    def output_text(self, row: Dict[str, Any]) -> str:
        out = row.get("output") or {}
        mode = out.get("mode")
        if mode == "phone":
            return "Starting on this phone…" if out.get("starting") else "Playing on the phone"
        if mode == "pc":
            where = self.ctx.device_label(out.get("device"), "this computer's default output")
            return f"Starting on {where}…" if out.get("starting") else f"Playing on {where}"
        if out.get("error"):
            return "Stopped — " + str(out["error"])
        return "Not playing"

    def stop_zone(self, client_id: str) -> None:
        if self.hub.stop_zone_output(client_id):
            self.ctx.say("Stopped that phone's rain on this computer", 3000)


def _friendly_error(e) -> str:
    text = str(e) or e.__class__.__name__
    if isinstance(e, OSError) and "No free port" in text:
        return "Couldn't find a free network port. Close other apps that share to phones and try again."
    return f"Couldn't start sharing: {text}"


__all__ = ["RemoteBridge", "default_settings_path"]
