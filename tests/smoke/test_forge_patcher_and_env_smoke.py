"""
tests/smoke/test_forge_patcher_and_env_smoke.py
================================================
Изолированный смок-тест подсистемы Forge:
1. Применение и откат патча совместимости с фиксацией и обратимым запретом
   обновления 8 ключевых библиотек (fastapi, starlette, pydantic, gradio...).
2. Изоляция окружения запуска (get_clean_forge_env, удаление прокси, NO_PROXY,
   PYTHONUNBUFFERED и подстановка PIP_CONSTRAINT).
3. Диагностическое логирование ошибок и HTTP 404 при handshake и таймауте старта.
4. Отсутствие сетевых запросов и остаточных мусорных файлов/процессов.
"""

import io
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.forge_locator import get_clean_forge_env
from services.forge_patcher import (
    ForgePatcher,
    ForgePatchStatus,
    PINNED_FORGE_LIBRARIES,
    CONSTRAINTS_FILENAME,
)
from services.process_manager import ProcessManager, verify_forge_http_handshake


STOCK_API_PY = '''
class Api:
    def __init__(self):
        self.add_api_route("/sdapi/v1/unload-checkpoint", self.unloadapi, methods=["POST"])
        self.add_api_route("/sdapi/v1/reload-checkpoint", self.reloadapi, methods=["POST"])

    def unloadapi(self):
        sd_models.unload_model_weights()
        return {}

    def reloadapi(self):
        sd_models.send_model_to_device(shared.sd_model)
        return {}

    def set_config(self, req: dict[str, Any]):
        for k, v in req.items():
            shared.opts.set(k, v, is_api=True)
        shared.opts.save(shared.config_filename)
        return

    def get_sd_models(self):
        return [{"title": x.title, "filename": x.filename} for x in sd_models.checkpoints_list.values()]
'''

STOCK_MODELS_PY = '''
class SDModelItem(BaseModel):
    title: str = Field(title="Title")
    filename: str = Field(title="Filename")
    config: Optional[str] = Field(title="Config file")
'''

STOCK_SD_MODELS_PY = '''
def unload_model_weights(sd_model=None, info=None):
    pass
'''

STOCK_REQS_TXT = '''
numpy==1.26.2
torch==2.3.1
fastapi==0.95.0
gradio==3.41.0
'''

STOCK_USER_BAT = '''@echo off
set COMMANDLINE_ARGS=
call webui.bat
'''


def _create_mock_forge(root_dir: Path) -> Path:
    """Создает изолированную структуру файлов Forge для смок-теста."""
    webui_dir = root_dir / "webui"
    (webui_dir / "modules" / "api").mkdir(parents=True, exist_ok=True)

    (webui_dir / "modules" / "api" / "api.py").write_text(STOCK_API_PY, encoding="utf-8")
    (webui_dir / "modules" / "api" / "models.py").write_text(STOCK_MODELS_PY, encoding="utf-8")
    (webui_dir / "modules" / "sd_models.py").write_text(STOCK_SD_MODELS_PY, encoding="utf-8")
    (webui_dir / "requirements_versions.txt").write_text(STOCK_REQS_TXT, encoding="utf-8")
    (webui_dir / "webui-user.bat").write_text(STOCK_USER_BAT, encoding="utf-8")

    return root_dir


