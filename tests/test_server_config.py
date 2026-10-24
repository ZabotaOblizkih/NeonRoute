"""
Unit tests for services.server_config (ServerConfigResolver).
"""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import config
from services.server_config import ServerConfigResolver, _sanitize_ini_value


def test_sanitize_ini_value():
    assert _sanitize_ini_value(None) == ""
    assert _sanitize_ini_value("") == ""
    assert _sanitize_ini_value("  hello world  ") == "hello world"
    assert _sanitize_ini_value('"C:\\path\\with spaces\\bin"') == "C:\\path\\with spaces\\bin"
    assert _sanitize_ini_value("'C:\\path\\with spaces\\bin'") == "C:\\path\\with spaces\\bin"
    assert _sanitize_ini_value("1234 ; inline comment") == "1234"
    assert _sanitize_ini_value("q8_0 # comment here") == "q8_0"
    assert _sanitize_ini_value('"quoted_value" ; and comment') == "quoted_value"


def test_load_ini_defaults_when_missing(monkeypatch, tmp_path):
    missing_ini = tmp_path / "non_existent.ini"
    monkeypatch.setattr(config, "SERVER_CONFIG_INI_PATH", missing_ini)

    cfg = ServerConfigResolver.load_ini()
    assert cfg["host"] == "127.0.0.1"
    assert cfg["port"] == 1234
    assert cfg["default_context"] == 14336
    assert cfg["gpu_layers"] == 99
    assert cfg["flash_attn"] == "on"
    assert cfg["cache_type_k"] == "q8_0"
    assert cfg["cache_type_v"] == "q8_0"
    assert cfg["parallel_slots"] == 1
    assert cfg["verbose"] is False
    assert cfg["log_file"] == "logs/llama_server.log"


def test_load_ini_custom_values(monkeypatch, tmp_path):
    custom_ini = tmp_path / "server_config.ini"
    custom_ini.write_text(
        """[SERVER]
host = "0.0.0.0"
port = 8080 ; custom port
default_context = 24576
gpu_layers = 45
flash_attn = off
cache_type_k = f16
cache_type_v = q4_0
parallel_slots = 1

[PATHS]
server_bin_path = "D:\\custom\\llama-server.exe"
cuda_dll_dir = D:\\custom\\cuda
models_dir = "D:\\models\\GGUF"
default_model = custom_model.gguf

[LOGGING]
verbose = true
log_file = logs/custom.log
""",
        encoding="utf-8"
    )
    monkeypatch.setattr(config, "SERVER_CONFIG_INI_PATH", custom_ini)

    cfg = ServerConfigResolver.load_ini()
    assert cfg["host"] == "0.0.0.0"
    assert cfg["port"] == 8080
    assert cfg["default_context"] == 24576
    assert cfg["gpu_layers"] == 45
    assert cfg["flash_attn"] == "off"
    assert cfg["cache_type_k"] == "f16"
    assert cfg["cache_type_v"] == "q4_0"
    assert cfg["parallel_slots"] == 1
    assert cfg["server_bin_path"] == "D:\\custom\\llama-server.exe"
    assert cfg["cuda_dll_dir"] == "D:\\custom\\cuda"
    assert cfg["models_dir"] == "D:\\models\\GGUF"
    assert cfg["default_model"] == "custom_model.gguf"
    assert cfg["verbose"] is True
    assert cfg["log_file"] == "logs/custom.log"


def test_resolve_server_exe_level1_local_bin(monkeypatch, tmp_path):
    local_bin = tmp_path / "bin"
    local_bin.mkdir(parents=True)
    exe_file = local_bin / "llama-server.exe"
    exe_file.touch()

    monkeypatch.setattr(config, "LOCAL_BIN_DIR", local_bin)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)

    resolved = ServerConfigResolver.resolve_server_exe()
    assert resolved == exe_file.resolve()


