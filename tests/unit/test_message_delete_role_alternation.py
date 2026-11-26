"""
Unit tests for message deletion history synchronization and role alternation normalization.
Covers the bug where deleting a user message in Assistant mode pulled the UI welcome message
into chat_history[0] as 'assistant', causing HTTP 400 'Conversation roles must alternate'.
"""
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QMessageBox

from services.context_window_builder import ContextWindowBuilder
from services.tavern_parser import CharacterCard
from ui.builders.chat_bubble_builder import ChatBubbleBuilderMixin


class _DummyChatWindow(ChatBubbleBuilderMixin):
    """Minimal headless harness for ChatBubbleBuilderMixin._on_message_delete_requested."""

    def __init__(self, session_data, mode="assistant"):
        self.current_session = session_data
        self.current_mode = mode
        self.chat_history = list(session_data.get("chat_history", []))
        self.chat_layout = MagicMock()
        self.chat_layout.indexOf.return_value = -1
        self._status = MagicMock()
        self.saved_count = 0

    def statusBar(self):
        return self._status

    def _save_current_session(self):
        self.saved_count += 1

    def _remove_message_widget(self, widget):
        pass


def _make_widget(msg_id: str, is_user: bool, text: str = ""):
    w = MagicMock()
    w.message_id = msg_id
    w.is_user = is_user
    w.original_text = text
    w.raw_text = text
    return w


