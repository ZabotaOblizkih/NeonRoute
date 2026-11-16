"""
Регрессионные тесты персистентности истории сессии после инструментального цикла
и fallback-перехвата кода (ChatFlowControllerMixin / Engineer Mode).
"""
import pytest
from unittest.mock import MagicMock
from pathlib import Path

import config
import services.session_manager as sm_mod
import services.sandbox_manager as sbm_mod
from services.session_manager import SessionManager
from services.sandbox_manager import SandboxManager
from services.context_window_builder import ContextWindowBuilder
from ui.controllers.chat_flow_controller import ChatFlowControllerMixin


class DummyEngineerFlowHost(ChatFlowControllerMixin):
    """Минимальный изолированный тестовый хост для проверки инженерного цикла."""
    def __init__(self, session_dict: dict):
        super().__init__()
        self.current_session = session_dict
        self.current_mode = "engineer"
        self._engineering_mode_active = True
        self.chat_history = []
        self._turn_bubble = MagicMock()
        self._is_post_tool_synthesis = False
        self._synthesis_first_chunk = False
        self._tool_loop_count = 0
        self._script_executed_this_turn = False
        self._tool_executed_this_turn = False
        self.active_character = MagicMock(name="Элария")
        self.active_character.name = "Элария"

    def is_engineer_mode_active(self) -> bool:
        return True

    def get_effective_mode(self) -> str:
        return "engineer"

    def _enqueue_llm_task(self, *args, **kwargs):
        """Заглушка планировщика задач LLM для изоляции теста от LM Studio."""
        pass

    def _update_message_attachment(self, *args, **kwargs):
        pass

    def _execute_workspace_script(self, *args, **kwargs):
        pass


@pytest.fixture
def isolated_engineer_env(tmp_path, monkeypatch):
    """Изолирует директории истории сессий и песочницы во временную папку tmp_path."""
    test_hist_dir = tmp_path / "history"
    test_hist_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "HISTORY_DIR", test_hist_dir)
    monkeypatch.setattr(sm_mod, "HISTORY_DIR", test_hist_dir)

    test_ws_dir = tmp_path / "workspace"
    test_ws_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "WORKSPACE_DIR", test_ws_dir)
    if hasattr(sbm_mod, "WORKSPACE_DIR"):
        monkeypatch.setattr(sbm_mod, "WORKSPACE_DIR", test_ws_dir)

    SessionManager._sessions_cache.clear()
    yield tmp_path
    SessionManager._sessions_cache.clear()
    SandboxManager.set_active_session_id(None)


def test_post_tool_synthesis_persistence_and_no_duplicates(isolated_engineer_env):
    """
    Сценарий 1: Синтез после выполнения инструмента через ToolEngine (_is_post_tool_synthesis = True).
    Проверяет:
      1. Вызов реального _handle_engineering_llm_flow.
      2. Ответ попадает и в chat_history, и в current_session["messages"].
      3. Отсутствие дублирования записей в ОЗУ.
      4. Персистентность на диске после SessionManager.load_session.
      5. Корректность сборки контекста LLM на следующем ходе.
    """
    sess = SessionManager.create_session(mode="engineer", title="Тест Синтеза")
    sess["type"] = "engineering"
    SandboxManager.set_active_session_id(sess["id"])
    host = DummyEngineerFlowHost(sess)

    # 1. Запрос пользователя
    user_prompt = "Создай скрипт test_app.py"
    host.chat_history.append({"role": "user", "content": user_prompt})
    host.current_session["messages"].append({
        "id": "msg_u1",
        "role": "user",
        "sender": "Вы",
        "text": user_prompt,
        "is_user": True
    })
    SessionManager.save_session(host.current_session)

    # 2. Плейсхолдер ответа ассистента со стриминга
    host.current_session["messages"].append({
        "id": "msg_a1",
        "role": "assistant",
        "sender": "Элария",
        "text": "",
        "is_user": False
    })
    SessionManager.save_session(host.current_session)

    # 3. Выполнение инструмента -> _on_tool_execution_finished
    tool_result = {"tool_name": "write_file", "result": "Created test_app.py", "success": True}
    host._on_tool_execution_finished(tool_result)
    assert host._is_post_tool_synthesis is True

    # 4. Реальный вызов _handle_engineering_llm_flow с финальным синтезом
    final_synthetic_answer = "✅ Скрипт test_app.py успешно создан в рабочей песочнице."
    host._handle_engineering_llm_flow(final_synthetic_answer)

    # Проверка ОЗУ: отсутствие дублей
    # В chat_history должно быть ровно 3 сообщения: [user, observation, assistant]
    assert len(host.chat_history) == 3
    assert host.chat_history[0]["role"] == "user"
    assert "[Tool Observation:" in host.chat_history[1]["content"]
    assert host.chat_history[2]["role"] == "assistant"
    assert host.chat_history[2]["content"] == final_synthetic_answer

    # В current_session["messages"] должно быть ровно 2 сообщения: [user, assistant] (плейсхолдер обновлен)
    assert len(host.current_session["messages"]) == 2
    assert host.current_session["messages"][1]["text"] == final_synthetic_answer

    # 5. Проверка после перезагрузки с диска
    SessionManager._sessions_cache.clear()
    reloaded = SessionManager.load_session(sess["id"])
    assert reloaded is not None

    persisted_ast = [m for m in reloaded["messages"] if not m.get("is_user")]
    assert len(persisted_ast) == 1
    assert persisted_ast[0]["text"] == final_synthetic_answer

    assert len(reloaded["chat_history"]) == 3
    assert reloaded["chat_history"][-1]["content"] == final_synthetic_answer

    # 6. Проверка сборки контекста на следующем ходе
    next_query = "Теперь проверь его синтаксис."
    host.chat_history.append({"role": "user", "content": next_query})
    messages_for_llm = ContextWindowBuilder.get_lm_studio_messages(
        history=host.chat_history,
        is_engineer=True,
        mode="engineer"
    )
    roles = [m["role"] for m in messages_for_llm]
    assert roles == ["system", "user", "assistant", "user"]
    assert final_synthetic_answer in messages_for_llm[2]["content"]


