import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Добавляем корень проекта в путь поиска модулей
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from services.thought_stream_parser import ThoughtStreamParser
from ui.controllers.assistant_flow_controller import AssistantFlowControllerMixin
from ui.controllers.chat_flow_controller import ChatFlowControllerMixin


class DummyHost(AssistantFlowControllerMixin, ChatFlowControllerMixin):
    """Тестовый каркас, объединяющий миксины главного окна."""

    def __init__(self):
        self.is_busy = False
        self._is_switching_session = False
        self._active_tasks = {}
        self._turn_bubble = None
        self._thought_stream_parser = None
        self.typing_loader = None
        self._stream_token_buffer = ""
        self.current_mode = "assistant"
        self.current_session = {"sampling_params": {}, "is_custom_sampling": False}
        self.app_config = {}

        # Моки сервисов и таймеров
        self.task_queue = MagicMock()
        self.lm_client = MagicMock()
        self._stream_flush_timer = MagicMock()
        self._stream_flush_timer.isActive.return_value = False

    def _get_current_avatar_path(self):
        return ""

    def _append_message(self, sender, text, is_user=False, avatar_path=""):
        mock_bubble = MagicMock()
        mock_bubble.get_or_create_thought_spoiler = MagicMock()
        mock_bubble.append_thought_chunk = MagicMock()
        mock_bubble.finalize_thought = MagicMock()
        return mock_bubble


def test_token_streamed_dropped_when_not_busy():
    """Проверка регрессии: при is_busy = False токены должны блокироваться."""
    host = DummyHost()
    host.is_busy = False
    host._thought_stream_parser = ThoughtStreamParser()

    # Посылаем токен, когда контроллер считает себя свободным
    host._on_token_streamed("<think>")

    # Токен отброшен guard-условием: баббл не создан, парсер чист
    assert host._turn_bubble is None
    assert host._thought_stream_parser.thought_text == ""


def test_token_streamed_routes_to_parser_when_busy():
    """Проверка прохождения токенов в парсер рассуждений при установленном is_busy."""
    host = DummyHost()
    host.is_busy = True
    host._thought_stream_parser = ThoughtStreamParser()

    # 1. Посылаем открывающий тег рассуждений
    host._on_token_streamed("<think>")
    assert host._turn_bubble is not None
    host._turn_bubble.get_or_create_thought_spoiler.assert_called_once()

    # 2. Посылаем чанк мыслей
    host._on_token_streamed("Сверяю МАЗ-457041")
    host._turn_bubble.append_thought_chunk.assert_called_with("Сверяю МАЗ-457041")

    # 3. Закрываем блок мыслей
    host._on_token_streamed("</think>")
    host._turn_bubble.finalize_thought.assert_called_once()

    # 4. Посылаем чистовой ответ
    host._on_token_streamed("По регламенту завода:")
    assert "По регламенту завода:" in host._stream_token_buffer


def test_assistant_lifecycle_is_busy_and_timer():
    """Проверка жизненного цикла: сброс флага is_busy и остановка таймера при завершении генерации."""
    host = DummyHost()
    host.is_busy = True
    host._stream_flush_timer.isActive.return_value = True
    host._thought_stream_parser = ThoughtStreamParser()
    host._turn_bubble = MagicMock()

    # Имитируем получение финального ответа модели
    host._handle_assistant_llm_response("<think>тест</think>Готово.")

    # Флаг занятости обязан быть снят, а таймер остановлен
    assert host.is_busy is False
    host._stream_flush_timer.stop.assert_called_once()
    assert host._thought_stream_parser is None


if __name__ == "__main__":
    print("Запуск тестов сигнального конвейера стриминга...")
    test_token_streamed_dropped_when_not_busy()
    print("✅ test_token_streamed_dropped_when_not_busy пройден.")
    test_token_streamed_routes_to_parser_when_busy()
    print("✅ test_token_streamed_routes_to_parser_when_busy пройден.")
    test_assistant_lifecycle_is_busy_and_timer()
    print("✅ test_assistant_lifecycle_is_busy_and_timer пройден.")
    print("\nВсе тесты сигналов и жизненного цикла выполнены успешно!")