import logging, os, sys, datetime
from PySide6 import QtCore

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "app.log")

def _qt_message_handler(mode, context, message):
    # Capture Qt (and OpenGL) messages
    lvl = logging.INFO
    if mode == QtCore.QtMsgType.QtWarningMsg: lvl = logging.WARNING
    elif mode == QtCore.QtMsgType.QtCriticalMsg: lvl = logging.CRITICAL
    elif mode == QtCore.QtMsgType.QtFatalMsg: lvl = logging.CRITICAL
    logging.getLogger("qt").log(lvl, message)

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    # file handler
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # console handler (so run_debug captures it too)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    # Hook Qt messages
    QtCore.qInstallMessageHandler(_qt_message_handler)
    logger.info("Logging initialized at %s", LOG_PATH)
    return LOG_PATH
