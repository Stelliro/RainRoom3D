#!/usr/bin/env python3
"""Capture real RainRoom3D UI screenshots for the README.

Usage (from repo root):
  python scripts/capture_screenshots.py

Writes PNG files under docs/media/.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Prefer real Windows window for best look; fall back to offscreen if needed.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("RAINROOM_NOGPU", "1")

from PySide6 import QtCore, QtGui, QtWidgets

from app.ui.theme import APP_STYLESHEET
from app.main import Main


def _save(widget: QtWidgets.QWidget, path: Path, scale: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pix = widget.grab()
    if scale != 1.0 and not pix.isNull():
        pix = pix.scaled(
            int(pix.width() * scale),
            int(pix.height() * scale),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
    # Convert to high-quality JPEG for smaller README assets
    if path.suffix.lower() in (".jpg", ".jpeg"):
        pix.save(str(path), "JPG", 92)
    else:
        pix.save(str(path), "PNG")
    print(f"  wrote {path}  ({pix.width()}x{pix.height()})")


def main() -> int:
    out = ROOT / "docs" / "media"
    out.mkdir(parents=True, exist_ok=True)

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    app.setApplicationName("RainRoom3D")

    win = Main()
    win.resize(1520, 960)
    win.show()
    # Process events so layouts settle
    for _ in range(12):
        app.processEvents()
        QtCore.QThread.msleep(40)

    # Tighter terrain for readable floor-plan shots (restored after capture)
    _saved_terrain = float(win.room.terrain_size)
    win.room.terrain_size = min(_saved_terrain, 14.0)
    win.spin_terrain.blockSignals(True)
    win.spin_terrain.setValue(win.room.terrain_size)
    win.spin_terrain.blockSignals(False)

    # Ensure a window is selected for a richer Design panel
    if win.room.windows:
        win._on_selection(("window", 0))
        win.floor.set_selection("window", 0, emit=False)
    win.floor.set_room(win.room)
    win.floor._fit_view()
    app.processEvents()
    QtCore.QThread.msleep(80)

    print("Capturing screenshots…")

    # 1) Full window — Design / floor plan
    win.nav.setCurrentRow(0)
    win._on_step(0)
    win._set_view(0)
    if win.room.windows:
        win._on_selection(("window", 0))
    win.floor._fit_view()
    win.floor.update()
    app.processEvents()
    QtCore.QThread.msleep(100)
    _save(win, out / "screenshot_design_plan.jpg")

    # 2) Design / software 3D
    win._set_view(1)
    app.processEvents()
    QtCore.QThread.msleep(120)
    win.view3d.fit_camera()
    app.processEvents()
    QtCore.QThread.msleep(80)
    _save(win, out / "screenshot_design_3d.jpg")

    # 3) Speakers step
    win.nav.setCurrentRow(1)
    win._on_step(1)
    app.processEvents()
    QtCore.QThread.msleep(100)
    _save(win, out / "screenshot_speakers.jpg")

    # 4) Simulate step
    win.nav.setCurrentRow(2)
    win._on_step(2)
    win._set_view(1)
    app.processEvents()
    QtCore.QThread.msleep(120)
    _save(win, out / "screenshot_simulate.jpg")

    # 5) Crop-friendly: canvas only (floor plan)
    win.nav.setCurrentRow(0)
    win._on_step(0)
    win._set_view(0)
    app.processEvents()
    QtCore.QThread.msleep(80)
    _save(win.floor, out / "screenshot_floorplan_closeup.jpg")

    # 6) 3D view close-up
    win._set_view(1)
    app.processEvents()
    QtCore.QThread.msleep(100)
    _save(win.view3d, out / "screenshot_3d_closeup.jpg")

    # Hero = design floor plan (readable house)
    win.nav.setCurrentRow(0)
    win._on_step(0)
    win._set_view(0)
    if win.room.windows:
        win._on_selection(("window", 0))
    win.floor._fit_view()
    win.floor.update()
    app.processEvents()
    QtCore.QThread.msleep(80)
    _save(win, out / "hero_app.jpg")
    _save(win, out / "screenshot_design_plan.jpg")

    # Restore terrain size for honesty (user layout)
    win.room.terrain_size = _saved_terrain

    # 3D hero secondary
    win._set_view(1)
    app.processEvents()
    win.view3d.fit_camera()
    app.processEvents()
    QtCore.QThread.msleep(80)
    _save(win, out / "hero_3d.jpg")

    win.close()
    app.quit()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
