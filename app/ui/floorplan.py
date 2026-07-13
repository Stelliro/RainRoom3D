
"""Interactive top-down terrain + house floor-plan editor."""

from __future__ import annotations

import math
from typing import Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from app.models.room import Room, Speaker, Window, Headphones


# Tools
TOOL_SELECT = "select"
TOOL_SPEAKER = "speaker"
TOOL_WINDOW = "window"
TOOL_LISTENER = "listener"
TOOL_RESIZE = "resize"


class FloorPlanCanvas(QtWidgets.QWidget):
    """Top-down map: outdoor terrain, house footprint, windows, speakers."""

    selectionChanged = QtCore.Signal(tuple)   # ("speaker"|"window"|"listener"|"none", idx)
    roomChanged = QtCore.Signal()
    statusMessage = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(640, 480)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        self.room: Optional[Room] = None
        self.tool = TOOL_SELECT
        self.selection: Tuple[str, int] = ("none", -1)

        self._dragging = False
        self._drag_kind = None
        self._drag_idx = -1
        self._drag_last: Optional[QtCore.QPointF] = None
        self._resize_edge: Optional[str] = None
        self._hover_world: Optional[Tuple[float, float]] = None

        # View transform: world meters → widget pixels
        self._scale = 28.0  # px per meter
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._panning = False
        self._pan_last = None

    # ----- public API -----
    def set_room(self, room: Room):
        self.room = room
        self._fit_view()
        self.update()

    def set_tool(self, tool: str):
        self.tool = tool
        self.statusMessage.emit(f"Tool: {tool}")
        self.update()

    def set_selection(self, kind: str, idx: int, emit: bool = True):
        sel = (kind, idx)
        if sel == self.selection:
            self.update()
            return
        self.selection = sel
        if emit:
            self.selectionChanged.emit(self.selection)
        self.update()

    def clear_selection(self):
        self.set_selection("none", -1)

    # ----- coordinate transforms -----
    def _house_origin_screen(self) -> Tuple[float, float]:
        """Screen position of world (0,0) house SW corner."""
        if not self.room:
            return self.width() * 0.5, self.height() * 0.5
        # Center house in view with pan
        cx = self.width() * 0.5 + self._pan_x
        cy = self.height() * 0.5 + self._pan_y
        ox = cx - self.room.width * 0.5 * self._scale
        oy = cy + self.room.depth * 0.5 * self._scale  # screen Y grows down
        return ox, oy

    def world_to_screen(self, x: float, z: float) -> Tuple[float, float]:
        ox, oy = self._house_origin_screen()
        return ox + x * self._scale, oy - z * self._scale

    def screen_to_world(self, sx: float, sy: float) -> Tuple[float, float]:
        ox, oy = self._house_origin_screen()
        x = (sx - ox) / max(1e-6, self._scale)
        z = (oy - sy) / max(1e-6, self._scale)
        return x, z

    def _fit_view(self):
        if not self.room:
            return
        # Fit terrain roughly in view
        t = max(12.0, self.room.terrain_size * 0.55)
        s = min(self.width(), self.height()) / max(1.0, t)
        self._scale = max(8.0, min(48.0, s * 0.85))
        self._pan_x = 0.0
        self._pan_y = 0.0

    # ----- painting -----
    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor("#0a1018"))
        if not self.room:
            p.setPen(QtGui.QColor("#6b7a90"))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, "No house loaded")
            return

        self._draw_terrain(p)
        self._draw_grid(p)
        self._draw_rain_hints(p)
        self._draw_house(p)
        self._draw_windows(p)
        self._draw_speakers(p)
        self._draw_listener(p)
        self._draw_overlay(p)

    def _draw_terrain(self, p: QtGui.QPainter):
        R = self.room.terrain_size * 0.5
        cx, cz = self.room.width * 0.5, self.room.depth * 0.5
        # Soft outdoor ground
        pts = [
            self.world_to_screen(cx - R, cz - R),
            self.world_to_screen(cx + R, cz - R),
            self.world_to_screen(cx + R, cz + R),
            self.world_to_screen(cx - R, cz + R),
        ]
        path = QtGui.QPainterPath()
        path.moveTo(*pts[0])
        for pt in pts[1:]:
            path.lineTo(*pt)
        path.closeSubpath()
        grad = QtGui.QLinearGradient(pts[0][0], pts[0][1], pts[2][0], pts[2][1])
        grad.setColorAt(0.0, QtGui.QColor("#132018"))
        grad.setColorAt(0.5, QtGui.QColor("#101c16"))
        grad.setColorAt(1.0, QtGui.QColor("#0d1814"))
        p.fillPath(path, grad)
        # Terrain edge
        p.setPen(QtGui.QPen(QtGui.QColor("#1e3a2a"), 1.5, QtCore.Qt.DashLine))
        p.drawPath(path)
        # Label
        sx, sy = self.world_to_screen(cx - R + 0.8, cz + R - 0.5)
        p.setPen(QtGui.QColor("#3d6b52"))
        p.drawText(int(sx), int(sy), "OUTDOOR TERRAIN")

    def _draw_grid(self, p: QtGui.QPainter):
        R = self.room.terrain_size * 0.5
        cx, cz = self.room.width * 0.5, self.room.depth * 0.5
        p.setPen(QtGui.QPen(QtGui.QColor(40, 60, 50, 60), 1))
        step = 1.0
        x0 = math.floor(cx - R)
        x1 = math.ceil(cx + R)
        z0 = math.floor(cz - R)
        z1 = math.ceil(cz + R)
        x = x0
        while x <= x1:
            a = self.world_to_screen(x, z0)
            b = self.world_to_screen(x, z1)
            p.drawLine(QtCore.QPointF(*a), QtCore.QPointF(*b))
            x += step
        z = z0
        while z <= z1:
            a = self.world_to_screen(x0, z)
            b = self.world_to_screen(x1, z)
            p.drawLine(QtCore.QPointF(*a), QtCore.QPointF(*b))
            z += step

    def _draw_rain_hints(self, p: QtGui.QPainter):
        """Soft cyan glow outside windows to show where rain is simulated."""
        for w in self.room.windows:
            ex, ey, ez = self.room.window_exterior_point(w, out_dist=0.6)
            sx, sy = self.world_to_screen(ex, ez)
            open_a = int(40 + 100 * max(0.0, min(1.0, w.open)))
            p.setBrush(QtGui.QColor(80, 180, 255, open_a))
            p.setPen(QtCore.Qt.NoPen)
            rad = 10 + 18 * max(0.2, w.open)
            p.drawEllipse(QtCore.QPointF(sx, sy), rad, rad)

    def _draw_house(self, p: QtGui.QPainter):
        x0, y0 = self.world_to_screen(0, self.room.depth)
        x1, y1 = self.world_to_screen(self.room.width, 0)
        rect = QtCore.QRectF(
            min(x0, x1), min(y0, y1),
            abs(x1 - x0), abs(y1 - y0),
        )
        # Floor fill
        p.setBrush(QtGui.QColor("#1a2230"))
        p.setPen(QtGui.QPen(QtGui.QColor("#5a7aa0"), 3))
        p.drawRect(rect)
        # Interior floor hatch
        p.setPen(QtGui.QPen(QtGui.QColor(70, 90, 120, 40), 1))
        step = max(12, int(self._scale * 0.5))
        for i in range(int(rect.left()), int(rect.right()), step):
            p.drawLine(i, int(rect.top()), i, int(rect.bottom()))

        # Wall thickness visual
        p.setPen(QtGui.QPen(QtGui.QColor("#8aa4c4"), 5))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(rect)

        # Dimension labels
        p.setPen(QtGui.QColor("#6b7a90"))
        p.drawText(int(rect.center().x() - 30), int(rect.bottom() + 18),
                   f"{self.room.width:.1f} m wide")
        p.save()
        p.translate(rect.left() - 12, rect.center().y() + 30)
        p.rotate(-90)
        p.drawText(0, 0, f"{self.room.depth:.1f} m deep")
        p.restore()

        # North indicator
        nx, ny = self.world_to_screen(self.room.width * 0.5, self.room.depth + 1.2)
        p.setPen(QtGui.QPen(QtGui.QColor("#4a9eff"), 2))
        p.drawLine(int(nx), int(ny + 14), int(nx), int(ny - 10))
        p.drawText(int(nx - 6), int(ny - 14), "N")

        # House name
        p.setPen(QtGui.QColor("#c8d4e4"))
        font = p.font(); font.setBold(True); p.setFont(font)
        p.drawText(int(rect.left() + 8), int(rect.top() + 18), self.room.name)

    def _draw_windows(self, p: QtGui.QPainter):
        for i, w in enumerate(self.room.windows):
            self.room.sync_window_coords(w)
            sel = self.selection == ("window", i)
            pts = self._window_screen_segment(w)
            if not pts:
                continue
            (ax, ay), (bx, by) = pts
            color = QtGui.QColor("#5eead4") if sel else QtGui.QColor("#2dd4bf")
            p.setPen(QtGui.QPen(color, 8 if sel else 6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            p.drawLine(QtCore.QPointF(ax, ay), QtCore.QPointF(bx, by))
            # open indicator
            mx, my = (ax + bx) * 0.5, (ay + by) * 0.5
            p.setPen(QtGui.QColor("#99f6e4"))
            p.drawText(int(mx - 28), int(my - 12), f"{w.name}")
            p.setPen(QtGui.QColor("#5eead4"))
            style = getattr(w, "open_style", "casement") or "casement"
            p.drawText(
                int(mx - 36), int(my + 8),
                f"{style}  {int(w.open*100)}%  h={getattr(w,'height',1.2):.1f}m",
            )
            p.setPen(QtGui.QColor("#94a3b8"))
            p.drawText(
                int(mx - 28), int(my + 22),
                f"sill {getattr(w,'sill',0.9):.1f}m",
            )

    def _window_screen_segment(self, w: Window):
        wall = (w.wall or "north").lower()
        if wall == "north":
            a = self.world_to_screen(w.offset, self.room.depth)
            b = self.world_to_screen(w.offset + w.width, self.room.depth)
        elif wall == "south":
            a = self.world_to_screen(w.offset, 0.0)
            b = self.world_to_screen(w.offset + w.width, 0.0)
        elif wall == "east":
            a = self.world_to_screen(self.room.width, w.offset)
            b = self.world_to_screen(self.room.width, w.offset + w.width)
        else:
            a = self.world_to_screen(0.0, w.offset)
            b = self.world_to_screen(0.0, w.offset + w.width)
        return a, b

    def _draw_speakers(self, p: QtGui.QPainter):
        for i, s in enumerate(self.room.speakers):
            sx, sy = self.world_to_screen(s.x, s.z)
            sel = self.selection == ("speaker", i)
            assigned = getattr(s, "audio_device", None) is not None and s.enabled
            if not s.enabled:
                fill = QtGui.QColor("#3a4050")
            elif assigned:
                fill = QtGui.QColor("#f59e0b") if not sel else QtGui.QColor("#fbbf24")
            else:
                fill = QtGui.QColor("#78716c") if not sel else QtGui.QColor("#a8a29e")
            p.setBrush(fill)
            p.setPen(QtGui.QPen(QtGui.QColor("#fff7ed") if sel else QtGui.QColor("#1c1917"), 2))
            p.drawEllipse(QtCore.QPointF(sx, sy), 11 if sel else 9, 11 if sel else 9)
            # Icon: small speaker cone
            p.setPen(QtGui.QPen(QtGui.QColor("#1c1917"), 1.5))
            p.drawLine(int(sx - 3), int(sy), int(sx + 2), int(sy - 4))
            p.drawLine(int(sx - 3), int(sy), int(sx + 2), int(sy + 4))
            p.setPen(QtGui.QColor("#fde68a"))
            p.drawText(int(sx + 12), int(sy + 4), s.name)
            if assigned:
                p.setPen(QtGui.QColor("#86efac"))
                p.drawText(int(sx + 12), int(sy + 18), f"dev {s.audio_device}")
            else:
                p.setPen(QtGui.QColor("#fca5a5"))
                p.drawText(int(sx + 12), int(sy + 18), "unassigned")

    def _draw_listener(self, p: QtGui.QPainter):
        L = self.room.listener
        sx, sy = self.world_to_screen(L.x, L.z)
        sel = self.selection == ("listener", 0)
        p.setBrush(QtGui.QColor("#38bdf8") if sel else QtGui.QColor("#0ea5e9"))
        p.setPen(QtGui.QPen(QtGui.QColor("#e0f2fe"), 2))
        p.drawEllipse(QtCore.QPointF(sx, sy), 8, 8)
        # facing
        dx = math.sin(L.yaw) * 18
        dz = math.cos(L.yaw) * 18
        ex, ey = self.world_to_screen(L.x + math.sin(L.yaw) * 0.6, L.z + math.cos(L.yaw) * 0.6)
        p.drawLine(QtCore.QPointF(sx, sy), QtCore.QPointF(ex, ey))
        p.setPen(QtGui.QColor("#7dd3fc"))
        p.drawText(int(sx + 12), int(sy - 4), "You")
        you_dev = getattr(L, "audio_device", None)
        if you_dev is not None:
            p.setPen(QtGui.QColor("#86efac"))
            p.drawText(int(sx + 12), int(sy + 12), f"hp dev {you_dev}")
        else:
            p.setPen(QtGui.QColor("#94a3b8"))
            p.drawText(int(sx + 12), int(sy + 12), "hp default")

    def _draw_overlay(self, p: QtGui.QPainter):
        # Tool hint
        p.setPen(QtGui.QColor("#6b7a90"))
        hints = {
            TOOL_SELECT: "Select · drag to move · Delete to remove",
            TOOL_SPEAKER: "Click inside the house to place a speaker",
            TOOL_WINDOW: "Click near a wall edge to place a window",
            TOOL_LISTENER: "Click to move your listening position",
            TOOL_RESIZE: "Drag house edges to resize the footprint",
        }
        p.drawText(12, self.height() - 12, hints.get(self.tool, ""))
        if self._hover_world:
            hx, hz = self._hover_world
            p.drawText(self.width() - 140, self.height() - 12, f"x={hx:.2f}  z={hz:.2f} m")

    # ----- hit testing -----
    def _hit_test(self, sx: float, sy: float) -> Tuple[str, int]:
        if not self.room:
            return ("none", -1)
        best = ("none", -1)
        best_d = 14.0
        for i, s in enumerate(self.room.speakers):
            px, py = self.world_to_screen(s.x, s.z)
            d = math.hypot(sx - px, sy - py)
            if d < best_d:
                best_d = d
                best = ("speaker", i)
        L = self.room.listener
        px, py = self.world_to_screen(L.x, L.z)
        d = math.hypot(sx - px, sy - py)
        if d < best_d:
            best_d = d
            best = ("listener", 0)
        for i, w in enumerate(self.room.windows):
            seg = self._window_screen_segment(w)
            if not seg:
                continue
            (ax, ay), (bx, by) = seg
            d = _dist_point_segment(sx, sy, ax, ay, bx, by)
            if d < best_d:
                best_d = d
                best = ("window", i)
        return best

    def _nearest_wall(self, x: float, z: float):
        """Return (wall, offset) for a point near the house perimeter."""
        r = self.room
        candidates = [
            ("north", abs(z - r.depth), x, r.width),
            ("south", abs(z - 0.0), x, r.width),
            ("east", abs(x - r.width), z, r.depth),
            ("west", abs(x - 0.0), z, r.depth),
        ]
        wall, dist, along, length = min(candidates, key=lambda t: t[1])
        if dist > 1.2:
            return None
        offset = max(0.1, min(length - 1.0, along - 0.5))
        return wall, offset

    def _hit_resize_edge(self, sx, sy) -> Optional[str]:
        if not self.room:
            return None
        # Edges of house rect in screen space
        corners = {
            "n": self.world_to_screen(self.room.width * 0.5, self.room.depth),
            "s": self.world_to_screen(self.room.width * 0.5, 0.0),
            "e": self.world_to_screen(self.room.width, self.room.depth * 0.5),
            "w": self.world_to_screen(0.0, self.room.depth * 0.5),
        }
        for edge, (px, py) in corners.items():
            if math.hypot(sx - px, sy - py) < 16:
                return edge
        return None

    # ----- interaction -----
    def mousePressEvent(self, e: QtGui.QMouseEvent):
        if not self.room:
            return
        pos = e.position()
        sx, sy = pos.x(), pos.y()
        wx, wz = self.screen_to_world(sx, sy)
        self._hover_world = (wx, wz)

        if e.button() == QtCore.Qt.MiddleButton or (
            e.button() == QtCore.Qt.LeftButton and e.modifiers() & QtCore.Qt.AltModifier
        ):
            self._panning = True
            self._pan_last = pos
            return

        if e.button() == QtCore.Qt.RightButton:
            # pan
            self._panning = True
            self._pan_last = pos
            return

        if e.button() != QtCore.Qt.LeftButton:
            return

        if self.tool == TOOL_SPEAKER:
            if self.room.contains_point(wx, wz, margin=0.1):
                n = len(self.room.speakers)
                sp = Speaker(name=f"Speaker {n+1}", x=wx, y=1.1, z=wz)
                self.room.speakers.append(sp)
                self.set_selection("speaker", n)
                self.roomChanged.emit()
                self.statusMessage.emit(f"Placed {sp.name}")
            else:
                self.statusMessage.emit("Place speakers inside the house footprint")
            return

        if self.tool == TOOL_WINDOW:
            hit = self._nearest_wall(wx, wz)
            if hit:
                wall, offset = hit
                n = len(self.room.windows)
                win = Window(
                    name=f"Window {n+1}",
                    wall=wall,
                    offset=offset,
                    width=1.2,
                    height=1.2,
                    open=0.7,
                )
                self.room.windows.append(win)
                self.room.sync_window_coords(win)
                self.set_selection("window", n)
                self.roomChanged.emit()
                self.statusMessage.emit(f"Placed {win.name} on {wall} wall")
            else:
                self.statusMessage.emit("Click near a wall edge to place a window")
            return

        if self.tool == TOOL_LISTENER:
            x, z = self.room.clamp_inside(wx, wz)
            self.room.listener.x = x
            self.room.listener.z = z
            # Keep headphones marker in sync if present
            if self.room.headphones_items:
                self.room.headphones_items[0].x = x
                self.room.headphones_items[0].z = z
            self.set_selection("listener", 0)
            self.roomChanged.emit()
            return

        if self.tool == TOOL_RESIZE:
            edge = self._hit_resize_edge(sx, sy)
            if edge:
                self._dragging = True
                self._resize_edge = edge
                self._drag_last = pos
            return

        # Select / drag
        hit = self._hit_test(sx, sy)
        self.set_selection(*hit)
        if hit[0] != "none":
            self._dragging = True
            self._drag_kind = hit[0]
            self._drag_idx = hit[1]
            self._drag_last = pos

    def mouseMoveEvent(self, e: QtGui.QMouseEvent):
        if not self.room:
            return
        pos = e.position()
        wx, wz = self.screen_to_world(pos.x(), pos.y())
        self._hover_world = (wx, wz)

        if self._panning and self._pan_last is not None:
            dx = pos.x() - self._pan_last.x()
            dy = pos.y() - self._pan_last.y()
            self._pan_x += dx
            self._pan_y += dy
            self._pan_last = pos
            self.update()
            return

        if self._dragging and self.tool == TOOL_RESIZE and self._resize_edge:
            if self._resize_edge == "e":
                self.room.width = max(2.0, min(30.0, wx))
            elif self._resize_edge == "w":
                # move west wall: change width and shift contents? keep simple: just set width from origin
                new_w = max(2.0, min(30.0, self.room.width - wx))
                # ignore complex shift
                pass
            elif self._resize_edge == "n":
                self.room.depth = max(2.0, min(30.0, wz))
            elif self._resize_edge == "s":
                pass
            self.room.sync_all_windows()
            self.roomChanged.emit()
            self.update()
            return

        if self._dragging and self._drag_kind:
            if self._drag_kind == "speaker" and 0 <= self._drag_idx < len(self.room.speakers):
                x, z = self.room.clamp_inside(wx, wz)
                self.room.speakers[self._drag_idx].x = x
                self.room.speakers[self._drag_idx].z = z
                self.roomChanged.emit()
            elif self._drag_kind == "listener":
                x, z = self.room.clamp_inside(wx, wz)
                self.room.listener.x = x
                self.room.listener.z = z
                if self.room.headphones_items:
                    self.room.headphones_items[0].x = x
                    self.room.headphones_items[0].z = z
                self.roomChanged.emit()
            elif self._drag_kind == "window" and 0 <= self._drag_idx < len(self.room.windows):
                w = self.room.windows[self._drag_idx]
                wall = (w.wall or "north").lower()
                if wall in ("north", "south"):
                    w.offset = max(0.0, min(self.room.width - w.width, wx - w.width * 0.5))
                else:
                    w.offset = max(0.0, min(self.room.depth - w.width, wz - w.width * 0.5))
                self.room.sync_window_coords(w)
                self.roomChanged.emit()
            self.update()
            return

        self.update()

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent):
        self._dragging = False
        self._panning = False
        self._drag_kind = None
        self._resize_edge = None
        self._pan_last = None

    def wheelEvent(self, e: QtGui.QWheelEvent):
        delta = e.angleDelta().y()
        factor = 1.1 if delta > 0 else 0.9
        self._scale = max(6.0, min(80.0, self._scale * factor))
        self.update()

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        if e.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            kind, idx = self.selection
            if kind == "speaker" and 0 <= idx < len(self.room.speakers):
                name = self.room.speakers[idx].name
                del self.room.speakers[idx]
                self.clear_selection()
                self.roomChanged.emit()
                self.statusMessage.emit(f"Removed {name}")
            elif kind == "window" and 0 <= idx < len(self.room.windows):
                name = self.room.windows[idx].name
                del self.room.windows[idx]
                self.clear_selection()
                self.roomChanged.emit()
                self.statusMessage.emit(f"Removed {name}")
            self.update()
        elif e.key() == QtCore.Qt.Key_F:
            self._fit_view()
            self.update()


def _dist_point_segment(px, py, ax, ay, bx, by) -> float:
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    ab2 = abx * abx + aby * aby
    if ab2 < 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    cx, cy = ax + t * abx, ay + t * aby
    return math.hypot(px - cx, py - cy)
