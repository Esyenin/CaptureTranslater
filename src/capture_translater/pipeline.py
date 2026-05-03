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

    def scan_area(
        self,
        area: TranslationArea,
        style: OverlayStyle,
        scan_id: str = "scan",
    ) -> PipelineResult:
        logger.info(
            "[%s] Scanning area x=%s y=%s size=%sx%s with engine=%s",
            scan_id,
            area.x,
            area.y,
            area.width,
            area.height,
            self.engine.name,
        )
        region = ScreenRect(area.x, area.y, area.width, area.height)
        image = grab_screen_qimage(region, diagnostic_label=scan_id)
        return self.scan_image(area, style, image, scan_id)

    def scan_image(
        self,
        area: TranslationArea,
        style: OverlayStyle,
        image: QImage,
        scan_id: str = "scan",
    ) -> PipelineResult:
        pipeline_started = time.perf_counter()
        logger.info(
            "[%s] Running OCR pipeline on prepared image size=%sx%s with engine=%s "
            "translator=%s",
            scan_id,
            image.width(),
            image.height(),
            self.engine.name,
            self.translator.name,
        )
        warning = ""
        engine_name = self.engine.name
        try:
            detections = self.recognize_with_cache(image, scan_id)
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
            logger.info("[%s] OCR completed but found no text blocks", scan_id)
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
        prepare_started = time.perf_counter()
        detections = self.prepare_detections(detections, image, scan_id)
        logger.info(
            "[%s] Detection preparation finished in %.3fs",
            scan_id,
            time.perf_counter() - prepare_started,
        )
        if not detections:
            logger.info("[%s] OCR text blocks were filtered out as noise", scan_id)
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
            "[%s] OCR detections prepared: raw=%s prepared=%s",
            scan_id,
            original_count,
            len(detections),
        )

        translation_started = time.perf_counter()
        translated_texts, translation_warning = self.translate_detections(
            detections,
            scan_id,
        )
        logger.info(
            "[%s] Translation stage finished in %.3fs",
            scan_id,
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
        logger.info(
            "[%s] Pipeline produced %s overlay boxes in %.3fs total",
            scan_id,
            len(boxes),
            time.perf_counter() - pipeline_started,
        )
        return PipelineResult(
            boxes=boxes,
            engine_name=engine_name,
            translation_engine_name=self.translator.name,
            warning=warning,
        )

    def recognize_with_cache(
        self,
        image: QImage,
        scan_id: str = "scan",
    ) -> list[DetectedText]:
        cache_started = time.perf_counter()
        cache_key = ocr_image_cache_key(image)
        logger.info(
            "[%s] OCR image cache key computed in %.3fs key=%s cache_items=%s",
            scan_id,
            time.perf_counter() - cache_started,
            cache_key[:12],
            len(self.ocr_cache),
        )
        cached = self.ocr_cache.get(cache_key)
        if cached is not None:
            logger.info(
                "[%s] OCR cache hit for prepared image; detections=%s",
                scan_id,
                len(cached),
            )
            return list(cached)

        logger.info("[%s] OCR cache miss; running engine=%s", scan_id, self.engine.name)
        ocr_started = time.perf_counter()
        detections = self.engine.recognize(image)
        logger.info(
            "[%s] OCR stage finished in %.3fs with %s detections",
            scan_id,
            time.perf_counter() - ocr_started,
            len(detections),
        )
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
        scan_id: str = "scan",
    ) -> list[DetectedText]:
        filtered = [
            detection
            for detection in detections
            if self.is_meaningful_detection(detection, image)
        ]
        merged = merge_nearby_detections(filtered)
        logger.debug(
            "[%s] Detection filter details: raw=%s filtered=%s merged=%s samples=%s",
            scan_id,
            len(detections),
            len(filtered),
            len(merged),
            detection_samples(merged),
        )
        return merged

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

    def translate_detections(
        self,
        detections: list[DetectedText],
        scan_id: str = "scan",
    ) -> tuple[list[str], str]:
        source_texts = [str(detection.text) for detection in detections]
        total_chars = sum(len(text) for text in source_texts)
        logger.info(
            "[%s] Translation input: blocks=%s chars=%s engine=%s",
            scan_id,
            len(source_texts),
            total_chars,
            self.translator.name,
        )
        try:
            translated = self.translator.translate_batch(source_texts)
            logger.info(
                "[%s] Translation completed for %s text blocks",
                scan_id,
                len(translated),
            )
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


def detection_samples(detections: list[DetectedText], limit: int = 5) -> list[str]:
    samples: list[str] = []
    for detection in detections[:limit]:
        text = normalize_detection_text(detection.text)
        if len(text) > 80:
            text = f"{text[:77]}..."
        samples.append(
            f"{detection.x},{detection.y} {detection.width}x{detection.height} "
            f"conf={detection.confidence:.2f} text={text!r}"
        )
    return samples


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
