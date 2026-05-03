from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QFontDatabase

from .constants import CUSTOM_FONT_EXTENSIONS


logger = logging.getLogger(__name__)
DEFAULT_READABLE_FONT = "Segoe UI"
TEXT_UNFRIENDLY_FONT_KEYWORDS = (
    "fluent icons",
    "mdl2 assets",
    "wingdings",
    "webdings",
    "marlett",
    "symbol",
)


@dataclass(frozen=True)
class FontLoadResult:
    path: Path
    families: list[str]


class FontRegistry:
    """Keeps custom font loading idempotent while settings are edited and saved."""

    def __init__(self) -> None:
        self.loaded_paths: set[Path] = set()
        self.loaded_families: dict[Path, list[str]] = {}

    def load_paths(self, paths: list[str]) -> list[FontLoadResult]:
        results: list[FontLoadResult] = []
        for raw_path in paths:
            result = self.add_font_file(Path(raw_path))
            if result is not None:
                results.append(result)
        return results

    def add_font_file(self, path: Path) -> FontLoadResult | None:
        normalized = path.expanduser().resolve()
        if normalized in self.loaded_paths:
            logger.debug("Custom font already loaded: %s", normalized)
            return FontLoadResult(
                path=normalized,
                families=self.loaded_families.get(normalized, []),
            )
        if not normalized.exists():
            logger.warning("Custom font file does not exist: %s", normalized)
            return None
        if normalized.suffix.lower() not in CUSTOM_FONT_EXTENSIONS:
            logger.warning("Unsupported font extension for %s", normalized)
            return None

        font_id = QFontDatabase.addApplicationFont(str(normalized))
        if font_id < 0:
            logger.warning("Qt rejected custom font: %s", normalized)
            return None

        self.loaded_paths.add(normalized)
        families = list(QFontDatabase.applicationFontFamilies(font_id))
        self.loaded_families[normalized] = families
        logger.info("Loaded custom font %s with families: %s", normalized, families)
        return FontLoadResult(path=normalized, families=families)


def unique_font_paths(paths: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        normalized = str(Path(raw_path).expanduser().resolve())
        if normalized not in seen:
            unique.append(normalized)
            seen.add(normalized)
    return unique


@lru_cache(maxsize=128)
def readable_font_family(family: str) -> str:
    """Avoid icon-only system fonts that make translated text look invisible."""
    normalized = family.casefold()
    if not normalized:
        return DEFAULT_READABLE_FONT
    if any(keyword in normalized for keyword in TEXT_UNFRIENDLY_FONT_KEYWORDS):
        logger.info(
            "Font %s is icon/symbol-oriented; using %s",
            family,
            DEFAULT_READABLE_FONT,
        )
        return DEFAULT_READABLE_FONT
    return family
