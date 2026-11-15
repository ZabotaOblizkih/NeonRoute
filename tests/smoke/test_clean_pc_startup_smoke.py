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
    assert config.APP_VERSION == "0.9.8-beta"

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
