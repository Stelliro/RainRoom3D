
"""
RainRoom3D — Design your house on terrain, map speakers, simulate outdoor rain.

Workflow
--------
1. Design   — size the house, place windows on walls, set materials
2. Speakers — discover devices, place speakers, assign + test each one
3. Simulate — rain outside windows/roof, heard through speaker positions
"""

from __future__ import annotations

import logging
import os
import sys
import webbrowser

from PySide6 import QtCore, QtGui, QtWidgets

# Canonical project home (shown in UI + About)
PROJECT_URL = "https://github.com/Stelliro/RainRoom3D"
PROJECT_NAME = "RainRoom3D"

from app.logging_setup import setup_logging
from app.models.materials import MATERIAL_PRESETS
from app.models.room import (
    Room,
    Speaker,
    Window,
    WINDOW_OPEN_STYLES,
    WINDOW_STYLE_LABELS,
    CUSTOM_HINGES,
    CUSTOM_MOTIONS,
    default_house,
    place_speakers_evenly,
)
from app.ui.floorplan import (
    TOOL_LISTENER,
    TOOL_RESIZE,
    TOOL_SELECT,
    TOOL_SPEAKER,
    TOOL_WINDOW,
    FloorPlanCanvas,
)
from app.ui.theme import APP_STYLESHEET
from app.ui.widgets import WindDirectionWheel
from app.utils.persistence import load_room, save_room
from app.audio.spatial_engine import SpatialRainEngine

LOG_PATH = setup_logging()
log = logging.getLogger("ui")

# Always-available software 3D preview (does not depend on OpenGL profile)
from app.graphics.room3d_view import Room3DView

GL_AVAILABLE = False
GLRoomView = None
configure_gl_surface_format = None
try:
    from app.graphics.glview import GLRoomView as _GLRoomView, configure_gl_surface_format
    GLRoomView = _GLRoomView
    GL_AVAILABLE = True
except Exception as _e:
    GL_AVAILABLE = False
    log.exception("OpenGL optional view unavailable: %s", _e)


def _card() -> QtWidgets.QFrame:
    f = QtWidgets.QFrame()
    f.setObjectName("Card")
    return f


def _section(text: str) -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel(text.upper())
    lab.setObjectName("Section")
    return lab


def _scroll_panel(inner: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
    """Inspector panels: never compress children into overlapping zero-height rows."""
    sc = QtWidgets.QScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QtWidgets.QFrame.NoFrame)
    sc.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    sc.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
    sc.setWidget(inner)
    # Let the scroll area fill the stacked inspector; inner keeps natural height
    inner.setMinimumWidth(280)
    return sc


def _tune_form(form: QtWidgets.QFormLayout) -> None:
    form.setSpacing(8)
    form.setContentsMargins(0, 0, 0, 0)
    form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
    form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QtWidgets.QFormLayout.DontWrapRows)
    try:
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
    except Exception:
        pass


def _tune_field(w: QtWidgets.QWidget) -> QtWidgets.QWidget:
    """Ensure line edits / spinboxes stay tall enough to click and type."""
    w.setMinimumHeight(30)
    w.setSizePolicy(
        QtWidgets.QSizePolicy.Expanding,
        QtWidgets.QSizePolicy.Fixed,
    )
    if isinstance(w, (QtWidgets.QLineEdit, QtWidgets.QComboBox, QtWidgets.QAbstractSpinBox)):
        w.setMinimumWidth(120)
    return w


