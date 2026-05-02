from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import mss

from .capture import CaptureThread, grab_screen_qimage
from .constants import (
    APP_NAME,
    DEFAULT_PREVIEW_FPS,
    FPS_CHOICES,
    MAX_PREVIEW_FPS,
    MIN_AREA_SIZE,
    PREVIEW_BACKGROUND,
    SETTINGS_PATH,
)
from .font_manager import FontRegistry, unique_font_paths
from .geometry import clamp_area_to_screen
from .logging_config import get_current_log_path
from .models import AppSettings, OcrSettings, OverlayStyle, ScreenRect, TranslationArea
from .ocr_presets import OCR_PRESETS, get_ocr_preset
from .overlay import OverlayWindow
from .platform import get_virtual_screen_rect
from .preview import OPENGL_WIDGET_AVAILABLE, PreviewWidget
from .pipeline import OcrPipeline
from .settings_store import load_settings, save_settings


logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 760)
        self.screen = get_virtual_screen_rect()
        self.font_registry = FontRegistry()
        self.saved_settings = load_settings(self.screen)
        self.font_registry.load_paths(self.saved_settings.style.custom_font_paths)
        self.draft_settings = copy.deepcopy(self.saved_settings)
        self.syncing = False
        self.dirty = False
        self.capture_thread: CaptureThread | None = None
        self.overlay_window = OverlayWindow(self.screen, self.saved_settings.style)
        self.ocr_pipeline = OcrPipeline(self.screen, self.saved_settings.ocr.preset_id)

        self.preview = PreviewWidget(self.screen, self.draft_settings)
        self.preview.area_changed.connect(self.on_area_changed_from_preview)
        self.status_label = QLabel()
        self.live_checkbox = QCheckBox("Live")
        self.live_checkbox.setChecked(True)
        self.overlay_checkbox = QCheckBox("Показать overlay")
        self.overlay_edit_checkbox = QCheckBox("Редактировать окна overlay")
        self.scan_button = QPushButton("Сканировать область")
        self.clear_overlay_button = QPushButton("Очистить overlay")
        self.ocr_preset_combo = QComboBox()
        self.ocr_preset_description = QLabel()
        self.fps_combo = QComboBox()
        self.area_x = QSpinBox()
        self.area_y = QSpinBox()
        self.area_width = QSpinBox()
        self.area_height = QSpinBox()
        self.bg_button = QPushButton()
        self.text_button = QPushButton()
        self.text_outline_button = QPushButton()
        self.border_button = QPushButton()
        self.marker_button = QPushButton()
        self.alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_combo = QFontComboBox()
        self.add_font_button = QPushButton("Добавить...")
        self.custom_fonts_label = QLabel()
        self.font_size = QSpinBox()
        self.text_outline_width = QSpinBox()
        self.padding = QSpinBox()

        self.build_ui()
        self.sync_panel_from_draft()
        self.start_capture()
        self.set_status(self.initial_status())
        logger.info("Main window initialized")

    def build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(12, 12, 12, 12)

        header = QHBoxLayout()
        title = QLabel("Preview экрана")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch(1)
        self.live_checkbox.toggled.connect(self.on_live_toggled)
        header.addWidget(self.live_checkbox)
        header.addWidget(QLabel("FPS"))
        self.fps_combo.addItems(list(FPS_CHOICES))
        self.fps_combo.setCurrentText(str(DEFAULT_PREVIEW_FPS))
        self.fps_combo.currentTextChanged.connect(self.on_fps_changed)
        header.addWidget(self.fps_combo)
        refresh_button = QPushButton("Обновить кадр")
        refresh_button.clicked.connect(self.refresh_once)
        header.addWidget(refresh_button)
        preview_layout.addLayout(header)
        preview_layout.addWidget(self.preview, 1)
        hint = QLabel("Красная рамка - область OCR. Колесо: zoom, зажатое колесо: перемещение.")
        hint.setStyleSheet("color: #555;")
        preview_layout.addWidget(hint)

        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setMinimumWidth(360)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 12, 12, 12)
        settings_title = QLabel("Настройки")
        settings_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        side_layout.addWidget(settings_title)
        screen_label = QLabel(
            f"Экран: X={self.screen.x}, Y={self.screen.y}, "
            f"{self.screen.width}x{self.screen.height}"
        )
        side_layout.addWidget(screen_label)
        side_layout.addWidget(self.build_area_group())
        side_layout.addWidget(self.build_style_group())
        side_layout.addWidget(self.build_overlay_group())

        save_button = QPushButton("Сохранить")
        save_button.clicked.connect(self.save_settings)
        side_layout.addWidget(save_button)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        side_layout.addWidget(line)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #333;")
        side_layout.addWidget(self.status_label)
        side_layout.addStretch(1)

        splitter.addWidget(preview_panel)
        side_scroll.setWidget(side)

        splitter.addWidget(side_scroll)
        splitter.setStretchFactor(0, 1)

    def build_area_group(self) -> QGroupBox:
        area_box = QGroupBox("Область видимости перевода")
        area_form = QFormLayout(area_box)
        for spin in (self.area_x, self.area_y):
            spin.setRange(-10000, 30000)
            spin.valueChanged.connect(self.update_draft_from_panel)
        for spin in (self.area_width, self.area_height):
            spin.setRange(MIN_AREA_SIZE, 30000)
            spin.valueChanged.connect(self.update_draft_from_panel)
        area_form.addRow("X", self.area_x)
        area_form.addRow("Y", self.area_y)
        area_form.addRow("Ширина", self.area_width)
        area_form.addRow("Высота", self.area_height)

        area_buttons = QHBoxLayout()
        full_button = QPushButton("На весь экран")
        full_button.clicked.connect(self.set_area_fullscreen)
        center_button = QPushButton("В центр")
        center_button.clicked.connect(self.set_area_centered)
        area_buttons.addWidget(full_button)
        area_buttons.addWidget(center_button)
        area_form.addRow(area_buttons)
        return area_box

    def build_style_group(self) -> QGroupBox:
        style_box = QGroupBox("Параметры окон перевода")
        style_form = QFormLayout(style_box)
        self.bg_button.clicked.connect(lambda: self.choose_color("bg_color"))
        self.text_button.clicked.connect(lambda: self.choose_color("text_color"))
        self.text_outline_button.clicked.connect(
            lambda: self.choose_color("text_outline_color")
        )
        self.border_button.clicked.connect(lambda: self.choose_color("border_color"))
        self.marker_button.clicked.connect(lambda: self.choose_color("marker_color"))
        style_form.addRow("Фон", self.bg_button)
        style_form.addRow("Текст", self.text_button)
        style_form.addRow("Обводка текста", self.text_outline_button)
        style_form.addRow("Рамка", self.border_button)
        style_form.addRow("Метка", self.marker_button)
        self.alpha_slider.setRange(15, 100)
        self.alpha_slider.valueChanged.connect(self.update_draft_from_panel)
        style_form.addRow("Прозрачность", self.alpha_slider)
        self.font_combo.currentFontChanged.connect(self.update_draft_from_panel)
        self.add_font_button.clicked.connect(self.add_custom_font)
        font_row = QHBoxLayout()
        font_row.addWidget(self.font_combo, 1)
        font_row.addWidget(self.add_font_button)
        style_form.addRow("Шрифт", font_row)
        self.custom_fonts_label.setWordWrap(True)
        self.custom_fonts_label.setStyleSheet("color: #555;")
        style_form.addRow("", self.custom_fonts_label)
        self.font_size.setRange(8, 72)
        self.font_size.valueChanged.connect(self.update_draft_from_panel)
        style_form.addRow("Размер", self.font_size)
        self.text_outline_width.setRange(0, 8)
        self.text_outline_width.valueChanged.connect(self.update_draft_from_panel)
        style_form.addRow("Толщина обводки", self.text_outline_width)
        self.padding.setRange(2, 48)
        self.padding.valueChanged.connect(self.update_draft_from_panel)
        style_form.addRow("Отступ", self.padding)
        return style_box

    def build_overlay_group(self) -> QGroupBox:
        overlay_box = QGroupBox("Overlay и OCR")
        overlay_layout = QVBoxLayout(overlay_box)
        for preset in OCR_PRESETS:
            self.ocr_preset_combo.addItem(preset.label, preset.id)
        self.ocr_preset_combo.currentIndexChanged.connect(
            lambda _index: self.update_draft_from_panel()
        )
        self.ocr_preset_description.setWordWrap(True)
        self.ocr_preset_description.setStyleSheet("color: #555;")
        self.overlay_checkbox.toggled.connect(self.on_overlay_toggled)
        self.overlay_edit_checkbox.toggled.connect(self.on_overlay_edit_toggled)
        self.scan_button.clicked.connect(self.scan_area)
        self.clear_overlay_button.clicked.connect(self.clear_overlay)
        overlay_layout.addWidget(QLabel("OCR пресет"))
        overlay_layout.addWidget(self.ocr_preset_combo)
        overlay_layout.addWidget(self.ocr_preset_description)
        overlay_layout.addWidget(self.overlay_checkbox)
        overlay_layout.addWidget(self.overlay_edit_checkbox)
        buttons = QHBoxLayout()
        buttons.addWidget(self.scan_button)
        buttons.addWidget(self.clear_overlay_button)
        overlay_layout.addLayout(buttons)
        hint = QLabel(
            "OCR берет сохраненную красную область. "
            "Edit mode нужен для движения и скрытия окон."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555;")
        overlay_layout.addWidget(hint)
        return overlay_box

    def initial_status(self) -> str:
        renderer = "QOpenGLWidget" if OPENGL_WIDGET_AVAILABLE else "QWidget fallback"
        return (
            "Настройки загружены автоматически. "
            f"Backend: mss {mss.__version__}. Renderer: {renderer}. "
            f"Лог: {get_current_log_path().resolve()}"
        )

    def save_settings(self) -> None:
        self.update_draft_from_panel(mark_dirty=False)
        self.saved_settings = copy.deepcopy(self.draft_settings)
        self.font_registry.load_paths(self.saved_settings.style.custom_font_paths)
        self.ocr_pipeline.set_preset(self.saved_settings.ocr.preset_id)
        save_settings(self.saved_settings)
        self.overlay_window.set_style(self.saved_settings.style)
        if self.overlay_window.isVisible():
            self.overlay_window.show_overlay()
        self.dirty = False
        self.set_status(f"Сохранено и применено: {SETTINGS_PATH.resolve()}")
        logger.info("Settings were saved and applied")

    def sync_panel_from_draft(self) -> None:
        self.syncing = True
        try:
            area = self.draft_settings.area
            style = self.draft_settings.style
            self.area_x.setValue(area.x)
            self.area_y.setValue(area.y)
            self.area_width.setValue(area.width)
            self.area_height.setValue(area.height)
            self.alpha_slider.setValue(round(style.alpha * 100))
            self.font_combo.setCurrentFont(QFont(style.font_family))
            self.font_size.setValue(style.font_size)
            self.text_outline_width.setValue(style.text_outline_width)
            self.padding.setValue(style.padding)
            self.set_combo_data(self.ocr_preset_combo, self.draft_settings.ocr.preset_id)
            self.update_color_buttons()
            self.update_custom_fonts_label()
            self.update_ocr_preset_description()
        finally:
            self.syncing = False

    def update_draft_from_panel(self, mark_dirty: bool = True) -> None:
        if self.syncing:
            return
        self.draft_settings.area = self.clamp_area(
            TranslationArea(
                x=self.area_x.value(),
                y=self.area_y.value(),
                width=self.area_width.value(),
                height=self.area_height.value(),
            )
        )
        style = self.draft_settings.style
        self.draft_settings.style = OverlayStyle(
            bg_color=style.bg_color,
            text_color=style.text_color,
            text_outline_color=style.text_outline_color,
            text_outline_width=self.text_outline_width.value(),
            border_color=style.border_color,
            marker_color=style.marker_color,
            alpha=self.alpha_slider.value() / 100,
            font_family=self.font_combo.currentFont().family(),
            font_size=self.font_size.value(),
            padding=self.padding.value(),
            custom_font_paths=list(style.custom_font_paths),
        )
        self.draft_settings.ocr = OcrSettings(
            preset_id=str(self.ocr_preset_combo.currentData())
        )
        self.preview.set_settings(self.draft_settings)
        self.sync_panel_from_draft()
        if mark_dirty:
            self.mark_dirty()

    def update_color_buttons(self) -> None:
        style = self.draft_settings.style
        for button, color in (
            (self.bg_button, style.bg_color),
            (self.text_button, style.text_color),
            (self.text_outline_button, style.text_outline_color),
            (self.border_button, style.border_color),
            (self.marker_button, style.marker_color),
        ):
            button.setText(color)
            button.setStyleSheet(f"background-color: {color};")

    def update_custom_fonts_label(self) -> None:
        count = len(self.draft_settings.style.custom_font_paths)
        if count == 0:
            self.custom_fonts_label.setText("Пользовательские шрифты не добавлены.")
        else:
            self.custom_fonts_label.setText(f"Добавлено пользовательских шрифтов: {count}.")

    def update_ocr_preset_description(self) -> None:
        preset = get_ocr_preset(self.draft_settings.ocr.preset_id)
        self.ocr_preset_description.setText(preset.description)

    def set_combo_data(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def choose_color(self, field: str) -> None:
        current = QColor(getattr(self.draft_settings.style, field))
        color = QColorDialog.getColor(current, self, "Выбор цвета")
        if not color.isValid():
            return
        setattr(self.draft_settings.style, field, color.name())
        self.update_color_buttons()
        self.preview.set_settings(self.draft_settings)
        self.mark_dirty()
        logger.info("Draft color changed: %s=%s", field, color.name())

    def add_custom_font(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Добавить шрифт",
            "",
            "Fonts (*.ttf *.otf *.ttc)",
        )
        if not path:
            return

        result = self.font_registry.add_font_file(Path(path))
        if result is None:
            self.set_status("Не удалось добавить шрифт. Детали записаны в лог.")
            return

        style = self.draft_settings.style
        style.custom_font_paths = unique_font_paths(
            [*style.custom_font_paths, str(result.path)]
        )
        if result.families:
            style.font_family = result.families[0]
        self.sync_panel_from_draft()
        self.preview.set_settings(self.draft_settings)
        self.mark_dirty()
        self.set_status("Шрифт добавлен. Он применится к overlay после «Сохранить».")
        logger.info("Custom font added through UI: %s", result.path)

    def on_area_changed_from_preview(self, area: TranslationArea) -> None:
        self.draft_settings.area = self.clamp_area(area)
        self.sync_panel_from_draft()
        self.preview.set_settings(self.draft_settings)
        self.mark_dirty()
        logger.debug("Draft area changed from preview: %s", self.draft_settings.area)

    def set_area_fullscreen(self) -> None:
        self.draft_settings.area = TranslationArea(
            x=self.screen.x,
            y=self.screen.y,
            width=self.screen.width,
            height=self.screen.height,
        )
        self.sync_panel_from_draft()
        self.preview.set_settings(self.draft_settings)
        self.mark_dirty()
        logger.info("Draft area set to fullscreen")

    def set_area_centered(self) -> None:
        self.draft_settings.area = AppSettings.default(self.screen).area
        self.sync_panel_from_draft()
        self.preview.set_settings(self.draft_settings)
        self.mark_dirty()
        logger.info("Draft area centered")

    def on_live_toggled(self, enabled: bool) -> None:
        if enabled:
            self.start_capture()
            self.set_status("Live preview включен.")
        else:
            self.stop_capture()
            self.set_status("Live preview на паузе.")
        logger.info("Live preview toggled: %s", enabled)

    def on_fps_changed(self) -> None:
        if self.capture_thread is not None:
            self.capture_thread.set_fps(self.current_fps())

    def current_fps(self) -> int:
        try:
            return max(1, min(MAX_PREVIEW_FPS, int(self.fps_combo.currentText())))
        except ValueError:
            return DEFAULT_PREVIEW_FPS

    def start_capture(self) -> None:
        self.stop_capture()
        self.capture_thread = CaptureThread(self.screen, self.current_fps())
        self.capture_thread.frame_captured.connect(self.on_frame_captured)
        self.capture_thread.capture_error.connect(self.set_status)
        self.capture_thread.start()

    def stop_capture(self) -> None:
        if self.capture_thread is not None:
            self.capture_thread.stop()
            self.capture_thread = None

    def refresh_once(self) -> None:
        try:
            frame = grab_screen_qimage(self.screen)
            self.on_frame_captured(frame)
            logger.info("Manual preview refresh completed")
        except Exception as exc:  # noqa: BLE001 - UI boundary
            logger.exception("Manual preview refresh failed")
            self.set_status(f"Не удалось обновить кадр: {exc}")

    def on_overlay_toggled(self, enabled: bool) -> None:
        self.overlay_window.set_style(self.saved_settings.style)
        self.overlay_window.set_edit_mode(self.overlay_edit_checkbox.isChecked())
        if enabled:
            self.overlay_window.show_overlay()
            mode = "edit" if self.overlay_edit_checkbox.isChecked() else "click-through"
            self.set_status(f"Overlay включен ({mode}).")
        else:
            self.overlay_window.hide()
            self.set_status("Overlay скрыт.")
        logger.info("Overlay visibility toggled: %s", enabled)

    def on_overlay_edit_toggled(self, enabled: bool) -> None:
        if enabled and not self.overlay_checkbox.isChecked():
            self.overlay_checkbox.setChecked(True)
        self.overlay_window.set_edit_mode(enabled)
        if self.overlay_window.isVisible():
            self.overlay_window.show_overlay()
        self.set_status(
            "Overlay edit включен: окна можно двигать и скрывать двойным кликом."
            if enabled
            else "Overlay edit выключен: окно снова пропускает клики к приложениям."
        )

    def scan_area(self) -> None:
        settings = self.saved_settings
        was_visible = self.overlay_window.isVisible()
        if was_visible:
            self.overlay_window.hide()
            QApplication.processEvents()
        try:
            result = self.ocr_pipeline.scan_area(settings.area, settings.style)
        except Exception as exc:  # noqa: BLE001 - UI boundary
            if was_visible:
                self.overlay_window.show_overlay()
            logger.exception("OCR scan failed")
            self.set_status(f"OCR не смог захватить область: {exc}")
            return

        self.overlay_window.set_style(settings.style)
        self.overlay_window.set_boxes(result.boxes)
        if not self.overlay_checkbox.isChecked():
            self.overlay_checkbox.setChecked(True)
        self.overlay_window.show_overlay()

        dirty_note = " Несохраненные изменения пока не применены." if self.dirty else ""
        warning = f" {result.warning}" if result.warning else ""
        result_label = "Диагностика" if result.diagnostic else f"Найдено окон: {len(result.boxes)}"
        self.set_status(
            "OCR: "
            f"{result.engine_name}. Перевод: {result.translation_engine_name}. "
            f"{result_label}.{dirty_note}{warning}"
        )
        logger.info("OCR scan completed with %s boxes", len(result.boxes))

    def clear_overlay(self) -> None:
        self.overlay_window.clear_boxes()
        self.set_status("Overlay очищен.")
        logger.info("Overlay cleared")

    def on_frame_captured(self, frame: QImage) -> None:
        self.preview.set_frame(self.mask_preview_canvas(frame))

    def mask_preview_canvas(self, frame: QImage) -> QImage:
        if frame.isNull():
            return frame
        image = frame
        # The preview canvas is masked only inside captured frames to avoid recursion.
        top_left = self.preview.mapToGlobal(QPoint(0, 0))
        ratio_x = image.width() / max(1, self.screen.width)
        ratio_y = image.height() / max(1, self.screen.height)
        left = round((top_left.x() - self.screen.x) * ratio_x)
        top = round((top_left.y() - self.screen.y) * ratio_y)
        width = round(self.preview.width() * ratio_x)
        height = round(self.preview.height() * ratio_y)
        rect = QRectF(
            max(0, left),
            max(0, top),
            min(width, image.width() - max(0, left)),
            min(height, image.height() - max(0, top)),
        )
        painter = QPainter(image)
        painter.fillRect(rect, QColor(PREVIEW_BACKGROUND))
        painter.end()
        return image

    def clamp_area(self, area: TranslationArea) -> TranslationArea:
        return clamp_area_to_screen(area, self.screen)

    def mark_dirty(self) -> None:
        if not self.dirty:
            self.set_status("Есть несохраненные изменения. Они применятся после «Сохранить».")
        self.dirty = True
        logger.debug("Settings marked dirty")

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        self.stop_capture()
        self.overlay_window.close()
        logger.info("Main window closed")
        super().closeEvent(event)
