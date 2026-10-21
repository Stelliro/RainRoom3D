"""Shared studio state: the Room, the engine, the selection and room edits.

Every panel talks to the others only through this object:

* ``select(sel)``            → ``selectionChanged(sel)``
* ``edited(area, source)``   → ``roomEdited(area, source)``   (a value changed in place)
* ``replace_room(room)``     → ``roomReplaced()``             (new / load / example)
* ``say(text)``              → ``message(text, ms)``          (transient status line)
* ``refresh_devices()``      → ``devicesChanged()``

``area`` names what changed so listeners can skip work they do not need:
"house" (footprint / ceiling / terrain), "materials", "name", "windows",
"speakers", "listener", "canvas" (a drag on a canvas), "sound" (rain page
values), "rain" (values that change the drawn 3D rain), "devices".

Room operations used from several places (canvas keys, inspector buttons,
objects list) live here too, so they behave identically everywhere.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, List, Optional, Tuple

from PySide6 import QtCore, QtWidgets

from app.models.room import Room, Speaker, Window, place_speakers_evenly

log = logging.getLogger("ui")

NONE_SEL: Tuple[str, int] = ("none", -1)
_API_SUFFIX = re.compile(r"\s*Windows\s+(WASAPI|DirectSound|MME|WDM-KS)\s*$", re.I)

# Defaults for a new speaker (same everywhere: canvas tool, objects list, inspector)
SPEAKER_DIMS = dict(width=0.45, height=0.28, depth=0.18, size=0.32)


def clean_device_name(name: str) -> str:
    return _API_SUFFIX.sub("", str(name or "?")).strip()


class StudioContext(QtCore.QObject):
    roomReplaced = QtCore.Signal()
    roomEdited = QtCore.Signal(str, object)
    selectionChanged = QtCore.Signal(tuple)
    devicesChanged = QtCore.Signal()
    routingChanged = QtCore.Signal(str)       # a device assignment changed: "listener" | "speaker"
    message = QtCore.Signal(str, int)
    dirtyChanged = QtCore.Signal(bool)

    def __init__(self, room: Room, engine, parent=None):
        super().__init__(parent)
        self.room: Room = room
        self.engine = engine
        self.selected: Tuple[str, int] = NONE_SEL
        self.devices: List[dict] = []
        self.dirty = False
        self.path: Optional[str] = None
        # The house preset the room was opened from (None: not from a preset)
        self.preset_name: Optional[str] = None
        # Hooks the window fills in (dialogs live with the window)
        self.confirm: Callable[[str, str], bool] = lambda title, text: True
        self.open_output_setup: Callable[[], None] = lambda: None

    # ----- pending text edits -----
    @staticmethod
    def commit_pending_edit() -> None:
        """Finish a name / notes field that is still being typed in.

        Those fields apply on ``editingFinished`` (focus leaving). The Objects
        list, toolbar buttons and Ctrl+S never take focus, so without this the
        typed text would land on whatever is selected *next* (or miss a save).
        Clearing focus now runs ``editingFinished`` while the old object is
        still the target."""
        fw = QtWidgets.QApplication.focusWidget()
        # (a spin box applies a typed number on focus-out too: keyboardTracking is off)
        if isinstance(fw, (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox)):
            fw.clearFocus()

    # ----- selection -----
    def select(self, sel, force: bool = False) -> None:
        self.commit_pending_edit()
        kind, idx = (tuple(sel) + (-1,))[:2] if sel else NONE_SEL
        if kind == "headphones":            # legacy GL marker = the You listener
            kind, idx = "listener", 0
        if kind == "listener":
            idx = 0
        if kind == "window" and not (0 <= idx < len(self.room.windows)):
            kind, idx = NONE_SEL
        if kind == "speaker" and not (0 <= idx < len(self.room.speakers)):
            kind, idx = NONE_SEL
        if kind not in ("window", "speaker", "listener"):
            kind, idx = NONE_SEL
        new = (kind, int(idx))
        if new == self.selected and not force:
            return
        self.selected = new
        self.selectionChanged.emit(new)

    def current_window(self) -> Optional[Window]:
        k, i = self.selected
        return self.room.windows[i] if k == "window" and 0 <= i < len(self.room.windows) else None

    def current_speaker(self) -> Optional[Speaker]:
        k, i = self.selected
        return self.room.speakers[i] if k == "speaker" and 0 <= i < len(self.room.speakers) else None

    # ----- edits -----
    def edited(self, area: str, source=None) -> None:
        if area not in ("view",):
            self.set_dirty(True)
        self.roomEdited.emit(area, source)

    def set_dirty(self, on: bool) -> None:
        on = bool(on)
        if on != self.dirty:
            self.dirty = on
            self.dirtyChanged.emit(on)

    def replace_room(self, room: Room, path: Optional[str] = None, preset_name: Optional[str] = None) -> None:
        self.commit_pending_edit()  # into the old room, never onto the new one
        self.room = room
        self.engine.room = room
        self.path = path
        self.preset_name = preset_name
        self.selected = NONE_SEL
        self.set_dirty(False)
        self.roomReplaced.emit()
        self.selectionChanged.emit(NONE_SEL)

    def say(self, text: str, ms: int = 4000) -> None:
        self.message.emit(str(text), int(ms))

    # ----- devices -----
    def refresh_devices(self) -> List[dict]:
        try:
            self.devices = list(self.engine.refresh_devices() or [])
        except Exception as e:  # no audio backend: the UI still works
            log.warning("Device query failed: %s", e)
            self.devices = []
        self.devicesChanged.emit()
        return self.devices

    def device_label(self, dev, none_text: str = "System default") -> str:
        """A device name for a saved output index (people know names, not numbers)."""
        if dev is None:
            return none_text
        for d in self.devices or getattr(self.engine, "devices", []) or []:
            if d.get("index") == dev:
                return clean_device_name(d.get("name", "?"))
        return f"Output #{dev}"

    def device_items(self) -> List[Tuple[str, int]]:
        out = []
        for d in self.devices:
            star = "  (default)" if d.get("is_default") else ""
            out.append((f"{clean_device_name(d.get('name'))}{star}", d["index"]))
        return out

    def set_listener_device(self, dev) -> None:
        self.room.listener.audio_device = int(dev) if dev is not None else None
        self.edited("listener", None)
        self.routingChanged.emit("listener")
        self.say(f"Your headphones → {self.device_label(self.room.listener.audio_device)}")

    def set_speaker_device(self, idx: int, dev) -> None:
        if not (0 <= idx < len(self.room.speakers)):
            return
        s = self.room.speakers[idx]
        self.engine.set_speaker_device(s, int(dev) if dev is not None else None)
        self.edited("speakers", None)
        self.routingChanged.emit("speaker")
        self.say(f"{s.name} → {self.device_label(s.audio_device, 'no device')}")

    # ----- room operations -----
    def add_speaker(self, x: Optional[float] = None, z: Optional[float] = None) -> int:
        r = self.room
        n = len(r.speakers)
        if x is None or z is None:
            x = r.width * (0.25 + 0.15 * (n % 4))
            z = r.depth * (0.3 + 0.15 * ((n // 2) % 3))
        x, z = r.clamp_inside(x, z)
        s = Speaker(name=f"Speaker {n + 1}", x=x, y=1.1, z=z, **SPEAKER_DIMS)
        r.speakers.append(s)
        self.edited("speakers", None)
        self.select(("speaker", n))
        self.say(f"Added {s.name} — drag it to where the real speaker stands")
        return n

    def add_window(self) -> int:
        """A new window on the longest free stretch of wall."""
        r = self.room
        best = None
        for wall in ("north", "south", "east", "west"):
            length = r.width if wall in ("north", "south") else r.depth
            taken = sorted((w.offset, w.offset + w.width) for w in r.windows
                           if (w.wall or "north").lower() == wall and not getattr(w, "free_place", False))
            edges = [0.0] + [v for seg in taken for v in seg] + [length]
            for a, b in zip(edges[0::2], edges[1::2]):
                gap = b - a
                if best is None or gap > best[0]:
                    best = (gap, wall, a, b)
        _, wall, a, b = best
        width = max(0.4, min(1.2, (b - a) - 0.4))
        offset = max(0.0, a + ((b - a) - width) * 0.5)
        n = len(r.windows)
        win = Window(name=f"Window {n + 1}", wall=wall, offset=offset, width=width, height=1.2, open=0.7)
        r.windows.append(win)
        r.sync_window_coords(win)
        self.edited("windows", None)
        self.select(("window", n))
        self.say(f"Added {win.name} on the {wall} wall")
        return n

    def remove_window(self, idx: int) -> bool:
        if not (0 <= idx < len(self.room.windows)):
            return False
        self.commit_pending_edit()  # before the indices shift
        name = self.room.windows.pop(idx).name
        self.select(NONE_SEL, force=True)
        self.edited("windows", None)
        self.say(f"Removed {name}")
        return True

    def remove_speaker(self, idx: int) -> bool:
        if not (0 <= idx < len(self.room.speakers)):
            return False
        self.commit_pending_edit()  # before the indices shift
        name = self.room.speakers.pop(idx).name
        self.select(NONE_SEL, force=True)
        self.edited("speakers", None)
        self.say(f"Removed {name}")
        return True

    def delete_selected(self) -> bool:
        kind, idx = self.selected
        if kind == "window":
            return self.remove_window(idx)
        if kind == "speaker":
            return self.remove_speaker(idx)
        if kind == "listener":
            self.say("“You” is always there — move it instead of deleting it")
        return False

    def spread_speakers(self, count: int = 3, ask: bool = True) -> bool:
        """Replace the room speakers with ``count`` spread evenly (asks first
        when that would throw away speakers you already placed)."""
        r = self.room
        if ask and r.speakers and not self.confirm(
                "Spread speakers evenly",
                f"This replaces your {len(r.speakers)} speaker(s) — and their device "
                f"choices — with {count} new ones spread around the room. Continue?"):
            return False
        self.commit_pending_edit()
        place_speakers_evenly(r, count=count)
        for s in r.speakers:  # same cabinet as a newly added speaker
            s.width, s.height, s.depth = SPEAKER_DIMS["width"], SPEAKER_DIMS["height"], SPEAKER_DIMS["depth"]
        self.edited("speakers", None)
        self.select(NONE_SEL, force=True)
        self.say(f"Placed {count} speakers — pick a device for each one")
        return True

    def clamp_contents(self) -> None:
        """Keep speakers and You inside the walls and under the ceiling."""
        r = self.room
        top = max(0.2, float(r.height) - 0.08)
        for s in r.speakers:
            s.x, s.z = r.clamp_inside(s.x, s.z)
            s.y = max(0.1, min(top, float(s.y)))
        L = r.listener
        L.x, L.z = r.clamp_inside(L.x, L.z)
        L.y = max(0.4, min(max(0.5, float(r.height) - 0.1), float(L.y)))
        if r.headphones_items:
            r.headphones_items[0].x, r.headphones_items[0].z = L.x, L.z
        r.sync_all_windows()


__all__ = ["NONE_SEL", "SPEAKER_DIMS", "StudioContext", "clean_device_name"]
