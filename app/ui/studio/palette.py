"""Floating vertical tool palette (top-left of the canvas)."""

from __future__ import annotations

from typing import Dict

from PySide6 import QtCore, QtWidgets

from app.ui.floorplan import TOOL_LISTENER, TOOL_RESIZE, TOOL_SELECT, TOOL_SPEAKER, TOOL_WINDOW
from app.ui.studio.controls import FloatCard, hline
from app.ui.studio.icons import icon
from app.ui.theme import T

# key, icon, tooltip, shortcut letter
TOOLS = (
    (TOOL_SELECT, "select", "Select and move", "V"),
    (TOOL_WINDOW, "window", "Add a window — click a wall", "W"),
    (TOOL_SPEAKER, "speaker", "Add a speaker — click inside the house", "S"),
    (TOOL_LISTENER, "you", "Move You — click where you sit", "Y"),
    (TOOL_RESIZE, "resize", "Resize the house — drag a wall (floor plan)", "R"),
)
TOOL_KEYS = {letter: key for key, _i, _t, letter in TOOLS}


class ToolPalette(FloatCard):
    toolChosen = QtCore.Signal(str)
    fitRequested = QtCore.Signal()

    BTN = 38

    def __init__(self, parent=None):
        super().__init__(parent, radius=T.r_card)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self.buttons: Dict[str, QtWidgets.QToolButton] = {}
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        for key, ic, tip, letter in TOOLS:
            b = self._button(ic, f"{tip}  ({letter})")
            b.setCheckable(True)
            group.addButton(b)
            b.clicked.connect(lambda _=False, k=key: self.toolChosen.emit(k))
            self.buttons[key] = b
            lay.addWidget(b, 0, QtCore.Qt.AlignHCenter)
        sep = hline()
        sep.setFixedWidth(self.BTN - 8)
        lay.addWidget(sep, 0, QtCore.Qt.AlignHCenter)
        self.btn_fit = self._button("fit", "Fit the house in view  (F)")
        self.btn_fit.clicked.connect(self.fitRequested.emit)
        lay.addWidget(self.btn_fit, 0, QtCore.Qt.AlignHCenter)
        self.set_tool(TOOL_SELECT)

    def _button(self, ic: str, tip: str) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton()
        b.setObjectName("ToolBtn")
        b.setIcon(icon(ic, T.text2, 20, on_color=T.accent))
        b.setIconSize(QtCore.QSize(20, 20))
        b.setFixedSize(self.BTN, self.BTN)
        b.setToolTip(tip)
        b.setFocusPolicy(QtCore.Qt.NoFocus)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        return b

    def set_tool(self, tool: str) -> None:
        b = self.buttons.get(tool)
        if b is not None:
            b.setChecked(True)


__all__ = ["TOOLS", "TOOL_KEYS", "ToolPalette"]
