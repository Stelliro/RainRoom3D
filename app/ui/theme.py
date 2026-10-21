"""RainRoom dark theme: one set of design tokens, one stylesheet.

Every colour, radius and type size used by the app lives in ``T`` (and the
canvas colours in ``C``). Paint code (floor plan, wheels, icons) reads the
same tokens, so a palette change is a one-line edit here.

Spacing follows an 8 px grid (``SP``); radii are 8 (controls) and 12 (cards).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from string import Template

from PySide6 import QtGui


class T:
    """Design tokens (dark)."""

    # Surfaces, from the back of the window to the front
    bg = "#0b0e13"          # window + canvas backdrop
    surface = "#10141b"     # app bar, dock, transport
    raised = "#161b24"      # floating cards
    overlay = "#1b212b"     # inputs, menus, popups
    hover = "#232a35"
    pressed = "#191e27"

    border = "#232b37"
    border_strong = "#323c4b"

    text = "#e7ecf3"
    text2 = "#a1abba"
    text3 = "#6f7a8a"
    disabled = "#4a5260"

    accent = "#4cb8ff"
    accent_hover = "#6dc6ff"
    accent_press = "#38a2e8"
    accent_text = "#04131f"          # text on an accent fill
    accent_soft = "rgba(76,184,255,0.14)"
    accent_line = "rgba(76,184,255,0.55)"

    danger = "#f47174"
    danger_soft = "rgba(244,113,116,0.12)"
    success = "#47d18c"
    success_soft = "rgba(71,209,140,0.14)"
    warning = "#f5b84a"

    # Object colours (shared by canvas, objects list and inspector)
    window = "#2dd4bf"
    speaker = "#f59e0b"
    you = "#4cb8ff"

    # Radii
    r_ctl = 8
    r_card = 12

    # Type scale (px)
    fs_overline = 11
    fs_caption = 12
    fs_body = 13
    fs_strong = 14
    fs_title = 16
    fs_brand = 17

    font = '"Segoe UI Variable Text", "Segoe UI", "Inter", system-ui, sans-serif'


SP = 8  # spacing unit: use SP, 2*SP, 3*SP ...


class C:
    """Canvas palette (floor plan + software 3D + wheels)."""

    backdrop_top = "#0d1219"
    backdrop_bottom = "#090c11"
    terrain_a = "#122019"
    terrain_b = "#0e1a14"
    terrain_edge = "#1f3a2b"
    terrain_label = "#3c6650"
    grid = (44, 66, 54, 70)
    floor_a = "#1b2533"
    floor_b = "#151d29"
    floor_hatch = (110, 140, 180, 22)
    wall = "#7fa9d6"
    dim_text = "#6f7a8a"
    north = T.accent
    name_text = "#c9d4e3"
    window = T.window
    window_sel = "#7af0dd"
    window_text = "#9ff3e6"
    rain_glow = (80, 180, 255)
    speaker_on = T.speaker
    speaker_sel = "#fcc85a"
    speaker_unassigned = "#7d7770"
    speaker_off = "#3a404c"
    speaker_text = "#fde2a6"
    you = T.you
    you_sel = "#8fd3ff"
    ok_text = "#8de3b5"
    warn_text = T.warning   # "no device" etc.: the same warning colour as the objects list
    wheel_disc = "#151a22"
    wheel_ring = "#2a3240"
    wheel_tick = "#3b4555"
    wheel_tick_major = "#6b7688"
    wheel_label = "#a1abba"


def qcolor(token: str) -> QtGui.QColor:
    """QColor from a token string (hex or rgba(...))."""
    s = str(token).strip()
    if s.startswith("rgba"):
        parts = [p.strip() for p in s[s.index("(") + 1:s.index(")")].split(",")]
        r, g, b = (int(float(x)) for x in parts[:3])
        a = float(parts[3]) if len(parts) > 3 else 1.0
        return QtGui.QColor(r, g, b, int(round(a * 255 if a <= 1.0 else a)))
    return QtGui.QColor(s)


_QSS = Template(r"""
* { outline: none; }
QWidget {
    color: $text;
    font-family: $font;
    font-size: ${fs_body}px;
}
QMainWindow, QDialog { background-color: $bg; }
QWidget#Central { background-color: $bg; }

