"""Inspector page for one window."""

from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets

from app.models.room import CUSTOM_HINGES, CUSTOM_MOTIONS, WINDOW_OPEN_STYLES, WINDOW_STYLE_LABELS, Window
from app.ui.studio.context import StudioContext
from app.ui.studio.controls import (
    Form, ToggleSwitch, block, button, combo, hbox, label, line_edit, slider, spin,
)

WALLS = (("North wall", "north"), ("South wall", "south"), ("East wall", "east"), ("West wall", "west"))
HINGE_STYLES = ("casement", "tilt_turn")                          # a left / right hinge matters
ANGLE_STYLES = ("casement", "awning", "hopper", "pivot", "tilt_turn")  # opens by swinging / tilting


def describe_opening(w: Window):
    """(plain sentence, tooltip with the raw portal numbers)."""
    from app.audio.spatial import window_acoustic_profile

    style = w.resolved_style_for_draw()
    hinge = w.resolved_hinge_side()
    p = window_acoustic_profile(w.open_amount(), style, width=w.width, height=w.height,
                                angle_deg=w.open_angle_deg(), hinge_side=hinge)
    y0, y1 = w.effective_gap_y_range()
    g, fc = float(p["gain"]), float(p["lp_fc"])
    if w.open_amount() <= 0.005:
        text = "Closed — rain is heard only through the glass"
    else:
        much = ("almost no" if g < 0.15 else "a little" if g < 0.5
                else "a fair amount of" if g < 1.0 else "plenty of")
        tone = "muffled" if fc < 2500 else ("a little muffled" if fc < 6000 else "clear")
        text = f"Open {int(round(w.open_amount() * 100))}% — lets in {much} rain sound, {tone}"
    tip = (f"Portal: gain×{g:.2f} · tone~{fc:.0f} Hz · gap {y0:.2f}–{y1:.2f} m · "
           f"area {w.aperture_area_m2():.2f} m²\n"
           f"Open: {style} · hinge {hinge} · low-scoop {p['scoop_low']:+.2f} "
           f"high-scoop {p['scoop_high']:+.2f} crack {p['crack_k']:.2f}")
    return text, tip


def _style_name(key: str) -> str:
    return WINDOW_STYLE_LABELS.get(key, key).split(" (")[0]


def _style_detail(key: str) -> str:
    lab = WINDOW_STYLE_LABELS.get(key, "")
    return lab[lab.index("(") + 1:lab.rindex(")")] if "(" in lab else ""


def _style_full(key: str) -> str:
    """"Casement, side hinge": the name plus how it opens (subtitle + tooltip)."""
    detail = _style_detail(key) if key != "custom" else ""
    return f"{_style_name(key)}, {detail}" if detail else _style_name(key)


