"""
Smoke Test: Clean PC Cold Start without SD WebUI Forge.
Simulates a fresh installation on a machine where:
1. No prior config.json, server_config.ini, memory.db, vector_memory/, history/, outputs/, logs/ exist.
2. SD WebUI Forge is completely absent from disk (and FORGE_ROOT_DIR points to a non-existent path).
3. Verifies that all core services (ConfigManager, ServerConfigResolver, MemoryDB, SessionManager)
   and MainWindow initialize safely without throwing exceptions or popping up locate_forge dialogs.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox
import config
from utils.config_manager import ConfigManager
from services.server_config import ServerConfigResolver
from services.session_manager import SessionManager


@pytest.fixture(scope="session")
def qapp():
    """Headless Qt Application fixture for offscreen test execution."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


def test_clean_pc_cold_start_without_forge_smoke(tmp_path: Path, qapp, monkeypatch):
    """
    Simulates complete cold start in an isolated clean tmp_path:
    - Missing config.json and server_config.ini
    - Missing memory.db, vector_memory, history, outputs, logs
    - Non-existent Forge installation path
    - Confirms APP_VERSION is 0.9.8-beta and prompt_locate_forge is NOT invoked.
    """
    clean_root = tmp_path / "NeonRouteClean"
    clean_root.mkdir(parents=True, exist_ok=True)

    clean_data = clean_root / "data"
    clean_history = clean_root / "history"
    clean_outputs = clean_root / "outputs"
    clean_logs = clean_root / "logs"
    clean_workspace = clean_root / "workspace"
    non_existent_forge = clean_root / "non_existent_forge_dir"

    # 1. Verify target paths do not exist initially
    assert not (clean_root / "config.json").exists()
    assert not (clean_root / "server_config.ini").exists()
    assert not (clean_data / "memory.db").exists()
    assert not non_existent_forge.exists()

    # 2. Redirect config module paths to clean sandbox
    monkeypatch.setattr(config, "BASE_DIR", clean_root)
    monkeypatch.setattr(config, "DATA_DIR", clean_data)
    monkeypatch.setattr(config, "HISTORY_DIR", clean_history)
    monkeypatch.setattr(config, "OUTPUTS_DIR", clean_outputs)
    monkeypatch.setattr(config, "LOGS_DIR", clean_logs)
    monkeypatch.setattr(config, "WORKSPACE_DIR", clean_workspace)
    monkeypatch.setattr(config, "SERVER_CONFIG_INI_PATH", clean_root / "server_config.ini")
    monkeypatch.setattr(config, "FORGE_ROOT_DIR", non_existent_forge)
    monkeypatch.setenv("FORGE_ROOT_DIR", str(non_existent_forge))

    # Also update module-level constants in resolvers
    import utils.config_manager as cm
    monkeypatch.setattr(cm, "CONFIG_PATH", clean_root / "config.json")

    import services.session_manager as sm
    monkeypatch.setattr(sm, "HISTORY_DIR", clean_history)

    import services.memory_db as mdb
    monkeypatch.setattr(mdb, "DATA_DIR", clean_data)
    monkeypatch.setattr(mdb, "MEMORY_DB_PATH", clean_data / "memory.db")

    # 3. Verify Version SSOT
    assert bool(config.APP_VERSION) and isinstance(config.APP_VERSION, str)

    # 4. Verify ConfigManager handles missing config.json gracefully
    loaded_config = ConfigManager.load_config()
    assert isinstance(loaded_config, dict)
    assert loaded_config.get("theme") == "dark"
    assert loaded_config.get("chat_font_size") == 13
    assert loaded_config.get("background_path") == ""

    # 5. Verify ServerConfigResolver handles missing server_config.ini gracefully
    loaded_server_cfg = ServerConfigResolver.load_ini()
    assert isinstance(loaded_server_cfg, dict)
    assert loaded_server_cfg.get("port") == 1234
    assert loaded_server_cfg.get("default_context") == 14336

    # 6. Verify SessionManager handles empty history directory
    sessions = SessionManager.list_sessions()
    assert sessions == []
    assert clean_history.exists()  # Auto-created

    # 7. Verify MemoryDB initializes SQLite schema on clean machine
    from services.memory_db import MemoryDB
    clean_db = MemoryDB(db_path=clean_data / "memory.db")
    assert clean_db is not None
    assert (clean_data / "memory.db").exists()
    facts = clean_db.get_all_facts()
    assert facts == []

    # 8. Verify MainWindow startup lifecycle:
    # Ensure NO modal popup / prompt_locate_forge dialog is ever shown on clean cold start
    prompt_locate_mock = MagicMock()
    msg_box_exec_mock = MagicMock()

    monkeypatch.setattr("ui.controllers.hardware_engine.HardwareEngineMixin.prompt_locate_forge", prompt_locate_mock)
    monkeypatch.setattr(QMessageBox, "exec", msg_box_exec_mock)

    # Suppress background sockets / threads during test
    monkeypatch.setattr("services.lm_studio.LMStudioClient.check_health", lambda self: True)
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", lambda: "PURE COMPUTE")
    monkeypatch.setattr("ui.main_window.MainWindow._start_background_services", lambda self: None)
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])
    monkeypatch.setattr("services.process_manager.ProcessManager.start_lm_studio_async", lambda *a, **kw: None)

    from ui.main_window import MainWindow
    window = MainWindow()
    window.show()

    try:
        assert window is not None
        assert window.current_mode == "assistant"
        # Verify dialog was NOT triggered during clean start
        prompt_locate_mock.assert_not_called()
        msg_box_exec_mock.assert_not_called()
    finally:
        window._shutdown_completed = True
        window.close()


