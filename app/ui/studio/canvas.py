"""The canvas: floor plan / 3D views with floating chrome on top.

The views sit in a QStackedWidget; the tool palette, objects list, inspector
card, view switch and hint line are non-native *siblings* stacked over it and
placed by hand in ``resizeEvent``. Nothing is parented to a view, so the
overlays survive view switches and never become children of the OpenGL widget
(which also drives drawn-drop audio while it is visible: there is exactly one
GL view and it is only ever shown through the stack).
"""

from __future__ import annotations

import logging
import math
import os
from typing import Dict

from PySide6 import QtCore, QtWidgets

from app.graphics import lightning
from app.graphics.room3d_view import Room3DView
from app.ui.floorplan import (
    TOOL_LISTENER, TOOL_RESIZE, TOOL_SELECT, TOOL_SPEAKER, TOOL_WINDOW, FloorPlanCanvas,
)
from app.ui.studio.context import StudioContext
from app.ui.studio.controls import SegmentedControl, label
from app.ui.theme import SP

log = logging.getLogger("ui")

GL_AVAILABLE = False
GLRoomView = None
configure_gl_surface_format = None
try:
    from app.graphics.glview import GLRoomView as _GLRoomView, configure_gl_surface_format  # noqa: F401
    GLRoomView = _GLRoomView
    GL_AVAILABLE = True
except Exception as _e:  # PyOpenGL missing etc.
    log.info("OpenGL view unavailable: %s", _e)

VIEW_PLAN, VIEW_3D, VIEW_GL = "plan", "3d", "gl"

MARGIN = 2 * SP  # gap between floating chrome and the canvas edge

_HINTS = {
    VIEW_PLAN: {
        TOOL_SELECT: "Click to select · drag to move · right-drag pans",
        TOOL_WINDOW: "Click near a wall to add a window · Esc cancels",
        TOOL_SPEAKER: "Click inside the house to add a speaker · Esc cancels",
        TOOL_LISTENER: "Click where you sit · Esc cancels",
        TOOL_RESIZE: "Drag a wall handle to resize · Esc when done",
    },
    VIEW_3D: {
        TOOL_SELECT: "Click to select · arrows move · right-drag orbits",
        TOOL_WINDOW: "Click a wall to add a window · Esc cancels",
        TOOL_SPEAKER: "Click inside the house to add a speaker · Esc cancels",
        TOOL_LISTENER: "Click where you sit · Esc cancels",
        TOOL_RESIZE: "Resizing works on the floor plan",
    },
    VIEW_GL: {
        TOOL_SELECT: "Click a speaker or You · drag to move · right-drag orbits",
    },
}
_SEL_HINTS_3D = {
    "speaker": "Arrows move · coloured squares resize",
    "window": "Drag to slide · edges resize · arrows move freely",
    "listener": "Arrows move You · green sets ear height",
}
_LISTEN_HINT = "Right-drag to look around · scroll zooms · F fits"


