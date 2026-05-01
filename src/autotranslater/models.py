from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .constants import MIN_AREA_SIZE


@dataclass
class ScreenRect:
    x: int
    y: int
    width: int
    height: int


@dataclass
class TranslationArea:
    x: int = 120
    y: int = 80
    width: int = 920
    height: int = 1180

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "TranslationArea":
        return cls(
            x=int(data.get("x", 120)),
            y=int(data.get("y", 80)),
            width=int(data.get("width", 920)),
            height=int(data.get("height", 1180)),
        )


@dataclass
class OverlayStyle:
    bg_color: str = "#fff7dc"
    text_color: str = "#141414"
    border_color: str = "#111111"
    marker_color: str = "#ffcc33"
    alpha: float = 0.88
    font_family: str = "Arial"
    font_size: int = 18
    padding: int = 10

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "OverlayStyle":
        return cls(
            bg_color=str(data.get("bg_color", "#fff7dc")),
            text_color=str(data.get("text_color", "#141414")),
            border_color=str(data.get("border_color", "#111111")),
            marker_color=str(data.get("marker_color", "#ffcc33")),
            alpha=float(data.get("alpha", 0.88)),
            font_family=str(data.get("font_family", "Arial")),
            font_size=int(data.get("font_size", 18)),
            padding=int(data.get("padding", 10)),
        )


@dataclass
class AppSettings:
    area: TranslationArea
    style: OverlayStyle

    @classmethod
    def default(cls, screen: ScreenRect) -> "AppSettings":
        width = min(980, max(MIN_AREA_SIZE, screen.width - 240))
        height = min(1260, max(MIN_AREA_SIZE, screen.height - 160))
        return cls(
            area=TranslationArea(
                x=screen.x + (screen.width - width) // 2,
                y=screen.y + (screen.height - height) // 2,
                width=width,
                height=height,
            ),
            style=OverlayStyle(),
        )

    @classmethod
    def from_json(cls, data: dict[str, Any], screen: ScreenRect) -> "AppSettings":
        defaults = cls.default(screen)
        return cls(
            area=TranslationArea.from_json(data.get("area", asdict(defaults.area))),
            style=OverlayStyle.from_json(data.get("style", asdict(defaults.style))),
        )