/* ---------- menus + tooltips ---------- */
QMenu {
    background-color: $overlay;
    border: 1px solid $border_strong;
    border-radius: ${r_card}px;
    padding: 6px;
}
QMenu::item {
    padding: 7px 28px 7px 12px;
    border-radius: 6px;
    color: $text;
    background: transparent;
}
QMenu::item:selected { background: $hover; }
QMenu::item:disabled { color: $disabled; }
QMenu::separator { height: 1px; background: $border; margin: 6px 8px; }
QMenu::right-arrow { image: url($arrow_right); width: 10px; height: 10px; margin-right: 6px; }
QToolTip {
    background-color: $overlay;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: ${fs_caption}px;
}

/* ---------- typography ---------- */
QLabel { background: transparent; }
QLabel#Wordmark { font-size: ${fs_brand}px; font-weight: 700; color: $text; letter-spacing: -0.2px; }
QLabel#Overline {
    font-size: ${fs_overline}px; font-weight: 700; letter-spacing: 1.2px; color: $text3;
}
QLabel#Caption { font-size: ${fs_caption}px; color: $text2; }
QLabel#Hint { font-size: ${fs_caption}px; color: $text3; }
QLabel#CardTitle { font-size: ${fs_title}px; font-weight: 600; color: $text; }
QLabel#CardSub { font-size: ${fs_caption}px; color: $text2; }
QLabel#DockTitle { font-size: ${fs_strong}px; font-weight: 600; color: $text; }
QLabel#SetName { font-weight: 600; color: $text; }
QLabel#SetValue { font-size: ${fs_caption}px; color: $text2; }
QLabel#EndCap { font-size: 11px; color: $text3; }
QLabel#FieldLabel { color: $text2; }
QLabel#Acoustics {
    font-size: ${fs_caption}px; color: $text2;
    background: $overlay; border: 1px solid $border; border-radius: ${r_ctl}px;
    padding: 8px 10px;
}
QLabel#HintPill {
    background-color: rgba(16,20,27,0.92);
    border: 1px solid $border;
    border-radius: 14px;
    padding: 5px 14px;
    color: $text2;
    font-size: ${fs_caption}px;
}
QLabel#MsgLabel { color: $text2; font-size: ${fs_caption}px; }
QLabel#UnsavedDot { color: $warning; font-size: ${fs_caption}px; }
QFrame#Dock[large="true"] QLabel#SetName { font-size: 15px; }
QFrame#Dock[large="true"] QLabel#SetValue { font-size: ${fs_body}px; }
/* A disabled row must look disabled: the named rules above set explicit colours,
   which would otherwise beat the palette's disabled text colour. */
QLabel:disabled, QLabel#SetName:disabled, QLabel#SetValue:disabled, QLabel#EndCap:disabled,
QLabel#FieldLabel:disabled, QLabel#Caption:disabled, QLabel#Hint:disabled,
QLabel#Acoustics:disabled, QLabel#Overline:disabled { color: $disabled; }

/* ---------- shell ---------- */
QFrame#AppBar { background-color: $surface; border-bottom: 1px solid $border; }
QFrame#Transport { background-color: $surface; border-top: 1px solid $border; }
QFrame#Dock { background-color: $surface; border-left: 1px solid $border; }
QFrame#DockHeader { background: transparent; border-bottom: 1px solid $border; }
QWidget#CanvasHost { background-color: $bg; }
QFrame#VSep { background: $border; border: none; }
QFrame#HLine { background: $border; border: none; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget#ScrollInner { background: transparent; }
QWidget#ScrollInner { background: transparent; }
QStackedWidget { background: transparent; }

/* ---------- buttons ---------- */
QPushButton, QToolButton#Button {
    background-color: $overlay;
    border: 1px solid $border_strong;
    border-radius: ${r_ctl}px;
    padding: 0 12px;
    min-height: 30px;
    color: $text;
    font-weight: 600;
}
QPushButton:hover, QToolButton#Button:hover { background-color: $hover; border-color: #3d4859; }
QPushButton:pressed, QToolButton#Button:pressed { background-color: $pressed; }
QPushButton:focus { border-color: $accent_line; }
QPushButton:disabled, QToolButton#Button:disabled {
    color: $disabled; background-color: $surface; border-color: $border;
}
QPushButton#Primary {
    background-color: $accent; border: 1px solid $accent; color: $accent_text;
}
QPushButton#Primary:hover { background-color: $accent_hover; border-color: $accent_hover; }
QPushButton#Primary:pressed { background-color: $accent_press; }
QPushButton#Primary:disabled { background-color: $overlay; border-color: $border; color: $disabled; }
QPushButton#Danger {
    background-color: transparent; border: 1px solid rgba(244,113,116,0.35); color: $danger;
}
QPushButton#Danger:hover { background-color: $danger_soft; border-color: $danger; }
QPushButton#Danger:disabled { color: $disabled; border-color: $border; background: transparent; }
QPushButton#Ghost {
    background: transparent; border: 1px solid transparent; color: $text2; padding: 0 8px;
}
QPushButton#Ghost:hover { background: $hover; color: $text; }
QPushButton#Ghost:disabled { color: $disabled; background: transparent; }
QPushButton#Link {
    background: transparent; border: none; color: $accent; padding: 0 2px; min-height: 22px;
    font-weight: 600; text-align: left;
}
QPushButton#Link:hover { color: $accent_hover; text-decoration: underline; }
QPushButton#Link:disabled { color: $disabled; }

