from PySide6 import QtCore, QtGui, QtWidgets
import math


class WindDirectionWheel(QtWidgets.QWidget):
    """Compass wheel with an arrow showing where wind (rain) is blown toward.

    Degrees: 0°=North (up), 90°=East (right), 180°=South, 270°=West.
    """

    valueChanged = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._deg = 90  # default east
        self._dragging = False
        self.setFixedSize(132, 132)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed,
            QtWidgets.QSizePolicy.Fixed,
        )
        self.setToolTip(
            "Drag to set wind direction. Arrow points where rain is blown toward.\n"
            "0°=North, 90°=East, 180°=South, 270°=West."
        )
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def value(self) -> int:
        return int(self._deg) % 360

    def setValue(self, deg: int):
        d = int(deg) % 360
        if d != self._deg:
            self._deg = d
            self.update()
            self.valueChanged.emit(self._deg)

    def sizeHint(self):
        return QtCore.QSize(140, 140)

    def _center_radius(self):
        side = min(self.width(), self.height())
        r = side * 0.42
        c = QtCore.QPointF(self.width() * 0.5, self.height() * 0.5)
        return c, r

    def _deg_from_pos(self, pos: QtCore.QPointF) -> int:
        c, _ = self._center_radius()
        dx = pos.x() - c.x()
        dy = pos.y() - c.y()
        # Screen: +x right, +y down. Compass: 0=up(N), clockwise.
        ang = math.degrees(math.atan2(dx, -dy))  # 0 up, + toward east
        if ang < 0:
            ang += 360.0
        return int(round(ang)) % 360

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

    def paintEvent(self, e: QtGui.QPaintEvent):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        c, r = self._center_radius()

        # Disc
        p.setPen(QtGui.QPen(QtGui.QColor("#3d4a5c"), 2))
        p.setBrush(QtGui.QColor("#1a2230"))
        p.drawEllipse(c, r, r)

        # Inner ring
        p.setPen(QtGui.QPen(QtGui.QColor("#2a3545"), 1))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(c, r * 0.72, r * 0.72)

        # Tick marks every 30°
        for i in range(12):
            a = math.radians(i * 30.0)
            # 0° up
            outer = QtCore.QPointF(
                c.x() + r * 0.92 * math.sin(a),
                c.y() - r * 0.92 * math.cos(a),
            )
            inner = QtCore.QPointF(
                c.x() + r * (0.78 if i % 3 == 0 else 0.84) * math.sin(a),
                c.y() - r * (0.78 if i % 3 == 0 else 0.84) * math.cos(a),
            )
            p.setPen(QtGui.QPen(QtGui.QColor("#6b7c93" if i % 3 == 0 else "#3d4a5c"), 2 if i % 3 == 0 else 1))
            p.drawLine(inner, outer)

        # Cardinal labels
        font = p.font()
        font.setBold(True)
        font.setPointSize(max(8, int(r * 0.14)))
        p.setFont(font)
        p.setPen(QtGui.QColor("#94a3b8"))
        for label, ang in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
            a = math.radians(float(ang))
            lp = QtCore.QPointF(
                c.x() + r * 0.58 * math.sin(a) - 5,
                c.y() - r * 0.58 * math.cos(a) + 5,
            )
            p.drawText(lp, label)

        # Arrow: points where wind blows rain toward
        a = math.radians(float(self._deg % 360))
        # Unit vector in compass frame (0=up)
        ux = math.sin(a)
        uy = -math.cos(a)
        # Perpendicular for arrow head
        px, py = -uy, ux

        tip = QtCore.QPointF(c.x() + ux * r * 0.78, c.y() + uy * r * 0.78)
        tail = QtCore.QPointF(c.x() - ux * r * 0.28, c.y() - uy * r * 0.28)
        # Shaft
        p.setPen(QtGui.QPen(QtGui.QColor("#38bdf8"), 3.5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        p.drawLine(tail, tip)
        # Arrow head
        head_len = r * 0.28
        head_w = r * 0.16
        base = QtCore.QPointF(tip.x() - ux * head_len, tip.y() - uy * head_len)
        left = QtCore.QPointF(base.x() + px * head_w, base.y() + py * head_w)
        right = QtCore.QPointF(base.x() - px * head_w, base.y() - py * head_w)
        head = QtGui.QPolygonF([tip, left, right])
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("#38bdf8"))
        p.drawPolygon(head)
        # Small hub
        p.setBrush(QtGui.QColor("#e2e8f0"))
        p.drawEllipse(c, r * 0.07, r * 0.07)

        # Degree text under hub
        font.setPointSize(max(7, int(r * 0.12)))
        font.setBold(False)
        p.setFont(font)
        p.setPen(QtGui.QColor("#cbd5e1"))
        txt = f"{self._deg}°"
        br = p.fontMetrics().boundingRect(txt)
        p.drawText(
            QtCore.QPointF(c.x() - br.width() * 0.5, c.y() + r * 0.95),
            txt,
        )


class Knob(QtWidgets.QDial):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setNotchesVisible(True)

class Meter(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.v = 0.0
    def set_value(self, v):
        self.v = max(0.0, min(1.0, v))
        self.update()
    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        r = self.rect()
        p.fillRect(r, QtGui.QColor('#111'))
        h = int(r.height()*self.v)
        p.fillRect(QtCore.QRect(r.left(), r.bottom()-h, r.width(), h), QtGui.QColor('#3aa'))

class RoomCanvas(QtWidgets.QWidget):
    yawChanged = QtCore.Signal(float)
    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumSize(480, 360)
        self.room = None
        self.dragging = False
        self.last_pos = None
    def set_room(self, room):
        self.room = room
        self.update()
    def _project(self, x, z):
        if not self.room: return 0,0
        margin = 40
        W = self.width() - 2*margin
        H = self.height() - 2*margin
        sx = W / max(1e-3, self.room.width)
        sz = H / max(1e-3, self.room.depth)
        px = margin + x*sx
        pz = margin + z*sz
        return int(px), int(pz)
    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.dragging = True
            self.last_pos = e.position()
    def mouseMoveEvent(self, e):
        if self.dragging and self.room:
            dx = e.position().x() - self.last_pos.x()
            yaw_delta = dx * 0.01
            self.room.listener.yaw += yaw_delta
            self.last_pos = e.position()
            self.yawChanged.emit(self.room.listener.yaw)
            self.update()
    def mouseReleaseEvent(self, e):
        self.dragging = False
    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = self.rect()
        p.fillRect(r, QtGui.QColor('#0b0e11'))
        if not self.room: return
        p.setPen(QtGui.QPen(QtGui.QColor('#444'), 2))
        margin = 40
        p.drawRect(margin, margin, self.width()-2*margin, self.height()-2*margin)
        # windows
        p.setPen(QtGui.QPen(QtGui.QColor('#2aa198'), 3))
        for w in self.room.windows:
            x1,z1 = self._project(w.x, w.z)
            x2,_ = self._project(min(self.room.width, w.x+w.width), w.z)
            p.drawLine(x1,z1, x2,z1)
            p.setBrush(QtGui.QColor(42,161,152, int(80*max(0.0,min(1.0,w.open)))))
            p.drawEllipse(QtCore.QPointF((x1+x2)/2, z1), 6+8*w.open, 6+8*w.open)
        # speakers
        p.setPen(QtGui.QPen(QtGui.QColor('#b58900'), 2))
        p.setBrush(QtGui.QColor('#b58900'))
        for s in self.room.speakers:
            x,z = self._project(s.x, s.z)
            p.drawEllipse(QtCore.QPoint(x, z), 6, 6)
        # listener
        L = self.room.listener
        lx,lz = self._project(L.x, L.z)
        p.setBrush(QtGui.QColor('#268bd2'))
        p.setPen(QtGui.QPen(QtGui.QColor('#268bd2'), 2))
        p.drawEllipse(QtCore.QPoint(lx, lz), 7, 7)
        rlen = 24
        x2 = lx + rlen*math.sin(L.yaw)
        z2 = lz + rlen*math.cos(L.yaw)
        p.drawLine(lx,lz, int(x2), int(z2))
