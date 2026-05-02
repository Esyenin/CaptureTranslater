from __future__ import annotations

import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .constants import LOG_DIR, LOG_FILE_PREFIX


def configure_logging() -> Path:
    """Configure one shared logger tree for UI, capture, OCR and overlay events."""
    root = logging.getLogger()
    if getattr(configure_logging, "_configured", False):
        return get_current_log_path()

    log_path = build_log_path()
    configure_logging._current_log_path = log_path

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=1_500_000,
        backupCount=4,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    # Keep the terminal quiet; the rotating file receives the detailed trace.
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)

    root.addHandler(file_handler)
    root.addHandler(console_handler)
    configure_logging._configured = True
    logging.getLogger(__name__).info("Logging configured: %s", log_path.resolve())
    return log_path


def build_log_path() -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return LOG_DIR / f"{LOG_FILE_PREFIX}_{timestamp}.log"


def get_current_log_path() -> Path:
    return configure_logging._current_log_path or build_log_path()


configure_logging._configured = False
configure_logging._current_log_path = None
