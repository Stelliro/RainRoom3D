"""Output setup: your audio devices, and which output each listener uses."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from app.ui.studio.context import StudioContext, clean_device_name
from app.ui.studio.controls import button, caption, combo, hbox, hint, label, overline, scroll_area
from app.ui.studio.page_speaker import fill_device_combo
from app.ui.theme import SP


class OutputSetupDialog(QtWidgets.QDialog):
    def __init__(self, ctx: StudioContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("OutputSetup")
        self.setWindowTitle("Output setup")
        self.setMinimumSize(560, 600)
        self.resize(620, 700)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(2 * SP)

        lay.addWidget(label("Output setup", "CardTitle"))
        lay.addWidget(caption("Pick which real output plays for You (headphones) and for every room speaker. "
                              "Test each one to be sure."))

        lay.addWidget(overline("Your audio outputs"))
        self.lst_devices = QtWidgets.QListWidget()
        self.lst_devices.setObjectName("DeviceList")
        self.lst_devices.setMinimumHeight(120)
        self.lst_devices.setMaximumHeight(190)
        lay.addWidget(self.lst_devices)
        self.btn_refresh = button("Refresh", "Ghost", "Look for devices again (after plugging something in)")
        self.btn_test_device = button("Test selected", "", "Play a short tone on the selected output", "volume")
        lay.addLayout(hbox(self.btn_refresh, None, self.btn_test_device))
        lay.addWidget(hint("Duplicates of the same device from older Windows audio systems are hidden."))

        lay.addWidget(overline("What plays where"))
        self.grid_host = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(SP + 4)
        self.grid.setVerticalSpacing(SP)
        self.grid.setColumnStretch(1, 1)
        lay.addWidget(scroll_area(self.grid_host), 1)

        close = button("Done", "Primary")
        close.clicked.connect(self.accept)
        lay.addLayout(hbox(None, close))

        self.btn_refresh.clicked.connect(self._refresh)
        self.btn_test_device.clicked.connect(self._test_selected)
        self._fill()

    def _refresh(self) -> None:
        devs = self.ctx.refresh_devices()
        self.ctx.say(f"Found {len(devs)} output(s)")
        self._fill()

    def _fill(self) -> None:
        self.lst_devices.clear()
        for d in self.ctx.devices:
            star = "   (system default)" if d.get("is_default") else ""
            it = QtWidgets.QListWidgetItem(f"{clean_device_name(d.get('name'))}{star}   ·   {d.get('channels', '?')} ch")
            it.setData(QtCore.Qt.UserRole, d["index"])
            self.lst_devices.addItem(it)
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        room = self.ctx.room
        rows = [("You (headphones)", None)] + [(s.name, i) for i, s in enumerate(room.speakers)]
        for r, (name, idx) in enumerate(rows):
            self.grid.addWidget(label(name, "SetName"), r, 0)
            cmb = combo()
            cur = room.listener.audio_device if idx is None else room.speakers[idx].audio_device
            fill_device_combo(cmb, self.ctx, "System default" if idx is None else "No device (silent)", cur)
            cmb.currentIndexChanged.connect(lambda _i, c=cmb, k=idx: self._assign(k, c.currentData()))
            self.grid.addWidget(cmb, r, 1)
            t = button("Test", "Ghost", "Play a test sound on this output")
            t.clicked.connect(lambda _=False, k=idx: self._test_output(k))
            self.grid.addWidget(t, r, 2)

    def _assign(self, idx, dev) -> None:
        if idx is None:
            self.ctx.set_listener_device(dev)
        else:
            self.ctx.set_speaker_device(idx, dev)

    def _test_selected(self) -> None:
        item = self.lst_devices.currentItem()
        if not item:
            QtWidgets.QMessageBox.information(self, "Test device", "Select a device in the list first.")
            return
        di = item.data(QtCore.Qt.UserRole)
        try:
            self.ctx.engine.play_test_tone(int(di))
            self.ctx.say(f"Played a test tone on {self.ctx.device_label(di)}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Test failed", str(e))

    def _test_output(self, idx) -> None:
        room = self.ctx.room
        try:
            if idx is None:
                dev = room.listener.audio_device
                self.ctx.engine.play_test_tone(dev if dev is not None else None)
                self.ctx.say(f"Test tone → your headphones ({self.ctx.device_label(dev)})")
            else:
                s = room.speakers[idx]
                where = self.ctx.engine.play_speaker_test(s, index_hint=idx)
                self.ctx.say(f"Test sound → {s.name}" + (f" ({where})" if where else ""))
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Test failed", str(e))


__all__ = ["OutputSetupDialog"]
