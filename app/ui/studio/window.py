"""The studio main window: builds the modules and wires them together.

    ┌ AppBar ──────────────────────────────────────────────┐
    │ CanvasHost (views + palette, objects, inspector) │Dock│
    └ TransportBar ────────────────────────────────────────┘

Panels never call each other directly: they share ``StudioContext``.
This window owns only what is window-wide: file I/O, menus, keyboard,
Design / Listen mode and dialogs.
"""

from __future__ import annotations

import json
import logging
import sys
import webbrowser
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets

from app.audio.spatial_engine import SpatialRainEngine
from app.models import presets
from app.models import settings_schema as schema
from app.models.room import Room, default_house
from app.ui.floorplan import TOOL_SELECT
from app.ui.studio.app_bar import MODE_DESIGN, MODE_LISTEN, AppBar
from app.ui.studio.canvas import VIEW_3D, VIEW_PLAN, CanvasHost
from app.ui.studio.context import NONE_SEL, StudioContext
from app.ui.studio.controls import is_text_input
from app.ui.studio.icons import icon
from app.ui.studio.inspector import InspectorCard
from app.ui.studio.objects_panel import ObjectsPanel
from app.ui.studio.palette import TOOL_KEYS, ToolPalette
from app.ui.studio.transport import TransportBar
from app.ui.studio.weather_dock import SoundDock, rain_rate_text
from app.ui.theme import T
from app.utils.persistence import load_room, save_room
from app.version import __version__

log = logging.getLogger("ui")

PROJECT_URL = "https://github.com/Stelliro/RainRoom3D"
PROJECT_NAME = "RainRoom3D"
_REPO = Path(__file__).resolve().parents[3]


def _configs_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "configs"
    return _REPO / "configs"


