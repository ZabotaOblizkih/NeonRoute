import os
import shutil
import tempfile
import threading
from pathlib import Path
import pytest

# Включаем Headless-режим Qt до любых импортов UI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Блокируем фоновый прогрев FastEmbed/LanceDB в тестах для мгновенного старта и 0 утечек
_orig_thread_start = threading.Thread.start
def _safe_thread_start(self, *args, **kwargs):
    target_name = getattr(getattr(self, "_target", None), "__name__", "")
    if getattr(self, "name", "") == "FastEmbedWarmup" or target_name in (
        "_get_embedding_engine", "_run", "warmup_memory_engine", "_watchdog"
    ):
        return
    return _orig_thread_start(self, *args, **kwargs)
threading.Thread.start = _safe_thread_start

# Заглушка синхронной загрузки FastEmbed ONNX для мгновенного старта в тестах
try:
    import fastembed
    import numpy as np

    class _FastEmbedMock:
        def __init__(self, *args, **kwargs):
            pass

        def embed(self, texts):
            for _ in texts:
                yield np.zeros(384, dtype=np.float32)

    fastembed.TextEmbedding = _FastEmbedMock
except ImportError:
    pass

_AIRGAP_BASE = Path(__file__).resolve().parent.parent / "pytest_airgap_sandbox"
_AIRGAP_BASE.mkdir(parents=True, exist_ok=True)
_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")
_AIRGAP_TEMP_ROOT = Path(tempfile.mkdtemp(prefix=f"run_{_XDIST_WORKER}_", dir=str(_AIRGAP_BASE)))


def _apply_airgap_paths(temp_root: Path) -> None:
    """Перенаправляет все директории записи в изолированный временный полигон."""
    import config

    mapping = {
        "CONFIG_DIR": temp_root / "config",
        "FORGE_ROOT_DIR": temp_root,
        "CONFIG_PATH": temp_root / "config.json",
        "DATA_DIR": temp_root / "data",
        "HISTORY_DIR": temp_root / "history",
        "WORKSPACE_DIR": temp_root / "workspace",
        "OUTPUTS_DIR": temp_root / "outputs",
        "LOGS_DIR": temp_root / "logs",
        "EXPORTS_DIR": temp_root / "exports",
        "KNOWLEDGE_BASE_DIR": temp_root / "knowledge_base",
        "KNOWLEDGE_REGISTRY_PATH": temp_root / "knowledge_base" / "registry.json",
        "MEMORY_DB_PATH": temp_root / "data" / "memory.db",
        "ART_DIRECTOR_MODELS_PATH": temp_root / "data" / "art_director_models.json",
        "LORA_REGISTRY_PATH": temp_root / "data" / "lora_registry.json",
        "SERVER_CONFIG_INI_PATH": temp_root / "server_config.ini",
    }
    for attr, target_path in mapping.items():
        setattr(config, attr, target_path)
        if attr.endswith("_DIR"):
            target_path.mkdir(parents=True, exist_ok=True)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)

    if not mapping["SERVER_CONFIG_INI_PATH"].exists():
        mapping["SERVER_CONFIG_INI_PATH"].write_text("[PATHS]\nforge_root_dir = \n", encoding="utf-8")

    # Синхронизируем уже импортированные модули, которые могли скопировать константы через from config import ...
    for mod_name, mod_attrs in [
        ("utils.config_manager", {"CONFIG_PATH": mapping["CONFIG_PATH"]}),
        ("services.session_manager", {"HISTORY_DIR": mapping["HISTORY_DIR"], "OUTPUTS_DIR": mapping["OUTPUTS_DIR"]}),
        ("services.sandbox_manager", {"WORKSPACE_DIR": mapping["WORKSPACE_DIR"], "OUTPUTS_DIR": mapping["OUTPUTS_DIR"], "FORGE_ROOT_DIR": temp_root}),
        ("services.rp_cast_manager", {"HISTORY_DIR": mapping["HISTORY_DIR"]}),
        ("services.rp_cast_extractor", {"HISTORY_DIR": mapping["HISTORY_DIR"]}),
        ("mobile_server.routes.sessions", {"HISTORY_DIR": mapping["HISTORY_DIR"]}),
        ("services.user_profile_manager", {"DATA_DIR": mapping["DATA_DIR"], "USER_PROFILES_PATH": mapping["DATA_DIR"] / "user_profiles.json"}),
        ("services.art_director_config", {"ART_DIRECTOR_MODELS_PATH": mapping["ART_DIRECTOR_MODELS_PATH"], "DATA_DIR": mapping["DATA_DIR"]}),
        ("services.knowledge_manager", {"KNOWLEDGE_BASE_DIR": mapping["KNOWLEDGE_BASE_DIR"], "KNOWLEDGE_REGISTRY_PATH": mapping["KNOWLEDGE_REGISTRY_PATH"]}),
        ("services.memory_db", {"MEMORY_DB_PATH": mapping["MEMORY_DB_PATH"], "DATA_DIR": mapping["DATA_DIR"]}),
        ("core.forge_locator", {"SERVER_CONFIG_INI_PATH": mapping["SERVER_CONFIG_INI_PATH"]}),
    ]:
        mod = sys.modules.get(mod_name)
        if mod is not None:
            for k, v in mod_attrs.items():
                if hasattr(mod, k):
                    setattr(mod, k, v)

    # Переключаем синглтон ConfigManager на временный config.json
    cfg_mod = sys.modules.get("utils.config_manager")
    if cfg_mod and hasattr(cfg_mod, "ConfigManager"):
        cfg_mod.ConfigManager.CONFIG_FILE = mapping["CONFIG_PATH"]

    # Отключаем LanceDB/ONNX в memory_db и уводим SQLite в Temp
    mdb = sys.modules.get("services.memory_db")
    if mdb is not None:
        mdb._EMBED_MODEL = False
        mdb._LANCE_TABLE = False
        mdb._RP_LANCE_TABLE = False
        if hasattr(mdb, "memory_db") and mdb.memory_db is not None:
            mdb.memory_db.db_path = mapping["MEMORY_DB_PATH"]


