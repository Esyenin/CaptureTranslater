from __future__ import annotations

import copy
import logging
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget

    OPENGL_WIDGET_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on installed PySide6 wheels
    from PySide6.QtWidgets import QWidget as QOpenGLWidget

    OPENGL_WIDGET_AVAILABLE = False

from PySide6.QtWidgets import QSizePolicy

from .constants import (
    EDGE_HIT_RADIUS,
    HANDLE_RADIUS,
    MAX_PREVIEW_ZOOM,
    MIN_AREA_SIZE,
    PREVIEW_BACKGROUND,
)
from .geometry import clamp, clamp_area_to_screen
from .models import AppSettings, OverlayStyle, ScreenRect, TranslationArea
from .text_painter import draw_outlined_text


logger = logging.getLogger(__name__)


class PreviewWidget(QOpenGLWidget):
    area_changed = Signal(object)

    def __init__(self, screen: ScreenRect, settings: AppSettings) -> None:
        super().__init__()
        self.screen = screen
        self.settings = settings
        self.frame = QImage()
        self.preview_zoom = 1.0
        self.preview_scale = 1.0
        self.preview_offset = QPointF(0, 0)
        self.view_initialized = False
        self.drag_state: dict[str, Any] | None = None
        self.pan_state: dict[str, Any] | None = None
        self.setMinimumSize(QSize(640, 420))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        logger.info("Preview widget initialized")

    def set_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        self.update()

    def set_frame(self, frame: QImage) -> None:
        self.frame = frame
        self.ensure_view()
        self.update()

    def initializeGL(self) -> None:  # noqa: N802 - Qt API
        pass

    def paintGL(self) -> None:  # noqa: N802 - Qt API
        self.paint_scene()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - QWidget fallback
        if OPENGL_WIDGET_AVAILABLE:
            super().paintEvent(event)
        else:
            self.paint_scene()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if self.preview_zoom <= 1.0:
            self.view_initialized = False
        self.ensure_view()

    def paint_scene(self) -> None:
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform,
            True,
        )
        painter.fillRect(self.rect(), QColor(PREVIEW_BACKGROUND))

        if self.frame.isNull():
            painter.setPen(QColor("#f4f4f4"))
            painter.setFont(QFont("Segoe UI", 14))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Захватываю экран...")
            painter.end()
            return

        self.ensure_view()
        target = QRectF(
            self.preview_offset,
            QSize(
                round(self.frame.width() * self.preview_scale),
                round(self.frame.height() * self.preview_scale),
            ),
        )
        painter.drawImage(target, self.frame)
        self.draw_area_frame(painter)
        self.draw_style_sample(painter)
        painter.end()

    def ensure_view(self) -> None:
        if self.frame.isNull() or self.width() <= 0 or self.height() <= 0:
            return
        fit_scale = min(
            self.width() / self.frame.width(),
            self.height() / self.frame.height(),
        )
        self.preview_scale = fit_scale * self.preview_zoom
        display_width = self.frame.width() * self.preview_scale
        display_height = self.frame.height() * self.preview_scale
        if not self.view_initialized or self.preview_zoom <= 1.0:
            self.preview_offset = QPointF(
                (self.width() - display_width) / 2,
                (self.height() - display_height) / 2,
            )
            self.view_initialized = True
        self.preview_offset = self.clamped_offset(
            self.preview_offset,
            display_width,
            display_height,
        )

    def clamped_offset(
        self,
        offset: QPointF,
        display_width: float,
        display_height: float,
    ) -> QPointF:
        horizontal_margin = self.width() / 2
        vertical_margin = self.height() / 2

        if display_width <= self.width():
            min_x = -display_width + horizontal_margin
            max_x = self.width() - horizontal_margin
        else:
            min_x = self.width() - display_width - horizontal_margin
            max_x = horizontal_margin

        if display_height <= self.height():
            min_y = -display_height + vertical_margin
            max_y = self.height() - vertical_margin
        else:
            min_y = self.height() - display_height - vertical_margin
            max_y = vertical_margin

        return QPointF(clamp(offset.x(), min_x, max_x), clamp(offset.y(), min_y, max_y))

    def wheelEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if self.frame.isNull():
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        old_zoom = self.preview_zoom
        new_zoom = clamp(old_zoom * factor, 1.0, MAX_PREVIEW_ZOOM)
        if abs(new_zoom - old_zoom) < 0.001:
            return

        cursor = event.position()
        screen_x, screen_y = self.preview_to_screen(cursor.x(), cursor.y())
        fit_scale = min(
            self.width() / self.frame.width(),
            self.height() / self.frame.height(),
        )
        new_scale = fit_scale * new_zoom
        display_width = self.frame.width() * new_scale
        display_height = self.frame.height() * new_scale

        if new_zoom <= 1.0:
            new_offset = QPointF(
                (self.width() - display_width) / 2,
                (self.height() - display_height) / 2,
            )
        else:
            new_offset = QPointF(
                cursor.x() - (screen_x - self.screen.x) * new_scale,
                cursor.y() - (screen_y - self.screen.y) * new_scale,
            )

        self.preview_zoom = new_zoom
        self.preview_scale = new_scale
        self.preview_offset = self.clamped_offset(
            new_offset,
            display_width,
            display_height,
        )
        logger.debug("Preview zoom changed from %.2f to %.2f", old_zoom, new_zoom)
        self.update()

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.MiddleButton:
            self.pan_state = {
                "start": event.position(),
                "offset": QPointF(self.preview_offset),
            }
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            event.accept()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return
        rect = self.area_to_preview(self.settings.area)
        mode = self.hit_test_area(event.position(), rect)
        if mode is None:
            return
        self.drag_state = {
            "mode": mode,
            "start_screen": self.preview_to_screen(event.position().x(), event.position().y()),
            "area": copy.deepcopy(self.settings.area),
        }
        event.accept()

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if self.pan_state is not None:
            delta = event.position() - self.pan_state["start"]
            display_width = self.frame.width() * self.preview_scale
            display_height = self.frame.height() * self.preview_scale
            self.preview_offset = self.clamped_offset(
                self.pan_state["offset"] + delta,
                display_width,
                display_height,
            )
            self.update()
            event.accept()
            return

        if self.drag_state is None:
            return
        current_x, current_y = self.preview_to_screen(
            event.position().x(),
            event.position().y(),
        )
        start_x, start_y = self.drag_state["start_screen"]
        dx = current_x - start_x
        dy = current_y - start_y
        original = self.drag_state["area"]
        mode = self.drag_state["mode"]

        left = original.x
        top = original.y
        right = original.x + original.width
        bottom = original.y + original.height

        if mode == "move":
            left += dx
            right += dx
            top += dy
            bottom += dy
        else:
            if "w" in mode:
                left += dx
            if "e" in mode:
                right += dx
            if "n" in mode:
                top += dy
            if "s" in mode:
                bottom += dy

        if right - left < MIN_AREA_SIZE:
            if "w" in mode:
                left = right - MIN_AREA_SIZE
            else:
                right = left + MIN_AREA_SIZE
        if bottom - top < MIN_AREA_SIZE:
            if "n" in mode:
                top = bottom - MIN_AREA_SIZE
            else:
                bottom = top + MIN_AREA_SIZE

        self.area_changed.emit(
            clamp_area_to_screen(
                TranslationArea(
                    x=round(left),
                    y=round(top),
                    width=round(right - left),
                    height=round(bottom - top),
                ),
                self.screen,
            )
        )
        event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.MiddleButton:
            self.pan_state = None
            self.unsetCursor()
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_state = None

    def hit_test_area(self, point: QPointF, rect: QRectF) -> str | None:
        for name, handle in self.area_handles(rect).items():
            horizontal_hit = abs(point.x() - handle.x()) <= HANDLE_RADIUS + 4
            vertical_hit = abs(point.y() - handle.y()) <= HANDLE_RADIUS + 4
            if horizontal_hit and vertical_hit:
                return name
        if rect.left() - EDGE_HIT_RADIUS <= point.x() <= rect.right() + EDGE_HIT_RADIUS:
            if abs(point.y() - rect.top()) <= EDGE_HIT_RADIUS:
                return "n"
            if abs(point.y() - rect.bottom()) <= EDGE_HIT_RADIUS:
                return "s"
        if rect.top() - EDGE_HIT_RADIUS <= point.y() <= rect.bottom() + EDGE_HIT_RADIUS:
            if abs(point.x() - rect.left()) <= EDGE_HIT_RADIUS:
                return "w"
            if abs(point.x() - rect.right()) <= EDGE_HIT_RADIUS:
                return "e"
        if rect.contains(point):
            return "move"
        return None

    def draw_area_frame(self, painter: QPainter) -> None:
        rect = self.area_to_preview(self.settings.area)
        painter.setPen(QPen(QColor("#ff2b2b"), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawRect(rect.adjusted(1, 1, -1, -1))
        painter.setBrush(QColor("#ff2b2b"))
        painter.setPen(QPen(QColor("#ffffff"), 1))
        for handle in self.area_handles(rect).values():
            painter.drawRect(
                QRectF(
                    handle.x() - HANDLE_RADIUS,
                    handle.y() - HANDLE_RADIUS,
                    HANDLE_RADIUS * 2,
                    HANDLE_RADIUS * 2,
                )
            )
        painter.setPen(QColor("#ff2b2b"))
        painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        painter.drawText(rect.left() + 8, rect.top() + 22, "Зона видимости")

    def draw_style_sample(self, painter: QPainter) -> None:
        area = self.settings.area
        style = self.settings.style
        if area.width < 48 or area.height < 48:
            return

        sample_text = "Пример окна перевода"
        sample_screen_width = min(360, max(160, area.width - 32))
        available_sample_height = max(36, area.height - 32)
        sample_screen_height = min(
            max(72, style.padding * 2 + int(style.font_size * 3.2)),
            available_sample_height,
        )
        sample_screen_x = area.x + min(16, max(0, area.width - sample_screen_width))
        sample_screen_y = area.y + max(16, area.height - sample_screen_height - 16)
        sample_x = self.screen_x_to_preview(sample_screen_x)
        sample_y = self.screen_y_to_preview(sample_screen_y)
        sample_width = sample_screen_width * self.preview_scale
        sample_height = sample_screen_height * self.preview_scale
        padding = max(1, style.padding * self.preview_scale)
        outline_width = max(0, round(style.text_outline_width * self.preview_scale))
        font_size = self.fit_sample_font_size(
            sample_text,
            style,
            max(8, sample_width - padding * 2 - outline_width * 2),
            max(8, sample_height - padding * 2 - outline_width * 2),
        )

        bg = QColor(style.bg_color)
        bg.setAlphaF(clamp(style.alpha, 0.0, 1.0))
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(style.border_color), 2))
        painter.drawRect(QRectF(sample_x, sample_y, sample_width, sample_height))

        draw_outlined_text(
            painter,
            QRectF(
                sample_x + padding + outline_width,
                sample_y + padding + outline_width,
                max(8, sample_width - padding * 2 - outline_width * 2),
                max(8, sample_height - padding * 2 - outline_width * 2),
            ),
            sample_text,
            QFont(style.font_family, font_size),
            style.text_color,
            style.text_outline_color,
            outline_width,
            Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter,
        )

    def fit_sample_font_size(
        self,
        text: str,
        style: OverlayStyle,
        max_width: float,
        max_height: float,
    ) -> int:
        target_size = max(1, round(style.font_size * self.preview_scale))
        for size in range(target_size, 0, -1):
            font = QFont(style.font_family, size)
            metrics = QFontMetrics(font)
            rect = metrics.boundingRect(
                0,
                0,
                round(max_width),
                10000,
                int(Qt.TextFlag.TextWordWrap),
                text,
            )
            if rect.width() <= max_width and rect.height() <= max_height:
                return size
        return 1

    def area_to_preview(self, area: TranslationArea) -> QRectF:
        x0 = self.screen_x_to_preview(area.x)
        y0 = self.screen_y_to_preview(area.y)
        x1 = self.screen_x_to_preview(area.x + area.width)
        y1 = self.screen_y_to_preview(area.y + area.height)
        return QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized()

    def area_handles(self, rect: QRectF) -> dict[str, QPointF]:
        return {
            "nw": rect.topLeft(),
            "n": QPointF(rect.center().x(), rect.top()),
            "ne": rect.topRight(),
            "e": QPointF(rect.right(), rect.center().y()),
            "se": rect.bottomRight(),
            "s": QPointF(rect.center().x(), rect.bottom()),
            "sw": rect.bottomLeft(),
            "w": QPointF(rect.left(), rect.center().y()),
        }

    def preview_to_screen(self, x: float, y: float) -> tuple[int, int]:
        return (
            round((x - self.preview_offset.x()) / self.preview_scale + self.screen.x),
            round((y - self.preview_offset.y()) / self.preview_scale + self.screen.y),
        )

    def screen_x_to_preview(self, screen_x: int | float) -> float:
        return self.preview_offset.x() + (screen_x - self.screen.x) * self.preview_scale

    def screen_y_to_preview(self, screen_y: int | float) -> float:
        return self.preview_offset.y() + (screen_y - self.screen.y) * self.preview_scale
