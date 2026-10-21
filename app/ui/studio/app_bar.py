"""Top app bar: wordmark, house name, Design | Listen, weather quick-pick, menus."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from app.ui.studio.context import StudioContext
from app.ui.studio.controls import SegmentedControl, button, hint, label, set_prop, vsep
from app.ui.studio.icons import icon
from app.ui.theme import SP, T, qcolor
from app.ui.widgets import PresetChips

MODE_DESIGN, MODE_LISTEN = "design", "listen"


class _WeatherPopup(QtWidgets.QWidget):
    """Content of the weather quick-pick menu: one chip per weather preset."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(SP)
        lay.addWidget(label("WEATHER", "Overline"))
        self.chips = PresetChips(columns=3)
        self.chips.setFixedWidth(300)
        lay.addWidget(self.chips)
        self.lbl_more = hint("Fine-tune amount, wind and sound in the panel on the right.")
        lay.addWidget(self.lbl_more)
        # When the panel is folded away, offer it instead of pointing at it
        self.btn_show_dock = button("Show rain && sound panel", "Link",
                                    "Open the panel with amount, wind and sound mix", "sliders", T.accent)
        self.btn_show_dock.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_show_dock.hide()
        lay.addWidget(self.btn_show_dock)
        self.setFixedWidth(324)

    def set_dock_shown(self, shown: bool) -> None:
        self.lbl_more.setVisible(shown)
        self.btn_show_dock.setVisible(not shown)


