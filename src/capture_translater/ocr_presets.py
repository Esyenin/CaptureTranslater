from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OcrPreset:
    id: str
    label: str
    description: str
    engine_kind: str
    language: str | None = None
    confidence_threshold: float = 0.35
    use_doc_orientation_classify: bool = False
    use_doc_unwarping: bool = False
    use_textline_orientation: bool = False
    upscale_factor: float = 1.0


DEFAULT_OCR_PRESET_ID = "paddle_english"

OCR_PRESETS: tuple[OcrPreset, ...] = (
    OcrPreset(
        id="paddle_english",
        label="PaddleOCR: английский",
        description="Быстрый основной режим для английских переводов манги и маньхуа.",
        engine_kind="paddle",
        language="en",
        confidence_threshold=0.40,
    ),
    OcrPreset(
        id="paddle_japanese",
        label="PaddleOCR: японский",
        description="Японский OCR через PaddleOCR; полезен для оригинальной манги.",
        engine_kind="paddle",
        language="japan",
        confidence_threshold=0.32,
        use_textline_orientation=True,
    ),
    OcrPreset(
        id="paddle_chinese",
        label="PaddleOCR: китайский + английский",
        description="Режим ch для маньхуа и смешанного китайско-английского текста.",
        engine_kind="paddle",
        language="ch",
        confidence_threshold=0.35,
        use_textline_orientation=True,
    ),
    OcrPreset(
        id="paddle_complex",
        label="PaddleOCR: сложная страница",
        description="Медленнее, но включает ориентацию строк, выравнивание и легкий upscale.",
        engine_kind="paddle",
        language="ch",
        confidence_threshold=0.25,
        use_doc_orientation_classify=True,
        use_doc_unwarping=True,
        use_textline_orientation=True,
        upscale_factor=1.5,
    ),
    OcrPreset(
        id="tesseract_basic",
        label="Tesseract: простой текст",
        description="Запасной режим для простого печатного текста, если Tesseract установлен.",
        engine_kind="pytesseract",
        language="eng+rus",
        confidence_threshold=35.0,
    ),
    OcrPreset(
        id="ai_vision_placeholder",
        label="ИИ vision: позже",
        description="Будущий режим для сложных случаев через мультимодальную модель.",
        engine_kind="placeholder",
    ),
    OcrPreset(
        id="hybrid_placeholder",
        label="Комбо: OCR + ИИ позже",
        description="Будущий гибрид: PaddleOCR сначала, ИИ проверяет сомнительные блоки.",
        engine_kind="placeholder",
    ),
)

OCR_PRESET_BY_ID = {preset.id: preset for preset in OCR_PRESETS}


def get_ocr_preset(preset_id: str) -> OcrPreset:
    return OCR_PRESET_BY_ID.get(preset_id, OCR_PRESET_BY_ID[DEFAULT_OCR_PRESET_ID])

