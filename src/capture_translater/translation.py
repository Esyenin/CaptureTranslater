from __future__ import annotations

import logging
from typing import Protocol


logger = logging.getLogger(__name__)


class TranslationEngine(Protocol):
    name: str

    def translate_batch(self, texts: list[str]) -> list[str]:
        ...


class TranslationUnavailable(RuntimeError):
    pass


class IdentityTranslator:
    name = "source text"

    def translate_batch(self, texts: list[str]) -> list[str]:
        logger.info("Translation fallback returned source text for %s blocks", len(texts))
        return texts


class UnavailableTranslator:
    name = "translator unavailable"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def translate_batch(self, texts: list[str]) -> list[str]:
        raise TranslationUnavailable(self.reason)


class DeepTranslatorEngine:
    name = "deep-translator: Google -> ru"

    @classmethod
    def available(cls) -> bool:
        try:
            import deep_translator  # noqa: F401
        except ImportError:
            logger.info("deep-translator is not installed")
            return False
        logger.info("deep-translator import is available")
        return True

    def translate_batch(self, texts: list[str]) -> list[str]:
        from deep_translator import GoogleTranslator

        clean_texts = [text.strip() for text in texts]
        logger.info("Translating %s OCR text blocks to Russian", len(clean_texts))
        translator = GoogleTranslator(source="auto", target="ru")
        if hasattr(translator, "translate_batch"):
            return [str(text) for text in translator.translate_batch(clean_texts)]
        return [str(translator.translate(text)) for text in clean_texts]


def create_translation_engine() -> TranslationEngine:
    if DeepTranslatorEngine.available():
        return DeepTranslatorEngine()
    return UnavailableTranslator(
        "Переводчик не установлен. Установи пакет deep-translator.",
    )