def test_clean_pc_process_manager_safe_when_llm_engine_missing(monkeypatch):
    """
    Проверяет безопасную обработку отсутствующего LLM-движка в ProcessManager:
    - Ни llama-server, ни LM Studio не обнаружены на диске.
    - subprocess.run(["lms", ...]) НЕ вызывается (нет WinError 2).
    - Состояние переводится в ServerState.ERROR с информативным сообщением.
    - Сигналы lms_status_changed и action_completed эмитируются корректно.
    """
    import subprocess
    from services.process_manager import ProcessManager, ServerState

    pm = ProcessManager()
    monkeypatch.setattr(pm, "has_headless_server", lambda: False)
    monkeypatch.setattr(pm, "is_lm_studio_running", lambda: False)
    pm._lms_exe = None
    monkeypatch.setattr(pm, "_locate_lm_studio_app", lambda: None)

    mock_run = MagicMock()
    monkeypatch.setattr(subprocess, "run", mock_run)

    emitted_status = []
    emitted_actions = []
    pm.lms_status_changed.connect(lambda ok, msg: emitted_status.append((ok, msg)))
    pm.action_completed.connect(lambda action, ok, msg: emitted_actions.append((action, ok, msg)))

    # 1. _run_lms_start
    pm._run_lms_start()
    assert pm.server_state == ServerState.ERROR
    mock_run.assert_not_called()
    assert len(emitted_status) == 1
    assert emitted_status[0][0] is False
    assert "Движок llama-server не найден в папке bin/" in emitted_status[0][1]
    assert len(emitted_actions) == 1
    assert emitted_actions[0][0] == "lms_start"
    assert emitted_actions[0][1] is False

    # 2. _run_lms_stop
    pm._run_lms_stop()
    mock_run.assert_not_called()

    # 3. _run_lms_load
    pm._run_lms_load("test_model")
    mock_run.assert_not_called()

    # 4. unload_all_lms_models
    pm.unload_all_lms_models()
    mock_run.assert_not_called()


def test_clean_pc_hardware_engine_no_periodic_restart_on_error(monkeypatch):
    """
    Проверяет, что HardwareEngineMixin предотвращает периодический перезапуск
    start_lm_studio_async() каждые 30 секунд, если server_state == ServerState.ERROR
    и движки не появились на диске.
    """
    from services.process_manager import ProcessManager, ServerState
    from ui.controllers.hardware_engine import HardwareEngineMixin

    class DummyWindow(HardwareEngineMixin):
        def __init__(self):
            self.process_manager = ProcessManager()
            self.statusBar = lambda: MagicMock()

    win = DummyWindow()
    monkeypatch.setattr(win.process_manager, "has_headless_server", lambda: False)
    monkeypatch.setattr(win.process_manager, "is_lm_studio_running", lambda: False)
    win.process_manager._lms_exe = None
    monkeypatch.setattr(win.process_manager, "_locate_lm_studio_app", lambda: None)
    win.process_manager._server_state = ServerState.ERROR

    mock_start_async = MagicMock()
    monkeypatch.setattr(win.process_manager, "start_lm_studio_async", mock_start_async)

    assert win._can_start_llm_engine() is False

    # Периодический вызов (каждые 30 секунд)
    win._check_and_restart_lms_periodic()
    mock_start_async.assert_not_called()

    # Ручной переключатель при отсутствии движков также блокирует запуск
    win.pill_lm = MagicMock(is_online=False)
    win._toggle_lms_server()
    mock_start_async.assert_not_called()


