import configparser
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from core.forge_locator import (
    is_valid_forge_dir,
    find_forge_root,
    update_server_config_forge_root,
    FORGE_LAUNCHER_CANDIDATES,
    SERVER_CONFIG_INI_PATH,
    get_clean_forge_env,
)


def test_is_valid_forge_dir_with_forge_launcher(tmp_path):
    """Создает маркеры Forge в tmp_path и проверяет, что is_valid_forge_dir возвращает True."""
    # Проверка основного лаунчера
    launcher_file = tmp_path / "forge_launcher.bat"
    launcher_file.write_text("@echo off", encoding="utf-8")
    assert is_valid_forge_dir(tmp_path) is True
    assert is_valid_forge_dir(str(tmp_path)) is True

    # Проверка альтернативных кандидатов
    for candidate in ("webui-user.bat", "run.bat", "launch.py"):
        sub_dir = tmp_path / f"test_{candidate}"
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / candidate).write_text("@echo off", encoding="utf-8")
        assert is_valid_forge_dir(sub_dir) is True


def test_is_valid_forge_dir_empty_or_nonexistent(tmp_path):
    """Пустая папка, файл, несуществующий путь, пустая строка или None возвращают False без исключений."""
    # Пустая папка
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert is_valid_forge_dir(empty_dir) is False

    # Несуществующий путь
    nonexistent = tmp_path / "does_not_exist"
    assert is_valid_forge_dir(nonexistent) is False

    # Путь указывает на файл, а не на директорию
    dummy_file = tmp_path / "regular_file.txt"
    dummy_file.write_text("hello", encoding="utf-8")
    assert is_valid_forge_dir(dummy_file) is False

    # Граничные значения
    assert is_valid_forge_dir(None) is False
    assert is_valid_forge_dir("") is False


def test_find_forge_root_prefers_configured_path(tmp_path, monkeypatch):
    """Сконфигурированный путь имеет наивысший приоритет над переменными окружения."""
    configured_dir = tmp_path / "configured_forge"
    configured_dir.mkdir()
    (configured_dir / "forge_launcher.bat").write_text("@echo off", encoding="utf-8")

    env_dir = tmp_path / "env_forge"
    env_dir.mkdir()
    (env_dir / "forge_launcher.bat").write_text("@echo off", encoding="utf-8")

    monkeypatch.setenv("FORGE_ROOT_DIR", str(env_dir))

    resolved = find_forge_root(configured_path=configured_dir)
    assert resolved == configured_dir.resolve()
    assert resolved != env_dir.resolve()


def test_find_forge_root_env_fallback(tmp_path, monkeypatch):
    """При отсутствии configured_path используется значение из FORGE_ROOT_DIR."""
    env_dir = tmp_path / "env_forge"
    env_dir.mkdir()
    (env_dir / "webui-user.bat").write_text("@echo off", encoding="utf-8")

    monkeypatch.setenv("FORGE_ROOT_DIR", str(env_dir))

    # С configured_path=None
    resolved_none = find_forge_root(configured_path=None)
    assert resolved_none == env_dir.resolve()

    # С невалидным configured_path (фоллбек на env)
    invalid_path = tmp_path / "invalid_forge"
    resolved_invalid = find_forge_root(configured_path=invalid_path)
    assert resolved_invalid == env_dir.resolve()


def test_find_forge_root_returns_none_when_missing(tmp_path, monkeypatch):
    """Если маркеры нигде не обнаружены, возвращает None без падений."""
    monkeypatch.delenv("FORGE_ROOT_DIR", raising=False)

    # Имитируем отсутствие валидных маркеров во всех проверяемых путях
    monkeypatch.setattr("core.forge_locator.is_valid_forge_dir", lambda p: False)

    resolved = find_forge_root(configured_path=None)
    assert resolved is None

    # Проверяем также с явно переданным путем, где нет маркеров
    empty_dir = tmp_path / "empty_forge"
    empty_dir.mkdir()
    resolved_empty = find_forge_root(configured_path=empty_dir)
    assert resolved_empty is None


