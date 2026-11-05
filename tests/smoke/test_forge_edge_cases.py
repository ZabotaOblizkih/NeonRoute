"""
Smoke tests for edge cases and hardening in SD WebUI Forge integration:
1. Unicode and spaces in paths.
2. Malformed or missing directories in server_config.ini.
3. Directory traps (subdirectories named as launcher files).
4. Re-entrancy guard in UI controller (prompt_locate_forge).
5. PATH sanitation with messy and case-insensitive entries.
6. Burst requests handling when Forge is offline without VRAM thrashing.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtWidgets import QApplication, QWidget, QMessageBox, QFileDialog

from core.forge_locator import (
    is_valid_forge_dir,
    find_forge_root,
    update_server_config_forge_root,
    get_clean_forge_env,
)
from ui.controllers.hardware_engine import HardwareEngineMixin
from services.vram_arbiter import VRAMArbiter


@pytest.fixture(scope="session")
def qapp():
    """Headless QApplication fixture."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class DummyHardwareHost(QWidget, HardwareEngineMixin):
    """Мок-хост для тестирования методов HardwareEngineMixin."""
    def __init__(self):
        super().__init__()
        self._status_bar_mock = MagicMock()
        self.process_manager = MagicMock()
        self.forge_client = MagicMock()
        self.task_queue = MagicMock()
        self.pill_forge = MagicMock()
        self._reload_active_lms_model_async = MagicMock()
        self._cancel_lms_reload = MagicMock()

    def statusBar(self):
        return self._status_bar_mock


def test_forge_locator_and_env_with_unicode_and_spaces(tmp_path):
    """
    Проверяет корректность работы с путями, содержащими пробелы и кириллицу:
    1. is_valid_forge_dir распознает лаунчер.
    2. update_server_config_forge_root сохраняет UTF-8 путь в server_config.ini.
    3. find_forge_root корректно считывает и разрешает путь из файла.
    """
    import config
    orig_root = config.FORGE_ROOT_DIR
    orig_launcher = config.FORGE_LAUNCHER_PATH
    try:
        forge_dir = tmp_path / "Нейро Инструменты" / "SD Forge Portable 1.0"
        forge_dir.mkdir(parents=True, exist_ok=True)
        (forge_dir / "forge_launcher.bat").write_text("@echo off\n", encoding="utf-8")

        # 1. Валидация директории с кириллицей и пробелами
        assert is_valid_forge_dir(forge_dir) is True
        assert is_valid_forge_dir(str(forge_dir)) is True

        # 2. Сохранение в server_config.ini
        ini_file = tmp_path / "server_config.ini"
        ok = update_server_config_forge_root(forge_dir, config_file=ini_file)
        assert ok is True
        assert ini_file.is_file()

        # 3. Чтение через каскадный локатор
        resolved = find_forge_root(config_file=ini_file)
        assert resolved is not None
        assert resolved == forge_dir.resolve()
    finally:
        config.FORGE_ROOT_DIR = orig_root
        config.FORGE_LAUNCHER_PATH = orig_launcher


def test_forge_locator_malformed_ini_resilience(tmp_path, monkeypatch):
    """
    Проверяет устойчивость локатора к поврежденному или неполному server_config.ini:
    - Битый синтаксис / бинарный мусор не роняют локатор.
    - Указание на несуществующую директорию игнорируется.
    """
    monkeypatch.delenv("FORGE_ROOT_DIR", raising=False)
    monkeypatch.setattr("core.forge_locator.is_valid_forge_dir", lambda p: False)

    # 1. Бинарный мусор в файле конфигурации
    broken_ini = tmp_path / "broken_server_config.ini"
    broken_ini.write_bytes(b"\x00\xff\xfe[PATHS\ncorrupted syntax == :: ???")
    res_broken = find_forge_root(config_file=broken_ini)
    assert res_broken is None

    # 2. Секция [PATHS] есть, но директория удалена / не существует
    missing_ini = tmp_path / "missing_folder_server_config.ini"
    missing_dir = tmp_path / "deleted_forge_folder"
    missing_ini.write_text(f"[PATHS]\nforge_root_dir = {missing_dir}\n", encoding="utf-8")
    res_missing = find_forge_root(config_file=missing_ini)
    assert res_missing is None


def test_is_valid_forge_dir_rejects_subdirectories_named_as_launchers(tmp_path):
    """
    Защита от ложных маркеров (Directory Trap):
    Подпапка с именем forge_launcher.bat не должна считаться валидным исполняемым файлом.
    """
    fake_forge = tmp_path / "fake_forge"
    fake_forge.mkdir(parents=True, exist_ok=True)
    # Создаем директорию вместо файла
    (fake_forge / "forge_launcher.bat").mkdir()

    assert is_valid_forge_dir(fake_forge) is False
    assert is_valid_forge_dir(str(fake_forge)) is False


def test_prompt_locate_forge_reentrancy_guard(qapp):
    """
    Проверяет работу флага защиты от повторного входа (_is_locating_forge):
    Если диалог уже открыт/активен, повторный вызов мгновенно возвращает False
    без открытия новых окон диалогов.
    """
    host = DummyHardwareHost()
    host._is_locating_forge = True

    with patch.object(QMessageBox, "exec") as mock_msg_box, \
         patch.object(QFileDialog, "getExistingDirectory") as mock_file_dialog:
        result = host.prompt_locate_forge()
        assert result is False
        mock_msg_box.assert_not_called()
        mock_file_dialog.assert_not_called()


def test_get_clean_forge_env_handles_messy_path():
    r"""
    Проверяет санитацию переменной PATH при замусоренных значениях:
    - Пустые сегменты (;;).
    - Разный регистр (.VENV\SCRIPTS).
    - Относительные сегменты с точками (.\.venv\Scripts).
    - Сохранение путей к System32 и CUDA.
    """
    mock_env = {
        "PATH": (
            ";;"
            r"C:\NEONROUTE\.VENV\SCRIPTS;;"
            r"C:\NeonRoute\.\.venv\Scripts;"
            r"C:\Windows\System32;"
            r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1\bin;"
        )
    }
    cleaned = get_clean_forge_env(mock_env)
    cleaned_path = cleaned.get("PATH", "")

    # 1. Проверяем удаление всех вариаций .venv
    assert ".venv" not in cleaned_path.lower()
    assert "neonroute" not in cleaned_path.lower()

    # 2. Проверяем сохранение системных путей и CUDA
    assert r"C:\Windows\System32" in cleaned_path
    assert r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1\bin" in cleaned_path

    # 3. Проверяем отсутствие пустых элементов в разделителях
    parts = cleaned_path.split(";")
    for part in parts:
        assert part.strip() != "", "PATH не должен содержать пустых сегментов"


def test_scene_art_burst_requests_never_trigger_vram_release(qapp):
    """
    Проверяет устойчивость к лавинному потоку запросов на генерацию сцены при мертвом Forge:
    Серия из 5 последовательных запросов не должна ни разу вызвать release_llm_for_diffusion
    или поставить задачи в TaskQueue.
    """
    host = DummyHardwareHost()
    host.process_manager.is_forge_running.return_value = False
    host.forge_client.is_server_ready.return_value = False
    host.pill_forge.is_online = False

    with patch.object(VRAMArbiter, "release_llm_for_diffusion") as mock_release:
        for i in range(5):
            host._on_scene_art_requested(text=f"Быстрое сообщение диалога #{i}")

        assert mock_release.call_count == 0
        assert host.task_queue.enqueue.call_count == 0
        assert host._reload_active_lms_model_async.call_count == 0
