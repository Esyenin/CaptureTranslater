from __future__ import annotations

from pathlib import Path


APP_NAME = "CaptureTranslater"
PROJECT_VERSION = 7
SETTINGS_PATH = Path("capture_translater.settings.json")
LOG_DIR = Path("logs")
LOG_FILE_PREFIX = "capture_translater"

MIN_AREA_SIZE = 80
DEFAULT_PREVIEW_FPS = 30
MAX_PREVIEW_FPS = 30
FPS_CHOICES = ("1", "5", "10", "15", "30")

HANDLE_RADIUS = 8
EDGE_HIT_RADIUS = 8
MAX_PREVIEW_ZOOM = 6.0

PREVIEW_BACKGROUND = "#151515"
CUSTOM_FONT_EXTENSIONS = {".ttf", ".otf", ".ttc"}
