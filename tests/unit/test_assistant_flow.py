"""
Unit tests for AssistantFlowControllerMixin and services/assistant_flow.py.
Verifies dynamic Gallery Art Pass temperature resolution and task enqueuing.
"""
from unittest.mock import MagicMock
import pytest

from ui.controllers.assistant_flow_controller import AssistantFlowControllerMixin
from services.assistant_flow import resolve_gallery_art_temperature


class DummyAssistantFlow(AssistantFlowControllerMixin):
    """Headless dummy object to test AssistantFlowControllerMixin methods."""

    def __init__(self):
        self.lm_client = MagicMock()
        self.task_queue = MagicMock()
        self.current_session = {}
        self.selected_forge_checkpoint = None
        self._active_tasks = {}
        self.app_config = {}
        self.mode_profile = {"sampling": {"temperature": 0.70}}


def test_enqueue_gallery_art_pass_custom_session_temperature():
    """
    Проверка, что при session['sampling_params']['temperature'] = 0.85
    в вызов Gallery Art Pass (llm_kwargs) уходит temperature=0.85 и enable_thinking=False.
    """
    flow = DummyAssistantFlow()
    flow.current_session = {
        "id": "sess_test_1",
        "sampling_params": {"temperature": 0.85}
    }

    flow._enqueue_gallery_art_pass(user_query="нарисуй неоновый город")

    assert flow.task_queue.enqueue.called
    enqueued_task = flow.task_queue.enqueue.call_args[0][0]
    assert enqueued_task.name == "Gallery Art Pass"
    assert enqueued_task.kwargs["temperature"] == pytest.approx(0.85)
    assert enqueued_task.kwargs["enable_thinking"] is False


def test_enqueue_gallery_art_pass_empty_session_default_temperature():
    """
    Проверка, что при отсутствии кастомной температуры в сессии срабатывает
    временный per-request override temperature=0.4 и enable_thinking=False.
    """
    flow = DummyAssistantFlow()
    flow.current_session = {}
    flow.mode_profile = {}
    flow.app_config = {}

    flow._enqueue_gallery_art_pass(user_query="нарисуй киберпанк закат")

    assert flow.task_queue.enqueue.called
    enqueued_task = flow.task_queue.enqueue.call_args[0][0]
    assert enqueued_task.name == "Gallery Art Pass"
    assert enqueued_task.kwargs["temperature"] == pytest.approx(0.4)
    assert enqueued_task.kwargs["enable_thinking"] is False


def test_resolve_effective_thinking_logic():
    """Проверяет все ветви резолвера resolve_effective_thinking."""
    from services.assistant_flow import resolve_effective_thinking

    # 1. explicit_override
    assert resolve_effective_thinking("assistant", "qwen3.5", explicit_override=True) is True
    assert resolve_effective_thinking("engineer", "qwen3.5", explicit_override=False) is False

    # 2. active_skill == 'gallery'
    assert resolve_effective_thinking("engineer", "qwen3.5", active_skill="gallery") is False
    assert resolve_effective_thinking("assistant", "r1", active_skill="gallery") is False

    # 3. non-thinking model in auto mode returns None
    assert resolve_effective_thinking("assistant", "llama-3-8b", active_skill=None) is None

    # 4. thinking model in auto mode: True for engineer, False for assistant/roleplay
    assert resolve_effective_thinking("engineer", "Qwen3.5-35B", active_skill=None) is True
    assert resolve_effective_thinking("assistant", "Qwen3.5-35B", active_skill=None) is False
    assert resolve_effective_thinking("roleplay", "DeepSeek-R1-Distill", active_skill=None) is False



def test_resolve_gallery_art_temperature_pure():
    """Тестирование функции resolve_gallery_art_temperature с различными входными данными."""
    # 1. Приоритет сессии
    assert resolve_gallery_art_temperature(
        session={"sampling_params": {"temperature": 0.9}},
        profile={"sampling": {"temperature": 0.6}}
    ) == pytest.approx(0.9)

    # 2. Дефолт при отсутствии параметров
    assert resolve_gallery_art_temperature(None, None, None) == pytest.approx(0.8)
    assert resolve_gallery_art_temperature({}, None, {}) == pytest.approx(0.8)

    # 3. Невалидные значения
    assert resolve_gallery_art_temperature(
        session={"sampling_params": {"temperature": "bad"}}
    ) == pytest.approx(0.8)


def test_build_gallery_art_plan_passes_enable_thinking_false():
    """
    Проверка, что build_gallery_art_plan всегда передает enable_thinking=False
    в lm_client.chat_completion для исключения генерации <think> при выводе тегов.
    """
    from services.assistant_flow import build_gallery_art_plan

    mock_lm = MagicMock()
    mock_lm.chat_completion.return_value = "masterpiece, 1girl, cyberpunk city, neon glow"

    mock_ad = MagicMock()
    mock_ad.plan_from_visual_intent.return_value = MagicMock()

    plan = build_gallery_art_plan(
        user_query="нарисуй девушку",
        lm_client=mock_lm,
        art_director=mock_ad,
        session={"id": "test_sess_gallery"},
        profile={"sampling": {"temperature": 0.8}},
        available_models=[{"name": "cyberpunk_sdxl", "architecture": "sdxl", "description": "Cyberpunk"}],
    )

    assert mock_lm.chat_completion.called
    kwargs = mock_lm.chat_completion.call_args[1]
    assert kwargs.get("enable_thinking") is False
    assert kwargs.get("temperature") == pytest.approx(0.8)
    assert plan is not None
