from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from hashlib import blake2b

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
MIN_MEANINGFUL_CHARS = 2
MAX_MERGE_GAP_FACTOR = 1.7
MAX_OCR_CACHE_ITEMS = 8


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
        self.ocr_cache: dict[str, list[DetectedText]] = {}
        self.ocr_cache_order: list[str] = []
        logger.info("OCR pipeline initialized with engine=%s", self.engine.name)

    def set_preset(self, preset_id: str) -> None:
        if preset_id == self.preset_id:
            return
        self.preset_id = preset_id
        self.engine = create_ocr_engine(preset_id)
        self.ocr_cache.clear()
        self.ocr_cache_order.clear()
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
            detections = self.recognize_with_cache(image)
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

        original_count = len(detections)
        detections = self.prepare_detections(detections, image)
        if not detections:
            logger.info("OCR text blocks were filtered out as noise")
            return PipelineResult(
                boxes=[
                    self.build_diagnostic_box(
                        area,
                        style,
                        "OCR нашел только шумовые фрагменты в выбранной области.",
                    )
                ],
                engine_name=engine_name,
                translation_engine_name=self.translator.name,
                warning="OCR нашел только шум.",
                diagnostic=True,
            )
        logger.info(
            "OCR detections prepared: raw=%s prepared=%s",
            original_count,
            len(detections),
        )

        translation_started = time.perf_counter()
        translated_texts, translation_warning = self.translate_detections(detections)
        logger.info(
            "Translation stage finished in %.3fs",
            time.perf_counter() - translation_started,
        )
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

    def recognize_with_cache(self, image: QImage) -> list[DetectedText]:
        cache_key = ocr_image_cache_key(image)
        cached = self.ocr_cache.get(cache_key)
        if cached is not None:
            logger.info("OCR cache hit for prepared image")
            return list(cached)

        ocr_started = time.perf_counter()
        detections = self.engine.recognize(image)
        logger.info("OCR stage finished in %.3fs", time.perf_counter() - ocr_started)
        self.remember_ocr_result(cache_key, detections)
        return detections

    def remember_ocr_result(self, cache_key: str, detections: list[DetectedText]) -> None:
        self.ocr_cache[cache_key] = list(detections)
        self.ocr_cache_order.append(cache_key)
        while len(self.ocr_cache_order) > MAX_OCR_CACHE_ITEMS:
            expired = self.ocr_cache_order.pop(0)
            self.ocr_cache.pop(expired, None)

    def prepare_detections(
        self,
        detections: list[DetectedText],
        image: QImage,
    ) -> list[DetectedText]:
        filtered = [
            detection
            for detection in detections
            if self.is_meaningful_detection(detection, image)
        ]
        return merge_nearby_detections(filtered)

    def is_meaningful_detection(
        self,
        detection: DetectedText,
        image: QImage,
    ) -> bool:
        text = normalize_detection_text(detection.text)
        meaningful_chars = len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]", text))
        if meaningful_chars < MIN_MEANINGFUL_CHARS:
            return False
        if detection.width < 10 or detection.height < 8:
            return False
        if detection.width > image.width() * 0.92 and detection.height < 24:
            return False
        return True

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


def merge_nearby_detections(detections: list[DetectedText]) -> list[DetectedText]:
    if not detections:
        return []

    groups: list[DetectionGroup] = []
    for detection in sorted(detections, key=lambda item: (item.y, item.x)):
        target = next(
            (
                group
                for group in reversed(groups)
                if group.can_accept(detection)
            ),
            None,
        )
        if target is None:
            groups.append(DetectionGroup(detection))
        else:
            target.add(detection)

    return [group.to_detection() for group in groups]


@dataclass
class DetectionGroup:
    first: DetectedText

    def __post_init__(self) -> None:
        self.items: list[DetectedText] = [self.first]
        self.left = self.first.x
        self.top = self.first.y
        self.right = self.first.x + self.first.width
        self.bottom = self.first.y + self.first.height

    def can_accept(self, detection: DetectedText) -> bool:
        gap = detection.y - self.bottom
        average_height = max(1, self.average_height)
        if gap < -average_height * 0.45 or gap > average_height * MAX_MERGE_GAP_FACTOR:
            return False

        detection_right = detection.x + detection.width
        overlap = max(0, min(self.right, detection_right) - max(self.left, detection.x))
        min_width = max(1, min(self.right - self.left, detection.width))
        overlap_ratio = overlap / min_width
        left_distance = abs(detection.x - self.left)
        same_column = overlap_ratio >= 0.25 or left_distance <= max(80, min_width * 0.35)
        return same_column

    def add(self, detection: DetectedText) -> None:
        self.items.append(detection)
        self.left = min(self.left, detection.x)
        self.top = min(self.top, detection.y)
        self.right = max(self.right, detection.x + detection.width)
        self.bottom = max(self.bottom, detection.y + detection.height)

    @property
    def average_height(self) -> float:
        return sum(item.height for item in self.items) / max(1, len(self.items))

    def to_detection(self) -> DetectedText:
        ordered = sorted(self.items, key=lambda item: (item.y, item.x))
        text = " ".join(normalize_detection_text(item.text) for item in ordered)
        confidence = sum(item.confidence for item in ordered) / max(1, len(ordered))
        return DetectedText(
            x=self.left,
            y=self.top,
            width=max(1, self.right - self.left),
            height=max(1, self.bottom - self.top),
            text=text,
            confidence=confidence,
        )


def normalize_detection_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def ocr_image_cache_key(image: QImage) -> str:
    normalized = image.convertToFormat(QImage.Format.Format_RGB32)
    payload = bytes(normalized.bits())
    digest = blake2b(digest_size=16)
    digest.update(str(normalized.width()).encode("ascii"))
    digest.update(b"x")
    digest.update(str(normalized.height()).encode("ascii"))
    digest.update(b":")
    digest.update(payload)
    return digest.hexdigest()
