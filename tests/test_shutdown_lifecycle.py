import pytest
from unittest.mock import MagicMock, patch
from services.process_manager import ProcessManager


def test_shutdown_all_terminates_forge_first_then_unloads_lms():
    """Проверяет, что активный процесс Forge глушится через taskkill /F /T ДО выгрузки LM Studio."""
    pm = ProcessManager()
    fake_proc = MagicMock()
    fake_proc.pid = 12345
    pm._forge_process = fake_proc

    execution_order = []

    def fake_subprocess_run(cmd, *args, **kwargs):
        if "taskkill" in cmd and "/T" in cmd and str(fake_proc.pid) in cmd:
            execution_order.append("forge_taskkill")
        return MagicMock(returncode=0)

    def fake_unload_lms():
        execution_order.append("lms_unload")

    with patch("services.process_manager.subprocess.run", side_effect=fake_subprocess_run), \
         patch.object(pm, "unload_all_lms_models", side_effect=fake_unload_lms) as mock_unload, \
         patch("psutil.process_iter", return_value=[]):
        pm.shutdown_all()

    assert execution_order == ["forge_taskkill", "lms_unload"]
    assert pm._forge_process is None
    mock_unload.assert_called_once()


def test_shutdown_all_sweeps_residual_forge_processes():
    """Проверяет, что оторванные/зомби-процессы Forge зачищаются через taskkill /F /T."""
    pm = ProcessManager()
    pm._forge_process = None

    mock_proc = MagicMock()
    mock_proc.pid = 9999
    mock_proc.info = {
        "pid": 9999,
        "name": "python.exe",
        "cmdline": ["python", "C:\\neyroGPD\\webui\\launch.py", "--port", "7860"]
    }

    execution_order = []

    def fake_subprocess_run(cmd, *args, **kwargs):
        if "taskkill" in cmd and "/T" in cmd and "9999" in cmd:
            execution_order.append("residual_taskkill")
        return MagicMock(returncode=0)

    def fake_unload_lms():
        execution_order.append("lms_unload")

    with patch("services.process_manager.subprocess.run", side_effect=fake_subprocess_run), \
         patch.object(pm, "unload_all_lms_models", side_effect=fake_unload_lms) as mock_unload, \
         patch("psutil.process_iter", return_value=[mock_proc]):
        pm.shutdown_all()

    assert execution_order == ["residual_taskkill", "lms_unload"]
    mock_unload.assert_called_once()


def test_shutdown_worker_calls_shutdown_all():
    """Проверяет, что ShutdownWorker вызывает shutdown_all у ProcessManager."""
    from ui.workers.shutdown_worker import ShutdownWorker

    mock_main_window = MagicMock()
    worker = ShutdownWorker(mock_main_window)

    with patch("time.sleep"), \
         patch("services.memory_db.memory_db.close"), \
         patch("utils.config_manager.ConfigManager.save_config"), \
         patch("threading.Thread"):
        worker.run()

    mock_main_window.process_manager.shutdown_all.assert_called_once()