def test_find_forge_root_reads_server_config_ini(tmp_path, monkeypatch):
    """При наличии server_config.ini с валидным forge_root_dir путь успешно подхватывается."""
    monkeypatch.delenv("FORGE_ROOT_DIR", raising=False)

    forge_dir = tmp_path / "ini_forge"
    forge_dir.mkdir()
    (forge_dir / "forge_launcher.bat").write_text("@echo off", encoding="utf-8")

    ini_file = tmp_path / "server_config.ini"
    ini_file.write_text(f"[PATHS]\nforge_root_dir = {forge_dir}\n", encoding="utf-8")

    resolved = find_forge_root(config_file=ini_file)
    assert resolved == forge_dir.resolve()

    # Проверка ключа forge_path (синоним)
    ini_file_alt = tmp_path / "server_config_alt.ini"
    ini_file_alt.write_text(f"[PATHS]\nforge_path = {forge_dir}\n", encoding="utf-8")
    resolved_alt = find_forge_root(config_file=ini_file_alt)
    assert resolved_alt == forge_dir.resolve()


def test_find_forge_root_corrupted_or_empty_server_config_ini(tmp_path, monkeypatch):
    """Поврежденный или пустой server_config.ini не вызывает исключений и продолжает каскад."""
    monkeypatch.delenv("FORGE_ROOT_DIR", raising=False)
    monkeypatch.setattr("core.forge_locator.is_valid_forge_dir", lambda p: False)

    # 1. Пустой файл
    empty_ini = tmp_path / "empty_server_config.ini"
    empty_ini.write_text("", encoding="utf-8")
    assert find_forge_root(config_file=empty_ini) is None

    # 2. Невалидный синтаксис INI
    broken_ini = tmp_path / "broken_server_config.ini"
    broken_ini.write_text("[PATHS\nthis is not valid ini content!!!", encoding="utf-8")
    assert find_forge_root(config_file=broken_ini) is None

    # 3. Пустой ключ forge_root_dir
    empty_val_ini = tmp_path / "empty_val_server_config.ini"
    empty_val_ini.write_text("[PATHS]\nforge_root_dir = \n", encoding="utf-8")
    assert find_forge_root(config_file=empty_val_ini) is None


def test_update_server_config_forge_root(tmp_path):
    """Проверяет создание/модификацию server_config.ini с записью forge_root_dir."""
    import config
    orig_root = config.FORGE_ROOT_DIR
    orig_launcher = config.FORGE_LAUNCHER_PATH
    try:
        ini_file = tmp_path / "custom_server_config.ini"
        fake_forge = tmp_path / "test_forge_install"
        fake_forge.mkdir()
        (fake_forge / "forge_launcher.bat").write_text("@echo off", encoding="utf-8")

        success = update_server_config_forge_root(fake_forge, config_file=ini_file)
        assert success is True
        assert ini_file.is_file()

        parser = configparser.ConfigParser()
        parser.read(str(ini_file), encoding="utf-8")
        assert parser.has_section("PATHS")
        assert parser.get("PATHS", "forge_root_dir") == str(fake_forge.resolve())
        assert config.FORGE_ROOT_DIR == fake_forge.resolve()
        assert config.FORGE_LAUNCHER_PATH == fake_forge.resolve() / "forge_launcher.bat"
    finally:
        config.FORGE_ROOT_DIR = orig_root
        config.FORGE_LAUNCHER_PATH = orig_launcher


def test_prompt_locate_forge_cancel(monkeypatch):
    """При отказе пользователя (кнопка Отмена) prompt_locate_forge возвращает False без исключений."""
    from ui.controllers.hardware_engine import HardwareEngineMixin
    from PyQt6.QtWidgets import QApplication, QWidget, QMessageBox

    app = QApplication.instance() or QApplication([])

    class DummyHardwareHost(QWidget, HardwareEngineMixin):
        def __init__(self):
            super().__init__()
            self._status_mock = MagicMock()

        def statusBar(self):
            return self._status_mock

    host = DummyHardwareHost()

    # Имитируем клик по кнопке Отмена (clickedButton != btn_locate)
    with patch.object(QMessageBox, "exec", return_value=0), \
         patch.object(QMessageBox, "clickedButton", return_value=MagicMock()):
        result = host.prompt_locate_forge()
        assert result is False
        host.statusBar().showMessage.assert_called_with("SD WebUI Forge не настроен (генерация отключена)", 5000)


