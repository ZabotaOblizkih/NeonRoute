"""
tests/unit/test_llama_downloader.py
===================================
Unit-тесты для сервиса автозагрузки llama-server (services/llama_downloader.py)
и CLI-скрипта (scripts/download_llama_server.py).
"""

import io
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from services.llama_downloader import (
    resolve_required_build,
    select_download_urls,
    is_llama_server_installed,
    extract_binaries,
    download_and_extract_llama_server,
)


MOCK_ASSETS = [
    {
        "name": "cudart-llama-bin-win-cuda-12.4-x64.zip",
        "browser_download_url": "https://example.com/cudart-12.4.zip",
    },
    {
        "name": "llama-b12426-bin-win-cuda-12.4-x64.zip",
        "browser_download_url": "https://example.com/cuda-12.4.zip",
    },
    {
        "name": "llama-b12426-bin-win-vulkan-x64.zip",
        "browser_download_url": "https://example.com/vulkan.zip",
    },
    {
        "name": "llama-b12426-bin-win-cpu-x64.zip",
        "browser_download_url": "https://example.com/cpu.zip",
    },
    {
        "name": "llama-b12426-bin-win-cuda-12.4-arm64.zip",
        "browser_download_url": "https://example.com/arm.zip",
    },
]


def test_resolve_required_build():
    """Проверка выбора типа сборки по вендору."""
    assert resolve_required_build("nvidia") == "cuda"
    assert resolve_required_build("GeForce RTX 4070") == "cuda"
    assert resolve_required_build("amd") == "vulkan"
    assert resolve_required_build("AMD Radeon") == "vulkan"
    assert resolve_required_build("intel") == "vulkan"
    assert resolve_required_build("cpu") == "cpu"
    assert resolve_required_build("unknown") == "cpu"


def test_select_download_urls_cuda():
    """Проверка выбора ассетов для CUDA: основной бинарник + cudart."""
    urls = select_download_urls(MOCK_ASSETS, "cuda")
    names = [name for name, _ in urls]
    assert "llama-b12426-bin-win-cuda-12.4-x64.zip" in names
    assert "cudart-llama-bin-win-cuda-12.4-x64.zip" in names
    assert len(urls) == 2


def test_select_download_urls_vulkan():
    """Проверка выбора ассетов для Vulkan (AMD/Intel)."""
    urls = select_download_urls(MOCK_ASSETS, "vulkan")
    names = [name for name, _ in urls]
    assert names == ["llama-b12426-bin-win-vulkan-x64.zip"]


def test_select_download_urls_cpu():
    """Проверка выбора ассетов для CPU."""
    urls = select_download_urls(MOCK_ASSETS, "cpu")
    names = [name for name, _ in urls]
    assert names == ["llama-b12426-bin-win-cpu-x64.zip"]


def test_is_llama_server_installed(tmp_path):
    """Проверка определения установленного llama-server."""
    assert is_llama_server_installed(tmp_path) is False

    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"MZ" + b"\x00" * 2048)
    assert is_llama_server_installed(tmp_path) is True


def test_is_llama_server_installed_cuda_dependencies(tmp_path):
    """
    Проверка: если присутствует ggml-cuda.dll, наличие cudart64_*.dll
    И cublas64_*.dll строго обязательно.
    """
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"MZ" + b"\x00" * 2048)

    cuda_dll = tmp_path / "ggml-cuda.dll"
    cuda_dll.write_bytes(b"CUDA" + b"\x00" * 1024)

    # Только ggml-cuda.dll без cudart и cublas -> False
    assert is_llama_server_installed(tmp_path) is False

    # Только cudart без cublas -> False
    cudart = tmp_path / "cudart64_12.dll"
    cudart.write_bytes(b"CUDART" + b"\x00" * 1024)
    assert is_llama_server_installed(tmp_path) is False

    # И cudart, и cublas -> True
    cublas = tmp_path / "cublas64_12.dll"
    cublas.write_bytes(b"CUBLAS" + b"\x00" * 1024)
    assert is_llama_server_installed(tmp_path) is True


def test_download_file_retry_and_resume(tmp_path):
    """Тест повторных попыток скачивания и докачки (Range/206)."""
    dest = tmp_path / "test_file.bin"
    mock_url = "https://example.com/big_file.bin"

    # 1. Попытка с ошибкой на 1-й итерации, успех на 2-й
    attempts = [0]

    class FakeResponse:
        status = 200
        headers = {"Content-Length": "100"}

        def __init__(self, data):
            self._io = io.BytesIO(data)

        def read(self, size):
            return self._io.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def mock_urlopen(req, timeout):
        attempts[0] += 1
        if attempts[0] == 1:
            raise urllib.error.URLError("Connection reset")
        return FakeResponse(b"A" * 100)

    import urllib.error
    from services.llama_downloader import download_file

    with patch("urllib.request.urlopen", side_effect=mock_urlopen), \
         patch("time.sleep", return_value=None) as mock_sleep:
        download_file(mock_url, dest, timeout=10, max_retries=3, retry_delay=0.1)
        assert attempts[0] == 2
        assert mock_sleep.called
        assert dest.is_file()
        assert dest.stat().st_size == 100


