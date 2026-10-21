"""Phones: share the rain with phones on the same Wi-Fi.

One switch starts / stops the phone server, a QR code + link let a phone
join, "New link" changes the key, and the list shows every phone with its
room and where its rain plays (with Stop for a phone playing on this PC).
"""

from __future__ import annotations

from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from app.remote import qr
from app.ui.studio.controls import (
    ToggleSwitch, button, caption, combo, hbox, hint, label, overline, scroll_area,
)
from app.ui.studio.icons import icon
from app.ui.theme import SP, T

QR_PX = 216          # on-screen size of the code, quiet zone included
QR_BORDER = 4        # quiet zone in modules (the QR spec asks for 4)


def to_qimage(matrix: List[List[bool]], scale: int = 8, border: int = QR_BORDER) -> QtGui.QImage:
    """A crisp black-on-white QImage of a QR matrix (whole pixels per module,
    white quiet zone all round, so phones read it on a dark screen too)."""
    n = len(matrix)
    side = (n + 2 * border) * scale
    img = QtGui.QImage(side, side, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor("#ffffff"))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, False)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor("#000000"))
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                p.drawRect((x + border) * scale, (y + border) * scale, scale, scale)
    p.end()
    return img


def qr_pixmap(text: str, px: int = QR_PX, dpr: float = 1.0) -> QtGui.QPixmap:
    """QR code of ``text`` as a pixmap about ``px`` logical pixels wide."""
    m = qr.encode(text, "M")
    modules = len(m) + 2 * QR_BORDER
    scale = max(1, int(px * dpr) // modules)
    pm = QtGui.QPixmap.fromImage(to_qimage(m, scale))
    pm.setDevicePixelRatio(max(1.0, float(dpr)))
    return pm


class _PhoneRow(QtWidgets.QFrame):
    def __init__(self, row: dict, text: str, on_stop=None, parent=None):
        super().__init__(parent)
        self.setObjectName("PhoneRow")
        self.client_id = row.get("client_id")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(SP + 4)
        ic = QtWidgets.QLabel()
        col = T.accent if row.get("connected") else T.text3
        ic.setPixmap(icon("phone", col, 20).pixmap(20, 20))
        lay.addWidget(ic)
        col_l = QtWidgets.QVBoxLayout()
        col_l.setSpacing(2)
        self.lbl_name = label(str(row.get("name") or "Room"), "SetName")
        who = str(row.get("device_name") or "A phone")
        if not row.get("connected"):
            who += " (left)"
        self.lbl_info = label(f"{who} · {text}", "Caption", wrap=True)
        col_l.addWidget(self.lbl_name)
        col_l.addWidget(self.lbl_info)
        lay.addLayout(col_l, 1)
        self.btn_stop = None
        if on_stop is not None:
            self.btn_stop = button("Stop", "Danger", "Stop this phone's rain on this computer", "stop", T.danger)
            self.btn_stop.clicked.connect(lambda: on_stop(self.client_id))
            lay.addWidget(self.btn_stop, 0, QtCore.Qt.AlignVCenter)


class PhoneDialog(QtWidgets.QDialog):
    def __init__(self, ctx, bridge, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.bridge = bridge
        self.setObjectName("PhoneDialog")
        self.setWindowTitle("Phones")
        self.setMinimumWidth(600)
        self.resize(640, 660)
        self._sized_on: Optional[bool] = None   # which layout the size was last fitted to
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(2 * SP)

        ic = QtWidgets.QLabel()
        ic.setPixmap(icon("phone", T.accent, 22).pixmap(22, 22))
        lay.addLayout(hbox(ic, label("Phones", "CardTitle"), None))
        lay.addWidget(caption("Each phone gets its own room. Weather is shared with this PC."))

        self.sw_on = ToggleSwitch("Let phones on this Wi-Fi join")
        self.sw_on.setToolTip("Phones on the same network open a link to hear the rain in their own room")
        self.lbl_status = label("", "Caption")
        self.lbl_status.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        lay.addLayout(hbox(self.sw_on, None, self.lbl_status))

        # ---- join: QR + link ----
        self.join = QtWidgets.QWidget()
        jl = QtWidgets.QHBoxLayout(self.join)
        jl.setContentsMargins(0, 0, 0, 0)
        jl.setSpacing(3 * SP)
        self.lbl_qr = QtWidgets.QLabel()
        self.lbl_qr.setObjectName("QrCode")
        self.lbl_qr.setFixedSize(QR_PX, QR_PX)
        self.lbl_qr.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_qr.setToolTip("Point the phone's camera at this code")
        jl.addWidget(self.lbl_qr, 0, QtCore.Qt.AlignTop)
        side = QtWidgets.QVBoxLayout()
        side.setSpacing(SP)
        side.addWidget(overline("Scan or open this link"))
        side.addWidget(hint("Point the phone's camera at the code, or type the link into its browser."))
        self.cmb_url = combo(tip="This computer has more than one network address: pick the one "
                                 "your phone's Wi-Fi can reach")
        self.cmb_url.setMinimumContentsLength(20)
        side.addWidget(self.cmb_url)
        self.ed_url = QtWidgets.QLineEdit()
        self.ed_url.setObjectName("PhoneUrl")
        self.ed_url.setReadOnly(True)
        self.ed_url.setToolTip("The link for phones — select it or press Copy")
        side.addWidget(self.ed_url)
        self.btn_copy = button("Copy link", "", "Copy the link to the clipboard", "copy")
        self.btn_new = button("New link", "Ghost", "Make a new link. Phones using the old one are "
                                                   "disconnected and need to scan again", "link")
        side.addLayout(hbox(self.btn_copy, self.btn_new, None, spacing=SP))
        side.addWidget(hint("The first time, Windows may ask whether RainRoom can use the network. "
                            "Allow it on private networks so phones can connect."))
        side.addStretch(1)
        jl.addLayout(side, 1)
        lay.addWidget(self.join)
        self.lbl_off = caption(
            "How it works:<br>"
            "1. Turn this on. A code appears here.<br>"
            "2. Scan it with the phone's camera (the phone must be on the same Wi-Fi).<br>"
            "3. On the phone, set up its room — size, walls, windows and where you sit — "
            "then play the rain on the phone, or on one of this computer's sound outputs.<br><br>"
            "Phones join over your own network; nothing goes through the internet.")
        self.lbl_off.setTextFormat(QtCore.Qt.RichText)
        lay.addWidget(self.lbl_off)

        # ---- phones ----
        self.lbl_phones = overline("Phones")
        lay.addWidget(self.lbl_phones)
        self.rows_host = QtWidgets.QWidget()
        self.rows = QtWidgets.QVBoxLayout(self.rows_host)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(SP)
        self.rows.addStretch(1)
        self.scroll_rows = scroll_area(self.rows_host)
        lay.addWidget(self.scroll_rows, 1)
        self.lbl_empty = hint("No phones yet. Scan the code with a phone to add one.")
        lay.addWidget(self.lbl_empty)
        n = int(getattr(bridge.hub, "max_active", 4))
        self.lbl_limit = hint(f"Up to {n} phone rooms can play at the same time. Each one runs in its own "
                              "background process, so this computer's own rain keeps playing smoothly.")
        lay.addWidget(self.lbl_limit)
        lay.addStretch(0)

        done = button("Done", "Primary")
        done.clicked.connect(self.accept)
        lay.addLayout(hbox(None, done))

        self.sw_on.toggled.connect(self._on_switch)
        self.cmb_url.currentIndexChanged.connect(self._show_url)
        self.btn_copy.clicked.connect(self.copy_url)
        self.btn_new.clicked.connect(self._new_link)
        bridge.stateChanged.connect(self.refresh)
        bridge.zonesChanged.connect(self.refresh_phones)
        self._url_list: List[str] = []
        self.refresh()

    # ----- state -----
    def _on_switch(self, on: bool) -> None:
        self.bridge.set_enabled(on)

    def _new_link(self) -> None:
        if self.bridge.connected_count() and not self.ctx.confirm(
                "New link", "Phones using the current link will be disconnected and need to scan "
                            "the new code. Make a new link?"):
            return
        self.bridge.new_link()

    def refresh(self) -> None:
        b = self.bridge
        st = b.status
        self.sw_on.blockSignals(True)
        self.sw_on.setChecked(st in ("on", "starting"))
        self.sw_on.blockSignals(False)
        self.sw_on.setEnabled(st not in ("starting", "stopping"))
        text = {"off": "Off", "starting": "Starting…", "stopping": "Stopping…",
                "on": f"On · port {b.server.port}" if b.server is not None and b.server.port else "On",
                "error": b.error or "Couldn't start"}.get(st, "")
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(f"color: {T.danger};" if st == "error" else "")
        self.lbl_status.setWordWrap(st == "error")
        on = st == "on"
        self.join.setVisible(on)
        self.lbl_off.setVisible(not on)
        if on and b.urls != self._url_list:
            self._url_list = list(b.urls)
            self.cmb_url.blockSignals(True)
            self.cmb_url.clear()
            for u in self._url_list:
                self.cmb_url.addItem(u.split("//", 1)[-1].split("/", 1)[0], userData=u)
            self.cmb_url.setCurrentIndex(0)
            self.cmb_url.blockSignals(False)
            self.cmb_url.setVisible(len(self._url_list) > 1)
            self._show_url()
        elif on:
            self._show_url()        # same addresses, maybe a new key
        if not on:
            self._url_list = []
        self.refresh_phones()
        self._fit_size(on)

    def _fit_size(self, on: bool) -> None:
        """Tall with the code and the phone list; just as tall as the how-to when off."""
        if self._sized_on == on:
            return
        self._sized_on = on
        lay = self.layout()
        lay.activate()
        if on:
            self.setMinimumHeight(560)
            if self.height() < 660:
                self.resize(max(self.width(), 640), 660)
        else:
            self.setMinimumHeight(0)
            h = max(lay.minimumSize().height(), lay.sizeHint().height())
            self.resize(max(self.width(), 640), min(h, 660))

    def current_url(self) -> str:
        u = self.cmb_url.currentData()
        return str(u) if u else (self._url_list[0] if self._url_list else "")

    def _show_url(self, *_a) -> None:
        url = self.current_url()
        if url and self.bridge.server is not None:
            # follow a new key without rebuilding the address list
            base = url.split("?k=", 1)[0]
            url = f"{base}?k={self.bridge.server.key}"
        self.ed_url.setText(url)
        self.ed_url.setCursorPosition(0)
        if url:
            dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
            self.lbl_qr.setPixmap(qr_pixmap(url, QR_PX, dpr))
        else:
            self.lbl_qr.clear()

    def copy_url(self) -> None:
        if self.ed_url.text():
            QtWidgets.QApplication.clipboard().setText(self.ed_url.text())
            self.ctx.say("Phone link copied to the clipboard", 3000)

    def qr_pixmap(self) -> Optional[QtGui.QPixmap]:
        pm = self.lbl_qr.pixmap()
        return pm if pm is not None and not pm.isNull() else None

    # ----- phones -----
    def phone_rows(self) -> List[_PhoneRow]:
        return [self.rows.itemAt(i).widget() for i in range(self.rows.count())
                if isinstance(self.rows.itemAt(i).widget(), _PhoneRow)]

    def refresh_phones(self) -> None:
        while self.rows.count() > 1:
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.hide()                # gone now, not when the event loop deletes it
                w.setParent(None)
                w.deleteLater()
        rows = self.bridge.zone_rows()
        for i, r in enumerate(rows):
            out = r.get("output") or {}
            stop = self.bridge.stop_zone if out.get("mode") == "pc" else None
            self.rows.insertWidget(i, _PhoneRow(r, self.bridge.output_text(r), stop))
        on = self.bridge.status == "on"
        self.scroll_rows.setVisible(bool(rows))
        self.lbl_phones.setVisible(on or bool(rows))
        self.lbl_empty.setVisible(on and not rows)
        self.lbl_limit.setVisible(on)


__all__ = ["PhoneDialog", "qr_pixmap", "to_qimage"]
