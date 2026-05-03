from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QImage

from .models import OverlayStyle, TranslationArea
from .pipeline import OcrPipeline


logger = logging.getLogger(__name__)


class ScanWorker(QObject):
    """Runs the slow OCR and translation pipeline away from the UI thread."""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        pipeline: OcrPipeline,
        area: TranslationArea,
        style: OverlayStyle,
        image: QImage | None = None,
        scan_id: str = "scan",
    ) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.area = area
        self.style = style
        self.image = image
        self.scan_id = scan_id

    @Slot()
    def run(self) -> None:
        started = time.perf_counter()
        try:
            image_info = (
                "none"
                if self.image is None
                else f"{self.image.width()}x{self.image.height()}"
            )
            logger.info(
                "[%s] OCR scan worker started area=%s,%s %sx%s image=%s",
                self.scan_id,
                self.area.x,
                self.area.y,
                self.area.width,
                self.area.height,
                image_info,
            )
            if self.image is None:
                result = self.pipeline.scan_area(self.area, self.style, self.scan_id)
            else:
                result = self.pipeline.scan_image(
                    self.area,
                    self.style,
                    self.image,
                    self.scan_id,
                )
            logger.info(
                "[%s] OCR scan worker finished in %.3fs",
                self.scan_id,
                time.perf_counter() - started,
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001 - worker boundary
            logger.exception(
                "[%s] OCR scan worker failed after %.3fs",
                self.scan_id,
                time.perf_counter() - started,
            )
            self.failed.emit(str(exc))