def test_verify_llama_server_binary_cuda_and_devices(tmp_path):
    """Проверка верификации: отказ при отсутствии cudart или при (none) устройствах."""
    from services.llama_downloader import verify_llama_server_binary

    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"MZ" + b"\x00" * 2048)
    cuda_dll = tmp_path / "ggml-cuda.dll"
    cuda_dll.write_bytes(b"CUDA")

    # Без cudart/cublas -> False
    ok, err = verify_llama_server_binary(tmp_path)
    assert ok is False
    assert "отсутствуют cudart64_*.dll или cublas64_*.dll" in err

    # Добавляем DLL
    (tmp_path / "cudart64_12.dll").write_bytes(b"CUDART")
    (tmp_path / "cublas64_12.dll").write_bytes(b"CUBLAS")

    # Мокаем запуск --version и --list-devices
    def fake_run(cmd, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.returncode = 0
        if "--version" in cmd:
            mock_res.stdout = "llama-server version 0.9.0"
        elif "--list-devices" in cmd:
            mock_res.stdout = "Available devices:\n (none)\n"
        return mock_res

    with patch("subprocess.run", side_effect=fake_run), \
         patch("services.layer_budgeter.LayerBudgeter.detect_hardware_kv_profile", return_value={"vendor": "nvidia"}):
        ok, err = verify_llama_server_binary(tmp_path)
        assert ok is False
        assert "Available devices: (none)" in err


def test_extract_binaries_filters_and_flattens(tmp_path):
    """Проверка распаковки: извлекаются только .exe и .dll прямо в целевую папку."""
    zip_path = tmp_path / "test_bin.zip"
    dest_dir = tmp_path / "extracted_bin"

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("subfolder/llama-server.exe", b"fake exe data")
        zf.writestr("subfolder/ggml.dll", b"fake dll data")
        zf.writestr("subfolder/readme.txt", b"text should be ignored")
        zf.writestr("subfolder/license.md", b"markdown should be ignored")

    count = extract_binaries(zip_path, dest_dir)
    assert count == 2
    assert (dest_dir / "llama-server.exe").is_file()
    assert (dest_dir / "ggml.dll").is_file()
    assert not (dest_dir / "readme.txt").exists()
    assert not (dest_dir / "license.md").exists()


def test_download_and_extract_mocked(tmp_path):
    """Тестирование полного цикла скачивания с моком сети и верификации."""
    fake_zip_bytes = io.BytesIO()
    with zipfile.ZipFile(fake_zip_bytes, "w") as zf:
        zf.writestr("llama-server.exe", b"MZ" + b"\x00" * 2048)
        zf.writestr("ggml-cuda.dll", b"DLL" + b"\x00" * 1024)

    def mock_download_file(url, dest_path, progress=None):
        dest_path.write_bytes(fake_zip_bytes.getvalue())

    with patch("services.llama_downloader.fetch_release_assets", return_value=("b12426", MOCK_ASSETS)), \
         patch("services.llama_downloader.download_file", side_effect=mock_download_file), \
         patch("services.llama_downloader.verify_llama_server_binary", return_value=(True, "llama-server version 1.2.3")):

        ok, msg = download_and_extract_llama_server(target_dir=tmp_path, vendor="nvidia")
        assert ok is True
        assert "успешно установлен" in msg
        assert (tmp_path / "llama-server.exe").is_file()


def test_download_and_extract_abort_on_second_archive_error(tmp_path):
    """
    Проверяет, что при сбое скачивания второго архива (например, cudart)
    распаковка extract_binaries НЕ вызывается ни для одного архива,
    а целевая папка bin/ остаётся нетронутой (без полурабочего llama-server.exe).
    """
    download_calls = []

    def mock_download_file(url, dest_path, progress=None):
        download_calls.append(url)
        if len(download_calls) == 2:
            raise RuntimeError("Connection dropped during cudart download")
        dest_path.write_bytes(b"PK\x03\x04fake_first_zip")

    with patch("services.llama_downloader.fetch_release_assets", return_value=("b12426", MOCK_ASSETS)), \
         patch("services.llama_downloader.download_file", side_effect=mock_download_file), \
         patch("services.llama_downloader.extract_binaries") as mock_extract, \
         patch("services.llama_downloader.verify_llama_server_binary") as mock_verify:

        ok, msg = download_and_extract_llama_server(target_dir=tmp_path, vendor="nvidia")

        assert ok is False
        assert "Ошибка при скачивании" in msg
        assert "Connection dropped during cudart download" in msg
        # extract_binaries не должен вызываться вообще
        mock_extract.assert_not_called()
        mock_verify.assert_not_called()
        # В целевой папке не должно появиться файлов
        assert list(tmp_path.iterdir()) == []


def test_cli_download_llama_server_if_missing_error_exits_code_1(tmp_path, monkeypatch):
    """
    Проверяет, что при сбое скачивания CLI download_llama_server.py
    возвращает код 1 даже с флагом --if-missing (ADR-30 fix).
    """
    import sys
    from scripts.download_llama_server import main

    monkeypatch.setattr(
        "scripts.download_llama_server.download_and_extract_llama_server",
        lambda **kwargs: (False, "Network connection error")
    )
    monkeypatch.setattr(
        "scripts.download_llama_server.is_llama_server_installed",
        lambda path: False
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["download_llama_server.py", "--if-missing", "--target-dir", str(tmp_path)]
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1


def test_cli_download_llama_server_if_missing_already_installed_exits_0(tmp_path, monkeypatch):
    """Проверяет, что если движок уже установлен, --if-missing завершается с кодом 0."""
    import sys
    from scripts.download_llama_server import main

    monkeypatch.setattr(
        "scripts.download_llama_server.is_llama_server_installed",
        lambda path: True
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["download_llama_server.py", "--if-missing", "--target-dir", str(tmp_path)]
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
