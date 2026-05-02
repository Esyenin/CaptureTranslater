from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TranslationBox:
    id: str
    x: int
    y: int
    width: int
    height: int
    source_text: str
    translated_text: str
    hidden: bool = False

