"""Inspector page for one room speaker."""

from __future__ import annotations

from typing import Optional

from PySide6 import QtWidgets

from app.models.room import Speaker
from app.ui.studio.context import StudioContext
from app.ui.studio.controls import (
    Form, ToggleSwitch, block, button, combo, hbox, hint, line_edit, spin,
)

TEST_TIP = ("Plays a chirp from this speaker’s place in the room. If several room speakers share one "
            "device, the chirp pans left/right (or onto surround seats) by how far this one sits from "
            "the others.")


def fill_device_combo(cmb: QtWidgets.QComboBox, ctx: StudioContext, none_text: str, current) -> None:
    """Rebuild a device picker (signals blocked) and select ``current``."""
    with block(cmb):
        cmb.clear()
        cmb.addItem(none_text, userData=None)
        for text, idx in ctx.device_items():
            cmb.addItem(text, userData=idx)
        if current is not None and cmb.findData(current) < 0:
            cmb.addItem(f"Output #{current} (not found)", userData=current)
        cmb.setCurrentIndex(max(0, cmb.findData(current)) if current is not None else 0)


class SpeakerPage(QtWidgets.QWidget):
    def __init__(self, ctx: StudioContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.idx = -1
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        f = Form()
        lay.addWidget(f)

        f.section("Plays on", first=True)
        self.cmb_device = combo(tip="The real speaker / sound output this one plays through")
        self.btn_test = button("Test sound", "", TEST_TIP, "volume")
        f.full(self.cmb_device)
        self.sw_enabled = ToggleSwitch("On")
        self.sw_enabled.setToolTip("Switch this speaker off without deleting it")
        f.full(hbox(self.btn_test, None, self.sw_enabled))
        self.spin_gain = spin(-24.0, 12.0, 0.5, 1, " dB", "Make this speaker louder or quieter than the others")
        f.row("Level trim", self.spin_gain)

        f.section("Where it is")
        self.ed_name = line_edit("Speaker name")
        self.spin_x = spin(0.0, 30.0, 0.05, 2, " m", "Distance from the west wall")
        self.spin_z = spin(0.0, 30.0, 0.05, 2, " m", "Distance from the south wall")
        self.spin_y = spin(0.1, 3.5, 0.05, 2, " m", "Height of the speaker’s centre above the floor")
        f.row("Name", self.ed_name)
        f.row("From west", self.spin_x)
        f.row("From south", self.spin_z)
        f.row("Height", self.spin_y)

        f.section("Cabinet size")
        self.spin_w = spin(0.12, 2.5, 0.01, 2, " m", "Width / horizontal span — wide = soundbar range")
        self.spin_h = spin(0.08, 2.0, 0.01, 2, " m", "Vertical size of the cabinet")
        self.spin_d = spin(0.06, 1.2, 0.01, 2, " m", "Depth / thickness of the cabinet")
        f.row("Width", self.spin_w)
        f.row("Height", self.spin_h)
        f.row("Depth", self.spin_d)
        f.full(hint("Wide and flat ≈ a soundbar (wide pickup). Tall or square ≈ a point speaker."))
        self.ed_notes = line_edit("Notes (optional)")
        f.row("Notes", self.ed_notes)

        f.spacer(4)
        self.btn_remove = button("Remove speaker", "Danger", "Delete this speaker (also: Delete key)", "trash")
        f.full(hbox(self.btn_remove, None))  # last: the page's only destructive action

        self._inputs = (self.cmb_device, self.sw_enabled, self.spin_gain, self.ed_name, self.spin_x, self.spin_z,
                        self.spin_y, self.spin_w, self.spin_h, self.spin_d, self.ed_notes)

        self.cmb_device.currentIndexChanged.connect(
            lambda _i: self.ctx.set_speaker_device(self.idx, self.cmb_device.currentData()))
        self.btn_test.clicked.connect(self._test)
        self.sw_enabled.toggled.connect(lambda on: self._edit(lambda s: setattr(s, "enabled", bool(on))))
        self.spin_gain.valueChanged.connect(lambda v: self._edit(lambda s: setattr(s, "gain_db", float(v))))
        self.ed_name.editingFinished.connect(self._apply_name)
        self.spin_x.valueChanged.connect(lambda v: self._edit(lambda s: self._move(s, x=v)))
        self.spin_z.valueChanged.connect(lambda v: self._edit(lambda s: self._move(s, z=v)))
        self.spin_y.valueChanged.connect(lambda v: self._edit(lambda s: setattr(s, "y", float(v))))
        self.spin_w.valueChanged.connect(lambda v: self._edit(lambda s: self._dim(s, "width", v)))
        self.spin_h.valueChanged.connect(lambda v: self._edit(lambda s: self._dim(s, "height", v)))
        self.spin_d.valueChanged.connect(lambda v: self._edit(lambda s: self._dim(s, "depth", v)))
        self.ed_notes.editingFinished.connect(
            lambda: self._edit(lambda s: setattr(s, "notes", self.ed_notes.text().strip())))
        self.btn_remove.clicked.connect(lambda: self.ctx.remove_speaker(self.idx))
        ctx.devicesChanged.connect(lambda: self.load(self.idx) if self.speaker() else None)

    def speaker(self) -> Optional[Speaker]:
        sp = self.ctx.room.speakers
        return sp[self.idx] if 0 <= self.idx < len(sp) else None

    def subtitle(self) -> str:
        s = self.speaker()
        if s is None:
            return ""
        if not s.enabled:
            return "Switched off"
        if s.audio_device is None:
            return "No device yet — pick one below"
        return f"Plays on {self.ctx.device_label(s.audio_device)}"

    # ----- room → widgets -----
    def load(self, idx: int) -> None:
        if idx != self.idx:
            self.ctx.commit_pending_edit()  # a half-typed name belongs to the old speaker
        same = idx == self.idx
        self.idx = idx
        s = self.speaker()
        if s is None:
            return
        r = self.ctx.room
        fill_device_combo(self.cmb_device, self.ctx, "No device (silent)", s.audio_device)
        bw, bh, bd = s.box_dims()
        with block(*self._inputs):
            self.sw_enabled.setChecked(bool(s.enabled))
            self.spin_gain.setValue(float(s.gain_db))
            if not (same and self.ed_name.hasFocus()):
                self.ed_name.setText(s.name)
            self.spin_x.setRange(0.0, float(r.width))
            self.spin_z.setRange(0.0, float(r.depth))
            self.spin_y.setRange(0.1, max(0.2, float(r.height) - 0.08))
            self.spin_x.setValue(float(s.x))
            self.spin_z.setValue(float(s.z))
            self.spin_y.setValue(float(s.y))
            self.spin_w.setValue(bw)
            self.spin_h.setValue(bh)
            self.spin_d.setValue(bd)
            if not (same and self.ed_notes.hasFocus()):
                self.ed_notes.setText(str(getattr(s, "notes", "") or ""))
        self.btn_test.setEnabled(s.audio_device is not None)
        self.btn_test.setToolTip(TEST_TIP if s.audio_device is not None else "Pick a device first")

    # ----- widgets → room -----
    def _edit(self, fn) -> None:
        s = self.speaker()
        if s is None:
            return
        fn(s)
        self.ctx.edited("speakers", self)

    def _move(self, s: Speaker, x=None, z=None) -> None:
        nx = float(x) if x is not None else s.x
        nz = float(z) if z is not None else s.z
        s.x, s.z = self.ctx.room.clamp_inside(nx, nz)

    @staticmethod
    def _dim(s: Speaker, attr: str, v: float) -> None:
        s.materialize_dims()
        setattr(s, attr, float(v))
        s.size = max(float(s.width or 0.1), float(s.height or 0.1), float(s.depth or 0.1))

    def _apply_name(self) -> None:
        s = self.speaker()
        name = self.ed_name.text().strip()
        if s is None or not name or name == s.name:
            return
        s.name = name
        self.ctx.edited("speakers", None)

    def _test(self) -> None:
        s = self.speaker()
        if s is None:
            return
        try:
            where = self.ctx.engine.play_speaker_test(s, index_hint=self.idx)
            tag = f" ({where})" if where else ""
            self.ctx.say(f"Test sound → {s.name}{tag} on {self.ctx.device_label(s.audio_device)}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self.window(), "Test failed", str(e))


__all__ = ["SpeakerPage", "fill_device_combo"]
