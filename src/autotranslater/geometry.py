from __future__ import annotations

from .constants import MIN_AREA_SIZE
from .models import ScreenRect, TranslationArea


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def clamp_area_to_screen(area: TranslationArea, screen: ScreenRect) -> TranslationArea:
    width = max(MIN_AREA_SIZE, min(area.width, screen.width))
    height = max(MIN_AREA_SIZE, min(area.height, screen.height))
    max_x = screen.x + screen.width - width
    max_y = screen.y + screen.height - height
    return TranslationArea(
        x=round(clamp(area.x, screen.x, max_x)),
        y=round(clamp(area.y, screen.y, max_y)),
        width=round(width),
        height=round(height),
    )
