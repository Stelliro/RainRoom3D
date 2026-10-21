"""Floating "Objects" list: House, You, every window and speaker.

Click a row to select it. Each row shows a short status on the right: a
window's open amount, a speaker's device (or "no device" in warning colour).
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from app.ui.studio.context import NONE_SEL, StudioContext
from app.ui.studio.controls import FloatCard, button, hbox, icon_button, label
from app.ui.studio.icons import icon
from app.ui.theme import T, qcolor

ROLE_SEL = QtCore.Qt.UserRole
ROLE_STATUS = QtCore.Qt.UserRole + 1
ROLE_STATUS_COLOR = QtCore.Qt.UserRole + 2
ROLE_HEADER = QtCore.Qt.UserRole + 3

ROW_H, HEAD_H = 30, 26


class _RowDelegate(QtWidgets.QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QtCore.QSize(option.rect.width(), HEAD_H if index.data(ROLE_HEADER) else ROW_H)

    def paint(self, p: QtGui.QPainter, option, index):
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(option.rect)
        if index.data(ROLE_HEADER):
            f = QtGui.QFont(option.font)
            f.setPixelSize(T.fs_overline)
            f.setBold(True)
            f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1.0)
            p.setFont(f)
            p.setPen(qcolor(T.text3))
            p.drawText(r.adjusted(8, 6, -8, 0), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                       str(index.data(QtCore.Qt.DisplayRole)).upper())
            p.restore()
            return
        sel = bool(option.state & QtWidgets.QStyle.State_Selected)
        hov = bool(option.state & QtWidgets.QStyle.State_MouseOver)
        if sel or hov:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(qcolor(T.accent_soft if sel else T.hover))
            p.drawRoundedRect(r.adjusted(2, 1, -2, -1), 6, 6)
        ic = index.data(QtCore.Qt.DecorationRole)
        x = r.left() + 10
        if isinstance(ic, QtGui.QIcon):
            ic.paint(p, QtCore.QRect(int(x), int(r.center().y() - 8), 16, 16))
        x += 24
        status = str(index.data(ROLE_STATUS) or "")
        f = QtGui.QFont(option.font)
        f.setPixelSize(T.fs_caption)
        fm = QtGui.QFontMetrics(f)
        sw = min(fm.horizontalAdvance(status), int(r.width() * 0.45)) if status else 0
        if status:
            p.setFont(f)
            p.setPen(qcolor(index.data(ROLE_STATUS_COLOR) or T.text3))
            st = fm.elidedText(status, QtCore.Qt.ElideRight, sw)
            p.drawText(QtCore.QRectF(r.right() - sw - 10, r.top(), sw, r.height()),
                       QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, st)
        f2 = QtGui.QFont(option.font)
        f2.setPixelSize(T.fs_body)
        f2.setWeight(QtGui.QFont.DemiBold if sel else QtGui.QFont.Normal)
        p.setFont(f2)
        p.setPen(qcolor(T.text))
        name_w = r.right() - x - sw - 18
        name = QtGui.QFontMetrics(f2).elidedText(str(index.data(QtCore.Qt.DisplayRole)), QtCore.Qt.ElideRight,
                                                  int(max(20, name_w)))
        p.drawText(QtCore.QRectF(x, r.top(), max(20, name_w), r.height()),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, name)
        p.restore()


class ObjectsPanel(FloatCard):
    """Collapsible list of everything in the house."""

    collapsedChanged = QtCore.Signal(bool, bool)   # collapsed, by the user

    WIDTH = 248
    # Folded: an icon tile as wide as the tool palette above it, so it never
    # covers the plan (the canvas reserves only this much on the left).
    COLLAPSED_W = 66

    def __init__(self, ctx: StudioContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._collapsed = False
        self._max_h = 600
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        head = self._head = QtWidgets.QWidget()
        hl = self._head_layout = hbox(spacing=6, margins=(6, 0, 0, 0))
        head.setLayout(hl)
        ic = self._head_icon = QtWidgets.QLabel()
        ic.setFixedSize(16, 16)
        ic.setPixmap(icon("layers", T.text2, 16).pixmap(16, 16))
        self.lbl_title = label("Objects", "DockTitle")
        self.btn_collapse = icon_button("chevron_up", "Hide the list", size=28, icon_size=14)
        self.btn_collapse.clicked.connect(lambda: self.set_collapsed(not self._collapsed))
        hl.addWidget(ic)
        hl.addWidget(self.lbl_title)
        hl.addStretch(1)
        hl.addWidget(self.btn_collapse)
        head.setCursor(QtCore.Qt.PointingHandCursor)
        head.mousePressEvent = lambda e: self.set_collapsed(not self._collapsed)
        lay.addWidget(head)

        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("ObjList")
        self.list.setItemDelegate(_RowDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setFocusPolicy(QtCore.Qt.NoFocus)
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.list.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.list.viewport().setAutoFillBackground(False)
        self.list.itemClicked.connect(self._clicked)
        lay.addWidget(self.list, 1)

        self.btn_add_window = button("Window", "Ghost", "Add a window on the freest wall", "plus", T.window)
        self.btn_add_speaker = button("Speaker", "Ghost", "Add a speaker inside the house", "plus", T.speaker)
        self.btn_add_window.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_add_speaker.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_add_window.clicked.connect(self.ctx.add_window)
        self.btn_add_speaker.clicked.connect(lambda: self.ctx.add_speaker())
        self.add_row = QtWidgets.QWidget()
        self.add_row.setLayout(hbox(self.btn_add_window, self.btn_add_speaker, None, spacing=4,
                                    margins=(2, 2, 2, 0)))
        lay.addWidget(self.add_row)

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.refresh)

        ctx.roomReplaced.connect(self.refresh)
        ctx.roomEdited.connect(lambda area, _src: self.refresh_later() if area != "sound" and area != "rain" else None)
        ctx.selectionChanged.connect(self._sync_selection)
        ctx.devicesChanged.connect(self.refresh_later)
        self.refresh()

    # ----- content -----
    def _item(self, text, sel, ic, status="", status_color=None):
        it = QtWidgets.QListWidgetItem(text)
        it.setData(ROLE_SEL, sel)
        it.setData(QtCore.Qt.DecorationRole, ic)
        it.setData(ROLE_STATUS, status)
        it.setData(ROLE_STATUS_COLOR, status_color)
        it.setSizeHint(QtCore.QSize(10, ROW_H))
        self.list.addItem(it)
        return it

    def _header(self, text):
        it = QtWidgets.QListWidgetItem(text)
        it.setData(ROLE_HEADER, True)
        it.setFlags(QtCore.Qt.NoItemFlags)
        it.setSizeHint(QtCore.QSize(10, HEAD_H))
        self.list.addItem(it)

    def refresh_later(self) -> None:
        self._timer.start()

    def refresh(self) -> None:
        r = self.ctx.room
        self.list.blockSignals(True)
        self.list.clear()
        self._item(r.name or "House", NONE_SEL, icon("house", T.text2, 16),
                   f"{r.width:.1f} × {r.depth:.1f} m")
        L = r.listener
        self._item("You", ("listener", 0), icon("you", T.you, 16),
                   self.ctx.device_label(getattr(L, "audio_device", None), "default output"))
        self._header(f"Windows · {len(r.windows)}")
        for i, w in enumerate(r.windows):
            st = "closed" if w.open <= 0.005 else f"{int(round(w.open * 100))}% open"
            self._item(w.name, ("window", i), icon("window", T.window, 16), st)
        self._header(f"Speakers · {len(r.speakers)}")
        for i, s in enumerate(r.speakers):
            if not s.enabled:
                st, col, ic = "off", T.text3, icon("speaker", T.text3, 16)
            elif s.audio_device is None:
                st, col, ic = "no device", T.warning, icon("speaker", T.text2, 16)
            else:
                st, col, ic = self.ctx.device_label(s.audio_device), T.text3, icon("speaker", T.speaker, 16)
            self._item(s.name, ("speaker", i), ic, st, col)
        self.list.blockSignals(False)
        self._sync_selection(self.ctx.selected)
        self._resize_to_content()

    def _sync_selection(self, sel) -> None:
        self.list.blockSignals(True)
        self.list.clearSelection()
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(ROLE_HEADER):
                continue
            if tuple(it.data(ROLE_SEL) or ()) == tuple(sel):
                it.setSelected(True)
                self.list.setCurrentItem(it)
                self.list.scrollToItem(it)
                break
        self.list.blockSignals(False)
        self.list.viewport().update()

    def _clicked(self, it: QtWidgets.QListWidgetItem) -> None:
        sel = it.data(ROLE_SEL)
        if sel is not None:
            self.ctx.select(tuple(sel), force=True)

    # ----- sizing -----
    def set_collapsed(self, on: bool, user: bool = True) -> None:
        self._collapsed = bool(on)
        self.list.setVisible(not on)
        self.add_row.setVisible(not on)
        self.lbl_title.setVisible(not on)
        self.btn_collapse.setVisible(not on)
        # centre the icon in the folded tile (card inner width = tile - shadow pad - layout margins)
        inner = self.COLLAPSED_W - 2 * self.PAD - 12
        self._head_layout.setContentsMargins((max(0, inner - 16) // 2) if on else 6, 0, 0, 0)
        self._head.setToolTip("Objects — show the list of windows, speakers and You" if on else "")
        self._head_icon.setPixmap(icon("layers", T.accent if on else T.text2, 16).pixmap(16, 16))
        self.btn_collapse.setIcon(icon("chevron_down" if on else "chevron_up", T.text2, 14))
        self.btn_collapse.setToolTip("Show the list" if on else "Hide the list")
        self._resize_to_content()
        self.collapsedChanged.emit(self._collapsed, user)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_max_height(self, h: int) -> None:
        self._max_h = max(80, int(h))
        self._resize_to_content()

    def content_height(self) -> int:
        m = self.contentsMargins()
        base = m.top() + m.bottom() + 12 + 32  # layout margins + header
        if self._collapsed:
            return base
        rows = 0
        for i in range(self.list.count()):
            rows += HEAD_H if self.list.item(i).data(ROLE_HEADER) else ROW_H
        return base + 4 + rows + 4 + 36

    def _resize_to_content(self) -> None:
        h = min(self._max_h, self.content_height())
        self.setFixedSize(self.COLLAPSED_W if self._collapsed else self.WIDTH, h)


__all__ = ["ObjectsPanel"]
