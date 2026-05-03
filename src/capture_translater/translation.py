from __future__ import annotations

import logging
import time
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

        started = time.perf_counter()
        clean_texts = [text.strip() for text in texts]
        total_chars = sum(len(text) for text in clean_texts)
        logger.info(
            "Translating %s OCR text blocks to Russian; chars=%s cache_items=%s",
            len(clean_texts),
            total_chars,
            len(self.cache),
        )
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
            logger.info(
                "Translation cache satisfied all %s blocks in %.3fs",
                len(clean_texts),
                time.perf_counter() - started,
            )
            return translated

        logger.info(
            "Translation cache hits=%s misses=%s miss_chars=%s",
            len(clean_texts) - len(missing_texts),
            len(missing_texts),
            sum(len(text) for text in missing_texts),
        )
        translator_started = time.perf_counter()
        translator = GoogleTranslator(source="auto", target="ru")
        logger.info(
            "GoogleTranslator object created in %.3fs",
            time.perf_counter() - translator_started,
        )
        translated_missing = self.translate_in_chunks(translator, missing_texts)
        for index, source, target in zip(
            missing_indices,
            missing_texts,
            translated_missing,
            strict=True,
        ):
            translated[index] = target
            self.cache[source] = target
        logger.info(
            "Translation batch finished in %.3fs; cache_items=%s",
            time.perf_counter() - started,
            len(self.cache),
        )
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
        chunk_number = 0

        for index, text in enumerate(texts):
            if not text:
                continue
            added_size = len(text)
            if chunk_texts:
                added_size += len(BATCH_SEPARATOR)
            if chunk_texts and chunk_size + added_size > MAX_TRANSLATION_CHARS:
                chunk_number += 1
                self.flush_chunk(
                    translator,
                    chunk_indices,
                    chunk_texts,
                    translated,
                    chunk_number,
                )
                chunk_indices = []
                chunk_texts = []
                chunk_size = 0

            chunk_indices.append(index)
            chunk_texts.append(text)
            chunk_size += added_size

        if chunk_texts:
            chunk_number += 1
            self.flush_chunk(
                translator,
                chunk_indices,
                chunk_texts,
                translated,
                chunk_number,
            )
        logger.info("Translation split into %s network chunk(s)", chunk_number)
        return translated

    def flush_chunk(
        self,
        translator: object,
        indices: list[int],
        texts: list[str],
        translated: list[str],
        chunk_number: int,
    ) -> None:
        started = time.perf_counter()
        char_count = sum(len(text) for text in texts)
        logger.info(
            "Translating chunk #%s with %s blocks and %s characters",
            chunk_number,
            len(texts),
            char_count,
        )
        if len(texts) == 1:
            translated[indices[0]] = str(translator.translate(texts[0])).strip()
            logger.info(
                "Translation chunk #%s finished as single request in %.3fs",
                chunk_number,
                time.perf_counter() - started,
            )
            return

        joined = BATCH_SEPARATOR.join(texts)
        combined = str(translator.translate(joined))
        translated_at = time.perf_counter()
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
        logger.info(
            "Translation chunk #%s finished in %.3fs; split_parse=%.3fs",
            chunk_number,
            time.perf_counter() - started,
            time.perf_counter() - translated_at,
        )

    def translate_individually(
        self,
        translator: object,
        indices: list[int],
        texts: list[str],
        translated: list[str],
    ) -> None:
        logger.info("Falling back to per-block translation for %s blocks", len(texts))
        for item_number, (index, text) in enumerate(zip(indices, texts, strict=True), 1):
            started = time.perf_counter()
            translated[index] = str(translator.translate(text)).strip()
            logger.info(
                "Per-block translation #%s finished in %.3fs chars=%s",
                item_number,
                time.perf_counter() - started,
                len(text),
            )


def create_translation_engine() -> TranslationEngine:
    if DeepTranslatorEngine.available():
        return DeepTranslatorEngine()
    return UnavailableTranslator(
        "Переводчик не установлен. Установи пакет deep-translator.",
    )
