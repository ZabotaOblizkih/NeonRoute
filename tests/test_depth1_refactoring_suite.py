import pytest
from unittest.mock import MagicMock, patch
from services.tavern_models import CharacterCard
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.roleplay_flow_controller import RoleplayFlowControllerMixin
from services.context_window_builder import ContextWindowBuilder
from services.session_manager import SessionManager

class MockFlowController(RoleplayFlowControllerMixin):
    def __init__(self, character=None, session=None):
        self.active_character = character
        self.current_session = session or {"id": "test_sess_01", "is_dm_mode": False}
        self.chat_history = [
            {"role": "user", "content": "Привет, как поживаешь?"}
        ]
        self._current_user_request = "Привет, как поживаешь?"
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


@patch.object(SessionManager, "get_active_user_persona", return_value={"name": "Игрок"})
def test_depth_1_system_anchor_solo(mock_persona):
    card = CharacterCard(name="Селеста")
    controller = MockFlowController(character=card)
    controller._enqueue_roleplay_llm_task()

    assert controller.task_queue.enqueue.called
    task = controller.task_queue.enqueue.call_args[0][0]
    msgs = task.kwargs["messages"]

    # 1. Final message is pure user text
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "Привет, как поживаешь?"

    # 2. Depth-1 system anchor immediately precedes user turn
    assert msgs[-2]["role"] == "system"
    expected_anchor = (
        "[Стиль сцены: кинематографичное повествование от лица Селеста. Отреагируй на слова, действия или присутствие Игрок, "
        "передай мимику, жесты и подтекст. Строго запрещено действовать, мыслить или говорить за Игрок. "
        "Заверши ход репликой или действием Селеста, требующим реакции Игрок.]"
    )
    assert msgs[-2]["content"] == expected_anchor
    # Ensure no auto-art prompt directive
    assert "[IMAGE_PROMPT:" not in msgs[-1]["content"]
    assert "[IMAGE_PROMPT:" not in msgs[-2]["content"]


@patch.object(SessionManager, "get_active_user_persona", return_value={"name": "Игрок"})
def test_depth_1_system_anchor_group(mock_persona):
    card = CharacterCard(name="Отряд", is_group=True)
    controller = MockFlowController(character=card)
    controller._enqueue_roleplay_llm_task()

    task = controller.task_queue.enqueue.call_args[0][0]
    msgs = task.kwargs["messages"]

    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "Привет, как поживаешь?"

    assert msgs[-2]["role"] == "system"
    expected_anchor = (
        "[Стиль сцены: кинематографичное повествование группы спутников. Отреагируй на слова и действия Игрок. "
        "Выдели одного спутника как инициатора ответа, остальных покажи краткой фоновой реакцией. "
        "Строго запрещено действовать, мыслить или говорить за Игрок. "
        "Заверши сцену прямым обращением к Игрок.]"
    )
    assert msgs[-2]["content"] == expected_anchor


@patch.object(SessionManager, "get_active_user_persona", return_value={"name": "Игрок"})
def test_depth_1_system_anchor_dm_cyoa(mock_persona):
    card = CharacterCard(name="Мастер", is_dm=True)
    controller = MockFlowController(character=card)
    controller._enqueue_roleplay_llm_task()

    task = controller.task_queue.enqueue.call_args[0][0]
    msgs = task.kwargs["messages"]

    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "Привет, как поживаешь?"

    assert msgs[-2]["role"] == "system"
    expected_anchor = (
        "[Стиль сцены: интерактивное повествование мира. Отреагируй на решение и действия Игрок. "
        "Опиши последствия для мира в 3-м лице. Запрещено действовать за Игрок. "
        "В финале предложи варианты действий внутри <choices>...</choices>.]"
    )
    assert msgs[-2]["content"] == expected_anchor


def test_custom_depth_anchor_preserved_intact():
    custom_text = "[Кастомный стиль: мрачный киберпанк. Реагируй шепотом.]"
    card = CharacterCard(name="Селеста")
    session = {"id": "sess_custom", "custom_depth_anchor": f"  {custom_text}  \n\n"}
    controller = MockFlowController(character=card, session=session)
    controller._enqueue_roleplay_llm_task()

    task = controller.task_queue.enqueue.call_args[0][0]
    msgs = task.kwargs["messages"]

    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "Привет, как поживаешь?"

    assert msgs[-2]["role"] == "system"
    assert msgs[-2]["content"] == custom_text
    # Check build_depth_anchor directly
    res = RoleplayPromptBuilder.build_depth_anchor(card, user_name="Игрок", custom_anchor=f"  {custom_text} \n")
    assert res == custom_text


def test_context_window_builder_leaves_user_message_pure():
    card = CharacterCard(name="Селеста", depth_prompt="Заметка автора")
    history = [
        {"role": "user", "content": "Привет, Селеста!"}
    ]
    messages = ContextWindowBuilder.build_context_messages(
        history=history,
        is_roleplay=True,
        character_card=card,
        user_name="Игрок"
    )
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "Привет, Селеста!"
