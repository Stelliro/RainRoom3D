
"""RainRoom3D dark UI theme — polished product chrome."""

# Cohesive palette:
#   bg base #0a0e14 · surface #121a24 · elevated #1a2433
#   accent rain-blue #38bdf8 · teal windows #2dd4bf · amber speakers #f59e0b

APP_STYLESHEET = """
* {
    outline: none;
}
QWidget {
    background-color: #0a0e14;
    color: #e2e8f0;
    font-family: "Segoe UI Variable", "Segoe UI", "Inter", system-ui, sans-serif;
    font-size: 13px;
}
QMainWindow, QDialog {
    background-color: #0a0e14;
}
QMenuBar {
    background-color: #0c1118;
    color: #94a3b8;
    border-bottom: 1px solid #1e293b;
    padding: 2px 6px;
    spacing: 4px;
}
QMenuBar::item {
    padding: 6px 12px;
    border-radius: 6px;
    background: transparent;
}
QMenuBar::item:selected {
    background: #1e293b;
    color: #f1f5f9;
}
QMenu {
    background-color: #121a24;
    border: 1px solid #2a3a4f;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    padding: 8px 28px 8px 14px;
    border-radius: 6px;
}
QMenu::item:selected {
    background: #1e3a5f;
    color: #ffffff;
}
QToolTip {
    background-color: #1a2433;
    color: #f1f5f9;
    border: 1px solid #3b4f68;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 12px;
}

/* —— Typography —— */
QLabel#Title {
    font-size: 22px;
    font-weight: 700;
    color: #f8fafc;
    letter-spacing: -0.3px;
}
QLabel#BrandMark {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    color: #38bdf8;
    padding: 0;
}
QLabel#Subtitle {
    color: #94a3b8;
    font-size: 12.5px;
    line-height: 1.35;
}
QLabel#Section {
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 1.4px;
    color: #64748b;
    padding-top: 14px;
    padding-bottom: 4px;
}
QLabel#Badge {
    background-color: #1e3a5f;
    color: #7dd3fc;
    border: 1px solid #2563a8;
    border-radius: 999px;
    padding: 3px 10px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
}
QLabel#LiveOk {
    color: #4ade80;
    font-weight: 600;
}
QLabel#LiveIdle {
    color: #64748b;
    font-weight: 500;
}

/* —— Shell —— */
QFrame#Sidebar {
    background-color: #0c1219;
    border-right: 1px solid #1e293b;
}
QFrame#Card {
    background-color: #121a24;
    border: 1px solid #243041;
    border-radius: 14px;
}
QFrame#InspectorChrome {
    background-color: #0d131b;
    border-left: 1px solid #1e293b;
}
QFrame#ToolBar {
    background-color: #0f1520;
    border: 1px solid #1e293b;
    border-radius: 12px;
}
QFrame#HeroBar {
    background-color: transparent;
    border: none;
}

/* —— Buttons —— */
QPushButton {
    background-color: #162032;
    border: 1px solid #2a3a50;
    border-radius: 10px;
    padding: 9px 16px;
    color: #e2e8f0;
    font-weight: 600;
    min-height: 20px;
}
QPushButton:hover {
    background-color: #1c2a40;
    border-color: #38bdf8;
    color: #f8fafc;
}
QPushButton:pressed {
    background-color: #122030;
}
QPushButton:disabled {
    color: #475569;
    background-color: #101722;
    border-color: #1e293b;
}
QPushButton#Primary {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #0ea5e9, stop:1 #0284c7);
    border-color: #38bdf8;
    color: #ffffff;
}
QPushButton#Primary:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #38bdf8, stop:1 #0ea5e9);
}
QPushButton#Danger {
    background-color: #2a1518;
    border-color: #7f1d1d;
    color: #fca5a5;
}
QPushButton#Danger:hover {
    background-color: #3b1c20;
    border-color: #ef4444;
}
QPushButton#Success {
    background-color: #0f291a;
    border-color: #166534;
    color: #86efac;
}
QPushButton#Success:hover {
    background-color: #14532d;
    border-color: #22c55e;
}
QPushButton#ToolActive {
    background-color: #0c2a4a;
    border-color: #38bdf8;
    color: #e0f2fe;
}
QPushButton#Ghost {
    background: transparent;
    border: 1px solid transparent;
    color: #94a3b8;
}
QPushButton#Ghost:hover {
    background: #162032;
    border-color: #2a3a50;
    color: #e2e8f0;
}

/* —— Lists / nav —— */
QListWidget {
    background-color: #0f1520;
    border: 1px solid #1e293b;
    border-radius: 12px;
    outline: none;
    padding: 6px;
}
QListWidget#NavList {
    background-color: transparent;
    border: none;
    padding: 0;
}
QListWidget#NavList::item {
    padding: 12px 14px;
    margin: 3px 0;
    border-radius: 10px;
    color: #94a3b8;
    font-weight: 600;
}
QListWidget#NavList::item:selected {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0c2a4a, stop:1 #12263a);
    color: #e0f2fe;
    border: 1px solid #1e4a72;
}
QListWidget#NavList::item:hover:!selected {
    background-color: #121a24;
    color: #e2e8f0;
}
QListWidget::item {
    padding: 10px 12px;
    border-radius: 8px;
    margin: 1px 0;
}
QListWidget::item:selected {
    background-color: #0c2a4a;
    color: #e0f2fe;
    border: 1px solid #1e4a72;
}
QListWidget::item:hover:!selected {
    background-color: #162032;
}

/* —— Inputs —— */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: #0f1520;
    border: 1px solid #2a3a50;
    border-radius: 10px;
    padding: 7px 12px;
    min-height: 30px;
    color: #f1f5f9;
    selection-background-color: #0ea5e9;
    selection-color: #ffffff;
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
    border-color: #38bdf8;
    background-color: #121a24;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border-color: #38bdf8;
    background-color: #121a24;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: #475569;
    background-color: #0a0e14;
    border-color: #1e293b;
}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    width: 20px;
    border: none;
    background: transparent;
    subcontrol-origin: border;
}
QComboBox::drop-down {
    border: none;
    width: 28px;
    subcontrol-origin: padding;
    subcontrol-position: top right;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #94a3b8;
    width: 0;
    height: 0;
    margin-right: 10px;
}
QComboBox QAbstractItemView {
    background-color: #121a24;
    border: 1px solid #2a3a50;
    border-radius: 10px;
    selection-background-color: #0c2a4a;
    selection-color: #e0f2fe;
    padding: 4px;
    outline: none;
}

/* —— Sliders —— */
QSlider::groove:horizontal {
    height: 6px;
    background: #1e293b;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 18px;
    height: 18px;
    margin: -7px 0;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #7dd3fc, stop:1 #0ea5e9);
    border: 2px solid #e0f2fe;
    border-radius: 10px;
}
QSlider::handle:horizontal:hover {
    background: #38bdf8;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0284c7, stop:1 #38bdf8);
    border-radius: 3px;
}

/* —— Checkbox —— */
QCheckBox {
    spacing: 10px;
    color: #cbd5e1;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1.5px solid #334155;
    background: #0f1520;
}
QCheckBox::indicator:hover {
    border-color: #38bdf8;
}
QCheckBox::indicator:checked {
    background: #0ea5e9;
    border-color: #38bdf8;
}

/* —— Group / scroll —— */
QGroupBox {
    background-color: #0f1520;
    border: 1px solid #1e293b;
    border-radius: 12px;
    margin-top: 14px;
    padding: 16px 12px 12px 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 8px;
    color: #94a3b8;
    font-size: 11px;
    letter-spacing: 0.4px;
}
QScrollArea {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: transparent;
    width: 11px;
    margin: 4px 2px;
}
QScrollBar::handle:vertical {
    background: #2a3a50;
    border-radius: 5px;
    min-height: 32px;
}
QScrollBar::handle:vertical:hover {
    background: #3b5270;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    height: 0;
}

/* —— Tabs / status / splitter —— */
QTabWidget::pane {
    border: 1px solid #1e293b;
    border-radius: 12px;
    background: #0f1520;
    top: -1px;
}
QTabBar::tab {
    background: #121a24;
    border: 1px solid #1e293b;
    border-bottom: none;
    padding: 9px 18px;
    margin-right: 3px;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    color: #94a3b8;
    font-weight: 600;
}
QTabBar::tab:selected {
    background: #0c2a4a;
    color: #e0f2fe;
    border-color: #1e4a72;
}
QStatusBar {
    background: #080c11;
    color: #64748b;
    border-top: 1px solid #1e293b;
    padding: 2px 8px;
    font-size: 12px;
}
QStatusBar::item {
    border: none;
}
QSplitter::handle {
    background: #1e293b;
    width: 2px;
}
QSplitter::handle:hover {
    background: #38bdf8;
}
QProgressBar {
    border: 1px solid #1e293b;
    border-radius: 8px;
    background: #0f1520;
    text-align: center;
    height: 14px;
    color: #94a3b8;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #0284c7, stop:1 #38bdf8);
    border-radius: 7px;
}
"""
