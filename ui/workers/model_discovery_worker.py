import logging
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger("NeonRoute.ModelDiscoveryWorker")


class ModelDiscoveryWorker(QThread):
    """
    Фоновый рабочий поток обнаружения моделей LM Studio и чекпоинтов Forge.
    Исключает синхронные вызовы CLI и дискового I/O в GUI-потоке при старте.
    """
    lms_models_ready = pyqtSignal(list)
    forge_models_ready = pyqtSignal(list)

    def __init__(self, process_manager, lm_client, forge_client):
        super().__init__()
        self.process_manager = process_manager
        self.lm_client = lm_client
        self.forge_client = forge_client

    def run(self):
        # 1. Фоновое обнаружение моделей LM Studio
        try:
            cli_models = self.process_manager.get_available_lms_models()
            api_models = self.lm_client.get_models()
            all_lms = []
            for m in cli_models + api_models:
                if m and m not in all_lms:
                    all_lms.append(m)
            self.lms_models_ready.emit(all_lms)
        except Exception as e:
            logger.debug(f"Фоновое сканирование моделей LMS: {e}")
            self.lms_models_ready.emit([])

        # 2. Фоновое обнаружение чекпоинтов Forge
        try:
            items = ["✨ Арт-директор (Авто)"]
            models = self.forge_client.get_models()
            if models:
                for m in models:
                    t = m.get("title", m.get("model_name", "checkpoint"))
                    if t and t not in items:
                        items.append(t)
            else:
                installed = self.forge_client.get_installed_checkpoints()
                for ckpt in installed:
                    if ckpt and ckpt not in items:
                        items.append(ckpt)
            self.forge_models_ready.emit(items)
        except Exception as e:
            logger.debug(f"Фоновое сканирование чекпоинтов Forge: {e}")
            self.forge_models_ready.emit(["✨ Арт-директор (Авто)"])
