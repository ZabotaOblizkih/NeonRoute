"""
NeonRoute - Desktop AI Orchestrator
Optimized for 8 GB VRAM (RTX 5050 Laptop / MSI Cyborg 15)
Main Entry Point
"""

import sys
import logging
import traceback
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from config import (
    BASE_DIR, EXPORTS_DIR, OUTPUTS_DIR, AUDIO_DIR, LOGS_DIR,
    CHARACTERS_DIR, APP_NAME, APP_VERSION, SPLASH_PATH, APP_ICON_PATH
)
# NOTE: ui.main_window is intentionally NOT imported here at module level.
# It is deferred inside main() so the splash screen can show and animate
# during the heavy transitive import chain (~50+ modules).


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

    if sys.platform == "win32":
        import ctypes
        from config import APP_USER_MODEL_ID
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)

    # Qt High DPI & Rendering configuration
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    from qfluentwidgets import setTheme, Theme
    setTheme(Theme.DARK)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    if APP_ICON_PATH.exists():
        from PyQt6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))

    # --- Splash screen: visible immediately while MainWindow loads ---
    splash = None
    if SPLASH_PATH.exists():
        from ui.widgets.splash_screen import launch_isolated_splash
        splash = launch_isolated_splash(SPLASH_PATH)
        if hasattr(splash, "attach_import_pulse"):
            splash.attach_import_pulse()
        app.processEvents()

    def _boot_main_window() -> None:
        try:
            # Heavy transitive import of ~50+ modules happens here,
            # while the splash is visible and animating via import pulse.
            from ui.main_window import MainWindow

            if splash:
                splash.pump_animation()

            window = MainWindow()
            app._main_window = window  # keep strong reference, prevent GC

            def _on_startup_ready() -> None:
                window.showMaximized()
                if splash:
                    splash.fade_out_and_close(duration_ms=450)

            # Wait for all background milestones (knowledge_sync, memory_warmup, llm_server)
            window.startup_ready.connect(_on_startup_ready)
        except Exception:
            # If MainWindow construction crashes, ensure splash is dismissed
            if splash:
                splash.fade_out_and_close(duration_ms=450)
            raise

    # 15 ms delay gives the event loop one full paint cycle so the splash
    # frame is composited on-screen before the blocking import starts.
    QTimer.singleShot(15, _boot_main_window)
    sys.exit(app.exec())
    # --- End splash integration ---


if __name__ == "__main__":
    main()
