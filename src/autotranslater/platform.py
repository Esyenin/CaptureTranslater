from __future__ import annotations

import ctypes

from PySide6.QtWidgets import QApplication

from .models import ScreenRect


def get_virtual_screen_rect() -> ScreenRect:
    try:
        user32 = ctypes.windll.user32
        x = user32.GetSystemMetrics(76)
        y = user32.GetSystemMetrics(77)
        width = user32.GetSystemMetrics(78)
        height = user32.GetSystemMetrics(79)
        if width > 0 and height > 0:
            return ScreenRect(x, y, width, height)
    except Exception:  # noqa: BLE001 - platform fallback
        pass

    app_screen = QApplication.primaryScreen()
    geometry = app_screen.geometry()
    return ScreenRect(geometry.x(), geometry.y(), geometry.width(), geometry.height())
