import pytest
import sys
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication

from ui.controllers.chat_flow_controller import ChatFlowControllerMixin
from ui.dialogs.user_settings_dialog import UserSettingsDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class DummyHost(ChatFlowControllerMixin):
    def __init__(self):
        super().__init__()
        self.app_config = {}
        self.current_mode = "assistant"
        self.current_session = {}
        self.chat_history = [{"role": "user", "content": "Привет"}]
        self.lm_combo = MagicMock()
        self.lm_combo.currentText.return_value = "test-model"
        self.lm_client = MagicMock()
        self.task_queue = MagicMock()
        self._active_tasks = {}
        self._pre_routed_art_guidance = None
        self._current_user_request = "Привет"
        self.btn_engine_mode = MagicMock()
        self.btn_engine_mode.isChecked.return_value = True  # force engineer mode to bypass sub-delegates


def test_user_settings_dialog_signal_declaration():
    assert hasattr(UserSettingsDialog, "settings_applied"), "UserSettingsDialog must declare settings_applied signal"


def test_user_settings_dialog_save_emits_settings_applied(qapp):
    with patch("ui.dialogs.user_settings_dialog.SessionManager.load_user_profiles", return_value={}), \
         patch("ui.dialogs.user_settings_dialog.SessionManager.save_user_profiles"), \
         patch("ui.dialogs.user_settings_dialog.ConfigManager.load_config") as mock_load, \
         patch("ui.dialogs.user_settings_dialog.ConfigManager.save_config"):
        
        mock_cfg = {"active_model": "test_model", "user_name": "Тестер", "auto_generate_art": False}
        mock_load.return_value = mock_cfg

        dlg = UserSettingsDialog()
        emitted_payload = []
        dlg.settings_applied.connect(lambda cfg: emitted_payload.append(cfg))

        dlg._save_all_profiles()

        assert len(emitted_payload) == 1
        assert emitted_payload[0] == mock_cfg


def test_chat_flow_controller_update_config():
    host = DummyHost()
    new_cfg = {
        "llm_sampling": {
            "temperature": 0.65,
            "min_p": 0.08,
            "top_p": 0.92,
            "max_tokens": 2048
        }
    }
    host.update_config(new_cfg)
    assert host.app_config == new_cfg


def test_chat_flow_controller_sampling_priority():
    host = DummyHost()
    host.app_config = {
        "llm_sampling": {
            "temperature": 0.40,
            "min_p": 0.07,
            "top_p": 0.85,
            "max_tokens": 1200
        }
    }
    # 1. Without session overrides, global_sampling is effective
    host._enqueue_llm_task(is_roleplay=False)
    assert host.task_queue.enqueue.called
    task = host.task_queue.enqueue.call_args[0][0]
    llm_kwargs = task.kwargs
    assert llm_kwargs["min_p"] == 0.07
    assert llm_kwargs["top_p"] == 0.85
    assert llm_kwargs["max_tokens"] == 1200

    # 2. With session sampling_overrides, session overrides global
    host.task_queue.enqueue.reset_mock()
    host.current_session = {
        "sampling_overrides": {
            "temperature": 0.88,
            "max_tokens": 3000
        }
    }
    host._enqueue_llm_task(is_roleplay=False)
    task2 = host.task_queue.enqueue.call_args[0][0]
    llm_kwargs2 = task2.kwargs
    assert llm_kwargs2["max_tokens"] == 3000
    assert llm_kwargs2["min_p"] == 0.07
    assert llm_kwargs2["top_p"] == 0.85


def test_main_window_settings_applied_handling(qapp):
    from ui.main_window import MainWindow
    with patch.object(MainWindow, "__init__", lambda self: None):
        win = MainWindow()
        win.chat_controller = MagicMock()
        win.lm_combo = MagicMock()
        win.lm_combo.findText.return_value = -1

        new_cfg = {"active_model": "model-xyz", "llm_sampling": {"temperature": 0.5}}
        win._on_settings_applied(new_cfg)

        assert win.app_config == new_cfg
        win.chat_controller.update_config.assert_called_once_with(new_cfg)
        win.lm_combo.addItem.assert_called_once_with("model-xyz")
