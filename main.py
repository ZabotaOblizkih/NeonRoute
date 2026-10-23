"""
NeonRoute - Desktop AI Orchestrator
Optimized for 8 GB VRAM (RTX 5050 Laptop / MSI Cyborg 15)
Main Entry Point
"""

import sys
import logging
import traceback
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from config import (
    BASE_DIR, EXPORTS_DIR, OUTPUTS_DIR, AUDIO_DIR, LOGS_DIR,
    CHARACTERS_DIR, APP_NAME, APP_VERSION
)
from ui.main_window import MainWindow


def global_exception_handler(exc_type, exc_value, exc_traceback):
    """
    Global exception interceptor: logs full traceback to file/console
    and displays an interactive error dialog to prevent silent app crashes.
    """
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logging.error(f"Unhandled exception:\n{err_msg}")

    # If QApplication instance is already running, show informative modal error dialog
    if QApplication.instance():
        try:
            msg_box = QMessageBox()
            msg_box.setIcon(QMessageBox.Icon.Critical)
            msg_box.setWindowTitle("Ошибка NeonRoute")
            msg_box.setText("Произошла непредвиденная ошибка в приложении:")
            msg_box.setInformativeText(str(exc_value))
            msg_box.setDetailedText(err_msg)
            msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg_box.exec()
        except Exception as dialog_err:
            logging.error(f"Failed to display error dialog: {dialog_err}")
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
    else:
        sys.__excepthook__(exc_type, exc_value, exc_traceback)


sys.excepthook = global_exception_handler


def setup_environment_and_logging():
    """Ensures directories exist, cleans up expired logs, and activates TimedRotatingFileHandler (7-day retention)."""
    import time
    from logging.handlers import TimedRotatingFileHandler

    for directory in [BASE_DIR, EXPORTS_DIR, OUTPUTS_DIR, AUDIO_DIR, LOGS_DIR, CHARACTERS_DIR]:
        directory.mkdir(parents=True, exist_ok=True)

    # 1. Startup cleanup: remove log archives older than 7 days (7 * 86400 sec)
    now = time.time()
    cutoff_seconds = 7 * 86400
    try:
        for p in LOGS_DIR.glob("*.log*"):
            if p.is_file() and (now - p.stat().st_mtime) > cutoff_seconds:
                try:
                    p.unlink()
                except Exception:
                    pass
    except Exception:
        pass

    # 2. Daily rotating log file with 7-day retention
    log_file = LOGS_DIR / "neonroute.log"
    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8"
    )

    handlers = [file_handler]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        handlers=handlers
    )

    try:
        from ui.log_console import setup_global_log_handler
        setup_global_log_handler()
    except Exception:
        pass

    logging.info(f"Initialized {APP_NAME} v{APP_VERSION} in {BASE_DIR}")


def main():
    setup_environment_and_logging()

    # Qt High DPI & Rendering configuration
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