def test_get_clean_forge_env():
    """Проверяет изоляцию окружения: удаление VIRTUAL_ENV, PYTHONPATH, PYTHONHOME и очистку PATH от venv."""
    mock_env = {
        "VIRTUAL_ENV": "C:\\NeonRoute\\.venv",
        "PYTHONPATH": "C:\\NeonRoute",
        "PYTHONHOME": "C:\\Python310",
        "PATH": "C:\\NeonRoute\\.venv\\Scripts;C:\\Windows\\System32;C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v12.1\\bin",
    }
    cleaned = get_clean_forge_env(mock_env)

    # 1. Переменные изоляции удалены
    assert "VIRTUAL_ENV" not in cleaned
    assert "PYTHONPATH" not in cleaned
    assert "PYTHONHOME" not in cleaned

    # 2. PATH очищен от путей .venv
    cleaned_path = cleaned.get("PATH", "")
    assert "C:\\NeonRoute\\.venv\\Scripts" not in cleaned_path
    assert ".venv" not in cleaned_path.lower()

    # 3. Системные пути и CUDA сохранены
    assert "C:\\Windows\\System32" in cleaned_path
    assert "C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v12.1\\bin" in cleaned_path

    # 4. Прокси-переменные удалены, NO_PROXY и PYTHONUNBUFFERED заданы
    assert cleaned.get("NO_PROXY") == "localhost,127.0.0.1,0.0.0.0"
    assert cleaned.get("no_proxy") == "localhost,127.0.0.1,0.0.0.0"
    assert cleaned.get("PYTHONUNBUFFERED") == "1"


def test_get_clean_forge_env_purges_proxy_variables():
    """Проверяет полное удаление прокси-переменных верхнего и нижнего регистра."""
    mock_env = {
        "HTTP_PROXY": "http://127.0.0.1:8080",
        "HTTPS_PROXY": "http://127.0.0.1:8080",
        "ALL_PROXY": "socks5://127.0.0.1:1080",
        "FTP_PROXY": "http://127.0.0.1:2121",
        "AG_LS_PROXY": "http://127.0.0.1:53129",
        "http_proxy": "http://127.0.0.1:8080",
        "https_proxy": "http://127.0.0.1:8080",
        "all_proxy": "socks5://127.0.0.1:1080",
        "ftp_proxy": "http://127.0.0.1:2121",
        "ag_ls_proxy": "http://127.0.0.1:53129",
        "PATH": "C:\\Windows\\System32",
    }
    cleaned = get_clean_forge_env(mock_env)
    for proxy_key in [
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY", "AG_LS_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "ag_ls_proxy"
    ]:
        assert proxy_key not in cleaned

    assert cleaned["NO_PROXY"] == "localhost,127.0.0.1,0.0.0.0"
    assert cleaned["no_proxy"] == "localhost,127.0.0.1,0.0.0.0"
    assert cleaned["PYTHONUNBUFFERED"] == "1"
    assert "C:\\Windows\\System32" in cleaned["PATH"]


def test_get_clean_forge_env_purges_runtime_python_and_sys_executable(monkeypatch, tmp_path):
    """
    Проверяет, что при запуске NeonRoute через встроенный runtime\\python\\
    каталог sys.executable и runtime\\python гарантированно удаляются из PATH Forge.
    """
    fake_runtime = tmp_path / "NeonRoute" / "runtime" / "python"
    fake_runtime.mkdir(parents=True, exist_ok=True)
    fake_python = fake_runtime / "python.exe"
    fake_python.touch()

    monkeypatch.setattr(sys, "executable", str(fake_python))
    monkeypatch.setattr(sys, "prefix", str(fake_runtime))

    mock_env = {
        "PATH": f"{fake_runtime};{fake_runtime / 'Scripts'};C:\\Windows\\System32;C:\\CUDA\\bin"
    }
    cleaned = get_clean_forge_env(mock_env)
    cleaned_path = cleaned.get("PATH", "")

    assert str(fake_runtime).lower() not in cleaned_path.lower()
    assert "runtime\\python" not in cleaned_path.lower()
    assert "C:\\Windows\\System32" in cleaned_path
    assert "C:\\CUDA\\bin" in cleaned_path


