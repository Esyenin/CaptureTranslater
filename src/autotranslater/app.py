from __future__ import annotations

import logging

from PySide6.QtWidgets import QApplication

from .logging_config import configure_logging
from .main_window import MainWindow


def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("Application startup")
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
    logger.info("Application shutdown")


if __name__ == "__main__":
    main()
