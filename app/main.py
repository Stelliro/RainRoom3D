
"""
RainRoom3D — design your house, map your speakers, hear outdoor rain in 3D.

This module is only the entry point. The UI lives in ``app/ui/studio``:

    window.py         StudioWindow — wires the modules, files, menus, keys, modes
    context.py        shared state (room, engine, selection) + room operations
    app_bar.py        wordmark · house name · Design | Listen · weather · menus
    canvas.py         plan / 3D views with floating chrome on top
    palette.py        tool palette        objects_panel.py   objects list
    inspector.py      floating inspector  page_*.py          its pages
    weather_dock.py   rain & sound dock (built from settings_schema)
    transport.py      Play / Stop · output · headphones · volume
    controls.py       shared controls     icons.py           drawn icons

Run:  python -m app.main
"""

from __future__ import annotations

import logging
import sys

if __name__ == "__main__" and "--rainroom-zone-worker" in sys.argv:
    # A phone room's sound worker (app/remote/worker.py) started by this app:
    # render only, before Qt or the log file are touched.
    from app.remote.worker import serve as _serve_zone_worker
    sys.exit(_serve_zone_worker(sys.argv))

from PySide6 import QtCore, QtWidgets

from app.logging_setup import setup_logging

LOG_PATH = setup_logging()
log = logging.getLogger("ui")

from app.ui.studio.canvas import GL_AVAILABLE, configure_gl_surface_format  # noqa: E402
from app.ui.studio.controls import _from_ticks, _ticks  # noqa: E402,F401  (used by tests/scripts)
from app.ui.studio.transport import PLAY_OUTPUTS  # noqa: E402,F401
from app.ui.studio.window import PROJECT_NAME, PROJECT_URL, StudioWindow  # noqa: E402,F401
from app.ui.theme import APP_STYLESHEET, apply_theme  # noqa: E402,F401
from app.version import __version__  # noqa: E402

# The main window class (kept under its historic name for scripts and tests)
Main = StudioWindow


def _configure_gl() -> None:
    # Desktop OpenGL + shared contexts BEFORE QApplication (critical on Windows / Qt6)
    for attr in ("AA_ShareOpenGLContexts", "AA_UseDesktopOpenGL"):
        try:
            QtCore.QCoreApplication.setAttribute(getattr(QtCore.Qt, attr), True)
        except Exception:
            pass
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


def main():
    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    _configure_gl()
    app = QtWidgets.QApplication(sys.argv)
    apply_theme(app)
    app.setApplicationName("RainRoom3D")
    app.setApplicationVersion(__version__)
    log.info("RainRoom3D %s", __version__)
    w = Main()
    w.show()
    # If the OpenGL view exists, create its context now so its status is known early
    if w.gl is not None:
        try:
            w.gl.makeCurrent()
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
