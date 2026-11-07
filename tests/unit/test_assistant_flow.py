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
    в вызов Gallery Art Pass (llm_kwargs) уходит temperature=0.85.
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
    assert enqueued_task.kwargs["temperature"] != 0.40


def test_enqueue_gallery_art_pass_empty_session_default_temperature():
    """
    Проверка, что при пустой сессии выставляется безопасный дефолт 0.8
    (или дефолт профиля, если сессия пуста и нет кастомных настроек).
    """
    flow = DummyAssistantFlow()
    flow.current_session = {}
    flow.mode_profile = {}
    flow.app_config = {}

    flow._enqueue_gallery_art_pass(user_query="нарисуй киберпанк закат")

    assert flow.task_queue.enqueue.called
    enqueued_task = flow.task_queue.enqueue.call_args[0][0]
    assert enqueued_task.name == "Gallery Art Pass"
    assert enqueued_task.kwargs["temperature"] == pytest.approx(0.8)
    assert enqueued_task.kwargs["temperature"] != 0.40


def test_enqueue_gallery_art_pass_profile_sampling_fallback():
    """
    Проверка, что если в сессии нет temperature, но в mode_profile задано 0.75,
    подхватывается температура профиля 0.75.
    """
    flow = DummyAssistantFlow()
    flow.current_session = {}
    flow.mode_profile = {"sampling": {"temperature": 0.75}}

    flow._enqueue_gallery_art_pass(user_query="нарисуй космический корабль")

    assert flow.task_queue.enqueue.called
    enqueued_task = flow.task_queue.enqueue.call_args[0][0]
    assert enqueued_task.kwargs["temperature"] == pytest.approx(0.75)


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
