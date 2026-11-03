import pytest
from unittest.mock import MagicMock

from ui.controllers.assistant_flow_controller import AssistantFlowControllerMixin


class FakeTurnBubble:
    """Эмулятор UI-баббла для проверки сигналов finalize_thought, set_thought_data и текстового контракта."""
    def __init__(self):
        self.finalize_thought_called = False
        self.last_duration = None
        self.last_payload = None
        self.text_content = ""
        self.thought_data = None

    def finalize_thought(self, duration_sec: float = 0.0, token_count=0):
        self.finalize_thought_called = True
        self.last_duration = duration_sec
        # При спасении мыслей санитайзер передает th_body вторым аргументом
        self.last_payload = token_count

    def set_thought_data(self, content: str = "", duration: float = 0.0, token_count: int = 0):
        self.thought_data = {
            "content": content,
            "duration": duration,
            "token_count": token_count
        }

    def get_text(self):
        return self.text_content


class DummySanitizerHost(AssistantFlowControllerMixin):
    """Изолированный хост контроллера без привязки к циклу событий Qt."""
    def __init__(self):
        self.is_busy = True
        self._turn_bubble = FakeTurnBubble()
        self._stream_flush_timer = None
        self._is_running_gallery_art_pass = False
        self.current_session = {"id": "sess_test_sanitizer", "messages": []}
        self.chat_history = []
        self._active_tasks = {}

        # Моки синхронизации и отрисовки сообщений
        self._sync_turn = MagicMock()
        self._append_message = MagicMock(return_value=self._turn_bubble)

        # Настраиваем мок потокового парсера для эмуляции замера времени
        self._thought_stream_parser = MagicMock()
        self._thought_stream_parser.duration = 3.50
        self._thought_stream_parser.token_count = 0
        self._thought_stream_parser.normal_text = ""
        self._thought_stream_parser.finalize.return_value = []
        self._thought_stream_parser.get_thought_data.return_value = None


@pytest.fixture
def host():
    return DummySanitizerHost()


class TestSanitizerCoreLogic:
    """Проверка чистой логики извлечения мыслей, очистки текста и защиты от опустошения."""

    def test_rescue_analyze_tag_and_clean_text(self, host):
        """Проверяет спасение тега <analyze> и удаление его из тела реплики."""
        raw_response = (
            "<analyze>\n"
            "— Интент: Оценка архитектуры NeonRoute\n"
            "— Опорные сущности: LayerBudgeter, LanceDB\n"
            "</analyze>\n"
            "Архитектура системы полностью готова к масштабированию."
        )

        host._handle_assistant_llm_response(raw_response)

        host._sync_turn.assert_called_once()
        _, kwargs = host._sync_turn.call_args

        saved_content = kwargs.get("content")
        saved_thought = kwargs.get("thought")

        assert "<analyze>" not in saved_content
        assert "</analyze>" not in saved_content
        assert saved_content.strip() == "Архитектура системы полностью готова к масштабированию."

        assert saved_thought is not None
        assert "— Интент: Оценка архитектуры NeonRoute" in saved_thought["content"]
        assert saved_thought["tag"] == "analyze"
        assert saved_thought["duration"] == 3.50

    def test_unclosed_reasoning_tag_graceful_rescue(self, host):
        """Проверяет обработку незакрытого тега <thought>, оборвавшегося по лимиту токенов."""
        raw_response = "<thought>\nГенерация рассуждений внезапно оборвалась"

        host._handle_assistant_llm_response(raw_response)

        host._sync_turn.assert_called_once()
        _, kwargs = host._sync_turn.call_args

        saved_content = kwargs.get("content")
        saved_thought = kwargs.get("thought")

        assert "Генерация рассуждений внезапно оборвалась" in saved_content
        assert saved_thought is not None
        assert saved_thought["tag"] == "thought"

    def test_empty_text_guard_prevents_blank_message(self, host):
        """Проверяет, что если весь ответ был внутри тега, текст восстанавливается из мыслей."""
        raw_response = "<reasoning>Только инженерный анализ без внешнего текста</reasoning>"

        host._handle_assistant_llm_response(raw_response)

        host._sync_turn.assert_called_once()
        _, kwargs = host._sync_turn.call_args

        saved_content = kwargs.get("content")
        assert saved_content == "Только инженерный анализ без внешнего текста"


class TestSanitizerSignalsAndTurnSync:
    """Проверка проброса сигналов в UI-баббл и контракта вызова _sync_turn."""

    def test_ui_bubble_finalize_thought_called_on_rescue(self, host):
        """Проверяет, что баббл получает сигнал finalize_thought со спасательным телом мыслей."""
        raw_response = "<analyze>Спасенные мысли анализа</analyze>Финальный результат"
        bubble = host._turn_bubble

        host._handle_assistant_llm_response(raw_response)

        assert bubble.finalize_thought_called is True
        assert bubble.last_payload == "Спасенные мысли анализа"
        assert bubble.last_duration == 3.50

    def test_sync_turn_contract_parameters(self, host):
        """Проверяет точный контракт аргументов сохранения хода диалога в SessionManager."""
        raw_response = "<think>Базовый CoT</think>Ответ пользователю"

        host._handle_assistant_llm_response(raw_response)

        host._sync_turn.assert_called_once()
        _, kwargs = host._sync_turn.call_args

        assert kwargs.get("role") == "assistant"
        assert kwargs.get("replace_last") is True
        assert kwargs.get("content") == "Ответ пользователю"
        assert kwargs.get("thought")["content"] == "Базовый CoT"
        assert kwargs.get("thought")["tag"] == "think"
