"""Inspector page shown when nothing is selected: the house itself."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from app.models import presets
from app.models import settings_schema as schema
from app.models.materials import ROOF_MATERIALS, WALL_MATERIALS, role_material
from app.ui.studio.context import StudioContext
from app.ui.studio.controls import Form, block, button, caption, combo, guard_wheel, hbox, hint, line_edit, spin

ROOF_T_TIP = ("How thick the roof is. A thicker roof lets less rain through and sounds duller;\n"
              "a really thick one mutes the rain above you. Leave empty for the usual build.")
WALL_T_TIP = ("How thick the walls are. Thicker walls let less outside rain through, sound duller\n"
              "and set the windows deeper; really thick walls (with their heavier glazing) mute it.\n"
              "Open windows still let the yard in. Leave empty for the usual build.")
THICK_MAX_CM = 150.0


class HousePage(QtWidgets.QWidget):
    title = "House"

    def __init__(self, ctx: StudioContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        f = self.form = Form()
        lay.addWidget(f)

        f.section("Your presets", first=True)
        self.cmb_preset = combo(tip="Open one of your saved houses, exactly as you saved it\n"
                                    "(replaces the room you're working on)")
        f.full(self.cmb_preset)
        self.btn_save_preset = button("Save as preset…", "", "Keep this house — size, materials, windows, "
                                      "speakers, You and every sound setting — as a preset you can open again",
                                      "save")
        self.btn_delete_preset = button("Delete", "", "Delete the preset picked above (asks first)")
        f.full(hbox(self.btn_save_preset, self.btn_delete_preset, None))
        self.lbl_preset = caption()
        f.full(self.lbl_preset)

        f.section("Start from an example")
        self.cmb_example = combo(tip="Load a ready-made house (replaces the current one, like Open…)")
        f.full(self.cmb_example)

        f.section("Size")
        self.spin_w = spin(2.0, 30.0, 0.1, 2, " m", "East–west length of the house")
        self.spin_d = spin(2.0, 30.0, 0.1, 2, " m", "North–south length of the house")
        self.spin_h = spin(2.0, 6.0, 0.05, 2, " m", "Floor to ceiling")
        self.spin_terrain = spin(12.0, 120.0, 1.0, 0, " m", "How much outdoor ground (and rain) surrounds the house")
        f.row("Width", self.spin_w)
        f.row("Depth", self.spin_d)
        f.row("Ceiling", self.spin_h)
        f.row("Yard size", self.spin_terrain)

        f.section("Materials")
        self.cmb_roof = combo(list(ROOF_MATERIALS), "What the roof is made of — it changes how the rain overhead sounds")
        self.cmb_wall = combo(list(WALL_MATERIALS), "What the walls are made of (windows are always glass panes) — "
                              "it changes how blown rain and the wind sound")
        self.lbl_roof_hint = caption()
        self.lbl_wall_hint = caption()
        self.ed_roof_t, roof_row = self._thickness_field(ROOF_T_TIP)
        self.ed_wall_t, wall_row = self._thickness_field(WALL_T_TIP)
        f.row("Roof", self.cmb_roof)
        f.full(self.lbl_roof_hint)
        f.row("Roof thickness", roof_row, ROOF_T_TIP)
        f.spacer(4)
        f.row("Walls", self.cmb_wall)
        f.full(self.lbl_wall_hint)
        f.row("Wall thickness", wall_row, WALL_T_TIP)

        f.section("Sound outputs")
        self.lbl_routing = caption()
        f.full(self.lbl_routing)
        self.btn_outputs = button("Output setup…", "", "See your audio devices and choose what plays where",
                                  "sliders")
        self.btn_outputs.clicked.connect(lambda: self.ctx.open_output_setup())
        f.full(hbox(self.btn_outputs, None))
        # House-wide speaker layout (replaces every speaker, so it lives here, not on one speaker)
        self.spin_even = QtWidgets.QSpinBox()
        self.spin_even.setRange(1, 8)
        self.spin_even.setValue(3)
        self.spin_even.setSuffix(" speakers")
        self.spin_even.setMinimumWidth(110)
        self.spin_even.setToolTip("How many speakers to place")
        guard_wheel(self.spin_even)
        self.btn_even = button("Spread evenly", "", "Replace the room speakers with this many, "
                               "spread evenly around the room (asks first)")
        self.btn_even.clicked.connect(lambda: self.ctx.spread_speakers(self.spin_even.value()))
        f.full(hbox(self.spin_even, self.btn_even, None))
        f.full(hint("Tip: click a window, speaker or You in the view to edit it. "
                    "Windows face the outdoor weather."))

        self._inputs = (self.spin_w, self.spin_d, self.spin_h, self.spin_terrain, self.cmb_roof,
                        self.cmb_wall, self.ed_roof_t, self.ed_wall_t)
        # Late-bound lambdas (tests wrap _set_dim / _set_terrain on the class)
        self.spin_w.valueChanged.connect(lambda v: self._set_dim("width", v))
        self.spin_d.valueChanged.connect(lambda v: self._set_dim("depth", v))
        self.spin_h.valueChanged.connect(lambda v: self._set_dim("height", v))
        self.spin_terrain.valueChanged.connect(lambda v: self._set_terrain(v))
        self.cmb_roof.currentTextChanged.connect(self._set_roof)
        self.cmb_wall.currentTextChanged.connect(self._set_wall)
        self.ed_roof_t.textChanged.connect(lambda t: self._set_thickness("roof_thickness", t))
        self.ed_wall_t.textChanged.connect(lambda t: self._set_thickness("wall_thickness", t))
        self.cmb_preset.currentIndexChanged.connect(lambda _i: self._update_preset_buttons())
        self.reload_examples()
        self.reload_presets()

    # ----- building -----
    def _thickness_field(self, tip: str):
        """A cm box that's empty for 'usual': the usual build shows greyed in it."""
        ed = line_edit("", tip)
        val = QtGui.QDoubleValidator(0.0, THICK_MAX_CM, 1, ed)
        val.setNotation(QtGui.QDoubleValidator.StandardNotation)
        ed.setValidator(val)
        unit = caption("cm", wrap=False)
        return ed, hbox(ed, unit, spacing=6)

    def reload_examples(self) -> None:
        cmb = self.cmb_example
        with block(cmb):
            cmb.clear()
            cmb.addItem("Choose an example house…", userData=None)
            for name, desc, path in presets.example_houses():
                cmb.addItem(name, userData=str(path))
                cmb.setItemData(cmb.count() - 1, desc or name, QtCore.Qt.ToolTipRole)
            cmb.setCurrentIndex(0)
            cmb.setEnabled(cmb.count() > 1)

    def reload_presets(self) -> None:
        """Your saved houses; the one the room came from stays picked."""
        cmb = self.cmb_preset
        current = getattr(self.ctx, "preset_name", None)
        with block(cmb):
            cmb.clear()
            items = presets.house_presets()
            cmb.addItem("Open a preset…" if items else "No presets yet — Save as preset… below", userData=None)
            for name, path in items:
                cmb.addItem(name, userData=str(path))
            i = cmb.findText(current) if current else -1
            cmb.setCurrentIndex(max(0, i))
            cmb.setEnabled(bool(items))
        self._update_preset_buttons()

    def picked_preset(self) -> str:
        cmb = self.cmb_preset
        return cmb.currentText() if cmb.currentIndex() > 0 else ""

    def _update_preset_buttons(self) -> None:
        self.btn_delete_preset.setEnabled(bool(self.picked_preset()))
        name = getattr(self.ctx, "preset_name", None)
        if name:
            self.lbl_preset.setText(f"Opened from “{name}”. Your changes are kept in your room (it "
                                    "reopens next time); Save as preset… to update the preset.")
        else:
            self.lbl_preset.setText("Your room is saved automatically and reopens next time.")
        self.lbl_preset.setText(self.lbl_preset.text() + " Opening another house? File → Back to your "
                                "previous room brings this one back.")

    # ----- room → widgets -----
    def load(self) -> None:
        r = self.ctx.room
        with block(*self._inputs):
            self.spin_w.setValue(r.width)
            self.spin_d.setValue(r.depth)
            self.spin_h.setValue(r.height)
            self.spin_terrain.setValue(r.terrain_size)
            r.roof_material = role_material(r.roof_material, "roof")
            r.wall_material = role_material(r.wall_material, "wall")
            self._select(self.cmb_roof, r.roof_material)
            self._select(self.cmb_wall, r.wall_material)
            self.ed_roof_t.setText(self._cm_text(float(getattr(r, "roof_thickness", 0.0) or 0.0)))
            self.ed_wall_t.setText(self._cm_text(float(getattr(r, "wall_thickness", 0.0) or 0.0)))
        self._update_material_hints()
        self.update_routing()

    def load_footprint(self) -> None:
        """Cheap refresh after a canvas resize drag."""
        r = self.ctx.room
        with block(self.spin_w, self.spin_d):
            self.spin_w.setValue(r.width)
            self.spin_d.setValue(r.depth)

    @staticmethod
    def _select(cmb: QtWidgets.QComboBox, text: str) -> None:
        i = cmb.findText(text)
        if i >= 0:
            cmb.setCurrentIndex(i)

    @staticmethod
    def _cm_text(m: float) -> str:
        return "" if m <= 0.0 else f"{m * 100.0:g}"

    def _update_material_hints(self) -> None:
        r = self.ctx.room
        self.lbl_roof_hint.setText(schema.material_hint(r.roof_material, "roof"))
        self.lbl_wall_hint.setText(schema.material_hint(r.wall_material, "wall"))
        from app.audio import house_model as hm

        for ed, typ in ((self.ed_roof_t, hm.typical_roof_thickness(r)),
                        (self.ed_wall_t, hm.typical_wall_thickness(r))):
            ed.setPlaceholderText(f"default {typ * 100.0:g} cm")

    def update_routing(self) -> None:
        r = self.ctx.room
        assigned = r.assigned_speakers()
        on = [s for s in r.speakers if getattr(s, "enabled", True)]
        lines = [
            f"{len(r.windows)} window(s) · {len(r.speakers)} speaker(s), {len(on)} on · "
            f"{len(assigned)} connected to a device",
            f"You (headphones): {self.ctx.device_label(getattr(r.listener, 'audio_device', None))}",
        ]
        if not on:
            lines.append("Add a speaker to play rain on room speakers.")
        elif not assigned:
            lines.append("Pick a device for each speaker to play on speakers.")
        self.lbl_routing.setText("\n".join(lines))

    # ----- widgets → room -----
    def _set_dim(self, attr: str, val: float) -> None:
        setattr(self.ctx.room, attr, float(val))
        self.ctx.clamp_contents()  # keep speakers / You inside and under the ceiling
        self.ctx.edited("house", self)

    def _set_terrain(self, v: float) -> None:
        self.ctx.room.terrain_size = float(v)
        self.ctx.edited("house", self)

    def _set_roof(self, name: str) -> None:
        self.ctx.room.roof_material = name
        self._update_material_hints()
        self.ctx.edited("materials", self)

    def _set_wall(self, name: str) -> None:
        self.ctx.room.wall_material = name
        self._update_material_hints()
        self.ctx.edited("materials", self)

    def _set_thickness(self, attr: str, text: str) -> None:
        """Empty (or 0) = usual for the material; else cm (0–150)."""
        try:
            cm = float(str(text).replace(",", ".")) if str(text).strip() else 0.0
        except ValueError:
            return
        if cm > THICK_MAX_CM:
            return  # still typing / out of range: keep the last good value
        setattr(self.ctx.room, attr, max(0.0, cm) / 100.0)
        self.ctx.edited("materials", self)


__all__ = ["HousePage"]
