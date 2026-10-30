"""
tests/test_llm_engine_settings.py
Тесты аппаратного автотюна KV-кэша, Flash Attention, диалога настроек движка LLM
и проброса CLI-параметров в ProcessManager.
"""

import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from services.layer_budgeter import LayerBudgeter
from services.process_manager import ProcessManager, ServerState
from utils.config_manager import ConfigManager, DEFAULT_SETTINGS


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


class TestLayerBudgeterHardwareKVProfile:
    """Проверка детектора архитектуры GPU и расчета KV-кэша."""

    def test_detect_hardware_kv_profile_turing_plus(self):
        """Для чипов Turing+ (CC >= 7.5) рекомендуется q8_0 и Flash Attention = True."""
        mock_handle = MagicMock()

        with patch("pynvml.nvmlInit"), \
             patch("pynvml.nvmlDeviceGetHandleByIndex", return_value=mock_handle), \
             patch("pynvml.nvmlDeviceGetName", return_value=b"NVIDIA GeForce RTX 5050 Laptop GPU"), \
             patch("pynvml.nvmlDeviceGetCudaComputeCapability", return_value=(12, 0)):

            profile = LayerBudgeter.detect_hardware_kv_profile()

            assert profile["supported"] is True
            assert profile["recommended_kv"] == "q8_0"
            assert profile["recommended_fa"] is True
            assert profile["compute_cap"] == "12.0"
            assert "RTX 5050" in profile["gpu_name"]

    def test_detect_hardware_kv_profile_legacy_gtx(self):
        """Для старых чипов (GTX 1060, CC 6.1 < 7.5) выставляется FP16 и Flash Attention = False."""
        mock_handle = MagicMock()

        with patch("pynvml.nvmlInit"), \
             patch("pynvml.nvmlDeviceGetHandleByIndex", return_value=mock_handle), \
             patch("pynvml.nvmlDeviceGetName", return_value="GeForce GTX 1060 6GB"), \
             patch("pynvml.nvmlDeviceGetCudaComputeCapability", return_value=(6, 1)):

            profile = LayerBudgeter.detect_hardware_kv_profile()

            assert profile["supported"] is False
            assert profile["recommended_kv"] == "f16"
            assert profile["recommended_fa"] is False
            assert profile["compute_cap"] == "6.1"

    def test_detect_hardware_kv_profile_nvml_error_fallback(self):
        """При ошибке NVML выполняется безопасный фоллбэк на FP16 и FA=False."""
        with patch("pynvml.nvmlInit", side_effect=Exception("NVML Init Failed")):
            profile = LayerBudgeter.detect_hardware_kv_profile()

            assert profile["supported"] is False
            assert profile["recommended_kv"] == "f16"
            assert profile["recommended_fa"] is False
            assert profile["compute_cap"] == "N/A"
            assert profile["gpu_name"] == "Unknown GPU"

    def test_estimate_kv_cache_mb_proportions(self):
        """Проверка формулы расчета KV-кэша: 36 КБ (q4_0), 64 КБ (q8_0), 128 КБ (f16) + 15% CUDA overhead."""
        ctx = 14336

        expected_q4 = int(math.ceil((ctx * 36 / 1024.0) * 1.15))
        expected_q8 = int(math.ceil((ctx * 64 / 1024.0) * 1.15))
        expected_f16 = int(math.ceil((ctx * 128 / 1024.0) * 1.15))

        calc_q4 = LayerBudgeter.estimate_kv_cache_mb(ctx, cache_type="q4_0")
        calc_q8 = LayerBudgeter.estimate_kv_cache_mb(ctx, cache_type="q8_0")
        calc_f16 = LayerBudgeter.estimate_kv_cache_mb(ctx, cache_type="f16")

        assert calc_q4 == expected_q4
        assert calc_q8 == expected_q8
        assert calc_f16 == expected_f16

        # Проверка пропорций: q4 заметно легче q8, а f16 ровно вдвое тяжелее q8
        assert calc_q4 < calc_q8 < calc_f16
        assert math.isclose(calc_f16 / calc_q8, 2.0, rel_tol=0.02)


