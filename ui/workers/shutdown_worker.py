import os
import time
import logging
import threading
from typing import TYPE_CHECKING
from PyQt6.QtCore import QThread
from utils.config_manager import ConfigManager

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger("NeonRoute.ShutdownWorker")


class ShutdownWorker(QThread):
    """
    Background worker thread that runs ProcessManager.shutdown() and state persistence
    to prevent GUI event loop freeze on application exit.
    """
    def __init__(self, main_window: 'MainWindow'):
        super().__init__()
        self.main_window = main_window

    def run(self):
        # Аварийный сторожевой таймер на 8.0 сек
        def _watchdog():
            time.sleep(8.0)
            logger.info("Shutdown watchdog triggered. Forcing exit.")
            os._exit(0)
        t = threading.Thread(target=_watchdog, daemon=True)
        t.start()

        # 1. Сохранение активной сессии и конфигурации
        try:
            self.main_window._save_current_session()
            ConfigManager.save_config(self.main_window.app_config)
        except Exception as e:
            logger.error(f"Error saving session/config on close: {e}")

        # 1.5. Оптимизация векторной памяти LanceDB (сжатие фрагментов и удаление .txn)
        try:
            from services.memory_db import memory_db
            memory_db.close()
        except Exception as e:
            logger.debug(f"Error optimizing LanceDB on shutdown: {e}")

        # 2. Остановка рабочих потоков с таймаутом
        try:
            self.main_window.health_worker.stop()
            self.main_window.system_monitor.stop()
            self.main_window.task_queue.stop()

            self.main_window.health_worker.wait(1000)
            self.main_window.system_monitor.wait(1000)
            self.main_window.task_queue.wait(1000)
        except Exception as e:
            logger.debug(f"Error terminating background threads: {e}")

        # 3. Выгрузка VRAM и серверов
        try:
            self.main_window.process_manager.shutdown_all()
        except Exception as e:
            logger.error(f"Error in process_manager.shutdown_all: {e}")
