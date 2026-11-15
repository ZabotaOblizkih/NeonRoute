"""
tests/unit/test_seamless_onboarding_and_models.py
=================================================
Тестирование одноразового онбординга SD WebUI Forge и автоподхвата .gguf моделей из models\\.
"""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from utils.config_manager import ConfigManager
from services.server_config import ServerConfigResolver


class DummyParent:
    def __init__(self):
        self.app_config = {}
        self.system_monitor = MagicMock()
        self.prompt_locate_forge_called = False

    def prompt_locate_forge(self):
        self.prompt_locate_forge_called = True


def test_forge_onboarding_skips_when_pytest():
    """Проверка, что онбординг сразу возвращается, если выполняется pytest."""
    from ui.controllers.hardware_engine import HardwareEngineMixin

    obj = DummyParent()
    HardwareEngineMixin._check_forge_onboarding(obj)
    assert obj.prompt_locate_forge_called is False


def test_forge_onboarding_skips_on_amd_and_low_vram():
    """Проверка, что онбординг НЕ показывается на AMD или при VRAM < 3800 МБ."""
    from ui.controllers.hardware_engine import HardwareEngineMixin

    obj = DummyParent()
    obj.system_monitor.nvml_initialized = False

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PYTEST_CURRENT_TEST", None)

        # 1. AMD GPU (nvml_initialized = False)
        with patch.object(ConfigManager, "load_config", return_value={"forge_onboarding_shown": False}), \
             patch("core.forge_locator.is_valid_forge_dir", return_value=False):
            HardwareEngineMixin._check_forge_onboarding(obj)
            assert obj.prompt_locate_forge_called is False

        # 2. NVIDIA но VRAM < 3800 (например 2048 МБ)
        obj.system_monitor.nvml_initialized = True
        obj.system_monitor.get_total_vram_mb.return_value = 2048
        profile_low = {"vendor": "nvidia", "compute_cap": "7.5", "gpu_name": "NVIDIA GTX 1050"}

        with patch.object(ConfigManager, "load_config", return_value={"forge_onboarding_shown": False}), \
             patch("core.forge_locator.is_valid_forge_dir", return_value=False), \
             patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value=profile_low):
            HardwareEngineMixin._check_forge_onboarding(obj)
            assert obj.prompt_locate_forge_called is False


def test_forge_onboarding_triggers_once_for_nvidia_4gb_and_above():
    """Проверка, что онбординг срабатывает 1 раз на NVIDIA 4 ГБ (>=3800 МБ) и помечает флаг."""
    from ui.controllers.hardware_engine import HardwareEngineMixin

    obj = DummyParent()
    obj.system_monitor.nvml_initialized = True
    obj.system_monitor.get_total_vram_mb.return_value = 4096
    profile_4gb = {"vendor": "nvidia", "compute_cap": "7.5", "gpu_name": "NVIDIA RTX 3050"}

    mock_dialog_inst = MagicMock()
    mock_dialog_inst.choice = "text_only"
    mock_dialog_cls = MagicMock(return_value=mock_dialog_inst)

    saved_settings = {}

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PYTEST_CURRENT_TEST", None)

        with patch.object(ConfigManager, "load_config", return_value={"forge_onboarding_shown": False}), \
             patch.object(ConfigManager, "update_setting", side_effect=lambda k, v: saved_settings.update({k: v})), \
             patch("core.forge_locator.is_valid_forge_dir", return_value=False), \
             patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value=profile_4gb), \
             patch("ui.dialogs.forge_onboarding_dialog.ForgeOnboardingDialog", mock_dialog_cls), \
             patch("ui.common.toast_helper.toast_info", MagicMock()):

            HardwareEngineMixin._check_forge_onboarding(obj)

            assert mock_dialog_cls.called
            assert saved_settings.get("forge_onboarding_shown") is True


def test_auto_select_first_gguf_from_models(tmp_path):
    """
    Проверка, что если в models\\ лежит .gguf, а конфиг пуст,
    ServerConfigResolver разрешает первую модель, а UI синхронизирует active_model.
    """
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    fake_gguf = models_dir / "Mistral-7B-Instruct-v0.3.Q4_K_M.gguf"
    fake_gguf.write_bytes(b"GGUF" + b"\x00" * 1024)

    # 1. ServerConfigResolver находит модель
    with patch("config.LOCAL_MODELS_DIR", models_dir):
        discovered = ServerConfigResolver.discover_gguf_models(include_lmstudio=False)
        assert len(discovered) == 1
        assert "Mistral-7B-Instruct-v0.3.Q4_K_M" in discovered

        # 2. Резолв при пустом идентификаторе возвращает первую найденную модель
        with patch.object(ServerConfigResolver, "discover_gguf_models", return_value=discovered):
            resolved = ServerConfigResolver.resolve_model_path(None)
            assert resolved == fake_gguf.resolve()

            # 3. Резолв при отсутствующей в конфиге старой модели тоже возвращает первую модель
            resolved_missing = ServerConfigResolver.resolve_model_path("deleted_model_qwen.gguf")
            assert resolved_missing == fake_gguf.resolve()
