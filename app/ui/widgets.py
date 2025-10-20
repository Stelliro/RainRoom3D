from PySide6 import QtCore, QtGui, QtWidgets
import math

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
