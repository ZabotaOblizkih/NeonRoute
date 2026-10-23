"""
Unit tests for Sandbox Path Traversal and Workspace Escape Protection.
Tests:
1. Canonical path validation in SandboxManager.validate_write_path
2. Windows non-existing file support (parent resolution)
3. Audit hook in SandboxGuard for:
   - os.chdir
   - os.remove / os.unlink / os.rename / os.replace / shutil.rmtree
   - open (write vs read modes)
"""

import os
import sys
import pytest
from pathlib import Path

from config import WORKSPACE_DIR
from services.sandbox_manager import SandboxManager
from services.sandbox_guard import install_guard


def test_validate_write_path_valid_file():
    ws = SandboxManager.get_workspace_dir().resolve()
    target = ws / "valid_script.py"
    res = SandboxManager.validate_write_path(target)
    assert res == target


def test_validate_write_path_non_existent_nested_file():
    ws = SandboxManager.get_workspace_dir().resolve()
    target = ws / "new_dir" / "new_file.txt"
    res = SandboxManager.validate_write_path(target)
    assert res == target


def test_validate_write_path_traversal_blocked():
    traversal_path = Path("../../config.json")
    with pytest.raises(PermissionError) as exc_info:
        SandboxManager.validate_write_path(traversal_path)
    assert "[Sandbox] Попытка записи за пределы workspace:" in str(exc_info.value)


def test_validate_write_path_absolute_escape_blocked():
    outside_path = Path("C:/NeonRoute/config.json")
    with pytest.raises(PermissionError) as exc_info:
        SandboxManager.validate_write_path(outside_path)
    assert "[Sandbox] Попытка записи за пределы workspace:" in str(exc_info.value)


def test_validate_write_path_instance_and_class_call():
    ws = SandboxManager.get_workspace_dir().resolve()
    target = ws / "test_call.py"
    # Class call
    assert SandboxManager.validate_write_path(target) == target
    # Instance call
    sm = SandboxManager()
    assert sm.validate_write_path(target) == target


def test_audit_hook_chdir_outside_workspace_blocked():
    install_guard()
    resolved_ws = WORKSPACE_DIR.resolve()
    resolved_ws.mkdir(parents=True, exist_ok=True)

    # os.chdir inside workspace should succeed
    os.chdir(str(resolved_ws))

    # os.chdir outside workspace should fail
    with pytest.raises(PermissionError) as exc_info:
        os.chdir("..")
    assert "Смена директории за пределы workspace запрещена" in str(exc_info.value)


def test_audit_hook_write_outside_workspace_blocked():
    install_guard()
    outside_target = Path("../../escape_test.txt")
    with pytest.raises(PermissionError):
        with open(outside_target, "w", encoding="utf-8") as f:
            f.write("malicious")


def test_audit_hook_append_outside_workspace_blocked():
    install_guard()
    outside_target = Path("../../escape_append.txt")
    with pytest.raises(PermissionError):
        with open(outside_target, "a", encoding="utf-8") as f:
            f.write("malicious")


def test_audit_hook_read_outside_workspace_allowed_for_imports():
    install_guard()
    # Reading python standard library or venv files must not raise PermissionError
    import math
    import json
    import csv
    assert math.sqrt(16) == 4.0


def test_audit_hook_rename_outside_workspace_blocked():
    install_guard()
    resolved_ws = WORKSPACE_DIR.resolve()
    ws_file = resolved_ws / "rename_source.txt"
    ws_file.write_text("safe content", encoding="utf-8")

    dst_outside = Path("../../escaped_rename.txt")
    try:
        with pytest.raises(PermissionError):
            os.rename(str(ws_file), str(dst_outside))
    finally:
        if ws_file.exists():
            os.remove(str(ws_file))


def test_validate_write_path_tempfile_allowed():
    import tempfile
    temp_dir = Path(tempfile.gettempdir()).resolve()
    temp_target = temp_dir / "neonroute_temp_test.tmp"
    res = SandboxManager.validate_write_path(temp_target)
    assert res == temp_target


