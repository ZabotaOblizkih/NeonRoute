from pathlib import Path
from unittest.mock import MagicMock, patch

from services.knowledge_manager import KnowledgeManager
from ui.controllers.assistant_flow_controller import AssistantFlowControllerMixin


class _DummyTaskQueue:
    """Минимальный перехватчик InferenceTask без фоновых потоков."""

    def __init__(self):
        self.tasks = []

    def enqueue(self, task):
        self.tasks.append(task)
        return getattr(task, "id", None)


class _DummyAssistantController(AssistantFlowControllerMixin):
    """Изолированный контроллер Ассистента для сквозного теста без GUI."""

    def __init__(self, session_id: str):
        self.lm_client = MagicMock()
        self.lm_client.chat_completion.return_value = "Тестовый ответ модели."
        self.task_queue = _DummyTaskQueue()
        self.current_session = {
            "id": session_id,
            "chat_history": [],
            "sampling_params": {},
        }
        self.chat_history = self.current_session["chat_history"]
        self.app_config = {}
        self.system_monitor = None
        self.active_lms_model = "test-local-8b"
        self._current_user_request = ""
        self._pending_knowledge_sources = None
        self._active_tasks = {}
        self.is_busy = False


def _run_user_turn(controller: _DummyAssistantController, user_text: str):
    """Симулирует полный цикл хода пользователя: очередь -> deferred RAG -> lm_client."""
    controller._current_user_request = user_text
    controller._enqueue_assistant_llm_task()
    task = controller.task_queue.tasks[-1]
    result = task.func(**task.kwargs)
    assert result == "Тестовый ответ модели."
    call_kwargs = controller.lm_client.chat_completion.call_args[1]
    messages = call_kwargs["messages"]
    controller.chat_history.append({"role": "user", "content": user_text})
    controller.chat_history.append({"role": "assistant", "content": result})
    return messages, call_kwargs


def test_sticky_knowledge_docs_cleared_on_unrelated_turn_and_preserved_on_anaphora_e2e(
    tmp_path: Path, monkeypatch
):
    """
    Сквозной тест (от запроса пользователя в контроллере до payload в lm_client.chat_completion):
    1. Релевантный запрос -> документ попадает в системный промпт модели и сохраняется в active_knowledge_docs.
    2. Small talk («Спасибо») -> документ НЕ подмешивается в промпт модели.
    3. Явная анафора («Продолжи по нему») -> активный документ возвращается в промпт модели.
    4. Самостоятельный несвязанный запрос («Напиши рецепт яблочного пирога») -> промпт чист,
       а active_knowledge_docs сбрасывается.
    5. Анафора после смены темы («Продолжи по нему») -> старый документ больше НЕ воскрешается.
    """
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir(parents=True, exist_ok=True)
    reg_path = kb_dir / "registry.json"

    manual_file = kb_dir / "reactor_manual.md"
    manual_file.write_text(
        "# Протокол охлаждения реактора NR-77\n\n"
        "При перегреве контура охлаждения NR-77 активируйте аварийный клапан KV-404.\n"
        "Рабочее давление теплоносителя не должно превышать 12 МПа.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("config.KNOWLEDGE_BASE_DIR", kb_dir)
    monkeypatch.setattr("config.KNOWLEDGE_REGISTRY_PATH", reg_path)
    monkeypatch.setattr("services.knowledge_manager.KNOWLEDGE_BASE_DIR", kb_dir)
    monkeypatch.setattr("services.knowledge_manager.KNOWLEDGE_REGISTRY_PATH", reg_path)
    monkeypatch.setattr(KnowledgeManager, "_search_engine_cache", {})

    KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_path)

    controller = _DummyAssistantController(session_id="e2e_sticky_test_01")

    with patch("services.memory_db.memory_db.search_relevant_facts", return_value=[]):
        # Ход 1: релевантный запрос по базе знаний
        msgs_1, _ = _run_user_turn(controller, "Как активировать клапан охлаждения NR-77?")
        sys_1 = msgs_1[0]["content"]
        assert "<KnowledgeBase>\n" in sys_1
        assert "reactor_manual.md" in sys_1
        assert "KV-404" in sys_1
        assert controller._pending_knowledge_sources
        assert controller.current_session.get("active_knowledge_docs")

        # Ход 2: small talk не должен тянуть старый документ в модель
        msgs_2, _ = _run_user_turn(controller, "Спасибо")
        sys_2 = msgs_2[0]["content"]
        assert "<KnowledgeBase>\n" not in sys_2
        assert "reactor_manual.md" not in sys_2
        assert "KV-404" not in sys_2
        assert controller._pending_knowledge_sources is None

        # Ход 3: явная анафора («Продолжи по нему») должна подхватить активный документ
        msgs_3, _ = _run_user_turn(controller, "Продолжи по нему")
        sys_3 = msgs_3[0]["content"]
        assert "<KnowledgeBase>\n" in sys_3
        assert "reactor_manual.md" in sys_3
        assert "KV-404" in sys_3
        assert controller._pending_knowledge_sources

        # Ход 4: самостоятельный несвязанный запрос -> без документа и со сбросом active_knowledge_docs
        msgs_4, _ = _run_user_turn(controller, "Какая сегодня погода в Токио?")
        sys_4 = msgs_4[0]["content"]
        assert "<KnowledgeBase>\n" not in sys_4
        assert "reactor_manual.md" not in sys_4
        assert "KV-404" not in sys_4
        assert controller._pending_knowledge_sources is None
        assert controller.current_session.get("active_knowledge_docs") == []

        # Ход 5: анафора после смены темы не должна воскрешать старый файл
        msgs_5, _ = _run_user_turn(controller, "Продолжи по нему")
        sys_5 = msgs_5[0]["content"]
        assert "<KnowledgeBase>\n" not in sys_5
        assert "reactor_manual.md" not in sys_5
        assert "KV-404" not in sys_5
        assert controller._pending_knowledge_sources is None


