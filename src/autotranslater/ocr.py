from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PySide6.QtGui import QImage


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


class DummyOcrEngine:
    name = "fallback OCR"

    def recognize(self, image: QImage) -> list[DetectedText]:
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
    name = "pytesseract"

    def __init__(self, languages: str = "eng+rus") -> None:
        self.languages = languages

    @classmethod
    def available(cls) -> bool:
        try:
            import PIL.Image  # noqa: F401
            import pytesseract  # noqa: F401
        except ImportError:
            return False
        return True

    def recognize(self, image: QImage) -> list[DetectedText]:
        import pytesseract
        from PIL import Image
        from pytesseract import Output

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
            if confidence < 0:
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
            entry["right"] = max(int(entry["right"]), int(raw["left"][index]) + int(raw["width"][index]))
            entry["bottom"] = max(int(entry["bottom"]), int(raw["top"][index]) + int(raw["height"][index]))

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
        return detections

    @staticmethod
    def parse_confidence(value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return -1.0


def create_ocr_engine() -> OcrEngine:
    if PytesseractOcrEngine.available():
        return PytesseractOcrEngine()
    return DummyOcrEngine()

