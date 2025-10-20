
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL.GL import *
from OpenGL.GLU import gluPerspective, gluLookAt
import math, random, time

PICK_NONE = ("none", -1)

class GLRoomView(QOpenGLWidget):
    cameraChanged = QtCore.Signal(float, float, float)
    selectionChanged = QtCore.Signal(tuple)
    requestRepaint = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(720, 520)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.yaw = 0.5
        self.pitch = 0.35
        self.distance = 7.0
        self.last = None
        self.room = None
        self.roof_type = "Flat"
        self._zen = True
        self._t0 = time.time()
        self._rain_particles = []
        self._max_particles = 900
        self._wind_scale = 2.0
        self._selection = PICK_NONE
        self._dragging = False
        self.setMouseTracking(True)
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    # ... snip: identical to your current file, except these two functions change ...

    def _screen_ray(self, x, y):
        # Use camera basis vectors to compute ray more robustly
        w, h = self.width(), self.height()
        nx = (2.0*x/w - 1.0)
        ny = 1.0 - (2.0*y/h)
        fov = math.radians(60.0)
        tan = math.tan(fov*0.5)
        # camera position on orbit
        cam = (
            self.distance*math.sin(self.yaw)*math.cos(self.pitch),
            self.distance*math.sin(self.pitch),
            self.distance*math.cos(self.yaw)*math.cos(self.pitch),
        )
        # camera axes
        forward = (-math.sin(self.yaw)*math.cos(self.pitch),
                   -math.sin(self.pitch),
                   -math.cos(self.yaw)*math.cos(self.pitch))
        right = (math.cos(self.yaw), 0.0, -math.sin(self.yaw))
        up = (0.0, 1.0, 0.0)
        dir_world = (
            forward[0] + right[0]*nx*tan + up[0]*ny*tan,
            forward[1] + right[1]*nx*tan + up[1]*ny*tan,
            forward[2] + right[2]*nx*tan + up[2]*ny*tan,
        )
        # normalize
        L = math.sqrt(dir_world[0]**2 + dir_world[1]**2 + dir_world[2]**2) + 1e-6
        rd = (dir_world[0]/L, dir_world[1]/L, dir_world[2]/L)
        return cam, rd

    def _try_select(self, pos):
        ro, rd = self._screen_ray(pos.x(), pos.y())
        # intersect with floor (y=0)
        if abs(rd[1]) < 1e-6:
            self._selection = PICK_NONE; self.selectionChanged.emit(self._selection); return
        t = (0.0 - ro[1]) / rd[1]
        hx = ro[0] + rd[0]*t
        hz = ro[2] + rd[2]*t
        sel = PICK_NONE
        best = 0.5  # bigger pick radius
        # compare to object centers projected to floor
        if self.room:
            for i, s in enumerate(self.room.speakers):
                sx = s.x - self.room.width/2; sz = s.z - self.room.depth/2
                d = math.hypot(hx-sx, hz-sz)
                if d < best: best = d; sel = ("speaker", i)
            for i, hp in enumerate(self.room.headphones_items):
                sx = hp.x - self.room.width/2; sz = hp.z - self.room.depth/2
                d = math.hypot(hx-sx, hz-sz)
                if d < best: best = d; sel = ("headphones", i)
            for i, w in enumerate(self.room.windows):
                sx = (w.x + min(self.room.width, w.x + w.width))/2 - self.room.width/2
                sz = w.z - self.room.depth/2
                d = math.hypot(hx-sx, hz-sz)
                if d < best: best = d; sel = ("window", i)
        self._selection = sel
        self.selectionChanged.emit(sel)
        self.update()

    def _draw_selection_ring(self):
        if self._selection == PICK_NONE or not self.room: return
        kind, idx = self._selection
        if kind == "speaker":
            x = self.room.speakers[idx].x - self.room.width/2
            z = self.room.speakers[idx].z - self.room.depth/2
        elif kind == "headphones":
            x = self.room.headphones_items[idx].x - self.room.width/2
            z = self.room.headphones_items[idx].z - self.room.depth/2
        else:
            x = (self.room.windows[idx].x + min(self.room.width, self.room.windows[idx].x + self.room.windows[idx].width))/2 - self.room.width/2
            z = self.room.windows[idx].z - self.room.depth/2
        r = 0.25
        glLineWidth(2.0); glColor3f(0.6,0.8,1.0)
        glBegin(GL_LINE_LOOP)
        for i in range(32):
            a = 2*math.pi*i/32.0
            glVertex3f(x + r*math.cos(a), 0.01, z + r*math.sin(a))
        glEnd()
