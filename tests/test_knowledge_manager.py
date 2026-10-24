"""
Unit tests for KnowledgeManager service.
Tests trigger extraction, registry synchronization, file imports, and safe deletion.
"""

import sys
from pathlib import Path
import pytest
from PyQt6.QtWidgets import QApplication
from services.knowledge_manager import KnowledgeManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app



def test_extract_triggers_ru_en():
    sample_text = """
    # Архитектура NeonRoute и База Знаний Эларии
    Сервис KnowledgeManager предназначен для автоматического извлечения ключевых триггеров
    и индексации документов без использования ресурсов видеопамяти VRAM (строго на CPU).
    Документы могут содержать исходный код на Python, отчеты в PDF, системные логи и конфигурации JSON.
    Быстрый алгоритм фильтрует стоп-слова, лемматизирует термины и формирует список тегов.
    Интерфейс на PyQt6 qfluentwidgets позволяет управлять документами в реальном времени.
    """
    triggers = KnowledgeManager.extract_triggers(sample_text, max_triggers=10)
    assert isinstance(triggers, list)
    assert 8 <= len(triggers) <= 10
    # Check that common stopwords were filtered out
    assert "и" not in triggers
    assert "в" not in triggers
    assert "на" not in triggers
    assert "для" not in triggers
    # Check that high-value domain terms and acronyms are present
    lower_triggers = [t.lower() for t in triggers]
    assert any("vram" in t for t in lower_triggers)
    assert any("cpu" in t for t in lower_triggers) or any("neonroute" in t for t in lower_triggers)


def test_extract_triggers_empty_text():
    assert KnowledgeManager.extract_triggers("") == []
    assert KnowledgeManager.extract_triggers("   ") == []


def test_scan_and_sync_lifecycle(tmp_path: Path):
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"

    # 1. Initial sync on empty folder
    reg = KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_file)
    assert reg["total_documents"] == 0
    assert len(reg["documents"]) == 0
    assert reg_file.exists()

    # 2. Add text file
    doc_path = kb_dir / "overview.txt"
    doc_path.write_text("Архитектура модулей и спецификация VRAM нейросетевого ассистента.", encoding="utf-8")

    reg = KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_file)
    assert reg["total_documents"] == 1
    assert "overview.txt" in reg["documents"]
    doc_meta = reg["documents"]["overview.txt"]
    assert doc_meta["status"] == "parsed"
    assert doc_meta["filename"] == "overview.txt"
    assert len(doc_meta["triggers"]) > 0

    # 3. Add python code file
    code_path = kb_dir / "worker.py"
    code_path.write_text("class InferenceWorker:\n    def process_task(self):\n        pass\n", encoding="utf-8")

    reg = KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_file)
    assert reg["total_documents"] == 2
    assert "worker.py" in reg["documents"]

    # 4. Remove file from disk and sync
    doc_path.unlink()
    reg = KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_file)
    assert reg["total_documents"] == 1
    assert "overview.txt" not in reg["documents"]
    assert "worker.py" in reg["documents"]


def test_import_and_delete_file(tmp_path: Path):
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"

    # Create external file
    external_file = tmp_path / "instructions.md"
    external_file.write_text("# Инструкция пользователя\nБыстрый запуск приложения NeonRoute.", encoding="utf-8")

    # Import
    success = KnowledgeManager.import_file(external_file, custom_dir=kb_dir, custom_registry=reg_file)
    assert success is True
    assert (kb_dir / "instructions.md").exists()

    docs = KnowledgeManager.get_documents(custom_dir=kb_dir, custom_registry=reg_file)
    assert len(docs) == 1
    assert docs[0]["filename"] == "instructions.md"
    assert docs[0]["status"] == "parsed"

    # Delete
    del_success = KnowledgeManager.delete_file("instructions.md", custom_dir=kb_dir, custom_registry=reg_file)
    assert del_success is True
    assert not (kb_dir / "instructions.md").exists()

    docs_after = KnowledgeManager.get_documents(custom_dir=kb_dir, custom_registry=reg_file)
    assert len(docs_after) == 0


def test_import_unsupported_extension(tmp_path: Path):
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"

    invalid_file = tmp_path / "archive.zip"
    invalid_file.write_bytes(b"PK000fakezip")

    success = KnowledgeManager.import_file(invalid_file, custom_dir=kb_dir, custom_registry=reg_file)
    assert success is False


def test_knowledge_sync_worker_thread(qapp, tmp_path: Path):
    from services.knowledge_manager import KnowledgeSyncWorker
    worker = KnowledgeSyncWorker()
    results = []

    def _on_finish(data):
        results.append(data)

    worker.sync_finished.connect(_on_finish)
    worker.run()
    assert len(results) == 1
    assert "documents" in results[0]


def test_knowledge_manager_dialog_ui(qapp, tmp_path: Path, monkeypatch):
    from ui.dialogs.knowledge_manager_dialog import KnowledgeManagerDialog, DocumentCardWidget
    from qfluentwidgets import TitleLabel, CaptionLabel, PrimaryPushButton, PushButton, TransparentToolButton

    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"
    doc_file = kb_dir / "guide.txt"
    doc_file.write_text("Архитектура и модули системы знаний Эларии. Параметры VRAM и памяти.", encoding="utf-8")

    monkeypatch.setattr(KnowledgeManager, "get_knowledge_dir", lambda custom_dir=None: kb_dir)
    monkeypatch.setattr(KnowledgeManager, "get_registry_path", lambda custom_path=None, custom_dir=None: reg_file)

    KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_file)

    dlg = KnowledgeManagerDialog()
    assert dlg.title_label.text() == "Знания Эларии"
    assert "Проиндексировано документов: 1" in dlg.caption_label.text()
    assert isinstance(dlg.btn_add, PrimaryPushButton)
    assert isinstance(dlg.btn_open_folder, PushButton)
    assert isinstance(dlg.btn_sync, TransparentToolButton)

    # Check card
    assert dlg.cards_layout.count() == 1
    card = dlg.cards_layout.itemAt(0).widget()
    assert isinstance(card, DocumentCardWidget)
    assert card.filename == "guide.txt"
    assert len(card.doc.get("triggers", [])) > 0
    dlg.close()


def test_menu_builder_has_knowledge_action(qapp):
    from ui.builders.menu_builder import MenuBuilder
    from PyQt6.QtWidgets import QMainWindow

    class MockWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.app_config = {}
            self._open_knowledge_manager_dialog = lambda: None
            self.open_dependency_dialog = lambda: None

        def statusBar(self):
            class MockStatusBar:
                def showMessage(self, *a, **k):
                    pass
            return MockStatusBar()

    win = MockWindow()
    MenuBuilder.setup_menus(win)

    # Check that system menu contains "Знания Эларии..."
    found = False
    for action in win.menuBar().actions():
        menu = action.menu()
        if menu and "Система" in menu.title():
            for sub_act in menu.actions():
                if "Знания Эларии" in sub_act.text():
                    found = True
                    break
    assert found is True

