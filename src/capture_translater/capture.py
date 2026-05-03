from __future__ import annotations

import logging
import time
from typing import Any

import mss
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .constants import MAX_PREVIEW_FPS
from .models import ScreenRect


logger = logging.getLogger(__name__)


class CaptureThread(QThread):
    frame_captured = Signal(QImage)
    capture_error = Signal(str)

    def __init__(self, screen: ScreenRect, fps: int) -> None:
        super().__init__()
        self.screen = screen
        self.fps = fps
        self.running = False
        self.frame_count = 0
        self.total_loop_seconds = 0.0

    def set_fps(self, fps: int) -> None:
        self.fps = max(1, min(MAX_PREVIEW_FPS, int(fps)))
        logger.info("Capture FPS changed to %s", self.fps)

    def stop(self) -> None:
        logger.info("Stopping capture thread")
        self.running = False
        self.wait(1500)

    def run(self) -> None:
        self.running = True
        self.frame_count = 0
        self.total_loop_seconds = 0.0
        logger.info(
            "Capture thread started for screen x=%s y=%s size=%sx%s at %s FPS",
            self.screen.x,
            self.screen.y,
            self.screen.width,
            self.screen.height,
            self.fps,
        )
        with mss.mss() as backend:
            while self.running:
                started = time.perf_counter()
                try:
                    frame = grab_screen_qimage(self.screen, backend)
                    if not frame.isNull():
                        self.frame_captured.emit(frame)
                        self.frame_count += 1
                        if self.frame_count % 120 == 0:
                            average_ms = (
                                self.total_loop_seconds / max(1, self.frame_count) * 1000
                            )
                            logger.debug(
                                "Captured %s preview frames; average capture loop %.1fms",
                                self.frame_count,
                                average_ms,
                            )
                except Exception as exc:  # noqa: BLE001 - thread boundary
                    logger.exception("Screen capture failed")
                    self.capture_error.emit(str(exc))
                elapsed = time.perf_counter() - started
                self.total_loop_seconds += elapsed
                interval = 1 / max(1, min(MAX_PREVIEW_FPS, self.fps))
                remaining = max(0.001, interval - elapsed)
                self.msleep(round(remaining * 1000))
        logger.info("Capture thread stopped after %s frames", self.frame_count)


def grab_screen_qimage(
    screen: ScreenRect,
    backend: Any | None = None,
    diagnostic_label: str | None = None,
) -> QImage:
    if backend is None:
        backend_started = time.perf_counter()
        with mss.mss() as local_backend:
            if diagnostic_label:
                logger.info(
                    "[%s] MSS backend opened in %.3fs",
                    diagnostic_label,
                    time.perf_counter() - backend_started,
                )
            return grab_screen_qimage(screen, local_backend, diagnostic_label)

    monitor = {
        "left": screen.x,
        "top": screen.y,
        "width": screen.width,
        "height": screen.height,
    }
    started = time.perf_counter()
    raw = backend.grab(monitor)
    grabbed_at = time.perf_counter()
    image = QImage(
        raw.bgra,
        raw.width,
        raw.height,
        raw.width * 4,
        QImage.Format.Format_RGB32,
    ).copy()
    finished_at = time.perf_counter()
    if diagnostic_label:
        logger.info(
            "[%s] Screen capture x=%s y=%s size=%sx%s: mss=%.3fs qimage_copy=%.3fs "
            "total=%.3fs",
            diagnostic_label,
            screen.x,
            screen.y,
            screen.width,
            screen.height,
            grabbed_at - started,
            finished_at - grabbed_at,
            finished_at - started,
        )
    return image
