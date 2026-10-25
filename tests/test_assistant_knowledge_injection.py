"""
Unit and integration tests for Step 2 of Elaria's Knowledge System.
Tests trigger routing, knowledge context XML building, and injection into Assistant system prompt.
"""

from pathlib import Path
from unittest.mock import MagicMock
import pytest
from PyQt6.QtWidgets import QApplication

from services.knowledge_manager import KnowledgeManager
from services.assistant_prompt_builder import AssistantPromptBuilder


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        import sys
        app = QApplication(sys.argv)
    yield app


def test_find_relevant_documents(tmp_path: Path):
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"

    # Создаем тестовые файлы
    f1 = kb_dir / "vram_guide.txt"
    f1.write_text("Руководство по распределению видеопамяти VRAM и оптимизации Forge SD.", encoding="utf-8")

    f2 = kb_dir / "ast_engine.py"
    f2.write_text("class ASTEngine:\n    def parse_tokens(self):\n        pass\n", encoding="utf-8")

    KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_file)

    # 1. Поиск по VRAM и Forge
    matches_vram = KnowledgeManager.find_relevant_documents(
        "Как работает распределение VRAM в Forge?",
        threshold=0.25,
        custom_dir=kb_dir,
        custom_registry=reg_file
    )
    assert len(matches_vram) >= 1
    assert matches_vram[0]["filename"] == "vram_guide.txt"
    assert matches_vram[0]["relevance_score"] > 0.25
    assert len(matches_vram[0]["matched_triggers"]) > 0

    # 2. Поиск по имени файла или коду
    matches_ast = KnowledgeManager.find_relevant_documents(
        "Где исходный код ast_engine?",
        threshold=0.25,
        custom_dir=kb_dir,
        custom_registry=reg_file
    )
    assert len(matches_ast) >= 1
    assert matches_ast[0]["filename"] == "ast_engine.py"

    # 3. Нерелевантный запрос
    matches_none = KnowledgeManager.find_relevant_documents(
        "Рецепт яблочного пирога с корицей",
        threshold=0.3,
        custom_dir=kb_dir,
        custom_registry=reg_file
    )
    assert len(matches_none) == 0


def test_get_document_knowledge_snippet(tmp_path: Path):
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()

    f = kb_dir / "notes.txt"
    long_content = "Важная заметка о системе.\n" * 50
    f.write_text(long_content, encoding="utf-8")

    # Успешный сниппет
    snippet = KnowledgeManager.get_document_knowledge_snippet("notes.txt", max_chars=200, custom_dir=kb_dir)
    assert snippet is not None
    assert "Важная заметка" in snippet
    assert len(snippet) <= 400  # С учетом строки о сокращении

    # Несуществующий файл
    missing = KnowledgeManager.get_document_knowledge_snippet("missing.pdf", custom_dir=kb_dir)
    assert missing is None


def test_build_knowledge_context_xml():
    docs = [
        {
            "filename": "manual.txt",
            "triggers": ["руководство", "элария", "vram"],
            "content_snippet": "Это содержимое документа manual."
        },
        {
            "filename": "api.md",
            "triggers": ["интерфейс", "fastapi"],
            "content_snippet": "Описание эндпоинтов API."
        }
    ]

    xml_block = AssistantPromptBuilder.build_knowledge_context(docs)
    assert "<KnowledgeBase>" in xml_block
    assert "</KnowledgeBase>" in xml_block
    assert '<Document name="manual.txt" triggers="руководство, элария, vram">' in xml_block
    assert "Это содержимое документа manual." in xml_block
    assert '<Document name="api.md" triggers="интерфейс, fastapi">' in xml_block
    assert "Описание эндпоинтов API." in xml_block

    # Пустой список
    assert AssistantPromptBuilder.build_knowledge_context([]) == ""


def test_assistant_prompt_builder_knowledge_injection():
    docs = [
        {
            "filename": "server.log",
            "triggers": ["ошибка", "таймаут"],
            "content": "[2026-10-24 22:00:00] Timeout detected."
        }
    ]

    prompt = AssistantPromptBuilder.build(
        user_name="Алексей",
        include_memory=False,
        matched_knowledge_docs=docs
    )

    assert "<KnowledgeBase>" in prompt
    assert '<Document name="server.log" triggers="ошибка, таймаут">' in prompt
    assert "[2026-10-24 22:00:00] Timeout detected." in prompt
    assert "[identity & persona]" in prompt
    # Блок базы знаний должен располагаться в хвосте системного промпта (Recency Bias)
    assert prompt.index("[identity & persona]") < prompt.index("<KnowledgeBase>")


def test_assistant_flow_controller_knowledge_routing(qapp, tmp_path: Path, monkeypatch):
    from ui.controllers.assistant_flow_controller import AssistantFlowControllerMixin

    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"

    doc = kb_dir / "architecture.txt"
    doc.write_text("Архитектура модулей NeonRoute и управление памятью VRAM.", encoding="utf-8")

    monkeypatch.setattr(KnowledgeManager, "get_knowledge_dir", lambda custom_dir=None: kb_dir)
    monkeypatch.setattr(KnowledgeManager, "get_registry_path", lambda custom_path=None, custom_dir=None: reg_file)

    KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_file)

    class MockAssistantWindow(AssistantFlowControllerMixin):
        def __init__(self):
            self.current_session = {
                "id": "sess-1",
                "messages": [],
                "sampling_params": {"temperature": 0.7, "max_tokens": 1024}
            }
            self.chat_history = [
                {"role": "user", "content": "Расскажи про архитектуру и VRAM"}
            ]
            self._current_user_request = "Расскажи про архитектуру и VRAM"
            self.task_queue = MagicMock()
            self.lm_client = MagicMock()
            self._active_tasks = {}
            self.lm_combo = MagicMock()
            self.lm_combo.currentText.return_value = "meta-llama-3"
            self.app_config = {}

    win = MockAssistantWindow()
    win._enqueue_assistant_llm_task()

    # Проверяем, что задача инференса отправлена в task_queue
    assert win.task_queue.enqueue.called
    task = win.task_queue.enqueue.call_args[0][0]
    messages = task.kwargs.get("messages", [])
    assert len(messages) >= 2
    system_msg = messages[0]["content"]

    assert "<KnowledgeBase>" in system_msg
    assert '<Document name="architecture.txt"' in system_msg
    assert "управление памятью VRAM" in system_msg
