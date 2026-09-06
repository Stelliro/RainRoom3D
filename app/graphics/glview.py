"""
OpenGL 3D house view for RainRoom3D.

Uses a Compatibility / 2.1-style fixed-function path with **unlit** solid
colours (lighting is optional). Core-profile drivers that reject glBegin
are detected and surface a clear on-screen error instead of a blank view.
"""

from __future__ import annotations

import logging
import math
import random
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtOpenGLWidgets import QOpenGLWidget

try:
    from OpenGL.GL import *
    from OpenGL.GLU import gluPerspective, gluLookAt
    _HAS_GL = True
except Exception:  # pragma: no cover
    _HAS_GL = False

log = logging.getLogger("graphics.glview")

PICK_NONE = ("none", -1)
MODE_MOVE, MODE_ROT, MODE_SCALE = 0, 1, 2


def configure_gl_surface_format() -> None:
    """Call before QApplication. Forces a desktop compatibility context."""
    fmt = QtGui.QSurfaceFormat()
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setSamples(4)
    fmt.setSwapBehavior(QtGui.QSurfaceFormat.DoubleBuffer)
    # Prefer 2.1 compatibility so glBegin / matrix stack work
    fmt.setVersion(2, 1)
    fmt.setProfile(QtGui.QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    fmt.setRenderableType(QtGui.QSurfaceFormat.OpenGL)
    QtGui.QSurfaceFormat.setDefaultFormat(fmt)


class GLRoomView(QOpenGLWidget):
    cameraChanged = QtCore.Signal(float, float, float)
    selectionChanged = QtCore.Signal(tuple)
    requestRepaint = QtCore.Signal()
    glStatus = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Ensure this widget asks for the same format
        try:
            fmt = QtGui.QSurfaceFormat.defaultFormat()
            if fmt.majorVersion() < 2:
                configure_gl_surface_format()
                fmt = QtGui.QSurfaceFormat.defaultFormat()
            self.setFormat(fmt)
        except Exception:
            pass

        self.setMinimumSize(400, 300)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)

        # Orbit camera (match software 3D defaults: outside looking at house)
        self.yaw = math.pi + 0.45
        self.pitch = 0.48
        self.distance = 14.0
        self._target = [0.0, 1.2, 0.0]
        self.room = None
        self.roof_type = "Flat"
        # Visual rain is independent of audio droplets. Batched GL lines are
        # cheap; keep a dense field without 1:1 Python glVertex calls.
        self._rain = np.zeros((0, 5), dtype=np.float32)  # x y z vy kind
        self._rain_verts = np.zeros((0, 3), dtype=np.float32)
        self._splashes = []
        self._max_particles = 2400
        self._selection = PICK_NONE
        self._dragging = False
        self._mode = MODE_MOVE
        self.last = None
        self._gl_ok = False
        self._gl_error = ""
        self._inited = False

        self._on_impacts = None
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_impact_handler(self, fn):
        """fn(list of (x, y, z, kind)) in room coordinates, called on landings."""
        self._on_impacts = fn

    # ---------- Public API ----------
    def set_room(self, room):
        first = self.room is None
        self.room = room
        self._init_particles()
        if first or self.distance < self._min_orbit_distance() * 1.05:
            self.fit_camera()
        else:
            self._clamp_camera()
            self._retarget()
        self.update()

    def set_roof(self, name):
        self.roof_type = name or "Flat"
        self.update()

    def set_transform_mode(self, mode):
        self._mode = int(mode)
        self.update()

    def fit_camera(self):
        self.yaw = math.pi + 0.45
        self.pitch = 0.48
        if not self.room:
            self.distance = 14.0
            self._target = [0.0, 1.2, 0.0]
        else:
            w = max(2.0, float(self.room.width))
            d = max(2.0, float(self.room.depth))
            h = max(2.0, float(self.room.height))
            diag = math.sqrt(w * w + d * d)
            self.distance = max(10.0, diag * 1.7 + h * 1.0)
            self._target = [0.0, h * 0.38, 0.0]
        self._clamp_camera()
        self.cameraChanged.emit(self.yaw, self.pitch, self.distance)
        self.update()

    def _retarget(self):
        if self.room:
            self._target = [0.0, max(0.8, float(self.room.height) * 0.38), 0.0]
        else:
            self._target = [0.0, 1.2, 0.0]

    def _min_orbit_distance(self) -> float:
        if not self.room:
            return 6.0
        w, d, h = float(self.room.width), float(self.room.depth), float(self.room.height)
        half = 0.5 * math.sqrt(w * w + d * d + h * h)
        return max(6.0, half + 2.5)

    def _max_orbit_distance(self) -> float:
        if not self.room:
            return 80.0
        t = float(getattr(self.room, "terrain_size", 40.0) or 40.0)
        return max(40.0, t * 1.2)

    def _clamp_camera(self):
        self.pitch = max(0.12, min(1.15, float(self.pitch)))
        self.distance = max(
            self._min_orbit_distance(),
            min(self._max_orbit_distance(), float(self.distance)),
        )

    def _cam_eye(self):
        self._clamp_camera()
        tx, ty, tz = self._target
        cx = tx + self.distance * math.sin(self.yaw) * math.cos(self.pitch)
        cy = ty + self.distance * math.sin(self.pitch)
        cz = tz + self.distance * math.cos(self.yaw) * math.cos(self.pitch)
        return cx, max(0.6, cy), cz

    # ---------- GL lifecycle ----------
    def initializeGL(self):
        self._inited = True
        if not _HAS_GL:
            self._gl_ok = False
            self._gl_error = "PyOpenGL not installed"
            self.glStatus.emit(self._gl_error)
            return
        try:
            # Clear any stale error
            while glGetError() != GL_NO_ERROR:
                pass

            glEnable(GL_DEPTH_TEST)
            glDisable(GL_CULL_FACE)
            glClearColor(0.06, 0.08, 0.10, 1.0)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glEnable(GL_LINE_SMOOTH)
            # Unlit is more reliable across drivers — no black geometry
            glDisable(GL_LIGHTING)
            glShadeModel(GL_SMOOTH)

            # Probe fixed-function
            glMatrixMode(GL_MODELVIEW)
            glLoadIdentity()
            glBegin(GL_TRIANGLES)
            glColor3f(1, 1, 1)
            glVertex3f(0, 0, 0)
            glVertex3f(0, 0, 0)
            glVertex3f(0, 0, 0)
            glEnd()
            err = glGetError()
            if err != GL_NO_ERROR:
                self._gl_ok = False
                self._gl_error = (
                    f"Fixed-function GL unavailable (err 0x{err:04X}). "
                    "Driver may be Core-only."
                )
                log.error(self._gl_error)
                self.glStatus.emit(self._gl_error)
                return

            ver = glGetString(GL_VERSION)
            ren = glGetString(GL_RENDERER)
            log.info(
                "OpenGL ready: %s | %s | fmt %s.%s profile=%s",
                ver, ren,
                self.format().majorVersion(),
                self.format().minorVersion(),
                self.format().profile(),
            )
            self._gl_ok = True
            self._gl_error = ""
            self.glStatus.emit("OpenGL OK")
        except Exception as e:
            self._gl_ok = False
            self._gl_error = f"OpenGL init failed: {e}"
            log.exception(self._gl_error)
            self.glStatus.emit(self._gl_error)

    def resizeGL(self, w, h):
        if not _HAS_GL:
            return
        glViewport(0, 0, max(1, int(w)), max(1, int(h)))

    def _apply_perspective(self):
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        aspect = self.width() / max(1.0, float(self.height()))
        near = max(0.2, self.distance * 0.02)
        far = max(250.0, self.distance * 25.0)
        gluPerspective(50.0, aspect, near, far)
        glMatrixMode(GL_MODELVIEW)

    def _apply_cam(self):
        glLoadIdentity()
        cx, cy, cz = self._cam_eye()
        tx, ty, tz = self._target
        gluLookAt(cx, cy, cz, tx, ty, tz, 0, 1, 0)

    def paintGL(self):
        if not _HAS_GL:
            return
        try:
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            if not self._gl_ok:
                self._draw_error_overlay()
                return

            # Sky (2D)
            self._draw_sky()

            self._apply_perspective()
            self._apply_cam()

            if self.room:
                self._draw_terrain()
                self._draw_house()
                self._draw_windows()
                self._draw_speakers()
                self._draw_listener()
                self._draw_rain()
                self._draw_selection_ring()
            else:
                self._draw_empty_hint()
        except Exception as e:
            log.exception("paintGL error: %s", e)
            self._gl_ok = False
            self._gl_error = str(e)

    def _draw_error_overlay(self):
        """2D wash when GL path is broken (text goes via glStatus / status bar)."""
        w, h = max(1, self.width()), max(1, self.height())
        glDisable(GL_DEPTH_TEST)
        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(0, w, 0, h, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()
        glBegin(GL_QUADS)
        glColor3f(0.14, 0.08, 0.08)
        glVertex2f(0, 0)
        glVertex2f(w, 0)
        glVertex2f(w, h)
        glVertex2f(0, h)
        glEnd()
        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glEnable(GL_DEPTH_TEST)

    def _draw_empty_hint(self):
        glDisable(GL_DEPTH_TEST)
        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(0, max(1, self.width()), 0, max(1, self.height()), -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()
        glBegin(GL_QUADS)
        glColor3f(0.08, 0.09, 0.11)
        glVertex2f(0, 0)
        glVertex2f(self.width(), 0)
        glVertex2f(self.width(), self.height())
        glVertex2f(0, self.height())
        glEnd()
        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glEnable(GL_DEPTH_TEST)

    # ---------- Scene ----------
    def _draw_sky(self):
        w, h = max(1, self.width()), max(1, self.height())
        glDisable(GL_DEPTH_TEST)
        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(0, w, 0, h, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()
        glBegin(GL_QUADS)
        glColor3f(0.14, 0.20, 0.28)
        glVertex2f(0, h)
        glVertex2f(w, h)
        glColor3f(0.05, 0.07, 0.10)
        glVertex2f(w, 0)
        glVertex2f(0, 0)
        glEnd()
        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glEnable(GL_DEPTH_TEST)

    def _draw_terrain(self):
        R = max(self.room.width, self.room.depth, 8.0) * 5.0
        # Ground
        glBegin(GL_QUADS)
        glColor3f(0.14, 0.16, 0.14)
        glVertex3f(-R, 0, -R)
        glVertex3f(R, 0, -R)
        glVertex3f(R, 0, R)
        glVertex3f(-R, 0, R)
        glEnd()
        # Grid
        glLineWidth(1.0)
        glBegin(GL_LINES)
        glColor4f(0.22, 0.26, 0.22, 0.55)
        step = 1.0
        i = -R
        while i <= R + 0.01:
            glVertex3f(i, 0.01, -R)
            glVertex3f(i, 0.01, R)
            glVertex3f(-R, 0.01, i)
            glVertex3f(R, 0.01, i)
            i += step
        glEnd()
        # Splashes
        now = time.time()
        glLineWidth(1.5)
        for item in list(self._splashes):
            x, z, t0 = item
            age = now - t0
            if age > 0.55:
                try:
                    self._splashes.remove(item)
                except ValueError:
                    pass
                continue
            rad = 0.05 + age * 0.55
            a = max(0.0, 0.5 * (1.0 - age / 0.55))
            glColor4f(0.7, 0.85, 1.0, a)
            glBegin(GL_LINE_LOOP)
            for k in range(24):
                ang = 2 * math.pi * k / 24
                glVertex3f(x + rad * math.cos(ang), 0.02, z + rad * math.sin(ang))
            glEnd()

    def _draw_house(self):
        w = float(self.room.width)
        h = float(self.room.height)
        d = float(self.room.depth)
        x0, z0 = -w * 0.5, -d * 0.5
        x1, z1 = w * 0.5, d * 0.5

        # Floor (opaque)
        glBegin(GL_QUADS)
        glColor3f(0.22, 0.22, 0.26)
        glVertex3f(x0, 0.0, z0)
        glVertex3f(x1, 0.0, z0)
        glVertex3f(x1, 0.0, z1)
        glVertex3f(x0, 0.0, z1)
        glEnd()

        # Walls — semi-transparent but brighter so they read clearly
        glBegin(GL_QUADS)
        glColor4f(0.42, 0.44, 0.50, 0.55)
        # +Z (north)
        glVertex3f(x0, 0, z1)
        glVertex3f(x1, 0, z1)
        glVertex3f(x1, h, z1)
        glVertex3f(x0, h, z1)
        # -Z (south)
        glVertex3f(x1, 0, z0)
        glVertex3f(x0, 0, z0)
        glVertex3f(x0, h, z0)
        glVertex3f(x1, h, z0)
        # +X (east)
        glVertex3f(x1, 0, z1)
        glVertex3f(x1, 0, z0)
        glVertex3f(x1, h, z0)
        glVertex3f(x1, h, z1)
        # -X (west)
        glVertex3f(x0, 0, z0)
        glVertex3f(x0, 0, z1)
        glVertex3f(x0, h, z1)
        glVertex3f(x0, h, z0)
        glEnd()

        # Wireframe edges
        glLineWidth(2.0)
        glColor4f(0.75, 0.78, 0.88, 0.95)
        glBegin(GL_LINE_LOOP)
        glVertex3f(x0, 0, z0)
        glVertex3f(x1, 0, z0)
        glVertex3f(x1, 0, z1)
        glVertex3f(x0, 0, z1)
        glEnd()
        glBegin(GL_LINE_LOOP)
        glVertex3f(x0, h, z0)
        glVertex3f(x1, h, z0)
        glVertex3f(x1, h, z1)
        glVertex3f(x0, h, z1)
        glEnd()
        glBegin(GL_LINES)
        for x, z in ((x0, z0), (x1, z0), (x1, z1), (x0, z1)):
            glVertex3f(x, 0, z)
            glVertex3f(x, h, z)
        glEnd()

        # Simple flat roof
        glBegin(GL_QUADS)
        glColor4f(0.35, 0.30, 0.28, 0.85)
        glVertex3f(x0 - 0.08, h, z0 - 0.08)
        glVertex3f(x1 + 0.08, h, z0 - 0.08)
        glVertex3f(x1 + 0.08, h, z1 + 0.08)
        glVertex3f(x0 - 0.08, h, z1 + 0.08)
        glEnd()

    def _draw_windows(self):
        if not self.room:
            return
        w = float(self.room.width)
        d = float(self.room.depth)
        for win in self.room.windows:
            if hasattr(self.room, "sync_window_coords"):
                try:
                    self.room.sync_window_coords(win)
                except Exception:
                    pass
            wall = str(getattr(win, "wall", "") or "").lower()
            if not wall:
                if getattr(win, "z", 0) >= d * 0.9:
                    wall = "north"
                elif getattr(win, "z", 0) <= d * 0.1:
                    wall = "south"
                elif getattr(win, "x", 0) >= w * 0.9:
                    wall = "east"
                else:
                    wall = "west"
            sill = float(getattr(win, "sill", 0.9))
            y0, y1 = sill, sill + float(win.height)
            open_a = 0.45 + 0.45 * max(0.0, min(1.0, float(getattr(win, "open", 0.7))))
            glColor4f(0.35, 0.9, 1.0, open_a)
            glBegin(GL_QUADS)
            if wall == "north":
                x0 = float(getattr(win, "offset", win.x)) - w * 0.5
                glVertex3f(x0, y0, d * 0.5 + 0.02)
                glVertex3f(x0 + win.width, y0, d * 0.5 + 0.02)
                glVertex3f(x0 + win.width, y1, d * 0.5 + 0.02)
                glVertex3f(x0, y1, d * 0.5 + 0.02)
            elif wall == "south":
                x0 = float(getattr(win, "offset", win.x)) - w * 0.5
                glVertex3f(x0, y0, -d * 0.5 - 0.02)
                glVertex3f(x0 + win.width, y0, -d * 0.5 - 0.02)
                glVertex3f(x0 + win.width, y1, -d * 0.5 - 0.02)
                glVertex3f(x0, y1, -d * 0.5 - 0.02)
            elif wall == "east":
                z0 = float(getattr(win, "offset", win.z)) - d * 0.5
                glVertex3f(w * 0.5 + 0.02, y0, z0)
                glVertex3f(w * 0.5 + 0.02, y0, z0 + win.width)
                glVertex3f(w * 0.5 + 0.02, y1, z0 + win.width)
                glVertex3f(w * 0.5 + 0.02, y1, z0)
            else:
                z0 = float(getattr(win, "offset", win.z)) - d * 0.5
                glVertex3f(-w * 0.5 - 0.02, y0, z0)
                glVertex3f(-w * 0.5 - 0.02, y0, z0 + win.width)
                glVertex3f(-w * 0.5 - 0.02, y1, z0 + win.width)
                glVertex3f(-w * 0.5 - 0.02, y1, z0)
            glEnd()
            # Frame
            glLineWidth(2.0)
            glColor4f(0.6, 0.95, 1.0, 0.95)
            glBegin(GL_LINE_LOOP)
            if wall == "north":
                x0 = float(getattr(win, "offset", win.x)) - w * 0.5
                glVertex3f(x0, y0, d * 0.5 + 0.03)
                glVertex3f(x0 + win.width, y0, d * 0.5 + 0.03)
                glVertex3f(x0 + win.width, y1, d * 0.5 + 0.03)
                glVertex3f(x0, y1, d * 0.5 + 0.03)
            elif wall == "south":
                x0 = float(getattr(win, "offset", win.x)) - w * 0.5
                glVertex3f(x0, y0, -d * 0.5 - 0.03)
                glVertex3f(x0 + win.width, y0, -d * 0.5 - 0.03)
                glVertex3f(x0 + win.width, y1, -d * 0.5 - 0.03)
                glVertex3f(x0, y1, -d * 0.5 - 0.03)
            elif wall == "east":
                z0 = float(getattr(win, "offset", win.z)) - d * 0.5
                glVertex3f(w * 0.5 + 0.03, y0, z0)
                glVertex3f(w * 0.5 + 0.03, y0, z0 + win.width)
                glVertex3f(w * 0.5 + 0.03, y1, z0 + win.width)
                glVertex3f(w * 0.5 + 0.03, y1, z0)
            else:
                z0 = float(getattr(win, "offset", win.z)) - d * 0.5
                glVertex3f(-w * 0.5 - 0.03, y0, z0)
                glVertex3f(-w * 0.5 - 0.03, y0, z0 + win.width)
                glVertex3f(-w * 0.5 - 0.03, y1, z0 + win.width)
                glVertex3f(-w * 0.5 - 0.03, y1, z0)
            glEnd()

    def _draw_speakers(self):
        if not self.room:
            return
        for s in self.room.speakers:
            if not getattr(s, "enabled", True):
                continue
            half = max(0.06, float(getattr(s, "size", 0.32)) * 0.5)
            self._draw_cube(
                s.x - self.room.width * 0.5,
                float(getattr(s, "y", 1.1)),
                s.z - self.room.depth * 0.5,
                half,
                (0.92, 0.72, 0.18),
            )

    def _draw_listener(self):
        if not self.room:
            return
        L = self.room.listener
        x = float(L.x) - self.room.width * 0.5
        y = float(getattr(L, "y", 1.2))
        z = float(L.z) - self.room.depth * 0.5
        self._draw_sphere(x, y, z, 0.16, (0.25, 0.75, 1.0))
        # Facing marker
        yaw = float(getattr(L, "yaw", 0.0))
        fx = math.sin(yaw) * 0.45
        fz = math.cos(yaw) * 0.45
        glLineWidth(2.5)
        glBegin(GL_LINES)
        glColor3f(0.4, 0.85, 1.0)
        glVertex3f(x, y, z)
        glVertex3f(x + fx, y, z + fz)
        glEnd()
        # Also headphones markers
        for hp in getattr(self.room, "headphones_items", []) or []:
            self._draw_sphere(
                hp.x - self.room.width * 0.5,
                float(getattr(hp, "y", 1.5)),
                hp.z - self.room.depth * 0.5,
                0.12,
                (0.3, 0.65, 0.95),
            )

    def _draw_cube(self, x, y, z, r, rgb):
        glColor3f(*rgb)
        glBegin(GL_QUADS)
        # faces
        for n, verts in (
            ((0, 0, -1), ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1))),
            ((0, 0, 1), ((-1, -1, 1), (-1, 1, 1), (1, 1, 1), (1, -1, 1))),
            ((0, -1, 0), ((-1, -1, -1), (-1, -1, 1), (1, -1, 1), (1, -1, -1))),
            ((0, 1, 0), ((-1, 1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1))),
            ((-1, 0, 0), ((-1, -1, -1), (-1, 1, -1), (-1, 1, 1), (-1, -1, 1))),
            ((1, 0, 0), ((1, -1, 1), (1, 1, 1), (1, 1, -1), (1, -1, -1))),
        ):
            for vx, vy, vz in verts:
                glVertex3f(x + vx * r, y + vy * r, z + vz * r)
        glEnd()

    def _draw_sphere(self, x, y, z, r, rgb):
        glColor3f(*rgb)
        stacks, slices = 8, 12
        for i in range(stacks):
            lat0 = math.pi * (-0.5 + i / stacks)
            lat1 = math.pi * (-0.5 + (i + 1) / stacks)
            z0, zr0 = math.sin(lat0), math.cos(lat0)
            z1, zr1 = math.sin(lat1), math.cos(lat1)
            glBegin(GL_QUAD_STRIP)
            for j in range(slices + 1):
                lng = 2 * math.pi * j / slices
                cx, cy = math.cos(lng), math.sin(lng)
                glVertex3f(x + r * cx * zr0, y + r * cy * zr0, z + r * z0)
                glVertex3f(x + r * cx * zr1, y + r * cy * zr1, z + r * z1)
            glEnd()

    def _wind_push(self):
        if not self.room:
            return 0.0, 0.0
        if hasattr(self.room, "base_wind_push_xz"):
            try:
                # Prefer live engine eff if present on room — else base
                spd = float(getattr(self.room, "wind_speed", 0.0))
                deg = float(getattr(self.room, "wind_direction_deg", 90.0))
                from app.models.room import Room
                return Room.wind_push_xz(spd, deg)
            except Exception:
                pass
        w = float(getattr(self.room, "wind", 0.0))
        return w, 0.0

    def _draw_rain(self):
        n = int(self._rain.shape[0])
        if n <= 0:
            return
        streak = 0.16
        verts = self._rain_verts
        if verts.shape[0] != n * 2:
            verts = np.zeros((n * 2, 3), dtype=np.float32)
            self._rain_verts = verts
        r = self._rain
        verts[0::2, 0] = r[:, 0]
        verts[0::2, 1] = r[:, 1]
        verts[0::2, 2] = r[:, 2]
        verts[1::2, 0] = r[:, 0]
        verts[1::2, 1] = r[:, 1] - streak
        verts[1::2, 2] = r[:, 2]
        glLineWidth(1.2)
        glColor4f(0.75, 0.88, 1.0, 0.42)
        try:
            glEnableClientState(GL_VERTEX_ARRAY)
            glVertexPointer(3, GL_FLOAT, 0, verts)
            glDrawArrays(GL_LINES, 0, n * 2)
            glDisableClientState(GL_VERTEX_ARRAY)
        except Exception:
            # Immediate-mode fallback if the driver rejects client arrays
            glBegin(GL_LINES)
            for i in range(n):
                x, y, z = float(r[i, 0]), float(r[i, 1]), float(r[i, 2])
                glVertex3f(x, y, z)
                glVertex3f(x, y - streak, z)
            glEnd()

    def _draw_selection_ring(self):
        if self._selection == PICK_NONE or not self.room:
            return
        k, i = self._selection
        try:
            if k == "speaker":
                x = self.room.speakers[i].x - self.room.width * 0.5
                z = self.room.speakers[i].z - self.room.depth * 0.5
            elif k == "headphones":
                x = self.room.headphones_items[i].x - self.room.width * 0.5
                z = self.room.headphones_items[i].z - self.room.depth * 0.5
            elif k == "window":
                win = self.room.windows[i]
                x = (win.x + min(self.room.width, win.x + win.width)) * 0.5 - self.room.width * 0.5
                z = win.z - self.room.depth * 0.5
            else:
                return
        except Exception:
            return
        r = 0.28
        glLineWidth(2.5)
        glColor3f(0.55, 0.85, 1.0)
        glBegin(GL_LINE_LOOP)
        for n in range(32):
            a = 2 * math.pi * n / 32
            glVertex3f(x + r * math.cos(a), 0.03, z + r * math.sin(a))
        glEnd()

    # ---------- Rain sim (visual only — audio droplets are independent) ----------
    def _visual_target(self) -> int:
        dens = max(0.0, float(getattr(self.room, "droplet_density", 0.5) or 0.0))
        if dens <= 0.0005:
            return 0
        # Drizzle ~80 streaks, downpour fills the cap — each landing makes a tick.
        n = int(80 + (self._max_particles - 80) * (dens ** 1.12))
        return max(24, min(self._max_particles, n))

    def _spawn_world_rain(self, count: int) -> np.ndarray:
        """Rain over the room footprint plus a thin eaves ring (not the whole terrain)."""
        if not self.room or count <= 0:
            return np.zeros((0, 5), dtype=np.float32)
        w, h, d = float(self.room.width), float(self.room.height), float(self.room.depth)
        half_w, half_d = w * 0.5, d * 0.5
        n = int(count)
        out = np.empty((n, 5), dtype=np.float32)
        u = np.random.random(n)
        roof_mat = str(getattr(self.room, "roof_material", "") or "").lower()
        tin_roof = ("tin" in roof_mat) or ("metal" in roof_mat)
        # Quantity is for the house, not a 40 m field. A little overshoot = gutters / drip line.
        p_roof = 0.88 if tin_roof else 0.78
        dens = max(0.0, min(1.0, float(getattr(self.room, "droplet_density", 0.5) or 0.0)))
        y_span = 3.8 - 1.1 * dens
        eaves = 0.85
        for i in range(n):
            if u[i] < p_roof:
                x = float(np.random.uniform(-half_w, half_w))
                z = float(np.random.uniform(-half_d, half_d))
                y0 = h + float(np.random.uniform(0.35, max(0.8, y_span)))
                kind = 3.0
            else:
                dist = float(np.random.uniform(0.12, eaves))
                per = 2.0 * ((w + 2.0 * dist) + (d + 2.0 * dist))
                t = float(np.random.uniform(0.0, per))
                pw = w + 2.0 * dist
                pd = d + 2.0 * dist
                if t < pw:
                    x, z = t - dist - half_w, half_d + dist
                else:
                    t -= pw
                    if t < pd:
                        x, z = half_w + dist, t - dist - half_d
                    else:
                        t -= pd
                        if t < pw:
                            x, z = half_w + dist - t, -half_d - dist
                        else:
                            t -= pw
                            x, z = -half_w - dist, half_d + dist - t
                y0 = float(np.random.uniform(h * 0.35, h + y_span))
                kind = 1.0
            vy = float(np.random.uniform(-(3.2 + 1.6 * dens), -(5.4 + 2.4 * dens)))
            out[i] = (x, y0, z, vy, kind)
        return out

    def _spawn_window_ingress(self) -> np.ndarray:
        if not self.room:
            return np.zeros((0, 5), dtype=np.float32)
        w, d = float(self.room.width), float(self.room.depth)
        rows = []
        for win in self.room.windows:
            if float(getattr(win, "open", 0.0)) <= 0.05:
                continue
            try:
                if hasattr(self.room, "window_exterior_point"):
                    ex, ey, ez = self.room.window_exterior_point(win, out_dist=0.25)
                    cx, cy, cz = ex - w * 0.5, ey, ez - d * 0.5
                else:
                    cx = win.x - w * 0.5
                    cz = win.z - d * 0.5
                    cy = float(getattr(win, "sill", 0.9)) + 0.4
            except Exception:
                continue
            n = max(1, int(3 * float(getattr(win, "open", 0.5))))
            for _ in range(n):
                rows.append((
                    cx + random.uniform(-0.25, 0.25),
                    random.uniform(max(0.2, cy - 0.35), cy + 0.35),
                    cz + random.uniform(-0.25, 0.25),
                    random.uniform(-2.5, -3.5),
                    2.0,
                ))
        if not rows:
            return np.zeros((0, 5), dtype=np.float32)
        return np.asarray(rows, dtype=np.float32)

    def _init_particles(self):
        self._rain = np.zeros((0, 5), dtype=np.float32)
        self._rain_verts = np.zeros((0, 3), dtype=np.float32)
        if not self.room:
            return
        world = self._spawn_world_rain(self._visual_target())
        win = self._spawn_window_ingress()
        if win.shape[0]:
            self._rain = np.vstack([world, win])
        else:
            self._rain = world

    def _tick(self):
        if not self.room or not self.isVisible():
            return
        dens = max(0.0, float(getattr(self.room, "droplet_density", 0.5) or 0.0))
        if dens <= 0.0005:
            if self._rain.shape[0]:
                self._rain = np.zeros((0, 5), dtype=np.float32)
                self.update()
            return
        wx, wz = self._wind_push()
        wx *= 2.2
        wz *= 2.2
        dt = 0.033
        h = float(self.room.height)
        if self._rain.shape[0] == 0:
            self._init_particles()
        r = self._rain
        if r.shape[0] == 0:
            self.update()
            return
        r[:, 0] += wx * dt
        r[:, 1] += r[:, 3] * dt
        r[:, 2] += wz * dt
        ground = np.where(r[:, 4] == 3.0, h + 0.02, 0.0)
        dead = r[:, 1] <= ground
        n_dead = int(np.count_nonzero(dead))
        if n_dead:
            hits = r[dead]
            if self._on_impacts is not None:
                hw = float(self.room.width) * 0.5
                hd = float(self.room.depth) * 0.5
                payload = []
                for i in range(hits.shape[0]):
                    payload.append((
                        float(hits[i, 0]) + hw,
                        float(hits[i, 1]),
                        float(hits[i, 2]) + hd,
                        int(hits[i, 4]),
                    ))
                try:
                    self._on_impacts(payload)
                except Exception:
                    log.exception("rain impact handler")
            if len(self._splashes) < 40:
                for i in range(min(4, hits.shape[0])):
                    self._splashes.append((float(hits[i, 0]), float(hits[i, 2]), time.time()))
                if len(self._splashes) > 40:
                    self._splashes = self._splashes[-40:]
            r[dead] = self._spawn_world_rain(n_dead)
        target = self._visual_target()
        n = int(r.shape[0])
        if n < target:
            extra = self._spawn_world_rain(target - n)
            r = np.vstack([r, extra]) if extra.shape[0] else r
        elif n > self._max_particles:
            r = r[: self._max_particles]
        if random.random() < 0.06:
            acc = self._spawn_window_ingress()
            if acc.shape[0]:
                r = np.vstack([r, acc])
                if r.shape[0] > self._max_particles:
                    r = r[: self._max_particles]
        self._rain = r
        self.update()

    # ---------- Interaction ----------
    def _screen_ray(self, x, y):
        w, h = max(1, self.width()), max(1, self.height())
        nx = 2.0 * x / w - 1.0
        ny = 1.0 - 2.0 * y / h
        fov = math.radians(50.0)
        tan_v = math.tan(fov * 0.5)
        tan_h = tan_v * (w / max(1.0, h))
        cam = self._cam_eye()
        tx, ty, tz = self._target
        forward = (tx - cam[0], ty - cam[1], tz - cam[2])
        fl = math.sqrt(sum(v * v for v in forward)) + 1e-9
        forward = (forward[0] / fl, forward[1] / fl, forward[2] / fl)
        up0 = (0.0, 1.0, 0.0)
        right = (
            forward[1] * up0[2] - forward[2] * up0[1],
            forward[2] * up0[0] - forward[0] * up0[2],
            forward[0] * up0[1] - forward[1] * up0[0],
        )
        rl = math.sqrt(sum(v * v for v in right)) + 1e-9
        right = (right[0] / rl, right[1] / rl, right[2] / rl)
        up = (
            right[1] * forward[2] - right[2] * forward[1],
            right[2] * forward[0] - right[0] * forward[2],
            right[0] * forward[1] - right[1] * forward[0],
        )
        dir_world = (
            forward[0] + right[0] * nx * tan_h + up[0] * ny * tan_v,
            forward[1] + right[1] * nx * tan_h + up[1] * ny * tan_v,
            forward[2] + right[2] * nx * tan_h + up[2] * ny * tan_v,
        )
        L = math.sqrt(sum(d * d for d in dir_world)) + 1e-6
        return cam, tuple(d / L for d in dir_world)

    def mousePressEvent(self, e):
        self.last = e.position()
        if e.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self._try_select(e.position())
        self.setFocus()

    def mouseMoveEvent(self, e):
        if self.last is None:
            self.last = e.position()
            return
        dx = (e.position().x() - self.last.x()) * 0.008
        dy = (e.position().y() - self.last.y()) * 0.008
        if e.buttons() & (QtCore.Qt.RightButton | QtCore.Qt.MiddleButton):
            self.yaw += dx
            self.pitch -= dy
            self._clamp_camera()
            self.last = e.position()
            self.cameraChanged.emit(self.yaw, self.pitch, self.distance)
            self.update()
            return
        if (e.buttons() & QtCore.Qt.LeftButton) and self._dragging and self._selection != PICK_NONE:
            self._apply_transform_drag(e.position())
            self.last = e.position()
            return
        self.last = e.position()

    def mouseReleaseEvent(self, e):
        self._dragging = False

    def mouseDoubleClickEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.fit_camera()

    def wheelEvent(self, e):
        factor = 0.88 if e.angleDelta().y() > 0 else 1.14
        self.distance *= factor
        self._clamp_camera()
        self.cameraChanged.emit(self.yaw, self.pitch, self.distance)
        self.update()

    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_F or e.key() == QtCore.Qt.Key_Home:
            self.fit_camera()
        if e.key() == QtCore.Qt.Key_W:
            self.set_transform_mode(MODE_MOVE)
        if e.key() == QtCore.Qt.Key_E:
            self.set_transform_mode(MODE_ROT)
        if e.key() == QtCore.Qt.Key_R:
            self.set_transform_mode(MODE_SCALE)

    def _apply_transform_drag(self, pos):
        if not self.room:
            return
        ro, rd = self._screen_ray(pos.x(), pos.y())
        if abs(rd[1]) < 1e-6:
            return
        t = (0.0 - ro[1]) / rd[1]
        hx = ro[0] + rd[0] * t
        hz = ro[2] + rd[2] * t
        kind, idx = self._selection
        x = hx + self.room.width * 0.5
        z = hz + self.room.depth * 0.5
        if kind == "speaker" and 0 <= idx < len(self.room.speakers):
            if self._mode == MODE_MOVE:
                self.room.speakers[idx].x, self.room.speakers[idx].z = x, z
        elif kind == "headphones" and 0 <= idx < len(self.room.headphones_items):
            if self._mode == MODE_MOVE:
                self.room.headphones_items[idx].x = x
                self.room.headphones_items[idx].z = z
        self.update()
        self.requestRepaint.emit()

    def _try_select(self, pos):
        if not self.room:
            self._selection = PICK_NONE
            self.selectionChanged.emit(self._selection)
            return
        ro, rd = self._screen_ray(pos.x(), pos.y())
        if abs(rd[1]) < 1e-6:
            self._selection = PICK_NONE
            self.selectionChanged.emit(self._selection)
            return
        t = (0.0 - ro[1]) / rd[1]
        hx = ro[0] + rd[0] * t
        hz = ro[2] + rd[2] * t
        sel = PICK_NONE
        best = 0.45
        for i, s in enumerate(self.room.speakers):
            sx = s.x - self.room.width * 0.5
            sz = s.z - self.room.depth * 0.5
            dist = math.hypot(hx - sx, hz - sz)
            if dist < best:
                best = dist
                sel = ("speaker", i)
        for i, hp in enumerate(getattr(self.room, "headphones_items", []) or []):
            sx = hp.x - self.room.width * 0.5
            sz = hp.z - self.room.depth * 0.5
            dist = math.hypot(hx - sx, hz - sz)
            if dist < best:
                best = dist
                sel = ("headphones", i)
        self._selection = sel
        self.selectionChanged.emit(sel)
        self.update()