/* Play / Stop */
QPushButton#PlayBtn {
    background-color: $accent; border: 1px solid $accent; color: $accent_text;
    border-radius: 20px; min-height: 40px; max-height: 40px; padding: 0 20px 0 16px;
    font-size: ${fs_strong}px; font-weight: 700;
}
QPushButton#PlayBtn:hover { background-color: $accent_hover; border-color: $accent_hover; }
QPushButton#PlayBtn:pressed { background-color: $accent_press; }
QPushButton#PlayBtn[playing="true"] {
    background-color: $overlay; border: 1px solid $border_strong; color: $text;
}
QPushButton#PlayBtn[playing="true"]:hover { background-color: $hover; }
QPushButton#PlayBtn[large="true"] {
    min-height: 44px; max-height: 44px; border-radius: 22px; padding: 0 26px 0 20px; font-size: 15px;
}

/* Icon-only buttons */
QToolButton#IconBtn {
    background: transparent; border: 1px solid transparent; border-radius: ${r_ctl}px;
    padding: 4px;
}
QToolButton#IconBtn:hover { background: $hover; }
QToolButton#IconBtn:pressed { background: $pressed; }
QToolButton#IconBtn:checked { background: $accent_soft; border-color: $accent_line; }
QToolButton#IconBtn::menu-indicator { image: none; width: 0; }

/* App bar buttons */
QToolButton#BarButton {
    background: transparent; border: 1px solid transparent; border-radius: ${r_ctl}px;
    padding: 0 10px; min-height: 32px; color: $text; font-weight: 600;
}
QToolButton#BarButton:hover { background: $hover; }
QToolButton#BarButton:pressed, QToolButton#BarButton:open { background: $pressed; }
QToolButton#BarButton::menu-indicator { image: none; width: 0; }
QToolButton#WeatherPick {
    background: $overlay; border: 1px solid $border_strong; border-radius: 16px;
    padding: 0 14px 0 10px; min-height: 32px; color: $text; font-weight: 600;
}
QToolButton#WeatherPick:hover { background: $hover; border-color: #3d4859; }
QToolButton#WeatherPick::menu-indicator { image: none; width: 0; }

/* Palette tools */
QToolButton#ToolBtn {
    background: transparent; border: 1px solid transparent; border-radius: ${r_ctl}px;
}
QToolButton#ToolBtn:hover { background: $hover; }
QToolButton#ToolBtn:checked { background: $accent_soft; border-color: $accent_line; }
QToolButton#ToolBtn:disabled { background: transparent; }

/* Segmented control */
QFrame#Segmented {
    background: $bg; border: 1px solid $border; border-radius: ${r_ctl}px;
}
QFrame#Segmented[floating="true"] { background: rgba(16,20,27,0.94); border-color: $border_strong; }
QPushButton#SegButton {
    background: transparent; border: none; border-radius: 6px;
    padding: 0 14px; min-height: 26px; max-height: 26px; color: $text2; font-weight: 600;
}
QPushButton#SegButton:hover { color: $text; background: $hover; }
QPushButton#SegButton:checked { background: $hover; color: $text; }
QFrame#Segmented[accent="true"] QPushButton#SegButton:checked { background: $accent_soft; color: $accent; }
QPushButton#SegButton:disabled { color: $disabled; }

/* Preset chips */
QPushButton#Chip {
    background: $overlay; border: 1px solid $border_strong; border-radius: 14px;
    padding: 0 12px; min-height: 26px; max-height: 26px; font-weight: 600; color: $text2;
}
QPushButton#Chip:hover { color: $text; background: $hover; }
QPushButton#Chip:checked { background: $accent_soft; border-color: $accent_line; color: $accent; }
QPushButton#Chip[custom="true"]:checked { background: $overlay; border-color: $border_strong; color: $text; }

