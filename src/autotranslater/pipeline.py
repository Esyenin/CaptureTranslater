from __future__ import annotations

import logging
from dataclasses import dataclass

from .boxes import TranslationBox
from .capture import grab_screen_qimage
from .geometry import clamp
from .models import OverlayStyle, ScreenRect, TranslationArea
from .ocr import DummyOcrEngine, OcrEngine, create_ocr_engine


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    boxes: list[TranslationBox]
    engine_name: str
    warning: str = ""


class OcrPipeline:
    def __init__(self, screen: ScreenRect, engine: OcrEngine | None = None) -> None:
        self.screen = screen
        self.engine = engine or create_ocr_engine()
        logger.info("OCR pipeline initialized with engine=%s", self.engine.name)

    def scan_area(self, area: TranslationArea, style: OverlayStyle) -> PipelineResult:
        logger.info(
            "Scanning area x=%s y=%s size=%sx%s with engine=%s",
            area.x,
            area.y,
            area.width,
            area.height,
            self.engine.name,
        )
        region = ScreenRect(area.x, area.y, area.width, area.height)
        image = grab_screen_qimage(region)
        warning = ""
        engine_name = self.engine.name
        try:
            detections = self.engine.recognize(image)
        except Exception as exc:  # noqa: BLE001 - optional OCR engines fail in many local setups
            logger.exception("Primary OCR engine failed; switching to fallback")
            fallback = DummyOcrEngine()
            detections = fallback.recognize(image)
            engine_name = fallback.name
            warning = f"Основной OCR не сработал ({exc}); показан fallback."

        boxes: list[TranslationBox] = []
        padding = max(6, style.padding)
        for index, detection in enumerate(detections):
            # OCR coordinates are local to the captured region; overlay boxes
            # must be converted back to virtual desktop coordinates.
            box_width = max(96, detection.width + padding * 2)
            box_height = max(40, detection.height + padding * 2)
            x = round(
                clamp(
                    area.x + detection.x - padding,
                    self.screen.x,
                    self.screen.x + self.screen.width - box_width,
                )
            )
            y = round(
                clamp(
                    area.y + detection.y - padding,
                    self.screen.y,
                    self.screen.y + self.screen.height - box_height,
                )
            )
            boxes.append(
                TranslationBox(
                    id=f"scan-{index}",
                    x=x,
                    y=y,
                    width=round(box_width),
                    height=round(box_height),
                    source_text=detection.text,
                    translated_text=detection.text,
                )
            )
        logger.info("Pipeline produced %s overlay boxes", len(boxes))
        return PipelineResult(boxes=boxes, engine_name=engine_name, warning=warning)