def test_document_anaphora_phrase_in_file_and_no_false_positives(tmp_path: Path, monkeypatch):
    """
    Проверяет:
    1. Фраза «а что в этом файле» активирует подхват документа до модели.
    2. Ложноположительная фраза «В этом году я хочу выучить Python» не считается документной анафорой.
    """
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir(parents=True, exist_ok=True)
    reg_path = kb_dir / "registry.json"

    (kb_dir / "port_guide.md").write_text(
        "# Настройка сетевого шлюза GW-99\n\n"
        "Порт шлюза GW-99 по умолчанию равен 8443.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("config.KNOWLEDGE_BASE_DIR", kb_dir)
    monkeypatch.setattr("config.KNOWLEDGE_REGISTRY_PATH", reg_path)
    monkeypatch.setattr("services.knowledge_manager.KNOWLEDGE_BASE_DIR", kb_dir)
    monkeypatch.setattr("services.knowledge_manager.KNOWLEDGE_REGISTRY_PATH", reg_path)
    monkeypatch.setattr(KnowledgeManager, "_search_engine_cache", {})

    KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_path)

    assert KnowledgeManager.has_document_anaphora("а что в этом файле") is True
    assert KnowledgeManager.has_document_anaphora("В этом году я хочу выучить Python") is False

    controller = _DummyAssistantController(session_id="e2e_sticky_test_02")

    with patch("services.memory_db.memory_db.search_relevant_facts", return_value=[]):
        msgs_1, _ = _run_user_turn(controller, "Какой порт у шлюза GW-99?")
        assert "8443" in msgs_1[0]["content"]

        # Уточнение с «а что в этом файле» сохраняет контекст документа до модели
        msgs_2, _ = _run_user_turn(controller, "А что в этом файле еще есть?")
        assert "<KnowledgeBase>\n" in msgs_2[0]["content"]
        assert "port_guide.md" in msgs_2[0]["content"]
        assert "8443" in msgs_2[0]["content"]

        # Самостоятельный вопрос с «В этом году...» НЕ должен подтягивать port_guide.md
        msgs_3, _ = _run_user_turn(controller, "В этом году я хочу выучить Python")
        assert "<KnowledgeBase>\n" not in msgs_3[0]["content"]
        assert "port_guide.md" not in msgs_3[0]["content"]
        assert "8443" not in msgs_3[0]["content"]