def test_smoke_forge_patcher_lifecycle_and_library_pins(tmp_path):
    """
    Сквозной смок-тест жизненного цикла патчера:
    - Проверка исходного статуса NEEDS_PATCH.
    - Создание бэкапа оригиналов (включая requirements_versions.txt).
    - Применение патча и фиксация 8 библиотек (requirements_versions.txt + neonroute_constraints.txt).
    - Адаптация лаунчера с включением PIP_CONSTRAINT.
    - Подтверждение статуса PATCHED и идемпотентность повторного вызова.
    - Полный чистый откат restore_backup с удалением ограничений и возвратом оригиналов.
    """
    forge_dir = _create_mock_forge(tmp_path / "smoke_forge")
    webui_dir = forge_dir / "webui"

    # 1. До патчинга
    status_0, _ = ForgePatcher.check_status(forge_dir)
    assert status_0 == ForgePatchStatus.NEEDS_PATCH
    assert ForgePatcher.has_backup(forge_dir) is False

    # 2. Применение патча
    ok, msg = ForgePatcher.apply_patch(forge_dir)
    assert ok is True
    assert ForgePatcher.has_backup(forge_dir) is True

    # 3. Проверка статуса PATCHED
    status_1, _ = ForgePatcher.check_status(forge_dir)
    assert status_1 == ForgePatchStatus.PATCHED

    # 4. Проверка фиксации 8 библиотек
    assert ForgePatcher.are_libraries_pinned(forge_dir) is True

    constraints_file = webui_dir / CONSTRAINTS_FILENAME
    assert constraints_file.is_file()
    c_text = constraints_file.read_text(encoding="utf-8")
    for pkg, ver in PINNED_FORGE_LIBRARIES.items():
        assert f"{pkg}=={ver}" in c_text

    req_text = (webui_dir / "requirements_versions.txt").read_text(encoding="utf-8")
    for pkg, ver in PINNED_FORGE_LIBRARIES.items():
        assert f"{pkg}=={ver}" in req_text
    # Несвязанные пакеты не повреждены
    assert "numpy==1.26.2" in req_text
    assert "torch==2.3.1" in req_text

    # 5. Проверка адаптации лаунчера под PIP_CONSTRAINT
    bat_text = (webui_dir / "webui-user.bat").read_text(encoding="utf-8")
    assert "PIP_CONSTRAINT" in bat_text
    assert "--api" in bat_text

    # 6. Идемпотентность
    ok_idemp, _ = ForgePatcher.apply_patch(forge_dir)
    assert ok_idemp is True
    assert ForgePatcher.check_status(forge_dir)[0] == ForgePatchStatus.PATCHED

    # 7. Откат патча к исходному состоянию
    ok_restore, res_msg = ForgePatcher.restore_backup(forge_dir)
    assert ok_restore is True

    # Проверка чистоты отката
    assert constraints_file.exists() is False
    restored_req = (webui_dir / "requirements_versions.txt").read_text(encoding="utf-8")
    assert restored_req.strip() == STOCK_REQS_TXT.strip()

    restored_api = (webui_dir / "modules" / "api" / "api.py").read_text(encoding="utf-8")
    assert restored_api.strip() == STOCK_API_PY.strip()

    restored_bat = (webui_dir / "webui-user.bat").read_text(encoding="utf-8")
    assert restored_bat.strip() == STOCK_USER_BAT.strip()

    # Статус возвращается в NEEDS_PATCH
    status_2, _ = ForgePatcher.check_status(forge_dir)
    assert status_2 == ForgePatchStatus.NEEDS_PATCH


def test_smoke_clean_forge_env_and_constraints(tmp_path, monkeypatch):
    """
    Смок-тест очистки окружения и подстановки PIP_CONSTRAINT:
    - Удаление прокси-переменных верхнего и нижнего регистра.
    - Выставление NO_PROXY, no_proxy и PYTHONUNBUFFERED=1.
    - Автоматическое обнаружение neonroute_constraints.txt в каталоге Forge.
    """
    forge_dir = tmp_path / "env_forge"
    forge_dir.mkdir()
    constraints = forge_dir / "neonroute_constraints.txt"
    constraints.write_text("fastapi==0.104.1\n", encoding="utf-8")

    import config
    monkeypatch.setattr(config, "FORGE_ROOT_DIR", forge_dir)

    dirty_env = {
        "HTTP_PROXY": "http://127.0.0.1:8080",
        "HTTPS_PROXY": "http://127.0.0.1:8080",
        "ALL_PROXY": "socks5://127.0.0.1:1080",
        "AG_LS_PROXY": "http://127.0.0.1:53129",
        "http_proxy": "http://127.0.0.1:8080",
        "all_proxy": "socks5://127.0.0.1:1080",
        "VIRTUAL_ENV": "C:\\NeonRoute\\.venv",
        "PYTHONPATH": "C:\\NeonRoute",
        "PYTHONHOME": "C:\\Python310",
        "PATH": "C:\\Windows\\System32;C:\\NeonRoute\\.venv\\Scripts",
    }

    clean = get_clean_forge_env(dirty_env)

    # 1. Прокси удалены
    for p in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "AG_LS_PROXY", "http_proxy", "all_proxy"]:
        assert p not in clean

    # 2. Переменные Python NeonRoute удалены
    assert "VIRTUAL_ENV" not in clean
    assert "PYTHONPATH" not in clean
    assert "PYTHONHOME" not in clean
    assert ".venv" not in clean["PATH"].lower()

    # 3. Гарантированы loopback и небуферизованный вывод
    assert clean["NO_PROXY"] == "localhost,127.0.0.1,0.0.0.0"
    assert clean["no_proxy"] == "localhost,127.0.0.1,0.0.0.0"
    assert clean["PYTHONUNBUFFERED"] == "1"

    # 4. PIP_CONSTRAINT выставлен на найденный файл ограничений
    assert clean["PIP_CONSTRAINT"] == str(constraints.resolve())


