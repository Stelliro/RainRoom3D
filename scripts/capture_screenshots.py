#!/usr/bin/env python3
"""Capture real RainRoom3D UI screenshots for the README.

Usage (from repo root):
  python scripts/capture_screenshots.py

Writes JPG files under docs/media/.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("RAINROOM_NOGPU", "1")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from app.main import Main  # noqa: E402
from app.ui.theme import apply_theme  # noqa: E402


def _save(widget: QtWidgets.QWidget, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pix = widget.grab()
    if path.suffix.lower() in (".jpg", ".jpeg"):
        pix.save(str(path), "JPG", 92)
    else:
        pix.save(str(path), "PNG")
    print(f"  wrote {path}  ({pix.width()}x{pix.height()})")


def _settle(app, ms: int = 80) -> None:
    for _ in range(8):
        app.processEvents()
        QtCore.QThread.msleep(ms // 8 + 1)


def main() -> int:
    out = ROOT / "docs" / "media"
    app = QtWidgets.QApplication(sys.argv)
    apply_theme(app)
    app.setApplicationName("RainRoom3D")

    win = Main()
    win.resize(1520, 960)
    win.show()
    _settle(app, 300)
    print("Capturing screenshots…")

    # 1) Design / floor plan with a window selected
    win.set_mode("design")
    win.canvas.set_view("plan")
    if win.room.windows:
        win.ctx.select(("window", 0), force=True)
    win.canvas.fit_view()
    _settle(app)
    _save(win, out / "screenshot_design_plan.jpg")
    _save(win, out / "hero_app.jpg")

    # 2) Design / 3D
    win.canvas.set_view("3d")
    win.canvas.view3d.fit_camera()
    _settle(app, 150)
    _save(win, out / "screenshot_design_3d.jpg")

    # 3) A speaker selected (device + placement inspector)
    if win.room.speakers:
        win.ctx.select(("speaker", 0), force=True)
        win.canvas.set_view("plan")
        _settle(app)
        _save(win, out / "screenshot_speakers.jpg")

    # 4) Listen mode
    win.ctx.select(("none", -1), force=True)
    win.set_mode("listen")
    _settle(app, 150)
    _save(win, out / "screenshot_simulate.jpg")
    _save(win, out / "hero_3d.jpg")

    # 5) Close-ups of the two canvases
    win.set_mode("design")
    win.canvas.set_view("plan")
    _settle(app)
    _save(win.canvas.floor, out / "screenshot_floorplan_closeup.jpg")
    win.canvas.set_view("3d")
    _settle(app, 120)
    _save(win.canvas.view3d, out / "screenshot_3d_closeup.jpg")

    win.close()
    app.quit()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