class _EdgeTab(QtWidgets.QToolButton):
    """Thin vertical tab that brings a collapsed side panel back."""

    def __init__(self, text: str, icon_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("EdgeTab")
        self._text = text
        self._icon = icon(icon_name, T.accent, 18)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        self._icon.paint(p, QtCore.QRect((self.width() - 18) // 2, 16, 18, 18))
        p.setPen(QtGui.QColor(T.accent if not self.underMouse() else T.text))
        f = p.font()
        f.setPixelSize(T.fs_caption)
        f.setBold(True)
        p.setFont(f)
        # rotated 90° clockwise: the glyphs' ascent lies to the right of the baseline
        p.translate(self.width() / 2 - p.fontMetrics().capHeight() / 2, 48)
        p.rotate(90)
        p.drawText(0, 0, self._text)


class StudioWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(1024, 640)
        self.resize(1480, 920)

        loaded = self._load_startup_preset()
        room, path, from_preset = (tuple(loaded) + (None, None))[:3] if isinstance(loaded, tuple) else (loaded, None, None)
        engine = SpatialRainEngine(room)
        self.ctx = StudioContext(room, engine, self)
        self.ctx.path = path
        self.ctx.preset_name = from_preset
        self.ctx.confirm = self._confirm
        self.ctx.open_output_setup = self._open_output_setup
        self._mode = MODE_DESIGN
        self._mode_view = {MODE_DESIGN: VIEW_PLAN, MODE_LISTEN: VIEW_3D}
        self._dock_user_set = False  # the user hid / showed the dock themselves

        self._build()
        self._build_menus()
        self._wire()
        self._apply_room_to_ui()
        self.ctx.refresh_devices()
        self._build_remote()
        self.ctx.select(NONE_SEL, force=True)
        # The room you're working on is kept for next time, a moment after each change
        self._autosave = QtCore.QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(1500)
        self._autosave.timeout.connect(self._autosave_now)
        self.ctx.roomEdited.connect(lambda area, _src: area != "view" and self._autosave.start())
        self.ctx.roomReplaced.connect(self._autosave.start)
        self._update_title()
        self.canvas.floor.setFocus(QtCore.Qt.OtherFocusReason)
        self.ctx.say(f"Loaded “{room.name}” — press Play to start", 6000)

    # ------------------------------------------------------------ handy names
    @property
    def room(self) -> Room:
        return self.ctx.room

    @room.setter
    def room(self, r: Room) -> None:
        self.ctx.room = r

    @property
    def engine(self):
        return self.ctx.engine

    @property
    def _selected(self):
        return self.ctx.selected

    @property
    def _play_mode(self):
        return self.transport.play_mode

    @property
    def gl(self):
        return self.canvas.gl

    # ------------------------------------------------------------ build
    def _build(self) -> None:
        central = QtWidgets.QWidget()
        central.setObjectName("Central")
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.app_bar = AppBar(self.ctx)
        v.addWidget(self.app_bar)

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        v.addLayout(body, 1)

        self.canvas = CanvasHost(self.ctx)
        self.palette = ToolPalette()
        self.objects = ObjectsPanel(self.ctx)
        self.inspector = InspectorCard(self.ctx)
        self.canvas.set_overlays(self.palette, self.objects, self.inspector)
        body.addWidget(self.canvas, 1)

        self.dock = SoundDock(self.ctx)
        body.addWidget(self.dock)
        self.dock_tab = _EdgeTab("Rain & sound", "sliders")
        self.dock_tab.setToolTip("Show the rain & sound panel")
        self.dock_tab.setFixedWidth(36)
        self.dock_tab.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Expanding)
        self.dock_tab.setCursor(QtCore.Qt.PointingHandCursor)
        self.dock_tab.setFocusPolicy(QtCore.Qt.NoFocus)
        self.dock_tab.hide()
        body.addWidget(self.dock_tab)

        self.transport = TransportBar(self.ctx)
        self.transport.focus_speaker = self._focus_speaker
        v.addWidget(self.transport)

    def _build_menus(self) -> None:
        fm = self.app_bar.file_menu
        self.act_new = fm.addAction(icon("new", T.text2, 16), "New house", self._new_house)
        self.act_new.setShortcut(QtGui.QKeySequence.New)
        self.act_open = fm.addAction(icon("folder", T.text2, 16), "Open…", self._open_house)
        self.act_open.setShortcut(QtGui.QKeySequence.Open)
        fm.addSeparator()
        self.act_save = fm.addAction(icon("save", T.text2, 16), "Save", self._save)
        self.act_save.setShortcut(QtGui.QKeySequence.Save)
        self.act_save_as = fm.addAction("Save as…", self._save_as)
        self.act_save_as.setShortcut(QtGui.QKeySequence("Ctrl+Shift+S"))
        fm.addSeparator()
        self.act_save_preset = fm.addAction(icon("save", T.text2, 16), "Save as preset…", self._save_house_preset)
        self.menu_presets = fm.addMenu(icon("house", T.text2, 16), "Your presets")
        self.menu_presets.aboutToShow.connect(self._fill_presets_menu)
        self.act_previous = fm.addAction("Back to your previous room", self._restore_previous_room)
        self.act_previous.setToolTip("The room you had before the last New, Open, example or preset")
        fm.aboutToShow.connect(lambda: self.act_previous.setEnabled(presets.previous_room_path().is_file()))
        self.menu_examples = fm.addMenu(icon("house", T.text2, 16), "Example houses")
        self.menu_examples.aboutToShow.connect(self._fill_examples_menu)
        sm = fm.addMenu("Save sound preset")
        sm.addAction("Weather preset…", lambda: self.dock.save_preset("weather"))
        sm.addAction("Sound mix preset…", lambda: self.dock.save_preset("mix"))
        for a in (self.act_new, self.act_open, self.act_save, self.act_save_as):
            a.setShortcutContext(QtCore.Qt.WindowShortcut)
            self.addAction(a)  # shortcuts work with the menu closed

        gm = self.app_bar.gear_menu
        gm.addAction(icon("sliders", T.text2, 16), "Output setup…", self._open_output_setup)
        gm.addAction(icon("copy", T.text2, 16), "Copy sound settings (JSON)", self._copy_sound_settings)
        gm.addSeparator()
        gm.addAction(icon("help", T.text2, 16), "Help && shortcuts", self._show_help)
        gm.addAction(icon("info", T.text2, 16), f"About {PROJECT_NAME}…", self._show_about)
        gm.addSeparator()
        gm.addAction(icon("link", T.text2, 16), "Open project page", self._open_project_page)
        gm.addAction("Copy project URL", self._copy_project_url)

    def _fill_examples_menu(self) -> None:
        m = self.menu_examples
        m.clear()
        items = presets.example_houses()
        if not items:
            a = m.addAction("No example houses found")
            a.setEnabled(False)
        for name, desc, path in items:
            a = m.addAction(name, lambda p=path: self._load_house_path(p))
            a.setToolTip(desc or name)
        m.setToolTipsVisible(True)

    def _fill_presets_menu(self) -> None:
        m = self.menu_presets
        m.clear()
        items = presets.house_presets()
        if not items:
            a = m.addAction("No presets yet — Save as preset… first")
            a.setEnabled(False)
        for name, path in items:
            a = m.addAction(name, lambda n=name, p=path: self._open_house_preset(n, p))
            a.setCheckable(True)
            a.setChecked(name == self.ctx.preset_name)

    def _wire(self) -> None:
        self.app_bar.modeChanged.connect(self.set_mode)
        self.app_bar.nameEdited.connect(lambda _t: self._update_title())
        self.app_bar.weather_popup.chips.chosen.connect(self._on_quick_weather)
        self.app_bar.weather_popup.btn_show_dock.clicked.connect(self._show_dock_from_popup)
        self.app_bar.weather_menu.aboutToShow.connect(self._sync_quick_weather)
        self.palette.toolChosen.connect(self.canvas.set_tool)
        self.palette.fitRequested.connect(self._fit_view)
        self.inspector.house.cmb_example.activated.connect(self._load_example_house)
        self.inspector.house.cmb_preset.activated.connect(self._load_house_preset)
        self.inspector.house.btn_save_preset.clicked.connect(self._save_house_preset)
        self.inspector.house.btn_delete_preset.clicked.connect(self._delete_house_preset)
        self.dock.weatherChanged.connect(self._update_weather_button)
        self.dock.collapseRequested.connect(lambda: self.set_dock_collapsed(True, user=True))
        self.dock_tab.clicked.connect(lambda: self.set_dock_collapsed(False, user=True))
        self.ctx.dirtyChanged.connect(lambda _d: self._update_title())
        self.ctx.roomEdited.connect(self._on_room_edited)

    # ------------------------------------------------------------ phones
    def _build_remote(self) -> None:
        """Phones in other rooms: the bridge owns the server; the app bar shows it."""
        from app.ui.studio.remote_bridge import RemoteBridge

        self.remote = RemoteBridge(self.ctx, self)
        self.remote.preset_getter = self.dock.current_weather
        self.remote.weatherApplied.connect(self._on_phone_weather)
        self.remote.stateChanged.connect(self._update_phone_button)
        self.remote.zonesChanged.connect(self._update_phone_button)
        self.app_bar.btn_phone.clicked.connect(self._open_phone_dialog)
        self._phone_dialog = None
        self._update_phone_button()
        self.remote.autostart()

    def _update_phone_button(self) -> None:
        self.app_bar.set_phone_state(self.remote.status in ("on", "starting"), self.remote.connected_count())

    def _on_phone_weather(self, _changes, preset_name) -> None:
        """A phone changed the shared weather (already written to the room)."""
        if preset_name:
            self.dock._active_preset["weather"] = preset_name
        else:
            self.dock._active_preset.pop("weather", None)
        self.dock.sync_settings_ui()   # sliders + chip; weatherChanged updates the app bar

    def _open_phone_dialog(self) -> None:
        from app.ui.studio.phone_dialog import PhoneDialog

        if self._phone_dialog is None:
            self._phone_dialog = PhoneDialog(self.ctx, self.remote, self)
        self._phone_dialog.refresh()
        self._phone_dialog.show()
        self._phone_dialog.raise_()
        self._phone_dialog.activateWindow()

    # ------------------------------------------------------------ room → UI
    def _apply_room_to_ui(self) -> None:
        """Sync every widget from the room (startup / new / load), signals blocked."""
        if self.ctx.engine.room is not self.ctx.room:
            self.ctx.engine.room = self.ctx.room
        self.inspector.house.load()
        self.inspector.house.reload_examples()
        self.inspector.house.reload_presets()
        self.app_bar.load_name()
        self.dock.sync_settings_ui()
        self.transport.sync_volume()
        self.transport.load_headphones()
        self.canvas.room_replaced()
        self.objects.refresh()
        self._update_weather_button()

    def _on_room_edited(self, area: str, _src) -> None:
        if area == "canvas" and self.ctx.selected[0] == "none":
            self.inspector.house.load_footprint()

    def _update_weather_button(self) -> None:
        self.app_bar.set_weather(self.dock.current_weather(), rain_rate_text(self.ctx.room))
        self.app_bar.btn_weather.setToolTip(
            "Pick the weather — now: " + (self.dock.current_weather() or "your own mix (Custom)"))

    def _sync_quick_weather(self) -> None:
        chips = self.app_bar.weather_popup.chips
        chips.set_presets(self.dock.weather_chip_items())
        chips.set_current(self.dock.current_weather())
        self.app_bar.weather_popup.set_dock_shown(self.dock_tab.isHidden())
        self.app_bar.weather_popup.adjustSize()

    def _on_quick_weather(self, name: str) -> None:
        self.dock.on_weather_chip(name)
        self.app_bar.weather_menu.close()

    def _show_dock_from_popup(self) -> None:
        self.app_bar.weather_menu.close()
        self.set_dock_collapsed(False, user=True)

    def _update_title(self) -> None:
        self.setWindowTitle(f"{self.ctx.room.name or 'Untitled'}[*] — RainRoom")
        self.setWindowModified(self.ctx.dirty)

    # ------------------------------------------------------------ modes + chrome
    def set_mode(self, mode: str) -> None:
        mode = MODE_LISTEN if mode == MODE_LISTEN else MODE_DESIGN
        if mode == self._mode:
            self.app_bar.seg_mode.set_current(mode)
            return
        self._mode_view[self._mode] = self.canvas.current_view()
        self._mode = mode
        self.app_bar.seg_mode.set_current(mode)
        listen = mode == MODE_LISTEN
        if listen:
            self.canvas.set_tool(TOOL_SELECT)
            if self.dock_tab.isVisible():
                self.set_dock_collapsed(False)
        elif not self._dock_user_set and self.width() < 1180:
            self.set_dock_collapsed(True)
        self.canvas.set_edit_chrome(not listen)
        self.dock.set_large(listen)
        self.transport.set_large(listen)
        self.canvas.set_view(self._mode_view.get(mode, VIEW_3D if listen else VIEW_PLAN))

    def mode(self) -> str:
        return self._mode

    def _focus_speaker(self, idx: int) -> None:
        """Show one speaker's settings (its "Plays on" picker): those live in
        the inspector, which only Design mode shows."""
        self.set_mode(MODE_DESIGN)
        self.ctx.select(("speaker", idx), force=True)

    def set_dock_collapsed(self, on: bool, user: bool = False) -> None:
        if user:
            self._dock_user_set = True
        self.dock.setVisible(not on)
        self.dock_tab.setVisible(on)

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        super().resizeEvent(e)
        # Small windows: fold the dock to its edge tab so the canvas keeps room
        # (Listen mode is about the dock, so it stays open there).
        if not self._dock_user_set and self._mode == MODE_DESIGN:
            narrow = self.width() < 1180
            if narrow != self.dock_tab.isVisible():
                self.set_dock_collapsed(narrow)

    def _fit_view(self) -> None:
        self.canvas.fit_view()
        self.ctx.say("View fitted to the house", 2000)

    # ------------------------------------------------------------ selection (kept for scripts/tests)
    def _on_selection(self, sel) -> None:
        self.ctx.select(tuple(sel), force=True)

    # ------------------------------------------------------------ keyboard
    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        key = e.key()
        focus = QtWidgets.QApplication.focusWidget()
        typing = is_text_input(focus) or isinstance(focus, QtWidgets.QComboBox)
        mods = e.modifiers() & ~QtCore.Qt.KeypadModifier
        if key == QtCore.Qt.Key_Escape:
            if typing and isinstance(focus, QtWidgets.QWidget):
                focus.clearFocus()
            elif self.canvas.tool != TOOL_SELECT:
                self.canvas.set_tool(TOOL_SELECT)
            else:
                self.ctx.select(NONE_SEL)
            e.accept()
            return
        if typing or mods not in (QtCore.Qt.NoModifier, QtCore.Qt.ShiftModifier):
            super().keyPressEvent(e)
            return
        if key in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            if self._mode == MODE_DESIGN:
                self.ctx.delete_selected()
            e.accept()
            return
        if key == QtCore.Qt.Key_Space:
            self.transport.toggle()
            e.accept()
            return
        if key == QtCore.Qt.Key_F:
            self._fit_view()
            e.accept()
            return
        letter = e.text().upper()
        if self._mode == MODE_DESIGN and letter in TOOL_KEYS and mods == QtCore.Qt.NoModifier:
            self.canvas.set_tool(TOOL_KEYS[letter])
            e.accept()
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------ files
    @staticmethod
    def _load_startup_preset():
        """The room you were working on (autosaved to configs/my_house.json),
        else the default house: (room, Save path, preset it came from)."""
        root = _REPO
        auto = presets.autosave_path()
        candidates = (
            auto,
            Path("configs/my_house.json"),
            root / "configs" / "my_house.json",
            Path("configs/default_house.json"),
            root / "configs" / "default_house.json",
        )
        for p in candidates:
            if p.is_file():
                try:
                    room = load_room(str(p))
                except Exception:
                    continue
                if p.name != "my_house.json" and p != auto:
                    return room, None, None
                try:
                    from_preset = json.loads(p.read_text(encoding="utf-8")).get("from_preset") or None
                except (OSError, ValueError, AttributeError):
                    from_preset = None
                return room, str(p.resolve()), from_preset
        return default_house()

    def _write_working_room(self, path: Path) -> None:
        from app.utils.persistence import room_to_dict

        data = room_to_dict(self.ctx.room)
        if self.ctx.preset_name:
            data["from_preset"] = self.ctx.preset_name
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _autosave_now(self) -> None:
        """Keep the room you're working on for next time (never a preset file)."""
        self._autosave.stop()
        try:
            self._write_working_room(presets.autosave_path())
        except Exception:
            log.exception("autosave failed")

    def _replace_room(self, room: Room, path=None, preset_name=None) -> None:
        """Swap in another house; the one you had is kept for "Back to your previous room"."""
        try:
            self._write_working_room(presets.previous_room_path())
        except Exception:
            log.exception("keeping the previous room failed")
        self.transport.stop(quiet=True)
        self.ctx.replace_room(room, path, preset_name=preset_name)
        self._apply_room_to_ui()
        self._update_title()

    def _restore_previous_room(self) -> bool:
        """Swap back to the room you had before (doing it again swaps forward)."""
        p = presets.previous_room_path()
        try:
            room = load_room(str(p))
            from_preset = json.loads(p.read_text(encoding="utf-8")).get("from_preset") or None
        except Exception as e:
            QtWidgets.QMessageBox.information(self, "Back to your previous room",
                                              f"There's no previous room to go back to.\n\n{e}")
            return False
        self._replace_room(room, None, preset_name=from_preset)
        self.ctx.say(f"Back to your previous room (“{room.name}”)")
        return True

    # ----- your presets -----
    def _load_house_preset(self, index: int) -> None:
        cmb = self.inspector.house.cmb_preset
        path = cmb.itemData(index)
        if path:
            self._open_house_preset(cmb.itemText(index), path)

    def _open_house_preset(self, name: str, path) -> bool:
        """Copy a preset into the room you're working on; the preset file stays as it was."""
        if self.ctx.dirty and not self._confirm(
                "Open preset",
                f"Opening “{name}” replaces the room you're working on.\n"
                "Changes you haven't saved as a preset will be lost.\n\nOpen it?"):
            self.inspector.house.reload_presets()
            return False
        try:
            room = load_room(str(path))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Open preset failed", str(e))
            self.inspector.house.reload_presets()
            return False
        self._replace_room(room, None, preset_name=name)
        self.ctx.say(f"Opened preset “{name}”")
        return True

    def _save_house_preset(self) -> bool:
        self.ctx.commit_pending_edit()
        r = self.ctx.room
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Save as preset", "Preset name:", QtWidgets.QLineEdit.Normal,
            self.ctx.preset_name or r.name or "My house")
        name = str(name or "").strip()
        if not ok or not name:
            return False
        if presets.house_preset_path(name) is not None and name.lower() != str(self.ctx.preset_name or "").lower():
            if not self._confirm("Save as preset", f"There's already a preset called “{name}”. Replace it?"):
                return False
        try:
            presets.save_house_preset(r, name)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save preset failed", str(e))
            return False
        self.ctx.preset_name = name
        self.ctx.set_dirty(False)
        self.inspector.house.reload_presets()
        self._autosave_now()
        self.ctx.say(f"Saved preset “{name}”")
        return True

    def _delete_house_preset(self) -> bool:
        name = self.inspector.house.picked_preset()
        if not name or not self._confirm("Delete preset", f"Delete the preset “{name}”? Your room stays as it is."):
            return False
        presets.delete_house_preset(name)
        if str(self.ctx.preset_name or "").lower() == name.lower():
            self.ctx.preset_name = None
        self.inspector.house.reload_presets()
        self._autosave.start()
        self.ctx.say(f"Deleted preset “{name}”")
        return True

    def _new_house(self) -> None:
        self._replace_room(default_house())
        self.ctx.say("New house ready")

    def _open_house(self) -> None:
        start = str(Path(self.ctx.path).parent) if self.ctx.path else str(_configs_dir())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open house", start, "House files (*.json)")
        if path:
            self._load_house_path(path)

    def _load_house_path(self, path) -> bool:
        """Shared by Open…, the example menu and the example picker."""
        try:
            room = load_room(str(path))  # parse first: a bad file leaves the rain playing
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load failed", str(e))
            return False
        is_example = Path(path).resolve().parent == presets.HOUSES_DIR.resolve()
        self._replace_room(room, None if is_example else str(path))
        self.ctx.say(f"Loaded “{room.name}”" + (" (example — Save as… to keep your changes)" if is_example else ""))
        return True

    def _load_example_house(self, index: int) -> None:
        path = self.inspector.house.cmb_example.itemData(index)
        if path:
            self._load_house_path(path)

    def _save(self) -> bool:
        if not self.ctx.path:
            return self._save_as()
        return self._write(self.ctx.path)

    def _save_as(self) -> bool:
        start = self.ctx.path or str(_configs_dir() / f"{presets._slug(self.ctx.room.name or 'my house')}.json")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save house as", start, "House files (*.json)")
        if not path:
            return False
        return self._write(path)

    def _write(self, path: str) -> bool:
        self.ctx.commit_pending_edit()  # Ctrl+S while typing a name: save what is typed
        try:
            save_room(self.ctx.room, path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e))
            return False
        self.ctx.path = str(path)
        self.ctx.set_dirty(False)
        self.ctx.say(f"Saved {path}")
        return True

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        self.transport.stop(quiet=True)
        self._autosave_now()
        remote = getattr(self, "remote", None)
        if remote is not None:
            remote.shutdown()          # phone server + every phone's rain; never waits
        if getattr(self, "_phone_dialog", None) is not None:
            self._phone_dialog.close()
        super().closeEvent(e)

    # ------------------------------------------------------------ dialogs
    def _confirm(self, title: str, text: str) -> bool:
        ans = QtWidgets.QMessageBox.question(self, title, text,
                                             QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        return ans == QtWidgets.QMessageBox.Yes

    def _open_output_setup(self) -> None:
        from app.ui.studio.devices_dialog import OutputSetupDialog

        OutputSetupDialog(self.ctx, self).exec()

    def _sound_settings_dict(self) -> dict:
        room = self.ctx.room
        out = {"master_volume": round(schema.get(room, "master_volume"), 3)}
        for s in schema.SETTINGS:
            v = schema.get(room, s.key)
            out[s.key] = bool(v) if s.kind == "toggle" else round(v, 3)
        out["roof_material"] = str(getattr(room, "roof_material", ""))
        out["wall_material"] = str(getattr(room, "wall_material", ""))
        return out

    def _copy_sound_settings(self) -> None:
        QtWidgets.QApplication.clipboard().setText(json.dumps(self._sound_settings_dict(), indent=2))
        self.ctx.say("Sound settings copied to the clipboard (JSON)", 4000)

    def _open_project_page(self) -> None:
        if webbrowser.open(PROJECT_URL):
            self.ctx.say(f"Opened {PROJECT_URL}")
        else:
            QtWidgets.QMessageBox.information(self, "Project page", f"Open this URL in your browser:\n\n{PROJECT_URL}")

    def _copy_project_url(self) -> None:
        QtWidgets.QApplication.clipboard().setText(PROJECT_URL)
        self.ctx.say("Project URL copied to the clipboard")

    def _show_help(self) -> None:
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Help")
        box.setTextFormat(QtCore.Qt.RichText)
        box.setText(
            "<h3>Getting started</h3>"
            "<p><b>1.</b> Shape the house in <b>Design</b>: click the house to set its size and materials, "
            "click a wall with the window tool to add windows.</p>"
            "<p><b>2.</b> Put <b>You</b> where you sit, and add a speaker for every real speaker "
            "in the room. Choose what each one <i>plays on</i>.</p>"
            "<p><b>3.</b> Pick an <b>Output</b> in the bar at the bottom and press <b>Play</b>. "
            "Switch to <b>Listen</b> to focus on the rain.</p>"
            "<h3>Keyboard</h3>"
            "<p><b>Space</b> play / stop · <b>Delete</b> remove the selected window or speaker · "
            "<b>Esc</b> cancel a tool / deselect · <b>F</b> fit view<br>"
            "Tools: <b>V</b> select · <b>W</b> window · <b>S</b> speaker · <b>Y</b> move You · <b>R</b> resize<br>"
            "<b>Ctrl+N</b> new · <b>Ctrl+O</b> open · <b>Ctrl+S</b> save · <b>Ctrl+Shift+S</b> save as</p>"
            "<h3>Views</h3>"
            "<p>Plan: right-drag to pan, scroll to zoom. 3D: right-drag to orbit, scroll to zoom, "
            "drag the coloured arrows to move things.</p>"
        )
        box.exec()

    def _show_about(self) -> None:
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(f"About {PROJECT_NAME}")
        box.setTextFormat(QtCore.Qt.RichText)
        box.setText(
            f"<h3>{PROJECT_NAME} {__version__}</h3>"
            "<p>Design your house on a terrain map, map real speakers, "
            "and simulate 3D outdoor rain through your windows.</p>"
            f"<p><b>Project page:</b><br><a href=\"{PROJECT_URL}\">{PROJECT_URL}</a></p>"
            "<p><b>How to listen in 3D:</b> pick Output “You (headphones)” on a stereo headphone "
            "device, then press Play.</p>"
            "<p><b>Rain &amp; sound panel:</b> weather presets, wind, sound mix and drop character — "
            "all saved with your house.</p>"
        )
        box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        open_btn = box.addButton("Open project page", QtWidgets.QMessageBox.ActionRole)
        box.exec()
        if box.clickedButton() is open_btn:
            self._open_project_page()


__all__ = ["PROJECT_NAME", "PROJECT_URL", "StudioWindow"]