class TestConfigDefaultsAndMerging:
    """Проверка конфигурационных констант по умолчанию и слияния."""

    def test_default_settings_contain_hardware_kv_keys(self):
        assert "hardware_autotune_done" in DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["hardware_autotune_done"] is False
        assert "kv_cache_type" in DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["kv_cache_type"] == "q8_0"
        assert "flash_attention_enabled" in DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["flash_attention_enabled"] is True

    def test_load_config_merges_defaults(self, tmp_path, monkeypatch):
        from utils import config_manager
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text('{"theme": "dark"}', encoding="utf-8")
        monkeypatch.setattr(config_manager, "CONFIG_PATH", cfg_file)

        loaded = ConfigManager.load_config()
        assert loaded["theme"] == "dark"
        assert loaded["hardware_autotune_done"] is False
        assert loaded["kv_cache_type"] == "q8_0"
        assert loaded["flash_attention_enabled"] is True


class TestProcessManagerCLIArgumentPassing:
    """Проверка проброса параметров -ctk, -ctv и -fa в CLI llama-server."""

    def test_start_llama_server_passes_q4_0_and_fa_on(self, tmp_path):
        fake_exe = tmp_path / "llama-server.exe"
        fake_exe.touch()
        fake_model = tmp_path / "model.gguf"
        fake_model.touch()

        pm = ProcessManager()
        pm.inspect_port_occupant = MagicMock(return_value=(False, None, None))
        pm.lm_client = MagicMock()

        mock_popen = MagicMock()
        mock_popen.poll.return_value = None
        mock_popen.stdout.readline.return_value = ""

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "model"}]}

        app_cfg_mock = {
            "kv_cache_type": "q4_0",
            "flash_attention_enabled": True,
            "active_model": "model.gguf",
        }

        with patch("services.server_config.ServerConfigResolver.resolve_server_exe", return_value=fake_exe), \
             patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=fake_model), \
             patch("services.server_config.ServerConfigResolver.resolve_cuda_dir", return_value=tmp_path), \
             patch("services.server_config.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
             patch("services.server_config.ServerConfigResolver.load_ini", return_value={"host": "127.0.0.1", "port": 1234, "gpu_layers": 99}), \
             patch("utils.config_manager.ConfigManager.load_config", return_value=app_cfg_mock), \
             patch.object(LayerBudgeter, "calculate_layer_offload", return_value=(99, True, "OK", {"total_layers": 99})), \
             patch.object(LayerBudgeter, "log_audit_report"), \
             patch("subprocess.Popen", return_value=mock_popen) as mock_spawn, \
             patch("requests.get", return_value=mock_resp), \
             patch.object(pm, "_kill_llama_server_instance"):

            ok = pm.start_llama_server_sync("model.gguf")
            assert ok is True

            cmd = mock_spawn.call_args[0][0]
            assert "-ctk" in cmd
            assert cmd[cmd.index("-ctk") + 1] == "q4_0"
            assert "-ctv" in cmd
            assert cmd[cmd.index("-ctv") + 1] == "q4_0"
            assert "-fa" in cmd
            assert cmd[cmd.index("-fa") + 1] == "on"

    def test_start_llama_server_passes_f16_and_fa_off(self, tmp_path):
        fake_exe = tmp_path / "llama-server.exe"
        fake_exe.touch()
        fake_model = tmp_path / "model.gguf"
        fake_model.touch()

        pm = ProcessManager()
        pm.inspect_port_occupant = MagicMock(return_value=(False, None, None))
        pm.lm_client = MagicMock()

        mock_popen = MagicMock()
        mock_popen.poll.return_value = None
        mock_popen.stdout.readline.return_value = ""

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "model"}]}

        app_cfg_mock = {
            "kv_cache_type": "f16",
            "flash_attention_enabled": False,
            "active_model": "model.gguf",
        }

        with patch("services.server_config.ServerConfigResolver.resolve_server_exe", return_value=fake_exe), \
             patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=fake_model), \
             patch("services.server_config.ServerConfigResolver.resolve_cuda_dir", return_value=tmp_path), \
             patch("services.server_config.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
             patch("services.server_config.ServerConfigResolver.load_ini", return_value={"host": "127.0.0.1", "port": 1234, "gpu_layers": 99}), \
             patch("utils.config_manager.ConfigManager.load_config", return_value=app_cfg_mock), \
             patch.object(LayerBudgeter, "calculate_layer_offload", return_value=(99, True, "OK", {"total_layers": 99})), \
             patch.object(LayerBudgeter, "log_audit_report"), \
             patch("subprocess.Popen", return_value=mock_popen) as mock_spawn, \
             patch("requests.get", return_value=mock_resp), \
             patch.object(pm, "_kill_llama_server_instance"):

            ok = pm.start_llama_server_sync("model.gguf")
            assert ok is True

            cmd = mock_spawn.call_args[0][0]
            assert "-ctk" in cmd
            assert cmd[cmd.index("-ctk") + 1] == "f16"
            assert "-ctv" in cmd
            assert cmd[cmd.index("-ctv") + 1] == "f16"
            assert "-fa" in cmd
            assert cmd[cmd.index("-fa") + 1] == "off"


class TestLLMEngineSettingsDialogAndAutotune:
    """Проверка диалога настроек и контроллера аппаратного автотюна."""

    def test_dialog_reactive_hint_card(self, qapp):
        from ui.dialogs.llm_engine_settings_dialog import LLMEngineSettingsDialog

        dlg = LLMEngineSettingsDialog()

        # По умолчанию q8_0
        dlg.kv_combo.setCurrentIndex(0)
        assert "Q8_0" in dlg.hint_title_lbl.text()
        assert "50%" in dlg.hint_budget_lbl.text()

        # Переключение на Q4_0
        dlg.kv_combo.setCurrentIndex(1)
        assert "Q4_0" in dlg.hint_title_lbl.text()
        assert "75%" in dlg.hint_budget_lbl.text()
        assert "36 КБ" in dlg.hint_budget_lbl.text()

        # Переключение на FP16
        dlg.kv_combo.setCurrentIndex(2)
        assert "FP16" in dlg.hint_title_lbl.text()
        assert "128 КБ" in dlg.hint_budget_lbl.text()
        assert "0%" in dlg.hint_budget_lbl.text()

        dlg.close()

    def test_dialog_save_persists_settings_and_triggers_restart(self, qapp, tmp_path, monkeypatch):
        from ui.dialogs.llm_engine_settings_dialog import LLMEngineSettingsDialog
        from utils import config_manager

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text('{"kv_cache_type": "q8_0", "flash_attention_enabled": true}', encoding="utf-8")
        monkeypatch.setattr(config_manager, "CONFIG_PATH", cfg_file)

        mock_parent = MagicMock()
        mock_pm = MagicMock()
        mock_pm.is_lm_studio_running.return_value = True
        mock_parent.process_manager = mock_pm

        dlg = LLMEngineSettingsDialog(parent=mock_parent)

        # Выбираем Q4_0 и отключаем FA
        dlg.kv_combo.setCurrentIndex(1)
        dlg.switch_fa.setChecked(False)

        dlg._on_save_clicked()

        saved = ConfigManager.load_config()
        assert saved["kv_cache_type"] == "q4_0"
        assert saved["flash_attention_enabled"] is False
        mock_pm.restart_llama_server_async.assert_called_once()

    def test_perform_hardware_kv_autotune_applies_profile(self, qapp, tmp_path, monkeypatch):
        from ui.controllers.hardware_engine import HardwareEngineMixin
        from utils import config_manager

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text('{"hardware_autotune_done": false}', encoding="utf-8")
        monkeypatch.setattr(config_manager, "CONFIG_PATH", cfg_file)

        class MockMainWindow(HardwareEngineMixin):
            def __init__(self):
                self.app_config = {}

        win = MockMainWindow()

        fake_profile = {
            "supported": True,
            "gpu_name": "NVIDIA GeForce RTX 5050",
            "compute_cap": "12.0",
            "recommended_kv": "q8_0",
            "recommended_fa": True
        }

        with patch.object(LayerBudgeter, "detect_hardware_kv_profile", return_value=fake_profile), \
             patch("qfluentwidgets.InfoBar.success") as mock_infobar:

            win._perform_hardware_kv_autotune()

            saved = ConfigManager.load_config()
            assert saved["hardware_autotune_done"] is True
            assert saved["kv_cache_type"] == "q8_0"
            assert saved["flash_attention_enabled"] is True
            mock_infobar.assert_called_once()
