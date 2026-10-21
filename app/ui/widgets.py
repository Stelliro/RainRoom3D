"""Reusable widgets: compass wheels, collapsible sections, preset chips."""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from app.ui.theme import C, T, qcolor


class CompassWheel(QtWidgets.QWidget):
    """Drag-to-set compass. 0° = North (up), 90° = East (right), clockwise."""

    valueChanged = QtCore.Signal(int)

    def __init__(self, parent=None, size: int = 120, color: str = T.accent, show_degrees: bool = True):
        super().__init__(parent)
        self._deg = 90
        self._dragging = False
        self._color = color
        self._show_deg = show_degrees
        self.setFixedSize(size, size)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def value(self) -> int:
        return int(self._deg) % 360

    def setValue(self, deg: int):  # noqa: N802 (Qt-style API)
        d = int(deg) % 360
        if d != self._deg:
            self._deg = d
            self.update()
            self.valueChanged.emit(self._deg)

    def sizeHint(self):
        return self.size()

    def _center_radius(self):
        side = min(self.width(), self.height())
        return QtCore.QPointF(self.width() * 0.5, self.height() * 0.5), side * 0.44

    def _deg_from_pos(self, pos: QtCore.QPointF) -> int:
        c, _ = self._center_radius()
        ang = math.degrees(math.atan2(pos.x() - c.x(), -(pos.y() - c.y())))
        return int(round(ang + 360.0)) % 360

    def mousePressEvent(self, e: QtGui.QMouseEvent):
        if e.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self.setValue(self._deg_from_pos(e.position()))
            e.accept()

    def mouseMoveEvent(self, e: QtGui.QMouseEvent):
        if self._dragging and (e.buttons() & QtCore.Qt.LeftButton):
            self.setValue(self._deg_from_pos(e.position()))
            e.accept()

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent):
        if e.button() == QtCore.Qt.LeftButton:
            self._dragging = False
            e.accept()

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        step = 15 if e.modifiers() & QtCore.Qt.ShiftModifier else 1
        if e.key() in (QtCore.Qt.Key_Right, QtCore.Qt.Key_Up):
            self.setValue(self._deg + step)
        elif e.key() in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Down):
            self.setValue(self._deg - step)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        c, r = self._center_radius()
        en = self.isEnabled()
        accent = qcolor(self._color if en else T.disabled)

        p.setPen(QtGui.QPen(qcolor(C.wheel_ring), 1.5))
        p.setBrush(qcolor(C.wheel_disc))
        p.drawEllipse(c, r, r)
        if self.hasFocus():
            p.setPen(QtGui.QPen(qcolor(T.accent_line), 1.5))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(c, r + 1.5, r + 1.5)

        for i in range(24):
            a = math.radians(i * 15.0)
            major = i % 6 == 0
            r0 = r * (0.80 if major else 0.87)
            p.setPen(QtGui.QPen(qcolor(C.wheel_tick_major if major else C.wheel_tick), 1.6 if major else 1))
            p.drawLine(QtCore.QPointF(c.x() + r0 * math.sin(a), c.y() - r0 * math.cos(a)),
                       QtCore.QPointF(c.x() + r * 0.94 * math.sin(a), c.y() - r * 0.94 * math.cos(a)))

        font = p.font()
        font.setBold(True)
        font.setPixelSize(max(9, int(r * 0.2)))
        p.setFont(font)
        fm = p.fontMetrics()
        for lab, ang in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
            a = math.radians(float(ang))
            p.setPen(qcolor(T.text if lab == "N" else C.wheel_label))
            x = c.x() + r * 0.6 * math.sin(a) - fm.horizontalAdvance(lab) / 2
            y = c.y() - r * 0.6 * math.cos(a) + fm.ascent() / 2 - 1
            p.drawText(QtCore.QPointF(x, y), lab)

        a = math.radians(float(self._deg % 360))
        ux, uy = math.sin(a), -math.cos(a)
        px, py = -uy, ux
        tip = QtCore.QPointF(c.x() + ux * r * 0.80, c.y() + uy * r * 0.80)
        tail = QtCore.QPointF(c.x() - ux * r * 0.30, c.y() - uy * r * 0.30)
        p.setPen(QtGui.QPen(accent, 3, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        hl, hw = r * 0.26, r * 0.15
        base = QtCore.QPointF(tip.x() - ux * hl, tip.y() - uy * hl)
        p.drawLine(tail, base)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(accent)
        p.drawPolygon(QtGui.QPolygonF([tip, QtCore.QPointF(base.x() + px * hw, base.y() + py * hw),
                                       QtCore.QPointF(base.x() - px * hw, base.y() - py * hw)]))
        p.setBrush(qcolor(T.text))
        p.drawEllipse(c, r * 0.07, r * 0.07)

        if self._show_deg:
            font.setBold(False)
            font.setPixelSize(max(9, int(r * 0.17)))
            p.setFont(font)
            p.setPen(qcolor(T.text2))
            txt = f"{self._deg}°"
            br = p.fontMetrics().horizontalAdvance(txt)
            p.drawText(QtCore.QPointF(c.x() - br * 0.5, c.y() + r * 0.42), txt)


class WindDirectionWheel(CompassWheel):
    """Arrow shows where the wind blows the rain toward."""

    def __init__(self, parent=None, size: int = 120):
        super().__init__(parent, size=size, color=T.accent, show_degrees=False)
        self.setToolTip(
            "Drag to set the wind direction. The arrow points where the rain is blown.\n"
            "0° = North, 90° = East, 180° = South, 270° = West."
        )


class CollapsibleSection(QtWidgets.QWidget):
    """A header you click to fold its body away — keeps long panels short
    while every control stays one click from view."""

    toggled = QtCore.Signal(bool)

    def __init__(self, title: str, subtitle: str = "", expanded: bool = True,
                 nested: bool = False, parent=None):
        super().__init__(parent)
        from app.ui.studio.icons import icon

        self._nested = nested
        self._icons = (icon("chevron_right", T.text3, 14), icon("chevron_down", T.text2, 14))
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.header = QtWidgets.QToolButton()
        self.header.setObjectName("SubSectionHeader" if nested else "SectionHeader")
        self.header.setText(title if nested else title.upper())
        self.header.setCheckable(True)
        self.header.setChecked(bool(expanded))
        self.header.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.header.setIconSize(QtCore.QSize(14, 14))
        self.header.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.header.setCursor(QtCore.Qt.PointingHandCursor)
        self.header.setFocusPolicy(QtCore.Qt.NoFocus)
        lay.addWidget(self.header)
        self.subtitle = QtWidgets.QLabel(subtitle)
        self.subtitle.setObjectName("Hint")
        self.subtitle.setWordWrap(True)
        self.subtitle.setVisible(bool(subtitle))
        lay.addWidget(self.subtitle)
        self.body = QtWidgets.QWidget()
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(4 if nested else 0, 6, 0, 4)
        self.body_layout.setSpacing(14)
        lay.addWidget(self.body)
        self.header.toggled.connect(self.set_expanded)
        self.set_expanded(bool(expanded))

    def set_expanded(self, on: bool) -> None:
        on = bool(on)
        if self.header.isChecked() != on:
            self.header.setChecked(on)  # re-enters via toggled
            return
        self.header.setIcon(self._icons[1] if on else self._icons[0])
        self.body.setVisible(on)
        if not self._nested:
            self.subtitle.setVisible(on and bool(self.subtitle.text()))
        self.toggled.emit(on)

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def add_widget(self, w: QtWidgets.QWidget) -> None:
        self.body_layout.addWidget(w)

    def add_layout(self, layout: QtWidgets.QLayout) -> None:
        self.body_layout.addLayout(layout)


class PresetChips(QtWidgets.QWidget):
    """One-click preset chips. A trailing "Custom" chip lights up when the
    current values match no preset, so you always know where you stand."""

    chosen = QtCore.Signal(str)

    CUSTOM = "Custom"

    def __init__(self, columns: int = 3, parent=None):
        super().__init__(parent)
        from app.ui.studio.controls import FlowLayout

        self._columns = max(1, int(columns))  # kept for API compatibility (layout wraps)
        self._flow = FlowLayout(self, spacing=6)
        self._buttons = {}
        self._current = None
        self._custom = None

    def set_presets(self, items) -> None:
        """items: iterable of (name, tooltip) or (name, tooltip, chip text)."""
        while self._flow.count():
            it = self._flow.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                w.hide()
                w.deleteLater()
        self._buttons = {}
        for item in items:
            name, tip = item[0], item[1]
            b = self._chip(item[2] if len(item) > 2 else name, tip)
            b.clicked.connect(lambda _=False, n=name: self._on_click(n))
            self._buttons[name] = b
            self._flow.addWidget(b)
        self._custom = self._chip(self.CUSTOM, "Your own values — they match none of the presets.")
        self._custom.setProperty("custom", True)
        # Custom is a status, not a choice: clicking it just restores the highlight.
        self._custom.clicked.connect(lambda _=False: self.set_current(self._current))
        self._flow.addWidget(self._custom)
        self.set_current(self._current if self._current in self._buttons else None)
        self.updateGeometry()

    def _chip(self, text: str, tip: str) -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton(text, self)
        b.setObjectName("Chip")
        b.setCheckable(True)
        b.setToolTip(tip or text)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setFocusPolicy(QtCore.Qt.TabFocus)
        return b

    def _on_click(self, name: str) -> None:
        self.chosen.emit(name)
        self.set_current(self._current)  # owner normally calls set_current; keep chips sane

    def set_current(self, name) -> None:
        self._current = name if name in self._buttons else None
        for n, b in self._buttons.items():
            b.setChecked(n == self._current)
        if self._custom is not None:
            self._custom.setChecked(self._current is None)

    def current(self):
        return self._current

    def buttons(self):
        return dict(self._buttons)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self._flow.heightForWidth(w)

    def sizeHint(self):
        w = max(200, self.width())
        return QtCore.QSize(w, self._flow.heightForWidth(w))

    def minimumSizeHint(self):
        return QtCore.QSize(120, self._flow.heightForWidth(max(120, self.width())))


__all__ = ["CollapsibleSection", "CompassWheel", "PresetChips", "WindDirectionWheel"]
