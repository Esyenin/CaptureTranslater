from __future__ import annotations

import logging
from typing import Protocol


logger = logging.getLogger(__name__)
BATCH_SEPARATOR = "\n<<<CAPTURE_TRANSLATER_BLOCK>>>\n"
MAX_TRANSLATION_CHARS = 3500


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

    def __init__(self) -> None:
        self.cache: dict[str, str] = {}

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
        translated = [""] * len(clean_texts)
        missing_texts: list[str] = []
        missing_indices: list[int] = []
        for index, text in enumerate(clean_texts):
            if not text:
                continue
            cached = self.cache.get(text)
            if cached is None:
                missing_indices.append(index)
                missing_texts.append(text)
            else:
                translated[index] = cached

        if not missing_texts:
            logger.info("Translation cache satisfied all %s blocks", len(clean_texts))
            return translated

        logger.info(
            "Translation cache hits=%s misses=%s",
            len(clean_texts) - len(missing_texts),
            len(missing_texts),
        )
        translator = GoogleTranslator(source="auto", target="ru")
        translated_missing = self.translate_in_chunks(translator, missing_texts)
        for index, source, target in zip(
            missing_indices,
            missing_texts,
            translated_missing,
            strict=True,
        ):
            translated[index] = target
            self.cache[source] = target
        return translated

    def translate_in_chunks(
        self,
        translator: object,
        texts: list[str],
    ) -> list[str]:
        translated = [""] * len(texts)
        chunk_indices: list[int] = []
        chunk_texts: list[str] = []
        chunk_size = 0

        for index, text in enumerate(texts):
            if not text:
                continue
            added_size = len(text)
            if chunk_texts:
                added_size += len(BATCH_SEPARATOR)
            if chunk_texts and chunk_size + added_size > MAX_TRANSLATION_CHARS:
                self.flush_chunk(translator, chunk_indices, chunk_texts, translated)
                chunk_indices = []
                chunk_texts = []
                chunk_size = 0

            chunk_indices.append(index)
            chunk_texts.append(text)
            chunk_size += added_size

        if chunk_texts:
            self.flush_chunk(translator, chunk_indices, chunk_texts, translated)
        return translated

    def flush_chunk(
        self,
        translator: object,
        indices: list[int],
        texts: list[str],
        translated: list[str],
    ) -> None:
        logger.debug(
            "Translating chunk with %s blocks and %s characters",
            len(texts),
            sum(len(text) for text in texts),
        )
        if len(texts) == 1:
            translated[indices[0]] = str(translator.translate(texts[0])).strip()
            return

        joined = BATCH_SEPARATOR.join(texts)
        combined = str(translator.translate(joined))
        parts = [part.strip() for part in combined.split(BATCH_SEPARATOR)]
        if len(parts) != len(texts):
            logger.warning(
                "Combined translation split mismatch: expected=%s actual=%s",
                len(texts),
                len(parts),
            )
            self.translate_individually(translator, indices, texts, translated)
            return

        for index, text in zip(indices, parts, strict=True):
            translated[index] = text

    def translate_individually(
        self,
        translator: object,
        indices: list[int],
        texts: list[str],
        translated: list[str],
    ) -> None:
        logger.info("Falling back to per-block translation for %s blocks", len(texts))
        for index, text in zip(indices, texts, strict=True):
            translated[index] = str(translator.translate(text)).strip()


def create_translation_engine() -> TranslationEngine:
    if DeepTranslatorEngine.available():
        return DeepTranslatorEngine()
    return UnavailableTranslator(
        "Переводчик не установлен. Установи пакет deep-translator.",
    )
