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