def test_smoke_handshake_diagnostics_logging(caplog):
    """
    Смок-тест детального диагностического логирования handshake:
    - HTTP 404: логирует объяснение о неинициализированных роутах /sdapi/v1/*.
    - Ошибка соединения: логирует отказ подключения.
    - HTTP 200: возвращает True.
    """
    # 1. HTTP 404
    http_404_err = urllib.error.HTTPError(
        url="http://127.0.0.1:7860/sdapi/v1/options",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=io.BytesIO(b"404 Not Found")
    )

    with caplog.at_level(logging.DEBUG, logger="NeonRoute.ProcessManager"):
        with patch("urllib.request.urlopen", side_effect=http_404_err):
            res_404 = verify_forge_http_handshake(port=7860)
            assert res_404 is False
            assert any("HTTP 404 Not Found" in r.message and "/sdapi/v1/*" in r.message for r in caplog.records)

    caplog.clear()

    # 2. Connection Refused
    url_err = urllib.error.URLError("[WinError 10061] Connection refused")
    with caplog.at_level(logging.DEBUG, logger="NeonRoute.ProcessManager"):
        with patch("urllib.request.urlopen", side_effect=url_err):
            res_conn = verify_forge_http_handshake(port=7860)
            assert res_conn is False
            assert any("Порт 7860 недоступен" in r.message for r in caplog.records)

    # 3. HTTP 200
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp
    with patch("urllib.request.urlopen", return_value=mock_resp):
        res_ok = verify_forge_http_handshake(port=7860)
        assert res_ok is True


def test_smoke_forge_startup_timeout_404_diagnostics_and_process_cleanup(tmp_path, monkeypatch):
    """
    Смок-тест ветки таймаута ProcessManager при получении HTTP 404:
    - Порт отвечает 404 во время таймаута.
    - В лог и сигналы пишется подробная причина (HTTP 404 Not Found: корневой веб-сервер активен...).
    - Вызывается штатная остановка _run_forge_stop, процесс сбрасывается в None.
    """
    import config
    fake_bat = tmp_path / "forge_launcher.bat"
    fake_bat.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(config, "FORGE_LAUNCHER_PATH", fake_bat)

    pm = ProcessManager()
    pm.is_port_open = MagicMock(return_value=False)

    mock_proc = MagicMock()
    mock_proc.pid = 7777
    mock_proc.poll.return_value = None
    mock_proc.stdout = None

    stop_called = False
    def fake_stop():
        nonlocal stop_called
        stop_called = True
        pm._forge_process = None

    pm._run_forge_stop = MagicMock(side_effect=fake_stop)

    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: mock_proc)
    monkeypatch.setattr("services.process_manager.Win32JobManager.get_instance", lambda: MagicMock(assign_process=MagicMock(return_value=True)))
    monkeypatch.setattr("services.process_manager.verify_forge_http_handshake", lambda *a, **kw: False)

    # При финальной диагностической проверке urlopen возвращает HTTP 404
    http_404_err = urllib.error.HTTPError(
        url="http://127.0.0.1:7860/sdapi/v1/options",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=io.BytesIO(b"404")
    )
    monkeypatch.setattr("urllib.request.urlopen", MagicMock(side_effect=http_404_err))

    times = [10.0, 10.0, 200.0, 200.0]
    monkeypatch.setattr("time.time", lambda: times.pop(0) if times else 500.0)
    monkeypatch.setattr("time.sleep", lambda s: None)

    status_signals = []
    action_signals = []
    pm.forge_status_changed.connect(lambda ok, msg: status_signals.append((ok, msg)))
    pm.action_completed.connect(lambda act, ok, msg: action_signals.append((act, ok, msg)))

    pm._run_forge_start()

    assert stop_called is True
    assert pm._forge_process is None

    # Проверяем детальное описание ошибки с указанием HTTP 404
    assert any("HTTP 404 Not Found" in msg and "API /sdapi/v1/options не зарегистрирован" in msg for ok, msg in status_signals)
    assert any(act == "forge_start" and not ok and "HTTP 404 Not Found" in msg for act, ok, msg in action_signals)