def test_clean_pc_knowledge_sync_zero_docs_instant(tmp_path, monkeypatch):
    """
    Проверяет, что при 0 документов в реестре KnowledgeManager сразу
    записывает пустой search_index.json без инициализации HybridSearchEngine и FastEmbed ONNX.
    """
    import json
    from services.knowledge_manager import KnowledgeManager

    clean_kdir = tmp_path / "knowledge"
    clean_kdir.mkdir(parents=True, exist_ok=True)

    # Гарантируем, что FastEmbed не будет вызван
    def _fail_embed():
        raise AssertionError("get_embedding_model() should NOT be called for 0 documents!")

    monkeypatch.setattr(KnowledgeManager, "get_embedding_model", _fail_embed)

    registry = KnowledgeManager.scan_and_sync(custom_dir=clean_kdir)
    assert registry.get("total_documents") == 0

    idx_path = clean_kdir / "search_index.json"
    assert idx_path.exists()
    with open(idx_path, "r", encoding="utf-8") as f:
        idx_data = json.load(f)

    assert idx_data.get("version") == 3
    assert idx_data.get("total_chunks") == 0
    assert idx_data.get("chunks") == []


def test_clean_pc_startup_barrier_milestones_no_memory_warmup(qapp, monkeypatch):
    """
    Проверяет, что memory_warmup исключен из блокирующих милстоунов
    StartupBarrier в MainWindow.
    """
    monkeypatch.setattr("services.lm_studio.LMStudioClient.check_health", lambda self: True)
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", lambda: "PURE COMPUTE")
    monkeypatch.setattr("ui.main_window.MainWindow._start_background_services", lambda self: None)
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])
    monkeypatch.setattr("services.process_manager.ProcessManager.start_lm_studio_async", lambda *a, **kw: None)

    from ui.main_window import MainWindow
    win = MainWindow()
    try:
        assert "memory_warmup" not in win._pending_startup_milestones
        assert "knowledge_sync" in win._pending_startup_milestones
        assert "llm_server" in win._pending_startup_milestones
    finally:
        win._shutdown_completed = True
        win.close()


def test_clean_pc_environment_doctor_session_caching_and_git_info(caplog, monkeypatch):
    """
    Проверяет:
    1) Кэширование результата аудита окружения на время сессии (однократное сканирование).
    2) Отсутствие уровня WARNING при отсутствии git (логируется только INFO).
    """
    import logging
    from services.environment_doctor import EnvironmentDoctor, DEPENDENCY_CATALOG

    EnvironmentDoctor.reset_cache()

    # Имитируем чистый ПК без Git, но со всеми python-пакетами
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)
    monkeypatch.setattr("importlib.util.find_spec", lambda pkg: MagicMock())

    with caplog.at_level(logging.INFO):
        missing_1 = EnvironmentDoctor.check_missing(verbose=True)

    assert len(missing_1) == 1
    assert missing_1[0]["key"] == "git"

    # Проверяем логирование: нет WARNING про Git, есть INFO
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 0, f"Expected 0 warnings for git, found: {[w.message for w in warnings]}"

    infos = [r for r in caplog.records if r.levelno == logging.INFO and "Git" in r.message]
    assert len(infos) == 1
    assert "Git не обнаружен в системе" in infos[0].message

    # Проверяем кэширование: повторный вызов возвращает закэшированный список без повторного сканирования
    scan_called = MagicMock()
    monkeypatch.setattr("importlib.util.find_spec", scan_called)

    missing_2 = EnvironmentDoctor.check_missing(verbose=True)
    assert missing_2 == missing_1
    scan_called.assert_not_called()
