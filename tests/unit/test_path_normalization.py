"""
TDD Red-Phase Tests: Path Normalization and Working Directory Independence.

These tests enforce:
1. config.py directory paths are absolute, rooted, and contain no hardcoded fallback paths.
2. SandboxManager file creation and error messages do not hardcode 'C:\\NeonRoute'.
3. Critical services (skill_router, process_manager) do not rely on process current working directory (cwd).
4. PATH environment variable generation in process_manager uses os.pathsep instead of hardcoded ';'.
5. UI components and config.json contain no hardcoded 'C:/NeonRoute' or 'C:\\NeonRoute' paths.
"""

import os
import sys
import json
import inspect
import importlib
from pathlib import Path
import pytest

import config
from services.sandbox_manager import SandboxManager


def test_config_paths_are_absolute_and_rooted():
    """
    1. Checks that key directories in config.py are absolute pathlib.Path objects.
    2. Checks that config.py source code contains no hardcoded Windows paths ('C:\\NeonRoute', 'C:\\neyroGPD').
    """
    key_dirs = [
        config.BASE_DIR,
        config.DATA_DIR,
        config.LOGS_DIR,
        config.WORKSPACE_DIR,
        config.OUTPUTS_DIR,
        config.HISTORY_DIR,
    ]
    for p in key_dirs:
        assert isinstance(p, Path), f"Config path {p} must be a pathlib.Path instance"
        assert p.is_absolute(), f"Config path {p} must be absolute"

    config_source = Path(config.__file__).read_text(encoding="utf-8")
    assert "C:\\NeonRoute" not in config_source, "config.py contains hardcoded 'C:\\NeonRoute'"
    assert "C:\\neyroGPD" not in config_source, (
        "config.py contains hardcoded fallback 'C:\\neyroGPD' instead of dynamic or environment resolution"
    )


def test_sandbox_manager_workspace_messages_no_hardcoded_c_drive(tmp_path, monkeypatch):
    """
    Checks that SandboxManager messages (success and error) dynamically reflect
    the active workspace directory and never output hardcoded 'C:\\NeonRoute'.
    """
    custom_ws = tmp_path / "custom_workspace"
    custom_ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(SandboxManager, "get_workspace_dir", classmethod(lambda cls: custom_ws))

    # 1. Error message on missing script
    res_error = SandboxManager.run_script("non_existent_script.py")
    assert "C:\\NeonRoute" not in res_error.output, (
        f"run_script error output contains hardcoded 'C:\\NeonRoute': {res_error.output}"
    )
    assert str(custom_ws) in res_error.output, (
        f"run_script error output must contain active workspace path '{custom_ws}', got: {res_error.output}"
    )

    # 2. Success message on file write
    write_msg = SandboxManager.write_file("test_probe.txt", "print('hello')")
    assert "C:\\NeonRoute" not in write_msg, (
        f"write_file message contains hardcoded 'C:\\NeonRoute': {write_msg}"
    )
    assert str(custom_ws) in write_msg, (
        f"write_file message must contain active workspace path '{custom_ws}', got: {write_msg}"
    )


def test_cwd_independence_for_critical_services(tmp_path, monkeypatch):
    """
    Verifies that changing process current working directory (cwd) does not break
    critical services path resolution:
    - services.skill_router.CUSTOM_ASSISTANT_SKILLS_FILE must reside in config.DATA_DIR.
    - services.process_manager must not use fragile Path("logs") bound to cwd.
    """
    monkeypatch.chdir(tmp_path)

    # 1. services.skill_router
    import services.skill_router
    importlib.reload(services.skill_router)
    skills_file = services.skill_router.CUSTOM_ASSISTANT_SKILLS_FILE.resolve()
    assert skills_file.is_relative_to(config.DATA_DIR.resolve()), (
        f"CUSTOM_ASSISTANT_SKILLS_FILE was resolved relative to cwd ({skills_file}) "
        f"instead of config.DATA_DIR ({config.DATA_DIR})"
    )

    # 2. services.process_manager
    from services.process_manager import ProcessManager
    src_llama = inspect.getsource(ProcessManager.start_llama_server_sync)
    src_forge = inspect.getsource(ProcessManager._start_forge_process)

    assert 'Path("logs")' not in src_llama, (
        "ProcessManager.start_llama_server_sync uses fragile Path('logs') relative to cwd instead of config.LOGS_DIR"
    )
    assert 'Path("logs")' not in src_forge, (
        "ProcessManager._start_forge_process uses fragile Path('logs') relative to cwd instead of config.LOGS_DIR"
    )


def test_path_env_separator_crossplatform():
    """
    Checks that ProcessManager.start_llama_server_sync does not hardcode
    Windows ';' separator when prepending directories to PATH.
    It must use os.pathsep for cross-platform compatibility.
    """
    from services.process_manager import ProcessManager

    src = inspect.getsource(ProcessManager.start_llama_server_sync)
    assert 'f"{str(cuda_dir)};' not in src and f";{{env" not in src, (
        "ProcessManager.start_llama_server_sync hardcodes ';' separator in PATH instead of os.pathsep"
    )


def test_ui_defaults_and_configs_no_hardcoded_c_drive():
    """
    Checks that ui.components.chat_header_widget and config.json do not
    contain hardcoded 'C:/NeonRoute' or 'C:\\NeonRoute' paths.
    """
    # 1. ui.components.chat_header_widget
    from ui.components import chat_header_widget
    header_src = inspect.getsource(chat_header_widget)
    assert "C:/NeonRoute" not in header_src and "C:\\NeonRoute" not in header_src, (
        "chat_header_widget contains hardcoded 'C:/NeonRoute' path for art_director_models.json"
    )

    # 2. config.json
    if config.CONFIG_PATH.exists():
        with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for key in ("background_path", "assistant_avatar_path"):
            val = cfg.get(key, "")
            assert not str(val).startswith("C:\\NeonRoute") and not str(val).startswith("C:/NeonRoute"), (
                f"config.json field '{key}' contains hardcoded path: {val}"
            )


def test_ui_dialogs_and_workers_no_hardcoded_c_drive():
    """
    Checks that ui/dialogs/clean_cache_dialog.py, ui/dialogs/help_dialog.py,
    ui/main_window.py, and ui/workers/tool_execution_worker.py contain no
    hardcoded 'C:\\NeonRoute' or 'C:/NeonRoute' string literals.
    """
    target_files = [
        config.BASE_DIR / "ui" / "dialogs" / "clean_cache_dialog.py",
        config.BASE_DIR / "ui" / "dialogs" / "help_dialog.py",
        config.BASE_DIR / "ui" / "main_window.py",
        config.BASE_DIR / "ui" / "workers" / "tool_execution_worker.py",
    ]
    for filepath in target_files:
        assert filepath.exists(), f"Target file does not exist: {filepath}"
        content = filepath.read_text(encoding="utf-8")
        assert r"C:\\NeonRoute" not in content, (
            f"File '{filepath.name}' contains hardcoded 'C:\\\\NeonRoute'"
        )
        assert "C:/NeonRoute" not in content, (
            f"File '{filepath.name}' contains hardcoded 'C:/NeonRoute'"
        )
