
"""RainRoom dark UI theme."""

APP_STYLESHEET = """
QWidget {
    background-color: #0e1218;
    color: #d8dee9;
    font-family: "Segoe UI", "Inter", "SF Pro Text", sans-serif;
    font-size: 13px;
}
QMainWindow, QDialog {
    background-color: #0e1218;
}
QToolTip {
    background-color: #1a2230;
    color: #eceff4;
    border: 1px solid #3b4558;
    padding: 4px 8px;
}
QLabel#Title {
    font-size: 20px;
    font-weight: 600;
    color: #eceff4;
}
QLabel#Subtitle {
    color: #8b95a8;
    font-size: 12px;
}
QLabel#Section {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.2px;
    color: #6b7a90;
    padding-top: 8px;
    padding-bottom: 2px;
}
QFrame#Card {
    background-color: #151b25;
    border: 1px solid #243041;
    border-radius: 10px;
}
QFrame#Sidebar {
    background-color: #11161f;
    border-right: 1px solid #243041;
}
QPushButton {
    background-color: #1c2636;
    border: 1px solid #314057;
    border-radius: 8px;
    padding: 8px 14px;
    color: #e5e9f0;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #243247;
    border-color: #4a90d9;
}
QPushButton:pressed {
    background-color: #182232;
}
QPushButton:disabled {
    color: #5a6578;
    border-color: #2a3344;
}
QPushButton#Primary {
    background-color: #2b6cb0;
    border-color: #3b82c4;
    color: white;
}
QPushButton#Primary:hover {
    background-color: #3480cc;
}
QPushButton#Danger {
    background-color: #3a1f24;
    border-color: #8b3a44;
    color: #ffb4b4;
}
QPushButton#Success {
    background-color: #1a3a2a;
    border-color: #2f8f5b;
    color: #b6f0c8;
}
QPushButton#ToolActive {
    background-color: #1e3a5f;
    border-color: #4a9eff;
    color: #dceeff;
}
QListWidget, QTreeWidget, QTableWidget {
    background-color: #121821;
    border: 1px solid #243041;
    border-radius: 8px;
    outline: none;
    padding: 4px;
}
QListWidget::item {
    padding: 8px 10px;
    border-radius: 6px;
}
QListWidget::item:selected {
    background-color: #1e3a5f;
    color: #ffffff;
}
QListWidget::item:hover {
    background-color: #1a2535;
}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: #121821;
    border: 1px solid #314057;
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 28px;
    selection-background-color: #1e3a5f;
    selection-color: #ffffff;
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
    border-color: #4a90d9;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border-color: #4a9eff;
    background-color: #151d28;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: #5a6578;
    background-color: #0e1218;
}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    width: 18px;
    border: none;
    background: transparent;
}
QScrollArea {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: #0e1218;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #314057;
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QGroupBox {
    border: 1px solid #243041;
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #8b95a8;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background-color: #151b25;
    border: 1px solid #314057;
    selection-background-color: #1e3a5f;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #243041;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -6px 0;
    background: #4a9eff;
    border-radius: 8px;
}
QSlider::sub-page:horizontal {
    background: #2b6cb0;
    border-radius: 3px;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #314057;
    background: #121821;
}
QCheckBox::indicator:checked {
    background: #2b6cb0;
    border-color: #4a9eff;
}
QTabWidget::pane {
    border: 1px solid #243041;
    border-radius: 8px;
    background: #121821;
    top: -1px;
}
QTabBar::tab {
    background: #151b25;
    border: 1px solid #243041;
    border-bottom: none;
    padding: 8px 16px;
    margin-right: 2px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    color: #8b95a8;
}
QTabBar::tab:selected {
    background: #1e3a5f;
    color: #ffffff;
}
QStatusBar {
    background: #0b0f14;
    color: #8b95a8;
    border-top: 1px solid #243041;
}
QScrollArea {
    border: none;
}
QGroupBox {
    border: 1px solid #243041;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #8b95a8;
}
QProgressBar {
    border: 1px solid #243041;
    border-radius: 6px;
    background: #121821;
    text-align: center;
    height: 14px;
}
QProgressBar::chunk {
    background: #2b6cb0;
    border-radius: 5px;
}
QSplitter::handle {
    background: #243041;
    width: 2px;
}
"""
