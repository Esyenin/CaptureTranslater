from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter


def draw_outlined_text(
    painter: QPainter,
    rect: QRectF,
    text: str,
    font: QFont,
    text_color: str,
    outline_color: str,
    outline_width: int,
    flags: Qt.TextFlag | Qt.AlignmentFlag,
) -> None:
    """Draw wrapped text with a cheap but readable stroke around glyphs."""
    painter.setFont(font)
    width = max(0, int(outline_width))
    if width > 0:
        painter.setPen(QColor(outline_color))
        for dx, dy in outline_offsets(width):
            painter.drawText(rect.translated(dx, dy), flags, text)

    painter.setPen(QColor(text_color))
    painter.drawText(rect, flags, text)


def outline_offsets(width: int) -> list[tuple[int, int]]:
    offsets: list[tuple[int, int]] = []
    for dx in range(-width, width + 1):
        for dy in range(-width, width + 1):
            if dx == 0 and dy == 0:
                continue
            if dx * dx + dy * dy <= width * width:
                offsets.append((dx, dy))
    return offsets