def test_delete_only_user_turn_in_assistant_mode_excludes_welcome():
    session = {
        "id": "sess_del_1",
        "mode": "assistant",
        "messages": [
            {"id": "m_welcome", "sender": "Элария", "text": "Привет. С чего начнём?", "is_user": False, "is_error": False},
            {"id": "m_u1", "sender": "Вы", "text": "Нарисуй закат", "is_user": True, "is_error": False},
            {"id": "m_a1", "sender": "Элария", "text": "Готово!", "is_user": False, "is_error": False},
        ],
        "chat_history": [
            {"role": "user", "content": "Нарисуй закат"},
            {"role": "assistant", "content": "Готово!"},
        ],
    }
    win = _DummyChatWindow(session, mode="assistant")
    widget_u1 = _make_widget("m_u1", is_user=True, text="Нарисуй закат")

    with patch("ui.builders.chat_bubble_builder.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
        win._on_message_delete_requested(widget_u1)

    assert len(win.current_session["messages"]) == 1
    assert win.current_session["messages"][0]["id"] == "m_welcome"
    assert win.chat_history == []
    assert win.current_session["chat_history"] == []

    # Subsequent user turn produces strictly valid [system, user]
    assembled = ContextWindowBuilder.get_lm_studio_messages(
        history=win.chat_history + [{"role": "user", "content": "Нарисуй улыбающуюся девушку"}],
        is_roleplay=False,
        include_memory=False,
    )
    assert [m["role"] for m in assembled] == ["system", "user"]


def test_delete_first_of_two_turns_in_assistant_mode_keeps_user_first():
    session = {
        "id": "sess_del_2",
        "mode": "assistant",
        "messages": [
            {"id": "m_welcome", "sender": "Элария", "text": "Привет, Виктор.", "is_user": False, "is_error": False},
            {"id": "m_u1", "sender": "Вы", "text": "Вопрос 1", "is_user": True, "is_error": False},
            {"id": "m_a1", "sender": "Элария", "text": "Ответ 1", "is_user": False, "is_error": False},
            {"id": "m_u2", "sender": "Вы", "text": "Вопрос 2", "is_user": True, "is_error": False},
            {"id": "m_a2", "sender": "Элария", "text": "Ответ 2", "is_user": False, "is_error": False},
        ],
        "chat_history": [],
    }
    win = _DummyChatWindow(session, mode="assistant")
    widget_u1 = _make_widget("m_u1", is_user=True, text="Вопрос 1")

    with patch("ui.builders.chat_bubble_builder.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
        win._on_message_delete_requested(widget_u1)

    assert [m["role"] for m in win.chat_history] == ["user", "assistant"]
    assert win.chat_history[0]["content"] == "Вопрос 2"
    assert win.chat_history[1]["content"] == "Ответ 2"


def test_delete_merges_consecutive_same_role_and_preserves_thought():
    session = {
        "id": "sess_del_3",
        "mode": "assistant",
        "messages": [
            {"id": "m_welcome", "sender": "Элария", "text": "Привет.", "is_user": False, "is_error": False},
            {"id": "m_u1", "sender": "Вы", "text": "Первый вопрос", "is_user": True, "is_error": False},
            {"id": "m_a1_err", "sender": "Система", "text": "Ошибка", "is_user": False, "is_error": True},
            {"id": "m_u2", "sender": "Вы", "text": "Уточнение", "is_user": True, "is_error": False},
            {
                "id": "m_a2",
                "sender": "Элария",
                "text": "Итоговый ответ",
                "is_user": False,
                "is_error": False,
                "thought": {"content": "Проверка шагов", "duration": 1.2, "token_count": 5},
            },
            {"id": "m_u3", "sender": "Вы", "text": "Лишний вопрос", "is_user": True, "is_error": False},
            {"id": "m_a3", "sender": "Элария", "text": "Лишний ответ", "is_user": False, "is_error": False},
        ],
        "chat_history": [],
    }
    win = _DummyChatWindow(session, mode="assistant")
    widget_u3 = _make_widget("m_u3", is_user=True, text="Лишний вопрос")

    with patch("ui.builders.chat_bubble_builder.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
        win._on_message_delete_requested(widget_u3)

    assert [m["role"] for m in win.chat_history] == ["user", "assistant"]
    assert "Первый вопрос" in win.chat_history[0]["content"]
    assert "Уточнение" in win.chat_history[0]["content"]
    assert win.chat_history[1]["thought"]["content"] == "Проверка шагов"

    # 1. Engineer mode: <think> reconstructed and preserved for the last assistant turn (Sliding CoT)
    assembled_eng = ContextWindowBuilder.get_lm_studio_messages(
        history=win.chat_history + [{"role": "user", "content": "Дальше"}],
        is_roleplay=False,
        is_engineer=True,
        mode="engineer",
        include_memory=False,
        enable_reasoning=True,
    )
    assert [m["role"] for m in assembled_eng] == ["system", "user", "assistant", "user"]
    assert "<think>" in assembled_eng[2]["content"]
    assert "Проверка шагов" in assembled_eng[2]["content"]

    # 2. Assistant mode: <think> is strictly stripped from LLM history
    assembled_asst = ContextWindowBuilder.get_lm_studio_messages(
        history=win.chat_history + [{"role": "user", "content": "Дальше"}],
        is_roleplay=False,
        mode="assistant",
        include_memory=False,
        enable_reasoning=True,
    )
    assert [m["role"] for m in assembled_asst] == ["system", "user", "assistant", "user"]
    assert "<think>" not in assembled_asst[2]["content"]
    assert "Проверка шагов" not in assembled_asst[2]["content"]
    assert assembled_asst[2]["content"] == "Итоговый ответ"

    # 3. Jinja Alternation Guard: when assistant content becomes empty after thought stripping
    thought_only_history = [
        {"role": "user", "content": "Вопрос"},
        {"role": "assistant", "content": "<think>Размышления без финального ответа</think>", "thought": {"content": "Размышления без финального ответа"}},
        {"role": "user", "content": "Следующий вопрос"}
    ]
    assembled_guard = ContextWindowBuilder.get_lm_studio_messages(
        history=thought_only_history,
        mode="assistant",
        include_memory=False,
        enable_reasoning=True,
    )
    assert [m["role"] for m in assembled_guard] == ["system", "user", "assistant", "user"]
    assert assembled_guard[2]["content"] == "*(Анализ завершен)*"


def test_context_window_builder_normalizes_assistant_and_preserves_roleplay_anchor():
    # Assistant mode: leading assistant and consecutive user/vision list must normalize cleanly
    raw_assistant_history = [
        {"role": "assistant", "content": "Старое приветствие"},
        {"role": "user", "content": "Первая часть"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Что на фото?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            ],
        },
    ]
    assembled_assistant = ContextWindowBuilder.get_lm_studio_messages(
        history=raw_assistant_history,
        is_roleplay=False,
        include_memory=False,
        vision_content=raw_assistant_history[-1]["content"],
    )
    assert [m["role"] for m in assembled_assistant] == ["system", "user"]
    assert isinstance(assembled_assistant[1]["content"], list)
    assert "Первая часть" in assembled_assistant[1]["content"][0]["text"]
    assert "Что на фото?" in assembled_assistant[1]["content"][0]["text"]

    # Roleplay mode: canonical first_mes assistant anchor at index 1 must be preserved
    card = CharacterCard(name="Каэли", first_mes="Привет, путник!")
    rp_history = [
        {"role": "assistant", "content": "Привет, путник!"},
        {"role": "user", "content": "Здравствуй!"},
    ]
    assembled_rp = ContextWindowBuilder.get_lm_studio_messages(
        history=rp_history,
        is_roleplay=True,
        character_card=card,
        user_name="Иван",
        max_context_tokens=24000,
    )
    assert assembled_rp[0]["role"] == "system"
    assert assembled_rp[1]["role"] == "assistant"
    assert assembled_rp[2]["role"] == "user"


def test_roleplay_strict_template_sanitization_via_lm_studio(monkeypatch):
    from services.lm_studio import LMStudioClient

    LMStudioClient._instance = None
    client = LMStudioClient()
    client._is_model_unloaded = False
    client._current_loaded_ctx = 16384
    client._current_loaded_gpu = "1.0"
    captured_payloads = []

    def mock_post(url, *args, **kwargs):
        captured_payloads.append(kwargs.get("json", {}))
        resp = MagicMock(status_code=200)
        resp.iter_lines.return_value = [
            b'data: {"choices": [{"delta": {"content": "*\xd0\xa1\xd0\xb5\xd1\x80\xd0\xb0\xd1\x84\xd0\xb8\xd0\xbd\xd0\xb0 \xd1\x83\xd0\xbb\xd1\x8b\xd0\xb1\xd0\xb0\xd0\xb5\xd1\x82\xd1\x81\xd1\x8f.*"}}]}',
            b"data: [DONE]",
        ]
        return resp

    monkeypatch.setattr(client, "is_model_loaded", lambda m: True)
    monkeypatch.setattr(client, "check_health", lambda: (True, "Online"))
    monkeypatch.setattr(client._persistent_session, "post", mock_post)

    rp_messages = [
        {"role": "system", "content": "Ты — Серафина."},
        {"role": "assistant", "content": "*Серафина стоит у окна.*"},
        {"role": "system", "content": "[Стиль сцены: кинематографичное повествование.]"},
        {"role": "user", "content": "Привет, Серафина!"},
    ]

    result = client.chat_completion(
        messages=rp_messages,
        model="MN-Violet-Lotus-12B.Q3_K_M.gguf",
        is_roleplay=True,
        stream=True,
        emit_signal=False,
    )
    assert "Серафина" in result
    assert len(captured_payloads) == 1
    sent = captured_payloads[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[0]["content"] == "Ты — Серафина."
    assert sent[1]["content"] == "[Начало сцены]"
    assert sent[2]["content"] == "*Серафина стоит у окна.*"
    assert "[Стиль сцены: кинематографичное повествование.]" in sent[3]["content"]
    assert "Привет, Серафина!" in sent[3]["content"]


def test_strict_template_sanitization_no_op_when_valid_and_strips_assistant_in_non_rp():
    valid = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "how are you?"},
    ]
    assert ContextWindowBuilder.sanitize_messages_for_strict_template(valid) is valid

    stale_assistant = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "Привет. С чего начнём?"},
        {"role": "user", "content": "Помоги с кодом"},
    ]
    cleaned = ContextWindowBuilder.sanitize_messages_for_strict_template(
        stale_assistant,
        prepend_scene_start_for_assistant=False,
    )
    assert [m["role"] for m in cleaned] == ["system", "user"]
    assert cleaned[1]["content"] == "Помоги с кодом"

