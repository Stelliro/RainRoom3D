"""Drawn icons (no emoji, no image files).

Each icon is a few vector strokes on a 24x24 grid, painted with QPainter into
crisp high-DPI pixmaps. ``icon(name)`` returns a QIcon whose normal / checked
/ disabled states use the theme's text, accent and disabled colours.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Callable, Dict, Optional

from PySide6 import QtCore, QtGui

from app.ui.theme import T, qcolor

_P = QtCore.QPointF


def _poly(p: QtGui.QPainter, pts, closed=False) -> None:
    path = QtGui.QPainterPath(_P(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(_P(*pt))
    if closed:
        path.closeSubpath()
    p.drawPath(path)


def _line(p, a, b) -> None:
    p.drawLine(_P(*a), _P(*b))


def _fill(p: QtGui.QPainter) -> None:
    p.setBrush(p.pen().color())


# ----- glyphs (24x24 grid, stroke set by caller) -----

def _select(p):
    _poly(p, [(5, 3.5), (18.5, 10.5), (12.2, 12.2), (9.6, 18.8)], closed=True)
    _line(p, (12.8, 12.8), (18, 18))


def _window(p):
    p.drawRoundedRect(QtCore.QRectF(4, 4, 16, 16), 2, 2)
    _line(p, (12, 4), (12, 20))
    _line(p, (4, 12), (20, 12))


def _speaker(p):
    p.drawRoundedRect(QtCore.QRectF(6, 2.5, 12, 19), 2.5, 2.5)
    p.drawEllipse(_P(12, 14.5), 3.6, 3.6)
    p.drawEllipse(_P(12, 7), 0.9, 0.9)


def _you(p):
    p.drawEllipse(_P(12, 7.5), 3.8, 3.8)
    path = QtGui.QPainterPath(_P(4.5, 21))
    path.cubicTo(_P(4.5, 15.5), _P(8, 13.5), _P(12, 13.5))
    path.cubicTo(_P(16, 13.5), _P(19.5, 15.5), _P(19.5, 21))
    p.drawPath(path)


def _resize(p):
    _poly(p, [(14, 4), (20, 4), (20, 10)])
    _poly(p, [(10, 20), (4, 20), (4, 14)])
    _line(p, (20, 4), (13.5, 10.5))
    _line(p, (4, 20), (10.5, 13.5))


def _fit(p):
    for pts in (((3.5, 8.5), (3.5, 3.5), (8.5, 3.5)), ((15.5, 3.5), (20.5, 3.5), (20.5, 8.5)),
                ((20.5, 15.5), (20.5, 20.5), (15.5, 20.5)), ((8.5, 20.5), (3.5, 20.5), (3.5, 15.5))):
        _poly(p, list(pts))
    p.drawRoundedRect(QtCore.QRectF(8, 8, 8, 8), 1.5, 1.5)


def _plus(p):
    _line(p, (12, 5), (12, 19))
    _line(p, (5, 12), (19, 12))


def _close(p):
    _line(p, (6, 6), (18, 18))
    _line(p, (18, 6), (6, 18))


def _trash(p):
    _line(p, (4, 6.5), (20, 6.5))
    _poly(p, [(6, 6.5), (7, 20.5), (17, 20.5), (18, 6.5)])
    _poly(p, [(9, 6.5), (9.5, 3.5), (14.5, 3.5), (15, 6.5)])
    _line(p, (10, 10.5), (10.3, 17))
    _line(p, (14, 10.5), (13.7, 17))


def _chev(dir_: str):
    pts = {
        "down": [(6, 9), (12, 15), (18, 9)],
        "up": [(6, 15), (12, 9), (18, 15)],
        "right": [(9, 6), (15, 12), (9, 18)],
        "left": [(15, 6), (9, 12), (15, 18)],
    }[dir_]
    return lambda p: _poly(p, pts)


def _play(p):
    p.setBrush(p.pen().color())
    path = QtGui.QPainterPath(_P(7.5, 4.8))
    path.lineTo(_P(19.5, 12))
    path.lineTo(_P(7.5, 19.2))
    path.closeSubpath()
    p.drawPath(path)


def _stop(p):
    p.setBrush(p.pen().color())
    p.drawRoundedRect(QtCore.QRectF(6.5, 6.5, 11, 11), 2, 2)


def _gear(p):
    pts = []
    for k in range(8):
        a = k * 45.0
        for da, r in ((-15, 7.0), (-9, 9.6), (9, 9.6), (15, 7.0)):
            t = math.radians(a + da)
            pts.append((12 + r * math.sin(t), 12 - r * math.cos(t)))
    _poly(p, pts, closed=True)
    p.drawEllipse(_P(12, 12), 3, 3)


def _cloud_rain(p):
    cloud = QtGui.QPainterPath()
    for (cx, cy, r) in ((8.5, 11, 3.6), (12.5, 8.5, 4.6), (16.5, 11.2, 3.3)):
        e = QtGui.QPainterPath()
        e.addEllipse(_P(cx, cy), r, r)
        cloud = cloud.united(e)
    base = QtGui.QPainterPath()
    base.addRoundedRect(QtCore.QRectF(5, 10.5, 15, 4.5), 2.2, 2.2)
    cloud = cloud.united(base).simplified()
    p.drawPath(cloud)
    for x in (8.5, 12.5, 16.5):
        _line(p, (x, 17.5), (x - 1.2, 21))


def _droplet(p):
    path = QtGui.QPainterPath(_P(12, 3))
    path.cubicTo(_P(12, 3), _P(5.5, 10.5), _P(5.5, 14.5))
    path.cubicTo(_P(5.5, 18.3), _P(8.4, 21), _P(12, 21))
    path.cubicTo(_P(15.6, 21), _P(18.5, 18.3), _P(18.5, 14.5))
    path.cubicTo(_P(18.5, 10.5), _P(12, 3), _P(12, 3))
    p.drawPath(path)


def _droplet_fill(p):
    c = QtGui.QColor(p.pen().color())
    p.setBrush(c)
    _droplet(p)


def _plan(p):
    p.drawRoundedRect(QtCore.QRectF(3.5, 3.5, 17, 17), 2, 2)
    _line(p, (3.5, 10), (20.5, 10))
    _line(p, (10, 10), (10, 20.5))


def _cube(p):
    _poly(p, [(12, 2.8), (20.2, 7.3), (20.2, 16.7), (12, 21.2), (3.8, 16.7), (3.8, 7.3)], closed=True)
    _poly(p, [(3.8, 7.3), (12, 11.8), (20.2, 7.3)])
    _line(p, (12, 11.8), (12, 21.2))


def _cube_gpu(p):
    _cube(p)
    p.setBrush(p.pen().color())
    p.drawEllipse(_P(19.5, 19.5), 2.4, 2.4)


def _headphones(p):
    path = QtGui.QPainterPath(_P(4, 15))
    path.lineTo(_P(4, 12))
    path.arcTo(QtCore.QRectF(4, 4, 16, 16), 180, -180)
    path.lineTo(_P(20, 15))
    p.drawPath(path)
    p.drawRoundedRect(QtCore.QRectF(3, 13.5, 5, 7), 2, 2)
    p.drawRoundedRect(QtCore.QRectF(16, 13.5, 5, 7), 2, 2)


def _volume(p):
    _poly(p, [(4, 9.5), (8, 9.5), (12.5, 5.5), (12.5, 18.5), (8, 14.5), (4, 14.5)], closed=True)
    path = QtGui.QPainterPath()
    path.arcMoveTo(QtCore.QRectF(11, 8, 8, 8), 60)
    path.arcTo(QtCore.QRectF(11, 8, 8, 8), 60, -120)
    p.drawPath(path)
    path = QtGui.QPainterPath()
    path.arcMoveTo(QtCore.QRectF(9, 4.5, 14, 15), 55)
    path.arcTo(QtCore.QRectF(9, 4.5, 14, 15), 55, -110)
    p.drawPath(path)


def _house(p):
    _poly(p, [(3, 11.5), (12, 4), (21, 11.5)])
    _poly(p, [(5.5, 9.8), (5.5, 20), (18.5, 20), (18.5, 9.8)])
    _poly(p, [(10, 20), (10, 14.5), (14, 14.5), (14, 20)])


def _pencil(p):
    _poly(p, [(15.5, 4.5), (19.5, 8.5), (8.5, 19.5), (4.5, 19.5), (4.5, 15.5)], closed=True)
    _line(p, (13, 7), (17, 11))


def _layers(p):
    _poly(p, [(12, 3.5), (20.5, 8), (12, 12.5), (3.5, 8)], closed=True)
    _poly(p, [(3.5, 12.2), (12, 16.7), (20.5, 12.2)])
    _poly(p, [(3.5, 16.2), (12, 20.7), (20.5, 16.2)])


def _sliders(p):
    for y, x in ((6, 15), (12, 8), (18, 13)):
        _line(p, (4, y), (20, y))
        p.setBrush(qcolor(T.surface))
        p.drawEllipse(_P(x, y), 2.2, 2.2)
        p.setBrush(QtCore.Qt.NoBrush)


def _panel_right(p):
    p.drawRoundedRect(QtCore.QRectF(3.5, 4.5, 17, 15), 2, 2)
    _line(p, (14.5, 4.5), (14.5, 19.5))


def _file(p):
    _poly(p, [(6, 3.5), (14, 3.5), (18.5, 8), (18.5, 20.5), (6, 20.5)], closed=True)
    _poly(p, [(14, 3.5), (14, 8), (18.5, 8)])


def _wave(p):
    # test-sound glyph: speaker + small waves
    _volume(p)


def _help(p):
    p.drawEllipse(_P(12, 12), 8.5, 8.5)
    path = QtGui.QPainterPath(_P(9.3, 9.6))
    path.cubicTo(_P(9.3, 7.9), _P(10.6, 7), _P(12, 7))
    path.cubicTo(_P(13.6, 7), _P(14.8, 8), _P(14.8, 9.5))
    path.cubicTo(_P(14.8, 11.6), _P(12, 11.8), _P(12, 13.8))
    p.drawPath(path)
    p.setBrush(p.pen().color())
    p.drawEllipse(_P(12, 16.8), 0.9, 0.9)


def _info(p):
    p.drawEllipse(_P(12, 12), 8.5, 8.5)
    _line(p, (12, 11), (12, 16.5))
    p.setBrush(p.pen().color())
    p.drawEllipse(_P(12, 7.8), 0.9, 0.9)


def _link(p):
    _poly(p, [(14, 4), (20, 4), (20, 10)])
    _line(p, (20, 4), (11, 13))
    _poly(p, [(17.5, 13.5), (17.5, 19.5), (4.5, 19.5), (4.5, 6.5), (10.5, 6.5)])


def _copy(p):
    p.drawRoundedRect(QtCore.QRectF(8.5, 8.5, 11.5, 11.5), 2, 2)
    _poly(p, [(15.5, 8.5), (15.5, 4), (4, 4), (4, 15.5), (8.5, 15.5)])


def _save(p):
    _poly(p, [(4.5, 4.5), (16.5, 4.5), (19.5, 7.5), (19.5, 19.5), (4.5, 19.5)], closed=True)
    _poly(p, [(8, 4.5), (8, 9), (15, 9), (15, 4.5)])
    p.drawRoundedRect(QtCore.QRectF(8, 13, 8, 6.5), 1, 1)


def _folder(p):
    _poly(p, [(3.5, 6), (9.5, 6), (11.5, 8.5), (20.5, 8.5), (20.5, 19), (3.5, 19)], closed=True)


def _new(p):
    _file(p)
    _line(p, (12.2, 11), (12.2, 17))
    _line(p, (9.2, 14), (15.2, 14))


def _dot(p):
    p.setBrush(p.pen().color())
    p.drawEllipse(_P(12, 12), 4, 4)


def _wind(p):
    path = QtGui.QPainterPath(_P(3, 9))
    path.lineTo(_P(14, 9))
    path.cubicTo(_P(17, 9), _P(17.5, 5), _P(15, 4.5))
    p.drawPath(path)
    path = QtGui.QPainterPath(_P(3, 13.5))
    path.lineTo(_P(18, 13.5))
    path.cubicTo(_P(21.5, 13.5), _P(21.5, 18.5), _P(18.5, 19))
    p.drawPath(path)
    _line(p, (3, 17.5), (11, 17.5))


def _bolt(p):
    path = QtGui.QPainterPath(_P(13.5, 2.5))
    path.lineTo(_P(6, 13.5))
    path.lineTo(_P(11.5, 13.5))
    path.lineTo(_P(10, 21.5))
    path.lineTo(_P(18, 10))
    path.lineTo(_P(12.5, 10))
    path.closeSubpath()
    p.drawPath(path)


def _phone(p):
    p.drawRoundedRect(QtCore.QRectF(6.5, 2.5, 11, 19), 2.6, 2.6)
    _line(p, (10.5, 5.4), (13.5, 5.4))
    p.setBrush(p.pen().color())
    p.drawEllipse(_P(12, 18.3), 0.9, 0.9)


GLYPHS: Dict[str, Callable] = {
    "select": _select, "window": _window, "speaker": _speaker, "you": _you,
    "resize": _resize, "fit": _fit, "plus": _plus, "close": _close, "trash": _trash,
    "chevron_down": _chev("down"), "chevron_up": _chev("up"),
    "chevron_right": _chev("right"), "chevron_left": _chev("left"),
    "play": _play, "stop": _stop, "gear": _gear, "cloud_rain": _cloud_rain,
    "droplet": _droplet, "droplet_fill": _droplet_fill, "plan": _plan, "cube": _cube,
    "cube_gpu": _cube_gpu, "headphones": _headphones, "volume": _volume, "house": _house,
    "pencil": _pencil, "layers": _layers, "sliders": _sliders, "panel_right": _panel_right,
    "file": _file, "wave": _wave, "help": _help, "info": _info, "link": _link,
    "copy": _copy, "save": _save, "folder": _folder, "new": _new, "dot": _dot, "wind": _wind, "bolt": _bolt,
    "phone": _phone,
}


def pixmap(name: str, color: str, size: int = 20, dpr: float = 2.0, stroke: float = 1.75) -> QtGui.QPixmap:
    px = QtGui.QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(px)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    s = size / 24.0
    p.scale(s, s)
    pen = QtGui.QPen(qcolor(color), stroke, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    GLYPHS.get(name, _dot)(p)
    p.end()
    return px


@lru_cache(maxsize=512)
def icon(name: str, color: Optional[str] = None, size: int = 20,
         on_color: Optional[str] = None, stroke: float = 1.75) -> QtGui.QIcon:
    """QIcon: ``color`` normally, ``on_color`` when checked, theme-disabled when off."""
    base = color or T.text2
    ic = QtGui.QIcon()
    for dpr in (1.0, 2.0):
        ic.addPixmap(pixmap(name, base, size, dpr, stroke), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        ic.addPixmap(pixmap(name, on_color or base, size, dpr, stroke), QtGui.QIcon.Normal, QtGui.QIcon.On)
        ic.addPixmap(pixmap(name, color or T.text, size, dpr, stroke), QtGui.QIcon.Active, QtGui.QIcon.Off)
        ic.addPixmap(pixmap(name, on_color or color or T.text, size, dpr, stroke), QtGui.QIcon.Active, QtGui.QIcon.On)
        ic.addPixmap(pixmap(name, T.disabled, size, dpr, stroke), QtGui.QIcon.Disabled, QtGui.QIcon.Off)
        ic.addPixmap(pixmap(name, T.disabled, size, dpr, stroke), QtGui.QIcon.Disabled, QtGui.QIcon.On)
    return ic


__all__ = ["GLYPHS", "icon", "pixmap"]
