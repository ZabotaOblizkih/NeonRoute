"""
Unit tests for SD WebUI Forge HTTP handshake verification and port drift protection.
"""

import urllib.request
import urllib.error
from unittest.mock import MagicMock, patch

from services.process_manager import ProcessManager, verify_forge_http_handshake


def test_verify_forge_http_handshake_success():
    """
    Проверяет успешный HTTP-handshake:
    Эндпоинт /sdapi/v1/options отвечает HTTP 200 -> функция возвращает True.
    """
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = verify_forge_http_handshake(host="127.0.0.1", port=7860, timeout=0.8)
        assert result is True
        assert mock_urlopen.called
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://127.0.0.1:7860/sdapi/v1/options"
        assert req.get_method() == "GET"


def test_verify_forge_http_handshake_foreign_service():
    """
    Проверяет ответ от постороннего сервиса (ComfyUI / Gradio другого приложения):
    Возврат 404 или генерация HTTPError(404) -> функция возвращает False.
    """
    # 1. Вариант с исключением HTTPError(404)
    http_err_404 = urllib.error.HTTPError(
        url="http://127.0.0.1:7860/sdapi/v1/options",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=None
    )
    with patch("urllib.request.urlopen", side_effect=http_err_404):
        assert verify_forge_http_handshake(port=7860) is False

    # 2. Вариант с прямым возвратом статуса 404
    mock_resp_404 = MagicMock()
    mock_resp_404.status = 404
    mock_resp_404.__enter__.return_value = mock_resp_404
    with patch("urllib.request.urlopen", return_value=mock_resp_404):
        assert verify_forge_http_handshake(port=7860) is False

    # 3. Вариант со статусом 502 Bad Gateway
    mock_resp_502 = MagicMock()
    mock_resp_502.status = 502
    mock_resp_502.__enter__.return_value = mock_resp_502
    with patch("urllib.request.urlopen", return_value=mock_resp_502):
        assert verify_forge_http_handshake(port=7860) is False


def test_verify_forge_http_handshake_connection_refused():
    """
    Проверяет поведение при недоступном сервере / закрытом порте:
    URLError(ConnectionRefusedError) перехватывается, функция возвращает False без исключений.
    """
    conn_err = urllib.error.URLError(ConnectionRefusedError("Connection refused"))
    with patch("urllib.request.urlopen", side_effect=conn_err):
        assert verify_forge_http_handshake(port=7860) is False

    # Проверка также таймаута
    timeout_err = TimeoutError("Connection timed out")
    with patch("urllib.request.urlopen", side_effect=timeout_err):
        assert verify_forge_http_handshake(port=7860) is False


def test_is_forge_running_requires_http_handshake():
    """
    Проверяет двухфакторную валидацию в is_forge_running:
    Даже если порт открыт на L4 (is_port_open -> True), но L7 HTTP-handshake не подтвержден ->
    is_forge_running ОБЯЗАН вернуть False (предотвращение ложного статуса при чужом сервисе).
    """
    pm = ProcessManager()
    pm.is_port_open = MagicMock(return_value=True)

    with patch("services.process_manager.verify_forge_http_handshake", return_value=False) as mock_handshake:
        assert pm.is_forge_running() is False
        assert mock_handshake.called

    # При успешном handshake возвращает True
    with patch("services.process_manager.verify_forge_http_handshake", return_value=True) as mock_handshake:
        assert pm.is_forge_running() is True
        assert mock_handshake.called


def test_forge_startup_timeout_terminates_process_tree(monkeypatch, tmp_path):
    """
    Проверяет, что при превышении startup_timeout во время запуска Forge
    ProcessManager гарантированно вызывает _run_forge_stop() для завершения дерева процессов,
    сбрасывает ссылку на процесс в None и отправляет сигналы об ошибке.
    """
    import config
    fake_bat = tmp_path / "forge_launcher.bat"
    fake_bat.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(config, "FORGE_LAUNCHER_PATH", fake_bat)

    pm = ProcessManager()
    pm.is_port_open = MagicMock(return_value=False)

    mock_proc = MagicMock()
    mock_proc.pid = 4321
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

    # Симулируем истечение таймаута (перескок времени)
    times = [100.0, 100.0, 300.0, 300.0]
    monkeypatch.setattr("time.time", lambda: times.pop(0) if times else 500.0)
    monkeypatch.setattr("time.sleep", lambda s: None)

    status_signals = []
    action_signals = []
    pm.forge_status_changed.connect(lambda ok, msg: status_signals.append((ok, msg)))
    pm.action_completed.connect(lambda action, ok, msg: action_signals.append((action, ok, msg)))

    pm._run_forge_start()

    assert stop_called is True
    assert pm._forge_process is None
    assert any(not ok and "Превышено время ожидания" in msg for ok, msg in status_signals)
    assert any(action == "forge_start" and not ok for action, ok, msg in action_signals)


def test_forge_start_aborts_when_port_occupied_by_foreign_service(monkeypatch):
    """
    Проверяет, что если порт 7860 занят, но HTTP-handshake не проходит (чужой процесс),
    ProcessManager НЕ запускает второй экземпляр Forge на том же порту и отправляет отказ.
    """
    pm = ProcessManager()
    # is_forge_running -> False
    monkeypatch.setattr(pm, "is_forge_running", lambda: False)
    # is_port_open -> True
    monkeypatch.setattr(pm, "is_port_open", lambda port=7860, timeout=0.3: True)
    # handshake -> False
    monkeypatch.setattr("services.process_manager.verify_forge_http_handshake", lambda *a, **kw: False)
    # inspect_port_occupant -> occupier found
    monkeypatch.setattr(pm, "inspect_port_occupant", lambda port: (True, 9999, "foreign_app.exe"))

    popen_called = False
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: setattr(popen_called, "val", True))

    status_signals = []
    pm.forge_status_changed.connect(lambda ok, msg: status_signals.append((ok, msg)))

    pm._run_forge_start()

    assert popen_called is False
    assert len(status_signals) == 1
    ok, msg = status_signals[0]
    assert ok is False
    assert "Порт 7860 уже занят" in msg
    assert "foreign_app.exe" in msg


def test_forge_start_reuses_when_port_occupied_by_alive_forge(monkeypatch):
    """
    Проверяет, что если порт 7860 открыт и handshake успешен,
    ProcessManager фиксирует готовность Forge и не производит повторный запуск.
    """
    pm = ProcessManager()
    monkeypatch.setattr(pm, "is_forge_running", lambda: False)
    monkeypatch.setattr(pm, "is_port_open", lambda port=7860, timeout=0.3: True)
    monkeypatch.setattr("services.process_manager.verify_forge_http_handshake", lambda *a, **kw: True)

    popen_called = False
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: setattr(popen_called, "val", True))

    status_signals = []
    pm.forge_status_changed.connect(lambda ok, msg: status_signals.append((ok, msg)))

    pm._run_forge_start()

    assert popen_called is False
    assert len(status_signals) == 1
    ok, msg = status_signals[0]
    assert ok is True
    assert "Forge уже запущен" in msg


