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
        "_get_embedding_engine", "_run", "warmup_memory_engine"
    ):
        return
    return _orig_thread_start(self, *args, **kwargs)
threading.Thread.start = _safe_thread_start

_AIRGAP_BASE = Path(__file__).resolve().parent.parent / "pytest_airgap_sandbox"
_AIRGAP_BASE.mkdir(parents=True, exist_ok=True)
_AIRGAP_TEMP_ROOT = Path(tempfile.mkdtemp(prefix="run_", dir=str(_AIRGAP_BASE)))


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
    }
    for attr, target_path in mapping.items():
        setattr(config, attr, target_path)
        if attr.endswith("_DIR"):
            target_path.mkdir(parents=True, exist_ok=True)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)

    # Синхронизируем уже импортированные модули, которые могли скопировать константы через from config import ...
    for mod_name, mod_attrs in [
        ("utils.config_manager", {"CONFIG_PATH": mapping["CONFIG_PATH"]}),
        ("services.session_manager", {"HISTORY_DIR": mapping["HISTORY_DIR"], "OUTPUTS_DIR": mapping["OUTPUTS_DIR"]}),
        ("services.sandbox_manager", {"WORKSPACE_DIR": mapping["WORKSPACE_DIR"], "OUTPUTS_DIR": mapping["OUTPUTS_DIR"], "FORGE_ROOT_DIR": temp_root}),
        ("services.rp_cast_manager", {"HISTORY_DIR": mapping["HISTORY_DIR"]}),
        ("services.user_profile_manager", {"DATA_DIR": mapping["DATA_DIR"], "USER_PROFILES_PATH": mapping["DATA_DIR"] / "user_profiles.json"}),
        ("services.art_director_config", {"ART_DIRECTOR_MODELS_PATH": mapping["ART_DIRECTOR_MODELS_PATH"], "DATA_DIR": mapping["DATA_DIR"]}),
        ("services.knowledge_manager", {"KNOWLEDGE_BASE_DIR": mapping["KNOWLEDGE_BASE_DIR"], "KNOWLEDGE_REGISTRY_PATH": mapping["KNOWLEDGE_REGISTRY_PATH"]}),
        ("services.memory_db", {"MEMORY_DB_PATH": mapping["MEMORY_DB_PATH"], "DATA_DIR": mapping["DATA_DIR"]}),
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
    shutil.rmtree(_AIRGAP_BASE, ignore_errors=True)


@pytest.fixture(autouse=True)
def check_sandbox_state_leak(request):
    _apply_airgap_paths(_AIRGAP_TEMP_ROOT)
    from services.sandbox_manager import SandboxManager
    SandboxManager._workspace_dir_override = _AIRGAP_TEMP_ROOT / "workspace"
    SandboxManager.set_active_session_id(None)

    yield

    active_sid = SandboxManager.get_active_session_id()
    if active_sid is not None:
        SandboxManager.set_active_session_id(None)
        pytest.fail(
            f"УТЕЧКА СОСТОЯНИЯ: Тест '{request.node.name}' оставил незакрытую сессию '{active_sid}'!"
        )
