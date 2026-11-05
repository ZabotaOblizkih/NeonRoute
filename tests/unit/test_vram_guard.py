from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from ui.controllers.hardware_engine import HardwareEngineMixin
from services.vram_arbiter import VRAMArbiter


@pytest.fixture(scope="session")
def qapp():
    """Headless QApplication fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class DummyHardwareHost(QWidget, HardwareEngineMixin):
    def __init__(self):
        super().__init__()
        self._status_bar_mock = MagicMock()
        self.process_manager = MagicMock()
        self.forge_client = MagicMock()
        self.task_queue = MagicMock()
        self.pill_forge = MagicMock()
        self._reload_active_lms_model_async = MagicMock()
        self._cancel_lms_reload = MagicMock()

    def statusBar(self):
        return self._status_bar_mock


def test_scene_art_skips_when_forge_offline(qapp):
    """
    Проверяет, что при неактивном Forge (is_forge_running=False, is_server_ready=False):
    1. VRAMArbiter.release_llm_for_diffusion НЕ вызывается (нет паразитной выгрузки LLM из VRAM).
    2. _reload_active_lms_model_async НЕ вызывается вхолостую.
    3. Задача не добавляется в очередь TaskQueue.
    """
    host = DummyHardwareHost()
    host.process_manager.is_forge_running.return_value = False
    host.forge_client.is_server_ready.return_value = False
    host.pill_forge.is_online = False

    with patch.object(VRAMArbiter, "release_llm_for_diffusion") as mock_release:
        # 1. Проверка через _on_scene_art_requested
        host._on_scene_art_requested(text="Тестовая сцена")
        mock_release.assert_not_called()
        host._reload_active_lms_model_async.assert_not_called()
        host.task_queue.enqueue.assert_not_called()

        # 2. Проверка через алиас _generate_scene_art_async
        host._generate_scene_art_async(text="Тестовая сцена")
        mock_release.assert_not_called()
        host._reload_active_lms_model_async.assert_not_called()
        host.task_queue.enqueue.assert_not_called()

        # 3. Проверка прямого вызова _scene_art_pipeline (функция рабочего потока)
        result = host._scene_art_pipeline(scene_text="Тестовая сцена")
        mock_release.assert_not_called()
        host._reload_active_lms_model_async.assert_not_called()
        assert result.get("skipped") is True
        assert result.get("image_path") is None


def test_manual_and_direct_image_task_skips_when_forge_offline(qapp):
    """
    Проверяет, что ручная и директ генерация арта также защищены от VRAM Thrashing.
    """
    host = DummyHardwareHost()
    host.process_manager.is_forge_running.return_value = False
    host.forge_client.is_server_ready.return_value = False

    with patch.object(VRAMArbiter, "release_llm_for_diffusion") as mock_release:
        # Ручная генерация (!art)
        host._enqueue_image_task("Портрет персонажа")
        mock_release.assert_not_called()
        host._reload_active_lms_model_async.assert_not_called()
        host.task_queue.enqueue.assert_not_called()

        # Директ-генерация
        host._enqueue_direct_image_task("Пейзаж киберпанк")
        mock_release.assert_not_called()
        host._reload_active_lms_model_async.assert_not_called()
        host.task_queue.enqueue.assert_not_called()
