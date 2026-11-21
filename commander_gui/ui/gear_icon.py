"""A settings cog drawn with QPainterPath.

The old button used the U+2699 glyph, which each OS draws from a different
fallback font (on Windows an uneven Segoe UI Symbol / emoji cog that ignores
the theme colour). Painting it ourselves, like ``deck_icon``, looks the same
everywhere and takes the colour the theme asks for. No QtSvg is needed.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap, QTransform

_UNITS = 24.0
_TEETH = 8


def gear_path(size: float = _UNITS) -> QPainterPath:
    """A solid cog ``size`` pixels wide with its centre hole cut out."""
    s = size / _UNITS
    c = _UNITS / 2 * s
    body = QPainterPath()
    body.addEllipse(QPointF(c, c), 7.6 * s, 7.6 * s)
    for i in range(_TEETH):
        tooth = QPainterPath()
        tooth.addRoundedRect(QRectF(c - 1.9 * s, c - 10.8 * s, 3.8 * s, 5.0 * s), 0.7 * s, 0.7 * s)
        tf = QTransform()
        tf.translate(c, c)
        tf.rotate(i * 360.0 / _TEETH)
        tf.translate(-c, -c)
        body = body.united(tf.map(tooth))
    hole = QPainterPath()
    hole.addEllipse(QPointF(c, c), 3.5 * s, 3.5 * s)
    return body.subtracted(hole)


def _render(color: QColor, size: int, ratio: float) -> QPixmap:
    pixels = max(1, round(size * ratio))
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillPath(gear_path(float(pixels)), color)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def gear_icon(normal: QColor, highlight: QColor, size: int = 20) -> QIcon:
    """Cog in ``normal``, switching to ``highlight`` on hover (Active mode)."""
    icon = QIcon()
    for ratio in (1.0, 2.0):
        icon.addPixmap(_render(normal, size, ratio), QIcon.Mode.Normal)
        icon.addPixmap(_render(highlight, size, ratio), QIcon.Mode.Active)
    return icon
