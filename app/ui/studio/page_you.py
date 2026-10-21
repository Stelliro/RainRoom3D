"""Inspector page for "You" — the listener heard on headphones."""

from __future__ import annotations

import math

from PySide6 import QtCore, QtWidgets

from app.models.settings_schema import compass_name
from app.ui.studio.context import StudioContext
from app.ui.studio.controls import Form, block, button, caption, combo, hbox, hint, label, spin
from app.ui.studio.page_speaker import fill_device_combo
from app.ui.theme import T
from app.ui.widgets import CompassWheel


class YouPage(QtWidgets.QWidget):
    def __init__(self, ctx: StudioContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        f = Form()
        lay.addWidget(f)

        f.section("Headphones", first=True)
        self.cmb_device = combo(tip="Which output your headphones are plugged into")
        f.full(self.cmb_device)
        self.btn_test = button("Test headphones", "", "Play a short tone on your headphones", "headphones")
        f.full(hbox(self.btn_test, None))
        f.full(hint("You hear the rain exactly as it sounds at this spot, in 3D — "
                    "best on headphones (Output: You)."))

        f.section("Where you sit")
        self.spin_x = spin(0.0, 30.0, 0.05, 2, " m", "Distance from the west wall")
        self.spin_z = spin(0.0, 30.0, 0.05, 2, " m", "Distance from the south wall")
        self.spin_y = spin(0.4, 5.9, 0.05, 2, " m", "Height of your ears above the floor "
                           "(about 1.2 m sitting, 1.6 m standing)")
        f.row("From west", self.spin_x)
        f.row("From south", self.spin_z)
        f.row("Ear height", self.spin_y)

        f.section("Facing")
        self.wheel = CompassWheel(size=104, color=T.you, show_degrees=False)
        self.wheel.setToolTip("Drag to turn the way you face. 0° = North, 90° = East.")
        self.spin_yaw = QtWidgets.QSpinBox()
        self.spin_yaw.setRange(0, 359)
        self.spin_yaw.setWrapping(True)
        self.spin_yaw.setSuffix("°")
        self.spin_yaw.setKeyboardTracking(False)
        self.spin_yaw.setMinimumWidth(90)
        from app.ui.studio.controls import guard_wheel
        guard_wheel(self.spin_yaw)
        self.lbl_face = caption("", wrap=False)
        side = QtWidgets.QVBoxLayout()
        side.setSpacing(6)
        side.setAlignment(QtCore.Qt.AlignTop)
        side.addWidget(label("Direction", "FieldLabel"))
        side.addWidget(self.spin_yaw)
        side.addWidget(self.lbl_face)
        row = hbox(self.wheel, side, None, spacing=16)
        row.setAlignment(self.wheel, QtCore.Qt.AlignTop)
        f.full(row)

        self._inputs = (self.cmb_device, self.spin_x, self.spin_z, self.spin_y, self.wheel, self.spin_yaw)
        self.cmb_device.currentIndexChanged.connect(
            lambda _i: self.ctx.set_listener_device(self.cmb_device.currentData()))
        self.btn_test.clicked.connect(self._test)
        self.spin_x.valueChanged.connect(lambda v: self._move(x=v))
        self.spin_z.valueChanged.connect(lambda v: self._move(z=v))
        self.spin_y.valueChanged.connect(lambda v: self._set_y(v))
        self.wheel.valueChanged.connect(lambda d: self._set_yaw(d, from_wheel=True))
        self.spin_yaw.valueChanged.connect(lambda d: self._set_yaw(d, from_wheel=False))
        ctx.devicesChanged.connect(lambda: self.load() if self.ctx.selected[0] == "listener" else None)

    def subtitle(self) -> str:
        return f"Headphones: {self.ctx.device_label(getattr(self.ctx.room.listener, 'audio_device', None))}"

    @staticmethod
    def _deg(yaw: float) -> int:
        return int(round(math.degrees(float(yaw)))) % 360

    def load(self) -> None:
        r, L = self.ctx.room, self.ctx.room.listener
        fill_device_combo(self.cmb_device, self.ctx, "System default output", getattr(L, "audio_device", None))
        d = self._deg(L.yaw)
        with block(*self._inputs):
            self.spin_x.setRange(0.0, float(r.width))
            self.spin_z.setRange(0.0, float(r.depth))
            self.spin_y.setRange(0.4, max(0.5, float(r.height) - 0.1))
            self.spin_x.setValue(float(L.x))
            self.spin_z.setValue(float(L.z))
            self.spin_y.setValue(float(L.y))
            self.wheel.setValue(d)
            self.spin_yaw.setValue(d)
        self.lbl_face.setText(f"Facing {compass_name(d)}")

    def _move(self, x=None, z=None) -> None:
        r, L = self.ctx.room, self.ctx.room.listener
        nx = float(x) if x is not None else L.x
        nz = float(z) if z is not None else L.z
        L.x, L.z = r.clamp_inside(nx, nz)
        if r.headphones_items:
            r.headphones_items[0].x, r.headphones_items[0].z = L.x, L.z
        self.ctx.edited("listener", self)

    def _set_y(self, v: float) -> None:
        self.ctx.room.listener.y = float(v)
        self.ctx.edited("listener", self)

    def _set_yaw(self, d: int, from_wheel: bool) -> None:
        d = int(d) % 360
        with block(self.wheel, self.spin_yaw):
            (self.spin_yaw if from_wheel else self.wheel).setValue(d)
        self.ctx.room.listener.yaw = math.radians(d)
        self.lbl_face.setText(f"Facing {compass_name(d)}")
        self.ctx.edited("listener", self)

    def _test(self) -> None:
        dev = getattr(self.ctx.room.listener, "audio_device", None)
        try:
            self.ctx.engine.play_test_tone(dev if dev is not None else None)
            self.ctx.say(f"Test tone → your headphones ({self.ctx.device_label(dev)})")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self.window(), "Test failed", str(e))


__all__ = ["YouPage"]
