"""
tests/test_process_manager.py
Интеграционные тесты для ProcessManager и LayerBudgeter:
- Авторасчет -ngl через LayerBudgeter
- Защитный флаг --no-mmap при гибридном оффлоаде
- Блокировка запуска процесса при срабатывании Pre-flight RAM Guard
- Ручной оверрайд gpu_layers
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from services.process_manager import ProcessManager, ServerState
from services.layer_budgeter import LayerBudgeter


class TestProcessManagerLayerBudgeterIntegration:
    """Проверка интеграции LayerBudgeter в боевой цикл запуска ProcessManager."""

    @pytest.fixture
    def mock_env(self, tmp_path):
        """Фикстура изолированного окружения: бинарник, модель, мок Popen и HTTP handshake."""
        fake_exe = tmp_path / "llama-server.exe"
        fake_exe.touch()
        fake_model = tmp_path / "model.gguf"
        fake_model.touch()

        mock_popen = MagicMock()
        mock_popen.poll.return_value = None
        mock_popen.stdout.readline.return_value = ""

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        pm = ProcessManager()
        pm.lm_client = MagicMock()
        pm.inspect_port_occupant = MagicMock(return_value=(False, None, None))

        return {
            "pm": pm,
            "exe": fake_exe,
            "model": fake_model,
            "popen": mock_popen,
            "resp": mock_resp,
            "tmp": tmp_path,
        }

    def test_start_llama_server_auto_calls_layer_budgeter(self, mock_env):
        """При старте с gpu_layers='auto' вызывается LayerBudgeter, а -ngl в cmdline равен расчетному."""
        pm = mock_env["pm"]
        fake_exe = mock_env["exe"]
        fake_model = mock_env["model"]
        mock_popen = mock_env["popen"]

        audit_data = {
            "total_layers": 48,
            "model_size_mb": 8704.0,
            "is_pure_compute": True,
            "usable_vram_mb": 7100.0,
            "final_ngl": 34,
        }

        with patch("services.server_config.ServerConfigResolver.resolve_server_exe", return_value=fake_exe), \
             patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=fake_model), \
             patch("services.server_config.ServerConfigResolver.resolve_cuda_dir", return_value=mock_env["tmp"]), \
             patch("services.server_config.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
             patch("services.server_config.ServerConfigResolver.load_ini", return_value={"host": "127.0.0.1", "port": 1234, "default_context": 14336, "gpu_layers": 99}), \
             patch.object(LayerBudgeter, "calculate_layer_offload", return_value=(34, True, "OK", audit_data)) as mock_calc, \
             patch.object(LayerBudgeter, "log_audit_report") as mock_log, \
             patch("subprocess.Popen", return_value=mock_popen) as mock_spawn, \
             patch("requests.get", return_value=mock_env["resp"]), \
             patch.object(pm, "_kill_llama_server_instance"):

            ok = pm.start_llama_server_sync("model.gguf", gpu_layers="auto")

            assert ok is True
            mock_calc.assert_called_once()
            mock_log.assert_called_once_with(audit_data)

            # Проверяем переданные аргументы процесса
            spawn_cmd = mock_spawn.call_args[0][0]
            assert "-ngl" in spawn_cmd
            ngl_idx = spawn_cmd.index("-ngl")
            assert spawn_cmd[ngl_idx + 1] == "34"

    def test_hybrid_mode_guarantees_no_mmap_flag(self, mock_env):
        """В гибридном режиме (ngl < total_layers) флаг --no-mmap добавляется гарантированно, даже если no_mmap=False в INI."""
        pm = mock_env["pm"]
        fake_exe = mock_env["exe"]
        fake_model = mock_env["model"]
        mock_popen = mock_env["popen"]

        audit_data = {
            "total_layers": 48,
            "model_size_mb": 8704.0,
            "final_ngl": 32,
        }

        with patch("services.server_config.ServerConfigResolver.resolve_server_exe", return_value=fake_exe), \
             patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=fake_model), \
             patch("services.server_config.ServerConfigResolver.resolve_cuda_dir", return_value=mock_env["tmp"]), \
             patch("services.server_config.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
             patch("services.server_config.ServerConfigResolver.load_ini", return_value={"host": "127.0.0.1", "port": 1234, "default_context": 14336, "no_mmap": False, "gpu_layers": 99}), \
             patch.object(LayerBudgeter, "calculate_layer_offload", return_value=(32, True, "OK", audit_data)), \
             patch.object(LayerBudgeter, "log_audit_report"), \
             patch("subprocess.Popen", return_value=mock_popen) as mock_spawn, \
             patch("requests.get", return_value=mock_env["resp"]), \
             patch.object(pm, "_kill_llama_server_instance"):

            ok = pm.start_llama_server_sync("model.gguf", gpu_layers=None)

            assert ok is True
            spawn_cmd = mock_spawn.call_args[0][0]
            assert "--no-mmap" in spawn_cmd

    def test_preflight_oom_guard_blocks_process_launch(self, mock_env):
        """При can_run=False процесс не создается (Popen не вызывается), а сервер переходит в ServerState.ERROR."""
        pm = mock_env["pm"]
        fake_exe = mock_env["exe"]
        fake_model = mock_env["model"]

        audit_data = {
            "total_layers": 64,
            "model_size_mb": 35840.0,
            "final_ngl": 0,
            "can_run": False,
        }
        oom_reason = "OOM Guard: недостаточно VRAM + RAM для безопасного запуска"

        with patch("services.server_config.ServerConfigResolver.resolve_server_exe", return_value=fake_exe), \
             patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=fake_model), \
             patch("services.server_config.ServerConfigResolver.resolve_cuda_dir", return_value=mock_env["tmp"]), \
             patch("services.server_config.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
             patch("services.server_config.ServerConfigResolver.load_ini", return_value={"host": "127.0.0.1", "port": 1234, "default_context": 14336, "gpu_layers": 99}), \
             patch.object(LayerBudgeter, "calculate_layer_offload", return_value=(0, False, oom_reason, audit_data)), \
             patch.object(LayerBudgeter, "log_audit_report"), \
             patch("subprocess.Popen") as mock_spawn, \
             patch.object(pm, "_kill_llama_server_instance"):

            ok = pm.start_llama_server_sync("model.gguf")

            # Процесс не должен быть запущен!
            assert ok is False
            mock_spawn.assert_not_called()
            assert pm.server_state == ServerState.ERROR
            assert pm.last_error == oom_reason

    def test_manual_override_bypasses_layer_budgeter(self, mock_env):
        """При явном числовом значении gpu_layers (например 0 для CPU Only) расчет LayerBudgeter пропускается."""
        pm = mock_env["pm"]
        fake_exe = mock_env["exe"]
        fake_model = mock_env["model"]
        mock_popen = mock_env["popen"]

        with patch("services.server_config.ServerConfigResolver.resolve_server_exe", return_value=fake_exe), \
             patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=fake_model), \
             patch("services.server_config.ServerConfigResolver.resolve_cuda_dir", return_value=mock_env["tmp"]), \
             patch("services.server_config.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
             patch("services.server_config.ServerConfigResolver.load_ini", return_value={"host": "127.0.0.1", "port": 1234, "default_context": 14336, "gpu_layers": 99}), \
             patch.object(LayerBudgeter, "calculate_layer_offload") as mock_calc, \
             patch("subprocess.Popen", return_value=mock_popen) as mock_spawn, \
             patch("requests.get", return_value=mock_env["resp"]), \
             patch.object(pm, "_kill_llama_server_instance"):

            ok = pm.start_llama_server_sync("model.gguf", gpu_layers=0)

            assert ok is True
            mock_calc.assert_not_called()

            spawn_cmd = mock_spawn.call_args[0][0]
            ngl_idx = spawn_cmd.index("-ngl")
            assert spawn_cmd[ngl_idx + 1] == "0"



class TestProcessManagerPortConflictAndReadiness:
    """Тестирование защиты от коллизий портов (Port Conflict Guard) и валидации готовности."""

    def test_inspect_port_occupant_utility(self):
        """inspect_port_occupant корректно детектирует занятый порт через net_connections."""
        from services.process_manager import inspect_port_occupant

        mock_conn = MagicMock()
        mock_conn.laddr.port = 1234
        mock_conn.status = "LISTEN"
        mock_conn.pid = 9999

        mock_other_conn = MagicMock()
        mock_other_conn.laddr.port = 8080
        mock_other_conn.status = "LISTEN"
        mock_other_conn.pid = 1111

        mock_proc = MagicMock()
        mock_proc.name.return_value = "LM Studio.exe"

        with patch("psutil.net_connections", return_value=[mock_other_conn, mock_conn]), \
             patch("psutil.Process", return_value=mock_proc), \
             patch("services.process_manager.psutil.CONN_LISTEN", "LISTEN"):
            is_occ, pid, pname = inspect_port_occupant(1234)
            assert is_occ is True
            assert pid == 9999
            assert pname == "LM Studio.exe"

            # Не занятый порт 5555
            is_occ_free, pid_free, pname_free = inspect_port_occupant(5555)
            assert is_occ_free is False
            assert pid_free is None
            assert pname_free is None

    def test_port_conflict_third_party_blocks_process_launch(self, tmp_path):
        """Если порт занят сторонней программой (LM Studio), Popen не вызывается и возвращается ошибка."""
        fake_exe = tmp_path / "llama-server.exe"
        fake_exe.touch()
        fake_model = tmp_path / "model.gguf"
        fake_model.touch()

        pm = ProcessManager()
        pm._saved_tracked_pid = None

        with patch("services.server_config.ServerConfigResolver.resolve_server_exe", return_value=fake_exe), \
             patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=fake_model), \
             patch("services.server_config.ServerConfigResolver.load_ini", return_value={"host": "127.0.0.1", "port": 1234}), \
             patch.object(pm, "inspect_port_occupant", return_value=(True, 4567, "LM Studio.exe")), \
             patch("subprocess.Popen") as mock_spawn, \
             patch.object(pm, "_kill_llama_server_instance"):

            ok = pm.start_llama_server_sync("model.gguf")

            assert ok is False
            mock_spawn.assert_not_called()
            assert pm.server_state == ServerState.ERROR
            assert "Порт 1234 занят процессом 'LM Studio.exe' (PID: 4567). Закройте стороннее приложение." in pm.last_error

    def test_port_conflict_own_tracked_server_triggers_taskkill(self, tmp_path):
        """Если порт занят собственным зависшим процессом (PID совпадает с tracked_pid), вызывается taskkill и запуск продолжается."""
        fake_exe = tmp_path / "llama-server.exe"
        fake_exe.touch()
        fake_model = tmp_path / "model.gguf"
        fake_model.touch()

        pm = ProcessManager()
        pm._saved_tracked_pid = 7890

        mock_popen = MagicMock()
        mock_popen.poll.return_value = None
        mock_popen.stdout.readline.return_value = ""

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "model"}]}

        with patch("services.server_config.ServerConfigResolver.resolve_server_exe", return_value=fake_exe), \
             patch("services.server_config.ServerConfigResolver.resolve_model_path", return_value=fake_model),              patch("services.server_config.ServerConfigResolver.resolve_cuda_dir", return_value=tmp_path), \
             patch("services.server_config.ServerConfigResolver.resolve_mmproj_for_model", return_value=None), \
             patch("services.server_config.ServerConfigResolver.load_ini", return_value={"host": "127.0.0.1", "port": 1234, "gpu_layers": 0}), \
             patch.object(pm, "inspect_port_occupant", return_value=(True, 7890, "llama-server.exe")), \
             patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen", return_value=mock_popen) as mock_spawn, \
             patch("requests.get", return_value=mock_resp), \
             patch.object(pm, "_kill_llama_server_instance"):

            ok = pm.start_llama_server_sync("model.gguf", gpu_layers=0)

            assert ok is True
            # Проверяем вызов taskkill для нашего tracked_pid 7890
            killed_cmds = [call.args[0] for call in mock_run.call_args_list if call.args]
            assert any("taskkill" in cmd and "7890" in cmd for cmd in killed_cmds)
            mock_spawn.assert_called_once()
            assert pm.server_state == ServerState.READY

    def test_early_process_crash_aborts_wait_immediately(self):
        """Если процесс llama-server упал (poll() != None), опрос прерывается мгновенно с кодом ошибки."""
        import time
        pm = ProcessManager()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Crash with exit code 1
        pm._process = mock_proc

        start_time = time.time()
        ok = pm._wait_for_server_ready(host="127.0.0.1", port=1234, timeout_s=10.0)
        elapsed = time.time() - start_time

        assert ok is False
        assert elapsed < 1.0  # Не ждал 10 секунд таймаута
        assert pm.server_state == ServerState.ERROR
        assert "Процесс llama-server завершился с кодом 1" in pm.last_error

    def test_wait_for_server_ready_rejects_empty_models(self):
        """Если /v1/models возвращает 200 OK, но data=[] (пустой список), сервер не считается готовым."""
        pm = ProcessManager()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        pm._process = mock_proc

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}

        with patch("requests.get", return_value=mock_resp):
            ok = pm._wait_for_server_ready(host="127.0.0.1", port=1234, timeout_s=0.4)
            assert ok is False

    def test_wait_for_server_ready_accepts_nonempty_models(self):
        """Если /v1/models возвращает непустой список в data, готовность подтверждается."""
        pm = ProcessManager()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        pm._process = mock_proc

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "qwen2.5-coder-7b"}]}

        with patch("requests.get", return_value=mock_resp):
            ok = pm._wait_for_server_ready(host="127.0.0.1", port=1234, timeout_s=1.0)
            assert ok is True
