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


def test_import_extended_formats(tmp_path: Path):
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"

    # CSV
    csv_file = tmp_path / "stats.csv"
    csv_file.write_text("model,vram_mb,latency_ms\nllama3,4000,45\nforge,3500,120\n", encoding="utf-8")
    assert KnowledgeManager.import_file(csv_file, custom_dir=kb_dir, custom_registry=reg_file) is True

    # Rust
    rs_file = tmp_path / "engine.rs"
    rs_file.write_text("pub struct FastEngine {\n    pub memory_limit: usize,\n}\n", encoding="utf-8")
    assert KnowledgeManager.import_file(rs_file, custom_dir=kb_dir, custom_registry=reg_file) is True

    # YAML
    yml_file = tmp_path / "settings.yml"
    yml_file.write_text("app_mode: engineer\nactive_device: cpu\n", encoding="utf-8")
    assert KnowledgeManager.import_file(yml_file, custom_dir=kb_dir, custom_registry=reg_file) is True

    # Sync check
    reg = KnowledgeManager.load_registry(reg_file, kb_dir)
    assert reg["total_documents"] == 3
    assert "stats.csv" in reg["documents"]
    assert "engine.rs" in reg["documents"]
    assert "settings.yml" in reg["documents"]

    # Verify triggers were extracted
    csv_doc = reg["documents"]["stats.csv"]
    assert len(csv_doc["triggers"]) > 0
    assert csv_doc["status"] == "parsed"



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

    # Check close button
    assert isinstance(dlg.close_btn, TransparentToolButton)
    assert dlg.close_btn.toolTip() == "Закрыть"

    rejected = []
    dlg.rejected.connect(lambda: rejected.append(True))
    dlg.close_btn.click()
    assert len(rejected) == 1

    # Check Esc key press
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import Qt, QEvent
    esc_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    dlg.keyPressEvent(esc_event)
    assert len(rejected) == 2

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


def test_find_relevant_documents_top_3_chunks(tmp_path: Path, monkeypatch):
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"

    doc_file = kb_dir / "telemetry_guide.txt"
    doc_file.write_text("Архитектура телеметрии и мониторинг серверов.", encoding="utf-8")

    KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_file)

    class MockSearchEngine:
        def search(self, query, top_k=10, threshold=0.05):
            return [
                {"chunk_id": "c1", "filename": "telemetry_guide.txt", "text": "Чанк 1: телеметрия", "score": 0.95},
                {"chunk_id": "c2", "filename": "telemetry_guide.txt", "text": "Чанк 2: метрики", "score": 0.85},
                {"chunk_id": "c3", "filename": "telemetry_guide.txt", "text": "Чанк 3: алерты", "score": 0.75},
                {"chunk_id": "c4", "filename": "telemetry_guide.txt", "text": "Чанк 4: лишний", "score": 0.65},
            ]

    monkeypatch.setattr(KnowledgeManager, "get_search_engine", lambda custom_dir=None, custom_index_path=None: MockSearchEngine())

    results = KnowledgeManager.find_relevant_documents(
        "телеметрия",
        threshold=0.2,
        custom_dir=kb_dir,
        custom_registry=reg_file
    )

    assert len(results) == 1
    doc = results[0]
    assert doc["filename"] == "telemetry_guide.txt"
    assert doc["chunk_id"] == "c1"
    assert len(doc["matched_chunks"]) == 3
    assert doc["matched_chunks"][0]["chunk_id"] == "c1"
    assert doc["matched_chunks"][1]["chunk_id"] == "c2"
    assert doc["matched_chunks"][2]["chunk_id"] == "c3"
    assert doc["content_snippet"] == "Чанк 1: телеметрия\n\n---\n\nЧанк 2: метрики\n\n---\n\nЧанк 3: алерты"


def test_fuse_contextual_query():
    # 1. No previous query
    assert KnowledgeManager._fuse_contextual_query("какой статус сервера", None) == "какой статус сервера"
    assert KnowledgeManager._fuse_contextual_query("какой статус сервера", "   ") == "какой статус сервера"

    # 2. Standalone short/long query without corrective or anaphoric markers -> NOT fused
    assert KnowledgeManager._fuse_contextual_query("покажи подробнее", "как настроить сервер базы данных postgresql") == "покажи подробнее"
    long_q = "один два три четыре пять шесть семь восемь"
    assert KnowledgeManager._fuse_contextual_query(long_q, "предыдущий контекст запроса") == long_q

    # 3. Anaphoric marker triggers fusion
    ana_q = "покажи его код"
    fused_ana = KnowledgeManager._fuse_contextual_query(ana_q, "как настроить сервер базы данных postgresql")
    assert ana_q in fused_ana
    assert any(tok in fused_ana for tok in ["настроить", "сервер", "базы", "данных", "postgresql"])

    # 4. Corrective marker triggers fusion
    corr_q = "не то, покажи другую информацию"
    fused_corr = KnowledgeManager._fuse_contextual_query(corr_q, "сетевой маршрутизатор router vlan")
    assert any(tok in fused_corr for tok in ["router", "сетевой", "маршрутизатор", "vlan"])


