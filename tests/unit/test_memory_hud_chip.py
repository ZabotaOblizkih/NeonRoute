"""
Unit tests for Memory HUD Chip creation and widget rendering.
"""

import sys
from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtWidgets import QApplication, QLabel
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QMouseEvent

# Инициализируем QApplication для PyQt тестов если еще не инициализирован
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app

def test_assemble_assistant_context_generates_memory_source():
    """Тест создания источника знаний с is_memory: True в assemble_assistant_context."""
    from services.assistant_flow import assemble_assistant_context

    mock_mem_db = MagicMock()
    mock_mem_db.search_relevant_facts.return_value = [
        "Пользователь пишет на Python",
        "Пользователь предпочитает чай"
    ]

    dummy_controller = MagicMock()
    dummy_controller.current_session = {"messages": [], "chat_history": []}
    dummy_controller._pending_knowledge_sources = None

    with patch("services.memory_db.memory_db", mock_mem_db):
        msgs, docs, kb_block = assemble_assistant_context(
            actual_prompt="На чем я пишу код?",
            curr_sess=dummy_controller.current_session,
            controller=dummy_controller,
            include_memory=True
        )

    # Проверяем наличие плашки памяти
    sources = dummy_controller._pending_knowledge_sources
    assert sources is not None and len(sources) > 0
    mem_source = next((s for s in sources if s.get("is_memory")), None)
    assert mem_source is not None
    assert "🧠 Долговременная память" in mem_source["filename"]
    assert mem_source["chunk_id"] == "#2"
    assert "Python" in mem_source["snippet"]
    assert "чай" in mem_source["snippet"]

def test_knowledge_source_chip_memory_styling_and_click(qapp):
    """Тест стилизации плашки памяти и безопасного клика (без краша открытия несуществующего файла)."""
    from ui.components.chat_message_widget import KnowledgeSourceChip

    chip = KnowledgeSourceChip(
        filename="🧠 Долговременная память",
        chunk_id="#2",
        snippet="• Пользователь пишет на Python\n• Пользователь предпочитает чай",
        score=1.0,
        is_memory=True
    )

    assert chip.is_memory is True
    lbl = chip.findChild(QLabel)
    assert lbl is not None
    assert "🧠 Память #2" in lbl.text()
    assert "#C084FC" in lbl.styleSheet()
    assert "rgba(168, 85, 247" in chip.styleSheet()

    # Симулируем клик мыши — для памяти должен показываться QToolTip, без попытки open_document_at_chunk
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(10.0, 10.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier
    )
    # Клик не должен выбрасывать исключений
    chip.mousePressEvent(event)


def test_knowledge_source_chip_regular_document_styling_and_click(qapp):
    """Тест стилизации обычного документа (не память) и безопасного клика/тултипа."""
    from ui.components.chat_message_widget import KnowledgeSourceChip

    chip = KnowledgeSourceChip(
        filename="manual.pdf",
        chunk_id="#4",
        snippet="Текст фрагмента из документации по проекту...",
        score=0.85,
        is_memory=False
    )

    assert chip.is_memory is False
    assert chip.filename == "manual.pdf"
    lbl = chip.findChild(QLabel)
    assert lbl is not None
    assert "manual.pdf #4" in lbl.text()
    assert "#38BDF8" in lbl.styleSheet()
    assert "rgba(0, 240, 255" in chip.styleSheet()
    assert "Источник: manual.pdf" in chip.tooltip_text
    assert "85%" in chip.tooltip_text

    # Симулируем клик мыши — для обычного документа также штатно показывается тултип без падений
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(10.0, 10.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier
    )
    chip.mousePressEvent(event)