class PhoneButton(QtWidgets.QToolButton):
    """App-bar "Phone" button; a small count badge shows connected phones."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PhoneBtn")
        self.setText("Phone")
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.setIconSize(QtCore.QSize(18, 18))
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self._count = 0
        self._on = False
        self.set_state(False, 0)

    def set_state(self, on: bool, count: int) -> None:
        self._on, self._count = bool(on), max(0, int(count))
        self.setIcon(icon("phone", T.accent if on else T.text2, 18))
        set_prop(self, "on", self._on)
        if not on:
            tip = "Listen on a phone in another room (sharing is off)"
        elif self._count:
            tip = f"{self._count} phone{'s' if self._count != 1 else ''} connected — open to manage"
        else:
            tip = "Phones can join — open to show the code"
        self.setToolTip(tip)
        self.update()

    def badge_count(self) -> int:
        return self._count if self._on else 0

    def paintEvent(self, e):
        super().paintEvent(e)
        n = self.badge_count()
        if n <= 0:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        f = p.font()
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        txt = str(n) if n < 10 else "9+"
        w = max(15, p.fontMetrics().horizontalAdvance(txt) + 8)
        # top-right of the icon (the icon sits after 8 px of padding)
        cx = 8 + 18 + 1
        r = QtCore.QRectF(cx - w / 2.0, self.height() / 2.0 - 15, w, 15)
        p.setPen(QtGui.QPen(qcolor(T.surface), 2))
        p.setBrush(qcolor(T.accent))
        p.drawRoundedRect(r, 7.5, 7.5)
        p.setPen(qcolor(T.accent_text))
        p.drawText(r, QtCore.Qt.AlignCenter, txt)
        p.end()


class AppBar(QtWidgets.QFrame):
    modeChanged = QtCore.Signal(str)
    nameEdited = QtCore.Signal(str)

    def __init__(self, ctx: StudioContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("AppBar")
        self.setFixedHeight(56)
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(16, 0, 12, 0)
        grid.setHorizontalSpacing(SP)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)

        # ---- left: brand + house name ----
        left = QtWidgets.QHBoxLayout()
        left.setSpacing(SP)
        mark = QtWidgets.QLabel()
        mark.setPixmap(icon("droplet_fill", T.accent, 22, stroke=1.5).pixmap(22, 22))
        left.addWidget(mark)
        left.addWidget(label("RainRoom", "Wordmark"))
        left.addSpacing(4)
        left.addWidget(vsep(24))
        left.addSpacing(2)
        self.ed_name = QtWidgets.QLineEdit()
        self.ed_name.setObjectName("HouseName")
        self.ed_name.setPlaceholderText("House name")
        self.ed_name.setToolTip("Your house’s name — click to rename")
        self.ed_name.setMinimumWidth(120)
        self.ed_name.setMaximumWidth(260)
        self.ed_name.textEdited.connect(self._on_name)
        self.ed_name.returnPressed.connect(self.ed_name.clearFocus)
        left.addWidget(self.ed_name)
        self.lbl_unsaved = label("", "UnsavedDot")
        self.lbl_unsaved.setToolTip("Unsaved changes — File › Save (Ctrl+S)")
        left.addWidget(self.lbl_unsaved)
        left.addStretch(1)
        grid.addLayout(left, 0, 0)

        # ---- centre: mode ----
        self.seg_mode = SegmentedControl(accent=True)
        self.seg_mode.add(MODE_DESIGN, "Design", "Shape the house, place windows and speakers", "pencil")
        self.seg_mode.add(MODE_LISTEN, "Listen", "Hide the editing tools and focus on the rain", "headphones")
        self.seg_mode.changed.connect(self.modeChanged.emit)
        grid.addWidget(self.seg_mode, 0, 1, QtCore.Qt.AlignCenter)

        # ---- right: weather, file, settings ----
        right = QtWidgets.QHBoxLayout()
        right.setSpacing(6)
        right.addStretch(1)
        self.btn_weather = QtWidgets.QToolButton()
        self.btn_weather.setObjectName("WeatherPick")
        self.btn_weather.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.btn_weather.setIcon(icon("cloud_rain", T.accent, 18))
        self.btn_weather.setIconSize(QtCore.QSize(18, 18))
        self.btn_weather.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.btn_weather.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_weather.setToolTip("Pick the weather")
        self.btn_weather.setFocusPolicy(QtCore.Qt.NoFocus)
        self.weather_menu = QtWidgets.QMenu(self.btn_weather)
        self.weather_popup = _WeatherPopup()
        act = QtWidgets.QWidgetAction(self.weather_menu)
        act.setDefaultWidget(self.weather_popup)
        self.weather_menu.addAction(act)
        self.btn_weather.setMenu(self.weather_menu)
        right.addWidget(self.btn_weather)
        right.addSpacing(6)

        self.btn_phone = PhoneButton()
        right.addWidget(self.btn_phone)

        self.btn_file = self._bar_button("File", "File: new, open, save, examples")
        self.file_menu = QtWidgets.QMenu(self.btn_file)
        self.file_menu.setLayoutDirection(QtCore.Qt.LeftToRight)  # the button is RTL (chevron last)
        self.btn_file.setMenu(self.file_menu)
        right.addWidget(self.btn_file)

        self.btn_gear = QtWidgets.QToolButton()
        self.btn_gear.setObjectName("IconBtn")
        self.btn_gear.setIcon(icon("gear", T.text2, 20))
        self.btn_gear.setIconSize(QtCore.QSize(20, 20))
        self.btn_gear.setFixedSize(36, 36)
        self.btn_gear.setToolTip("Audio setup, help and about")
        self.btn_gear.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.btn_gear.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_gear.setFocusPolicy(QtCore.Qt.NoFocus)
        self.gear_menu = QtWidgets.QMenu(self.btn_gear)
        self.btn_gear.setMenu(self.gear_menu)
        right.addWidget(self.btn_gear)
        grid.addLayout(right, 0, 2)

        ctx.dirtyChanged.connect(self._on_dirty)
        ctx.roomReplaced.connect(self.load_name)
        self.load_name()

    def _bar_button(self, text: str, tip: str) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton()
        b.setObjectName("BarButton")
        b.setText(text)
        b.setToolTip(tip)
        b.setIcon(icon("chevron_down", T.text2, 14))
        b.setIconSize(QtCore.QSize(14, 14))
        b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        b.setLayoutDirection(QtCore.Qt.RightToLeft)  # chevron after the text
        b.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setFocusPolicy(QtCore.Qt.NoFocus)
        return b

    # ----- name -----
    def load_name(self) -> None:
        self.ed_name.blockSignals(True)
        self.ed_name.setText(self.ctx.room.name)
        self.ed_name.setCursorPosition(0)
        self.ed_name.blockSignals(False)

    def _on_name(self, text: str) -> None:
        self.ctx.room.name = text
        self.ctx.edited("name", self)
        self.nameEdited.emit(text)

    def _on_dirty(self, on: bool) -> None:
        self.lbl_unsaved.setText("●  Unsaved" if on else "")

    # ----- weather -----
    def set_weather(self, preset_name, rate_text: str) -> None:
        name = preset_name or PresetChips.CUSTOM
        self.btn_weather.setText(f"{name} · {rate_text}")
        self.weather_popup.chips.set_current(preset_name)

    def weather_text(self) -> str:
        return self.btn_weather.text()

    # ----- phones -----
    def set_phone_state(self, on: bool, count: int) -> None:
        self.btn_phone.set_state(on, count)


__all__ = ["AppBar", "MODE_DESIGN", "MODE_LISTEN", "PhoneButton"]