/* Edge tab for the collapsed dock */
QToolButton#EdgeTab {
    background: rgba(76,184,255,0.07); border: none; border-left: 1px solid $accent_line; color: $accent;
}
QToolButton#EdgeTab:hover { background: $accent_soft; color: $text; }

/* ---------- inputs ---------- */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: $overlay;
    border: 1px solid $border_strong;
    border-radius: ${r_ctl}px;
    padding: 0 10px;
    min-height: 30px;
    color: $text;
    selection-background-color: $accent;
    selection-color: $accent_text;
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover { border-color: #3f4a5b; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border-color: $accent; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {
    color: $disabled; background-color: $surface; border-color: $border;
}
QLineEdit#HouseName {
    background: transparent; border: 1px solid transparent; border-radius: ${r_ctl}px;
    font-size: ${fs_strong}px; font-weight: 600; padding: 0 8px; min-height: 32px;
}
QLineEdit#HouseName:hover { background: $overlay; border-color: $border; }
QLineEdit#HouseName:focus { background: $overlay; border-color: $accent; }
QAbstractSpinBox { padding-right: 22px; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    subcontrol-origin: border; width: 20px; border: none; background: transparent;
}
QAbstractSpinBox::up-button { subcontrol-position: top right; margin: 3px 3px 0 0; }
QAbstractSpinBox::down-button { subcontrol-position: bottom right; margin: 0 3px 3px 0; }
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover { background: $hover; border-radius: 4px; }
QAbstractSpinBox::up-arrow { image: url($arrow_up); width: 10px; height: 10px; }
QAbstractSpinBox::down-arrow { image: url($arrow_down); width: 10px; height: 10px; }
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off { image: url($arrow_up_dis); }
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off { image: url($arrow_down_dis); }
QComboBox { padding-right: 28px; }
QComboBox::drop-down { border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: top right; }
QComboBox::down-arrow { image: url($arrow_down); width: 12px; height: 12px; margin-right: 8px; }
QComboBox::down-arrow:disabled { image: url($arrow_down_dis); }
QComboBox QAbstractItemView {
    background-color: $overlay;
    border: 1px solid $border_strong;
    border-radius: ${r_ctl}px;
    padding: 4px;
    selection-background-color: $hover;
    selection-color: $text;
    outline: none;
}

/* ---------- sliders ---------- */
QSlider { min-height: 22px; background: transparent; }
QSlider::groove:horizontal { height: 4px; background: $border_strong; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::sub-page:horizontal:disabled { background: $disabled; }
QSlider::handle:horizontal {
    width: 16px; height: 16px; margin: -6px 0;
    background: $text; border: 2px solid $accent; border-radius: 8px;
}
QSlider::handle:horizontal:hover { background: #ffffff; border-color: $accent_hover; }
QSlider::handle:horizontal:disabled { background: $overlay; border-color: $disabled; }
QFrame#Dock[large="true"] QSlider::groove:horizontal { height: 6px; border-radius: 3px; }
QFrame#Dock[large="true"] QSlider::handle:horizontal { width: 20px; height: 20px; margin: -7px 0; border-radius: 10px; }
QSlider#OpenSlider::groove:horizontal { height: 8px; border-radius: 4px; }
QSlider#OpenSlider::sub-page:horizontal { background: $window; border-radius: 4px; }
QSlider#OpenSlider::handle:horizontal { width: 22px; height: 22px; margin: -7px 0; border-radius: 11px; border-color: $window; }

/* ---------- checkbox (plain) ---------- */
QCheckBox { spacing: 10px; color: $text; background: transparent; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid $border_strong; background: $overlay;
}
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }

/* ---------- lists ---------- */
QListWidget {
    background: transparent; border: none; outline: none; padding: 0;
}
QListWidget#DeviceList {
    background: $overlay; border: 1px solid $border; border-radius: ${r_ctl}px; padding: 4px;
}
QListWidget#DeviceList::item { padding: 7px 8px; border-radius: 6px; }
QListWidget#DeviceList::item:selected { background: $accent_soft; color: $text; }
QListWidget#DeviceList::item:hover:!selected { background: $hover; }

/* ---------- collapsible sections ---------- */
QToolButton#SectionHeader, QToolButton#SubSectionHeader {
    background: transparent; border: none; color: $text; text-align: left;
    font-weight: 700; font-size: ${fs_overline}px; letter-spacing: 1.2px;
    padding: 6px 0; min-height: 24px;
}
QToolButton#SectionHeader { border-top: 1px solid $border; padding-top: 14px; }
QToolButton#SectionHeader:hover { color: $accent; }
QToolButton#SubSectionHeader {
    color: $text2; font-weight: 600; font-size: ${fs_caption}px; letter-spacing: 0; padding: 4px 0;
}
QToolButton#SubSectionHeader:hover { color: $text; }

