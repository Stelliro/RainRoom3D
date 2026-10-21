"""Small building blocks shared by every studio panel.

* ``SegmentedControl`` — a row of mutually exclusive pill buttons
* ``ToggleSwitch``     — an on/off switch (a QCheckBox, so the API is familiar)
* ``FloatCard``        — a rounded, softly shadowed card that floats over the canvas
* ``FlowLayout``       — wraps chips onto as many lines as needed
* ``Form``             — label / field rows on the 8 px grid
* field factories (``spin``, ``combo``, ``line_edit``) that ignore stray
  mouse-wheel scrolls while you scroll a panel
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from app.ui.theme import SP, T, qcolor
from app.ui.studio.icons import icon


# ----- slider ticks (QSlider only does ints) -----

def _ticks(s, v: float) -> int:
    """Model value → integer slider position."""
    return int(round((float(v) - float(s.lo)) / float(s.step)))


def _from_ticks(s, t: int) -> float:
    # round() keeps 0.35 from becoming 0.35000000000000003 in saved JSON
    return round(float(s.lo) + int(t) * float(s.step), 6)


def repolish(w: QtWidgets.QWidget) -> None:
    st = w.style()
    st.unpolish(w)
    st.polish(w)
    w.update()


def set_prop(w: QtWidgets.QWidget, name: str, value) -> None:
    if w.property(name) != value:
        w.setProperty(name, value)
        repolish(w)


def is_text_input(w: Optional[QtWidgets.QWidget]) -> bool:
    return isinstance(w, (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox,
                          QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit))


# ----- wheel guard -----

class _WheelGuard(QtCore.QObject):
    """Spinboxes, combos and sliders only take the wheel once focused, so
    scrolling a panel never nudges a value by accident."""

    def eventFilter(self, obj, ev):  # noqa: N802 (Qt name)
        if ev.type() == QtCore.QEvent.Wheel and isinstance(obj, QtWidgets.QWidget):
            if not obj.hasFocus():
                area = obj.parentWidget()
                while area is not None and not isinstance(area, QtWidgets.QAbstractScrollArea):
                    area = area.parentWidget()
                if area is None:
                    return False  # not inside a scrolling panel: behave normally
                QtWidgets.QApplication.sendEvent(area.verticalScrollBar(), ev)
                return True
        return False


_GUARD: Optional[_WheelGuard] = None


def guard_wheel(w: QtWidgets.QWidget) -> QtWidgets.QWidget:
    global _GUARD
    if _GUARD is None:
        _GUARD = _WheelGuard(QtWidgets.QApplication.instance())
    w.setFocusPolicy(QtCore.Qt.StrongFocus)
    w.installEventFilter(_GUARD)
    return w


# ----- labels / separators -----

def label(text: str = "", name: str = "", wrap: bool = False) -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


class ElidedLabel(QtWidgets.QLabel):
    """One-line label that ends in … instead of pushing its neighbours."""

    def minimumSizeHint(self):
        return QtCore.QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setPen(self.palette().color(QtGui.QPalette.WindowText))
        p.setFont(self.font())
        text = self.fontMetrics().elidedText(self.text(), QtCore.Qt.ElideRight, self.width())
        p.drawText(self.rect(), int(self.alignment()) | QtCore.Qt.AlignVCenter, text)


def overline(text: str) -> QtWidgets.QLabel:
    return label(text.upper(), "Overline")


def caption(text: str = "", wrap: bool = True) -> QtWidgets.QLabel:
    return label(text, "Caption", wrap)


def hint(text: str = "", wrap: bool = True) -> QtWidgets.QLabel:
    return label(text, "Hint", wrap)


def hline() -> QtWidgets.QFrame:
    f = QtWidgets.QFrame()
    f.setObjectName("HLine")
    f.setFixedHeight(1)
    return f


def vsep(height: int = 24) -> QtWidgets.QFrame:
    f = QtWidgets.QFrame()
    f.setObjectName("VSep")
    f.setFixedSize(1, height)
    return f


# ----- fields -----

def spin(lo: float, hi: float, step: float = 0.05, decimals: int = 2, suffix: str = "",
         tip: str = "") -> QtWidgets.QDoubleSpinBox:
    s = QtWidgets.QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    if suffix:
        s.setSuffix(suffix)
    s.setKeyboardTracking(False)  # typed numbers apply on Enter / leaving the box
    s.setAccelerated(True)
    if tip:
        s.setToolTip(tip)
    s.setMinimumWidth(96)
    return guard_wheel(s)


def combo(items: Iterable = (), tip: str = "") -> QtWidgets.QComboBox:
    c = QtWidgets.QComboBox()
    for it in items:
        if isinstance(it, tuple):
            c.addItem(it[0], userData=it[1])
        else:
            c.addItem(str(it), userData=it)
    if tip:
        c.setToolTip(tip)
    c.setMinimumWidth(96)
    c.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(8)
    return guard_wheel(c)


def line_edit(placeholder: str = "", tip: str = "") -> QtWidgets.QLineEdit:
    e = QtWidgets.QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setClearButtonEnabled(True)
    if tip:
        e.setToolTip(tip)
    return e


def button(text: str, kind: str = "", tip: str = "", icon_name: str = "",
           icon_color: Optional[str] = None) -> QtWidgets.QPushButton:
    b = QtWidgets.QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if tip:
        b.setToolTip(tip)
    if icon_name:
        b.setIcon(icon(icon_name, icon_color or (T.danger if kind == "Danger" else T.text), 16))
        b.setIconSize(QtCore.QSize(16, 16))
    b.setCursor(QtCore.Qt.PointingHandCursor)
    # Clicking must not keep keyboard focus: Space is Play / Stop app-wide, and a
    # focused button would swallow it (and press itself again). Tab still reaches it.
    b.setFocusPolicy(QtCore.Qt.TabFocus)
    return b


def icon_button(name: str, tip: str, size: int = 32, icon_size: int = 18,
                color: Optional[str] = None, checkable: bool = False) -> QtWidgets.QToolButton:
    b = QtWidgets.QToolButton()
    b.setObjectName("IconBtn")
    b.setIcon(icon(name, color or T.text2, icon_size, on_color=T.accent))
    b.setIconSize(QtCore.QSize(icon_size, icon_size))
    b.setFixedSize(size, size)
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setFocusPolicy(QtCore.Qt.NoFocus)
    b.setCursor(QtCore.Qt.PointingHandCursor)
    b.setAutoRaise(True)
    return b


def slider(lo: int, hi: int, tip: str = "", name: str = "") -> QtWidgets.QSlider:
    s = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    s.setRange(lo, hi)
    if tip:
        s.setToolTip(tip)
    if name:
        s.setObjectName(name)
    s.setMinimumHeight(24)
    return guard_wheel(s)


# ----- segmented control -----

class SegmentedControl(QtWidgets.QFrame):
    """Exclusive pill buttons: [Design | Listen], [Plan | 3D]."""

    changed = QtCore.Signal(str)

    def __init__(self, parent=None, accent: bool = False, floating: bool = False):
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.setProperty("accent", accent)
        self.setProperty("floating", floating)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: Dict[str, QtWidgets.QPushButton] = {}
        self._current: Optional[str] = None
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

    def add(self, key: str, text: str, tip: str = "", icon_name: str = "") -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton(text)
        b.setObjectName("SegButton")
        b.setCheckable(True)
        b.setFocusPolicy(QtCore.Qt.NoFocus)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        if tip:
            b.setToolTip(tip)
        if icon_name:
            b.setIcon(icon(icon_name, T.text2, 16, on_color=T.accent if self.property("accent") else T.text))
            b.setIconSize(QtCore.QSize(16, 16))
        self._group.addButton(b)
        self.layout().addWidget(b)
        self._buttons[key] = b
        b.clicked.connect(lambda _=False, k=key: self._clicked(k))
        if self._current is None:
            self.set_current(key)
        return b

    def _clicked(self, key: str) -> None:
        if key != self._current:
            self._current = key
            self.changed.emit(key)

    def set_current(self, key: str, emit: bool = False) -> None:
        b = self._buttons.get(key)
        if b is None:
            return
        b.setChecked(True)
        changed = key != self._current
        self._current = key
        if emit and changed:
            self.changed.emit(key)

    def current(self) -> Optional[str]:
        return self._current

    def button(self, key: str) -> Optional[QtWidgets.QPushButton]:
        return self._buttons.get(key)

    def set_visible_key(self, key: str, on: bool) -> None:
        b = self._buttons.get(key)
        if b is not None:
            b.setVisible(on)


# ----- toggle switch -----

class ToggleSwitch(QtWidgets.QCheckBox):
    """A switch that reads as on/off at a glance. Same API as QCheckBox."""

    _TW, _TH = 34, 18

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.TabFocus)  # a click must not steal Space (Play / Stop)
        self._hover = False

    def sizeHint(self):
        fm = self.fontMetrics()
        w = self._TW + (10 + fm.horizontalAdvance(self.text()) if self.text() else 0)
        return QtCore.QSize(w + 2, max(26, fm.height() + 6))

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos):  # noqa: N802
        return self.rect().contains(pos)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        on, en = self.isChecked(), self.isEnabled()
        y = (self.height() - self._TH) / 2.0
        track = QtCore.QRectF(1, y, self._TW, self._TH)
        if on:
            col = qcolor(T.accent if en else T.disabled)
        else:
            col = qcolor(T.hover if self._hover and en else T.border_strong)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(track, self._TH / 2, self._TH / 2)
        r = self._TH / 2 - 3
        cx = track.right() - self._TH / 2 if on else track.left() + self._TH / 2
        knob = qcolor(T.accent_text if (on and en) else (T.text if en else T.text3))
        if on and en:
            knob = QtGui.QColor("#ffffff")
        p.setBrush(knob)
        p.drawEllipse(QtCore.QPointF(cx, track.center().y()), r, r)
        if self.hasFocus():
            p.setPen(QtGui.QPen(qcolor(T.accent_line), 1.5))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRoundedRect(track.adjusted(-1.5, -1.5, 1.5, 1.5), self._TH / 2 + 1, self._TH / 2 + 1)
        if self.text():
            p.setPen(qcolor(T.text if en else T.disabled))
            p.drawText(QtCore.QRectF(self._TW + 11, 0, self.width() - self._TW - 11, self.height()),
                       QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, self.text())


# ----- floating card -----

class FloatCard(QtWidgets.QFrame):
    """Rounded card over the canvas. The soft shadow is painted here (no
    graphics effects: they would re-render on every OpenGL frame)."""

    PAD = 10  # room for the shadow inside the widget rect

    def __init__(self, parent=None, radius: int = T.r_card):
        super().__init__(parent)
        self._radius = radius
        self.setAttribute(QtCore.Qt.WA_StyledBackground, False)
        self.setContentsMargins(self.PAD, self.PAD - 2, self.PAD, self.PAD + 2)

    def body_rect(self) -> QtCore.QRectF:
        return QtCore.QRectF(self.rect()).adjusted(self.PAD, self.PAD - 2, -self.PAD, -self.PAD - 2)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        body = self.body_rect()
        p.setPen(QtCore.Qt.NoPen)
        for i, a in enumerate((26, 18, 12, 7, 4)):
            grow = 1.5 + i * 1.6
            p.setBrush(QtGui.QColor(0, 0, 0, a))
            p.drawRoundedRect(body.adjusted(-grow, -grow + 2, grow, grow + 3),
                              self._radius + grow, self._radius + grow)
        bg = qcolor(T.raised)
        bg.setAlpha(250)
        p.setBrush(bg)
        p.setPen(QtGui.QPen(qcolor(T.border_strong), 1))
        p.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), self._radius, self._radius)


# ----- flow layout (chips) -----

class FlowLayout(QtWidgets.QLayout):
    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self._do(QtCore.QRect(0, 0, w, 0), True)

    def setGeometry(self, r):  # noqa: N802
        super().setGeometry(r)
        self._do(r, False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        s = QtCore.QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect: QtCore.QRect, test: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            if it.widget() is not None and it.widget().isHidden():
                continue
            hint = it.sizeHint()
            nx = x + hint.width() + self._sp
            if nx - self._sp > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._sp
                nx = x + hint.width() + self._sp
                line_h = 0
            if not test:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x = nx
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


# ----- form -----

class Form(QtWidgets.QWidget):
    """Sections of label / field rows. Labels sit left in secondary text,
    fields take the rest; full-width widgets and captions span both."""

    LABEL_W = 92

    def __init__(self, parent=None, label_width: int = LABEL_W):
        super().__init__(parent)
        self._grid = QtWidgets.QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(SP + 4)
        self._grid.setVerticalSpacing(SP)
        self._grid.setColumnMinimumWidth(0, label_width)
        self._grid.setColumnStretch(1, 1)
        self._row = 0
        self._label_w = label_width

    def section(self, title: str, first: bool = False) -> QtWidgets.QLabel:
        if not first:
            self._grid.setRowMinimumHeight(self._row, SP)
            self._row += 1
        lab = overline(title)
        self._grid.addWidget(lab, self._row, 0, 1, 2)
        self._row += 1
        return lab

    def row(self, text: str, field, tip: str = "") -> QtWidgets.QLabel:
        lab = label(text, "FieldLabel")
        lab.setMaximumWidth(self._label_w + 16)
        lab.setWordWrap(True)
        if tip:
            lab.setToolTip(tip)
            if isinstance(field, QtWidgets.QWidget) and not field.toolTip():
                field.setToolTip(tip)
        self._grid.addWidget(lab, self._row, 0, QtCore.Qt.AlignVCenter)
        if isinstance(field, QtWidgets.QLayout):
            self._grid.addLayout(field, self._row, 1)
        else:
            self._grid.addWidget(field, self._row, 1)
        self._row += 1
        return lab

    def full(self, w, align=None) -> None:
        if isinstance(w, QtWidgets.QLayout):
            self._grid.addLayout(w, self._row, 0, 1, 2)
        elif align is not None:
            self._grid.addWidget(w, self._row, 0, 1, 2, align)
        else:
            self._grid.addWidget(w, self._row, 0, 1, 2)
        self._row += 1

    def spacer(self, h: int = SP) -> None:
        self._grid.setRowMinimumHeight(self._row, h)
        self._row += 1


def hbox(*items, spacing: int = SP, margins=(0, 0, 0, 0)) -> QtWidgets.QHBoxLayout:
    lay = QtWidgets.QHBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, int):
            lay.addSpacing(it)
        elif isinstance(it, QtWidgets.QLayout):
            lay.addLayout(it)
        else:
            lay.addWidget(it)
    return lay


def scroll_area(inner: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
    sc = QtWidgets.QScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QtWidgets.QFrame.NoFrame)
    sc.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    sc.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
    inner.setObjectName("ScrollInner")
    sc.setWidget(inner)
    sc.viewport().setAutoFillBackground(False)
    sc.viewport().setStyleSheet("background: transparent;")
    return sc


def block(*widgets):
    """Context manager: block signals on several widgets at once."""
    class _B:
        def __enter__(self):
            self._old = [w.blockSignals(True) for w in widgets]
            return self

        def __exit__(self, *exc):
            for w, o in zip(widgets, self._old):
                w.blockSignals(o)
            return False
    return _B()


__all__ = [
    "FloatCard", "FlowLayout", "Form", "SegmentedControl", "ToggleSwitch", "_from_ticks", "_ticks",
    "block", "button", "caption", "combo", "guard_wheel", "hbox", "hint", "hline", "icon_button",
    "is_text_input", "label", "line_edit", "overline", "repolish", "scroll_area", "set_prop",
    "slider", "spin", "vsep",
]
