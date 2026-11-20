"""
tests/smoke/conftest.py
Единый каркас для смоук-сьюта NeonRoute:
1. Стандартизированный диагностический хелпер assert_diag («Сценарий -> Вход -> Ожидали -> Получили»).
2. autouse=True фикстура полной изоляции путей и сброса синглтонов/кэшей (smoke_isolation_guard).
3. Переиспользуемые фабрики и фикстуры (модели ArtDirector, моки LM/Forge, DummyWindow, qapp).
"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock
import pytest

from PyQt6.QtWidgets import QApplication, QWidget

import config
from utils.config_manager import ConfigManager
from services.model_router import ModelRouter, normalize_model_choice
from services.art_director_config import save_art_director_models
from services.session_manager import SessionManager
from services.sandbox_manager import SandboxManager
from services.tool_engine import ToolEngine
from ui.controllers.hardware_engine import HardwareEngineMixin


# =========================================================================
# 1. ЕДИНЫЙ ДИАГНОСТИЧЕСКИЙ ХЕЛПЕР
# =========================================================================

def format_diag(scenario: str, input_data: Any, expected: Any, actual: Any) -> str:
    """Формирует строго стандартизированное диагностическое сообщение об ошибке."""
    return (
        f"\n[ДИАГНОСТИКА СБОЯ]\n"
        f"Сценарий -> {scenario}\n"
        f"Вход     -> {input_data}\n"
        f"Ожидали  -> {expected}\n"
        f"Получили -> {actual}\n"
    )


def assert_diag(condition: bool, scenario: str, input_data: Any, expected: Any, actual: Any):
    """Выполняет assert с выводом 'Сценарий -> Вход -> Ожидали -> Получили'."""
    assert condition, format_diag(scenario, input_data, expected, actual)


def assert_diag_eq(actual: Any, expected: Any, scenario: str, input_data: Any):
    """Проверяет эквивалентность actual == expected с диагностикой."""
    assert actual == expected, format_diag(scenario, input_data, expected, actual)


def assert_diag_in(item: Any, container: Any, scenario: str, input_data: Any):
    """Проверяет наличие item in container с диагностикой."""
    assert item in container, format_diag(scenario, input_data, f"Содержит {item!r}", container)


import builtins
builtins.format_diag = format_diag
builtins.assert_diag = assert_diag
builtins.assert_diag_eq = assert_diag_eq
builtins.assert_diag_in = assert_diag_in


# =========================================================================
# 2. AUTOUSE ФИКСТУРА ИЗОЛЯЦИИ И ЗАЧИСТКИ СИНГЛТОНОВ
# =========================================================================

@pytest.fixture(autouse=True)
def smoke_isolation_guard(tmp_path, monkeypatch):
    """
    Гарантирует 100% изоляцию путей при прямом запуске любого смоук-теста:
    - перенаправляет все каталоги записи в изолированный tmp_path;
    - предотвращает появление артефактов в c:\\NeonRoute\\data и c:\\NeonRoute\\history;
    - гарантированно сбрасывает состояние синглтонов и кэшей в teardown.
    """
    tmp_data = tmp_path / "data"
    tmp_data.mkdir(parents=True, exist_ok=True)
    tmp_history = tmp_path / "history"
    tmp_history.mkdir(parents=True, exist_ok=True)
    # Для workspace используем изолированный полигон внутри BASE_DIR (pytest_airgap_sandbox),
    # чтобы удовлетворять инварианту безопасности allowed_roots в SandboxManager.validate_read_file_path
    airgap_ws_base = config.BASE_DIR / "pytest_airgap_sandbox" / tmp_path.name
    tmp_workspace = airgap_ws_base / "workspace"
    tmp_workspace.mkdir(parents=True, exist_ok=True)
    tmp_outputs = tmp_path / "outputs"
    tmp_outputs.mkdir(parents=True, exist_ok=True)
    tmp_kv_cache = tmp_path / "kv_cache"
    tmp_kv_cache.mkdir(parents=True, exist_ok=True)
    tmp_config_file = tmp_path / "config.json"

    # Создаем базовый config.json
    base_config = {
        "active_mode": "roleplay",
        "assistant_custom_prompt_template": "",
        "default_sampling_assistant": {"temperature": 0.7, "top_p": 0.9},
        "thinking_mode": "auto"
    }
    tmp_config_file.write_text(json.dumps(base_config, ensure_ascii=False, indent=2), encoding="utf-8")

    # Изолируем пути в config
    monkeypatch.setattr(config, "DATA_DIR", tmp_data)
    monkeypatch.setattr(config, "HISTORY_DIR", tmp_history)
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_workspace)
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_outputs)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_config_file)
    tmp_art_models_path = tmp_data / "art_director_models.json"
    monkeypatch.setattr(config, "ART_DIRECTOR_MODELS_PATH", tmp_art_models_path)

    # Изолируем SessionManager и связанные модули
    import services.session_manager as sm
    monkeypatch.setattr(sm, "HISTORY_DIR", tmp_history, raising=False)
    monkeypatch.setattr(sm, "OUTPUTS_DIR", tmp_outputs, raising=False)
    monkeypatch.setattr(SessionManager, "SESSIONS_DIR", tmp_history, raising=False)

    import services.rp_cast_manager as rcm
    monkeypatch.setattr(rcm, "HISTORY_DIR", tmp_history, raising=False)
    import services.rp_cast_extractor as rce
    monkeypatch.setattr(rce, "HISTORY_DIR", tmp_history, raising=False)

    import services.process_manager as pm
    monkeypatch.setattr(pm, "KV_SLOTS_DIR", tmp_kv_cache, raising=False)

    import services.sandbox_manager as sbm
    monkeypatch.setattr(sbm, "WORKSPACE_DIR", tmp_workspace, raising=False)

    import services.art_director_config as adc
    monkeypatch.setattr(adc, "ART_DIRECTOR_MODELS_PATH", tmp_art_models_path, raising=False)
    monkeypatch.setattr(adc, "DATA_DIR", tmp_data, raising=False)

    import services.model_router as mr
    monkeypatch.setattr(mr, "DEFAULT_DATA_PATH", tmp_art_models_path, raising=False)

    import ui.models_manager_dialog as mmd
    if hasattr(mmd, "ART_DIRECTOR_MODELS_PATH"):
        monkeypatch.setattr(mmd, "ART_DIRECTOR_MODELS_PATH", tmp_art_models_path, raising=False)

    # Изоляция memory_db
    import services.memory_db as mdb_mod
    if hasattr(mdb_mod, "memory_db") and mdb_mod.memory_db is not None:
        monkeypatch.setattr(mdb_mod.memory_db, "db_path", tmp_data / "memory.db", raising=False)

    # Изоляция ConfigManager
    monkeypatch.setattr(ConfigManager, "CONFIG_FILE", tmp_config_file, raising=False)
    monkeypatch.setattr(ConfigManager, "CONFIG_PATH", tmp_config_file, raising=False)
    ConfigManager._config_cache = None

    # Очищаем кэши перед тестом
    SessionManager._sessions_cache.clear()
    SessionManager._current_session = None
    ModelRouter._instance = None

    yield {
        "tmp_path": tmp_path,
        "data_dir": tmp_data,
        "history_dir": tmp_history,
        "workspace_dir": tmp_workspace,
        "outputs_dir": tmp_outputs,
        "kv_cache_dir": tmp_kv_cache,
        "config_file": tmp_config_file,
        "art_models_path": tmp_art_models_path,
    }

    # TEARDOWN: строгий сброс синглтонов и оперативных состояний
    inst = getattr(ModelRouter, "_instance", None)
    if inst and hasattr(inst, "cancel_deferred_warmup"):
        try:
            inst.cancel_deferred_warmup()
        except Exception:
            pass
    ModelRouter._instance = None

    SessionManager._sessions_cache.clear()
    SessionManager._current_session = None
    ConfigManager._config_cache = None

    if hasattr(SandboxManager, "set_active_session_id"):
        SandboxManager.set_active_session_id(None)
    if hasattr(ToolEngine, "set_active_session_id"):
        ToolEngine.set_active_session_id(None)

    import shutil
    shutil.rmtree(airgap_ws_base, ignore_errors=True)


# =========================================================================
# 3. ОБЩИЕ ФИКСТУРЫ И ФАБРИКИ МОКОВ
# =========================================================================

@pytest.fixture(scope="session")
def qapp():
    """Headless Qt Application fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def isolated_art_models_env(smoke_isolation_guard, monkeypatch):
    """
    Регистрирует эталонные изолированные модели SD 1.5 и SDXL в isolated art_director_models.json.
    Возвращает экземпляр ArtDirector с перезагруженными профилями.
    """
    from services.art_director.director import ArtDirector

    model_sd15 = {
        "id": "model_sd15_classic",
        "name": "CyberRealistic SD15",
        "architecture": "sd15",
        "checkpoint": "cyberrealistic_v50.safetensors",
        "is_default": True,
        "style_triggers": ["realistic", "photo", "dialogue"],
        "routing_description": "photorealistic portrait, natural light, dialogue",
        "active": True,
        "pipeline": {
            "architecture": "sd15",
            "sampler": "DPM++ 2M Karras",
            "scheduler": "Karras",
            "steps": 25,
            "cfg_scale": 6.0,
            "clip_skip": 1,
            "enable_hr": False,
            "enable_adetailer": True,
            "negative_prompt": "ugly, deformed, bad hands"
        }
    }

    model_sdxl = {
        "id": "model_sdxl_anime",
        "name": "CounterfeitXL",
        "architecture": "sdxl",
        "checkpoint": "counterfeitxl_v10.safetensors",
        "is_default": False,
        "style_triggers": ["anime", "illustration", "2d drawing"],
        "routing_description": "vibrant colors, anime screencap, cel shaded, Avoid booru/SD1.5 tags like",
        "active": True,
        "pipeline": {
            "architecture": "sdxl",
            "sampler": "DPM++ 2M Karras",
            "scheduler": "Karras",
            "steps": 28,
            "cfg_scale": 7.0,
            "clip_skip": 2,
            "enable_hr": False,
            "enable_adetailer": False,
            "negative_prompt": "worst quality, low quality, bad anatomy"
        }
    }

    save_art_director_models([model_sd15, model_sdxl])

    director = ArtDirector()
    director.reload_models()
    return director


