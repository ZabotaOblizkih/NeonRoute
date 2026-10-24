"""
Unit tests for Elaria's Knowledge HUD indicator and source citations.
Verifies:
1. set_knowledge_sources populates chips and metadata correctly.
2. HUD container visibility (shown with sources, hidden without).
3. Tooltip format with source filename, fragment #chunk_id, relevance score, and citation snippet.
4. AssistantChatMessageWidget integration and styling.
5. Sources propagation in assistant_flow_controller.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication

from ui.components.chat_message_widget import (
    KnowledgeSourceChip,
    KnowledgeHUDWidget,
    ChatMessageWidget,
    AssistantChatMessageWidget
)
from services.knowledge_manager import KnowledgeManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_knowledge_chip_creation_and_tooltip(qapp):
    """Проверка микро-чипа: имя файла, номер чанка, иконка и цитата в тултипе."""
    chip = KnowledgeSourceChip(
        filename="manual_kpp.pdf",
        chunk_id="manual_kpp.pdf#c4",
        snippet="Шестерня пятой передачи имеет номер 02T 311 239 B для механических трансмиссий.",
        score=0.875
    )

    assert chip.filename == "manual_kpp.pdf"
    assert "manual_kpp.pdf" in chip.toolTip()
    assert "Фрагмент #4" in chip.toolTip()
    assert "88%" in chip.toolTip() or "87%" in chip.toolTip()
    assert "02T 311 239 B" in chip.toolTip()
    assert chip.toolTip().startswith("Источник: manual_kpp.pdf")


def test_knowledge_chip_table_and_raw_chunk_id(qapp):
    """Проверка чипа для табличных файлов и целочисленных chunk_id."""
    chip = KnowledgeSourceChip(
        filename="prices.csv",
        chunk_id=0,
        snippet="Колодки передние; 4500 руб; в наличии",
        score=0.92
    )
    assert "prices.csv" in chip.toolTip()
    assert "Фрагмент #0" in chip.toolTip()
    assert "92%" in chip.toolTip()
    assert "Колодки передние" in chip.toolTip()


def test_knowledge_hud_widget_set_sources(qapp):
    """Проверка контейнера HUD: создание чипов, дедупликация и видимость."""
    hud = KnowledgeHUDWidget()
    assert not hud.isVisible()

    sources = [
        {
            "filename": "manual.pdf",
            "chunk_id": 1,
            "snippet": "Инструкция по ремонту суппорта.",
            "score": 0.85
        },
        {
            "filename": "specs.docx",
            "chunk_id": "specs.docx#c2",
            "snippet": "Спецификация охлаждения CPU.",
            "score": 0.78
        },
        # Дубликат первого источника
        {
            "filename": "manual.pdf",
            "chunk_id": 1,
            "snippet": "Дублирующая запись.",
            "score": 0.85
        }
    ]

    hud.set_sources(sources)
    assert hud.isVisible()
    # Дедупликация должна оставить ровно 2 уникальных чипа
    assert len(hud.chips) == 2
    assert hud.chips[0].filename == "manual.pdf"
    assert hud.chips[1].filename == "specs.docx"

    # Очистка источников должна скрывать контейнер
    hud.set_sources([])
    assert not hud.isVisible()
    assert len(hud.chips) == 0


def test_chat_message_widget_knowledge_hud_toggle(qapp):
    """Проверка интеграции HUD в базовый ChatMessageWidget."""
    msg = ChatMessageWidget(
        sender="Элария",
        text="Вот информация по вашему запросу из базы знаний.",
        is_user=False
    )
    msg.show()

    # Изначально без источников HUD скрыт
    assert hasattr(msg, "knowledge_hud")
    assert not msg.knowledge_hud.isVisible()

    sources = [
        {
            "filename": "engine_manual.pdf",
            "chunk_id": "engine_manual.pdf#c3",
            "snippet": "Момент затяжки болтов ГБЦ составляет 90 Нм.",
            "score": 0.95
        }
    ]
    msg.set_knowledge_sources(sources)
    assert not msg.knowledge_hud.isHidden()
    assert msg.knowledge_hud.isVisible()
    assert len(msg.knowledge_hud.chips) == 1
    assert "engine_manual.pdf" in msg.knowledge_hud.chips[0].toolTip()

    # Сброс источников
    msg.set_knowledge_sources([])
    assert msg.knowledge_hud.isHidden()
    assert not msg.knowledge_hud.isVisible()


def test_assistant_chat_message_widget_knowledge_hud(qapp):
    """Проверка работы HUD в специализированном AssistantChatMessageWidget."""
    sources = [
        {
            "filename": "transmission.txt",
            "chunk_id": 2,
            "snippet": "Замена масла в коробке передач каждые 60 000 км.",
            "score": 0.89
        }
    ]

    assistant_msg = AssistantChatMessageWidget(
        sender="Элария",
        text="Согласно руководству по трансмиссии...",
        is_user=False,
        knowledge_sources=sources
    )
    assistant_msg.show()

    assert not assistant_msg.knowledge_hud.isHidden()
    assert assistant_msg.knowledge_hud.isVisible()
    assert len(assistant_msg.knowledge_hud.chips) == 1
    chip = assistant_msg.knowledge_hud.chips[0]
    assert chip.filename == "transmission.txt"
    assert "Фрагмент #2" in chip.toolTip()
    assert "89%" in chip.toolTip()


def test_assistant_flow_controller_sources_propagation(qapp, tmp_path: Path, monkeypatch):
    """Проверка извлечения sources в _enqueue_assistant_llm_task и их проброса в задачу и UI."""
    from ui.controllers.assistant_flow_controller import AssistantFlowControllerMixin

    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"

    # Документ в базе
    doc = kb_dir / "guide.txt"
    doc.write_text("Архитектура NeonRoute использует строго 0 МБ VRAM на CPU.", encoding="utf-8")

    monkeypatch.setattr(KnowledgeManager, "get_knowledge_dir", lambda custom_dir=None: kb_dir)
    monkeypatch.setattr(KnowledgeManager, "get_registry_path", lambda custom_path=None, custom_dir=None: reg_file)

    KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_file)

    class MockWindow(AssistantFlowControllerMixin):
        def __init__(self):
            self.current_session = {
                "id": "sess-test",
                "messages": [],
                "sampling_params": {"temperature": 0.7, "max_tokens": 512}
            }
            self.chat_history = [
                {"role": "user", "content": "Расскажи про архитектуру и VRAM"}
            ]
            self._current_user_request = "Расскажи про архитектуру и VRAM"
            self.task_queue = MagicMock()
            self.lm_client = MagicMock()
            self._active_tasks = {}
            self.lm_combo = MagicMock()
            self.lm_combo.currentText.return_value = "local-model"
            self.app_config = {}

    win = MockWindow()
    win._enqueue_assistant_llm_task()

    assert win.task_queue.enqueue.called
    task = win.task_queue.enqueue.call_args[0][0]
    metadata = task.metadata
    assert "knowledge_sources" in metadata
    sources = metadata["knowledge_sources"]
    assert len(sources) > 0
    assert sources[0]["filename"] == "guide.txt"
    assert "score" in sources[0]
    assert hasattr(win, "_pending_knowledge_sources")
    assert win._pending_knowledge_sources == sources
