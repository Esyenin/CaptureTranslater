from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtGui import QImage

from .boxes import TranslationBox
from .capture import grab_screen_qimage
from .geometry import clamp
from .models import OverlayStyle, ScreenRect, TranslationArea
from .ocr import (
    DetectedText,
    DummyOcrEngine,
    OcrEngine,
    OcrEngineUnavailable,
    create_ocr_engine,
)
from .translation import (
    IdentityTranslator,
    TranslationEngine,
    TranslationUnavailable,
    create_translation_engine,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    boxes: list[TranslationBox]
    engine_name: str
    translation_engine_name: str
    warning: str = ""
    diagnostic: bool = False


class OcrPipeline:
    def __init__(
        self,
        screen: ScreenRect,
        preset_id: str | None = None,
        engine: OcrEngine | None = None,
        translator: TranslationEngine | None = None,
    ) -> None:
        self.screen = screen
        self.preset_id = preset_id
        self.engine = engine or create_ocr_engine(preset_id)
        self.translator = translator or create_translation_engine()
        logger.info("OCR pipeline initialized with engine=%s", self.engine.name)

    def set_preset(self, preset_id: str) -> None:
        if preset_id == self.preset_id:
            return
        self.preset_id = preset_id
        self.engine = create_ocr_engine(preset_id)
        logger.info("OCR pipeline preset changed to %s", preset_id)

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
        return self.scan_image(area, style, image)

    def scan_image(
        self,
        area: TranslationArea,
        style: OverlayStyle,
        image: QImage,
    ) -> PipelineResult:
        logger.info(
            "Running OCR pipeline on prepared image size=%sx%s with engine=%s",
            image.width(),
            image.height(),
            self.engine.name,
        )
        warning = ""
        engine_name = self.engine.name
        try:
            detections = self.engine.recognize(image)
        except OcrEngineUnavailable as exc:
            logger.warning("OCR engine is unavailable: %s", exc)
            warning = f"{exc} Перевод невозможен без OCR."
            boxes = [
                self.build_diagnostic_box(
                    area,
                    style,
                    f"{exc}\n\nПеревод невозможен: сначала нужно подключить OCR.",
                )
            ]
            return PipelineResult(
                boxes=boxes,
                engine_name=engine_name,
                translation_engine_name=self.translator.name,
                warning=warning,
                diagnostic=True,
            )
        except Exception as exc:  # noqa: BLE001 - optional OCR engines fail in many local setups
            logger.exception("Primary OCR engine failed; switching to fallback")
            fallback = DummyOcrEngine()
            detections = fallback.recognize(image)
            engine_name = fallback.name
            warning = f"Основной OCR не сработал ({exc}); показан fallback."

        if not detections:
            logger.info("OCR completed but found no text blocks")
            return PipelineResult(
                boxes=[
                    self.build_diagnostic_box(
                        area,
                        style,
                        "OCR не нашел текст в выбранной области.",
                    )
                ],
                engine_name=engine_name,
                translation_engine_name=self.translator.name,
                warning="OCR не нашел текст.",
                diagnostic=True,
            )

        translated_texts, translation_warning = self.translate_detections(detections)
        if translation_warning:
            warning = f"{warning} {translation_warning}".strip()

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
                    translated_text=translated_texts[index],
                )
            )
        logger.info("Pipeline produced %s overlay boxes", len(boxes))
        return PipelineResult(
            boxes=boxes,
            engine_name=engine_name,
            translation_engine_name=self.translator.name,
            warning=warning,
        )

    def translate_detections(self, detections: list[DetectedText]) -> tuple[list[str], str]:
        source_texts = [str(detection.text) for detection in detections]
        try:
            translated = self.translator.translate_batch(source_texts)
            logger.info("Translation completed for %s text blocks", len(translated))
            return translated, ""
        except TranslationUnavailable as exc:
            logger.warning("Translation engine is unavailable: %s", exc)
            fallback = IdentityTranslator()
            return fallback.translate_batch(source_texts), str(exc)
        except Exception:
            logger.exception("Translation failed; source text will be shown")
            fallback = IdentityTranslator()
            return (
                fallback.translate_batch(source_texts),
                "Перевод не сработал; показан исходный текст.",
            )

    def build_diagnostic_box(
        self,
        area: TranslationArea,
        style: OverlayStyle,
        text: str,
    ) -> TranslationBox:
        padding = max(6, style.padding)
        box_width = min(max(360, area.width // 2), max(120, area.width - padding * 2))
        box_height = min(max(120, area.height // 6), max(80, area.height - padding * 2))
        return TranslationBox(
            id="diagnostic",
            x=area.x + padding,
            y=area.y + padding,
            width=box_width,
            height=box_height,
            source_text=text,
            translated_text=text,
        )