def test_find_cross_references():
    registered = {"config.py", "database.md", "logger.py", "main.py", "db.py"}
    
    # Direct match by filename (100% trust)
    text1 = "Настройки хранятся в config.py, а схема описана в database.md"
    refs1 = KnowledgeManager._find_cross_references(text1, registered)
    assert "config.py" in refs1
    assert "database.md" in refs1

    # Match by formatted non-generic stem: "модуль logger" or "`logger`"
    text2 = "Модуль logger отвечает за вывод логов в терминал"
    refs2 = KnowledgeManager._find_cross_references(text2, registered)
    assert "logger.py" in refs2

    text2_quotes = "Используйте `logger` для трассировки"
    refs2_quotes = KnowledgeManager._find_cross_references(text2_quotes, registered)
    assert "logger.py" in refs2_quotes

    # Generic stem in GENERIC_STEMS ("main" from main.py) should NOT match even if formatted
    text_generic = "Смотри модуль `main`"
    refs_generic = KnowledgeManager._find_cross_references(text_generic, registered)
    assert "main.py" not in refs_generic

    # Unformatted stem should NOT match
    text_unformatted = "проверь logger здесь"
    refs_unformatted = KnowledgeManager._find_cross_references(text_unformatted, registered)
    assert "logger.py" not in refs_unformatted

    # Stem with len < 4 ("db" from db.py) should NOT match
    text3 = "Мы подключили `db` к сервису"
    refs3 = KnowledgeManager._find_cross_references(text3, registered)
    assert "db.py" not in refs3

    # Empty inputs
    assert KnowledgeManager._find_cross_references("", registered) == []
    assert KnowledgeManager._find_cross_references("hello", set()) == []


def test_find_relevant_documents_exclude_chunk_ids(tmp_path: Path, monkeypatch):
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"

    doc_file = kb_dir / "network_spec.txt"
    doc_file.write_text("Сетевая спецификация маршрутизации.", encoding="utf-8")
    KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_file)

    class MockSearchEngine:
        def search(self, query, top_k=15, threshold=0.05):
            return [
                {"chunk_id": "chunk_excluded", "filename": "network_spec.txt", "text": "Старый чанк", "score": 0.9},
                {"chunk_id": "chunk_valid", "filename": "network_spec.txt", "text": "Новый чанк", "score": 0.8},
            ]

    monkeypatch.setattr(KnowledgeManager, "get_search_engine", lambda custom_dir=None, custom_index_path=None: MockSearchEngine())

    # Exclude chunk_excluded
    results = KnowledgeManager.find_relevant_documents(
        "маршрутизация",
        threshold=0.2,
        custom_dir=kb_dir,
        custom_registry=reg_file,
        exclude_chunk_ids={"chunk_excluded"}
    )

    assert len(results) == 1
    doc = results[0]
    assert doc["chunk_id"] == "chunk_valid"
    assert len(doc["matched_chunks"]) == 1
    assert doc["matched_chunks"][0]["chunk_id"] == "chunk_valid"


def test_find_relevant_documents_cross_reference_linker(tmp_path: Path, monkeypatch):
    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"

    doc1 = kb_dir / "service_a.txt"
    doc1.write_text("Архитектура микросервиса А.", encoding="utf-8")
    doc2 = kb_dir / "service_b.txt"
    doc2.write_text("Спецификация и детали service_b.", encoding="utf-8")
    KnowledgeManager.scan_and_sync(custom_dir=kb_dir, custom_registry=reg_file)

    class MockSearchEngine:
        def search(self, query, top_k=15, threshold=0.05):
            # When searching for primary query
            if "service_b" in query:
                return [
                    {"chunk_id": "b1", "filename": "service_b.txt", "text": "Контент service_b", "score": 0.85}
                ]
            return [
                {"chunk_id": "a1", "filename": "service_a.txt", "text": "Сервис А ссылается на service_b.txt в работе", "score": 0.9}
            ]

    monkeypatch.setattr(KnowledgeManager, "get_search_engine", lambda custom_dir=None, custom_index_path=None: MockSearchEngine())

    results = KnowledgeManager.find_relevant_documents(
        "микросервис",
        threshold=0.2,
        custom_dir=kb_dir,
        custom_registry=reg_file
    )

    assert len(results) >= 2
    filenames = [r["filename"] for r in results]
    assert "service_a.txt" in filenames
    assert "service_b.txt" in filenames

    # Find the cross-referenced entry
    cross_doc = next(r for r in results if r["filename"] == "service_b.txt")
    assert cross_doc.get("is_cross_reference") is True
    assert cross_doc.get("matched_triggers") == ["cross_reference"]
    # Child score capped below parent score
    parent_doc = next(r for r in results if r["filename"] == "service_a.txt")
    assert cross_doc["relevance_score"] == round(parent_doc["relevance_score"] - 0.05, 3)



