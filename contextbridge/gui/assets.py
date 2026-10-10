"""Illustrations, icons and the app icon.

Every picture is looked up as ``contextbridge/gui/assets/<name>.svg`` first. If that file
exists it is used as-is, so a designer can replace any placeholder just by dropping in an
SVG with the right name (see docs/GUI_ASSETS.md). Without a file, a simple placeholder is
drawn here from the palette, so the app always looks finished.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

from contextbridge.gui import theme

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
CANVAS = 240.0  # placeholder drawings use a 240x240 coordinate space

# Names a designer can replace by dropping <name>.svg into the assets folder.
ILLUSTRATIONS = ("welcome", "notes", "changes", "done")
NAV_ICONS = ("home", "notes", "changes", "handoff", "settings")


def asset_path(name: str) -> Path | None:
    path = ASSETS_DIR / f"{name}.svg"
    return path if path.is_file() else None


def _new_pixmap(width: int, height: int, dpr: float) -> QPixmap:
    pm = QPixmap(int(width * dpr), int(height * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    return pm


def _painter(pm: QPixmap) -> QPainter:
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    return p


def _pen(color: str, width: float) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _render_svg(path: Path, size: int, dpr: float) -> QPixmap | None:
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        return None
    default = renderer.defaultSize()
    scale = min(size / max(default.width(), 1), size / max(default.height(), 1))
    w, h = default.width() * scale, default.height() * scale
    pm = _new_pixmap(size, size, dpr)
    p = _painter(pm)
    renderer.render(p, QRectF((size - w) / 2, (size - h) / 2, w, h))
    p.end()
    return pm


# ----- placeholder illustrations (240 x 240) ------------------------------------------------


def _backdrop(p: QPainter) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.mix(theme.CARDAMOM, theme.WHITE, 0.35)))
    p.drawEllipse(QRectF(20, 20, 200, 200))


def _draw_welcome(p: QPainter) -> None:
    """A little arch bridge over water: the product's idea in one shape."""
    _backdrop(p)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_pen(theme.MASALA, 6))
    arch = QPainterPath()
    arch.moveTo(52, 132)
    arch.cubicTo(80, 70, 160, 70, 188, 132)
    p.drawPath(arch)
    p.drawLine(QPointF(40, 132), QPointF(200, 132))
    p.setPen(_pen(theme.CINNAMON_TOAST, 5))
    for x in (78, 120, 162):
        p.drawLine(QPointF(x, 132), QPointF(x, 100 + abs(x - 120) * 0.3))
    p.setPen(_pen(theme.CINNAMON_TOAST, 4))
    for y, off in ((152, 0), (166, 12)):
        wave = QPainterPath()
        wave.moveTo(50 + off, y)
        wave.cubicTo(70 + off, y - 8, 90 + off, y + 8, 110 + off, y)
        wave.cubicTo(130 + off, y - 8, 150 + off, y + 8, 170 + off, y)
        p.drawPath(wave)


def _draw_notes(p: QPainter) -> None:
    _backdrop(p)
    p.setPen(_pen(theme.MASALA, 6))
    p.setBrush(QColor(theme.SURFACE))
    p.drawRoundedRect(QRectF(78, 62, 84, 116), 12, 12)
    p.setPen(_pen(theme.CINNAMON_TOAST, 5))
    for i, w in enumerate((48, 40, 28)):
        y = 94 + i * 22
        p.drawLine(QPointF(94, y), QPointF(94 + w, y))
    p.setPen(_pen(theme.MASALA, 6))
    p.drawLine(QPointF(150, 160), QPointF(184, 120))  # pencil
    p.drawLine(QPointF(184, 120), QPointF(192, 128))
    p.drawLine(QPointF(150, 160), QPointF(146, 170))


def _draw_changes(p: QPainter) -> None:
    _backdrop(p)
    p.setPen(_pen(theme.CINNAMON_TOAST, 6))
    p.setBrush(QColor(theme.SURFACE))
    p.drawRoundedRect(QRectF(66, 74, 70, 92), 10, 10)
    p.setPen(_pen(theme.MASALA, 6))
    p.drawRoundedRect(QRectF(100, 58, 70, 92), 10, 10)
    p.setPen(_pen(theme.CINNAMON_TOAST, 5))
    p.drawLine(QPointF(114, 88), QPointF(154, 88))
    p.drawLine(QPointF(114, 106), QPointF(146, 106))
    leaf = QPainterPath()
    leaf.moveTo(150, 150)
    leaf.cubicTo(150, 128, 176, 126, 182, 134)
    leaf.cubicTo(184, 150, 168, 160, 150, 150)
    p.setPen(_pen(theme.MASALA, 5))
    p.setBrush(QColor(theme.CARDAMOM))
    p.drawPath(leaf)


