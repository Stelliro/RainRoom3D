"""Interactive top-down terrain + house floor-plan editor."""

from __future__ import annotations

import math
from typing import Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from app.models.room import Room, Speaker, Window, WINDOW_STYLE_LABELS
from app.ui.theme import C, T, qcolor


# Tools
TOOL_SELECT = "select"
TOOL_SPEAKER = "speaker"
TOOL_WINDOW = "window"
TOOL_LISTENER = "listener"
TOOL_RESIZE = "resize"

_MIN_SCALE, _MAX_SCALE = 6.0, 120.0


def _short_style(key: str) -> str:
    lab = WINDOW_STYLE_LABELS.get(key, key)
    return lab.split(" (")[0]


class FloorPlanCanvas(QtWidgets.QWidget):
    """Top-down map: outdoor terrain, house footprint, windows, speakers."""

    selectionChanged = QtCore.Signal(tuple)   # ("speaker"|"window"|"listener"|"none", idx)
    roomChanged = QtCore.Signal()
    statusMessage = QtCore.Signal(str)
    placed = QtCore.Signal(str, int)          # a placement tool created / moved something

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
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
        self._hover_edge: Optional[str] = None

        # View transform: world meters → widget pixels
        self._scale = 28.0  # px per meter
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._panning = False
        self._pan_last = None
        self._auto_fit = True                      # re-frame on resize until the user zooms/pans
        self._insets = QtCore.QMargins(0, 0, 0, 0)  # area covered by floating chrome

    # ----- public API -----
    def set_room(self, room: Room):
        self.room = room
        self._fit_view()
        self.update()

    def set_tool(self, tool: str):
        self.tool = tool
        self._hover_edge = None
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

    def set_view_insets(self, left: int, top: int, right: int, bottom: int) -> None:
        """Floating panels cover these margins; fit and HUD stay clear of them."""
        m = QtCore.QMargins(int(left), int(top), int(right), int(bottom))
        if m != self._insets:
            self._insets = m
            if self._auto_fit:
                self._fit_view()
            self.update()

    def safe_rect(self) -> QtCore.QRectF:
        r = QtCore.QRectF(self.rect()).marginsRemoved(QtCore.QMarginsF(self._insets))
        if r.width() < 120 or r.height() < 120:
            return QtCore.QRectF(self.rect())
        return r

    # ----- coordinate transforms -----
    def _house_origin_screen(self) -> Tuple[float, float]:
        """Screen position of world (0,0) house SW corner."""
        if not self.room:
            return self.width() * 0.5, self.height() * 0.5
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
        # Fit the house with ~3 m of yard around it, inside the uncovered area
        safe = self.safe_rect()
        t = max(8.0, max(float(self.room.width), float(self.room.depth)) + 6.0)
        s = min(safe.width(), safe.height()) / max(1.0, t)
        self._scale = max(8.0, min(110.0, s * 0.9))
        self._pan_x = safe.center().x() - self.width() * 0.5
        self._pan_y = safe.center().y() - self.height() * 0.5
        self._auto_fit = True

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._auto_fit:
            self._fit_view()

    # ----- painting -----
    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        bg = QtGui.QLinearGradient(0, 0, 0, self.height())
        bg.setColorAt(0.0, qcolor(C.backdrop_top))
        bg.setColorAt(1.0, qcolor(C.backdrop_bottom))
        p.fillRect(self.rect(), bg)
        if not self.room:
            p.setPen(qcolor(T.text3))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, "No house loaded")
            return

        self._draw_terrain(p)
        self._draw_grid(p)
        self._draw_rain_hints(p)
        self._draw_house(p)
        self._draw_windows(p)
        self._draw_speakers(p)
        self._draw_listener(p)
        self._draw_resize_handles(p)
        self._draw_overlay(p)

    def _font(self, p: QtGui.QPainter, px: int = 12, bold: bool = False):
        f = p.font()
        f.setPixelSize(px)
        f.setBold(bold)
        p.setFont(f)

    def _draw_terrain(self, p: QtGui.QPainter):
        R = self.room.terrain_size * 0.5
        cx, cz = self.room.width * 0.5, self.room.depth * 0.5
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
        grad.setColorAt(0.0, qcolor(C.terrain_a))
        grad.setColorAt(1.0, qcolor(C.terrain_b))
        p.fillPath(path, grad)
        p.setPen(QtGui.QPen(qcolor(C.terrain_edge), 1.2, QtCore.Qt.DashLine))
        p.drawPath(path)
        sx, sy = self.world_to_screen(cx - R + 0.8, cz + R - 0.6)
        self._font(p, 11, True)
        p.setPen(qcolor(C.terrain_label))
        p.drawText(int(sx), int(sy), "YARD")

    def _draw_grid(self, p: QtGui.QPainter):
        R = self.room.terrain_size * 0.5
        cx, cz = self.room.width * 0.5, self.room.depth * 0.5
        p.setPen(QtGui.QPen(QtGui.QColor(*C.grid), 1))
        x0, x1 = math.floor(cx - R), math.ceil(cx + R)
        z0, z1 = math.floor(cz - R), math.ceil(cz + R)
        step = 1.0 if self._scale >= 10 else 2.0
        x = x0
        while x <= x1:
            p.drawLine(QtCore.QPointF(*self.world_to_screen(x, z0)), QtCore.QPointF(*self.world_to_screen(x, z1)))
            x += step
        z = z0
        while z <= z1:
            p.drawLine(QtCore.QPointF(*self.world_to_screen(x0, z)), QtCore.QPointF(*self.world_to_screen(x1, z)))
            z += step

    def _draw_rain_hints(self, p: QtGui.QPainter):
        """Soft glow outside each window: where outdoor rain comes in."""
        for w in self.room.windows:
            ex, _ey, ez = self.room.window_exterior_point(w, out_dist=0.6)
            sx, sy = self.world_to_screen(ex, ez)
            o = max(0.0, min(1.0, w.open))
            rad = (0.5 + 0.9 * max(0.2, o)) * self._scale
            g = QtGui.QRadialGradient(QtCore.QPointF(sx, sy), rad)
            r_, g_, b_ = C.rain_glow
            g.setColorAt(0.0, QtGui.QColor(r_, g_, b_, int(30 + 90 * o)))
            g.setColorAt(1.0, QtGui.QColor(r_, g_, b_, 0))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(QtCore.QPointF(sx, sy), rad, rad)

    def _house_rect(self) -> QtCore.QRectF:
        x0, y0 = self.world_to_screen(0, self.room.depth)
        x1, y1 = self.world_to_screen(self.room.width, 0)
        return QtCore.QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))

    def _draw_house(self, p: QtGui.QPainter):
        rect = self._house_rect()
        floor = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
        floor.setColorAt(0.0, qcolor(C.floor_a))
        floor.setColorAt(1.0, qcolor(C.floor_b))
        p.setBrush(floor)
        p.setPen(QtCore.Qt.NoPen)
        p.drawRect(rect)
        p.setPen(QtGui.QPen(QtGui.QColor(*C.floor_hatch), 1))
        step = max(14, int(self._scale * 0.55))
        for i in range(int(rect.left()) + step, int(rect.right()), step):
            p.drawLine(i, int(rect.top()), i, int(rect.bottom()))

        from app.audio import house_model as hm

        wall_px = max(3.0, min(24.0, hm.wall_thickness(self.room) * self._scale))
        p.setPen(QtGui.QPen(qcolor(C.wall), wall_px, QtCore.Qt.SolidLine, QtCore.Qt.SquareCap,
                            QtCore.Qt.MiterJoin))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(rect)

        # Dimensions
        self._font(p, 12)
        p.setPen(qcolor(C.dim_text))
        fm = p.fontMetrics()
        wtxt = f"{self.room.width:.1f} m"
        p.drawText(QtCore.QPointF(rect.center().x() - fm.horizontalAdvance(wtxt) / 2,
                                  rect.bottom() + wall_px / 2 + 18), wtxt)
        dtxt = f"{self.room.depth:.1f} m"
        p.save()
        p.translate(rect.left() - wall_px / 2 - 10, rect.center().y() + fm.horizontalAdvance(dtxt) / 2)
        p.rotate(-90)
        p.drawText(0, 0, dtxt)
        p.restore()

        # North arrow, just outside the north-east corner
        nx, ny = rect.right() + wall_px / 2 + 18, rect.top() - wall_px / 2 - 8
        p.setPen(QtGui.QPen(qcolor(C.north), 2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        p.drawLine(QtCore.QPointF(nx, ny + 4), QtCore.QPointF(nx, ny - 12))
        p.setBrush(qcolor(C.north))
        p.setPen(QtCore.Qt.NoPen)
        p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(nx, ny - 17), QtCore.QPointF(nx - 4, ny - 10),
                                       QtCore.QPointF(nx + 4, ny - 10)]))
        self._font(p, 11, True)
        p.setPen(qcolor(C.north))
        p.drawText(QtCore.QPointF(nx - 4, ny + 17), "N")

    def _draw_windows(self, p: QtGui.QPainter):
        for i, w in enumerate(self.room.windows):
            self.room.sync_window_coords(w)
            sel = self.selection == ("window", i)
            pts = self._window_screen_segment(w)
            if not pts:
                continue
            (ax, ay), (bx, by) = pts
            if sel:
                p.setPen(QtGui.QPen(QtGui.QColor(45, 212, 191, 70), 16, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
                p.drawLine(QtCore.QPointF(ax, ay), QtCore.QPointF(bx, by))
            color = qcolor(C.window_sel if sel else C.window)
            p.setPen(QtGui.QPen(color, 7 if sel else 6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            p.drawLine(QtCore.QPointF(ax, ay), QtCore.QPointF(bx, by))
            # label sits outside the wall
            mx, my = (ax + bx) * 0.5, (ay + by) * 0.5
            wall = (w.wall or "north").lower()
            off = {"north": (0, -14), "south": (0, 22), "east": (14, 4), "west": (-14, 4)}[wall]
            self._font(p, 12, True)
            fm = p.fontMetrics()
            pct = "closed" if w.open <= 0.005 else f"{int(round(w.open * 100))}% open"
            line1, line2 = w.name, pct
            tw = max(fm.horizontalAdvance(line1), fm.horizontalAdvance(line2))
            if wall in ("north", "south"):
                tx = mx - tw / 2
            elif wall == "east":
                tx = mx + off[0]
            else:
                tx = mx + off[0] - tw
            ty = my + off[1] - (12 if wall == "north" else 0)
            p.setPen(qcolor(C.window_text))
            p.drawText(QtCore.QPointF(tx, ty), line1)
            self._font(p, 11)
            p.setPen(qcolor(T.text3))
            p.drawText(QtCore.QPointF(tx, ty + 14), line2)

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
            assigned = getattr(s, "audio_device", None) is not None
            if not s.enabled:
                fill = qcolor(C.speaker_off)
            elif assigned:
                fill = qcolor(C.speaker_sel if sel else C.speaker_on)
            else:
                fill = qcolor(C.speaker_unassigned)
            if sel:
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QColor(245, 158, 11, 60))
                p.drawEllipse(QtCore.QPointF(sx, sy), 17, 17)
            p.setBrush(fill)
            p.setPen(QtGui.QPen(QtGui.QColor("#fff7ed") if sel else qcolor(T.bg), 2))
            p.drawEllipse(QtCore.QPointF(sx, sy), 10 if sel else 9, 10 if sel else 9)
            # speaker cone glyph
            p.setPen(QtGui.QPen(qcolor(T.bg), 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            p.drawLine(QtCore.QPointF(sx - 3, sy - 2), QtCore.QPointF(sx - 3, sy + 2))
            p.drawLine(QtCore.QPointF(sx - 3, sy - 2), QtCore.QPointF(sx + 2, sy - 5))
            p.drawLine(QtCore.QPointF(sx - 3, sy + 2), QtCore.QPointF(sx + 2, sy + 5))
            p.drawLine(QtCore.QPointF(sx + 2, sy - 5), QtCore.QPointF(sx + 2, sy + 5))
            note = "off" if not s.enabled else ("" if assigned else "no device")
            self._font(p, 12, True)
            tw = p.fontMetrics().horizontalAdvance(s.name)
            if note:
                self._font(p, 11)
                tw = max(tw, p.fontMetrics().horizontalAdvance(note))
            # label to the right; below the dot when that would cross the wall
            house = self._house_rect()
            tx, ty = sx + 14, sy + 1
            if tx + tw > house.right() - 6:
                tx = max(house.left() + 6, min(house.right() - 6 - tw, sx - tw / 2))
                ty = sy + 26
            self._font(p, 12, True)
            p.setPen(qcolor(C.speaker_text))
            p.drawText(QtCore.QPointF(tx, ty), s.name)
            if note:
                self._font(p, 11)
                p.setPen(qcolor(C.warn_text if not assigned and s.enabled else T.text3))
                p.drawText(QtCore.QPointF(tx, ty + 14), note)

    def _draw_listener(self, p: QtGui.QPainter):
        L = self.room.listener
        sx, sy = self.world_to_screen(L.x, L.z)
        sel = self.selection == ("listener", 0)
        ex, ey = self.world_to_screen(L.x + math.sin(L.yaw) * 0.7, L.z + math.cos(L.yaw) * 0.7)
        # facing cone
        ang = math.atan2(ey - sy, ex - sx)
        cone = QtGui.QPainterPath(QtCore.QPointF(sx, sy))
        rad = max(22.0, self._scale * 0.8)
        cone.arcTo(QtCore.QRectF(sx - rad, sy - rad, 2 * rad, 2 * rad), -math.degrees(ang) - 28, 56)
        cone.closeSubpath()
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(76, 184, 255, 55 if sel else 35))
        p.drawPath(cone)
        if sel:
            p.setBrush(QtGui.QColor(76, 184, 255, 60))
            p.drawEllipse(QtCore.QPointF(sx, sy), 15, 15)
        p.setBrush(qcolor(C.you_sel if sel else C.you))
        p.setPen(QtGui.QPen(QtGui.QColor("#e0f2fe"), 2))
        p.drawEllipse(QtCore.QPointF(sx, sy), 8, 8)
        self._font(p, 12, True)
        p.setPen(qcolor(C.you_sel))
        p.drawText(QtCore.QPointF(sx + 13, sy - 4), "You")

    def _edge_points(self):
        r = self.room
        return {
            "n": self.world_to_screen(r.width * 0.5, r.depth),
            "s": self.world_to_screen(r.width * 0.5, 0.0),
            "e": self.world_to_screen(r.width, r.depth * 0.5),
            "w": self.world_to_screen(0.0, r.depth * 0.5),
        }

    def _draw_resize_handles(self, p: QtGui.QPainter):
        if self.tool != TOOL_RESIZE:
            return
        for edge, (px, py) in self._edge_points().items():
            active = edge in (self._hover_edge, self._resize_edge)
            p.setPen(QtGui.QPen(qcolor(T.accent), 2))
            p.setBrush(qcolor(T.accent if active else T.raised))
            if edge in ("n", "s"):
                p.drawRoundedRect(QtCore.QRectF(px - 14, py - 5, 28, 10), 5, 5)
            else:
                p.drawRoundedRect(QtCore.QRectF(px - 5, py - 14, 10, 28), 5, 5)

    def _draw_overlay(self, p: QtGui.QPainter):
        if self._hover_world and self.underMouse():
            hx, hz = self._hover_world
            safe = self.safe_rect()
            self._font(p, 11)
            p.setPen(qcolor(T.text3))
            txt = f"{hx:.2f} m east · {hz:.2f} m north"
            fm = p.fontMetrics()
            p.drawText(QtCore.QPointF(safe.right() - fm.horizontalAdvance(txt) - 14, safe.bottom() - 14), txt)

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
        for edge, (px, py) in self._edge_points().items():
            if math.hypot(sx - px, sy - py) < 18:
                return edge
        return None

    # ----- interaction -----
    def mousePressEvent(self, e: QtGui.QMouseEvent):
        if not self.room:
            return
        self.setFocus(QtCore.Qt.MouseFocusReason)
        pos = e.position()
        sx, sy = pos.x(), pos.y()
        wx, wz = self.screen_to_world(sx, sy)
        self._hover_world = (wx, wz)

        if e.button() in (QtCore.Qt.MiddleButton, QtCore.Qt.RightButton) or (
            e.button() == QtCore.Qt.LeftButton and e.modifiers() & QtCore.Qt.AltModifier
        ):
            self._panning = True
            self._pan_last = pos
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            return

        if e.button() != QtCore.Qt.LeftButton:
            return

        if self.tool == TOOL_SPEAKER:
            if self.room.contains_point(wx, wz, margin=0.1):
                n = len(self.room.speakers)
                sp = Speaker(
                    name=f"Speaker {n+1}", x=wx, y=1.1, z=wz,
                    width=0.45, height=0.28, depth=0.18, size=0.32,
                )
                self.room.speakers.append(sp)
                self.set_selection("speaker", n)
                self.roomChanged.emit()
                self.statusMessage.emit(f"Placed {sp.name} — pick its device in the inspector")
                self.placed.emit("speaker", n)
            else:
                self.statusMessage.emit("Place speakers inside the house")
            return

        if self.tool == TOOL_WINDOW:
            hit = self._nearest_wall(wx, wz)
            if hit:
                wall, offset = hit
                n = len(self.room.windows)
                win = Window(name=f"Window {n+1}", wall=wall, offset=offset, width=1.2, height=1.2, open=0.7)
                self.room.windows.append(win)
                self.room.sync_window_coords(win)
                self.set_selection("window", n)
                self.roomChanged.emit()
                self.statusMessage.emit(f"Placed {win.name} on the {wall} wall")
                self.placed.emit("window", n)
            else:
                self.statusMessage.emit("Click closer to a wall to place a window")
            return

        if self.tool == TOOL_LISTENER:
            x, z = self.room.clamp_inside(wx, wz)
            self.room.listener.x = x
            self.room.listener.z = z
            if self.room.headphones_items:
                self.room.headphones_items[0].x = x
                self.room.headphones_items[0].z = z
            self.set_selection("listener", 0)
            self.roomChanged.emit()
            self.statusMessage.emit("Moved You")
            self.placed.emit("listener", 0)
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

    def _shift_contents(self, dx: float, dz: float) -> None:
        """Move everything inside by (dx, dz) — used when the W or S wall moves."""
        r = self.room
        for s in r.speakers:
            s.x += dx
            s.z += dz
        r.listener.x += dx
        r.listener.z += dz
        for h in r.headphones_items or []:
            h.x += dx
            h.z += dz
        for w in r.windows:
            wall = (w.wall or "north").lower()
            if wall in ("north", "south"):
                w.offset += dx
            else:
                w.offset += dz
            if getattr(w, "free_place", False):
                w.free_x += dx
                w.free_z += dz

    def _keep_inside(self) -> None:
        r = self.room
        for s in r.speakers:
            s.x, s.z = r.clamp_inside(s.x, s.z)
        r.listener.x, r.listener.z = r.clamp_inside(r.listener.x, r.listener.z)
        if r.headphones_items:
            r.headphones_items[0].x, r.headphones_items[0].z = r.listener.x, r.listener.z
        for w in r.windows:
            length = r.width if (w.wall or "north").lower() in ("north", "south") else r.depth
            w.width = min(w.width, max(0.3, length - 0.1))
            w.offset = max(0.0, min(length - w.width, w.offset))
        r.sync_all_windows()

    def _apply_resize(self, wx: float, wz: float) -> None:
        r, s = self.room, self._scale
        e = self._resize_edge
        if e == "e":
            new = max(2.0, min(30.0, wx))
            dw = new - r.width
            r.width = new
            self._pan_x += dw * s * 0.5
        elif e == "w":
            new = max(2.0, min(30.0, r.width - wx))
            dw = new - r.width
            r.width = new
            self._shift_contents(dw, 0.0)
            self._pan_x -= dw * s * 0.5
        elif e == "n":
            new = max(2.0, min(30.0, wz))
            dd = new - r.depth
            r.depth = new
            self._pan_y -= dd * s * 0.5
        elif e == "s":
            new = max(2.0, min(30.0, r.depth - wz))
            dd = new - r.depth
            r.depth = new
            self._shift_contents(0.0, dd)
            self._pan_y += dd * s * 0.5
        self._auto_fit = False
        self._keep_inside()

    def mouseMoveEvent(self, e: QtGui.QMouseEvent):
        if not self.room:
            return
        pos = e.position()
        wx, wz = self.screen_to_world(pos.x(), pos.y())
        self._hover_world = (wx, wz)

        if self._panning and self._pan_last is not None:
            self._pan_x += pos.x() - self._pan_last.x()
            self._pan_y += pos.y() - self._pan_last.y()
            self._pan_last = pos
            self._auto_fit = False
            self.update()
            return

        if self._dragging and self.tool == TOOL_RESIZE and self._resize_edge:
            self._apply_resize(wx, wz)
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

        if self.tool == TOOL_RESIZE:
            edge = self._hit_resize_edge(pos.x(), pos.y())
            if edge != self._hover_edge:
                self._hover_edge = edge
                self.setCursor(QtCore.Qt.SizeHorCursor if edge in ("e", "w") else
                               QtCore.Qt.SizeVerCursor if edge else QtCore.Qt.ArrowCursor)
        elif self.tool in (TOOL_SPEAKER, TOOL_WINDOW, TOOL_LISTENER):
            self.setCursor(QtCore.Qt.CrossCursor)
        else:
            hit = self._hit_test(pos.x(), pos.y())
            self.setCursor(QtCore.Qt.PointingHandCursor if hit[0] != "none" else QtCore.Qt.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent):
        was_resize = self._resize_edge is not None
        self._dragging = False
        self._panning = False
        self._drag_kind = None
        self._resize_edge = None
        self._pan_last = None
        if was_resize and self.room:
            self.statusMessage.emit(f"House is now {self.room.width:.1f} × {self.room.depth:.1f} m")
        self.setCursor(QtCore.Qt.ArrowCursor)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)

    def wheelEvent(self, e: QtGui.QWheelEvent):
        delta = e.angleDelta().y()
        if not delta:
            return
        factor = 1.12 if delta > 0 else 1 / 1.12
        new = max(_MIN_SCALE, min(_MAX_SCALE, self._scale * factor))
        # zoom about the cursor: keep the world point under it fixed
        pos = e.position()
        wx, wz = self.screen_to_world(pos.x(), pos.y())
        self._scale = new
        nx, ny = self.world_to_screen(wx, wz)
        self._pan_x += pos.x() - nx
        self._pan_y += pos.y() - ny
        self._auto_fit = False
        self.update()

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        # Delete / Esc / F / tool keys are handled once, by the main window.
        e.ignore()


def _dist_point_segment(px, py, ax, ay, bx, by) -> float:
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    ab2 = abx * abx + aby * aby
    if ab2 < 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    cx, cy = ax + t * abx, ay + t * aby
    return math.hypot(px - cx, py - cy)