def test_resolve_server_exe_level2_ini_path(monkeypatch, tmp_path):
    # Ensure Level 1 does not match
    monkeypatch.setattr(config, "LOCAL_BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)

    custom_dir = tmp_path / "custom_bin"
    custom_dir.mkdir(parents=True)
    custom_exe = custom_dir / "llama-server.exe"
    custom_exe.touch()

    monkeypatch.setattr(
        ServerConfigResolver,
        "load_ini",
        staticmethod(lambda: {"server_bin_path": str(custom_exe)})
    )

    resolved = ServerConfigResolver.resolve_server_exe()
    assert resolved == custom_exe.resolve()


def test_resolve_cuda_dir_level1_beside_exe(tmp_path):
    exe_dir = tmp_path / "bin"
    exe_dir.mkdir(parents=True)
    exe = exe_dir / "llama-server.exe"
    exe.touch()
    dll = exe_dir / "cudart64_12.dll"
    dll.touch()

    cuda_dir = ServerConfigResolver.resolve_cuda_dir(exe)
    assert cuda_dir == exe_dir.resolve()


def test_discover_gguf_models(monkeypatch, tmp_path):
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True)

    # Base model directly in models_dir
    m1 = models_dir / "test_model_q4.gguf"
    m1.touch()

    # Model in subfolder
    sub_dir = models_dir / "publisher" / "repo"
    sub_dir.mkdir(parents=True)
    m2 = sub_dir / "deep_model_q8.gguf"
    m2.touch()

    # Projection file (must be ignored)
    proj = sub_dir / "mmproj-model-f16.gguf"
    proj.touch()

    monkeypatch.setattr(config, "LOCAL_MODELS_DIR", models_dir)
    monkeypatch.setattr(ServerConfigResolver, "load_ini", staticmethod(lambda: {}))
    # Patch home to empty tmp directory so ~/.lmstudio is not included
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    discovered = ServerConfigResolver.discover_gguf_models()
    assert "test_model_q4" in discovered
    assert discovered["test_model_q4"] == m1.resolve()

    assert "repo/deep_model_q8.gguf" in discovered
    assert discovered["repo/deep_model_q8.gguf"] == m2.resolve()

    # Verify mmproj is not in discovered models
    for key in discovered:
        assert "mmproj" not in key.lower()


def test_resolve_model_path_various_identifiers(monkeypatch, tmp_path):
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True)

    m1 = models_dir / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
    m1.touch()

    sub = models_dir / "bartowski" / "L3-8B-Stheno-v3.2-GGUF"
    sub.mkdir(parents=True)
    m2 = sub / "L3-8B-Stheno-v3.2-Q4_K_S.gguf"
    m2.touch()

    monkeypatch.setattr(config, "LOCAL_MODELS_DIR", models_dir)
    monkeypatch.setattr(ServerConfigResolver, "load_ini", staticmethod(lambda: {"default_model": "L3-8B-Stheno-v3.2-Q4_K_S.gguf"}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    # 1. Exact key match
    assert ServerConfigResolver.resolve_model_path("Qwen2.5-Coder-7B-Instruct-Q4_K_M") == m1.resolve()
    # 2. Filename match
    assert ServerConfigResolver.resolve_model_path("Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf") == m1.resolve()
    # 3. Slug / normalized match
    assert ServerConfigResolver.resolve_model_path("qwen2.5-coder-7b-instruct") == m1.resolve()
    assert ServerConfigResolver.resolve_model_path("l3-8b-stheno-v3.2") == m2.resolve()
    # 4. Fallback to default_model when None
    assert ServerConfigResolver.resolve_model_path(None) == m2.resolve()


def test_resolve_mmproj_for_model(tmp_path):
    # Vision model folder with mmproj
    vl_dir = tmp_path / "Qwen2.5-VL-7B-Instruct-GGUF"
    vl_dir.mkdir(parents=True)
    vl_model = vl_dir / "Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf"
    vl_model.touch()
    vl_proj = vl_dir / "mmproj-model-f16.gguf"
    vl_proj.touch()

    # Pure text model in another folder
    txt_dir = tmp_path / "Qwen2.5-Coder-7B-Instruct-GGUF"
    txt_dir.mkdir(parents=True)
    txt_model = txt_dir / "qwen2.5-coder-7b-instruct-q5_k_m.gguf"
    txt_model.touch()

    # Projector for vision model
    found_proj = ServerConfigResolver.resolve_mmproj_for_model(vl_model)
    assert found_proj == vl_proj.resolve()

    # Projector for text model should be None
    found_txt_proj = ServerConfigResolver.resolve_mmproj_for_model(txt_model)
    assert found_txt_proj is None