import sys
_apply_airgap_paths(_AIRGAP_TEMP_ROOT)


def pytest_configure(config):
    _apply_airgap_paths(_AIRGAP_TEMP_ROOT)


def pytest_unconfigure(config):
    mdb = sys.modules.get("services.memory_db")
    if mdb and hasattr(mdb, "memory_db") and hasattr(mdb.memory_db, "close"):
        try:
            mdb.memory_db.close()
        except Exception:
            pass
    shutil.rmtree(_AIRGAP_TEMP_ROOT, ignore_errors=True)
    if not hasattr(config, "workerinput"):
        shutil.rmtree(_AIRGAP_BASE, ignore_errors=True)


_PROD_HISTORY_DIR = Path(__file__).resolve().parent.parent / "history"
_PROD_MEMORY_DB = Path(__file__).resolve().parent.parent / "data" / "memory.db"


def _snapshot_prod_history() -> set[str]:
    if not _PROD_HISTORY_DIR.exists():
        return set()
    return {p.name for p in _PROD_HISTORY_DIR.glob("chat_*.json")}


_REAL_VRAM_WAIT_TESTS = {
    "test_wait_for_vram_stabilization_respects_dwell",
    "test_vram_arbiter_stabilization_exits_via_plateau",
}


@pytest.fixture(autouse=True)
def fast_test_guards(request, monkeypatch):
    """Убирает лишние 2.5–3.0с ожидания реальных сокетов и WDDM VRAM в тестах."""
    from services.process_manager import ProcessManager
    from services.vram_arbiter import VRAMArbiter

    monkeypatch.setattr(
        ProcessManager,
        "is_port_open",
        lambda self, host="127.0.0.1", port=1234, timeout=0.1: False,
    )
    if request.node.name not in _REAL_VRAM_WAIT_TESTS and "stabilization" not in request.node.name:
        monkeypatch.setattr(
            VRAMArbiter,
            "_wait_for_vram_stabilization",
            lambda self, *args, **kwargs: None,
        )


@pytest.fixture(autouse=True)
def check_sandbox_state_leak(request):
    _apply_airgap_paths(_AIRGAP_TEMP_ROOT)
    from services.sandbox_manager import SandboxManager
    SandboxManager._workspace_dir_override = _AIRGAP_TEMP_ROOT / "workspace"
    SandboxManager.set_active_session_id(None)
    is_xdist_worker = bool(os.environ.get("PYTEST_XDIST_WORKER"))
    before_prod_files = _snapshot_prod_history() if not is_xdist_worker else set()

    yield

    _apply_airgap_paths(_AIRGAP_TEMP_ROOT)

    # Очистка висящих фоновых таймеров роутера между тестами
    mr_mod = sys.modules.get("services.model_router")
    if mr_mod and hasattr(mr_mod, "ModelRouter"):
        inst = getattr(mr_mod.ModelRouter, "_instance", None)
        if inst and hasattr(inst, "cancel_deferred_warmup"):
            inst.cancel_deferred_warmup()

    active_sid = SandboxManager.get_active_session_id()
    if active_sid is not None:
        SandboxManager.set_active_session_id(None)
        pytest.fail(
            f"УТЕЧКА СОСТОЯНИЯ: Тест '{request.node.name}' оставил незакрытую сессию '{active_sid}'!"
        )

    if not is_xdist_worker:
        after_prod_files = _snapshot_prod_history()
        leaked_files = after_prod_files - before_prod_files
        if leaked_files:
            for lf in leaked_files:
                try:
                    (_PROD_HISTORY_DIR / lf).unlink(missing_ok=True)
                except Exception:
                    pass
            pytest.fail(
                f"УТЕЧКА СЕССИИ В ПРОДАКШЕН: Тест '{request.node.name}' создал файлы в боевой папке history/: {leaked_files}"
            )
