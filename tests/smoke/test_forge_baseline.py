"""
Smoke test suite fixing baseline contracts for SD WebUI Forge before refactoring.
Reflects current (pre-refactoring) behavior:
1. config path resolution (FORGE_ROOT_DIR, FORGE_LAUNCHER_PATH, FORGE_LORA_DIRS).
2. ProcessManager launch command assembly and working directory.
3. ProcessManager cleanup filtering on shutdown/stop.
4. UI Dialog default paths (CivitaiCatalogDialog).
"""

import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtWidgets import QApplication

import config
from services.process_manager import ProcessManager


@pytest.fixture(scope="session")
def qapp():
    """Headless QApplication fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_config_forge_paths_baseline(monkeypatch):
    """
    Проверяет контракт путей Forge в config.py:
    - FORGE_ROOT_DIR и FORGE_LAUNCHER_PATH являются объектами Path
    - FORGE_LORA_DIRS и зависимые пути строятся относительно FORGE_ROOT_DIR
    """
    # 1. Проверяем типы путей
    assert isinstance(config.FORGE_ROOT_DIR, Path), "FORGE_ROOT_DIR должен быть экземпляром Path"
    assert isinstance(config.FORGE_LAUNCHER_PATH, Path), "FORGE_LAUNCHER_PATH должен быть экземпляром Path"

    # 2. Проверяем, что при инициализации config зависимые пути FORGE_LORA_DIRS строятся относительно FORGE_ROOT_DIR
    test_root = Path(r"C:\test_forge_root")
    monkeypatch.setenv("FORGE_ROOT_DIR", str(test_root))
    monkeypatch.delenv("NEONROUTE_FORGE_LAUNCHER", raising=False)

    importlib.reload(config)
    try:
        assert config.FORGE_ROOT_DIR == test_root
        assert len(config.FORGE_LORA_DIRS) > 0, "FORGE_LORA_DIRS не должен быть пустым"
        for lora_dir in config.FORGE_LORA_DIRS:
            assert isinstance(lora_dir, Path), f"Путь LoRA {lora_dir} должен быть экземпляром Path"
            assert test_root in lora_dir.parents, (
                f"Путь LoRA {lora_dir} должен строиться относительно FORGE_ROOT_DIR ({test_root})"
            )
    finally:
        # Восстанавливаем состояние airgap-песочницы pytest
        import conftest
        monkeypatch.undo()
        importlib.reload(config)
        conftest._apply_airgap_paths(conftest._AIRGAP_TEMP_ROOT)


def test_process_manager_forge_launch_cmd_baseline(qapp, tmp_path):
    """
    Проверяет контракт сборки команды запуска Forge в ProcessManager:
    - Первые аргументы: cmd.exe /c <launcher_path>
    - cwd строго совпадает с launcher_path.parent
    - Добавление ожидаемых флагов Forge (--api, --port, 7860, --cuda-malloc и др.).
    """
    fake_launcher = tmp_path / "fake_forge" / "forge_launcher.bat"
    fake_launcher.parent.mkdir(parents=True, exist_ok=True)
    fake_launcher.write_text("@echo off\nexit /b 0\n", encoding="utf-8")

    pm = ProcessManager()

    # Симулируем: 1-й опрос (is_forge_running) -> False, 2-й опрос (в цикле ожидания) -> True
    pm.is_port_open = MagicMock(side_effect=[False, True])

    mock_proc = MagicMock()
    mock_proc.pid = 7777
    mock_proc.poll.return_value = None
    mock_proc.stdout.readline.return_value = ""

    # Принудительно включаем политику оффлоада, чтобы проверить сборку всех ожидаемых флагов
    with patch("services.process_manager.FORGE_LAUNCHER_PATH", fake_launcher), \
         patch("services.art_director.vram_policy.resolve_forge_offload_policy", return_value={"always_offload": True}), \
         patch("services.asset_validator.AssetValidator.log_validation_summary"), \
         patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
         patch("builtins.open", MagicMock()):

        pm._run_forge_start()

        assert mock_popen.called, "subprocess.Popen должен быть вызван для запуска Forge"
        forge_calls = [
            c for c in mock_popen.call_args_list
            if c.args and isinstance(c.args[0], list) and any("forge_launcher.bat" in str(x) for x in c.args[0])
        ]
        assert forge_calls, f"Вызов forge_launcher.bat не найден среди вызовов Popen: {mock_popen.call_args_list}"
        call_args, call_kwargs = forge_calls[0].args, forge_calls[0].kwargs

        cmd = call_args[0]
        cwd = call_kwargs.get("cwd")

        # 1. Проверка структуры команды
        assert cmd[0] == "cmd.exe", f"Первый элемент команды должен быть cmd.exe, получено: {cmd[0]}"
        assert cmd[1] == "/c", f"Второй элемент команды должен быть /c, получено: {cmd[1]}"
        assert cmd[2] == str(fake_launcher), f"Третий элемент должен быть путем к лаунчеру: {cmd[2]}"

        # 2. Проверка рабочего каталога cwd
        assert cwd == str(fake_launcher.parent), (
            f"cwd ({cwd}) должен строго совпадать с каталогом лаунчера ({fake_launcher.parent})"
        )

        # 3. Проверка наличия ожидаемых флагов Forge
        expected_flags = ["--api", "--port", "7860", "--cuda-malloc", "--always-offload-from-vram"]
        for flag in expected_flags:
            assert flag in cmd, f"Флаг '{flag}' должен присутствовать в команде запуска: {cmd}"


def test_process_manager_cleanup_filter_baseline(qapp):
    """
    Проверяет текущую логику фильтрации процессов при остановке Forge:
    - Отлов процессов python/cmd, содержащих launch.py или webui
    - Текущая baseline-привязка к строке 'neyrogpd' в cmdline
    - Процессы других приложений не затрагиваются.
    """
    pm = ProcessManager()

    # Процесс Forge с launch.py из каталога neyroGPD
    proc_forge_launch = MagicMock()
    proc_forge_launch.pid = 1001
    proc_forge_launch.info = {"name": "python.exe", "pid": 1001}
    proc_forge_launch.cmdline.return_value = ["python.exe", r"C:\neyroGPD\launch.py", "--port", "7860"]

    # Процесс Forge с webui из каталога neyroGPD
    proc_forge_webui = MagicMock()
    proc_forge_webui.pid = 1002
    proc_forge_webui.info = {"name": "python.exe", "pid": 1002}
    proc_forge_webui.cmdline.return_value = ["python.exe", r"C:\neyroGPD\webui\main.py"]

    # Посторонний процесс python
    proc_other = MagicMock()
    proc_other.pid = 1003
    proc_other.info = {"name": "python.exe", "pid": 1003}
    proc_other.cmdline.return_value = ["python.exe", r"C:\MyApp\server.py"]

    # Процесс Forge в нестандартной папке (в baseline он пропускается из-за хардкода 'neyrogpd')
    proc_other_forge = MagicMock()
    proc_other_forge.pid = 1004
    proc_other_forge.info = {"name": "python.exe", "pid": 1004}
    proc_other_forge.cmdline.return_value = ["python.exe", r"D:\AI\Forge\launch.py"]

    mock_processes = [proc_forge_launch, proc_forge_webui, proc_other, proc_other_forge]

    with patch("psutil.process_iter", return_value=mock_processes):
        pm._run_forge_stop()

        # Целевые процессы neyroGPD должны быть завершены
        proc_forge_launch.kill.assert_called_once()
        proc_forge_webui.kill.assert_called_once()

        # Посторонние процессы не должны быть завершены
        proc_other.kill.assert_not_called()

        # Фиксируем текущее (baseline) поведение: процесс без 'neyrogpd' в cmdline пока не отлавливается
        proc_other_forge.kill.assert_not_called()


def test_dialogs_default_paths_baseline(qapp):
    """
    Проверяет контракт дефолтных путей в CivitaiCatalogDialog:
    - При target_dir=None используется дефолтный путь
    - При явном указании target_dir используется переданный путь.
    """
    from ui.dialogs.civitai_catalog_dialog import CivitaiCatalogDialog

    # Отключаем фоновый поиск при инициализации, чтобы не делать реальных вызовов
    with patch.object(CivitaiCatalogDialog, "_on_search_clicked"):
        # 1. По умолчанию
        dlg_default = CivitaiCatalogDialog(target_dir=None)
        assert isinstance(dlg_default.target_dir, Path)
        assert dlg_default.target_dir == config.FORGE_ROOT_DIR / "models" / "Stable-diffusion"

        # 2. При передаче кастомного пути
        custom_dir = Path("D:/CustomAI/models/Stable-diffusion")
        dlg_custom = CivitaiCatalogDialog(target_dir=custom_dir)
        assert dlg_custom.target_dir == custom_dir