class CanvasHost(QtWidgets.QWidget):
    viewChanged = QtCore.Signal(str)

    def __init__(self, ctx: StudioContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("CanvasHost")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.tool = TOOL_SELECT
        self._edit_chrome = True
        self._objects_user_set = False   # the user folded / unfolded the objects list
        self.palette = self.objects = self.inspector = None

        self.floor = FloorPlanCanvas(self)
        self.view3d = Room3DView(self)
        self.gl = None
        if GL_AVAILABLE and os.getenv("RAINROOM_NOGPU", "0") != "1" and GLRoomView is not None:
            try:
                self.gl = GLRoomView(self)
                self.gl.set_impact_handler(ctx.engine.submit_visual_impacts)
            except Exception as e:
                log.exception("GL init failed: %s", e)
                self.gl = None

        self.stack = QtWidgets.QStackedWidget(self)
        self._views: Dict[str, QtWidgets.QWidget] = {VIEW_PLAN: self.floor, VIEW_3D: self.view3d}
        self.stack.addWidget(self.floor)
        self.stack.addWidget(self.view3d)
        if self.gl is not None:
            self.stack.addWidget(self.gl)
            self._views[VIEW_GL] = self.gl

        self.view_chip = SegmentedControl(self, accent=True, floating=True)
        self.view_chip.add(VIEW_PLAN, "Plan", "Top-down floor plan", "plan")
        self.view_chip.add(VIEW_3D, "3D", "3D preview (works everywhere)", "cube")
        if self.gl is not None:
            self.view_chip.add(VIEW_GL, "GPU", "Hardware OpenGL 3D with drawn rain "
                               "(orbit with right-drag, scroll zoom, F to fit)", "cube_gpu")
        self.view_chip.changed.connect(self.set_view)

        self.hint = label("", "HintPill")
        self.hint.setParent(self)
        self.hint.setAlignment(QtCore.Qt.AlignCenter)
        self.hint.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

        for v in (self.floor, self.view3d):
            v.set_room(ctx.room)
            v.selectionChanged.connect(self._from_view_selection)
            v.roomChanged.connect(lambda src=v: self.ctx.edited("canvas", src))
            v.statusMessage.connect(lambda t: self.ctx.say(t))
            v.placed.connect(self._on_placed)
        if self.gl is not None:
            self.gl.set_room(ctx.room)
            self.gl.selectionChanged.connect(self._from_view_selection)
            self.gl.roomChanged.connect(lambda: self.ctx.edited("canvas", self.gl))
            self.gl.glStatus.connect(self._on_gl_status)

        # Lightning: light the 3D sky when the audio clock reaches a flash
        self._flash_timer = QtCore.QTimer(self)
        self._flash_timer.setInterval(33)
        self._flash_timer.timeout.connect(self._update_flash)
        self._flash_timer.start()

        self._reseed = QtCore.QTimer(self)
        self._reseed.setSingleShot(True)
        self._reseed.setInterval(200)
        self._reseed.timeout.connect(self._reseed_rain)

        ctx.selectionChanged.connect(self._push_selection)
        ctx.roomEdited.connect(self._on_edited)
        ctx.roomReplaced.connect(self.room_replaced)
        self._update_hint()

    # ----- overlays -----
    def set_overlays(self, palette, objects, inspector) -> None:
        self.palette, self.objects, self.inspector = palette, objects, inspector
        for w in (palette, objects, inspector):
            w.setParent(self)
            w.show()
        objects.collapsedChanged.connect(self._on_objects_collapsed)
        self._raise_chrome()
        self._layout()

    def _on_objects_collapsed(self, _collapsed: bool, user: bool = True) -> None:
        if user:
            self._objects_user_set = True
        self._layout()

    def _raise_chrome(self) -> None:
        for w in (self.palette, self.objects, self.inspector, self.view_chip, self.hint):
            if w is not None:
                w.raise_()

    def set_edit_chrome(self, on: bool) -> None:
        """Design mode shows palette, objects and inspector; Listen hides them."""
        self._edit_chrome = bool(on)
        for w in (self.palette, self.objects, self.inspector):
            if w is not None:
                w.setVisible(on)
        self.view3d.set_annotations(on)
        self._update_hint()
        self._layout()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()

    def _layout(self) -> None:
        W, H = self.width(), self.height()
        self.stack.setGeometry(0, 0, W, H)
        m = MARGIN
        pad = 10  # FloatCard shadow padding: visible card edge sits PAD inside
        left_edge = 0
        right_edge = 0
        chip = self.view_chip.sizeHint()
        chip_y = H - m - chip.height()
        self.view_chip.setGeometry(m, chip_y, chip.width(), chip.height())
        if self._edit_chrome and self.palette is not None:
            ph = self.palette.sizeHint()
            self.palette.setGeometry(m - pad, m - pad, ph.width(), ph.height())
            if not self._objects_user_set:
                want = W < 1000
                if want != self.objects.is_collapsed():
                    self.objects.set_collapsed(want, user=False)
            oy = (m - pad) + ph.height() - pad + SP - pad
            self.objects.set_max_height(max(60, chip_y - SP + pad - oy))
            self.objects.move(m - pad, oy)
            # Reserve the objects panel's width even when folded: its header pill
            # still covers that strip, and the plan's dimension labels sit there.
            left_edge = max((m - pad) + ph.width() - pad, (m - pad) + self.objects.width() - pad)
            iw = self.inspector.width()
            self.inspector.set_max_height(H - 2 * (m - pad))
            self.inspector.move(W - iw - (m - pad), m - pad)
            right_edge = iw + (m - pad) - pad
        # hint pill centred in the free strip at the bottom
        self.hint.adjustSize()
        hw = self.hint.sizeHint().width()
        free_l = max(chip.width() + 2 * m, left_edge + m) if self._edit_chrome else chip.width() + 2 * m
        free_r = W - (right_edge + m if self._edit_chrome else m)
        cx = (free_l + free_r) / 2
        hx = int(max(free_l, min(free_r - hw, cx - hw / 2)))
        self.hint.setGeometry(hx, H - m - 28, int(hw), 28)
        # never overlap the chrome: hide the hint when the free strip is too narrow
        self.hint.setVisible(bool(self.hint.text()) and hw <= free_r - free_l)
        # Tell the views which margins are covered (fit + HUD stay clear)
        ins = (left_edge + m if self._edit_chrome else 0, 0,
               right_edge + m if self._edit_chrome else 0, chip.height() + m)
        self.floor.set_view_insets(*ins)
        self.view3d.set_view_insets(*ins)
        self._raise_chrome()

    # ----- views -----
    def current_view(self) -> str:
        w = self.stack.currentWidget()
        return next((k for k, v in self._views.items() if v is w), VIEW_PLAN)

    def has_gl(self) -> bool:
        return self.gl is not None

    def set_view(self, key: str) -> None:
        if key not in self._views:
            key = VIEW_3D if key == VIEW_GL else VIEW_PLAN
        w = self._views[key]
        if self.stack.currentWidget() is not w:
            self.stack.setCurrentWidget(w)
            if key == VIEW_3D:
                self.view3d.update()
            elif key == VIEW_GL:
                self.gl.set_room(self.ctx.room)
                self.gl.fit_camera()
                if not getattr(self.gl, "_gl_ok", True) and getattr(self.gl, "_gl_error", ""):
                    self.ctx.say(f"OpenGL issue: {self.gl._gl_error} — try the 3D view", 8000)
        self.view_chip.set_current(key)
        if key != VIEW_PLAN and self.tool == TOOL_RESIZE:
            self.set_tool(TOOL_SELECT)
        if key == VIEW_GL and self.tool != TOOL_SELECT:
            self.set_tool(TOOL_SELECT)
        self._update_hint()
        self.viewChanged.emit(key)

    def set_tool(self, tool: str) -> None:
        if tool == TOOL_RESIZE and self.current_view() != VIEW_PLAN:
            self.set_view(VIEW_PLAN)  # resizing happens on the plan
        if tool in (TOOL_WINDOW, TOOL_SPEAKER, TOOL_LISTENER) and self.current_view() == VIEW_GL:
            self.set_view(VIEW_3D)    # the GPU view cannot place things
        self.tool = tool
        self.floor.set_tool(tool)
        self.view3d.set_tool(tool)
        if self.gl is not None:
            self.gl.set_tool(tool)
        if self.palette is not None:
            self.palette.set_tool(tool)
        self._update_hint()

    def fit_view(self) -> None:
        self.floor._fit_view()
        self.floor.update()
        self.view3d.fit_camera()
        if self.gl is not None:
            self.gl.fit_camera()

    def current_widget(self) -> QtWidgets.QWidget:
        return self.stack.currentWidget()

    def _update_hint(self) -> None:
        if not self._edit_chrome:
            text = _LISTEN_HINT
        else:
            hints = _HINTS.get(self.current_view(), _HINTS[VIEW_PLAN])
            text = hints.get(self.tool, hints.get(TOOL_SELECT, ""))
            if self.tool == TOOL_SELECT and self.current_view() == VIEW_3D:
                text = _SEL_HINTS_3D.get(self.ctx.selected[0], text)
        self.hint.setText(text)
        self._layout() if self.palette is not None else None

    # ----- sync -----
    def _from_view_selection(self, sel) -> None:
        self.ctx.select(tuple(sel), force=True)

    def _push_selection(self, sel) -> None:
        kind, idx = sel
        if self.floor.selection != sel:
            self.floor.set_selection(kind, idx, emit=False)
        if getattr(self.view3d, "_selection", None) != sel:
            self.view3d.set_selection(kind, idx, emit=False)
        if self.gl is not None:
            self.gl._selection = sel
            self.gl.update()
        if self.tool == TOOL_SELECT:
            self._update_hint()

    def _on_placed(self, kind: str, idx: int) -> None:
        # One placement per tool pick; the new thing is selected for editing.
        QtCore.QTimer.singleShot(0, lambda: self.set_tool(TOOL_SELECT))

    def _on_edited(self, area: str, source) -> None:
        if area == "view":
            return
        if area in ("sound",):
            self.view3d.update()
            return
        if area == "rain":
            self.view3d.update()
            self._reseed.start()
            return
        if area == "materials" and self.gl is not None:
            self.gl.set_roof(self.ctx.room.roof_material)
        self.floor.update()
        self.view3d.update()
        if self.gl is not None:
            self.gl.update()
            if area in ("house", "windows"):
                self._reseed.start()

    def flash_level(self, t: float) -> float:
        """How lit the sky is at engine time ``t`` (each flash fades in ~0.1 s)."""
        return self._lightning_at(t)[0]

    def _lightning_at(self, t: float):
        """(sky flash 0..1, live strikes for the views) at engine time ``t``."""
        eng = self.ctx.engine
        try:
            flashes = eng.lightning_flashes(t - 1.2, t + 1e-3)
        except Exception:
            return 0.0, []
        lit = 0.0
        for f in flashes:
            tf, b = f[0], f[1]
            if t - 0.6 <= tf <= t:
                lit = max(lit, b * math.exp(-(t - tf) / 0.09))
        return min(1.0, lit), lightning.active_strikes(flashes, t)

    def _update_flash(self) -> None:
        eng = self.ctx.engine
        playing = bool(getattr(eng, "running", False))
        stormy = float(getattr(self.ctx.room, "thunder_rate", 0.0) or 0.0) > 0.005
        v, strikes = 0.0, []
        if playing and stormy and self.current_view() != VIEW_PLAN:
            try:
                v, strikes = self._lightning_at(eng.playback_time())
            except Exception:
                v, strikes = 0.0, []
        for view in (self.view3d, self.gl):
            if view is None:
                continue
            if getattr(view, "flash", 0.0) != v:
                view.set_flash(v)
            if strikes or getattr(view, "strikes", None):
                view.set_lightning(strikes)

    def _reseed_rain(self) -> None:
        if self.gl is not None:
            try:
                self.gl._init_particles()
            except Exception:
                pass

    def room_replaced(self) -> None:
        room = self.ctx.room
        self.floor.set_room(room)
        self.view3d.set_room(room)
        self.view3d.fit_camera()
        if self.gl is not None:
            self.gl.set_room(room)
            self.gl.set_roof(room.roof_material)

    def _on_gl_status(self, msg: str) -> None:
        if not msg:
            return
        if msg.startswith("OpenGL OK"):
            self.ctx.say("OpenGL view ready", 2500)
        else:
            self.ctx.say(msg, 8000)
            log.warning("GL status: %s", msg)


__all__ = ["CanvasHost", "GL_AVAILABLE", "VIEW_3D", "VIEW_GL", "VIEW_PLAN", "configure_gl_surface_format"]