class DummyWindow(QWidget, HardwareEngineMixin):
    """Headless window simulating UI synchronization with HardwareEngineMixin."""

    def __init__(self, art_director=None, forge_client=None, current_mode="roleplay"):
        super().__init__()
        self.art_director = art_director
        self.forge_client = forge_client
        self.available_forge_checkpoints = []
        self.current_session = {"id": "dummy_smoke_session"}
        self.current_mode = current_mode
        self.selected_forge_checkpoint = None

    def statusBar(self):
        return MagicMock()

    def _cancel_lms_reload(self):
        pass

    def _reload_active_lms_model_async(self, delay=1.2):
        pass

    def _refresh_forge_models(self):
        if self.forge_client and hasattr(self.forge_client, "get_models"):
            models = self.forge_client.get_models()
            titles = [m.get("title", "") for m in models if isinstance(m, dict)]
            self._on_forge_models_discovered(titles)
        else:
            self._on_forge_models_discovered([])

    def get_effective_mode(self):
        return self.current_mode


builtins.DummyWindow = DummyWindow


@pytest.fixture
def dummy_window_factory():
    """Фабрика для создания DummyWindow."""
    created_windows = []

    def _create(art_director=None, forge_client=None, current_mode="roleplay"):
        win = DummyWindow(art_director=art_director, forge_client=forge_client, current_mode=current_mode)
        created_windows.append(win)
        return win

    yield _create

    for w in created_windows:
        try:
            w.close()
            w.deleteLater()
        except Exception:
            pass


@pytest.fixture
def mock_lm_client():
    """Стандартный мок LM-клиента для смоук-тестов."""
    client = MagicMock()
    client._current_loaded_ctx = 8192
    client._last_loaded_model_id = "test-model-qwen"
    client.chat_completion.return_value = "Готовый ответ модели."
    client.is_server_ready.return_value = True
    return client


@pytest.fixture
def mock_forge_client():
    """Стандартный мок Forge-клиента для смоук-тестов."""
    client = MagicMock()
    client.is_server_ready.return_value = True
    client.get_models.return_value = [
        {"title": "cyberrealistic_v50.safetensors", "model_name": "cyberrealistic_v50"},
        {"title": "counterfeitxl_v10.safetensors", "model_name": "counterfeitxl_v10"}
    ]
    client.text_to_image.return_value = {"images": ["base64_data"], "parameters": {}}
    return client
