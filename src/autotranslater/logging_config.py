from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .constants import LOG_PATH


def configure_logging() -> None:
    """Configure one shared logger tree for UI, capture, OCR and overlay events."""
    root = logging.getLogger()
    if getattr(configure_logging, "_configured", False):
        return

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    root.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_PATH,
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
    logging.getLogger(__name__).info("Logging configured: %s", LOG_PATH.resolve())


configure_logging._configured = False