class WindowPage(QtWidgets.QWidget):
    def __init__(self, ctx: StudioContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.idx = -1
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        f = Form()
        lay.addWidget(f)

        # The big one first: how open is it?
        f.section("Opening", first=True)
        self.sld_open = slider(0, 100, "How far the window is open — 0% closed, 100% wide open", "OpenSlider")
        self.sld_open.setMinimumHeight(30)
        self.lbl_open = label("", "SetName")
        self.lbl_open.setMinimumWidth(64)
        self.lbl_open.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        f.full(hbox(label("Open", "SetName"), None, self.lbl_open))
        f.full(self.sld_open)
        self.lbl_acoustics = label("", "Acoustics", wrap=True)
        f.full(self.lbl_acoustics)

        f.section("How it opens")
        self.cmb_style = combo([(_style_name(k), k) for k in WINDOW_OPEN_STYLES],
                               "Each style lets rain in differently (a top hinge scoops low rain, …)")
        for i, k in enumerate(WINDOW_OPEN_STYLES):
            self.cmb_style.setItemData(i, WINDOW_STYLE_LABELS.get(k, k), QtCore.Qt.ToolTipRole)
        self.cmb_hinge = combo([("Left", "left"), ("Right", "right")], "Which side the hinge is on")
        self.spin_angle = spin(10.0, 90.0, 5.0, 0, "°", "How far it swings or tilts when fully open")
        f.row("Style", self.cmb_style)
        self.lab_hinge = f.row("Hinge side", self.cmb_hinge)
        self.lab_angle = f.row("Max angle", self.spin_angle)

        self.custom_box = QtWidgets.QGroupBox("Custom design")
        cf = Form(label_width=72)
        cl = QtWidgets.QVBoxLayout(self.custom_box)
        cl.setContentsMargins(8, 8, 8, 8)
        cl.addWidget(cf)
        self.cmb_custom_hinge = combo([(h.capitalize(), h) for h in CUSTOM_HINGES], "Where your window is hinged")
        self.cmb_custom_motion = combo([(m.capitalize(), m) for m in CUSTOM_MOTIONS], "How it moves")
        self.sw_custom_out = ToggleSwitch("Opens outward")
        self.ed_custom_notes = line_edit("Describe your window (optional)")
        cf.row("Hinge", self.cmb_custom_hinge)
        cf.row("Motion", self.cmb_custom_motion)
        cf.full(self.sw_custom_out)
        cf.row("Notes", self.ed_custom_notes)
        f.full(self.custom_box)

        f.section("Placement")
        self.ed_name = line_edit("Window name")
        self.cmb_wall = combo([(a, b) for a, b in WALLS], "Which outside wall the window is in")
        self.spin_offset = spin(0.0, 30.0, 0.05, 2, " m")
        f.row("Name", self.ed_name)
        f.row("Wall", self.cmb_wall)
        self.lab_offset = f.row("From corner", self.spin_offset)
        self.btn_snap_window = button("Snap back to wall", "", "This window was moved freely in 3D — "
                                      "put it back on its wall", "window")
        self.btn_snap_window.setVisible(False)
        f.full(self.btn_snap_window)

        f.section("Size")
        self.spin_width = spin(0.3, 8.0, 0.05, 2, " m", "Width of the glass along the wall")
        self.spin_height = spin(0.3, 4.0, 0.05, 2, " m", "Vertical size of the glass opening")
        self.spin_sill = spin(0.0, 3.5, 0.05, 2, " m", "Height of the window sill above the floor")
        f.row("Width", self.spin_width)
        f.row("Height", self.spin_height)
        f.row("Sill height", self.spin_sill)

        f.spacer(4)
        self.btn_del_window = button("Remove window", "Danger", "Delete this window (also: Delete key)", "trash")
        f.full(hbox(self.btn_del_window, None))

        self._inputs = (self.sld_open, self.cmb_style, self.cmb_hinge, self.spin_angle, self.cmb_custom_hinge,
                        self.cmb_custom_motion, self.sw_custom_out, self.ed_custom_notes, self.ed_name,
                        self.cmb_wall, self.spin_offset, self.spin_width, self.spin_height, self.spin_sill)

        self.sld_open.valueChanged.connect(lambda v: self._edit(lambda w: setattr(w, "open", v / 100.0)))
        self.cmb_style.currentIndexChanged.connect(
            lambda _i: self._edit(lambda w: setattr(w, "open_style", str(self.cmb_style.currentData() or "casement")),
                                  relayout=True))
        self.cmb_hinge.currentIndexChanged.connect(
            lambda _i: self._edit(lambda w: setattr(w, "hinge_side", str(self.cmb_hinge.currentData() or "left"))))
        self.spin_angle.valueChanged.connect(lambda v: self._edit(lambda w: setattr(w, "max_angle_deg", float(v))))
        self.cmb_custom_hinge.currentIndexChanged.connect(
            lambda _i: self._edit(lambda w: setattr(w, "custom_hinge", str(self.cmb_custom_hinge.currentData()))))
        self.cmb_custom_motion.currentIndexChanged.connect(
            lambda _i: self._edit(lambda w: setattr(w, "custom_motion", str(self.cmb_custom_motion.currentData()))))
        self.sw_custom_out.toggled.connect(lambda on: self._edit(lambda w: setattr(w, "custom_outward", bool(on))))
        self.ed_custom_notes.editingFinished.connect(
            lambda: self._edit(lambda w: setattr(w, "custom_notes", self.ed_custom_notes.text().strip())))
        self.ed_name.editingFinished.connect(self._apply_name)
        self.cmb_wall.currentIndexChanged.connect(lambda _i: self._edit(self._set_wall, relayout=True))
        self.spin_offset.valueChanged.connect(lambda v: self._edit(lambda w: setattr(w, "offset", float(v))))
        self.spin_width.valueChanged.connect(lambda v: self._edit(lambda w: setattr(w, "width", float(v)),
                                                                  relayout=True))
        self.spin_height.valueChanged.connect(lambda v: self._edit(lambda w: setattr(w, "height", float(v))))
        self.spin_sill.valueChanged.connect(lambda v: self._edit(lambda w: setattr(w, "sill", float(v))))
        self.btn_snap_window.clicked.connect(self._snap)
        self.btn_del_window.clicked.connect(lambda: self.ctx.remove_window(self.idx))

    # ----- helpers -----
    def win(self) -> Optional[Window]:
        ws = self.ctx.room.windows
        return ws[self.idx] if 0 <= self.idx < len(ws) else None

    def _wall_len(self, w: Window) -> float:
        r = self.ctx.room
        return r.width if (w.wall or "north").lower() in ("north", "south") else r.depth

    def _set_wall(self, w: Window) -> None:
        w.wall = str(self.cmb_wall.currentData() or "north")
        w.width = min(w.width, max(0.3, self._wall_len(w) - 0.1))
        w.offset = max(0.0, min(self._wall_len(w) - w.width, w.offset))

    def subtitle(self) -> str:
        w = self.win()
        if w is None:
            return ""
        wall = (w.wall or "north").lower()
        free = " · moved freely" if getattr(w, "free_place", False) else ""
        return f"{wall.capitalize()} wall · {_style_full(w.open_style_norm())}{free}"

    def _open_text(self, w: Window) -> str:
        pct = int(round(w.open_amount() * 100))
        if w.resolved_style_for_draw() in ANGLE_STYLES and pct > 0:
            return f"{pct}% · {w.open_angle_deg():.0f}°"
        return f"{pct}%"

    # ----- room → widgets -----
    def load(self, idx: int) -> None:
        if idx != self.idx:
            self.ctx.commit_pending_edit()  # a half-typed name belongs to the old window
        same = idx == self.idx
        self.idx = idx
        w = self.win()
        if w is None:
            return
        self.ctx.room.sync_window_coords(w)
        with block(*self._inputs):
            self.sld_open.setValue(int(round(w.open * 100)))
            self.cmb_style.setCurrentIndex(max(0, self.cmb_style.findData(w.open_style_norm())))
            self.cmb_hinge.setCurrentIndex(max(0, self.cmb_hinge.findData((w.hinge_side or "left").lower())))
            self.spin_angle.setValue(float(getattr(w, "max_angle_deg", 75.0)))
            self.cmb_custom_hinge.setCurrentIndex(max(0, self.cmb_custom_hinge.findData(w.custom_hinge or "left")))
            self.cmb_custom_motion.setCurrentIndex(
                max(0, self.cmb_custom_motion.findData(w.custom_motion or "swing")))
            self.sw_custom_out.setChecked(bool(getattr(w, "custom_outward", True)))
            # A reload of the same window keeps what is being typed; a new window always shows its own text
            if not (same and self.ed_custom_notes.hasFocus()):
                self.ed_custom_notes.setText(str(getattr(w, "custom_notes", "") or ""))
            if not (same and self.ed_name.hasFocus()):
                self.ed_name.setText(w.name)
            self.cmb_wall.setCurrentIndex(max(0, self.cmb_wall.findData((w.wall or "north").lower())))
            length = self._wall_len(w)
            self.spin_width.setMaximum(max(0.3, min(8.0, length * 1.35)))
            self.spin_width.setValue(w.width)
            self.spin_offset.setRange(0.0, max(0.0, length - w.width))
            self.spin_offset.setValue(max(0.0, w.offset))
            self.spin_height.setValue(w.height)
            self.spin_sill.setValue(w.sill)
        self._relayout(w)
        self._refresh_labels(w)

    def _relayout(self, w: Window) -> None:
        style = w.open_style_norm()
        drawn = w.resolved_style_for_draw()
        custom = style == "custom"
        self.cmb_style.setToolTip(f"{_style_full(style)} — each style lets rain in differently "
                                  "(a top hinge scoops low rain, …)")
        self.custom_box.setVisible(custom)
        hinge_on = drawn in HINGE_STYLES and not custom
        self.cmb_hinge.setEnabled(hinge_on)
        self.lab_hinge.setEnabled(hinge_on)
        angle_on = drawn in ANGLE_STYLES
        self.spin_angle.setEnabled(angle_on)
        self.lab_angle.setEnabled(angle_on)
        wall = (w.wall or "north").lower()
        self.lab_offset.setText("From west" if wall in ("north", "south") else "From south")
        self.spin_offset.setToolTip("How far the window starts from the "
                                    + ("west" if wall in ("north", "south") else "south") + " corner of its wall")
        free = bool(getattr(w, "free_place", False))
        self.btn_snap_window.setVisible(free)
        self.spin_offset.setEnabled(not free)

    def _refresh_labels(self, w: Window) -> None:
        self.lbl_open.setText(self._open_text(w))
        try:
            text, tip = describe_opening(w)
        except Exception:
            text, tip = "", ""
        self.lbl_acoustics.setText(text)
        self.lbl_acoustics.setToolTip(tip)
        self.lbl_acoustics.setVisible(bool(text))

    # ----- widgets → room -----
    def _edit(self, fn, relayout: bool = False) -> None:
        w = self.win()
        if w is None:
            return
        fn(w)
        self.ctx.room.sync_window_coords(w)
        if relayout:
            self.load(self.idx)  # ranges / enabled fields depend on wall + style
        else:
            self._refresh_labels(w)
        self.ctx.edited("windows", self)

    def _apply_name(self) -> None:
        w = self.win()
        name = self.ed_name.text().strip()
        if w is None or not name or name == w.name:
            return
        w.name = name
        self.ctx.edited("windows", None)  # header + objects list show the new name

    def _snap(self) -> None:
        """Undo a free 3D move: the window goes back to its wall + offset."""
        w = self.win()
        if w is None or not getattr(w, "free_place", False):
            return
        w.free_place = False
        self.ctx.room.sync_window_coords(w)
        self.load(self.idx)
        self.ctx.edited("windows", None)
        self.ctx.say(f"{w.name} snapped back to the {w.wall} wall")


__all__ = ["WindowPage", "describe_opening"]
