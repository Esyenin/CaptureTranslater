from __future__ import annotations

import logging

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
    ) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.area = area
        self.style = style
        self.image = image

    @Slot()
    def run(self) -> None:
        try:
            logger.info("OCR scan worker started")
            if self.image is None:
                result = self.pipeline.scan_area(self.area, self.style)
            else:
                result = self.pipeline.scan_image(self.area, self.style, self.image)
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001 - worker boundary
            logger.exception("OCR scan worker failed")
            self.failed.emit(str(exc))
