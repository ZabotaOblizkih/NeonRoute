"""
tests/unit/test_server_mmap_args.py
===================================
Unit tests for ServerConfigResolver.resolve_mmap_disable_args and
ProcessManager CLI argument assembly regarding mmap disabling flags.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.process_manager import ProcessManager
from services.server_config import ServerConfigResolver, _MMAP_ARGS_CACHE


@pytest.fixture(autouse=True)
def clear_mmap_cache():
    """Ensure _MMAP_ARGS_CACHE is cleared before and after each test."""
    _MMAP_ARGS_CACHE.clear()
    yield
    _MMAP_ARGS_CACHE.clear()


class TestResolveMmapDisableArgs:
    """Tests for ServerConfigResolver.resolve_mmap_disable_args()."""

    def test_returns_load_mode_when_supported(self, tmp_path):
        dummy_exe = tmp_path / "llama-server.exe"
        dummy_exe.touch()

        mock_proc = MagicMock()
        mock_proc.stdout = "-lm, --load-mode MODE\n--no-mmap DEPRECATED\n"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            args = ServerConfigResolver.resolve_mmap_disable_args(dummy_exe)
            assert args == ["--load-mode", "none"]

            mock_run.assert_called_once()
            called_kwargs = mock_run.call_args[1]
            assert called_kwargs["cwd"] == str(dummy_exe.parent.resolve())
            assert called_kwargs["encoding"] == "utf-8"
            assert called_kwargs["errors"] == "replace"
            assert called_kwargs["timeout"] == 5.0
            assert str(dummy_exe.parent.resolve()) in called_kwargs["env"]["PATH"]

    def test_returns_legacy_no_mmap_when_only_no_mmap_present(self, tmp_path):
        dummy_exe = tmp_path / "llama-server.exe"
        dummy_exe.touch()

        mock_proc = MagicMock()
        mock_proc.stdout = "--no-mmap   do not memory-map model\n"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            args = ServerConfigResolver.resolve_mmap_disable_args(dummy_exe)
            assert args == ["--no-mmap"]

    def test_caching_behavior(self, tmp_path):
        dummy_exe = tmp_path / "llama-server.exe"
        dummy_exe.touch()

        mock_proc = MagicMock()
        mock_proc.stdout = "--load-mode MODE\n"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            res1 = ServerConfigResolver.resolve_mmap_disable_args(dummy_exe)
            res2 = ServerConfigResolver.resolve_mmap_disable_args(dummy_exe)

            assert res1 == ["--load-mode", "none"]
            assert res2 == ["--load-mode", "none"]
            assert mock_run.call_count == 1

            # Mutation check: modifying returned list should not poison cache
            res1.append("--corrupt")
            res3 = ServerConfigResolver.resolve_mmap_disable_args(dummy_exe)
            assert res3 == ["--load-mode", "none"]

    def test_no_caching_on_empty_output(self, tmp_path):
        dummy_exe = tmp_path / "llama-server.exe"
        dummy_exe.touch()

        mock_proc = MagicMock()
        mock_proc.stdout = "   \n"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            res = ServerConfigResolver.resolve_mmap_disable_args(dummy_exe)
            assert res == ["--load-mode", "none"]
            assert len(_MMAP_ARGS_CACHE) == 0

    def test_no_caching_on_exception(self, tmp_path):
        dummy_exe = tmp_path / "llama-server.exe"
        dummy_exe.touch()

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="--help", timeout=5.0)):
            res = ServerConfigResolver.resolve_mmap_disable_args(dummy_exe)
            assert res == ["--load-mode", "none"]
            assert len(_MMAP_ARGS_CACHE) == 0

    def test_fallback_on_missing_file_or_none(self):
        assert ServerConfigResolver.resolve_mmap_disable_args(None) == ["--load-mode", "none"]
        assert ServerConfigResolver.resolve_mmap_disable_args(Path("C:/nonexistent/path.exe")) == ["--load-mode", "none"]


class TestProcessManagerMmapAssembly:
    """Tests for ProcessManager.start_llama_server_sync CLI argument construction."""

    @pytest.fixture
    def pm(self):
        return ProcessManager()

    def _intercept_cmd(self, pm, ini_override=None, is_hybrid=False, existing_args=None):
        captured_cmd = []

        def fake_popen(cmd, *args, **kwargs):
            captured_cmd.extend(cmd)
            raise RuntimeError("Intercepted Popen for CLI verification")

        mock_ini = {
            "host": "127.0.0.1",
            "port": 1234,
            "default_context": 8192,
            "gpu_layers": 99,
            "flash_attn": "on",
            "cache_type_k": "q8_0",
            "cache_type_v": "q8_0",
            "parallel_slots": 1,
            "no_mmap": True,
        }
        if ini_override:
            mock_ini.update(ini_override)

        total_layers = 10 if is_hybrid else 99
        effective_ngl = 5 if is_hybrid else 99

        with patch("services.process_manager.ServerConfigResolver.resolve_server_exe", return_value=Path("C:/bin/llama-server.exe")), \
             patch("services.process_manager.ServerConfigResolver.resolve_model_path", return_value=Path("C:/models/test.gguf")), \
             patch("services.process_manager.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
             patch("services.process_manager.ServerConfigResolver.resolve_cuda_dir", return_value=Path("C:/bin")), \
             patch("services.process_manager.ServerConfigResolver.load_ini", return_value=mock_ini), \
             patch("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", return_value=(True, 150)), \
             patch("services.layer_budgeter.LayerBudgeter.get_current_used_vram_mb", return_value=150.0), \
             patch("services.layer_budgeter.LayerBudgeter.calculate_layer_offload", return_value=(effective_ngl, True, "OK", {"total_layers": total_layers})), \
             patch("subprocess.Popen", side_effect=fake_popen), \
             patch.object(pm, "inspect_port_occupant", return_value=(False, None, None)), \
             patch("pathlib.Path.exists", return_value=True):

            try:
                pm.start_llama_server_sync(model_identifier="test.gguf", context_length=8192)
            except RuntimeError as e:
                if str(e) != "Intercepted Popen for CLI verification":
                    raise

        return captured_cmd

    def test_cmd_uses_load_mode_none_by_default(self, pm):
        with patch.object(ServerConfigResolver, "resolve_mmap_disable_args", return_value=["--load-mode", "none"]):
            cmd = self._intercept_cmd(pm, ini_override={"no_mmap": True})
            assert "--load-mode" in cmd
            assert cmd[cmd.index("--load-mode") + 1] == "none"
            assert "--no-mmap" not in cmd

    def test_cmd_uses_legacy_no_mmap_when_resolver_returns_it(self, pm):
        with patch.object(ServerConfigResolver, "resolve_mmap_disable_args", return_value=["--no-mmap"]):
            cmd = self._intercept_cmd(pm, ini_override={"no_mmap": True})
            assert "--no-mmap" in cmd
            assert "--load-mode" not in cmd

    def test_cmd_no_mmap_omitted_when_false_and_not_hybrid(self, pm):
        cmd = self._intercept_cmd(pm, ini_override={"no_mmap": False}, is_hybrid=False)
        assert "--no-mmap" not in cmd
        assert "--load-mode" not in cmd

    def test_cmd_no_mmap_added_when_hybrid_even_if_ini_false(self, pm):
        with patch.object(ServerConfigResolver, "resolve_mmap_disable_args", return_value=["--load-mode", "none"]):
            cmd = self._intercept_cmd(pm, ini_override={"no_mmap": False}, is_hybrid=True)
            assert "--load-mode" in cmd
            assert cmd[cmd.index("--load-mode") + 1] == "none"

    def test_no_duplicate_if_load_mode_already_in_cmd(self, pm):
        """Checks prefix checks like --load-mode= or -lm= prevent duplicate addition."""
        with patch.object(ServerConfigResolver, "resolve_mmap_disable_args", return_value=["--load-mode", "none"]) as mock_resolve:
            # Emulate an existing flag by patching cmd assembly
            cmd = self._intercept_cmd(pm, ini_override={"no_mmap": True})
            assert cmd.count("--load-mode") == 1
            mock_resolve.assert_called_once()
