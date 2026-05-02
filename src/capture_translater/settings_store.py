from __future__ import annotations

import json
import logging
from dataclasses import asdict

from .constants import PROJECT_VERSION, SETTINGS_PATH
from .models import AppSettings, ScreenRect


logger = logging.getLogger(__name__)


def load_settings(screen: ScreenRect) -> AppSettings:
    if not SETTINGS_PATH.exists():
        logger.info("Settings file is missing; using defaults")
        return AppSettings.default(screen)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        settings = AppSettings.from_json(data, screen)
        logger.info("Settings loaded from %s", SETTINGS_PATH.resolve())
        return settings
    except Exception:
        logger.exception("Failed to load settings; using defaults")
        return AppSettings.default(screen)


def save_settings(settings: AppSettings) -> None:
    payload = {
        "version": PROJECT_VERSION,
        "area": asdict(settings.area),
        "style": asdict(settings.style),
        "ocr": asdict(settings.ocr),
    }
    SETTINGS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Settings saved to %s", SETTINGS_PATH.resolve())
