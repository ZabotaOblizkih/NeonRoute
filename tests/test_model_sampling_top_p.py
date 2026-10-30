import sys
import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication

from config import MODE_PROFILES
from ui.dialogs.model_sampling_dialog import ModelSamplingDialog
from ui.controllers.assistant_flow_controller import AssistantFlowControllerMixin


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_mode_profiles_top_p_ssot():
    """MODE_PROFILES must define top_p for assistant, roleplay, and engineer modes."""
    assert "top_p" in MODE_PROFILES["assistant"]["sampling"]
    assert MODE_PROFILES["assistant"]["sampling"]["top_p"] == 0.95

    assert "top_p" in MODE_PROFILES["roleplay"]["sampling"]
    assert MODE_PROFILES["roleplay"]["sampling"]["top_p"] == 0.95

    assert "top_p" in MODE_PROFILES["engineer"]["sampling"]
    assert MODE_PROFILES["engineer"]["sampling"]["top_p"] == 1.00


def test_model_sampling_dialog_top_p_widgets_and_ranges(qapp):
    """ModelSamplingDialog must create top_p slider and spinbox with correct bounds and tooltips."""
    dlg = ModelSamplingDialog(current_params={}, mode="assistant", is_engineer_mode=False)

    assert hasattr(dlg, "spin_top_p")
    assert hasattr(dlg, "slider_top_p")
    assert hasattr(dlg, "lbl_top_p")

    # DoubleSpinBox specs: 0.10 - 1.00, step 0.01, decimals 2
    assert dlg.spin_top_p.minimum() == 0.10
    assert dlg.spin_top_p.maximum() == 1.00
    assert dlg.spin_top_p.singleStep() == 0.01
    assert dlg.spin_top_p.decimals() == 2

    # Slider specs: 10 - 100, singleStep 1, pageStep 5
    assert dlg.slider_top_p.minimum() == 10
    assert dlg.slider_top_p.maximum() == 100
    assert dlg.slider_top_p.singleStep() == 1
    assert dlg.slider_top_p.pageStep() == 5

    # Label text & tooltip
    assert "Top-P" in dlg.lbl_top_p.text()
    assert "хвост" in dlg.spin_top_p.toolTip()


def test_model_sampling_dialog_top_p_sync(qapp):
    """Slider and SpinBox changes should synchronously update each other."""
    dlg = ModelSamplingDialog(current_params={"top_p": 0.95}, mode="assistant")

    assert dlg.spin_top_p.value() == 0.95
    assert dlg.slider_top_p.value() == 95

    # Change via spinbox
    dlg.spin_top_p.setValue(0.78)
    assert dlg.slider_top_p.value() == 78

    # Change via slider
    dlg.slider_top_p.setValue(82)
    assert dlg.spin_top_p.value() == 0.82

    params = dlg.get_sampling_params()
    assert params["top_p"] == 0.82


def test_model_sampling_dialog_engineer_mode(qapp):
    """Engineer mode should default top_p to 1.00 and leave widgets enabled."""
    dlg = ModelSamplingDialog(current_params={}, mode="assistant", is_engineer_mode=True)

    assert dlg.spin_top_p.value() == 1.00
    assert dlg.slider_top_p.value() == 100
    assert dlg.spin_top_p.isEnabled() is True
    assert dlg.slider_top_p.isEnabled() is True

    # Temperature and DRY should be locked for engineer, but top_p remains unlocked
    assert dlg.spin_temp.isEnabled() is False
    assert dlg.spin_dry.isEnabled() is False


def test_model_sampling_dialog_reset_defaults(qapp):
    """_reset_defaults resets top_p to 0.95 for assistant, and 1.00 for engineer."""
    dlg_asst = ModelSamplingDialog(current_params={"top_p": 0.70}, mode="assistant")
    assert dlg_asst.spin_top_p.value() == 0.70
    dlg_asst._reset_defaults()
    assert dlg_asst.spin_top_p.value() == 0.95
    assert dlg_asst.slider_top_p.value() == 95
    assert dlg_asst.get_sampling_params()["top_p"] == 0.95

    dlg_eng = ModelSamplingDialog(current_params={"top_p": 0.80}, mode="assistant", is_engineer_mode=True)
    assert dlg_eng.spin_top_p.value() == 0.80
    dlg_eng._reset_defaults()
    assert dlg_eng.spin_top_p.value() == 1.00
    assert dlg_eng.slider_top_p.value() == 100
    assert dlg_eng.get_sampling_params()["top_p"] == 1.00


class DummyAssistantHost(AssistantFlowControllerMixin):
    def __init__(self):
        super().__init__()
        self.app_config = {}
        self.current_session = {}
        self.chat_history = [{"role": "user", "content": "Привет"}]
        self.lm_combo = MagicMock()
        self.lm_combo.currentText.return_value = "test-model"
        self.lm_client = MagicMock()
        self.task_queue = MagicMock()
        self._active_tasks = {}
        self.btn_engine_mode = MagicMock()
        self.btn_engine_mode.isChecked.return_value = False
        self.active_role = None


def test_assistant_flow_controller_top_p_propagation():
    """Assistant flow controller must extract top_p and pass it into llm_kwargs."""
    host = DummyAssistantHost()

    # 1. Global / profile default (0.95)
    host.current_session = {"id": "sess_default", "sampling_params": {}, "is_custom_sampling": False}
    host._enqueue_assistant_llm_task()

    assert host.task_queue.enqueue.called
    task = host.task_queue.enqueue.call_args[0][0]
    assert task.kwargs["top_p"] == 0.95

    # 2. Custom session override
    host.task_queue.enqueue.reset_mock()
    host.current_session = {
        "id": "sess_custom",
        "is_custom_sampling": True,
        "sampling_params": {
            "temperature": 0.8,
            "top_p": 0.88
        }
    }
    host._enqueue_assistant_llm_task()
    assert host.task_queue.enqueue.called
    task2 = host.task_queue.enqueue.call_args[0][0]
    assert task2.kwargs["top_p"] == 0.88