def test_get_python_cli_executable_resolves_python_from_pythonw(monkeypatch, tmp_path):
    """
    Проверяет, что get_python_cli_executable() при запуске под pythonw.exe
    находит соседний python.exe в той же директории.
    """
    from utils.file_utils import get_python_cli_executable

    fake_bin_dir = tmp_path / "runtime" / "python"
    fake_bin_dir.mkdir(parents=True, exist_ok=True)
    fake_pythonw = fake_bin_dir / "pythonw.exe"
    fake_python = fake_bin_dir / "python.exe"
    fake_pythonw.touch()
    fake_python.touch()

    # Симулируем запуск под pythonw.exe
    monkeypatch.setattr(sys, "executable", str(fake_pythonw))
    resolved = get_python_cli_executable()
    assert Path(resolved).resolve() == fake_python.resolve()
    assert Path(resolved).name.lower() == "python.exe"


def test_find_forge_python_from_launcher_bat(tmp_path):
    """Проверяет динамическое обнаружение Python через парсинг переменной set PYTHON= в лаунчере."""
    from core.forge_locator import find_forge_python

    forge_dir = tmp_path / "forge_test"
    system_py_dir = forge_dir / "system" / "python"
    system_py_dir.mkdir(parents=True, exist_ok=True)
    fake_py = system_py_dir / "python.exe"
    fake_py.touch()

    launcher = forge_dir / "forge_launcher.bat"
    launcher.write_text(
        "@echo off\n"
        "set ROOT=%~dp0\n"
        "set PYTHON=%ROOT%system\\python\\python.exe\n"
        "call \"%PYTHON%\" launch.py %*\n",
        encoding="utf-8"
    )

    found = find_forge_python(launcher_path=launcher)
    assert found is not None
    assert found.resolve() == fake_py.resolve()


def test_find_forge_python_from_root_subfolders(tmp_path):
    """Проверяет обнаружение Python в подпапках дистрибутива 1-го уровня (например, webui_forge_*/system/python)."""
    from core.forge_locator import find_forge_python

    root = tmp_path / "custom_forge_dist"
    nested_py = root / "webui_forge_cu121_torch231" / "system" / "python" / "python.exe"
    nested_py.parent.mkdir(parents=True, exist_ok=True)
    nested_py.touch()

    found = find_forge_python(forge_root=root)
    assert found is not None
    assert found.resolve() == nested_py.resolve()


def test_check_forge_environment_ready_missing_python(tmp_path):
    """Проверяет обработку отсутствующего интерпретатора Python."""
    from core.forge_locator import check_forge_environment_ready

    non_existent = tmp_path / "system" / "python" / "python.exe"
    ready, reason, missing = check_forge_environment_ready(python_exe=non_existent)
    assert ready is False
    assert "не найден" in reason
    assert len(missing) > 0


def test_check_forge_environment_ready_site_packages_fallback_success(tmp_path):
    """Проверяет фолбэк-проверку окружения по содержимому каталога site-packages (успешный случай)."""
    from core.forge_locator import check_forge_environment_ready, FORGE_REQUIRED_PACKAGES

    fake_py = tmp_path / "system" / "python" / "python.exe"
    fake_py.parent.mkdir(parents=True, exist_ok=True)
    fake_py.touch()

    site_pkgs = tmp_path / "system" / "python" / "lib" / "site-packages"
    site_pkgs.mkdir(parents=True, exist_ok=True)

    # Создаем папки всех требуемых пакетов
    for pkg in FORGE_REQUIRED_PACKAGES:
        (site_pkgs / pkg).mkdir(parents=True, exist_ok=True)

    ready, reason, missing = check_forge_environment_ready(python_exe=fake_py)
    assert ready is True
    assert missing == []


def test_check_forge_environment_ready_site_packages_fallback_missing_packages(tmp_path):
    """Проверяет фолбэк-проверку окружения при отсутствии части пакетов (например, clip и tqdm)."""
    from core.forge_locator import check_forge_environment_ready

    fake_py = tmp_path / "system" / "python" / "python.exe"
    fake_py.parent.mkdir(parents=True, exist_ok=True)
    fake_py.touch()

    site_pkgs = tmp_path / "system" / "python" / "lib" / "site-packages"
    site_pkgs.mkdir(parents=True, exist_ok=True)

    # Создаем только torch и regex, опуская clip, tqdm, ftfy, torchvision
    (site_pkgs / "torch").mkdir(parents=True, exist_ok=True)
    (site_pkgs / "regex").mkdir(parents=True, exist_ok=True)

    ready, reason, missing = check_forge_environment_ready(python_exe=fake_py)
    assert ready is False
    assert "clip" in missing
    assert "tqdm" in missing


