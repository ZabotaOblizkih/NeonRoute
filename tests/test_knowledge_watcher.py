"""
tests/test_knowledge_watcher.py

Unit tests for KnowledgeWatcher satellite and Gatekeeper inverted index checks:
1. KnowledgeWatcher initializes and monitors directory.
2. Debounce timer (2.0s) and scan_and_sync dispatch.
3. Gatekeeper inverted index matching: domain terms force search (is_conversational_query == False).
4. Global budget enforcement in AssistantPromptBuilder.build_knowledge_context (<= 9000 chars).
"""

import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import QCoreApplication

from services.knowledge_watcher import KnowledgeWatcher
from services.hybrid_search import BM25Index, is_conversational_query
from services.assistant_prompt_builder import AssistantPromptBuilder


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance()
    if not app:
        app = QCoreApplication([])
    return app


def test_knowledge_watcher_lifecycle(qapp, tmp_path):
    """Проверка создания, старта и остановки KnowledgeWatcher."""
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    (kb_dir / "doc1.md").write_text("Техническая документация", encoding="utf-8")

    watcher = KnowledgeWatcher(knowledge_dir=kb_dir)
    assert watcher.knowledge_dir == kb_dir.resolve()

    watcher.start()
    assert watcher._is_running is True
    assert str(kb_dir.resolve()) in watcher._watcher.directories()

    watcher.stop()
    assert watcher._is_running is False


def test_knowledge_watcher_debounce_triggers_sync(qapp, tmp_path):
    """Проверка срабатывания дебаунса и вызова scan_and_sync."""
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    doc = kb_dir / "test_doc.md"
    doc.write_text("Версия 1", encoding="utf-8")

    watcher = KnowledgeWatcher(knowledge_dir=kb_dir)
    watcher.start()

    with patch.object(watcher, "_dispatch_sync") as mock_dispatch:
        # Изменяем файл
        doc.write_text("Версия 2 с обновленными регламентами", encoding="utf-8")
        watcher._on_fs_event(str(doc))

        assert watcher._timer.isActive(), "Дебаунс-таймер должен быть запущен"
        assert watcher._timer.interval() == 2000, "Интервал дебаунса обязан быть 2000 мс (2.0 сек)"

        # Имитируем завершение дебаунс-таймера
        watcher._on_debounce_timeout()

        assert mock_dispatch.called
    watcher.stop()


def test_gatekeeper_inverted_index_domain_term():
    """Если токен запроса присутствует в inverted_index, запрос признается предметным."""
    bm25 = BM25Index()
    bm25.index([
        "Модуль vram_arbiter управляет переключением видеопамяти.",
        "Параметр турбонаддува 02T_311 составляет 1.4 бар."
    ])

    # Запрос из одного-двух слов, которые есть в базе
    assert is_conversational_query("vram_arbiter", bm25_index=bm25) is False
    assert is_conversational_query("видеопамять", bm25_index=bm25) is False

    # Разговорный запрос без предметных терминов
    assert is_conversational_query("привет", bm25_index=bm25) is True
    assert is_conversational_query("спасибо большое", bm25_index=bm25) is True
    assert is_conversational_query("ок", bm25_index=bm25) is True


def test_build_knowledge_context_global_budget_limit():
    """Проверка ограничения глобального контекстного бюджета MAX_GLOBAL_BUDGET = 9000 символов."""
    # Создаем 5 документов по 3000 символов каждый (суммарно 15000 символов)
    matched_docs = [
        {
            "filename": f"manual_{i}.md",
            "chunk_id": f"manual_{i}.md#c0",
            "content_snippet": f"Раздел {i}. " + ("Данные спецификации и регламенты детали. " * 60)
        }
        for i in range(5)
    ]

    kb_context = AssistantPromptBuilder.build_knowledge_context(matched_docs)

    assert "<KnowledgeBase>" in kb_context
    assert "</KnowledgeBase>" in kb_context

    # Извлекаем XML секцию
    kb_xml = kb_context[kb_context.find("<KnowledgeBase>"):kb_context.find("</KnowledgeBase>") + len("</KnowledgeBase>")]

    # Длина блока KnowledgeBase обязана укладываться в 9000 символов
    assert len(kb_xml) <= 9000, f"Размер секции превысил бюджет: {len(kb_xml)} > 9000"

    # Не все 5 документов должны войти (из-за отсечки по бюджету)
    included_count = sum(1 for i in range(5) if f"manual_{i}.md" in kb_xml)
    assert included_count < 5, "Все документы не должны были поместиться в 9000 символов"
    assert included_count >= 1, "Хотя бы один документ обязан быть включен"
