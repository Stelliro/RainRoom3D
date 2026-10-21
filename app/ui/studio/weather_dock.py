"""Right dock: Weather, Sound mix and Drop character.

Every control is built FROM ``settings_schema`` (labels, ranges, help,
groups, advanced flags), so a knob is defined in exactly one place. Presets
come from ``presets``; matching runs on the cached list (no disk I/O while a
slider moves).
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6 import QtCore, QtWidgets

from app.models import presets
from app.models import settings_schema as schema
from app.ui.studio.context import StudioContext
from app.ui.studio.controls import (
    ToggleSwitch, _from_ticks, _ticks, block, button, combo, hbox, hint, icon_button, label,
    overline, scroll_area, set_prop, slider,
)
from app.ui.studio.icons import icon
from app.ui.theme import SP, T
from app.ui.widgets import CollapsibleSection, PresetChips, WindDirectionWheel

RAIN_REDRAW_KEYS = ("droplet_density", "rain_intensity")  # change the drawn 3D rain


def rain_rate_text(room) -> str:
    """"12 mm/h" from the Amount setting (or "no rain")."""
    shown = schema.by_key("droplet_density").display(schema.get(room, "droplet_density"))
    if shown == "off":
        return "no rain"
    return shown.split(" · ")[-1]


def _amount_ladder():
    """(upper bound, name) bands from the built-in weather presets' Amounts, so
    the Amount caption speaks the same words as the chips ("Steady rain"). Storm
    is left out: it is a wind preset, not a rain amount."""
    pts = sorted((float(p.values["droplet_density"]), p.name) for p in presets.builtin("weather")
                 if "droplet_density" in p.values and float(p.values.get("wind_speed", 0.0)) < 0.5)
    bands = [((a + b) / 2.0, name) for (a, name), (b, _n) in zip(pts, pts[1:])]
    return bands + [(float("inf"), pts[-1][1])] if pts else []


_LADDER = None


def amount_text(q: float) -> str:
    """Amount caption: "Steady rain · 6.1 mm/h" (or "off")."""
    global _LADDER
    shown = schema.by_key("droplet_density").display(q)
    if shown == "off" or " · " not in shown:
        return shown
    if _LADDER is None:
        _LADDER = _amount_ladder()
    name = next((n for hi, n in _LADDER if float(q) < hi), "")
    rate = shown.split(" · ")[-1]
    return f"{name} · {rate}" if name else shown


def _display(s, v) -> str:
    return amount_text(v) if s.key == "droplet_density" else s.display(v)


class SoundDock(QtWidgets.QFrame):
    weatherChanged = QtCore.Signal()          # preset or amount changed → app bar label
    collapseRequested = QtCore.Signal()

    WIDTH = 340
    WIDTH_LARGE = 400

    def __init__(self, ctx: StudioContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("Dock")
        self.setProperty("large", False)
        self.setFixedWidth(self.WIDTH)
        self._setting_rows: Dict[str, QtWidgets.QWidget] = {}
        self._setting_inputs: Dict[str, QtWidgets.QWidget] = {}
        self._setting_values: Dict[str, QtWidgets.QLabel] = {}
        self._sections: Dict[str, QtWidgets.QWidget] = {}
        self._reset_buttons: Dict[str, QtWidgets.QPushButton] = {}
        self._active_preset: Dict[str, str] = {}
        self._presets: Dict[str, Dict[str, presets.Preset]] = {}
        # Hooks (tests stub these)
        self.ask_preset_name = self._ask_preset_name
        self.confirm = self._confirm

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QtWidgets.QFrame()
        head.setObjectName("DockHeader")
        hl = QtWidgets.QHBoxLayout(head)
        hl.setContentsMargins(20, 0, 10, 0)
        hl.setSpacing(8)
        head.setFixedHeight(48)
        ic = QtWidgets.QLabel()
        ic.setPixmap(icon("cloud_rain", T.accent, 18).pixmap(18, 18))
        hl.addWidget(ic)
        hl.addWidget(label("Rain & sound", "DockTitle"))
        hl.addStretch(1)
        self.btn_collapse = icon_button("panel_right", "Hide this panel", size=30, icon_size=18)
        self.btn_collapse.clicked.connect(self.collapseRequested.emit)
        hl.addWidget(self.btn_collapse)
        outer.addWidget(head)

        inner = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(inner)
        lay.setContentsMargins(20, 16, 16, 20)
        lay.setSpacing(2 * SP)
        self._build_weather(lay)
        self._build_group_section(lay, "mix")
        self._build_group_section(lay, "drop")
        lay.addSpacing(SP)
        lay.addWidget(hint("Changes are heard live while rain plays and are saved with the house. "
                           "Hover any control for a one-line explanation."))
        lay.addStretch(1)
        self.scroll = scroll_area(inner)
        outer.addWidget(self.scroll, 1)

        self.weather_chips.chosen.connect(self.on_weather_chip)
        self.btn_weather_save.clicked.connect(lambda: self.save_preset("weather"))
        self.btn_weather_del.clicked.connect(lambda: self.delete_preset("weather"))
        self.cmb_mix_preset.activated.connect(self.on_mix_preset)
        self.btn_mix_save_preset.clicked.connect(lambda: self.save_preset("mix"))
        self.btn_mix_del_preset.clicked.connect(lambda: self.delete_preset("mix"))
        for gid, btn in self._reset_buttons.items():
            btn.clicked.connect(lambda _=False, g=gid: self.reset_group(g))
        ctx.roomReplaced.connect(self._on_room_replaced)
        self.reload_presets()

    # ================================================================ build
    def _build_weather(self, lay: QtWidgets.QVBoxLayout) -> None:
        sec = QtWidgets.QWidget()
        sl = QtWidgets.QVBoxLayout(sec)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(2 * SP)
        self._sections["weather"] = sec
        self._sections["rain"] = sec
        sl.addWidget(overline("Weather"))
        self.weather_chips = PresetChips(columns=3)
        sl.addWidget(self.weather_chips)
        self.btn_weather_save = button("Save as preset…", "Ghost", "Keep the current rain + wind as your own "
                                       "weather chip", "plus")
        self.btn_weather_del = button("Delete preset", "Ghost", "Delete the selected weather preset you saved "
                                      "(built-ins stay)", "trash")
        sl.addLayout(hbox(self.btn_weather_save, self.btn_weather_del, None, spacing=4))
        for s in schema.in_group("rain", advanced=False):
            sl.addWidget(self._make_setting_row(s))

        sl.addSpacing(4)
        wind_head = QtWidgets.QHBoxLayout()
        wic = QtWidgets.QLabel()
        wic.setPixmap(icon("wind", T.text3, 14).pixmap(14, 14))
        wind_head.addWidget(wic)
        wind_head.addWidget(overline("Wind"))
        wind_head.addStretch(1)
        sl.addLayout(wind_head)
        self._sections["wind"] = sec
        basic = schema.in_group("wind", advanced=False)
        toggles = [s for s in basic if s.kind == "toggle"]
        for s in basic:
            if s.kind != "toggle":
                sl.addWidget(self._make_setting_row(s))
        if toggles:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(2 * SP)
            for s in toggles:
                row.addWidget(self._make_setting_row(s))
            row.addStretch(1)
            sl.addLayout(row)
        adv = schema.in_group("wind", advanced=True)
        if adv:
            more = CollapsibleSection("More wind options", expanded=False, nested=True)
            for s in adv:
                more.add_widget(self._make_setting_row(s))
            self._sections["wind_more"] = more
            sl.addWidget(more)

        sl.addSpacing(4)
        th_head = QtWidgets.QHBoxLayout()
        tic = QtWidgets.QLabel()
        tic.setPixmap(icon("bolt", T.text3, 14).pixmap(14, 14))
        th_head.addWidget(tic)
        th_head.addWidget(overline("Thunder"))
        th_head.addStretch(1)
        sl.addLayout(th_head)
        self._sections["thunder"] = sec
        for s in schema.in_group("thunder"):
            sl.addWidget(self._make_setting_row(s))
        lay.addWidget(sec)

    def _build_group_section(self, lay: QtWidgets.QVBoxLayout, gid: str) -> None:
        title, subtitle = next((t, st) for g, t, st in schema.GROUPS if g == gid)
        sec = CollapsibleSection(title, subtitle, expanded=False)
        self._sections[gid] = sec
        if gid == "mix":
            self.cmb_mix_preset = combo(tip="Ready-made balances of the faders below")
            self.btn_mix_save_preset = icon_button("plus", "Save the current faders as your own mix preset",
                                                   size=32, icon_size=16)
            self.btn_mix_del_preset = icon_button("trash", "Delete the selected mix preset you saved "
                                                  "(built-ins stay)", size=32, icon_size=16)
            row = hbox(self.cmb_mix_preset, self.btn_mix_save_preset, self.btn_mix_del_preset, spacing=4)
            row.setStretch(0, 1)
            sec.add_layout(row)
        for s in schema.in_group(gid, advanced=False):
            sec.add_widget(self._make_setting_row(s))
        adv = schema.in_group(gid, advanced=True)
        if adv:
            more = CollapsibleSection(f"More {title.lower()} options", expanded=False, nested=True)
            for s in adv:
                more.add_widget(self._make_setting_row(s))
            self._sections[f"{gid}_more"] = more
            sec.add_widget(more)
        btn = button("Reset to recommended", "Ghost", f"Put every {title.lower()} control back to how the app "
                     "ships")
        self._reset_buttons[gid] = btn
        sec.add_layout(hbox(btn, None))
        lay.addWidget(sec)

    def _make_setting_row(self, s: "schema.Setting") -> QtWidgets.QWidget:
        """One control for one Setting: captioned slider, switch, or the wind wheel.
        Every control writes through on_setting, so all behave the same."""
        row = QtWidgets.QWidget()
        row.setToolTip(s.help)
        v = QtWidgets.QVBoxLayout(row)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        val = None
        if s.kind == "toggle":
            w = ToggleSwitch(s.label)
            w.setToolTip(s.help)
            w.toggled.connect(lambda on, k=s.key: self.on_setting(k, on))
            v.addWidget(w)
        elif s.kind == "dial":
            w = WindDirectionWheel(size=112)
            w.setToolTip(s.help + "\n0° = North, 90° = East, 180° = South, 270° = West.")
            w.valueChanged.connect(lambda d, k=s.key: self.on_setting(k, float(d)))
            val = label("", "SetValue", wrap=True)
            side = QtWidgets.QVBoxLayout()
            side.setSpacing(4)
            side.addWidget(label(s.label, "SetName"))
            side.addWidget(val)
            side.addStretch(1)
            v.addLayout(hbox(w, side, spacing=2 * SP))
        else:
            name = label(s.label, "SetName")
            val = label("", "SetValue")
            val.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            v.addLayout(hbox(name, None, val))
            w = slider(0, _ticks(s, s.hi), s.help)
            w.valueChanged.connect(lambda t, k=s.key: self.on_setting(k, _from_ticks(schema.by_key(k), t)))
            v.addWidget(w)
            if s.left or s.right:
                lab_l, lab_r = label(s.left, "EndCap"), label(s.right, "EndCap")
                lab_r.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                v.addLayout(hbox(lab_l, None, lab_r, spacing=0))
        self._setting_rows[s.key] = row
        self._setting_inputs[s.key] = w
        if val is not None:
            self._setting_values[s.key] = val
        return row

    # ================================================================ values
    def on_setting(self, key: str, raw) -> None:
        """Single write path for every control on this dock."""
        s = schema.by_key(key)
        stored = schema.set(self.ctx.room, key, raw)
        lbl = self._setting_values.get(key)
        if lbl is not None:
            lbl.setText(_display(s, stored))
        if s.kind == "toggle" or key == "wind_speed":
            self.sync_setting_enabled()
        self.ctx.edited("rain" if key in RAIN_REDRAW_KEYS else "sound", self)
        self.refresh_preset_state()

    def sync_setting_enabled(self) -> None:
        # A vary knob does nothing while its switch is off — grey it out. The
        # engine never swings the direction of a calm wind (strength ≤ 0.02),
        # so "Shifting direction" and its knobs grey out while calm.
        room = self.ctx.room
        windy = schema.get(room, "wind_speed") > 0.02
        calm_tip = "\n(Needs some wind Strength first.)"
        for s in schema.SETTINGS:
            row = self._setting_rows.get(s.key)
            if row is None:
                continue
            on = True
            if s.enabled_by:
                on = bool(schema.get(room, s.enabled_by))
            needs_wind = s.key == "wind_vary_direction" or s.enabled_by == "wind_vary_direction"
            if s.key == "thunder_distance":
                # Distance only matters once thunder is on
                stormy = schema.get(room, "thunder_rate") > 0.005
                row.setEnabled(stormy)
                tip = s.help + ("" if stormy else "\n(Turn up How often first.)")
                row.setToolTip(tip)
                self._setting_inputs[s.key].setToolTip(tip)
                continue
            if needs_wind:
                on = on and windy
                tip = s.help + ("" if windy else calm_tip)
                row.setToolTip(tip)
                self._setting_inputs[s.key].setToolTip(tip)
            if s.enabled_by or needs_wind:
                row.setEnabled(on)

    def sync_settings_ui(self) -> None:
        """Push room values into every widget with signals blocked, so load /
        new / preset / reset never echo back into the room."""
        room = self.ctx.room
        for key, w in self._setting_inputs.items():
            s = schema.by_key(key)
            v = schema.get(room, key)
            with block(w):
                if s.kind == "toggle":
                    w.setChecked(bool(v))
                elif s.kind == "dial":
                    w.setValue(int(round(v)) % 360)
                else:
                    w.setValue(_ticks(s, s.clamp(v)))
            lbl = self._setting_values.get(key)
            if lbl is not None:
                lbl.setText(_display(s, v))
        self.sync_setting_enabled()
        self.refresh_preset_state()

    def reset_group(self, group: str) -> None:
        schema.reset(self.ctx.room, group)
        self.sync_settings_ui()
        self.ctx.edited("rain" if group == "rain" else "sound", self)
        title = next((t for g, t, _ in schema.GROUPS if g == group), group)
        self.ctx.say(f"{title} reset to recommended", 3000)

    def _on_room_replaced(self) -> None:
        self._active_preset = {}
        self.sync_settings_ui()

    # ================================================================ presets
    def reload_presets(self) -> None:
        """Re-read built-in + user presets (after save / delete) and rebuild pickers."""
        self._presets = {k: {p.name: p for p in presets.all_presets(k)} for k in presets.KINDS}
        self.weather_chips.set_presets(self.weather_chip_items())
        cmb = self.cmb_mix_preset
        with block(cmb):
            cmb.clear()
            # "Custom" is shown, never picked: it only says the faders match no preset
            cmb.addItem(PresetChips.CUSTOM, userData=None)
            cmb.model().item(0).setEnabled(False)
            for p in self._presets["mix"].values():
                cmb.addItem(p.name if p.builtin else f"{p.name}  (yours)", userData=p.name)
                cmb.setItemData(cmb.count() - 1, p.description or p.name, QtCore.Qt.ToolTipRole)
        self.refresh_preset_state()

    def weather_chip_items(self):
        return [
            (p.name, p.description or p.name) if p.builtin
            else (p.name, f"Your preset — {p.description or p.name}", f"{p.name} (yours)")
            for p in self._presets.get("weather", {}).values()
        ]

    def is_user_preset(self, kind: str, name) -> bool:
        p = self._presets.get(kind, {}).get(name) if name else None
        return p is not None and not p.builtin

    def matching(self, kind: str) -> Optional[str]:
        """Like presets.matching(), but over the cached list (no disk reads)."""
        prefer = self._active_preset.get(kind)
        found = None
        for p in self._presets.get(kind, {}).values():
            if presets.fits(self.ctx.room, p):
                if prefer is None or p.name.lower() == str(prefer).lower():
                    return p.name
                found = found or p.name
        return found

    def refresh_preset_state(self) -> None:
        """Highlight the preset the room matches, or "Custom"."""
        if not self._presets:
            return
        w = self.matching("weather")
        self.weather_chips.set_current(w)
        self.btn_weather_del.setEnabled(self.is_user_preset("weather", w))
        m = self.matching("mix")
        idx = self.cmb_mix_preset.findData(m) if m else 0
        with block(self.cmb_mix_preset):
            self.cmb_mix_preset.setCurrentIndex(max(0, idx))
        self.btn_mix_del_preset.setEnabled(self.is_user_preset("mix", m))
        self.weatherChanged.emit()

    def current_weather(self) -> Optional[str]:
        return self.weather_chips.current()

    def apply_preset(self, kind: str, name: str) -> None:
        p = self._presets.get(kind, {}).get(name)
        if p is None:
            return
        presets.apply(self.ctx.room, p)
        # Remember the pick: a user copy of a built-in has the same values,
        # and matching alone would always highlight the built-in.
        self._active_preset[kind] = p.name
        self.sync_settings_ui()
        self.ctx.edited("rain" if kind == "weather" else "sound", self)
        what = "Weather" if kind == "weather" else "Sound mix"
        tail = f" — {p.description}" if p.description else ""
        self.ctx.say(f"{what}: {p.name}{tail}", 4000)

    def on_weather_chip(self, name: str) -> None:
        self.apply_preset("weather", name)

    def on_mix_preset(self, index: int) -> None:
        name = self.cmb_mix_preset.itemData(index)
        if name:
            self.apply_preset("mix", name)
        else:
            self.refresh_preset_state()

    def _ask_preset_name(self, kind: str) -> str:
        """Ask for a preset name ("" = cancelled)."""
        what = "weather" if kind == "weather" else "sound mix"
        text, ok = QtWidgets.QInputDialog.getText(self.window(), f"Save {what} preset", "Name for this preset:",
                                                  QtWidgets.QLineEdit.Normal, "")
        return text.strip() if ok else ""

    def _confirm(self, title: str, text: str) -> bool:
        return self.ctx.confirm(title, text)

    def save_preset(self, kind: str) -> None:
        name = self.ask_preset_name(kind)
        if not name:
            return
        # Names that differ only in punctuation share a file ("Rain!" / "Rain?"
        # → rain.json), so compare by file slug as well as by name.
        slug = presets._slug(name)
        clash = next((p.name for p in self._presets.get(kind, {}).values()
                      if p.name.lower() == name.lower()
                      or (not p.builtin and presets._slug(p.name) == slug)), None)
        if clash is not None:
            what = f"“{clash}”" if clash == name else f"“{clash}” (same file as “{name}”)"
            if not self.confirm("Replace preset", f"A preset called {what} already exists. Replace it?"):
                return
        keys = presets.WEATHER_KEYS if kind == "weather" else presets.MIX_KEYS
        try:
            presets.save_user(kind, name, schema.values(self.ctx.room, keys))
        except (OSError, ValueError) as e:
            QtWidgets.QMessageBox.warning(self.window(), "Save preset failed", str(e))
            return
        self._active_preset[kind] = name
        self.reload_presets()
        self.ctx.say(f"Saved preset “{name}”", 3000)

    def delete_preset(self, kind: str) -> None:
        name = self.weather_chips.current() if kind == "weather" else self.cmb_mix_preset.currentData()
        if not self.is_user_preset(kind, name):
            return
        if not self.confirm("Delete preset", f"Delete your preset “{name}”?"):
            return
        presets.delete_user(kind, name)
        self._active_preset.pop(kind, None)
        self.reload_presets()
        self.ctx.say(f"Deleted preset “{name}”", 3000)

    # ================================================================ looks
    def set_large(self, on: bool) -> None:
        """Listen mode: bigger labels and handles, a little wider."""
        self.setFixedWidth(self.WIDTH_LARGE if on else self.WIDTH)
        set_prop(self, "large", bool(on))
        for w in self.findChildren(QtWidgets.QWidget):
            st = w.style()
            st.unpolish(w)
            st.polish(w)


__all__ = ["SoundDock", "rain_rate_text"]