def test_ast_guard_allowed_and_banned_imports():
    ws = SandboxManager.get_workspace_dir().resolve()
    # Test allowed urllib.parse and shutil
    script_allowed = ws / "test_script_ok.py"
    script_allowed.write_text("import urllib.parse\nimport shutil\nprint('ok')", encoding="utf-8")

    try:
        # run_script check for banned modules:
        res = SandboxManager.run_script("test_script_ok.py")
        assert "AST Safety Guard" not in res.stderr

        # Test banned urllib.request
        script_banned = ws / "test_script_bad.py"
        script_banned.write_text("import urllib.request\nprint('bad')", encoding="utf-8")
        res_bad = SandboxManager.run_script("test_script_bad.py")
        assert not res_bad.success
        assert "AST Safety Guard" in res_bad.stderr
        assert "urllib.request" in res_bad.stderr

        # Test banned socket
        script_socket = ws / "test_script_socket.py"
        script_socket.write_text("import socket\nprint('bad')", encoding="utf-8")
        res_sock = SandboxManager.run_script("test_script_socket.py")
        assert not res_sock.success
        assert "AST Safety Guard" in res_sock.stderr
        assert "socket" in res_sock.stderr
    finally:
        for p in (script_allowed, ws / "test_script_bad.py", ws / "test_script_socket.py"):
            if p.exists():
                p.unlink()


def test_run_script_cwd_is_workspace_dir():
    ws = SandboxManager.get_workspace_dir().resolve()
    script = ws / "test_script_cwd.py"
    # The script prints os.getcwd()
    script.write_text("import os\nprint(f'CWD:{os.getcwd()}')", encoding="utf-8")
    try:
        res = SandboxManager.run_script("test_script_cwd.py")
        assert res.success
        assert f"CWD:{str(ws)}" in res.stdout
    finally:
        if script.exists():
            script.unlink()


def test_make_doc_excel_openpyxl_and_tempfile():
    ws = SandboxManager.get_workspace_dir().resolve()
    script = ws / "make_doc.py"
    script.write_text("""import tempfile
import openpyxl
from pathlib import Path

# 1. Проверка работы с tempfile
with tempfile.NamedTemporaryFile(delete=True) as tmp:
    tmp.write(b"sandbox temporary data")

# 2. Проверка работы openpyxl
wb = openpyxl.Workbook()
sheet = wb.active
sheet["A1"] = "NeonRoute"
sheet["B1"] = "Guard Calibrated"
wb.save("report.xlsx")
print("EXCEL_CREATED_SUCCESSFULLY")
""", encoding="utf-8")

    try:
        res = SandboxManager.run_script("make_doc.py")
        assert res.success, f"Script failed with stderr: {res.stderr}"
        assert res.return_code == 0
        assert "EXCEL_CREATED_SUCCESSFULLY" in res.stdout
        excel_path = ws / "report.xlsx"
        assert excel_path.exists()
    finally:
        if script.exists():
            script.unlink()
        excel_path = ws / "report.xlsx"
        if excel_path.exists():
            excel_path.unlink()


def test_audit_hook_write_and_delete_in_root_and_windows_blocked():
    install_guard()

    # 1. Попытка записи в C:/NeonRoute
    with pytest.raises(PermissionError):
        with open("C:/NeonRoute/malicious_write_test.txt", "w", encoding="utf-8") as f:
            f.write("bad")

    # 2. Попытка записи в C:/Windows
    with pytest.raises(PermissionError):
        with open("C:/Windows/malicious_write_test.txt", "w", encoding="utf-8") as f:
            f.write("bad")

    # 3. Попытка удаления в C:/NeonRoute
    with pytest.raises(PermissionError):
        os.remove("C:/NeonRoute/non_existent_target.txt")

    # 4. Попытка удаления в C:/Windows
    with pytest.raises(PermissionError):
        os.remove("C:/Windows/non_existent_target.txt")

