from __future__ import annotations

import json
from dataclasses import asdict

from .constants import PROJECT_VERSION, SETTINGS_PATH
from .models import AppSettings, ScreenRect


def load_settings(screen: ScreenRect) -> AppSettings:
    if not SETTINGS_PATH.exists():
        return AppSettings.default(screen)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return AppSettings.from_json(data, screen)
    except Exception:
        return AppSettings.default(screen)


def save_settings(settings: AppSettings) -> None:
    payload = {
        "version": PROJECT_VERSION,
        "area": asdict(settings.area),
        "style": asdict(settings.style),
    }
    SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