/* ---------- group box (custom window design) ---------- */
QGroupBox {
    background: transparent; border: 1px solid $border; border-radius: ${r_ctl}px;
    margin-top: 10px; padding: 12px 10px 10px 10px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; color: $text2; font-size: ${fs_caption}px; }

/* ---------- scroll bars ---------- */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 3px; min-height: 32px; margin: 0 2px; }
QScrollBar::handle:vertical:hover { background: #45505f; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 0; }

/* ---------- dialogs ---------- */
QMessageBox, QInputDialog, QFileDialog { background-color: $raised; }
QDialog#OutputSetup, QDialog#PhoneDialog { background-color: $raised; }
QLabel#QrCode { background: #ffffff; border-radius: 10px; }
QFrame#PhoneRow { background: $overlay; border: 1px solid $border; border-radius: ${r_ctl}px; }
QFrame#PhoneRow QLabel#Caption { font-size: ${fs_caption}px; }

/* App bar: Phone button (badge painted by the button) */
QToolButton#PhoneBtn {
    background: transparent; border: 1px solid transparent; border-radius: ${r_ctl}px;
    padding: 0 10px 0 8px; min-height: 32px; color: $text; font-weight: 600;
}
QToolButton#PhoneBtn:hover { background: $hover; }
QToolButton#PhoneBtn:pressed { background: $pressed; }
QToolButton#PhoneBtn[on="true"] { color: $accent; }
""")


_ARROWS = {
    "arrow_up": ("M2 8 L6 4 L10 8", T.text2),
    "arrow_down": ("M2 4 L6 8 L10 4", T.text2),
    "arrow_right": ("M4 2 L8 6 L4 10", T.text2),
    "arrow_up_dis": ("M2 8 L6 4 L10 8", T.disabled),
    "arrow_down_dis": ("M2 4 L6 8 L10 4", T.disabled),
}


def _arrow_files() -> dict:
    """Tiny SVG chevrons for spin boxes / combos / menus (stylesheets need files)."""
    d = Path(tempfile.gettempdir()) / "rainroom_ui"
    out = {}
    try:
        d.mkdir(parents=True, exist_ok=True)
        for name, (path, col) in _ARROWS.items():
            f = d / f"{name}_{col.strip('#')}.svg"
            if not f.is_file():
                f.write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
                    f'<path d="{path}" fill="none" stroke="{col}" stroke-width="1.6" '
                    'stroke-linecap="round" stroke-linejoin="round"/></svg>', encoding="utf-8")
            out[name] = f.as_posix()
    except OSError:
        out = {name: "none" for name in _ARROWS}
    return out


def _qss() -> str:
    vals = {k: getattr(T, k) for k in dir(T) if not k.startswith("_")}
    vals.update(_arrow_files())
    return _QSS.substitute(vals)


APP_STYLESHEET = _qss()


def apply_theme(app) -> None:
    """Fusion + palette + stylesheet, so native-drawn bits match the tokens."""
    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    pal.setColor(QtGui.QPalette.Window, qcolor(T.bg))
    pal.setColor(QtGui.QPalette.WindowText, qcolor(T.text))
    pal.setColor(QtGui.QPalette.Base, qcolor(T.overlay))
    pal.setColor(QtGui.QPalette.AlternateBase, qcolor(T.raised))
    pal.setColor(QtGui.QPalette.Text, qcolor(T.text))
    pal.setColor(QtGui.QPalette.Button, qcolor(T.overlay))
    pal.setColor(QtGui.QPalette.ButtonText, qcolor(T.text))
    pal.setColor(QtGui.QPalette.Highlight, qcolor(T.accent))
    pal.setColor(QtGui.QPalette.HighlightedText, qcolor(T.accent_text))
    pal.setColor(QtGui.QPalette.ToolTipBase, qcolor(T.overlay))
    pal.setColor(QtGui.QPalette.ToolTipText, qcolor(T.text))
    pal.setColor(QtGui.QPalette.PlaceholderText, qcolor(T.text3))
    pal.setColor(QtGui.QPalette.Link, qcolor(T.accent))
    for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
        pal.setColor(QtGui.QPalette.Disabled, role, qcolor(T.disabled))
    app.setPalette(pal)
    app.setStyleSheet(APP_STYLESHEET)


__all__ = ["APP_STYLESHEET", "C", "SP", "T", "apply_theme", "qcolor"]
