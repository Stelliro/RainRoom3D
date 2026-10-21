"""Floating inspector card: shows only the selected thing's settings.

Nothing selected → the House page. Window / Speaker / You → their page.
The card sizes itself to its content and scrolls when the canvas is short.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from app.ui.studio.context import NONE_SEL, StudioContext
from app.ui.studio.controls import FloatCard, hline, icon_button, label, scroll_area
from app.ui.studio.icons import icon
from app.ui.studio.page_house import HousePage
from app.ui.studio.page_speaker import SpeakerPage
from app.ui.studio.page_window import WindowPage
from app.ui.studio.page_you import YouPage
from app.ui.theme import T

PAGE_HOUSE, PAGE_WINDOW, PAGE_SPEAKER, PAGE_YOU = range(4)
_KIND_PAGE = {"none": PAGE_HOUSE, "window": PAGE_WINDOW, "speaker": PAGE_SPEAKER, "listener": PAGE_YOU}
_KIND_ICON = {"none": ("house", T.text2), "window": ("window", T.window),
              "speaker": ("speaker", T.speaker), "listener": ("you", T.you)}


class _Pages(QtWidgets.QWidget):
    """Like a QStackedWidget, but only the current page takes space, so the
    card can shrink to a short page and wrapped text measures correctly."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._pages = []
        self._cur = -1

    def addWidget(self, w: QtWidgets.QWidget) -> None:  # noqa: N802 (QStackedWidget-like)
        self._pages.append(w)
        self._lay.addWidget(w)
        w.setVisible(len(self._pages) == 1)
        if self._cur < 0:
            self._cur = 0

    def setCurrentIndex(self, i: int) -> None:  # noqa: N802
        self._cur = i
        for k, w in enumerate(self._pages):
            w.setVisible(k == i)

    def currentIndex(self) -> int:  # noqa: N802
        return self._cur

    def currentWidget(self) -> QtWidgets.QWidget:  # noqa: N802
        return self._pages[self._cur]

    def count(self) -> int:
        return len(self._pages)

    def widget(self, i: int) -> QtWidgets.QWidget:
        return self._pages[i]


class InspectorCard(FloatCard):
    WIDTH = 352

    def __init__(self, ctx: StudioContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._max_h = 800
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(head)
        hl.setContentsMargins(16, 14, 10, 12)
        hl.setSpacing(10)
        self.lbl_icon = QtWidgets.QLabel()
        self.lbl_icon.setFixedSize(32, 32)
        self.lbl_icon.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_icon.setStyleSheet(f"background: {T.overlay}; border: 1px solid {T.border}; border-radius: 8px;")
        titles = QtWidgets.QVBoxLayout()
        titles.setSpacing(1)
        self.lbl_title = label("House", "CardTitle")
        self.lbl_sub = label("", "CardSub")
        titles.addWidget(self.lbl_title)
        titles.addWidget(self.lbl_sub)
        self.btn_close = icon_button("close", "Deselect  (Esc)", size=28, icon_size=16)
        self.btn_close.clicked.connect(lambda: self.ctx.select(NONE_SEL))
        hl.addWidget(self.lbl_icon, 0, QtCore.Qt.AlignTop)
        hl.addLayout(titles, 1)
        hl.addWidget(self.btn_close, 0, QtCore.Qt.AlignTop)
        outer.addWidget(head)
        outer.addWidget(hline())
        self._head = head

        self.stack = _Pages()
        self.house = HousePage(ctx)
        self.window_page = WindowPage(ctx)
        self.speaker = SpeakerPage(ctx)
        self.you = YouPage(ctx)
        for pg in (self.house, self.window_page, self.speaker, self.you):
            self.stack.addWidget(pg)
        body = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(body)
        bl.setContentsMargins(16, 12, 14, 16)
        bl.addWidget(self.stack)
        bl.addStretch(1)
        self._body = body
        self.scroll = scroll_area(body)
        outer.addWidget(self.scroll, 1)

        self._refresh = QtCore.QTimer(self)
        self._refresh.setSingleShot(True)
        self._refresh.setInterval(40)
        self._refresh.timeout.connect(self._reload_current)
        self._pending_full = False

        ctx.selectionChanged.connect(self.show_selection)
        ctx.roomEdited.connect(self._on_edited)
        ctx.roomReplaced.connect(lambda: self.house.load())
        ctx.devicesChanged.connect(self.house.update_routing)
        self.setFixedWidth(self.WIDTH)
        self.show_selection(NONE_SEL)

    # ----- selection -----
    def show_selection(self, sel) -> None:
        kind, idx = tuple(sel)
        page = _KIND_PAGE.get(kind, PAGE_HOUSE)
        if page == PAGE_WINDOW:
            self.window_page.load(idx)
        elif page == PAGE_SPEAKER:
            self.speaker.load(idx)
        elif page == PAGE_YOU:
            self.you.load()
        else:
            self.house.load()
        self.stack.setCurrentIndex(page)
        self._update_header()
        self.scroll.verticalScrollBar().setValue(0)
        self._fit()

    def current_page(self) -> int:
        return self.stack.currentIndex()

    def _update_header(self) -> None:
        kind, idx = self.ctx.selected
        r = self.ctx.room
        ic, col = _KIND_ICON.get(kind, _KIND_ICON["none"])
        self.lbl_icon.setPixmap(icon(ic, col, 18).pixmap(18, 18))
        if kind == "window" and 0 <= idx < len(r.windows):
            title, sub = r.windows[idx].name, self.window_page.subtitle()
        elif kind == "speaker" and 0 <= idx < len(r.speakers):
            title, sub = r.speakers[idx].name, self.speaker.subtitle()
        elif kind == "listener":
            title, sub = "You", self.you.subtitle()
        else:
            title, sub = "House", f"{r.width:.1f} × {r.depth:.1f} m · {r.height:.1f} m ceiling"
        self.lbl_title.setText(title)
        self.lbl_sub.setText(sub)
        self.btn_close.setVisible(kind != "none")

    def title(self) -> str:
        return self.lbl_title.text()

    # ----- edits -----
    def _on_edited(self, area: str, source) -> None:
        if area in ("sound", "rain", "view"):
            return
        page = self.stack.currentWidget()
        if source is page:
            self._update_header()   # the page already shows the value being edited
            if page is self.house:
                self.house.update_routing()
            return
        self._refresh.start()

    def _reload_current(self) -> None:
        kind, idx = self.ctx.selected
        page = self.stack.currentIndex()
        if page == PAGE_WINDOW:
            self.window_page.load(idx)
        elif page == PAGE_SPEAKER:
            self.speaker.load(idx)
        elif page == PAGE_YOU:
            self.you.load()
        else:
            self.house.load()
        self._update_header()
        self._fit()

    # ----- sizing -----
    def set_max_height(self, h: int) -> None:
        self._max_h = max(160, int(h))
        self._fit()

    def _fit(self) -> None:
        """Card height = header + current page (at the card's width), capped."""
        m = self.contentsMargins()
        width = self.WIDTH - m.left() - m.right() - 12  # as if the scroll bar were showing
        lay = self._body.layout()
        lay.invalidate()
        body_h = lay.totalHeightForWidth(width) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        want = m.top() + m.bottom() + self._head.sizeHint().height() + 1 + body_h + 2
        self.setFixedHeight(max(160, min(self._max_h, want)))


__all__ = ["InspectorCard", "PAGE_HOUSE", "PAGE_SPEAKER", "PAGE_WINDOW", "PAGE_YOU"]
