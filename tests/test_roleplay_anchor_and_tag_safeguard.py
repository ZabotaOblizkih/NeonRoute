import pytest
from unittest.mock import MagicMock
from services.tavern_models import CharacterCard
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.roleplay_flow_controller import RoleplayFlowControllerMixin

class DummyFlowController(RoleplayFlowControllerMixin):
    def __init__(self, character=None, session=None):
        self.active_character = character
        self.current_session = session or {"id": "sess_test", "is_dm_mode": False}
        self.chat_history = [
            {"role": "user", "content": "Привет, Система"}
        ]
        self._current_user_request = "Привет, Система"
        self.lm_combo = MagicMock()
        self.lm_combo.currentText.return_value = "default"
        self.lm_client = MagicMock()
        self.task_queue = MagicMock()
        self._active_tasks = {}
        self._turn_bubble = None

    def _sync_turn(self, **kwargs):
        pass

    def _append_message(self, *args, **kwargs):
        class DummyWidget:
            def set_text(self, t): pass
        return DummyWidget()

def test_unclosed_tags_safeguard_stream():
    controller = DummyFlowController()

    # 1. Broken <stats> with unclosed code block
    broken_stats = "Текст ответа...\n<stats>\n```json\n{\"hp\": 100}"
    # Run through _handle_roleplay_llm_response
    processed_texts = []
    original_process = None

    from services.roleplay_state_manager import RoleplayStateManager
    orig_process = RoleplayStateManager.process_model_turn

    def mock_process(sess_id, text):
        processed_texts.append(text)
        return orig_process(sess_id, text)

    RoleplayStateManager.process_model_turn = mock_process
    try:
        controller._handle_roleplay_llm_response(broken_stats)
    finally:
        RoleplayStateManager.process_model_turn = orig_process

    repaired = processed_texts[0]
    assert "</stats>" in repaired
    assert repaired.count("```") % 2 == 0

    # 2. Broken <meters>
    broken_meters = "Взгляд персонажа...\n<meters>\nСимпатия: 50/100"
    processed_meters = []
    def mock_process2(sess_id, text):
        processed_meters.append(text)
        return orig_process(sess_id, text)
    RoleplayStateManager.process_model_turn = mock_process2
    try:
        controller._handle_roleplay_llm_response(broken_meters)
    finally:
        RoleplayStateManager.process_model_turn = orig_process

    repaired_meters = processed_meters[0]
    assert "</meters>" in repaired_meters

    # 3. Broken <choices>
    broken_choices = "Что делать дальше?\n<choices>\n1. Идти вперед\n2. Отступить"
    processed_choices = []
    def mock_process3(sess_id, text):
        processed_choices.append(text)
        return orig_process(sess_id, text)
    RoleplayStateManager.process_model_turn = mock_process3
    try:
        controller._handle_roleplay_llm_response(broken_choices)
    finally:
        RoleplayStateManager.process_model_turn = orig_process

    repaired_choices = processed_choices[0]
    assert "</choices>" in repaired_choices

def test_depth_anchor_injection_in_flow():
    card = CharacterCard(name="Система", is_system=True)
    controller = DummyFlowController(character=card)

    controller._enqueue_roleplay_llm_task()

    # Verify task was enqueued
    assert controller.task_queue.enqueue.called
    task = controller.task_queue.enqueue.call_args[0][0]
    enqueued_messages = task.kwargs["messages"]

    # Last user message should contain depth anchor
    last_user_msg = None
    for m in reversed(enqueued_messages):
        if m.get("role") == "user":
            last_user_msg = m.get("content")
            break

    assert last_user_msg is not None
    assert "[Системная директива сцены:" in last_user_msg
    assert "<stats>```...```</stats>" in last_user_msg
