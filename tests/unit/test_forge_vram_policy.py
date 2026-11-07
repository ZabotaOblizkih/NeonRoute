"""
Unit tests for Forge VRAM offload policy and launch argument safety on <= 8 GB GPU.
"""

from unittest.mock import MagicMock, patch
from pathlib import Path
from PyQt6.QtWidgets import QApplication

from services.process_manager import ProcessManager
from services.art_director.vram_policy import resolve_forge_offload_policy


def test_forge_launch_cmd_contains_always_offload_on_8gb(tmp_path):
    """
    При подготовке команды запуска Forge на системе с 8 ГБ VRAM
    аргумент --always-offload-from-vram обязан присутствовать в cmd.
    """
    app = QApplication.instance() or QApplication([])
    fake_launcher = tmp_path / "fake_forge" / "forge_launcher.bat"
    fake_launcher.parent.mkdir(parents=True, exist_ok=True)
    fake_launcher.write_text("@echo off\nexit /b 0\n", encoding="utf-8")

    pm = ProcessManager()
    pm.is_port_open = MagicMock(side_effect=[False, True])

    mock_proc = MagicMock()
    mock_proc.pid = 8888
    mock_proc.poll.return_value = None
    mock_proc.stdout.readline.return_value = ""

    with patch("services.process_manager.FORGE_LAUNCHER_PATH", fake_launcher), \
         patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)), \
         patch("services.asset_validator.AssetValidator.log_validation_summary"), \
         patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
         patch("builtins.open", MagicMock()):

        pm._run_forge_start()

        assert mock_popen.called
        forge_calls = [
            c for c in mock_popen.call_args_list
            if c.args and isinstance(c.args[0], list) and any("forge_launcher.bat" in str(x) for x in c.args[0])
        ]
        assert forge_calls, f"Вызов forge_launcher.bat не найден среди вызовов Popen: {mock_popen.call_args_list}"
        cmd = forge_calls[0].args[0]
        assert "--always-offload-from-vram" in cmd
        assert "--api" in cmd
        assert "--port" in cmd
        assert "7860" in cmd


def test_resolve_forge_offload_policy_strict_8gb():
    """
    Функция resolve_forge_offload_policy при общем бюджете <= 8192 МБ
    гарантированно возвращает always_offload=True даже для архитектуры sd15.
    """
    with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(8.0, 700)):
        # 1. Для базовой sd15 без замеров свободной памяти
        pol_sd15 = resolve_forge_offload_policy(architecture="sd15")
        assert pol_sd15["always_offload"] is True
        assert pol_sd15["can_run"] is True

        # 2. Для sd15 с флагами FP8 и ADetailer
        pol_sd15_full = resolve_forge_offload_policy(architecture="sd15", is_fp8=True, enable_adetailer=True)
        assert pol_sd15_full["always_offload"] is True
        assert pol_sd15_full["can_run"] is True

        # 3. Для общего бюджета 6 ГБ (ноутбучные карты)
        with patch("services.art_director.vram_policy.resolve_hardware_vram_budget", return_value=(6.0, 700)):
            pol_6gb = resolve_forge_offload_policy(architecture="sd15")
            assert pol_6gb["always_offload"] is True
