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
