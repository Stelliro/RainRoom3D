
"""
Reliable software 3D house preview (QPainter).

Qt6/OpenGL core profiles often break fixed-function GL (glBegin / lighting),
which made the old OpenGL view look empty. This view always works.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

Vec3 = Tuple[float, float, float]


# Drag handles on a selected window (screen-space)
_HANDLE_R = 7.0
# Edge hit thickness in pixels
_EDGE_PX = 10.0


class Room3DView(QtWidgets.QWidget):
    """Orbitable perspective preview of the house on terrain.

    Left-click selects; drag window body to move along the wall;
    drag edges / corners to resize (width, height, sill).
    """

    selectionChanged = QtCore.Signal(tuple)
    roomChanged = QtCore.Signal()
    cameraChanged = QtCore.Signal(float, float, float)
    statusMessage = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)

        self.room = None
        self.tool = "select"
        # Yaw: 0 = camera on +Z (north of house, looking south).
        # Default π + ε = south of house looking north → matches floor plan
        # (N far / top of view, E on the right).
        self.yaw = math.pi + 0.45
        self.pitch = 0.48
        self.distance = 14.0
        self._target = [0.0, 1.1, 0.0]
        self._last = None
        self._orbiting = False
        self._selection = ("none", -1)

        # Edit state (windows + speakers + listener)
        # window: body / edges / gizmo_x|y|z
        # speaker: spk_body / spk_gizmo_* / spk_w / spk_h / spk_d
        # listener: lis_gizmo_*
        self._drag_mode: Optional[str] = None
        self._drag_win_idx: int = -1
        self._drag_spk_idx: int = -1
        self._drag_lis: bool = False
        self._drag_grab: Optional[Tuple[float, float]] = None  # wall-space grab (along, y)
        self._drag_orig: Optional[dict] = None  # original geom
        self._hover_handle: Optional[str] = None
        self._drag_screen0: Optional[Tuple[float, float]] = None
        self._gizmo_len = 0.55  # meters

        # Subtle rain animation
        self._t = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    # ----- public -----
    def set_room(self, room):
        first = self.room is None
        self.room = room
        if first:
            self.fit_camera()
        else:
            self._clamp()
            self._retarget()
        self.update()

    def fit_camera(self):
        # South-side view looking north — same compass as the top-down plan
        self.yaw = math.pi + 0.45
        self.pitch = 0.48
        if not self.room:
            self.distance = 14.0
            self._target = [0.0, 1.1, 0.0]
        else:
            w = max(2.0, float(self.room.width))
            d = max(2.0, float(self.room.depth))
            h = max(2.0, float(self.room.height))
            diag = math.sqrt(w * w + d * d)
            self.distance = max(11.0, diag * 1.75 + h * 1.0)
            self._target = [0.0, h * 0.35, 0.0]
        self._clamp()
        self.cameraChanged.emit(self.yaw, self.pitch, self.distance)
        self.update()

    def set_selection(self, kind: str, idx: int, emit: bool = True):
        sel = (kind, idx)
        if sel == self._selection:
            self.update()
            return
        self._selection = sel
        if emit:
            self.selectionChanged.emit(sel)
        self.update()

    def set_tool(self, tool: str):
        self.tool = tool or "select"
        hints = {
            "select": "Select · gizmo arrows move · edges resize · RMB orbit",
            "speaker": "Click inside the house (floor plane) to place a speaker",
            "window": "Click a wall to place a window",
            "listener": "Click to place You (listening position)",
            "resize": "House resize is on the floor plan",
        }
        self.statusMessage.emit(hints.get(self.tool, f"Tool: {self.tool}"))
        self.update()

    def _retarget(self):
        if self.room:
            self._target = [0.0, max(0.7, float(self.room.height) * 0.35), 0.0]

    def _min_dist(self) -> float:
        if not self.room:
            return 7.0
        w, d, h = float(self.room.width), float(self.room.depth), float(self.room.height)
        return max(7.0, 0.55 * math.sqrt(w * w + d * d + h * h) + 3.0)

    def _max_dist(self) -> float:
        if not self.room:
            return 80.0
        t = float(getattr(self.room, "terrain_size", 40.0) or 40.0)
        return max(50.0, t * 1.3)

    def _clamp(self):
        self.pitch = max(0.18, min(1.2, float(self.pitch)))
        self.distance = max(self._min_dist(), min(self._max_dist(), float(self.distance)))

    def _eye(self) -> Vec3:
        self._clamp()
        tx, ty, tz = self._target
        cx = tx + self.distance * math.sin(self.yaw) * math.cos(self.pitch)
        cy = ty + self.distance * math.sin(self.pitch)
        cz = tz + self.distance * math.cos(self.yaw) * math.cos(self.pitch)
        return (cx, max(1.0, cy), cz)

    # ----- projection -----
    def _world_from_room(self, x: float, y: float, z: float) -> Vec3:
        """Room coords (origin SW corner) → GL-style centred world."""
        if not self.room:
            return (x, y, z)
        return (x - self.room.width * 0.5, y, z - self.room.depth * 0.5)

    def _project(self, p: Vec3) -> Optional[QtCore.QPointF]:
        """Perspective project world point → widget pixels."""
        eye = self._eye()
        tx, ty, tz = self._target
        # Camera basis — match floor-plan compass: +X = East (screen-right
        # when looking North), +Z = North. Use right = up × forward so the
        # view is not E/W mirrored relative to the 2D map.
        f = (tx - eye[0], ty - eye[1], tz - eye[2])
        fl = math.sqrt(f[0] ** 2 + f[1] ** 2 + f[2] ** 2) + 1e-9
        f = (f[0] / fl, f[1] / fl, f[2] / fl)
        up_w = (0.0, 1.0, 0.0)
        # right = up × forward  (NOT forward × up — that mirrored East/West)
        r = (
            up_w[1] * f[2] - up_w[2] * f[1],
            up_w[2] * f[0] - up_w[0] * f[2],
            up_w[0] * f[1] - up_w[1] * f[0],
        )
        rl = math.sqrt(r[0] ** 2 + r[1] ** 2 + r[2] ** 2) + 1e-9
        r = (r[0] / rl, r[1] / rl, r[2] / rl)
        # true up = forward × right
        u = (
            f[1] * r[2] - f[2] * r[1],
            f[2] * r[0] - f[0] * r[2],
            f[0] * r[1] - f[1] * r[0],
        )

        # Point relative to eye
        vx, vy, vz = p[0] - eye[0], p[1] - eye[1], p[2] - eye[2]
        # Camera space
        cx = vx * r[0] + vy * r[1] + vz * r[2]
        cy = vx * u[0] + vy * u[1] + vz * u[2]
        cz = vx * f[0] + vy * f[1] + vz * f[2]
        if cz <= 0.2:
            return None  # behind camera

        fov = math.radians(50.0)
        aspect = self.width() / max(1.0, float(self.height()))
        # NDC
        sy = 1.0 / math.tan(fov * 0.5)
        sx = sy / aspect
        ndc_x = (cx * sx) / cz
        ndc_y = (cy * sy) / cz
        # to pixels (y down)
        px = (ndc_x * 0.5 + 0.5) * self.width()
        py = (1.0 - (ndc_y * 0.5 + 0.5)) * self.height()
        return QtCore.QPointF(px, py)

    def _poly(self, pts: List[Vec3]) -> Optional[QtGui.QPolygonF]:
        poly = QtGui.QPolygonF()
        for p in pts:
            q = self._project(p)
            if q is None:
                return None
            poly.append(q)
        return poly

    # ----- paint -----
    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        # Background sky gradient
        grad = QtGui.QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QtGui.QColor("#1a2740"))
        grad.setColorAt(0.55, QtGui.QColor("#0f1724"))
        grad.setColorAt(1.0, QtGui.QColor("#0a1018"))
        p.fillRect(self.rect(), grad)

        if not self.room:
            p.setPen(QtGui.QColor("#94a3b8"))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, "No house loaded")
            return

        w = float(self.room.width)
        d = float(self.room.depth)
        h = float(self.room.height)

        # Terrain disc-ish square
        R = max(w, d) * 3.5
        terr = [
            self._world_from_room(-R + w * 0.5, 0, -R + d * 0.5),
            self._world_from_room(R + w * 0.5, 0, -R + d * 0.5),
            self._world_from_room(R + w * 0.5, 0, R + d * 0.5),
            self._world_from_room(-R + w * 0.5, 0, R + d * 0.5),
        ]
        # Use world already centered: terrain in world space
        terr = [(-R, 0, -R), (R, 0, -R), (R, 0, R), (-R, 0, R)]
        poly = self._poly(terr)
        if poly:
            p.setBrush(QtGui.QColor("#14301f"))
            p.setPen(QtGui.QPen(QtGui.QColor("#1f4a32"), 1))
            p.drawPolygon(poly)

        # Grid on ground
        p.setPen(QtGui.QPen(QtGui.QColor(40, 80, 55, 70), 1))
        step = 1.0
        gmax = max(8.0, max(w, d) * 2.5)
        x = -gmax
        while x <= gmax + 0.01:
            a = self._project((x, 0.01, -gmax))
            b = self._project((x, 0.01, gmax))
            if a and b:
                p.drawLine(a, b)
            a = self._project((-gmax, 0.01, x))
            b = self._project((gmax, 0.01, x))
            if a and b:
                p.drawLine(a, b)
            x += step

        # House box corners (centred)
        x0, z0 = -w * 0.5, -d * 0.5
        x1, z1 = w * 0.5, d * 0.5
        bot = [(x0, 0, z0), (x1, 0, z0), (x1, 0, z1), (x0, 0, z1)]
        top = [(x0, h, z0), (x1, h, z0), (x1, h, z1), (x0, h, z1)]

        # Floor
        poly = self._poly(bot)
        if poly:
            p.setBrush(QtGui.QColor("#2a3344"))
            p.setPen(QtGui.QPen(QtGui.QColor("#5a7aa0"), 2))
            p.drawPolygon(poly)

        # Walls (semi-transparent faces, painter order: far first roughly by camera)
        faces = [
            # -Z south
            ([bot[0], bot[1], top[1], top[0]], QtGui.QColor(70, 85, 110, 160)),
            # +Z north
            ([bot[2], bot[3], top[3], top[2]], QtGui.QColor(80, 100, 130, 150)),
            # +X east
            ([bot[1], bot[2], top[2], top[1]], QtGui.QColor(65, 80, 105, 150)),
            # -X west
            ([bot[3], bot[0], top[0], top[3]], QtGui.QColor(75, 90, 115, 150)),
        ]
        # Sort by average depth (farther first)
        eye = self._eye()

        def face_depth(face_pts):
            ax = sum(pt[0] for pt in face_pts) / 4
            ay = sum(pt[1] for pt in face_pts) / 4
            az = sum(pt[2] for pt in face_pts) / 4
            return (ax - eye[0]) ** 2 + (ay - eye[1]) ** 2 + (az - eye[2]) ** 2

        for pts, col in sorted(faces, key=lambda fc: -face_depth(fc[0])):
            poly = self._poly(pts)
            if poly:
                p.setBrush(col)
                p.setPen(QtGui.QPen(QtGui.QColor("#8aa4c4"), 1.5))
                p.drawPolygon(poly)

        # Roof outline
        poly = self._poly(top)
        if poly:
            p.setBrush(QtGui.QColor(50, 58, 72, 100))
            p.setPen(QtGui.QPen(QtGui.QColor("#9bb4d0"), 2))
            p.drawPolygon(poly)

        # Vertical edges
        p.setPen(QtGui.QPen(QtGui.QColor("#c5d4e8"), 2))
        for i in range(4):
            a = self._project(bot[i])
            b = self._project(top[i])
            if a and b:
                p.drawLine(a, b)

        # Windows (with selection handles)
        for i, win in enumerate(self.room.windows):
            self._draw_window(p, win, w, d, h, selected=self._selection == ("window", i), win_idx=i)

        # Speakers (box + handles when selected)
        for i, s in enumerate(self.room.speakers):
            self._draw_speaker(p, s, selected=self._selection == ("speaker", i), spk_idx=i)

        # Listener
        L = self.room.listener
        self._draw_marker(
            p,
            self._world_from_room(L.x, L.y, L.z),
            QtGui.QColor("#38bdf8"),
            "You",
            selected=self._selection == ("listener", 0),
            radius=8,
        )

        # Rain streaks (simple, outdoor)
        self._draw_rain(p, w, d, h)

        # Compass (matches floor plan: N = +Z, E = +X)
        self._draw_compass(p)

        # HUD
        p.setPen(QtGui.QColor("#94a3b8"))
        p.drawText(
            12,
            self.height() - 14,
            "LMB: select/edit · tools place · RGB arrows move · edges resize · RMB: orbit · F: fit",
        )
        p.setPen(QtGui.QColor("#e2e8f0"))
        p.drawText(12, 22, f"{getattr(self.room, 'name', 'House')}  ·  {w:.1f}×{d:.1f}×{h:.1f} m")

    def _draw_compass(self, p: QtGui.QPainter):
        """Screen-space compass using the same axes as the floor plan."""
        origin = (0.0, 0.05, 0.0)
        north = (0.0, 0.05, 1.6)
        east = (1.6, 0.05, 0.0)
        o = self._project(origin)
        n = self._project(north)
        e = self._project(east)
        cx = self.width() - 52
        cy = 52
        if o and n and e:
            ndx, ndy = n.x() - o.x(), n.y() - o.y()
            edx, edy = e.x() - o.x(), e.y() - o.y()
            nl = math.hypot(ndx, ndy) or 1.0
            el = math.hypot(edx, edy) or 1.0
            ndx, ndy = ndx / nl * 22, ndy / nl * 22
            edx, edy = edx / el * 18, edy / el * 18
        else:
            ndx, ndy = 0.0, -22.0
            edx, edy = 18.0, 0.0

        p.setBrush(QtGui.QColor(15, 25, 40, 180))
        p.setPen(QtGui.QPen(QtGui.QColor("#4a9eff"), 1))
        p.drawEllipse(QtCore.QPointF(cx, cy), 28, 28)
        p.setPen(QtGui.QPen(QtGui.QColor("#64748b"), 2))
        p.drawLine(QtCore.QPointF(cx, cy), QtCore.QPointF(cx + edx, cy + edy))
        p.setPen(QtGui.QColor("#94a3b8"))
        p.drawText(int(cx + edx * 1.15 - 4), int(cy + edy * 1.15 + 4), "E")
        p.setPen(QtGui.QPen(QtGui.QColor("#4a9eff"), 3))
        p.drawLine(QtCore.QPointF(cx, cy), QtCore.QPointF(cx + ndx, cy + ndy))
        p.setPen(QtGui.QColor("#7dd3fc"))
        font = p.font()
        font.setBold(True)
        p.setFont(font)
        p.drawText(int(cx + ndx * 1.2 - 5), int(cy + ndy * 1.2 + 4), "N")

    # ----- window geometry helpers (room space) -----
    def _window_corners_room(self, win) -> List[Tuple[float, float, float]]:
        """Four corners of the glass in room coords: BL, BR, TR, TL."""
        wall = (getattr(win, "wall", "north") or "north").lower()
        ww = float(getattr(win, "width", 1.0))
        wh = float(getattr(win, "height", 1.2))
        rw = float(self.room.width)
        rd = float(self.room.depth)

        if getattr(win, "free_place", False):
            cx = float(getattr(win, "free_x", 0.0))
            cy = float(getattr(win, "free_y", 1.2))
            cz = float(getattr(win, "free_z", 0.0))
            y0, y1 = cy - 0.5 * wh, cy + 0.5 * wh
            hw = 0.5 * ww
            if wall == "north":
                return [(cx - hw, y0, cz), (cx + hw, y0, cz), (cx + hw, y1, cz), (cx - hw, y1, cz)]
            if wall == "south":
                return [(cx - hw, y0, cz), (cx + hw, y0, cz), (cx + hw, y1, cz), (cx - hw, y1, cz)]
            if wall == "east":
                return [(cx, y0, cz - hw), (cx, y0, cz + hw), (cx, y1, cz + hw), (cx, y1, cz - hw)]
            return [(cx, y0, cz - hw), (cx, y0, cz + hw), (cx, y1, cz + hw), (cx, y1, cz - hw)]

        sill = float(getattr(win, "sill", 0.9))
        off = float(getattr(win, "offset", 0.5))
        y0, y1 = sill, sill + wh
        if wall == "north":
            return [(off, y0, rd), (off + ww, y0, rd), (off + ww, y1, rd), (off, y1, rd)]
        if wall == "south":
            return [(off, y0, 0.0), (off + ww, y0, 0.0), (off + ww, y1, 0.0), (off, y1, 0.0)]
        if wall == "east":
            return [(rw, y0, off), (rw, y0, off + ww), (rw, y1, off + ww), (rw, y1, off)]
        return [(0.0, y0, off), (0.0, y0, off + ww), (0.0, y1, off + ww), (0.0, y1, off)]

    def _window_screen_quad(self, win) -> Optional[List[QtCore.QPointF]]:
        corners = self._window_corners_room(win)
        pts = []
        for c in corners:
            q = self._project(self._world_from_room(*c))
            if q is None:
                return None
            pts.append(q)
        return pts

    def _draw_window(self, p: QtGui.QPainter, win, rw, rd, rh, selected=False, win_idx=-1):
        o = max(0.0, min(1.0, float(getattr(win, "open", 0.7))))
        frame = self._window_screen_quad(win)
        if not frame:
            return

        # Frame (closed glass outline) — dimmer when open
        frame_a = int(50 + 40 * (1.0 - o))
        p.setBrush(QtGui.QColor(40, 70, 90, frame_a))
        p.setPen(QtGui.QPen(QtGui.QColor("#5eead4" if not selected else "#f0fdfa"), 2 if not selected else 3))
        p.drawPolygon(QtGui.QPolygonF(frame))

        # Open sash / leaf geometry (shows hinge & motion)
        sash_world = self._window_open_sash_corners(win)
        if sash_world and o > 0.02:
            sash_pts = []
            ok = True
            for c in sash_world:
                q = self._project(self._world_from_room(*c))
                if q is None:
                    ok = False
                    break
                sash_pts.append(q)
            if ok:
                p.setBrush(QtGui.QColor(100, 230, 245, int(120 + 100 * o)))
                p.setPen(QtGui.QPen(QtGui.QColor("#67e8f9"), 2.5))
                p.drawPolygon(QtGui.QPolygonF(sash_pts))
                # Hinge edge highlight
                hinge_i = self._window_hinge_edge_indices(win)
                if hinge_i is not None:
                    i0, i1 = hinge_i
                    p.setPen(QtGui.QPen(QtGui.QColor("#fbbf24"), 4))
                    p.drawLine(sash_pts[i0], sash_pts[i1])
                    mid = QtCore.QPointF(
                        0.5 * (sash_pts[i0].x() + sash_pts[i1].x()),
                        0.5 * (sash_pts[i0].y() + sash_pts[i1].y()),
                    )
                    p.setPen(QtGui.QColor("#fde68a"))
                    p.drawText(mid.toPoint() + QtCore.QPoint(4, -4), "hinge")

        # Opening arrow (from frame into open direction)
        self._draw_window_open_arrow(p, win, frame)

        # Label
        style = getattr(win, "open_style", "") or ""
        draw_style = win.resolved_style_for_draw() if hasattr(win, "resolved_style_for_draw") else style
        hinge = win.resolved_hinge_side() if hasattr(win, "resolved_hinge_side") else getattr(win, "hinge_side", "")
        p.setPen(QtGui.QColor("#ccfbf1"))
        p.drawText(
            frame[3].toPoint() + QtCore.QPoint(4, -4),
            f"{win.name}  {int(o * 100)}%  {style}"
            + (f" → {draw_style}/{hinge}" if style == "custom" else f" · {hinge}"),
        )

        # Resize handles + move gizmo when selected
        if selected:
            handles = self._window_handle_points(frame)
            for name, hp in handles.items():
                hot = self._hover_handle == name or self._drag_mode == name
                p.setBrush(QtGui.QColor("#fbbf24") if hot else QtGui.QColor("#f8fafc"))
                p.setPen(QtGui.QPen(QtGui.QColor("#0f172a"), 1.5))
                p.drawEllipse(hp, _HANDLE_R, _HANDLE_R)
            cx, cy, cz = self.room.window_center(win)
            self._draw_gizmo(p, (cx, cy, cz), prefix="win")
            p.setPen(QtGui.QColor("#94a3b8"))
            p.drawText(
                frame[1].toPoint() + QtCore.QPoint(6, 14),
                "edges=resize · RGB arrows=move (can stick past wall)",
            )

    def _window_hinge_edge_indices(self, win) -> Optional[Tuple[int, int]]:
        """Indices into sash corners (BL,BR,TR,TL) for the hinge edge."""
        style = win.resolved_style_for_draw() if hasattr(win, "resolved_style_for_draw") else win.open_style_norm()
        hinge = win.resolved_hinge_side() if hasattr(win, "resolved_hinge_side") else getattr(win, "hinge_side", "left")
        if style in ("casement", "tilt_turn"):
            return (0, 3) if hinge == "left" else (1, 2)  # left or right vertical edge
        if style == "awning":
            return (3, 2)  # top
        if style == "hopper":
            return (0, 1)  # bottom
        if style == "pivot":
            return (0, 3)  # show left as reference
        return None

    @staticmethod
    def _v_add(a: Vec3, b: Vec3) -> Vec3:
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    @staticmethod
    def _v_sub(a: Vec3, b: Vec3) -> Vec3:
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    @staticmethod
    def _v_scale(a: Vec3, s: float) -> Vec3:
        return (a[0] * s, a[1] * s, a[2] * s)

    @staticmethod
    def _v_len(a: Vec3) -> float:
        return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])

    def _window_outward_unit(self, win) -> Vec3:
        """Unit outward normal for the window's wall (room XZ)."""
        wall = (getattr(win, "wall", "north") or "north").lower()
        if wall == "north":
            return (0.0, 0.0, 1.0)
        if wall == "south":
            return (0.0, 0.0, -1.0)
        if wall == "east":
            return (1.0, 0.0, 0.0)
        return (-1.0, 0.0, 0.0)

    def _window_open_sash_corners(self, win) -> Optional[List[Tuple[float, float, float]]]:
        """Room-space corners of the moving sash when open (BL, BR, TR, TL).

        Rotates around the true hinge edge of the *frame* so the hinge side
        stays attached (previous tilt math pivoted on the wrong edge).
        """
        o = win.open_amount() if hasattr(win, "open_amount") else max(0.0, min(1.0, float(win.open)))
        if o <= 0.02:
            return None
        style = win.resolved_style_for_draw() if hasattr(win, "resolved_style_for_draw") else win.open_style_norm()
        hinge = win.resolved_hinge_side() if hasattr(win, "resolved_hinge_side") else getattr(win, "hinge_side", "left")
        outward = True
        if win.open_style_norm() == "custom":
            outward = bool(getattr(win, "custom_outward", True))
            if (getattr(win, "custom_motion", "") or "").lower() == "fixed":
                return self._window_corners_room(win)

        base = self._window_corners_room(win)
        if not base or len(base) < 4:
            return None
        # Frame corners: BL, BR, TR, TL (always this order)
        BL, BR, TR, TL = base[0], base[1], base[2], base[3]
        ang = math.radians(win.open_angle_deg() if hasattr(win, "open_angle_deg") else o * 70.0)
        ca, sa = math.cos(ang), math.sin(ang)
        n_unit = self._window_outward_unit(win)
        sign = 1.0 if outward else -1.0
        n_unit = self._v_scale(n_unit, sign)

        def swing_vertical(left_hinge: bool) -> List[Vec3]:
            """Casement: rotate around left (BL–TL) or right (BR–TR) edge."""
            if left_hinge:
                # Distance from hinge = how far along BL→BR
                width_v = self._v_sub(BR, BL)  # hinge → free along sill
                # BL' = BL, TL' = TL; BR/TR rotate in plane of width × outward
                wlen = self._v_len(width_v)
                if wlen < 1e-9:
                    return list(base)
                # Rotated sill direction: cos*along + sin*outward
                along_hat = self._v_scale(width_v, 1.0 / wlen)
                rot = self._v_add(
                    self._v_scale(along_hat, ca * wlen),
                    self._v_scale(n_unit, sa * wlen),
                )
                # Parametric: P(s,t) closed = BL + s*width + t*(TL-BL)
                # open: BL + s*rot + t*(TL-BL)  with s,t in {0,1}
                height_v = self._v_sub(TL, BL)
                return [
                    BL,  # s=0,t=0
                    self._v_add(BL, rot),  # s=1,t=0
                    self._v_add(self._v_add(BL, rot), height_v),  # s=1,t=1
                    TL,  # s=0,t=1
                ]
            # Right hinge: BR–TR fixed
            width_v = self._v_sub(BL, BR)  # from right hinge toward left free edge
            wlen = self._v_len(width_v)
            if wlen < 1e-9:
                return list(base)
            along_hat = self._v_scale(width_v, 1.0 / wlen)
            rot = self._v_add(
                self._v_scale(along_hat, ca * wlen),
                self._v_scale(n_unit, sa * wlen),
            )
            height_v = self._v_sub(TR, BR)
            return [
                self._v_add(BR, rot),  # BL'
                BR,  # BR fixed
                TR,  # TR fixed
                self._v_add(self._v_add(BR, rot), height_v),  # TL'
            ]

        def tilt_top_hinge() -> List[Vec3]:
            """Awning: top edge (TL–TR) fixed; bottom swings out."""
            # down from head to sill (left): BL - TL
            down_l = self._v_sub(BL, TL)
            down_r = self._v_sub(BR, TR)
            dlen = self._v_len(down_l)
            if dlen < 1e-9:
                return list(base)
            # Rotate "down" vector around top hinge: cos*down + sin*outward*|down|
            def rot_down(down: Vec3) -> Vec3:
                L = self._v_len(down)
                if L < 1e-9:
                    return down
                return self._v_add(
                    self._v_scale(down, ca),
                    self._v_scale(n_unit, sa * L),
                )

            rd_l = rot_down(down_l)
            rd_r = rot_down(down_r)
            # Head fixed
            return [
                self._v_add(TL, rd_l),  # BL'
                self._v_add(TR, rd_r),  # BR'
                TR,  # TR fixed
                TL,  # TL fixed
            ]

        def tilt_bot_hinge() -> List[Vec3]:
            """Hopper: bottom edge (BL–BR) fixed; head tips out/in."""
            up_l = self._v_sub(TL, BL)
            up_r = self._v_sub(TR, BR)

            def rot_up(up: Vec3) -> Vec3:
                L = self._v_len(up)
                if L < 1e-9:
                    return up
                return self._v_add(
                    self._v_scale(up, ca),
                    self._v_scale(n_unit, sa * L),
                )

            ru_l = rot_up(up_l)
            ru_r = rot_up(up_r)
            return [
                BL,  # fixed
                BR,  # fixed
                self._v_add(BR, ru_r),  # TR'
                self._v_add(BL, ru_l),  # TL'
            ]

        def slide_h() -> List[Vec3]:
            width_v = self._v_sub(BR, BL)
            slide = self._v_scale(width_v, o * 0.85)
            if hinge == "right":
                # leaf shifts toward right (opening grows on left)
                return [
                    self._v_add(BL, slide),
                    BR,
                    TR,
                    self._v_add(TL, slide),
                ]
            # leaf shifts toward left (opening on right)
            return [
                BL,
                self._v_sub(BR, slide),
                self._v_sub(TR, slide),
                TL,
            ]

        def slide_v() -> List[Vec3]:
            height_v = self._v_sub(TL, BL)
            rise = self._v_scale(height_v, o * 0.85)
            return [
                self._v_add(BL, rise),
                self._v_add(BR, rise),
                TR,
                TL,
            ]

        def pivot_c() -> List[Vec3]:
            # Vertical pivot at horizontal centre
            mid_b = self._v_scale(self._v_add(BL, BR), 0.5)
            mid_t = self._v_scale(self._v_add(TL, TR), 0.5)
            half = self._v_sub(BR, mid_b)
            hlen = self._v_len(half)
            if hlen < 1e-9:
                return list(base)
            hat = self._v_scale(half, 1.0 / hlen)
            # left free = -half direction, right free = +half
            left_rot = self._v_add(
                self._v_scale(hat, -ca * hlen),
                self._v_scale(n_unit, -sa * hlen),
            )
            right_rot = self._v_add(
                self._v_scale(hat, ca * hlen),
                self._v_scale(n_unit, sa * hlen),
            )
            height_v = self._v_sub(mid_t, mid_b)
            return [
                self._v_add(mid_b, left_rot),
                self._v_add(mid_b, right_rot),
                self._v_add(self._v_add(mid_b, right_rot), height_v),
                self._v_add(self._v_add(mid_b, left_rot), height_v),
            ]

        if style in ("casement", "tilt_turn"):
            if style == "tilt_turn" and o < 0.45:
                return tilt_bot_hinge()
            return swing_vertical(left_hinge=(hinge != "right"))
        if style == "awning":
            return tilt_top_hinge()
        if style == "hopper":
            return tilt_bot_hinge()
        if style == "slider":
            return slide_h()
        if style == "sash":
            return slide_v()
        if style == "pivot":
            return pivot_c()
        return swing_vertical(True)

    def _draw_window_open_arrow(self, p: QtGui.QPainter, win, frame: List[QtCore.QPointF]):
        o = win.open_amount() if hasattr(win, "open_amount") else float(win.open)
        if o < 0.08:
            return
        # Center of frame → along outward approx using open sash mid
        sash = self._window_open_sash_corners(win)
        if not sash:
            return
        # mid of free edge vs frame mid
        fc = QtCore.QPointF(
            sum(q.x() for q in frame) / 4.0,
            sum(q.y() for q in frame) / 4.0,
        )
        sc_pts = []
        for c in sash:
            q = self._project(self._world_from_room(*c))
            if q:
                sc_pts.append(q)
        if len(sc_pts) < 4:
            return
        sc = QtCore.QPointF(
            sum(q.x() for q in sc_pts) / 4.0,
            sum(q.y() for q in sc_pts) / 4.0,
        )
        p.setPen(QtGui.QPen(QtGui.QColor("#fbbf24"), 2))
        p.drawLine(fc, sc)
        # arrow head
        dx, dy = sc.x() - fc.x(), sc.y() - fc.y()
        L = math.hypot(dx, dy) or 1.0
        ux, uy = dx / L, dy / L
        px, py = -uy, ux
        tip = sc
        left = QtCore.QPointF(tip.x() - ux * 10 + px * 5, tip.y() - uy * 10 + py * 5)
        right = QtCore.QPointF(tip.x() - ux * 10 - px * 5, tip.y() - uy * 10 - py * 5)
        p.setBrush(QtGui.QColor("#fbbf24"))
        p.setPen(QtCore.Qt.NoPen)
        p.drawPolygon(QtGui.QPolygonF([tip, left, right]))

    def _spk_dims(self, spk) -> Tuple[float, float, float]:
        if hasattr(spk, "box_dims"):
            return spk.box_dims()
        s = max(0.12, float(getattr(spk, "size", 0.32)))
        return s, s, min(s, 0.22)

    def _draw_speaker(self, p: QtGui.QPainter, spk, selected=False, spk_idx=-1):
        bw, bh, bd = self._spk_dims(spk)
        hw, hh, hd = 0.5 * bw, 0.5 * bh, 0.5 * bd
        cx, cy, cz = float(spk.x), float(spk.y), float(spk.z)
        ordered = []
        for sx_ in (-1, 1):
            for sy_ in (-1, 1):
                for sz_ in (-1, 1):
                    ordered.append((cx + sx_ * hw, cy + sy_ * hh, cz + sz_ * hd))
        opts = []
        for c in ordered:
            q = self._project(self._world_from_room(*c))
            if q is None:
                return
            opts.append(q)
        p.setPen(QtGui.QPen(QtGui.QColor("#fbbf24" if selected else "#d97706"), 2 if selected else 1.5))
        for a, b in (
            (0, 1), (2, 3), (4, 5), (6, 7),
            (0, 2), (1, 3), (4, 6), (5, 7),
            (0, 4), (1, 5), (2, 6), (3, 7),
        ):
            p.drawLine(opts[a], opts[b])
        top = [opts[2], opts[3], opts[7], opts[6]]
        p.setBrush(QtGui.QColor(245, 158, 11, 120 if selected else 80))
        p.setPen(QtGui.QPen(QtGui.QColor("#fcd34d"), 1))
        p.drawPolygon(QtGui.QPolygonF(top))

        mid = self._project(self._world_from_room(cx, cy, cz))
        if mid:
            p.setPen(QtGui.QColor("#fde68a"))
            p.drawText(
                mid.toPoint() + QtCore.QPoint(8, 4),
                f"{spk.name}  W{bw:.2f}×H{bh:.2f}×D{bd:.2f}",
            )

        if selected:
            self._draw_gizmo(p, (cx, cy, cz), prefix="spk")
            # Size handles: right edge (width), top (height), front (depth)
            handles = self._speaker_size_handles(spk)
            for name, hp in handles.items():
                hot = self._hover_handle == name or self._drag_mode == name
                if name == "spk_w":
                    col = QtGui.QColor("#f87171") if hot else QtGui.QColor("#fca5a5")
                elif name == "spk_h":
                    col = QtGui.QColor("#4ade80") if hot else QtGui.QColor("#86efac")
                else:
                    col = QtGui.QColor("#60a5fa") if hot else QtGui.QColor("#93c5fd")
                p.setBrush(col)
                p.setPen(QtGui.QPen(QtGui.QColor("#0f172a"), 1.5))
                p.drawRect(QtCore.QRectF(hp.x() - 5, hp.y() - 5, 10, 10))
            if mid:
                p.setPen(QtGui.QColor("#94a3b8"))
                p.drawText(
                    mid.toPoint() + QtCore.QPoint(8, 18),
                    "arrows=move · red W · green H · blue D  (wide = soundbar range)",
                )

    def _speaker_size_handles(self, spk) -> dict:
        bw, bh, bd = self._spk_dims(spk)
        cx, cy, cz = float(spk.x), float(spk.y), float(spk.z)
        out = {}
        for name, pt in (
            ("spk_w", (cx + 0.5 * bw, cy, cz)),
            ("spk_h", (cx, cy + 0.5 * bh, cz)),
            ("spk_d", (cx, cy, cz + 0.5 * bd)),
        ):
            q = self._project(self._world_from_room(*pt))
            if q:
                out[name] = q
        return out

    def _speaker_handle_points(self, spk) -> dict:
        """All interactive handles for a speaker (gizmo + size)."""
        out = self._gizmo_handle_points(
            (float(spk.x), float(spk.y), float(spk.z)), prefix="spk"
        )
        out.update(self._speaker_size_handles(spk))
        body = self._project(self._world_from_room(spk.x, spk.y, spk.z))
        if body:
            out["spk_body"] = body
        return out

    def _gizmo_handle_points(self, center_room: Tuple[float, float, float], prefix: str) -> dict:
        cx, cy, cz = center_room
        L = self._gizmo_len
        out = {}
        tips = {
            f"{prefix}_gizmo_x": (cx + L, cy, cz),
            f"{prefix}_gizmo_y": (cx, cy + L, cz),
            f"{prefix}_gizmo_z": (cx, cy, cz + L),
        }
        for name, pt in tips.items():
            q = self._project(self._world_from_room(*pt))
            if q:
                out[name] = q
        return out

    def _draw_gizmo(self, p: QtGui.QPainter, center_room: Tuple[float, float, float], prefix: str):
        """RGB move arrows (X=red, Y=green, Z=blue) in room space."""
        cx, cy, cz = center_room
        L = self._gizmo_len
        origin = self._project(self._world_from_room(cx, cy, cz))
        if not origin:
            return
        axes = (
            (f"{prefix}_gizmo_x", (cx + L, cy, cz), QtGui.QColor("#ef4444"), "X"),
            (f"{prefix}_gizmo_y", (cx, cy + L, cz), QtGui.QColor("#22c55e"), "Y"),
            (f"{prefix}_gizmo_z", (cx, cy, cz + L), QtGui.QColor("#3b82f6"), "Z"),
        )
        for name, tip_r, col, lab in axes:
            tip = self._project(self._world_from_room(*tip_r))
            if not tip:
                continue
            hot = self._hover_handle == name or self._drag_mode == name
            pen = QtGui.QPen(col if not hot else QtGui.QColor("#ffffff"), 3.5 if hot else 2.5)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(origin, tip)
            p.setBrush(col if not hot else QtGui.QColor("#ffffff"))
            p.setPen(QtGui.QPen(QtGui.QColor("#0f172a"), 1))
            p.drawEllipse(tip, 7 if hot else 6, 7 if hot else 6)
            p.setPen(col)
            p.drawText(tip.toPoint() + QtCore.QPoint(8, 4), lab)

    def _draw_marker(self, p, world, color, label, selected=False, radius=7):
        q = self._project(world)
        if not q:
            return
        p.setBrush(color)
        p.setPen(QtGui.QPen(QtGui.QColor("#fff"), 2 if selected else 1))
        p.drawEllipse(q, radius + (2 if selected else 0), radius + (2 if selected else 0))
        p.setPen(QtGui.QColor("#e2e8f0"))
        p.drawText(q.toPoint() + QtCore.QPoint(10, 4), label)
        # Listener gizmo when selected
        if selected and label.startswith("You") and self.room:
            L = self.room.listener
            self._draw_gizmo(p, (float(L.x), float(L.y), float(L.z)), prefix="lis")

    def _draw_rain(self, p, rw, rd, rh):
        import random
        rng = random.Random(int(self._t * 10) % 10000)
        p.setPen(QtGui.QPen(QtGui.QColor(160, 200, 255, 90), 1))
        intensity = float(getattr(self.room, "rain_intensity", 0.5))
        n = int(40 + 120 * intensity)
        R = max(rw, rd) * 2.5
        for _ in range(n):
            x = rng.uniform(-R, R)
            z = rng.uniform(-R, R)
            y = rng.uniform(0.5, rh + 3.0)
            a = self._project((x, y, z))
            b = self._project((x, y - 0.35, z))
            if a and b:
                p.drawLine(a, b)

    def _tick(self):
        self._t += 0.033
        if self.room and self.isVisible() and self._drag_mode is None:
            self.update()

    # ----- handles / hit testing -----
    def _window_handle_points(self, quad: List[QtCore.QPointF]) -> dict:
        """BL=0 BR=1 TR=2 TL=3 → edge mids + corners."""
        bl, br, tr, tl = quad
        def mid(a, b):
            return QtCore.QPointF(0.5 * (a.x() + b.x()), 0.5 * (a.y() + b.y()))
        return {
            "bl": bl, "br": br, "tr": tr, "tl": tl,
            "b": mid(bl, br), "r": mid(br, tr), "t": mid(tr, tl), "l": mid(tl, bl),
        }

    @staticmethod
    def _dist_point_seg(px, py, ax, ay, bx, by) -> float:
        abx, aby = bx - ax, by - ay
        apx, apy = px - ax, py - ay
        ab2 = abx * abx + aby * aby
        if ab2 < 1e-9:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
        return math.hypot(px - (ax + t * abx), py - (ay + t * aby))

    @staticmethod
    def _point_in_quad(px, py, quad: List[QtCore.QPointF]) -> bool:
        # Ray cast
        n = len(quad)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = quad[i].x(), quad[i].y()
            xj, yj = quad[j].x(), quad[j].y()
            if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi):
                inside = not inside
            j = i
        return inside

    def _hit_window(self, sx: float, sy: float) -> Tuple[int, str]:
        """Return (window_index, handle) or (-1, ""). Prefer handles of selection."""
        if not self.room:
            return -1, ""

        # 1) Handles on currently selected window first
        kind, idx = self._selection
        if kind == "window" and 0 <= idx < len(self.room.windows):
            quad = self._window_screen_quad(self.room.windows[idx])
            if quad:
                handles = self._window_handle_points(quad)
                best_h, best_d = "", 1e9
                for name, hp in handles.items():
                    d = math.hypot(sx - hp.x(), sy - hp.y())
                    if d < best_d:
                        best_d, best_h = d, name
                if best_d <= _HANDLE_R + 4:
                    return idx, best_h
                # edges
                edges = [("b", 0, 1), ("r", 1, 2), ("t", 2, 3), ("l", 3, 0)]
                for ename, i0, i1 in edges:
                    d = self._dist_point_seg(
                        sx, sy, quad[i0].x(), quad[i0].y(), quad[i1].x(), quad[i1].y()
                    )
                    if d <= _EDGE_PX:
                        return idx, ename
                if self._point_in_quad(sx, sy, quad):
                    return idx, "body"

        # 2) Any window body
        best_i, best_area = -1, 1e18
        for i, win in enumerate(self.room.windows):
            quad = self._window_screen_quad(win)
            if not quad:
                continue
            if self._point_in_quad(sx, sy, quad):
                # pick smallest area (front-most-ish)
                xs = [q.x() for q in quad]
                ys = [q.y() for q in quad]
                area = (max(xs) - min(xs)) * (max(ys) - min(ys))
                if area < best_area:
                    best_area, best_i = area, i
        if best_i >= 0:
            return best_i, "body"
        return -1, ""

    def _hit_speaker(self, sx: float, sy: float) -> Tuple[int, str]:
        """Return (speaker_index, handle) or (-1, "")."""
        if not self.room:
            return -1, ""
        # Prefer handles on selected speaker
        kind, idx = self._selection
        if kind == "speaker" and 0 <= idx < len(self.room.speakers):
            handles = self._speaker_handle_points(self.room.speakers[idx])
            best_h, best_d = "", _HANDLE_R + 4
            for name, hp in handles.items():
                d = math.hypot(sx - hp.x(), sy - hp.y())
                if d < best_d:
                    best_d, best_h = d, name
            if best_h:
                return idx, best_h
        best_i, best_d = -1, 18.0
        for i, s in enumerate(self.room.speakers):
            q = self._project(self._world_from_room(s.x, s.y, s.z))
            if not q:
                continue
            d = math.hypot(sx - q.x(), sy - q.y())
            if d < best_d:
                best_d, best_i = d, i
        if best_i >= 0:
            return best_i, "spk_body"
        return -1, ""

    def _intersect_y_plane(self, y_plane: float, sx: float, sy: float) -> Optional[Tuple[float, float, float]]:
        """Raycast to horizontal plane y = y_plane (room coords). Return room (x,y,z)."""
        origin, dir_w = self._ray_from_screen(sx, sy)
        if abs(dir_w[1]) < 1e-8:
            return None
        t = (y_plane - origin[1]) / dir_w[1]
        if t < 0:
            return None
        wx = origin[0] + t * dir_w[0]
        wz = origin[2] + t * dir_w[2]
        # world (centered) → room
        rx = wx + float(self.room.width) * 0.5
        rz = wz + float(self.room.depth) * 0.5
        return rx, y_plane, rz

    def _axis_drag_delta(self, axis: str, sx: float, sy: float, origin_room: Tuple[float, float, float]) -> float:
        """Project mouse movement onto room axis; return signed delta meters from drag start."""
        if self._drag_screen0 is None or not self._drag_orig:
            return 0.0
        ox, oy, oz = origin_room
        if axis == "x":
            h0 = self._intersect_y_plane(oy, self._drag_screen0[0], self._drag_screen0[1])
            h1 = self._intersect_y_plane(oy, sx, sy)
            if h0 is None or h1 is None:
                return 0.0
            return h1[0] - h0[0]
        if axis == "z":
            h0 = self._intersect_y_plane(oy, self._drag_screen0[0], self._drag_screen0[1])
            h1 = self._intersect_y_plane(oy, sx, sy)
            if h0 is None or h1 is None:
                return 0.0
            return h1[2] - h0[2]
        # y: screen vertical (up = +y)
        dy = (self._drag_screen0[1] - sy) * 0.014
        return dy

    def _half_extent_along_axis(
        self, axis: str, sx: float, sy: float, origin_room: Tuple[float, float, float]
    ) -> Optional[float]:
        """Absolute half-size from center to mouse ray along axis (for resize handles)."""
        ox, oy, oz = origin_room
        if axis == "y":
            # Screen-space: distance from center projection vs mouse, scaled
            mid = self._project(self._world_from_room(ox, oy, oz))
            if not mid:
                return None
            # Map vertical pixels to meters (same scale as axis drag)
            return max(0.04, abs(mid.y() - sy) * 0.014)
        hit = self._intersect_y_plane(oy, sx, sy)
        if hit is None:
            return None
        hx, _, hz = hit
        if axis == "x":
            return abs(hx - ox)
        return abs(hz - oz)

    def _apply_speaker_drag(self, sx: float, sy: float):
        if self._drag_spk_idx < 0 or not self._drag_orig:
            return
        spk = self.room.speakers[self._drag_spk_idx]
        mode = self._drag_mode or "spk_body"
        orig = self._drag_orig
        bw0, bh0, bd0 = orig.get("bw", 0.32), orig.get("bh", 0.32), orig.get("bd", 0.2)
        # Ensure all three dims are real fields so shrinking one doesn't pull others via size
        if hasattr(spk, "materialize_dims"):
            spk.materialize_dims()

        if mode == "spk_body":
            hit = self._intersect_y_plane(float(orig.get("y", spk.y)), sx, sy)
            if hit is None:
                return
            x, y, z = hit
            margin = 0.08
            spk.x = max(margin, min(float(self.room.width) - margin, x))
            spk.z = max(margin, min(float(self.room.depth) - margin, z))
            spk.y = float(orig.get("y", spk.y))
            return

        if mode in ("spk_gizmo_x", "spk_gizmo_y", "spk_gizmo_z"):
            axis = mode[-1]
            dlt = self._axis_drag_delta(axis, sx, sy, (orig["x"], orig["y"], orig["z"]))
            if axis == "x":
                spk.x = max(0.08, min(float(self.room.width) - 0.08, orig["x"] + dlt))
            elif axis == "y":
                spk.y = max(0.12, min(float(self.room.height) - 0.08, orig["y"] + dlt))
            else:
                spk.z = max(0.08, min(float(self.room.depth) - 0.08, orig["z"] + dlt))
            return

        # Size handles: set absolute full size = 2 * half-extent from center (grow AND shrink)
        if mode == "spk_w":
            half = self._half_extent_along_axis("x", sx, sy, (orig["x"], orig["y"], orig["z"]))
            if half is None:
                dlt = self._axis_drag_delta("x", sx, sy, (orig["x"], orig["y"], orig["z"]))
                half = max(0.06, 0.5 * bw0 + dlt)
            spk.width = max(0.12, min(2.5, 2.0 * half))
            spk.size = max(spk.width, spk.height, spk.depth)
            return
        if mode == "spk_h":
            half = self._half_extent_along_axis("y", sx, sy, (orig["x"], orig["y"], orig["z"]))
            if half is None:
                dlt = self._axis_drag_delta("y", sx, sy, (orig["x"], orig["y"], orig["z"]))
                half = max(0.04, 0.5 * bh0 + dlt)
            spk.height = max(0.08, min(2.0, 2.0 * half))
            spk.size = max(spk.width, spk.height, spk.depth)
            return
        if mode == "spk_d":
            half = self._half_extent_along_axis("z", sx, sy, (orig["x"], orig["y"], orig["z"]))
            if half is None:
                dlt = self._axis_drag_delta("z", sx, sy, (orig["x"], orig["y"], orig["z"]))
                half = max(0.03, 0.5 * bd0 + dlt)
            spk.depth = max(0.06, min(1.2, 2.0 * half))
            spk.size = max(spk.width, spk.height, spk.depth)
            return
        # legacy aliases
        if mode == "spk_vert":
            dlt = self._axis_drag_delta("y", sx, sy, (orig["x"], orig["y"], orig["z"]))
            spk.y = max(0.12, min(float(self.room.height) - 0.08, orig["y"] + dlt))
            return
        if mode == "spk_size":
            # Old uniform scale: relative to start (can grow and shrink)
            dlt = self._axis_drag_delta("x", sx, sy, (orig["x"], orig["y"], orig["z"]))
            scale = max(0.25, 1.0 + (2.0 * dlt) / max(0.12, bw0))
            spk.width = max(0.12, min(2.5, bw0 * scale))
            spk.height = max(0.08, min(2.0, bh0 * scale))
            spk.depth = max(0.06, min(1.2, bd0 * scale))
            spk.size = max(spk.width, spk.height, spk.depth)
            return

    def _apply_listener_drag(self, sx: float, sy: float):
        if not self._drag_orig or not self.room:
            return
        L = self.room.listener
        mode = self._drag_mode or "lis_gizmo_x"
        orig = self._drag_orig
        if mode == "lis_body":
            hit = self._intersect_y_plane(float(orig.get("y", L.y)), sx, sy)
            if hit is None:
                return
            x, _, z = hit
            L.x, L.z = self.room.clamp_inside(x, z)
            return
        axis = mode[-1] if mode.startswith("lis_gizmo_") else "x"
        dlt = self._axis_drag_delta(axis, sx, sy, (orig["x"], orig["y"], orig["z"]))
        if axis == "x":
            L.x = max(0.15, min(float(self.room.width) - 0.15, orig["x"] + dlt))
        elif axis == "y":
            L.y = max(0.4, min(float(self.room.height) - 0.1, orig["y"] + dlt))
        else:
            L.z = max(0.15, min(float(self.room.depth) - 0.15, orig["z"] + dlt))
        if self.room.headphones_items:
            self.room.headphones_items[0].x = L.x
            self.room.headphones_items[0].y = L.y
            self.room.headphones_items[0].z = L.z

    def _apply_window_gizmo(self, sx: float, sy: float):
        """Move window with RGB gizmo — enables free_place for freer design."""
        if self._drag_win_idx < 0 or not self._drag_orig:
            return
        win = self.room.windows[self._drag_win_idx]
        mode = self._drag_mode or "win_gizmo_x"
        orig = self._drag_orig
        axis = mode[-1]
        dlt = self._axis_drag_delta(
            axis, sx, sy,
            (orig.get("cx", 0.0), orig.get("cy", 1.2), orig.get("cz", 0.0)),
        )
        # Promote to free placement on first gizmo move
        if not getattr(win, "free_place", False):
            cx, cy, cz = self.room.window_center(win)
            win.free_place = True
            win.free_x, win.free_y, win.free_z = cx, cy, cz
            # Keep wall for facing
        if axis == "x":
            win.free_x = orig.get("cx", win.free_x) + dlt
        elif axis == "y":
            win.free_y = orig.get("cy", win.free_y) + dlt
            win.sill = win.free_y - 0.5 * win.height
        else:
            win.free_z = orig.get("cz", win.free_z) + dlt
        self._clamp_window(win)

    def _ray_from_screen(self, sx: float, sy: float):
        """Camera ray (origin, dir) in world space for a screen pixel."""
        eye = self._eye()
        tx, ty, tz = self._target
        f = (tx - eye[0], ty - eye[1], tz - eye[2])
        fl = math.sqrt(f[0] ** 2 + f[1] ** 2 + f[2] ** 2) + 1e-9
        f = (f[0] / fl, f[1] / fl, f[2] / fl)
        up_w = (0.0, 1.0, 0.0)
        r = (
            up_w[1] * f[2] - up_w[2] * f[1],
            up_w[2] * f[0] - up_w[0] * f[2],
            up_w[0] * f[1] - up_w[1] * f[0],
        )
        rl = math.sqrt(r[0] ** 2 + r[1] ** 2 + r[2] ** 2) + 1e-9
        r = (r[0] / rl, r[1] / rl, r[2] / rl)
        u = (
            f[1] * r[2] - f[2] * r[1],
            f[2] * r[0] - f[0] * r[2],
            f[0] * r[1] - f[1] * r[0],
        )
        w, h = max(1, self.width()), max(1, self.height())
        nx = (2.0 * sx / w) - 1.0
        ny = 1.0 - (2.0 * sy / h)
        fov = math.radians(50.0)
        aspect = w / float(h)
        tan_v = math.tan(fov * 0.5)
        tan_h = tan_v * aspect
        dir_w = (
            f[0] + r[0] * nx * tan_h + u[0] * ny * tan_v,
            f[1] + r[1] * nx * tan_h + u[1] * ny * tan_v,
            f[2] + r[2] * nx * tan_h + u[2] * ny * tan_v,
        )
        dl = math.sqrt(dir_w[0] ** 2 + dir_w[1] ** 2 + dir_w[2] ** 2) + 1e-9
        return eye, (dir_w[0] / dl, dir_w[1] / dl, dir_w[2] / dl)

    def _intersect_wall(self, wall: str, sx: float, sy: float) -> Optional[Tuple[float, float]]:
        """Raycast screen → wall plane; return (along_wall, y) in room coords."""
        if not self.room:
            return None
        origin, dir_w = self._ray_from_screen(sx, sy)
        # World-space wall plane (room centred)
        rw, rd = float(self.room.width), float(self.room.depth)
        wall = wall.lower()
        if wall == "north":
            # world z = +rd/2
            z_plane = rd * 0.5
            if abs(dir_w[2]) < 1e-8:
                return None
            t = (z_plane - origin[2]) / dir_w[2]
            if t < 0:
                return None
            wx = origin[0] + t * dir_w[0]
            wy = origin[1] + t * dir_w[1]
            # world x → room x
            along = wx + rw * 0.5
            return along, wy
        if wall == "south":
            z_plane = -rd * 0.5
            if abs(dir_w[2]) < 1e-8:
                return None
            t = (z_plane - origin[2]) / dir_w[2]
            if t < 0:
                return None
            wx = origin[0] + t * dir_w[0]
            wy = origin[1] + t * dir_w[1]
            along = wx + rw * 0.5
            return along, wy
        if wall == "east":
            x_plane = rw * 0.5
            if abs(dir_w[0]) < 1e-8:
                return None
            t = (x_plane - origin[0]) / dir_w[0]
            if t < 0:
                return None
            wz = origin[2] + t * dir_w[2]
            wy = origin[1] + t * dir_w[1]
            along = wz + rd * 0.5
            return along, wy
        # west
        x_plane = -rw * 0.5
        if abs(dir_w[0]) < 1e-8:
            return None
        t = (x_plane - origin[0]) / dir_w[0]
        if t < 0:
            return None
        wz = origin[2] + t * dir_w[2]
        wy = origin[1] + t * dir_w[1]
        along = wz + rd * 0.5
        return along, wy

    def _wall_length(self, wall: str) -> float:
        wall = wall.lower()
        if wall in ("north", "south"):
            return float(self.room.width)
        return float(self.room.depth)

    def _clamp_window(self, win):
        """Soft clamp — allow stick-out past corners / freer free_place."""
        wl = self._wall_length(win.wall)
        win.width = max(0.3, min(float(win.width), wl * 1.35))
        lo = -float(win.width) * 0.45
        hi = wl - float(win.width) * 0.55
        if hi < lo:
            hi = lo
        if not getattr(win, "free_place", False):
            win.offset = max(lo, min(float(win.offset), hi))
        ceil = float(self.room.height)
        win.height = max(0.25, min(float(win.height), ceil + 0.4))
        win.sill = max(-0.2, min(float(win.sill), ceil - 0.15))
        if getattr(win, "free_place", False):
            # Keep free center loosely near the house volume
            win.free_x = max(-1.5, min(float(self.room.width) + 1.5, float(win.free_x)))
            win.free_y = max(0.1, min(ceil + 0.5, float(win.free_y)))
            win.free_z = max(-1.5, min(float(self.room.depth) + 1.5, float(win.free_z)))
        if hasattr(self.room, "sync_window_coords"):
            self.room.sync_window_coords(win)

    def _apply_window_drag(self, sx: float, sy: float):
        """Update window geom from current mouse using drag mode."""
        if self._drag_win_idx < 0 or not self._drag_orig:
            return
        win = self.room.windows[self._drag_win_idx]
        wall = (win.wall or "north").lower()
        hit = self._intersect_wall(wall, sx, sy)
        if hit is None:
            return
        along, y = hit
        orig = self._drag_orig
        mode = self._drag_mode or "body"
        grab = self._drag_grab or (0.0, 0.0)

        # Fixed edges at drag start
        left0 = orig["offset"]
        right0 = orig["offset"] + orig["width"]
        bot0 = orig["sill"]
        top0 = orig["sill"] + orig["height"]

        # Body move: keep grab offset relative to window
        if mode == "body":
            if getattr(win, "free_place", False):
                # Nudge free center along wall plane
                if wall in ("north", "south"):
                    win.free_x = along
                    win.free_z = float(self.room.depth if wall == "north" else 0.0)
                else:
                    win.free_z = along
                    win.free_x = float(self.room.width if wall == "east" else 0.0)
                win.free_y = y
                win.sill = y - 0.5 * win.height
            else:
                new_off = along - grab[0]
                new_sill = y - grab[1]
                win.offset = new_off
                win.sill = new_sill
            self._clamp_window(win)
            return

        # Horizontal component
        if "l" in mode:
            # left edge → set offset, keep right fixed
            new_left = along
            new_right = right0
            if new_right - new_left < 0.3:
                new_left = new_right - 0.3
            win.offset = new_left
            win.width = new_right - new_left
        if "r" in mode:
            new_left = left0
            new_right = along
            if new_right - new_left < 0.3:
                new_right = new_left + 0.3
            win.offset = new_left
            win.width = new_right - new_left

        # Vertical component
        if "b" in mode:
            new_bot = y
            new_top = top0
            if new_top - new_bot < 0.25:
                new_bot = new_top - 0.25
            win.sill = new_bot
            win.height = new_top - new_bot
        if "t" in mode:
            new_bot = bot0
            new_top = y
            if new_top - new_bot < 0.25:
                new_top = new_bot + 0.25
            win.sill = new_bot
            win.height = new_top - new_bot

        self._clamp_window(win)

    def _cursor_for_handle(self, handle: Optional[str]) -> QtCore.Qt.CursorShape:
        if not handle:
            return QtCore.Qt.ArrowCursor
        if handle in ("body", "spk_body", "lis_body") or "gizmo" in str(handle):
            return QtCore.Qt.SizeAllCursor
        if handle in ("spk_h", "spk_vert", "t", "b"):
            return QtCore.Qt.SizeVerCursor
        if handle in ("spk_w", "spk_d", "l", "r"):
            return QtCore.Qt.SizeHorCursor
        if handle in ("spk_size", "tl", "br"):
            return QtCore.Qt.SizeFDiagCursor
        if handle in ("tr", "bl"):
            return QtCore.Qt.SizeBDiagCursor
        return QtCore.Qt.PointingHandCursor

    def _hit_gizmo(self, sx: float, sy: float, center, prefix: str) -> Optional[str]:
        tips = self._gizmo_handle_points(center, prefix)
        best, bd = None, 14.0
        for name, hp in tips.items():
            d = math.hypot(sx - hp.x(), sy - hp.y())
            if d < bd:
                bd, best = d, name
        return best

    def _try_place_at(self, sx: float, sy: float) -> bool:
        """Place speaker/window/You based on current tool. Return True if handled."""
        from app.models.room import Speaker, Window
        tool = getattr(self, "tool", "select")
        if tool == "speaker":
            hit = self._intersect_y_plane(1.1, sx, sy)
            if hit is None:
                self.statusMessage.emit("Aim at the floor inside the house to place a speaker")
                return True
            x, y, z = hit
            if not self.room.contains_point(x, z, margin=0.05):
                self.statusMessage.emit("Place speakers inside the house footprint")
                return True
            n = len(self.room.speakers)
            sp = Speaker(name=f"Speaker {n + 1}", x=x, y=y, z=z, width=0.45, height=0.28, depth=0.18, size=0.32)
            self.room.speakers.append(sp)
            self.set_selection("speaker", n, emit=True)
            self.roomChanged.emit()
            self.statusMessage.emit(f"Placed {sp.name} in 3D")
            return True
        if tool == "window":
            # Prefer wall under ray
            best_wall, best_hit, best_t = None, None, 1e9
            for wall in ("north", "south", "east", "west"):
                hit = self._intersect_wall(wall, sx, sy)
                if hit is None:
                    continue
                along, y = hit
                # approximate depth along ray via y-plane distance
                t = abs(y - 1.2)
                if t < best_t and -0.5 < y < float(self.room.height) + 0.5:
                    best_t, best_wall, best_hit = t, wall, hit
            if best_wall is None or best_hit is None:
                self.statusMessage.emit("Click a house wall to place a window")
                return True
            along, y = best_hit
            n = len(self.room.windows)
            win = Window(
                name=f"Window {n + 1}",
                wall=best_wall,
                offset=along - 0.6,
                width=1.2,
                height=1.15,
                sill=max(0.0, y - 0.55),
                open=0.7,
            )
            self.room.windows.append(win)
            self.room.sync_window_coords(win)
            self.set_selection("window", n, emit=True)
            self.roomChanged.emit()
            self.statusMessage.emit(f"Placed {win.name} on {best_wall}")
            return True
        if tool == "listener":
            hit = self._intersect_y_plane(float(self.room.listener.y), sx, sy)
            if hit is None:
                return True
            x, y, z = hit
            x, z = self.room.clamp_inside(x, z)
            self.room.listener.x, self.room.listener.z = x, z
            if self.room.headphones_items:
                self.room.headphones_items[0].x = x
                self.room.headphones_items[0].z = z
            self.set_selection("listener", 0, emit=True)
            self.roomChanged.emit()
            self.statusMessage.emit("Moved You")
            return True
        return False

    # ----- interaction -----
    def mousePressEvent(self, e: QtGui.QMouseEvent):
        self._last = e.position()
        sx, sy = e.position().x(), e.position().y()

        if e.button() in (QtCore.Qt.RightButton, QtCore.Qt.MiddleButton):
            self._orbiting = True
            self._drag_mode = None
            return

        if e.button() != QtCore.Qt.LeftButton or not self.room:
            return

        # Placement tools first
        if getattr(self, "tool", "select") in ("speaker", "window", "listener"):
            if self._try_place_at(sx, sy):
                self.update()
                return

        # Prefer gizmo of current selection
        kind, idx = self._selection
        if kind == "speaker" and 0 <= idx < len(self.room.speakers):
            spk = self.room.speakers[idx]
            gh = self._hit_gizmo(sx, sy, (spk.x, spk.y, spk.z), "spk")
            if gh:
                self._start_speaker_drag(idx, gh, sx, sy)
                return
            handles = self._speaker_handle_points(spk)
            for name, hp in handles.items():
                if math.hypot(sx - hp.x(), sy - hp.y()) <= 12:
                    self._start_speaker_drag(idx, name, sx, sy)
                    return
        if kind == "window" and 0 <= idx < len(self.room.windows):
            win = self.room.windows[idx]
            cx, cy, cz = self.room.window_center(win)
            gh = self._hit_gizmo(sx, sy, (cx, cy, cz), "win")
            if gh:
                self._start_window_drag(idx, gh, sx, sy)
                return
        if kind == "listener":
            L = self.room.listener
            gh = self._hit_gizmo(sx, sy, (L.x, L.y, L.z), "lis")
            if gh:
                self._start_listener_drag(gh, sx, sy)
                return

        # Window hit
        wi, handle = self._hit_window(sx, sy)
        if wi >= 0:
            self._start_window_drag(wi, handle or "body", sx, sy)
            return

        # Speaker hit
        si, shandle = self._hit_speaker(sx, sy)
        if si >= 0:
            self._start_speaker_drag(si, shandle or "spk_body", sx, sy)
            return

        # Listener body
        L = self.room.listener
        q = self._project(self._world_from_room(L.x, L.y, L.z))
        if q and math.hypot(sx - q.x(), sy - q.y()) < 16:
            self._start_listener_drag("lis_body", sx, sy)
            return

        self.set_selection("none", -1, emit=True)
        self._drag_mode = None

    def _start_speaker_drag(self, si: int, handle: str, sx: float, sy: float):
        self.set_selection("speaker", si, emit=True)
        spk = self.room.speakers[si]
        # Bake w/h/d so resize can shrink one axis without inflating others via size
        if hasattr(spk, "materialize_dims"):
            bw, bh, bd = spk.materialize_dims()
        else:
            bw, bh, bd = self._spk_dims(spk)
        self._drag_mode = handle or "spk_body"
        self._drag_spk_idx = si
        self._drag_win_idx = -1
        self._drag_lis = False
        self._drag_screen0 = (sx, sy)
        self._drag_orig = {
            "x": float(spk.x), "y": float(spk.y), "z": float(spk.z),
            "size": float(getattr(spk, "size", 0.32)),
            "bw": bw, "bh": bh, "bd": bd,
        }
        self.statusMessage.emit(
            f"Editing {spk.name} — RGB arrows move · red W / green H / blue D resize"
        )
        self.update()

    def _start_window_drag(self, wi: int, handle: str, sx: float, sy: float):
        self.set_selection("window", wi, emit=True)
        win = self.room.windows[wi]
        wall = (win.wall or "north").lower()
        hit = self._intersect_wall(wall, sx, sy)
        cx, cy, cz = self.room.window_center(win)
        self._drag_mode = handle or "body"
        self._drag_win_idx = wi
        self._drag_spk_idx = -1
        self._drag_lis = False
        self._drag_screen0 = (sx, sy)
        self._drag_orig = {
            "offset": float(win.offset),
            "width": float(win.width),
            "sill": float(win.sill),
            "height": float(win.height),
            "cx": cx, "cy": cy, "cz": cz,
        }
        if hit and not str(handle).startswith("win_gizmo"):
            along, y = hit
            self._drag_grab = (along - win.offset, y - win.sill)
        else:
            self._drag_grab = (win.width * 0.5, win.height * 0.5)
        self.statusMessage.emit(
            f"Editing {win.name} — edges resize · RGB arrows free-move (can stick out)"
        )
        self.update()

    def _start_listener_drag(self, handle: str, sx: float, sy: float):
        L = self.room.listener
        self.set_selection("listener", 0, emit=True)
        self._drag_mode = handle or "lis_body"
        self._drag_lis = True
        self._drag_spk_idx = -1
        self._drag_win_idx = -1
        self._drag_screen0 = (sx, sy)
        self._drag_orig = {"x": float(L.x), "y": float(L.y), "z": float(L.z)}
        self.statusMessage.emit("Moving You — drag RGB arrows (X/Y/Z)")
        self.update()

    def mouseMoveEvent(self, e: QtGui.QMouseEvent):
        sx, sy = e.position().x(), e.position().y()

        if self._orbiting and e.buttons() & (QtCore.Qt.RightButton | QtCore.Qt.MiddleButton):
            if self._last is not None:
                dx = (sx - self._last.x()) * 0.01
                dy = (sy - self._last.y()) * 0.01
                self.yaw -= dx
                self.pitch -= dy
                self._clamp()
                self.cameraChanged.emit(self.yaw, self.pitch, self.distance)
                self.update()
            self._last = e.position()
            return

        if self._drag_mode and e.buttons() & QtCore.Qt.LeftButton:
            mode = str(self._drag_mode)
            if self._drag_win_idx >= 0:
                if mode.startswith("win_gizmo"):
                    self._apply_window_gizmo(sx, sy)
                else:
                    self._apply_window_drag(sx, sy)
                self.roomChanged.emit()
                self.update()
                self._last = e.position()
                return
            if self._drag_spk_idx >= 0 and mode.startswith("spk_"):
                self._apply_speaker_drag(sx, sy)
                self.roomChanged.emit()
                self.update()
                self._last = e.position()
                return
            if self._drag_lis and mode.startswith("lis_"):
                self._apply_listener_drag(sx, sy)
                self.roomChanged.emit()
                self.update()
                self._last = e.position()
                return

        # Hover handles for cursor feedback
        if self.room:
            # gizmo hover on selection
            kind, idx = self._selection
            hover = None
            if kind == "speaker" and 0 <= idx < len(self.room.speakers):
                spk = self.room.speakers[idx]
                hover = self._hit_gizmo(sx, sy, (spk.x, spk.y, spk.z), "spk")
                if not hover:
                    for name, hp in self._speaker_handle_points(spk).items():
                        if math.hypot(sx - hp.x(), sy - hp.y()) <= 12:
                            hover = name
                            break
            elif kind == "window" and 0 <= idx < len(self.room.windows):
                win = self.room.windows[idx]
                c = self.room.window_center(win)
                hover = self._hit_gizmo(sx, sy, c, "win")
                if not hover:
                    wi, handle = self._hit_window(sx, sy)
                    if wi == idx:
                        hover = handle
            elif kind == "listener":
                L = self.room.listener
                hover = self._hit_gizmo(sx, sy, (L.x, L.y, L.z), "lis")
            if hover:
                if hover != self._hover_handle:
                    self._hover_handle = hover
                    self.setCursor(self._cursor_for_handle(hover))
                    self.update()
            else:
                wi, handle = self._hit_window(sx, sy)
                if wi >= 0:
                    if handle != self._hover_handle:
                        self._hover_handle = handle
                        self.setCursor(self._cursor_for_handle(handle))
                        if self._selection[0] == "window":
                            self.update()
                else:
                    si, sh = self._hit_speaker(sx, sy)
                    if si >= 0:
                        if sh != self._hover_handle:
                            self._hover_handle = sh
                            self.setCursor(self._cursor_for_handle(sh))
                            if self._selection[0] == "speaker":
                                self.update()
                    else:
                        if self._hover_handle is not None:
                            self._hover_handle = None
                            self.update()
                        self.setCursor(QtCore.Qt.ArrowCursor)

        self._last = e.position()

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent):
        if e.button() == QtCore.Qt.LeftButton and self._drag_mode and self._drag_win_idx >= 0:
            win = self.room.windows[self._drag_win_idx]
            free = " free" if getattr(win, "free_place", False) else ""
            self.statusMessage.emit(
                f"{win.name}: {win.width:.2f}×{win.height:.2f} m @ sill {win.sill:.2f} m{free}"
            )
            self.roomChanged.emit()
        if e.button() == QtCore.Qt.LeftButton and self._drag_mode and self._drag_spk_idx >= 0:
            spk = self.room.speakers[self._drag_spk_idx]
            bw, bh, bd = self._spk_dims(spk)
            self.statusMessage.emit(
                f"{spk.name}: ({spk.x:.2f}, {spk.y:.2f}, {spk.z:.2f}) "
                f"W{bw:.2f}×H{bh:.2f}×D{bd:.2f} m  range~{max(bw, bd):.2f} m"
            )
            self.roomChanged.emit()
        if e.button() == QtCore.Qt.LeftButton and self._drag_lis:
            L = self.room.listener
            self.statusMessage.emit(f"You @ ({L.x:.2f}, {L.y:.2f}, {L.z:.2f}) m")
            self.roomChanged.emit()
        self._orbiting = False
        self._drag_mode = None
        self._drag_win_idx = -1
        self._drag_spk_idx = -1
        self._drag_lis = False
        self._drag_orig = None
        self._drag_grab = None
        self._drag_screen0 = None

    def mouseDoubleClickEvent(self, e: QtGui.QMouseEvent):
        if e.button() == QtCore.Qt.LeftButton:
            self.fit_camera()

    def wheelEvent(self, e: QtGui.QWheelEvent):
        factor = 0.88 if e.angleDelta().y() > 0 else 1.14
        self.distance *= factor
        self._clamp()
        self.cameraChanged.emit(self.yaw, self.pitch, self.distance)
        self.update()

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        if e.key() in (QtCore.Qt.Key_F, QtCore.Qt.Key_Home):
            self.fit_camera()
        elif e.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            kind, idx = self._selection
            if kind == "window" and 0 <= idx < len(self.room.windows):
                name = self.room.windows[idx].name
                del self.room.windows[idx]
                self.set_selection("none", -1, emit=True)
                self.roomChanged.emit()
                self.statusMessage.emit(f"Removed {name}")
                self.update()
