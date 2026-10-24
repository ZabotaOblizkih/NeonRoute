import sys
from unittest.mock import MagicMock
import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from services.process_manager import ServerState
from ui.components.status_bar_widget import MainStatusBar


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def test_status_bar_headless_labels(qapp):
    """Проверяет UI-тексты и тултипы в MainStatusBar для автономного сервера."""
    class DummyWindow(QWidget):
        def _toggle_lms_server(self):
            pass

    win = DummyWindow()
    status_bar = MainStatusBar(win)

    assert status_bar.btn_lms_toggle.text() == "⚡ Старт LLM"
    assert "llama-server.exe" in status_bar.btn_lms_toggle.toolTip()
    assert status_bar.pill_lm.port == 1234


def test_hardware_engine_server_state_reaction(qapp):
    """Проверяет реакцию индикатора pill_lm и кнопки btn_lms_toggle на смену ServerState."""
    from ui.controllers.hardware_engine import HardwareEngineMixin

    class DummyWindow(HardwareEngineMixin):
        def __init__(self):
            self.pill_lm = MagicMock()
            self.btn_lms_toggle = MagicMock()
            self.process_manager = MagicMock()

    win = DummyWindow()

    # 1. State: READY
    win._on_server_state_changed("starting", ServerState.READY.value)
    win.pill_lm.set_status.assert_called_with(True, "llama-server Online")
    win.btn_lms_toggle.setText.assert_called_with("⚡ Стоп LLM")

    # 2. State: STARTING
    win._on_server_state_changed("stopped", ServerState.STARTING.value)
    win.pill_lm.set_status.assert_called_with(False, "Запуск (STARTING)...")
    win.btn_lms_toggle.setText.assert_called_with("⚡ Стоп LLM")

    # 3. State: EVICTED_FOR_FORGE
    win._on_server_state_changed("ready", ServerState.EVICTED_FOR_FORGE.value)
    win.pill_lm.set_status.assert_called_with(False, "Выгружен для Forge")
    win.btn_lms_toggle.setText.assert_called_with("⚡ Старт LLM")

    # 4. State: STOPPED
    win._on_server_state_changed("ready", ServerState.STOPPED.value)
    win.pill_lm.set_status.assert_called_with(False, "Offline")
    win.btn_lms_toggle.setText.assert_called_with("⚡ Старт LLM")