def _draw_done(p: QPainter) -> None:
    """A cup of chai with steam: the 'all done, take a breath' picture."""
    _backdrop(p)
    p.setPen(_pen(theme.MASALA, 6))
    p.setBrush(QColor(theme.SURFACE))
    cup = QPainterPath()
    cup.moveTo(80, 112)
    cup.lineTo(160, 112)
    cup.lineTo(154, 160)
    cup.cubicTo(152, 172, 142, 178, 130, 178)
    cup.lineTo(110, 178)
    cup.cubicTo(98, 178, 88, 172, 86, 160)
    cup.closeSubpath()
    p.drawPath(cup)
    handle = QPainterPath()
    handle.moveTo(160, 124)
    handle.cubicTo(188, 120, 188, 160, 156, 156)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(handle)
    p.setPen(_pen(theme.CINNAMON_TOAST, 5))
    for x in (100, 120, 140):
        steam = QPainterPath()
        steam.moveTo(x, 98)
        steam.cubicTo(x - 8, 86, x + 8, 76, x, 62)
        p.drawPath(steam)


_ILLUSTRATORS: dict[str, Callable[[QPainter], None]] = {
    "welcome": _draw_welcome,
    "notes": _draw_notes,
    "changes": _draw_changes,
    "done": _draw_done,
}


def illustration(name: str, size: int = 160, dpr: float = 2.0) -> QPixmap:
    """The picture called ``name`` (a dropped-in SVG wins over the placeholder)."""
    svg = asset_path(name)
    if svg is not None:
        pm = _render_svg(svg, size, dpr)
        if pm is not None:
            return pm
    pm = _new_pixmap(size, size, dpr)
    p = _painter(pm)
    p.scale(size / CANVAS, size / CANVAS)
    _ILLUSTRATORS.get(name, _draw_welcome)(p)
    p.end()
    return pm


# ----- sidebar icons (24 x 24, single colour) -----------------------------------------------


