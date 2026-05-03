from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from math import floor
from typing import Protocol

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from .ocr_presets import OcrPreset, get_ocr_preset


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectedText:
    x: int
    y: int
    width: int
    height: int
    text: str
    confidence: float = 0.0


class OcrEngine(Protocol):
    name: str

    def recognize(self, image: QImage) -> list[DetectedText]:
        ...


class OcrEngineUnavailable(RuntimeError):
    pass


class DummyOcrEngine:
    name = "fallback OCR"

    def recognize(self, image: QImage) -> list[DetectedText]:
        logger.debug(
            "Dummy OCR requested for image size=%sx%s",
            image.width(),
            image.height(),
        )
        width = max(120, min(460, image.width() - 48))
        height = max(64, min(140, image.height() - 48))
        return [
            DetectedText(
                x=24,
                y=24,
                width=width,
                height=height,
                text="OCR пока не подключен. Это тестовое окно overlay.",
                confidence=1.0,
            )
        ]


class PytesseractOcrEngine:
    def __init__(
        self,
        languages: str = "eng+rus",
        confidence_threshold: float = 0.0,
    ) -> None:
        self.name = f"pytesseract:{languages}"
        self.languages = languages
        self.confidence_threshold = confidence_threshold

    @classmethod
    def available(cls) -> bool:
        try:
            import PIL.Image  # noqa: F401
            import pytesseract  # noqa: F401
        except ImportError:
            logger.info("pytesseract/Pillow are not installed; using fallback OCR")
            return False
        logger.info("pytesseract/Pillow imports are available")
        return True

    def recognize(self, image: QImage) -> list[DetectedText]:
        import pytesseract
        from PIL import Image
        from pytesseract import Output

        logger.info(
            "Running pytesseract OCR on image size=%sx%s with languages=%s",
            image.width(),
            image.height(),
            self.languages,
        )
        qimage = image.convertToFormat(QImage.Format.Format_RGBA8888)
        data = bytes(qimage.bits())
        pil_image = Image.frombytes(
            "RGBA",
            (qimage.width(), qimage.height()),
            data,
            "raw",
            "RGBA",
            qimage.bytesPerLine(),
            1,
        )
        raw = pytesseract.image_to_data(
            pil_image,
            lang=self.languages,
            config="--psm 6",
            output_type=Output.DICT,
        )
        grouped: dict[tuple[int, int, int], dict[str, object]] = {}
        for index, raw_text in enumerate(raw.get("text", [])):
            text = str(raw_text).strip()
            if not text:
                continue
            confidence = self.parse_confidence(raw.get("conf", ["-1"])[index])
            if confidence < max(0.0, self.confidence_threshold):
                continue
            key = (
                int(raw["block_num"][index]),
                int(raw["par_num"][index]),
                int(raw["line_num"][index]),
            )
            entry = grouped.setdefault(
                key,
                {
                    "texts": [],
                    "confidences": [],
                    "left": int(raw["left"][index]),
                    "top": int(raw["top"][index]),
                    "right": int(raw["left"][index]) + int(raw["width"][index]),
                    "bottom": int(raw["top"][index]) + int(raw["height"][index]),
                },
            )
            entry["texts"].append(text)
            entry["confidences"].append(confidence)
            entry["left"] = min(int(entry["left"]), int(raw["left"][index]))
            entry["top"] = min(int(entry["top"]), int(raw["top"][index]))
            entry["right"] = max(
                int(entry["right"]),
                int(raw["left"][index]) + int(raw["width"][index]),
            )
            entry["bottom"] = max(
                int(entry["bottom"]),
                int(raw["top"][index]) + int(raw["height"][index]),
            )

        detections: list[DetectedText] = []
        for entry in grouped.values():
            text = " ".join(entry["texts"])
            left = int(entry["left"])
            top = int(entry["top"])
            right = int(entry["right"])
            bottom = int(entry["bottom"])
            confidences = list(entry["confidences"])
            detections.append(
                DetectedText(
                    x=left,
                    y=top,
                    width=max(1, right - left),
                    height=max(1, bottom - top),
                    text=text,
                    confidence=sum(confidences) / max(1, len(confidences)),
                )
            )
        logger.info("pytesseract produced %s text line detections", len(detections))
        return detections

    @staticmethod
    def parse_confidence(value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return -1.0


class PaddleOcrEngine:
    def __init__(self, preset: OcrPreset) -> None:
        self.preset = preset
        self.name = f"paddleocr:{preset.id}"
        self.model: object | None = None

    @classmethod
    def available(cls) -> bool:
        try:
            import paddleocr  # noqa: F401
        except ImportError:
            logger.info("paddleocr is not installed")
            return False
        logger.info("paddleocr import is available")
        return True

    def recognize(self, image: QImage) -> list[DetectedText]:
        model = self.get_model()
        working_image, scale = self.preprocess_image(image)
        array = qimage_to_rgb_array(working_image)
        logger.info(
            "Running PaddleOCR preset=%s size=%sx%s scale=%.2f",
            self.preset.id,
            working_image.width(),
            working_image.height(),
            scale,
        )

        started = time.perf_counter()
        if hasattr(model, "predict"):
            raw_result = model.predict(array)
        else:
            raw_result = model.ocr(array, cls=self.preset.use_textline_orientation)
        elapsed = time.perf_counter() - started

        detections = parse_paddle_result(raw_result, self.preset.confidence_threshold)
        if abs(scale - 1.0) > 0.001:
            detections = [scale_detection(detection, 1 / scale) for detection in detections]
        logger.info(
            "PaddleOCR produced %s detections in %.3fs",
            len(detections),
            elapsed,
        )
        return detections

    def get_model(self) -> object:
        if self.model is not None:
            return self.model

        configure_paddle_runtime()

        from paddleocr import PaddleOCR

        candidates = self.constructor_candidates()
        last_error: Exception | None = None
        for kwargs in candidates:
            try:
                logger.info("Initializing PaddleOCR with kwargs=%s", kwargs)
                self.model = PaddleOCR(**kwargs)
                return self.model
            except TypeError as exc:
                last_error = exc
                logger.debug("PaddleOCR constructor rejected kwargs=%s", kwargs)

        raise RuntimeError(f"PaddleOCR constructor failed: {last_error}")

    def constructor_candidates(self) -> list[dict[str, object]]:
        new_api_kwargs: dict[str, object] = {
            "use_doc_orientation_classify": self.preset.use_doc_orientation_classify,
            "use_doc_unwarping": self.preset.use_doc_unwarping,
            "use_textline_orientation": self.preset.use_textline_orientation,
        }
        if self.preset.language:
            new_api_kwargs["lang"] = self.preset.language
        if self.preset.text_det_limit_side_len is not None:
            new_api_kwargs["text_det_limit_side_len"] = self.preset.text_det_limit_side_len
        if self.preset.text_det_limit_type is not None:
            new_api_kwargs["text_det_limit_type"] = self.preset.text_det_limit_type
        if self.preset.text_recognition_batch_size is not None:
            new_api_kwargs["text_recognition_batch_size"] = (
                self.preset.text_recognition_batch_size
            )

        old_api_kwargs: dict[str, object] = {
            "use_angle_cls": self.preset.use_textline_orientation,
            "show_log": False,
        }
        if self.preset.language:
            old_api_kwargs["lang"] = self.preset.language

        dynamic_kwargs = {
            **new_api_kwargs,
            "engine": "paddle_dynamic",
            "engine_config": {"device_type": "cpu"},
        }
        return [dynamic_kwargs, new_api_kwargs, old_api_kwargs, {}]

    def preprocess_image(self, image: QImage) -> tuple[QImage, float]:
        scale = max(1.0, self.preset.upscale_factor)
        if scale <= 1.001:
            return image, 1.0
        width = max(1, floor(image.width() * scale))
        height = max(1, floor(image.height() * scale))
        return (
            image.scaled(
                width,
                height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ),
            scale,
        )


class UnavailableOcrEngine:
    def __init__(self, preset: OcrPreset, reason: str) -> None:
        self.name = f"{preset.label} недоступен"
        self.reason = reason

    def recognize(self, image: QImage) -> list[DetectedText]:
        raise OcrEngineUnavailable(self.reason)


class PlaceholderOcrEngine:
    def __init__(self, preset: OcrPreset) -> None:
        self.name = preset.label
        self.preset = preset

    def recognize(self, image: QImage) -> list[DetectedText]:
        width = max(120, min(520, image.width() - 48))
        height = max(64, min(150, image.height() - 48))
        return [
            DetectedText(
                x=24,
                y=24,
                width=width,
                height=height,
                text=f"Пресет «{self.preset.label}» пока не подключен.",
                confidence=1.0,
            )
        ]


def create_ocr_engine(preset_id: str | None = None) -> OcrEngine:
    preset = get_ocr_preset(preset_id or "")
    logger.info("Selecting OCR preset: %s", preset.id)
    if preset.engine_kind == "paddle":
        if PaddleOcrEngine.available():
            return PaddleOcrEngine(preset)
        return UnavailableOcrEngine(
            preset,
            "PaddleOCR не установлен. Установи paddlepaddle и paddleocr.",
        )
    if preset.engine_kind == "pytesseract":
        if PytesseractOcrEngine.available():
            return PytesseractOcrEngine(
                languages=preset.language or "eng+rus",
                confidence_threshold=preset.confidence_threshold,
            )
        return UnavailableOcrEngine(
            preset,
            "pytesseract/Pillow или системный Tesseract не установлены.",
        )
    if preset.engine_kind == "placeholder":
        return PlaceholderOcrEngine(preset)

    if PytesseractOcrEngine.available():
        logger.info("Selected OCR engine: pytesseract")
        return PytesseractOcrEngine()
    logger.info("Selected OCR engine: fallback")
    return DummyOcrEngine()


def configure_paddle_runtime() -> None:
    # The default static oneDNN path currently fails on some Windows/Paddle 3.x
    # setups with ConvertPirAttribute2RuntimeAttribute. Prefer dynamic CPU.
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_use_onednn", "0")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def qimage_to_rgb_array(image: QImage) -> object:
    import numpy as np

    qimage = image.convertToFormat(QImage.Format.Format_RGB888)
    width = qimage.width()
    height = qimage.height()
    data = bytes(qimage.bits())
    array = np.frombuffer(data, dtype=np.uint8).reshape((height, qimage.bytesPerLine()))
    return array[:, : width * 3].reshape((height, width, 3)).copy()


def parse_paddle_result(raw_result: object, confidence_threshold: float) -> list[DetectedText]:
    detections: list[DetectedText] = []
    pages = raw_result if isinstance(raw_result, list) else [raw_result]
    for page in pages:
        detections.extend(parse_paddle_page(page, confidence_threshold))
    return detections


def parse_paddle_page(page: object, confidence_threshold: float) -> list[DetectedText]:
    payload = extract_paddle_payload(page)
    if isinstance(payload, dict):
        return parse_paddle_dict_payload(payload, confidence_threshold)
    if isinstance(page, list):
        return parse_paddle_legacy_lines(page, confidence_threshold)
    return []


def extract_paddle_payload(page: object) -> object:
    if isinstance(page, dict):
        return page.get("res", page)
    payload = getattr(page, "res", None)
    if payload is not None:
        return payload
    json_payload = getattr(page, "json", None)
    if callable(json_payload):
        try:
            value = json_payload()
            if isinstance(value, dict):
                return value.get("res", value)
        except Exception:
            logger.debug("Paddle result json() failed", exc_info=True)
    return page


def parse_paddle_dict_payload(
    payload: dict[str, object],
    confidence_threshold: float,
) -> list[DetectedText]:
    texts = list(payload.get("rec_texts") or payload.get("texts") or [])
    scores = list(payload.get("rec_scores") or payload.get("scores") or [])
    boxes = first_present(payload, "rec_boxes", "dt_polys", "rec_polys")
    if not texts or boxes is None:
        return []

    detections: list[DetectedText] = []
    for index, text in enumerate(texts):
        clean_text = str(text).strip()
        if not clean_text:
            continue
        score = float(scores[index]) if index < len(scores) else 1.0
        if score < confidence_threshold:
            continue
        bbox = normalize_paddle_box(boxes[index])
        if bbox is None:
            continue
        left, top, right, bottom = bbox
        detections.append(
            DetectedText(
                x=left,
                y=top,
                width=max(1, right - left),
                height=max(1, bottom - top),
                text=clean_text,
                confidence=score,
            )
        )
    return detections


def first_present(payload: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def parse_paddle_legacy_lines(
    page: list[object],
    confidence_threshold: float,
) -> list[DetectedText]:
    if len(page) == 1 and isinstance(page[0], list):
        page = page[0]

    detections: list[DetectedText] = []
    for line in page:
        if not isinstance(line, (list, tuple)) or len(line) < 2:
            continue
        bbox = normalize_paddle_box(line[0])
        text_payload = line[1]
        if not isinstance(text_payload, (list, tuple)) or len(text_payload) < 2:
            continue
        text = str(text_payload[0]).strip()
        score = float(text_payload[1])
        if not text or score < confidence_threshold or bbox is None:
            continue
        left, top, right, bottom = bbox
        detections.append(
            DetectedText(
                x=left,
                y=top,
                width=max(1, right - left),
                height=max(1, bottom - top),
                text=text,
                confidence=score,
            )
        )
    return detections


def normalize_paddle_box(box: object) -> tuple[int, int, int, int] | None:
    try:
        values = box.tolist() if hasattr(box, "tolist") else box
        if len(values) == 4 and all(not isinstance(value, (list, tuple)) for value in values):
            left, top, right, bottom = [float(value) for value in values]
            return round(left), round(top), round(right), round(bottom)
        points = [point.tolist() if hasattr(point, "tolist") else point for point in values]
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        return round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))
    except Exception:
        logger.debug("Failed to normalize PaddleOCR box: %r", box, exc_info=True)
        return None


def scale_detection(detection: DetectedText, factor: float) -> DetectedText:
    return DetectedText(
        x=round(detection.x * factor),
        y=round(detection.y * factor),
        width=max(1, round(detection.width * factor)),
        height=max(1, round(detection.height * factor)),
        text=detection.text,
        confidence=detection.confidence,
    )