class Main(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RainRoom3D — Design · Speakers · Spatial rain")
        self.setMinimumSize(1320, 860)
        self.resize(1520, 960)

        self.room: Room = self._load_startup_preset()
        self.engine = SpatialRainEngine(self.room)
        self._play_mode = None  # multi | headphones
        self._selected = ("none", -1)

        self._build_ui()
        self._wire()
        self._apply_room_to_ui()
        self._refresh_device_lists()
        self._on_selection(("none", -1))
        self._update_sim_status()
        self.statusBar().showMessage(
            f"Loaded “{self.room.name}” — set Volume, then Play as You to listen."
        )

    # ==================================================================
    # UI construction
    # ==================================================================
    def _build_ui(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        root_lay = QtWidgets.QHBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ---- Sidebar ----
        side = QtWidgets.QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(288)
        side_lay = QtWidgets.QVBoxLayout(side)
        side_lay.setContentsMargins(18, 20, 18, 18)
        side_lay.setSpacing(10)

        brand = QtWidgets.QLabel("RAINROOM 3D")
        brand.setObjectName("BrandMark")
        title = QtWidgets.QLabel("RainRoom")
        title.setObjectName("Title")
        sub = QtWidgets.QLabel("Design a house. Map speakers.\nHear outdoor rain in 3D.")
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        badge = QtWidgets.QLabel("WIP")
        badge.setObjectName("Badge")
        badge.setAlignment(QtCore.Qt.AlignCenter)
        badge.setFixedWidth(48)
        brand_row = QtWidgets.QHBoxLayout()
        brand_row.addWidget(brand, 1)
        brand_row.addWidget(badge, 0, QtCore.Qt.AlignTop)
        side_lay.addLayout(brand_row)
        side_lay.addWidget(title)
        side_lay.addWidget(sub)
        side_lay.addSpacing(4)

        self.btn_website = QtWidgets.QPushButton("Open project page")
        self.btn_website.setObjectName("Primary")
        self.btn_website.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_website.setToolTip(PROJECT_URL)
        side_lay.addWidget(self.btn_website)
        self.lbl_url = QtWidgets.QLabel(
            f'<a href="{PROJECT_URL}" style="color:#7dd3fc; text-decoration:none;">'
            f'{PROJECT_URL.replace("https://", "")}</a>'
        )
        self.lbl_url.setObjectName("Subtitle")
        self.lbl_url.setOpenExternalLinks(True)
        self.lbl_url.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        self.lbl_url.setWordWrap(True)
        side_lay.addWidget(self.lbl_url)
        side_lay.addSpacing(6)

        side_lay.addWidget(_section("Workflow"))
        self.nav = QtWidgets.QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setFixedHeight(148)
        self.nav.setSpacing(2)
        self.nav.setFocusPolicy(QtCore.Qt.NoFocus)
        for label in ("1  Design house", "2  Speakers", "3  Simulate rain"):
            self.nav.addItem(label)
        self.nav.setCurrentRow(0)
        side_lay.addWidget(self.nav)

        side_lay.addWidget(_section("Project"))
        self.btn_new = QtWidgets.QPushButton("New house")
        self.btn_load = QtWidgets.QPushButton("Load…")
        self.btn_save = QtWidgets.QPushButton("Save…")
        self.btn_about = QtWidgets.QPushButton("About / help")
        self.btn_about.setObjectName("Ghost")
        side_lay.addWidget(self.btn_new)
        side_lay.addWidget(self.btn_load)
        side_lay.addWidget(self.btn_save)
        side_lay.addWidget(self.btn_about)

        side_lay.addStretch(1)

        self.lbl_live = QtWidgets.QLabel("● Stopped")
        self.lbl_live.setObjectName("LiveIdle")
        side_lay.addWidget(self.lbl_live)
        self.btn_stop = QtWidgets.QPushButton("Stop audio")
        self.btn_stop.setObjectName("Danger")
        side_lay.addWidget(self.btn_stop)

        root_lay.addWidget(side)

        # ---- Main column ----
        main_col = QtWidgets.QVBoxLayout()
        main_col.setContentsMargins(18, 16, 16, 14)
        main_col.setSpacing(12)
        root_lay.addLayout(main_col, 1)

        # Header
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(12)
        self.lbl_step = QtWidgets.QLabel("Design your house on the terrain")
        self.lbl_step.setObjectName("Title")
        head.addWidget(self.lbl_step, 1)
        self.lbl_step_sub = QtWidgets.QLabel("Floor plan · 3D preview · inspector")
        self.lbl_step_sub.setObjectName("Subtitle")
        self.lbl_step_sub.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        head.addWidget(self.lbl_step_sub, 0)
        main_col.addLayout(head)

        # Splitter: canvas | inspector
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        main_col.addWidget(split, 1)

        # Views
        view_wrap = QtWidgets.QWidget()
        view_lay = QtWidgets.QVBoxLayout(view_wrap)
        view_lay.setContentsMargins(0, 0, 0, 0)
        view_lay.setSpacing(6)

        # Tool bar for design
        self.tool_bar = QtWidgets.QFrame()
        self.tool_bar.setObjectName("ToolBar")
        tb = QtWidgets.QHBoxLayout(self.tool_bar)
        tb.setContentsMargins(10, 8, 10, 8)
        tb.setSpacing(8)
        self.tool_btns = {}
        for key, label in (
            (TOOL_SELECT, "Select"),
            (TOOL_WINDOW, "+ Window"),
            (TOOL_SPEAKER, "+ Speaker"),
            (TOOL_LISTENER, "You"),
            (TOOL_RESIZE, "Resize"),
        ):
            b = QtWidgets.QPushButton(label)
            b.setCheckable(True)
            b.clicked.connect(lambda checked, k=key: self._set_tool(k))
            self.tool_btns[key] = b
            tb.addWidget(b)
        tb.addStretch(1)
        self.btn_fit = QtWidgets.QPushButton("Fit view")
        self.btn_fit.setToolTip("Frame the house in 2D/3D (also: F or double-click in 3D)")
        tb.addWidget(self.btn_fit)
        view_lay.addWidget(self.tool_bar)

        self.floor = FloorPlanCanvas()
        self.floor.set_room(self.room)

        # Software 3D (always works) — primary 3D preview
        self.view3d = Room3DView()
        self.view3d.set_room(self.room)

        # Hardware OpenGL 3D (preferred when available)
        self.gl = None
        if GL_AVAILABLE and os.getenv("RAINROOM_NOGPU", "0") != "1" and GLRoomView is not None:
            try:
                self.gl = GLRoomView()
                self.gl.set_room(self.room)
                self.gl.glStatus.connect(self._on_gl_status)
            except Exception as e:
                log.exception("GL init failed: %s", e)
                self.gl = None

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self.floor)   # 0 plan
        self.stack.addWidget(self.view3d)  # 1 software 3D
        if self.gl:
            self.stack.addWidget(self.gl)  # 2 OpenGL
        view_lay.addWidget(self.stack, 1)

        view_switch = QtWidgets.QHBoxLayout()
        self.btn_view_plan = QtWidgets.QPushButton("Floor plan")
        self.btn_view_plan.setCheckable(True)
        self.btn_view_plan.setChecked(True)
        self.btn_view_3d = QtWidgets.QPushButton("3D software")
        self.btn_view_3d.setCheckable(True)
        self.btn_view_3d.setEnabled(True)
        self.btn_view_3d.setToolTip("Always-works CPU 3D preview")
        self.btn_view_gl = QtWidgets.QPushButton("OpenGL 3D")
        self.btn_view_gl.setCheckable(True)
        self.btn_view_gl.setEnabled(self.gl is not None)
        self.btn_view_gl.setToolTip(
            "Hardware OpenGL house view (orbit with right-drag, scroll zoom, F to fit)"
            if self.gl is not None
            else "OpenGL unavailable — install drivers / PyOpenGL, or use 3D software"
        )
        view_switch.addWidget(self.btn_view_plan)
        view_switch.addWidget(self.btn_view_3d)
        if self.gl is not None:
            view_switch.addWidget(self.btn_view_gl)
        view_switch.addStretch(1)
        view_lay.addLayout(view_switch)

        split.addWidget(view_wrap)

        # Inspector stack (per workflow step) — scrollable so fields never pile up
        insp_wrap = QtWidgets.QFrame()
        insp_wrap.setObjectName("InspectorChrome")
        insp_lay = QtWidgets.QVBoxLayout(insp_wrap)
        insp_lay.setContentsMargins(10, 10, 10, 10)
        insp_lay.setSpacing(0)
        self.inspector = QtWidgets.QStackedWidget()
        self.inspector.setMinimumWidth(360)
        self.inspector.setMaximumWidth(480)
        insp_lay.addWidget(self.inspector)
        split.addWidget(insp_wrap)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([1000, 440])

        self.inspector.addWidget(self._build_design_panel())
        self.inspector.addWidget(self._build_speakers_panel())
        self.inspector.addWidget(self._build_sim_panel())

        self._set_tool(TOOL_SELECT)

    def _build_design_panel(self) -> QtWidgets.QWidget:
        inner = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(inner)
        lay.setContentsMargins(4, 4, 12, 16)
        lay.setSpacing(10)

        lay.addWidget(_section("House footprint"))
        form = QtWidgets.QFormLayout()
        _tune_form(form)
        self.ed_name = _tune_field(QtWidgets.QLineEdit(self.room.name))
        self.ed_name.setClearButtonEnabled(True)
        self.ed_name.setPlaceholderText("House name")
        self.spin_w = _tune_field(QtWidgets.QDoubleSpinBox())
        self.spin_w.setRange(2.0, 30.0)
        self.spin_w.setDecimals(2)
        self.spin_w.setSuffix(" m")
        self.spin_w.setValue(self.room.width)
        self.spin_d = _tune_field(QtWidgets.QDoubleSpinBox())
        self.spin_d.setRange(2.0, 30.0)
        self.spin_d.setDecimals(2)
        self.spin_d.setSuffix(" m")
        self.spin_d.setValue(self.room.depth)
        self.spin_h = _tune_field(QtWidgets.QDoubleSpinBox())
        self.spin_h.setRange(2.0, 6.0)
        self.spin_h.setDecimals(2)
        self.spin_h.setSuffix(" m")
        self.spin_h.setValue(self.room.height)
        self.spin_terrain = _tune_field(QtWidgets.QDoubleSpinBox())
        self.spin_terrain.setRange(12.0, 120.0)
        self.spin_terrain.setSuffix(" m")
        self.spin_terrain.setValue(self.room.terrain_size)
        form.addRow("Name", self.ed_name)
        form.addRow("Width", self.spin_w)
        form.addRow("Depth", self.spin_d)
        form.addRow("Ceiling", self.spin_h)
        form.addRow("Terrain", self.spin_terrain)
        lay.addLayout(form)

        lay.addWidget(_section("Materials"))
        self.cmb_roof = _tune_field(QtWidgets.QComboBox())
        self.cmb_wall = _tune_field(QtWidgets.QComboBox())
        mats = [m.name for m in MATERIAL_PRESETS]
        self.cmb_roof.addItems(mats)
        self.cmb_wall.addItems(mats)
        self._select_combo(self.cmb_roof, self.room.roof_material)
        self._select_combo(self.cmb_wall, self.room.wall_material)
        form2 = QtWidgets.QFormLayout()
        _tune_form(form2)
        form2.addRow("Roof", self.cmb_roof)
        form2.addRow("Walls", self.cmb_wall)
        lay.addLayout(form2)

        lay.addWidget(_section("Selection"))
        # Shared name field (window / speaker)
        self.sel_name = _tune_field(QtWidgets.QLineEdit())
        self.sel_name.setClearButtonEnabled(True)
        self.sel_name.setPlaceholderText("Name")

        # --- Window-only fields ---
        self.sel_wall = _tune_field(QtWidgets.QComboBox())
        self.sel_wall.addItems(["north", "south", "east", "west"])
        self.sel_width = _tune_field(QtWidgets.QDoubleSpinBox())
        self.sel_width.setRange(0.3, 8.0)
        self.sel_width.setDecimals(2)
        self.sel_width.setSuffix(" m")
        self.sel_height = _tune_field(QtWidgets.QDoubleSpinBox())
        self.sel_height.setRange(0.3, 4.0)
        self.sel_height.setDecimals(2)
        self.sel_height.setSuffix(" m")
        self.sel_height.setToolTip("Vertical size of the glass opening")
        self.sel_sill = _tune_field(QtWidgets.QDoubleSpinBox())
        self.sel_sill.setRange(0.0, 3.5)
        self.sel_sill.setDecimals(2)
        self.sel_sill.setSuffix(" m")
        self.sel_sill.setToolTip("Height of the window sill above the floor")
        self.sel_style = _tune_field(QtWidgets.QComboBox())
        for key in WINDOW_OPEN_STYLES:
            self.sel_style.addItem(WINDOW_STYLE_LABELS.get(key, key), userData=key)
        self.sel_open = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sel_open.setRange(0, 100)
        self.sel_open.setMinimumHeight(28)
        self.sel_open_lbl = QtWidgets.QLabel("70%")
        self.sel_open_lbl.setMinimumWidth(72)
        open_row = QtWidgets.QWidget()
        open_lay = QtWidgets.QHBoxLayout(open_row)
        open_lay.setContentsMargins(0, 0, 0, 0)
        open_lay.setSpacing(8)
        open_lay.addWidget(self.sel_open, 1)
        open_lay.addWidget(self.sel_open_lbl)
        self.sel_angle = _tune_field(QtWidgets.QDoubleSpinBox())
        self.sel_angle.setRange(10.0, 90.0)
        self.sel_angle.setSuffix("°")
        self.sel_angle.setToolTip("Max swing/tilt for hinged styles")
        self.sel_hinge = _tune_field(QtWidgets.QComboBox())
        self.sel_hinge.addItems(["left", "right"])
        self.sel_custom_hinge = _tune_field(QtWidgets.QComboBox())
        self.sel_custom_hinge.addItems(list(CUSTOM_HINGES))
        self.sel_custom_motion = _tune_field(QtWidgets.QComboBox())
        self.sel_custom_motion.addItems(list(CUSTOM_MOTIONS))
        self.sel_custom_out = QtWidgets.QCheckBox("Opens outward")
        self.sel_custom_out.setChecked(True)
        self.sel_custom_notes = _tune_field(QtWidgets.QLineEdit())
        self.sel_custom_notes.setClearButtonEnabled(True)
        self.sel_custom_notes.setPlaceholderText("Describe your window (optional)")

        # --- Speaker-only fields ---
        self.sel_spk_size = _tune_field(QtWidgets.QDoubleSpinBox())
        self.sel_spk_size.setRange(0.12, 1.2)
        self.sel_spk_size.setDecimals(2)
        self.sel_spk_size.setSuffix(" m")
        self.sel_spk_size.setToolTip("Speaker box size in 3D (edge length)")
        self.sel_spk_y = _tune_field(QtWidgets.QDoubleSpinBox())
        self.sel_spk_y.setRange(0.1, 3.5)
        self.sel_spk_y.setDecimals(2)
        self.sel_spk_y.setSuffix(" m")
        self.sel_spk_y.setToolTip("Speaker height above floor")

        self.sel_acoustics = QtWidgets.QLabel("")
        self.sel_acoustics.setObjectName("Subtitle")
        self.sel_acoustics.setWordWrap(True)
        self.sel_acoustics.setMinimumHeight(48)

        # Stacked detail so window/speaker fields never pile on top of each other
        self.sel_stack = QtWidgets.QStackedWidget()
        self.sel_stack.setMinimumHeight(120)

        # page 0 — empty
        empty = QtWidgets.QLabel("Click a window, speaker, or You in the view.")
        empty.setObjectName("Subtitle")
        empty.setWordWrap(True)
        empty.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        empty.setContentsMargins(4, 8, 4, 8)
        self.sel_stack.addWidget(empty)

        # page 1 — window
        win_page = QtWidgets.QWidget()
        win_form = QtWidgets.QFormLayout(win_page)
        _tune_form(win_form)
        win_form.addRow("Name", self.sel_name)
        win_form.addRow("Wall", self.sel_wall)
        win_form.addRow("Width", self.sel_width)
        win_form.addRow("Vertical size", self.sel_height)
        win_form.addRow("Sill height", self.sel_sill)
        win_form.addRow("Open style", self.sel_style)
        win_form.addRow("Open amount", open_row)
        win_form.addRow("Max angle", self.sel_angle)
        win_form.addRow("Hinge / side", self.sel_hinge)
        self.sel_custom_box = QtWidgets.QGroupBox("Custom open design")
        cust = QtWidgets.QFormLayout(self.sel_custom_box)
        _tune_form(cust)
        cust.addRow("Hinge", self.sel_custom_hinge)
        cust.addRow("Motion", self.sel_custom_motion)
        cust.addRow("", self.sel_custom_out)
        cust.addRow("Notes", self.sel_custom_notes)
        win_form.addRow(self.sel_custom_box)
        self.sel_stack.addWidget(win_page)

        # page 2 — speaker (own name field to avoid reparent bugs)
        spk_page = QtWidgets.QWidget()
        spk_form = QtWidgets.QFormLayout(spk_page)
        _tune_form(spk_form)
        self.sel_name_spk = _tune_field(QtWidgets.QLineEdit())
        self.sel_name_spk.setClearButtonEnabled(True)
        self.sel_name_spk.setPlaceholderText("Speaker name")
        spk_form.addRow("Name", self.sel_name_spk)
        spk_form.addRow("Size", self.sel_spk_size)
        spk_form.addRow("Height", self.sel_spk_y)
        self.sel_stack.addWidget(spk_page)

        # page 3 — You
        you_page = QtWidgets.QWidget()
        you_lay = QtWidgets.QVBoxLayout(you_page)
        you_lay.setContentsMargins(4, 8, 4, 4)
        you_info = QtWidgets.QLabel(
            "You is the binaural listener (headphones).\n"
            "Drag the blue marker on the plan / 3D view.\n"
            "Assign the headphone device under Speakers."
        )
        you_info.setObjectName("Subtitle")
        you_info.setWordWrap(True)
        you_lay.addWidget(you_info)
        you_lay.addStretch(1)
        self.sel_stack.addWidget(you_page)

        self.sel_box = QtWidgets.QGroupBox("Nothing selected")
        box_lay = QtWidgets.QVBoxLayout(self.sel_box)
        box_lay.setContentsMargins(10, 12, 10, 10)
        box_lay.setSpacing(8)
        box_lay.addWidget(self.sel_stack)
        box_lay.addWidget(self.sel_acoustics)
        lay.addWidget(self.sel_box)

        tip = QtWidgets.QLabel(
            "Windows face outdoor weather. Sill height + vertical size set the "
            "glass rectangle. Open style changes how rain couples in."
        )
        tip.setWordWrap(True)
        tip.setObjectName("Subtitle")
        lay.addWidget(tip)
        lay.addStretch(1)
        return _scroll_panel(inner)

    def _build_speakers_panel(self) -> QtWidgets.QWidget:
        inner = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(inner)
        lay.setContentsMargins(4, 4, 12, 16)
        lay.setSpacing(10)

        lay.addWidget(_section("Output devices"))
        row = QtWidgets.QHBoxLayout()
        self.btn_refresh_dev = QtWidgets.QPushButton("Refresh")
        row.addWidget(self.btn_refresh_dev)
        row.addStretch(1)
        lay.addLayout(row)

        self.lst_devices = QtWidgets.QListWidget()
        self.lst_devices.setMinimumHeight(140)
        self.lst_devices.setMaximumHeight(220)
        lay.addWidget(self.lst_devices)
        self.btn_test_device = QtWidgets.QPushButton("Test selected device")
        lay.addWidget(self.btn_test_device)

        lay.addWidget(_section("Outputs in house"))
        self.lst_speakers = QtWidgets.QListWidget()
        self.lst_speakers.setMinimumHeight(120)
        self.lst_speakers.setMaximumHeight(200)
        self.lst_speakers.setToolTip(
            "You = binaural headphones at the blue marker.\n"
            "Speaker N = a physical room speaker you can map to any OS output."
        )
        lay.addWidget(self.lst_speakers)

        form = QtWidgets.QFormLayout()
        _tune_form(form)
        self.spk_name = _tune_field(QtWidgets.QLineEdit())
        self.spk_name.setClearButtonEnabled(True)
        self.spk_name.setPlaceholderText("Speaker name")
        self.spk_device = _tune_field(QtWidgets.QComboBox())
        self.spk_gain = _tune_field(QtWidgets.QDoubleSpinBox())
        self.spk_gain.setRange(-24, 12)
        self.spk_gain.setSuffix(" dB")
        self.spk_enabled = QtWidgets.QCheckBox("Enabled")
        self.spk_enabled.setChecked(True)
        form.addRow("Name", self.spk_name)
        form.addRow("Device", self.spk_device)
        form.addRow("Gain", self.spk_gain)
        form.addRow("", self.spk_enabled)
        lay.addLayout(form)

        brow = QtWidgets.QHBoxLayout()
        brow.setSpacing(6)
        self.btn_test_spk = QtWidgets.QPushButton("Test this output")
        self.btn_test_spk.setObjectName("Primary")
        self.btn_add_spk_panel = QtWidgets.QPushButton("Add speaker")
        self.btn_del_spk = QtWidgets.QPushButton("Remove")
        self.btn_del_spk.setObjectName("Danger")
        brow.addWidget(self.btn_test_spk)
        brow.addWidget(self.btn_add_spk_panel)
        brow.addWidget(self.btn_del_spk)
        lay.addLayout(brow)

        tip = QtWidgets.QLabel(
            "Select “You” to choose which device is your headphones. "
            "Add speakers for the rest of the room and assign each a real OS output."
        )
        tip.setWordWrap(True)
        tip.setObjectName("Subtitle")
        lay.addWidget(tip)
        lay.addStretch(1)
        return _scroll_panel(inner)

    def _build_sim_panel(self) -> QtWidgets.QWidget:
        inner = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(inner)
        lay.setContentsMargins(4, 4, 12, 16)
        lay.setSpacing(10)

        def _slider_row(slider: QtWidgets.QSlider, label: QtWidgets.QLabel) -> QtWidgets.QWidget:
            row = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)
            slider.setMinimumHeight(28)
            label.setMinimumWidth(100)
            label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            hl.addWidget(slider, 1)
            hl.addWidget(label)
            return row

        lay.addWidget(_section("Weather"))
        form = QtWidgets.QFormLayout()
        _tune_form(form)
        self.sld_rain = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_rain.setRange(0, 100)
        self.sld_rain.setValue(int(self.room.rain_intensity * 100))
        self.sld_density = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_density.setRange(0, 100)
        self.sld_density.setValue(int(self.room.droplet_density * 100))
        dens0 = float(self.room.droplet_density)
        self.lbl_rain = QtWidgets.QLabel(self._sharpness_label(self.room.rain_intensity))
        self.lbl_density = QtWidgets.QLabel(self._quantity_label(dens0))
        form.addRow("Sharpness", _slider_row(self.sld_rain, self.lbl_rain))
        form.addRow("Quantity", _slider_row(self.sld_density, self.lbl_density))

        self.sld_volume = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_volume.setRange(0, 100)
        vol0 = float(getattr(self.room, "master_volume", 0.75))
        self.sld_volume.setValue(int(round(vol0 * 100)))
        self.sld_volume.setToolTip(
            "Master listen level. 100% = calibrated full scale (comfort peak ~0.9)."
        )
        self.lbl_volume = QtWidgets.QLabel(self._volume_label(vol0))
        form.addRow("Volume", _slider_row(self.sld_volume, self.lbl_volume))
        lay.addLayout(form)

        lay.addWidget(_section("Wind"))
        wind_form = QtWidgets.QFormLayout()
        _tune_form(wind_form)

        self.sld_wind_speed = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_wind_speed.setRange(0, 100)
        self.sld_wind_speed.setValue(int(round(float(getattr(self.room, "wind_speed", 0.0)) * 100)))
        self.sld_wind_speed.setToolTip("How hard the wind drives rain into the house.")
        self.lbl_wind_speed = QtWidgets.QLabel(self._wind_speed_label(self.room.wind_speed))
        wind_form.addRow("Speed", _slider_row(self.sld_wind_speed, self.lbl_wind_speed))

        self.dial_wind_dir = WindDirectionWheel()
        self.dial_wind_dir.setFixedSize(132, 132)
        self.dial_wind_dir.setValue(
            int(round(float(getattr(self.room, "wind_direction_deg", 90.0))) % 360)
        )
        dial_wrap = QtWidgets.QWidget()
        dial_lay = QtWidgets.QHBoxLayout(dial_wrap)
        dial_lay.setContentsMargins(0, 0, 0, 0)
        dial_lay.addWidget(self.dial_wind_dir)
        dial_lay.addStretch(1)
        self.sld_wind_dir = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_wind_dir.setRange(0, 359)
        self.sld_wind_dir.setValue(self.dial_wind_dir.value())
        self.lbl_wind_dir = QtWidgets.QLabel(self._wind_dir_label(self.dial_wind_dir.value()))
        wind_form.addRow("Direction", dial_wrap)
        wind_form.addRow("", _slider_row(self.sld_wind_dir, self.lbl_wind_dir))

        self.chk_wind_vary_dir = QtWidgets.QCheckBox("Vary direction")
        self.chk_wind_vary_dir.setChecked(bool(getattr(self.room, "wind_vary_direction", False)))
        wind_form.addRow(self.chk_wind_vary_dir)

        self.sld_wind_dir_range = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_wind_dir_range.setRange(0, 180)
        self.sld_wind_dir_range.setValue(int(round(float(getattr(self.room, "wind_dir_range_deg", 45.0)))))
        self.lbl_wind_dir_range = QtWidgets.QLabel(
            self._deg_range_label(self.sld_wind_dir_range.value())
        )
        wind_form.addRow("Dir range ±°", _slider_row(self.sld_wind_dir_range, self.lbl_wind_dir_range))

        self.sld_wind_dir_interval = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_wind_dir_interval.setRange(1, 60)
        self.sld_wind_dir_interval.setValue(int(round(float(getattr(self.room, "wind_dir_interval_s", 10.0)))))
        self.lbl_wind_dir_interval = QtWidgets.QLabel(
            f"every ~{self.sld_wind_dir_interval.value()}s"
        )
        wind_form.addRow(
            "Dir interval", _slider_row(self.sld_wind_dir_interval, self.lbl_wind_dir_interval)
        )

        self.sld_wind_dir_slew = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_wind_dir_slew.setRange(1, 60)
        self.sld_wind_dir_slew.setValue(int(round(float(getattr(self.room, "wind_dir_slew_deg_s", 15.0)))))
        self.lbl_wind_dir_slew = QtWidgets.QLabel(
            f"{self.sld_wind_dir_slew.value()}°/s turn"
        )
        wind_form.addRow("Dir rate", _slider_row(self.sld_wind_dir_slew, self.lbl_wind_dir_slew))

        self.chk_wind_vary_spd = QtWidgets.QCheckBox("Vary speed")
        self.chk_wind_vary_spd.setChecked(bool(getattr(self.room, "wind_vary_speed", False)))
        wind_form.addRow(self.chk_wind_vary_spd)

        self.sld_wind_spd_range = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_wind_spd_range.setRange(0, 100)
        self.sld_wind_spd_range.setValue(int(round(float(getattr(self.room, "wind_speed_range", 0.25)) * 100)))
        self.lbl_wind_spd_range = QtWidgets.QLabel(
            f"±{self.sld_wind_spd_range.value()}% speed"
        )
        wind_form.addRow(
            "Speed range", _slider_row(self.sld_wind_spd_range, self.lbl_wind_spd_range)
        )

        self.sld_wind_spd_interval = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_wind_spd_interval.setRange(1, 60)
        self.sld_wind_spd_interval.setValue(int(round(float(getattr(self.room, "wind_speed_interval_s", 8.0)))))
        self.lbl_wind_spd_interval = QtWidgets.QLabel(
            f"every ~{self.sld_wind_spd_interval.value()}s"
        )
        wind_form.addRow(
            "Speed interval", _slider_row(self.sld_wind_spd_interval, self.lbl_wind_spd_interval)
        )

        self.sld_wind_spd_slew = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_wind_spd_slew.setRange(1, 100)
        self.sld_wind_spd_slew.setValue(int(round(float(getattr(self.room, "wind_speed_slew_per_s", 0.20)) * 100)))
        self.lbl_wind_spd_slew = QtWidgets.QLabel(
            f"{self.sld_wind_spd_slew.value()}%/s ramp"
        )
        wind_form.addRow(
            "Speed rate", _slider_row(self.sld_wind_spd_slew, self.lbl_wind_spd_slew)
        )

        lay.addLayout(wind_form)
        self._sync_wind_vary_enabled()

        lay.addWidget(_section("Listen from"))
        tip_loc = QtWidgets.QLabel(
            "You = binaural at the blue marker.\n"
            "Mapped speakers = OS devices from the Speakers step."
        )
        tip_loc.setObjectName("Subtitle")
        tip_loc.setWordWrap(True)
        lay.addWidget(tip_loc)

        self.btn_play_hp = QtWidgets.QPushButton("▶  Play as You only")
        self.btn_play_hp.setObjectName("Primary")
        self.btn_play_hp.setMinimumHeight(36)
        self.btn_play_hp.setToolTip("Binaural at “You” only.")
        self.btn_play_multi = QtWidgets.QPushButton("▶  Play mapped speakers only")
        self.btn_play_multi.setMinimumHeight(36)
        self.btn_play_all = QtWidgets.QPushButton("▶  Play You + mapped speakers")
        self.btn_play_all.setObjectName("Success")
        self.btn_play_all.setMinimumHeight(36)
        lay.addWidget(self.btn_play_hp)
        lay.addWidget(self.btn_play_multi)
        lay.addWidget(self.btn_play_all)

        self.btn_place_3 = QtWidgets.QPushButton("Place 3 speakers evenly")
        lay.addWidget(self.btn_place_3)

        self.lbl_routing = QtWidgets.QLabel()
        self.lbl_routing.setWordWrap(True)
        self.lbl_routing.setObjectName("Subtitle")
        lay.addWidget(self.lbl_routing)

        lay.addWidget(_section("How it works"))
        how = QtWidgets.QLabel(
            "Volume = master level · Sharpness = hit brightness · "
            "Quantity = density · Wind = force & direction."
        )
        how.setWordWrap(True)
        how.setObjectName("Subtitle")
        lay.addWidget(how)
        lay.addStretch(1)
        return _scroll_panel(inner)

    # ==================================================================
    # Wiring
    # ==================================================================
    def _wire(self):
        self.nav.currentRowChanged.connect(self._on_step)
        self.btn_new.clicked.connect(self._new_house)
        self.btn_load.clicked.connect(self._load_house)
        self.btn_save.clicked.connect(self._save_house)
        self.btn_stop.clicked.connect(self._stop_audio)
        self.btn_website.clicked.connect(self._open_project_page)
        self.btn_about.clicked.connect(self._show_about)

        # Menu bar — Help always visible
        menubar = self.menuBar()
        m_help = menubar.addMenu("&Help")
        act_web = m_help.addAction("Open project page (GitHub)…")
        act_web.triggered.connect(self._open_project_page)
        act_about = m_help.addAction("About RainRoom3D…")
        act_about.triggered.connect(self._show_about)
        m_help.addSeparator()
        act_copy = m_help.addAction("Copy project URL")
        act_copy.triggered.connect(self._copy_project_url)
        self.btn_fit.clicked.connect(self._fit_view)

        self.btn_view_plan.clicked.connect(lambda: self._set_view(0))
        self.btn_view_3d.clicked.connect(lambda: self._set_view(1))
        if self.gl is not None:
            self.btn_view_gl.clicked.connect(lambda: self._set_view(2))

        self.floor.selectionChanged.connect(self._on_selection)
        self.floor.roomChanged.connect(self._on_room_changed)
        self.floor.statusMessage.connect(self.statusBar().showMessage)
        self.view3d.selectionChanged.connect(self._on_selection)
        self.view3d.roomChanged.connect(self._on_room_changed)
        self.view3d.statusMessage.connect(self.statusBar().showMessage)
        if self.gl:
            self.gl.selectionChanged.connect(self._on_selection)

        self.ed_name.textChanged.connect(self._on_name)
        self.spin_w.valueChanged.connect(lambda v: self._set_dim("width", v))
        self.spin_d.valueChanged.connect(lambda v: self._set_dim("depth", v))
        self.spin_h.valueChanged.connect(lambda v: self._set_dim("height", v))
        self.spin_terrain.valueChanged.connect(self._set_terrain)
        self.cmb_roof.currentTextChanged.connect(self._set_roof)
        self.cmb_wall.currentTextChanged.connect(self._set_wall)

        self.sel_name.editingFinished.connect(self._apply_sel_name)
        self.sel_name.returnPressed.connect(self._apply_sel_name)
        self.sel_name_spk.editingFinished.connect(self._apply_sel_name_spk)
        self.sel_name_spk.returnPressed.connect(self._apply_sel_name_spk)
        self.sel_open.valueChanged.connect(self._apply_sel_open)
        self.sel_wall.currentTextChanged.connect(self._apply_sel_wall)
        self.sel_width.valueChanged.connect(self._apply_sel_width)
        self.sel_height.valueChanged.connect(self._apply_sel_height)
        self.sel_sill.valueChanged.connect(self._apply_sel_sill)
        self.sel_style.currentIndexChanged.connect(self._apply_sel_style)
        self.sel_angle.valueChanged.connect(self._apply_sel_angle)
        self.sel_hinge.currentTextChanged.connect(self._apply_sel_hinge)
        self.sel_custom_hinge.currentTextChanged.connect(self._apply_custom_hinge)
        self.sel_custom_motion.currentTextChanged.connect(self._apply_custom_motion)
        self.sel_custom_out.toggled.connect(self._apply_custom_out)
        self.sel_custom_notes.editingFinished.connect(self._apply_custom_notes)
        self.sel_custom_notes.returnPressed.connect(self._apply_custom_notes)
        self.sel_spk_size.valueChanged.connect(self._apply_spk_size_panel)
        self.sel_spk_y.valueChanged.connect(self._apply_spk_y_panel)

        self.btn_refresh_dev.clicked.connect(self._refresh_device_lists)
        self.btn_test_device.clicked.connect(self._test_selected_device)
        self.lst_speakers.currentRowChanged.connect(self._on_speaker_list)
        self.spk_name.editingFinished.connect(self._apply_spk_fields)
        self.spk_name.returnPressed.connect(self._apply_spk_fields)
        self.spk_device.currentIndexChanged.connect(self._apply_spk_device)
        self.spk_gain.valueChanged.connect(self._apply_spk_fields)
        self.spk_enabled.toggled.connect(self._apply_spk_fields)
        self.btn_test_spk.clicked.connect(self._test_selected_speaker)
        self.btn_add_spk_panel.clicked.connect(self._add_speaker)
        self.btn_del_spk.clicked.connect(self._del_speaker)

        self.sld_rain.valueChanged.connect(self._on_rain)
        self.sld_density.valueChanged.connect(self._on_density)
        self.sld_volume.valueChanged.connect(self._on_volume)
        self.sld_wind_speed.valueChanged.connect(self._on_wind_speed)
        self.dial_wind_dir.valueChanged.connect(self._on_wind_dir_dial)
        self.sld_wind_dir.valueChanged.connect(self._on_wind_dir_slider)
        self.chk_wind_vary_dir.toggled.connect(self._on_wind_vary_dir)
        self.sld_wind_dir_range.valueChanged.connect(self._on_wind_dir_range)
        self.sld_wind_dir_interval.valueChanged.connect(self._on_wind_dir_interval)
        self.sld_wind_dir_slew.valueChanged.connect(self._on_wind_dir_slew)
        self.chk_wind_vary_spd.toggled.connect(self._on_wind_vary_spd)
        self.sld_wind_spd_range.valueChanged.connect(self._on_wind_spd_range)
        self.sld_wind_spd_interval.valueChanged.connect(self._on_wind_spd_interval)
        self.sld_wind_spd_slew.valueChanged.connect(self._on_wind_spd_slew)
        self.btn_play_multi.clicked.connect(self._play_multi)
        self.btn_play_hp.clicked.connect(self._play_headphones)
        self.btn_play_all.clicked.connect(self._play_all)
        self.btn_place_3.clicked.connect(self._place_three_speakers)

    # ==================================================================
    # Step / view
    # ==================================================================
    def _on_step(self, row: int):
        self.inspector.setCurrentIndex(max(0, min(2, row)))
        titles = (
            "Design your house on the terrain",
            "Connect & place speakers",
            "Simulate outdoor rain through your layout",
        )
        subs = (
            "Windows · materials · selection inspector",
            "OS devices · map outputs · test tones",
            "Quantity · sharpness · wind · play modes",
        )
        i = max(0, min(2, row))
        self.lbl_step.setText(titles[i])
        if hasattr(self, "lbl_step_sub"):
            self.lbl_step_sub.setText(subs[i])
        self.tool_bar.setVisible(row == 0)
        if row == 1:
            self._set_tool(TOOL_SPEAKER)
            self._refresh_speaker_list()
            self.tool_bar.setVisible(True)
        elif row == 2:
            self._set_tool(TOOL_SELECT)
            self._update_sim_status()
        else:
            self._set_tool(TOOL_SELECT)

    def _set_view(self, idx: int):
        max_idx = self.stack.count() - 1
        idx = max(0, min(max_idx, idx))
        self.stack.setCurrentIndex(idx)
        self.btn_view_plan.setChecked(idx == 0)
        self.btn_view_3d.setChecked(idx == 1)
        if self.gl is not None:
            self.btn_view_gl.setChecked(idx == 2)
        buttons = [
            (self.btn_view_plan, idx == 0),
            (self.btn_view_3d, idx == 1),
        ]
        if self.gl is not None:
            buttons.append((self.btn_view_gl, idx == 2))
        for b, active in buttons:
            b.setObjectName("ToolActive" if active else "")
            b.style().unpolish(b)
            b.style().polish(b)

        if idx == 1:
            self.view3d.set_room(self.room)
            self.view3d.fit_camera()
            self.statusBar().showMessage(
                "3D preview · Right-drag orbit · Scroll zoom · F / double-click fit"
            )
        elif idx == 2 and self.gl is not None:
            self.gl.set_room(self.room)
            self.gl.fit_camera()
            ok = getattr(self.gl, "_gl_ok", True)
            err = getattr(self.gl, "_gl_error", "")
            if ok:
                self.statusBar().showMessage(
                    "OpenGL 3D · Right-drag orbit · Scroll zoom · F / double-click fit"
                )
            else:
                self.statusBar().showMessage(
                    f"OpenGL issue: {err or 'unknown'} — try 3D software view"
                )

    def _on_gl_status(self, msg: str):
        if not msg:
            return
        if msg.startswith("OpenGL OK"):
            self.statusBar().showMessage("OpenGL context ready", 2500)
        else:
            self.statusBar().showMessage(msg, 8000)
            log.warning("GL status: %s", msg)

    def _fit_view(self):
        self.floor._fit_view()
        self.floor.update()
        self.view3d.fit_camera()
        if self.gl is not None:
            self.gl.fit_camera()
        self.statusBar().showMessage("View fitted to house")

    def _set_tool(self, tool: str):
        self.floor.set_tool(tool)
        for k, b in self.tool_btns.items():
            b.setChecked(k == tool)
            b.setObjectName("ToolActive" if k == tool else "")
            b.style().unpolish(b)
            b.style().polish(b)

    # ==================================================================
    # Room / design
    # ==================================================================
    def _select_combo(self, cmb: QtWidgets.QComboBox, text: str):
        i = cmb.findText(text)
        if i >= 0:
            cmb.setCurrentIndex(i)

    def _set_dim(self, attr: str, val: float):
        setattr(self.room, attr, float(val))
        # Keep contents inside
        for s in self.room.speakers:
            s.x, s.z = self.room.clamp_inside(s.x, s.z)
        self.room.listener.x, self.room.listener.z = self.room.clamp_inside(
            self.room.listener.x, self.room.listener.z
        )
        self.room.sync_all_windows()
        self._refresh_views()

    def _set_terrain(self, v: float):
        self.room.terrain_size = float(v)
        self._refresh_views()

    def _set_roof(self, name: str):
        self.room.roof_material = name
        if self.gl:
            self.gl.set_roof(name)

    def _set_wall(self, name: str):
        self.room.wall_material = name

    def _on_name(self, t: str):
        self.room.name = t
        self.floor.update()

    def _on_room_changed(self):
        self.spin_w.blockSignals(True)
        self.spin_d.blockSignals(True)
        self.spin_w.setValue(self.room.width)
        self.spin_d.setValue(self.room.depth)
        self.spin_w.blockSignals(False)
        self.spin_d.blockSignals(False)
        self._refresh_views(skip_floor=True)
        self._refresh_speaker_list()
        self._update_sim_status()
        if self._selected[0] != "none":
            self._on_selection(self._selected)

    def _refresh_views(self, skip_floor: bool = False):
        if not skip_floor:
            self.floor.set_room(self.room)
        else:
            self.floor.update()
        self.view3d.set_room(self.room)
        self.view3d.update()
        if self.gl:
            self.gl.set_room(self.room)
            try:
                self.gl._init_particles()
            except Exception:
                pass
            self.gl.update()

    def _on_selection(self, sel):
        self._selected = sel
        kind, idx = sel
        # Sync list selection for You / speakers (row 0 = You)
        if kind == "listener":
            self.lst_speakers.blockSignals(True)
            self.lst_speakers.setCurrentRow(0)
            self.lst_speakers.blockSignals(False)
            self._load_you_fields()
        elif kind == "speaker":
            self.lst_speakers.blockSignals(True)
            self.lst_speakers.setCurrentRow(idx + 1)
            self.lst_speakers.blockSignals(False)
            self._load_speaker_fields(idx)

        # Design inspector — one stack page at a time (no overlapping fields)
        widgets = [
            self.sel_name, self.sel_name_spk, self.sel_open, self.sel_wall, self.sel_width,
            self.sel_height, self.sel_sill, self.sel_style, self.sel_angle, self.sel_hinge,
            self.sel_custom_hinge, self.sel_custom_motion, self.sel_custom_out, self.sel_custom_notes,
            self.sel_spk_size, self.sel_spk_y,
        ]
        for wdg in widgets:
            wdg.blockSignals(True)

        if kind == "window" and 0 <= idx < len(self.room.windows):
            w = self.room.windows[idx]
            self.sel_box.setTitle(f"Window · {w.name}")
            self.sel_stack.setCurrentIndex(1)
            self.sel_name.setEnabled(True)
            self.sel_name.setText(w.name)
            self.sel_open.setValue(int(w.open * 100))
            self.sel_open_lbl.setText(f"{int(w.open * 100)}%  ·  ~{w.open_angle_deg():.0f}°")
            self.sel_wall.setCurrentText((w.wall or "north").lower())
            self.sel_width.setValue(w.width)
            self.sel_height.setValue(w.height)
            self.sel_sill.setValue(w.sill)
            style = w.open_style_norm()
            si = self.sel_style.findData(style)
            self.sel_style.setCurrentIndex(max(0, si))
            self.sel_angle.setValue(float(getattr(w, "max_angle_deg", 75.0)))
            self.sel_hinge.setCurrentText(str(getattr(w, "hinge_side", "left") or "left"))
            self.sel_custom_hinge.setCurrentText(str(getattr(w, "custom_hinge", "left") or "left"))
            self.sel_custom_motion.setCurrentText(str(getattr(w, "custom_motion", "swing") or "swing"))
            self.sel_custom_out.setChecked(bool(getattr(w, "custom_outward", True)))
            self.sel_custom_notes.setText(str(getattr(w, "custom_notes", "") or ""))
            is_custom = style == "custom"
            self.sel_custom_box.setVisible(True)
            self.sel_custom_box.setEnabled(is_custom)
            for wdg in (
                self.sel_custom_hinge, self.sel_custom_motion,
                self.sel_custom_out, self.sel_custom_notes,
            ):
                wdg.setEnabled(is_custom)
            self._update_window_acoustics_label(w)
        elif kind == "speaker" and 0 <= idx < len(self.room.speakers):
            s = self.room.speakers[idx]
            self.sel_box.setTitle(f"Speaker · {s.name}")
            self.sel_stack.setCurrentIndex(2)
            self.sel_name_spk.setEnabled(True)
            self.sel_name_spk.setText(s.name)
            self.sel_spk_size.setEnabled(True)
            self.sel_spk_y.setEnabled(True)
            self.sel_spk_size.setValue(float(getattr(s, "size", 0.32)))
            self.sel_spk_y.setValue(float(s.y))
            self.sel_acoustics.setText(
                f"3D: drag body to move · top (blue) height · green size\n"
                f"Position ({s.x:.2f}, {s.y:.2f}, {s.z:.2f}) m"
            )
        elif kind == "listener":
            self.sel_box.setTitle("You (headphones)")
            self.sel_stack.setCurrentIndex(3)
            dev = getattr(self.room.listener, "audio_device", None)
            self.sel_acoustics.setText(
                f"Headphones device: {'system default' if dev is None else f'#{dev}'}"
            )
        else:
            self.sel_box.setTitle("Nothing selected")
            self.sel_stack.setCurrentIndex(0)
            self.sel_name.setText("")
            self.sel_name_spk.setText("")
            self.sel_acoustics.setText("")

        for wdg in widgets:
            wdg.blockSignals(False)

        if self.gl and hasattr(self.gl, "_selection"):
            self.gl._selection = sel
            self.gl.update()
        if getattr(self.view3d, "_selection", None) != sel:
            self.view3d.set_selection(kind, idx, emit=False)
        if self.floor.selection != sel:
            self.floor.set_selection(kind, idx, emit=False)

    def _apply_sel_name(self):
        kind, idx = self._selected
        name = self.sel_name.text().strip()
        if not name:
            return
        if kind == "window" and 0 <= idx < len(self.room.windows):
            self.room.windows[idx].name = name
            self.sel_box.setTitle(f"Window · {name}")
            self._refresh_views()

    def _apply_sel_name_spk(self):
        kind, idx = self._selected
        name = self.sel_name_spk.text().strip()
        if not name:
            return
        if kind == "speaker" and 0 <= idx < len(self.room.speakers):
            self.room.speakers[idx].name = name
            self.sel_box.setTitle(f"Speaker · {name}")
            self._refresh_speaker_list()
            self._refresh_views()

    def _current_window(self):
        kind, idx = self._selected
        if kind == "window" and 0 <= idx < len(self.room.windows):
            return self.room.windows[idx]
        return None

    def _update_window_acoustics_label(self, w: Window):
        try:
            from app.audio.spatial import window_acoustic_profile
            style = w.resolved_style_for_draw() if hasattr(w, "resolved_style_for_draw") else w.open_style_norm()
            hinge = w.resolved_hinge_side() if hasattr(w, "resolved_hinge_side") else getattr(w, "hinge_side", "left")
            p = window_acoustic_profile(
                w.open_amount(), style,
                width=w.width, height=w.height,
                angle_deg=w.open_angle_deg(),
                hinge_side=hinge,
            )
            y0, y1 = w.effective_gap_y_range()
            self.sel_acoustics.setText(
                f"Portal: gain×{p['gain']:.2f}  ·  tone~{p['lp_fc']:.0f} Hz  ·  "
                f"gap {y0:.2f}–{y1:.2f} m  ·  area {w.aperture_area_m2():.2f} m²\n"
                f"Open: {style} · hinge {hinge} · low-scoop {p['scoop_low']:+.2f}  "
                f"high-scoop {p['scoop_high']:+.2f}  crack {p['crack_k']:.2f}"
            )
        except Exception:
            self.sel_acoustics.setText("")

    def _touch_window(self, w: Window):
        self.room.sync_window_coords(w)
        self.sel_open_lbl.setText(f"{int(w.open * 100)}%  ·  ~{w.open_angle_deg():.0f}°")
        self._update_window_acoustics_label(w)
        self._refresh_views()
        if self.gl:
            self.gl._init_particles()

    def _apply_sel_open(self, v: int):
        w = self._current_window()
        if not w:
            return
        w.open = v / 100.0
        self._touch_window(w)

    def _apply_sel_wall(self, wall: str):
        w = self._current_window()
        if not w:
            return
        w.wall = wall
        self._touch_window(w)

    def _apply_sel_width(self, v: float):
        w = self._current_window()
        if not w:
            return
        w.width = float(v)
        self._touch_window(w)

    def _apply_sel_height(self, v: float):
        w = self._current_window()
        if not w:
            return
        w.height = float(v)
        self._touch_window(w)

    def _apply_sel_sill(self, v: float):
        w = self._current_window()
        if not w:
            return
        w.sill = float(v)
        self._touch_window(w)

    def _apply_sel_style(self, *_):
        w = self._current_window()
        if not w:
            return
        key = self.sel_style.currentData()
        w.open_style = str(key or "casement")
        is_custom = w.open_style_norm() == "custom"
        self.sel_custom_box.setEnabled(is_custom)
        for wdg in (
            self.sel_custom_hinge, self.sel_custom_motion,
            self.sel_custom_out, self.sel_custom_notes,
        ):
            wdg.setEnabled(is_custom)
        self._touch_window(w)

    def _apply_sel_angle(self, v: float):
        w = self._current_window()
        if not w:
            return
        w.max_angle_deg = float(v)
        self._touch_window(w)

    def _apply_sel_hinge(self, side: str):
        w = self._current_window()
        if not w:
            return
        w.hinge_side = str(side or "left")
        self._touch_window(w)

    def _apply_custom_hinge(self, side: str):
        w = self._current_window()
        if not w:
            return
        w.custom_hinge = str(side or "left")
        self._touch_window(w)

    def _apply_custom_motion(self, motion: str):
        w = self._current_window()
        if not w:
            return
        w.custom_motion = str(motion or "swing")
        self._touch_window(w)

    def _apply_custom_out(self, on: bool):
        w = self._current_window()
        if not w:
            return
        w.custom_outward = bool(on)
        self._touch_window(w)

    def _apply_custom_notes(self):
        w = self._current_window()
        if not w:
            return
        w.custom_notes = self.sel_custom_notes.text().strip()
        self._touch_window(w)

    def _apply_spk_size_panel(self, v: float):
        kind, idx = self._selected
        if kind != "speaker" or not (0 <= idx < len(self.room.speakers)):
            return
        self.room.speakers[idx].size = float(v)
        self._refresh_views()

    def _apply_spk_y_panel(self, v: float):
        kind, idx = self._selected
        if kind != "speaker" or not (0 <= idx < len(self.room.speakers)):
            return
        self.room.speakers[idx].y = float(v)
        self._refresh_views()

    # ==================================================================
    # Speakers / devices
    # ==================================================================
    def _refresh_device_lists(self):
        devices = self.engine.refresh_devices()
        self.lst_devices.clear()
        self.spk_device.blockSignals(True)
        self.spk_device.clear()
        # None = system default for You; unassigned for room speakers
        self.spk_device.addItem("— System default / unassigned —", userData=None)
        for d in devices:
            host = d.get("hostapi_name") or ""
            tag = f" · {host}" if host and host != "?" else ""
            def_mark = " ★" if d.get("is_default") else ""
            label = f"{d['name']}{def_mark}  ({d['channels']} ch){tag}"
            self.lst_devices.addItem(label)
            self.lst_devices.item(self.lst_devices.count() - 1).setData(QtCore.Qt.UserRole, d["index"])
            self.spk_device.addItem(label, userData=d["index"])
        self.spk_device.blockSignals(False)
        self.statusBar().showMessage(f"Found {len(devices)} unique output device(s)")
        # Keep field widgets in sync with selection
        if self._selected[0] == "listener":
            self._load_you_fields()
        elif self._selected[0] == "speaker":
            self._load_speaker_fields(self._selected[1])
        self._update_sim_status()

    def _refresh_speaker_list(self):
        """List row 0 = You (headphones); rows 1.. = room speakers."""
        self.lst_speakers.blockSignals(True)
        cur = self.lst_speakers.currentRow()
        self.lst_speakers.clear()

        L = self.room.listener
        you_dev = getattr(L, "audio_device", None)
        if you_dev is None:
            you_tag = "system default"
        else:
            you_tag = f"dev {you_dev}"
        you_item = QtWidgets.QListWidgetItem(f"You  ·  headphones  ·  {you_tag}")
        you_item.setData(QtCore.Qt.UserRole, ("listener", 0))
        self.lst_speakers.addItem(you_item)

        for i, s in enumerate(self.room.speakers):
            dev = getattr(s, "audio_device", None)
            tag = f"dev {dev}" if dev is not None else "unassigned"
            flag = "" if s.enabled else " (off)"
            item = QtWidgets.QListWidgetItem(f"{s.name}  ·  speaker  ·  {tag}{flag}")
            item.setData(QtCore.Qt.UserRole, ("speaker", i))
            self.lst_speakers.addItem(item)

        if 0 <= cur < self.lst_speakers.count():
            self.lst_speakers.setCurrentRow(cur)
        self.lst_speakers.blockSignals(False)

    def _on_speaker_list(self, row: int):
        if row < 0:
            return
        item = self.lst_speakers.item(row)
        if item is None:
            return
        data = item.data(QtCore.Qt.UserRole)
        if not data:
            # Fallback: row 0 = You
            if row == 0:
                self._on_selection(("listener", 0))
                self.floor.set_selection("listener", 0)
            else:
                self._on_selection(("speaker", row - 1))
                self.floor.set_selection("speaker", row - 1)
            return
        kind, idx = data
        self._on_selection((kind, idx))
        self.floor.set_selection(kind, idx)

    def _set_device_combo(self, target):
        pick = 0
        for i in range(self.spk_device.count()):
            if self.spk_device.itemData(i) == target:
                pick = i
                break
        self.spk_device.setCurrentIndex(pick)

    def _load_you_fields(self):
        """Populate editor for the You / headphones output."""
        L = self.room.listener
        self.spk_name.blockSignals(True)
        self.spk_device.blockSignals(True)
        self.spk_gain.blockSignals(True)
        self.spk_enabled.blockSignals(True)
        self.spk_name.setText("You")
        self.spk_name.setEnabled(False)
        self.spk_gain.setValue(0.0)
        self.spk_gain.setEnabled(False)
        self.spk_enabled.setChecked(True)
        self.spk_enabled.setEnabled(False)
        self.spk_device.setEnabled(True)
        self._set_device_combo(getattr(L, "audio_device", None))
        self.btn_del_spk.setEnabled(False)
        self.btn_test_spk.setText("Test headphones")
        self.spk_name.blockSignals(False)
        self.spk_device.blockSignals(False)
        self.spk_gain.blockSignals(False)
        self.spk_enabled.blockSignals(False)

    def _load_speaker_fields(self, idx: int):
        if not (0 <= idx < len(self.room.speakers)):
            return
        s = self.room.speakers[idx]
        self.spk_name.blockSignals(True)
        self.spk_device.blockSignals(True)
        self.spk_gain.blockSignals(True)
        self.spk_enabled.blockSignals(True)
        self.spk_name.setEnabled(True)
        self.spk_gain.setEnabled(True)
        self.spk_enabled.setEnabled(True)
        self.spk_device.setEnabled(True)
        self.btn_del_spk.setEnabled(True)
        self.btn_test_spk.setText("Test this speaker")
        self.spk_name.setText(s.name)
        self.spk_gain.setValue(float(s.gain_db))
        self.spk_enabled.setChecked(bool(s.enabled))
        self._set_device_combo(getattr(s, "audio_device", None))
        self.spk_name.blockSignals(False)
        self.spk_device.blockSignals(False)
        self.spk_gain.blockSignals(False)
        self.spk_enabled.blockSignals(False)

    def _is_you_selected(self) -> bool:
        if self._selected[0] == "listener":
            return True
        row = self.lst_speakers.currentRow()
        if row == 0:
            return True
        item = self.lst_speakers.currentItem()
        if item is not None:
            data = item.data(QtCore.Qt.UserRole)
            if data and data[0] == "listener":
                return True
        return False

    def _current_speaker(self) -> Speaker | None:
        if self._is_you_selected():
            return None
        kind, idx = self._selected
        if kind == "speaker" and 0 <= idx < len(self.room.speakers):
            return self.room.speakers[idx]
        row = self.lst_speakers.currentRow()
        if row >= 1 and (row - 1) < len(self.room.speakers):
            return self.room.speakers[row - 1]
        return None

    def _you_headphones_device(self):
        """Device index for You binaural, or None for system default."""
        return getattr(self.room.listener, "audio_device", None)

    def _apply_spk_fields(self, *_):
        if self._is_you_selected():
            # Name / gain / enabled fixed for You
            return
        s = self._current_speaker()
        if not s:
            return
        s.name = self.spk_name.text().strip() or s.name
        s.gain_db = float(self.spk_gain.value())
        s.enabled = self.spk_enabled.isChecked()
        self._refresh_speaker_list()
        self._refresh_views()
        self._update_sim_status()

    def _apply_spk_device(self, *_):
        if self._is_you_selected():
            di = self.spk_device.currentData()
            self.room.listener.audio_device = int(di) if di is not None else None
            self._refresh_speaker_list()
            self._refresh_views()
            self._update_sim_status()
            tag = (
                f"device {self.room.listener.audio_device}"
                if self.room.listener.audio_device is not None
                else "system default"
            )
            self.statusBar().showMessage(f"You (headphones) → {tag}")
            return
        s = self._current_speaker()
        if not s:
            return
        s.audio_device = self.spk_device.currentData()
        self.engine.set_speaker_device(s, s.audio_device)
        self._refresh_speaker_list()
        self._refresh_views()
        self._update_sim_status()
        self.statusBar().showMessage(
            f"{s.name} → device {s.audio_device}" if s.audio_device is not None else f"{s.name} unassigned"
        )

    def _add_speaker(self):
        n = len(self.room.speakers)
        x = self.room.width * (0.25 + 0.15 * (n % 4))
        z = self.room.depth * (0.3 + 0.15 * ((n // 2) % 3))
        x, z = self.room.clamp_inside(x, z)
        s = Speaker(name=f"Speaker {n+1}", x=x, y=1.1, z=z)
        self.room.speakers.append(s)
        self._refresh_speaker_list()
        self._on_selection(("speaker", n))
        self.floor.set_selection("speaker", n)
        self._refresh_views()
        self.statusBar().showMessage(f"Added {s.name} — drag it to the real-world location")

    def _del_speaker(self):
        if self._is_you_selected():
            QtWidgets.QMessageBox.information(
                self, "You", "“You” is always present — assign a headphones device instead of removing it."
            )
            return
        kind, idx = self._selected
        if kind != "speaker" or not (0 <= idx < len(self.room.speakers)):
            row = self.lst_speakers.currentRow()
            idx = row - 1 if row >= 1 else -1
        if 0 <= idx < len(self.room.speakers):
            name = self.room.speakers[idx].name
            del self.room.speakers[idx]
            self._selected = ("listener", 0)
            self._refresh_speaker_list()
            self.lst_speakers.setCurrentRow(0)
            self._load_you_fields()
            self._refresh_views()
            self.statusBar().showMessage(f"Removed {name}")

    def _test_selected_device(self):
        item = self.lst_devices.currentItem()
        if not item:
            QtWidgets.QMessageBox.information(self, "Test device", "Select a device in the list first.")
            return
        di = item.data(QtCore.Qt.UserRole)
        try:
            self.engine.play_test_tone(int(di))
            self.statusBar().showMessage(f"Played test tone on device {di}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Test failed", str(e))

    def _test_selected_speaker(self):
        if self._is_you_selected():
            di = self._you_headphones_device()
            try:
                # None → system default device
                self.engine.play_test_tone(di if di is not None else None)
                tag = f"device {di}" if di is not None else "system default"
                self.statusBar().showMessage(f"Test tone → You / headphones ({tag})")
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Test failed", str(e))
            return
        s = self._current_speaker()
        if not s:
            QtWidgets.QMessageBox.information(self, "Test output", "Select You or a speaker first.")
            return
        try:
            idx = self.room.speakers.index(s)
            self.engine.play_speaker_test(s, index_hint=idx)
            self.statusBar().showMessage(f"Test tone → {s.name} (device {s.audio_device})")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Test failed", str(e))

    # ==================================================================
    # Simulate
    # ==================================================================
    @staticmethod
    def _sharpness_label(s: float) -> str:
        # Continuous label — no segmented named bands (timbre blends smoothly)
        s = max(0.0, min(1.0, float(s)))
        soft = 1.0 - s
        return f"{int(s * 100)}%  ·  soft {int(soft * 100)}% / bright {int(s * 100)}%"

    @staticmethod
    def _compass_name(deg: float) -> str:
        d = float(deg) % 360.0
        names = (
            "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
        )
        idx = int((d + 11.25) // 22.5) % 16
        return names[idx]

    @staticmethod
    def _wind_speed_label(s: float) -> str:
        s = max(0.0, min(1.0, float(s)))
        if s < 0.05:
            tag = "calm"
        elif s < 0.25:
            tag = "light breeze"
        elif s < 0.5:
            tag = "breeze"
        elif s < 0.75:
            tag = "strong"
        else:
            tag = "gale"
        return f"{int(round(s * 100))}%  ·  {tag}"

    @classmethod
    def _wind_dir_label(cls, deg: float) -> str:
        d = float(deg) % 360.0
        return f"{int(round(d))}°  ·  toward {cls._compass_name(d)}"

    @staticmethod
    def _deg_range_label(deg: int) -> str:
        return f"±{int(deg)}° around base"

    def _sync_wind_vary_enabled(self):
        d_on = self.chk_wind_vary_dir.isChecked()
        s_on = self.chk_wind_vary_spd.isChecked()
        for w in (
            self.sld_wind_dir_range, self.sld_wind_dir_interval, self.sld_wind_dir_slew,
        ):
            w.setEnabled(d_on)
        for w in (
            self.sld_wind_spd_range, self.sld_wind_spd_interval, self.sld_wind_spd_slew,
        ):
            w.setEnabled(s_on)

    def _on_rain(self, v: int):
        # rain_intensity = sharpness (timbre), not drop count
        self.room.rain_intensity = v / 100.0
        self.lbl_rain.setText(self._sharpness_label(self.room.rain_intensity))
        if self.gl:
            try:
                self.gl._init_particles()
            except Exception:
                pass
        self.view3d.update()

    def _on_wind_speed(self, v: int):
        self.room.wind_speed = max(0.0, min(1.0, v / 100.0))
        self.lbl_wind_speed.setText(self._wind_speed_label(self.room.wind_speed))
        self.view3d.update()

    def _on_wind_dir_dial(self, v: int):
        self.sld_wind_dir.blockSignals(True)
        self.sld_wind_dir.setValue(int(v) % 360)
        self.sld_wind_dir.blockSignals(False)
        self.room.wind_direction_deg = float(v % 360)
        self.lbl_wind_dir.setText(self._wind_dir_label(self.room.wind_direction_deg))
        self.view3d.update()

    def _on_wind_dir_slider(self, v: int):
        self.dial_wind_dir.blockSignals(True)
        self.dial_wind_dir.setValue(int(v) % 360)
        self.dial_wind_dir.blockSignals(False)
        self.room.wind_direction_deg = float(v % 360)
        self.lbl_wind_dir.setText(self._wind_dir_label(self.room.wind_direction_deg))
        self.view3d.update()

    def _on_wind_vary_dir(self, on: bool):
        self.room.wind_vary_direction = bool(on)
        self._sync_wind_vary_enabled()

    def _on_wind_dir_range(self, v: int):
        self.room.wind_dir_range_deg = float(v)
        self.lbl_wind_dir_range.setText(self._deg_range_label(v))

    def _on_wind_dir_interval(self, v: int):
        self.room.wind_dir_interval_s = float(v)
        self.lbl_wind_dir_interval.setText(f"every ~{v}s")

    def _on_wind_dir_slew(self, v: int):
        self.room.wind_dir_slew_deg_s = float(v)
        self.lbl_wind_dir_slew.setText(f"{v}°/s turn rate")

    def _on_wind_vary_spd(self, on: bool):
        self.room.wind_vary_speed = bool(on)
        self._sync_wind_vary_enabled()

    def _on_wind_spd_range(self, v: int):
        self.room.wind_speed_range = v / 100.0
        self.lbl_wind_spd_range.setText(f"±{v}% speed")

    def _on_wind_spd_interval(self, v: int):
        self.room.wind_speed_interval_s = float(v)
        self.lbl_wind_spd_interval.setText(f"every ~{v}s")

    def _on_wind_spd_slew(self, v: int):
        self.room.wind_speed_slew_per_s = v / 100.0
        self.lbl_wind_spd_slew.setText(f"{v}%/s ramp")

    @staticmethod
    def _quantity_label(q: float, wind: float = 0.0) -> str:
        q = max(0.0, min(1.0, float(q)))
        if q < 0.12:
            tag = "sparse drizzle"
        elif q < 0.35:
            tag = "light rain"
        elif q < 0.60:
            tag = "steady rain"
        elif q < 0.85:
            tag = "heavy rain"
        else:
            tag = "downpour"
        return f"{int(q * 100)}%  ·  {tag}"

    @staticmethod
    def _volume_label(v: float) -> str:
        v = max(0.0, min(1.0, float(v)))
        if v <= 0.001:
            return "0%  ·  muted"
        if v < 0.35:
            tag = "quiet"
        elif v < 0.65:
            tag = "medium"
        elif v < 0.90:
            tag = "loud"
        else:
            tag = "full scale"
        return f"{int(round(v * 100))}%  ·  {tag}"

    def _on_density(self, v: int):
        # droplet_density = quantity (continuous field mass + discrete accents)
        self.room.droplet_density = v / 100.0
        self.lbl_density.setText(self._quantity_label(self.room.droplet_density))
        self.view3d.update()

    def _on_volume(self, v: int):
        self.room.master_volume = max(0.0, min(1.0, v / 100.0))
        self.engine.set_volume(self.room.master_volume)
        self.lbl_volume.setText(self._volume_label(self.room.master_volume))

    def _update_sim_status(self):
        assigned = self.room.assigned_speakers()
        enabled = [s for s in self.room.speakers if getattr(s, "enabled", True)]
        lines = [
            f"{len(self.room.windows)} window(s) · {len(self.room.speakers)} speaker(s) "
            f"({len(enabled)} on) · {len(assigned)} device-mapped"
        ]
        L = self.room.listener
        you_dev = getattr(L, "audio_device", None)
        you_tag = f"dev {you_dev}" if you_dev is not None else "system default"
        lines.append(f"· You (headphones): ({L.x:.1f}, {L.z:.1f}) m · {you_tag}")
        for s in self.room.speakers:
            dev = getattr(s, "audio_device", None)
            tag = f"dev {dev}" if dev is not None else "no device"
            off = "" if s.enabled else " [off]"
            lines.append(f"· {s.name}: ({s.x:.1f}, {s.z:.1f}) · {tag}{off}")
        if not enabled:
            lines.append("Add speakers (or Place 3 evenly) for multi-speaker play.")
        if not assigned:
            lines.append("Map devices in the Speakers step to use mapped speakers.")
        self.lbl_routing.setText("\n".join(lines))

    def _place_three_speakers(self):
        place_speakers_evenly(self.room, count=3)
        self._refresh_views()
        self._refresh_speaker_list()
        self._update_sim_status()
        self.statusBar().showMessage(
            "Placed 3 speakers — open Speakers step and assign each a real OS device"
        )

    def _ensure_speakers_for_multi(self) -> bool:
        """Return True if at least one speaker is device-mapped."""
        if self.room.assigned_speakers():
            return True
        if not self.room.speakers:
            ans = QtWidgets.QMessageBox.question(
                self,
                "No room speakers",
                "This house has no speakers — only “You” (headphones).\n\n"
                "Place 3 speakers now so you can map them to real outputs?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if ans != QtWidgets.QMessageBox.Yes:
                return False
            self._place_three_speakers()
        if not self.room.assigned_speakers():
            QtWidgets.QMessageBox.information(
                self,
                "Assign devices",
                "Speakers exist but none have an OS device assigned.\n\n"
                "1) Open the Speakers step\n"
                "2) Select each speaker\n"
                "3) Choose a real output (e.g. Speakers, monitor, cable)\n"
                "4) Test this speaker, then play multi again\n\n"
                "Tip: do not map every speaker to your headphones unless you want that.",
            )
            self.nav.setCurrentRow(1)
            return False
        return True

    def _play_multi(self):
        if not self._ensure_speakers_for_multi():
            return
        try:
            self.engine.stop_all()
            self.engine.room = self.room
            self.engine.start(include_you=False)
            self._play_mode = "multi"
            self.lbl_live.setText("● Mapped speakers only")
            self.lbl_live.setObjectName("LiveOk")
            self.lbl_live.style().unpolish(self.lbl_live)
            self.lbl_live.style().polish(self.lbl_live)
            n = len(self.room.assigned_speakers())
            devs = sorted({int(s.audio_device) for s in self.room.assigned_speakers()})
            self.statusBar().showMessage(
                f"Speakers only → {n} mapped on device(s) {devs} (not default headphones)"
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Playback error", str(e))

    def _play_all(self):
        if not self._ensure_speakers_for_multi():
            return
        try:
            self.engine.stop_all()
            self.engine.room = self.room
            hp = self._you_headphones_device()
            self.engine.start(include_you=True, headphones_device=hp)
            self._play_mode = "all"
            self.lbl_live.setText("● You + speakers")
            self.lbl_live.setObjectName("LiveOk")
            self.lbl_live.style().unpolish(self.lbl_live)
            self.lbl_live.style().polish(self.lbl_live)
            n = len(self.room.assigned_speakers())
            hp_tag = f"dev {hp}" if hp is not None else "system default"
            self.statusBar().showMessage(
                f"You ({hp_tag}) + {n} mapped speaker device(s)"
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Playback error", str(e))

    def _play_headphones(self):
        try:
            self.engine.stop_all()
            self.engine.room = self.room
            hp = self._you_headphones_device()
            self.engine.start_headphones(headphones_device=hp)
            self._play_mode = "headphones"
            self.lbl_live.setText("● You only")
            self.lbl_live.setObjectName("LiveOk")
            self.lbl_live.style().unpolish(self.lbl_live)
            self.lbl_live.style().polish(self.lbl_live)
            hp_tag = f"device {hp}" if hp is not None else "system default"
            self.statusBar().showMessage(
                f"You only → headphones on {hp_tag}. Mapped room speakers silent."
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Playback error", str(e))

    def _stop_audio(self):
        try:
            self.engine.stop_all()
        except Exception:
            pass
        self._play_mode = None
        self.lbl_live.setText("● Stopped")
        self.lbl_live.setObjectName("LiveIdle")
        self.lbl_live.style().unpolish(self.lbl_live)
        self.lbl_live.style().polish(self.lbl_live)
        self.statusBar().showMessage("Audio stopped")

    # ==================================================================
    # Project I/O
    # ==================================================================
    def _new_house(self):
        self._stop_audio()
        self.room = default_house()
        self.engine.room = self.room
        self._apply_room_to_ui()
        self._refresh_views()
        self._refresh_speaker_list()
        self._update_sim_status()
        self.statusBar().showMessage("New house ready")

    @staticmethod
    def _load_startup_preset() -> Room:
        """Ship default: configs/my_house.json (user Living Room layout)."""
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        candidates = (
            Path("configs/my_house.json"),
            root / "configs" / "my_house.json",
            Path("configs/default_house.json"),
            root / "configs" / "default_house.json",
        )
        for p in candidates:
            if p.is_file():
                try:
                    return load_room(str(p))
                except Exception:
                    pass
        return default_house()

    def _apply_room_to_ui(self):
        """Sync inspector widgets from self.room (load / new / startup)."""
        self.ed_name.setText(self.room.name)
        self.spin_w.setValue(self.room.width)
        self.spin_d.setValue(self.room.depth)
        self.spin_h.setValue(self.room.height)
        self.spin_terrain.setValue(self.room.terrain_size)
        self._select_combo(self.cmb_roof, self.room.roof_material)
        self._select_combo(self.cmb_wall, self.room.wall_material)
        self.sld_rain.blockSignals(True)
        self.sld_density.blockSignals(True)
        self.sld_volume.blockSignals(True)
        self.sld_wind_speed.blockSignals(True)
        self.dial_wind_dir.blockSignals(True)
        self.sld_wind_dir.blockSignals(True)
        self.sld_rain.setValue(int(round(self.room.rain_intensity * 100)))
        self.sld_density.setValue(int(round(self.room.droplet_density * 100)))
        vol = float(getattr(self.room, "master_volume", 0.75))
        self.sld_volume.setValue(int(round(vol * 100)))
        spd = float(getattr(self.room, "wind_speed", 0.0))
        deg = int(round(float(getattr(self.room, "wind_direction_deg", 90.0)))) % 360
        self.sld_wind_speed.setValue(int(round(spd * 100)))
        self.dial_wind_dir.setValue(deg)
        self.sld_wind_dir.setValue(deg)
        self.chk_wind_vary_dir.setChecked(bool(getattr(self.room, "wind_vary_direction", False)))
        self.sld_wind_dir_range.setValue(int(round(float(getattr(self.room, "wind_dir_range_deg", 45.0)))))
        self.sld_wind_dir_interval.setValue(int(round(float(getattr(self.room, "wind_dir_interval_s", 10.0)))))
        self.sld_wind_dir_slew.setValue(int(round(float(getattr(self.room, "wind_dir_slew_deg_s", 15.0)))))
        self.chk_wind_vary_spd.setChecked(bool(getattr(self.room, "wind_vary_speed", False)))
        self.sld_wind_spd_range.setValue(int(round(float(getattr(self.room, "wind_speed_range", 0.25)) * 100)))
        self.sld_wind_spd_interval.setValue(int(round(float(getattr(self.room, "wind_speed_interval_s", 8.0)))))
        self.sld_wind_spd_slew.setValue(int(round(float(getattr(self.room, "wind_speed_slew_per_s", 0.20)) * 100)))
        self.sld_rain.blockSignals(False)
        self.sld_density.blockSignals(False)
        self.sld_volume.blockSignals(False)
        self.sld_wind_speed.blockSignals(False)
        self.dial_wind_dir.blockSignals(False)
        self.sld_wind_dir.blockSignals(False)
        self.lbl_rain.setText(self._sharpness_label(self.room.rain_intensity))
        self.lbl_density.setText(self._quantity_label(self.room.droplet_density))
        self.lbl_volume.setText(self._volume_label(vol))
        self.lbl_wind_speed.setText(self._wind_speed_label(spd))
        self.lbl_wind_dir.setText(self._wind_dir_label(deg))
        self.lbl_wind_dir_range.setText(self._deg_range_label(self.sld_wind_dir_range.value()))
        self.lbl_wind_dir_interval.setText(f"every ~{self.sld_wind_dir_interval.value()}s")
        self.lbl_wind_dir_slew.setText(f"{self.sld_wind_dir_slew.value()}°/s turn rate")
        self.lbl_wind_spd_range.setText(f"±{self.sld_wind_spd_range.value()}% speed")
        self.lbl_wind_spd_interval.setText(f"every ~{self.sld_wind_spd_interval.value()}s")
        self.lbl_wind_spd_slew.setText(f"{self.sld_wind_spd_slew.value()}%/s ramp")
        self._sync_wind_vary_enabled()
        self.engine.set_volume(vol)
        self.floor.set_room(self.room)

    def _load_house(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load house", "configs", "JSON (*.json)"
        )
        if not path:
            return
        try:
            self._stop_audio()
            self.room = load_room(path)
            self.engine.room = self.room
            self._apply_room_to_ui()
            self._refresh_views()
            self._refresh_speaker_list()
            self._update_sim_status()
            self.statusBar().showMessage(f"Loaded {path}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load failed", str(e))

    def _save_house(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save house", "configs/my_house.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            save_room(self.room, path)
            self.statusBar().showMessage(f"Saved {path}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e))

    def closeEvent(self, e: QtGui.QCloseEvent):
        self._stop_audio()
        super().closeEvent(e)

    # ==================================================================
    # Project page / about
    # ==================================================================
    def _open_project_page(self):
        ok = webbrowser.open(PROJECT_URL)
        if ok:
            self.statusBar().showMessage(f"Opened {PROJECT_URL}")
        else:
            QtWidgets.QMessageBox.information(
                self,
                "Project page",
                f"Open this URL in your browser:\n\n{PROJECT_URL}",
            )

    def _copy_project_url(self):
        QtWidgets.QApplication.clipboard().setText(PROJECT_URL)
        self.statusBar().showMessage("Project URL copied to clipboard")

    def _show_about(self):
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(f"About {PROJECT_NAME}")
        box.setTextFormat(QtCore.Qt.RichText)
        box.setText(
            f"<h3>{PROJECT_NAME}</h3>"
            "<p>Design your house on a terrain map, map real speakers, "
            "and simulate 3D outdoor rain through your windows.</p>"
            f'<p><b>Project page:</b><br>'
            f'<a href="{PROJECT_URL}">{PROJECT_URL}</a></p>'
            "<p><b>How to listen in 3D:</b> use "
            "<i>3D binaural (headphones)</i> on a stereo headphone output.</p>"
        )
        box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        open_btn = box.addButton("Open project page", QtWidgets.QMessageBox.ActionRole)
        box.exec()
        if box.clickedButton() is open_btn:
            self._open_project_page()


def main():
    # HiDPI
    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    # Desktop OpenGL + shared contexts BEFORE QApplication (critical on Windows/Qt6)
    try:
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts, True)
    except Exception:
        pass
    try:
        # Prefer real GPU GL over ANGLE/software when available
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseDesktopOpenGL, True)
    except Exception:
        pass
    # Compatibility surface format before any GL widget exists
    try:
        if configure_gl_surface_format is not None:
            configure_gl_surface_format()
        else:
            from PySide6.QtGui import QSurfaceFormat
            fmt = QSurfaceFormat()
            fmt.setDepthBufferSize(24)
            fmt.setStencilBufferSize(8)
            fmt.setVersion(2, 1)
            fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
            fmt.setRenderableType(QSurfaceFormat.OpenGL)
            QSurfaceFormat.setDefaultFormat(fmt)
    except Exception:
        log.exception("Failed to set default OpenGL surface format")

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    app.setApplicationName("RainRoom3D")
    w = Main()
    w.show()
    # If OpenGL view exists, force context creation so status is known early
    if w.gl is not None:
        try:
            w.gl.makeCurrent()
            # initializeGL runs on first makeCurrent/show
            if getattr(w.gl, "_gl_ok", False):
                log.info("OpenGL view ready at startup")
            elif getattr(w.gl, "_gl_error", ""):
                log.warning("OpenGL view degraded: %s", w.gl._gl_error)
            w.gl.doneCurrent()
        except Exception as e:
            log.warning("OpenGL warm-up failed: %s", e)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