def _icon_home(p: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(3.5, 11.5)
    path.lineTo(12, 4)
    path.lineTo(20.5, 11.5)
    path.moveTo(6, 10)
    path.lineTo(6, 19.5)
    path.lineTo(18, 19.5)
    path.lineTo(18, 10)
    path.moveTo(10, 19.5)
    path.lineTo(10, 14)
    path.lineTo(14, 14)
    path.lineTo(14, 19.5)
    p.drawPath(path)


def _icon_notes(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(5, 3.5, 14, 17), 2.5, 2.5)
    for y in (9, 13, 17):
        p.drawLine(QPointF(8.5, y), QPointF(15.5, y))


def _icon_changes(p: QPainter) -> None:
    p.drawEllipse(QPointF(7, 6), 2.2, 2.2)
    p.drawEllipse(QPointF(7, 18), 2.2, 2.2)
    p.drawEllipse(QPointF(17, 10), 2.2, 2.2)
    p.drawLine(QPointF(7, 8.2), QPointF(7, 15.8))
    path = QPainterPath()
    path.moveTo(17, 12.2)
    path.cubicTo(17, 15.5, 7, 13, 7, 15.8)
    p.drawPath(path)


def _icon_handoff(p: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(21, 3.5)
    path.lineTo(3.5, 10.5)
    path.lineTo(10.5, 13.5)
    path.lineTo(13.5, 20.5)
    path.lineTo(21, 3.5)
    path.moveTo(10.5, 13.5)
    path.lineTo(21, 3.5)
    p.drawPath(path)


def _icon_settings(p: QPainter) -> None:
    for y, x in ((7, 9), (12, 15), (17, 8)):
        p.drawLine(QPointF(4, y), QPointF(20, y))
        p.setBrush(QColor(p.pen().color()))
        p.drawEllipse(QPointF(x, y), 2.2, 2.2)
        p.setBrush(Qt.BrushStyle.NoBrush)


_ICON_DRAWERS: dict[str, Callable[[QPainter], None]] = {
    "home": _icon_home,
    "notes": _icon_notes,
    "changes": _icon_changes,
    "handoff": _icon_handoff,
    "settings": _icon_settings,
}


def nav_icon(name: str, color: str = theme.ON_DARK, size: int = 22, dpr: float = 2.0) -> QIcon:
    svg = asset_path(f"icon_{name}")
    pm: QPixmap | None = _render_svg(svg, size, dpr) if svg else None
    if pm is None:
        pm = _new_pixmap(size, size, dpr)
        p = _painter(pm)
        p.scale(size / 24.0, size / 24.0)
        p.setPen(_pen(color, 1.7))
        p.setBrush(Qt.BrushStyle.NoBrush)
        _ICON_DRAWERS.get(name, _icon_home)(p)
        p.end()
    return QIcon(pm)


# ----- the app icon -----------------------------------------------------------------------------


def app_icon_sized(size: int) -> QPixmap:
    """The app icon at ``size`` pixels: the designer's SVG if present, else the placeholder."""
    svg = asset_path("app_icon")
    pm = _render_svg(svg, size, 1.0) if svg else None
    return pm if pm is not None else app_icon_pixmap(size)


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icon.addPixmap(app_icon_sized(size))
    return icon


def app_icon_pixmap(size: int = 512) -> QPixmap:
    """The placeholder app icon: a cream arch bridge on a Red Chai rounded square."""
    pm = _new_pixmap(size, size, 1.0)
    p = _painter(pm)
    p.scale(size / CANVAS, size / CANVAS)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.RED_CHAI))
    p.drawRoundedRect(QRectF(8, 8, 224, 224), 52, 52)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_pen(theme.ON_DARK, 11))
    arch = QPainterPath()
    arch.moveTo(48, 138)
    arch.cubicTo(76, 62, 164, 62, 192, 138)
    p.drawPath(arch)
    p.drawLine(QPointF(38, 138), QPointF(202, 138))
    p.setPen(_pen(theme.CINNAMON_TOAST, 8))
    for x, top in ((84, 100), (120, 88), (156, 100)):  # hangers between the arch and the deck
        p.drawLine(QPointF(x, 138), QPointF(x, top))
    p.setPen(_pen(theme.CARDAMOM, 9))
    for y, off in ((170, 0), (192, 18)):  # water
        wave = QPainterPath()
        wave.moveTo(56 + off, y)
        wave.cubicTo(76 + off, y - 10, 96 + off, y + 10, 116 + off, y)
        wave.cubicTo(136 + off, y - 10, 156 + off, y + 10, 176 + off - 18, y)
        p.drawPath(wave)
    p.end()
    return pm


# ----- tick and dot images for checkboxes and radio buttons -------------------------------------


def indicator_images() -> dict[str, str]:
    """Draw the tick and dot used inside checkboxes and radio buttons; return their paths.

    Qt stylesheets can only show images from files, so they are written once per run to a
    temporary folder (with ``@2x`` copies for sharp rendering on high-resolution screens).
    """
    folder = Path(tempfile.gettempdir()) / "contextbridge-ui"
    folder.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for name, draw in (("check", _draw_tick), ("radio", _draw_dot), ("chevron", _draw_chevron)):
        for scale, suffix in ((1, ""), (2, "@2x")):
            size = 18
            pm = _new_pixmap(size * scale, size * scale, 1.0)
            p = _painter(pm)
            p.scale(scale, scale)
            draw(p, size)
            p.end()
            target = folder / f"{name}{suffix}.png"
            pm.save(str(target), "PNG")
            if not suffix:
                paths[name] = target.as_posix()
    return paths


def _draw_tick(p: QPainter, size: int) -> None:
    p.setPen(_pen(theme.WHITE, 2.4))
    path = QPainterPath()
    path.moveTo(size * 0.26, size * 0.52)
    path.lineTo(size * 0.43, size * 0.68)
    path.lineTo(size * 0.74, size * 0.34)
    p.drawPath(path)


def _draw_dot(p: QPainter, size: int) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.PRIMARY))
    p.drawEllipse(QPointF(size / 2, size / 2), size * 0.26, size * 0.26)


def _draw_chevron(p: QPainter, size: int) -> None:
    p.setPen(_pen(theme.MASALA, 2.2))
    path = QPainterPath()
    path.moveTo(size * 0.28, size * 0.40)
    path.lineTo(size * 0.50, size * 0.62)
    path.lineTo(size * 0.72, size * 0.40)
    p.drawPath(path)
