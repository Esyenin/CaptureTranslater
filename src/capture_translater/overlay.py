from __future__ import annotations

import logging
import sys
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .boxes import TranslationBox
from .constants import APP_NAME
from .font_manager import readable_font_family
from .geometry import clamp
from .models import OverlayStyle, ScreenRect
from .text_painter import draw_outlined_text


logger = logging.getLogger(__name__)


class OverlayWindow(QWidget):
    def __init__(self, screen: ScreenRect, style: OverlayStyle) -> None:
        super().__init__()
        self.screen = screen
        self.style = style
        self.boxes: list[TranslationBox] = []
        self.edit_mode = False
        self.drag_state: dict[str, Any] | None = None
        self.setWindowTitle(f"{APP_NAME} Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self.setGeometry(screen.x, screen.y, screen.width, screen.height)
        self.set_edit_mode(False)
        logger.info(
            "Overlay initialized for screen x=%s y=%s size=%sx%s",
            screen.x,
            screen.y,
            screen.width,
            screen.height,
        )

    def set_boxes(self, boxes: list[TranslationBox]) -> None:
        self.boxes = boxes
        logger.info("Overlay boxes updated: %s", len(boxes))
        self.update()

    def clear_boxes(self) -> None:
        self.set_boxes([])

    def set_style(self, style: OverlayStyle) -> None:
        self.style = style
        logger.debug(
            "Overlay style updated: font=%s size=%s outline=%s",
            style.font_family,
            style.font_size,
            style.text_outline_width,
        )
        self.update()

    def set_edit_mode(self, enabled: bool) -> None:
        self.edit_mode = enabled
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not enabled)
        self.apply_windows_click_through(not enabled)
        logger.info("Overlay edit mode set to %s", enabled)
        self.update()

    def show_overlay(self) -> None:
        self.setGeometry(
            self.screen.x,
            self.screen.y,
            self.screen.width,
            self.screen.height,
        )
        self.show()
        self.raise_()
        self.apply_windows_click_through(not self.edit_mode)
        logger.info("Overlay shown")

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing,
            True,
        )
        for box in self.boxes:
            if box.hidden:
                self.draw_marker(painter, box)
            else:
                self.draw_box(painter, box)
        if self.edit_mode:
            self.draw_edit_hint(painter)
        painter.end()

    def draw_box(self, painter: QPainter, box: TranslationBox) -> None:
        rect = self.box_rect(box)
        bg = QColor(self.style.bg_color)
        bg.setAlphaF(clamp(self.style.alpha, 0.0, 1.0))
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(self.style.border_color), 2))
        painter.drawRect(rect)

        padding = max(0, self.style.padding)
        outline_width = max(0, self.style.text_outline_width)
        text_rect = rect.adjusted(
            padding + outline_width,
            padding + outline_width,
            -padding - outline_width,
            -padding - outline_width,
        )
        draw_outlined_text(
            painter,
            text_rect,
            box.translated_text,
            self.fitted_font(box.translated_text, text_rect),
            self.style.text_color,
            self.style.text_outline_color,
            outline_width,
            Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter,
        )

    def draw_marker(self, painter: QPainter, box: TranslationBox) -> None:
        marker = self.marker_rect(box)
        marker_color = QColor(self.style.marker_color)
        marker_color.setAlphaF(0.92)
        painter.setBrush(marker_color)
        painter.setPen(QPen(QColor(self.style.border_color), 1))
        painter.drawRect(marker)
        painter.setPen(QColor(self.style.text_color))
        painter.setFont(
            QFont(readable_font_family(self.style.font_family), 10, QFont.Weight.Bold)
        )
        painter.drawText(marker, Qt.AlignmentFlag.AlignCenter, "T")

    def draw_edit_hint(self, painter: QPainter) -> None:
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(
            QRectF(16, 14, 560, 28),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Overlay edit: перетащи окно, двойной клик скрывает/возвращает.",
        )

    def fitted_font(self, text: str, rect: QRectF) -> QFont:
        start_size = max(6, self.style.font_size)
        family = readable_font_family(self.style.font_family)
        for size in range(start_size, 5, -1):
            font = QFont(family, size)
            metrics = QFontMetrics(font)
            bounds = metrics.boundingRect(
                0,
                0,
                max(1, round(rect.width())),
                10000,
                int(Qt.TextFlag.TextWordWrap),
                text,
            )
            if bounds.height() <= rect.height():
                return font
        return QFont(family, 6)

    def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if not self.edit_mode or event.button() != Qt.MouseButton.LeftButton:
            return
        box = self.box_at(event.position())
        if box is None:
            return
        box.hidden = not box.hidden
        logger.info("Overlay box %s hidden=%s", box.id, box.hidden)
        self.update()
        event.accept()

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if not self.edit_mode or event.button() != Qt.MouseButton.LeftButton:
            return
        box = self.box_at(event.position())
        if box is None or box.hidden:
            return
        rect = self.box_rect(box)
        self.drag_state = {
            "box": box,
            "offset": QPointF(
                event.position().x() - rect.x(),
                event.position().y() - rect.y(),
            ),
        }
        logger.debug("Started dragging overlay box %s", box.id)
        event.accept()

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if self.drag_state is None:
            return
        box = self.drag_state["box"]
        offset = self.drag_state["offset"]
        box.x = round(
            clamp(
                self.screen.x + event.position().x() - offset.x(),
                self.screen.x,
                self.screen.x + self.screen.width - box.width,
            )
        )
        box.y = round(
            clamp(
                self.screen.y + event.position().y() - offset.y(),
                self.screen.y,
                self.screen.y + self.screen.height - box.height,
            )
        )
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            if self.drag_state is not None:
                logger.debug(
                    "Finished dragging overlay box %s",
                    self.drag_state["box"].id,
                )
            self.drag_state = None

    def box_at(self, point: QPointF) -> TranslationBox | None:
        for box in reversed(self.boxes):
            rect = self.marker_rect(box) if box.hidden else self.box_rect(box)
            if rect.contains(point):
                return box
        return None

    def box_rect(self, box: TranslationBox) -> QRectF:
        return QRectF(
            box.x - self.screen.x,
            box.y - self.screen.y,
            max(1, box.width),
            max(1, box.height),
        )

    def marker_rect(self, box: TranslationBox) -> QRectF:
        return QRectF(box.x - self.screen.x, box.y - self.screen.y, 28, 24)

    def apply_windows_click_through(self, enabled: bool) -> None:
        if sys.platform != "win32" or not self.winId():
            return
        import ctypes

        # Qt handles transparent input on most platforms; Win32 needs the
        # extended transparent style so clicks reach the manga/browser below.
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        gwl_exstyle = -20
        ws_ex_layered = 0x00080000
        ws_ex_transparent = 0x00000020
        style = user32.GetWindowLongW(hwnd, gwl_exstyle)
        style |= ws_ex_layered
        if enabled:
            style |= ws_ex_transparent
        else:
            style &= ~ws_ex_transparent
        user32.SetWindowLongW(hwnd, gwl_exstyle, style)
        logger.debug("Windows click-through set to %s", enabled)