def test_fallback_code_block_persistence_with_streaming_placeholder(isolated_engineer_env):
    """
    Сценарий 2: Fallback-перехват чистого блока кода со стриминговым плейсхолдером в messages.
    Проверяет:
      1. Запись файла в песочницу.
      2. Обновление существующего плейсхолдера в messages через _sync_turn.
      3. Персистентность на диске без дублирования сообщений.
    """
    sess = SessionManager.create_session(mode="engineer", title="Fallback Со Стримингом")
    sess["type"] = "engineering"
    SandboxManager.set_active_session_id(sess["id"])
    host = DummyEngineerFlowHost(sess)

    user_prompt = "Напиши скрипт utility.py и сохрани его"
    host.chat_history.append({"role": "user", "content": user_prompt})
    host.current_session["messages"].append({
        "id": "msg_u1",
        "role": "user",
        "sender": "Вы",
        "text": user_prompt,
        "is_user": True
    })
    # Стриминговый плейсхолдер
    host.current_session["messages"].append({
        "id": "msg_a1",
        "role": "assistant",
        "sender": "Элария",
        "text": "",
        "is_user": False
    })
    SessionManager.save_session(host.current_session)

    raw_llm_code_response = (
        "Вот готовый скрипт:\n"
        "```python\n"
        "def run_check():\n"
        "    return True\n"
        "```\n"
    )
    host._handle_engineering_llm_flow(raw_llm_code_response)

    # Проверка в ОЗУ: файл создан, плейсхолдер обновлен, дублей нет
    assert len(host.current_session["messages"]) == 2
    assert "📄 Файл `utility.py` сохранен" in host.current_session["messages"][1]["text"]
    assert len(host.chat_history) == 2
    assert "📄 Файл `utility.py` сохранен" in host.chat_history[1]["content"]

    # Проверка после перезагрузки с диска
    SessionManager._sessions_cache.clear()
    reloaded = SessionManager.load_session(sess["id"])
    assert reloaded is not None
    assert len(reloaded["messages"]) == 2
    assert "📄 Файл `utility.py` сохранен" in reloaded["messages"][1]["text"]
    assert len(reloaded["chat_history"]) == 2
    assert "📄 Файл `utility.py` сохранен" in reloaded["chat_history"][1]["content"]


def test_fallback_code_block_persistence_without_placeholder(isolated_engineer_env):
    """
    Сценарий 3: Fallback-перехват кода без предварительного плейсхолдера в messages (нестриминговый ответ).
    Проверяет:
      1. Корректную работу ветки `if not updated: messages.append(...)` внутри _sync_turn.
      2. Добавление новой записи ассистента в messages и chat_history.
      3. Персистентность на диске без дублирования.
    """
    sess = SessionManager.create_session(mode="engineer", title="Fallback Без Стриминга")
    sess["type"] = "engineering"
    SandboxManager.set_active_session_id(sess["id"])
    host = DummyEngineerFlowHost(sess)

    user_prompt = "Создай файл config_gen.py"
    host.chat_history.append({"role": "user", "content": user_prompt})
    # В messages только сообщение пользователя (плейсхолдер ассистента НЕ создавался)
    host.current_session["messages"].append({
        "id": "msg_u1",
        "role": "user",
        "sender": "Вы",
        "text": user_prompt,
        "is_user": True
    })
    SessionManager.save_session(host.current_session)

    raw_llm_code_response = (
        "```python\n"
        "DEBUG = True\n"
        "PORT = 8080\n"
        "```\n"
    )
    host._handle_engineering_llm_flow(raw_llm_code_response)

    # Проверка ОЗУ: _sync_turn должен был добавить новую запись ассистента
    assert len(host.current_session["messages"]) == 2
    assert host.current_session["messages"][1]["role"] == "assistant"
    assert "📄 Файл `config_gen.py` сохранен" in host.current_session["messages"][1]["text"]

    assert len(host.chat_history) == 2
    assert host.chat_history[1]["role"] == "assistant"
    assert "📄 Файл `config_gen.py` сохранен" in host.chat_history[1]["content"]

    # Проверка после перезагрузки с диска
    SessionManager._sessions_cache.clear()
    reloaded = SessionManager.load_session(sess["id"])
    assert reloaded is not None
    assert len(reloaded["messages"]) == 2
    assert reloaded["messages"][1]["role"] == "assistant"
    assert "📄 Файл `config_gen.py` сохранен" in reloaded["messages"][1]["text"]
    assert len(reloaded["chat_history"]) == 2
    assert "📄 Файл `config_gen.py` сохранен" in reloaded["chat_history"][1]["content"]
