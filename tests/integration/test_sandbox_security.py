"""
Integration security tests for SandboxManager and SandboxGuard.
Validates:
1. Path Traversal attack prevention (relative directory escape, system paths, absolute cross-drive paths).
2. Process timeout killing without orphan processes (infinite while True loop).
3. Forbidden API / dangerous imports isolation.
4. Clean session scoping and workspace containment.
"""
import time
from pathlib import Path
import pytest

from services.sandbox_manager import SandboxManager
from services.sandbox_guard import _is_safe_path
from services.code_validator import CodeValidator


def test_path_traversal_prevention_on_read():
    """Проверка блокировки попыток чтения за пределами рабочей директории."""
    ws = Path("C:/NeonRoute/workspace").resolve()

    traversal_payloads = [
        "../../config.json",
        "..\\..\\config.json",
        "nested/../../../secret.txt",
        "/etc/passwd",
        "C:/Windows/System32/drivers/etc/hosts",
        "C:/Program Files/evil.exe",
    ]

    for payload in traversal_payloads:
        p = (ws / payload).resolve()
        assert not _is_safe_path(p, ws, allow_ws_root=False), (
            f"Path Traversal уязвимость: путь '{payload}' ({p}) был ошибочно сочтен безопасным!"
        )


def test_clean_workspace_filename_sanitization():
    """Проверка санитизации имен файлов: срезание относительных путей."""
    assert SandboxManager.clean_workspace_filename("../../../../app.py") == "app.py"
    assert SandboxManager.clean_workspace_filename("..\\..\\nested\\script.py") == "nested/script.py"
    assert SandboxManager.clean_workspace_filename("clean_module.py") == "clean_module.py"


def test_infinite_loop_timeout_termination():
    """
    Проверка аварийного завершения скрипта по таймауту.
    Скрипт с бесконечным циклом обязан быть принудительно убит по таймауту (2 сек),
    не подвешивая процесс и возвращая ScriptExecutionResult с success=False.
    """
    session_id = "test_timeout_guard_sess"

    with SandboxManager.session_scope(session_id):
        infinite_loop_code = (
            "import time\n\n"
            "def main():\n"
            "    while True:\n"
            "        time.sleep(0.1)\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )

        write_res = SandboxManager.write_file("infinite_loop.py", infinite_loop_code)
        assert "[Успешно]" in write_res

        t_start = time.perf_counter()
        exec_res = SandboxManager.run_script("infinite_loop.py", timeout=2)
        elapsed = time.perf_counter() - t_start

        # Должен отработать примерно за 2-3 секунды
        assert elapsed < 5.0, f"Таймаут не сработал вовремя: прошло {elapsed:.1f}с"
        assert exec_res.success is False, "Скрипт с бесконечным циклом не должен завершаться с успехом!"
        assert "Таймаут" in exec_res.output or "timeout" in exec_res.output.lower() or exec_res.return_code != 0


def test_forbidden_imports_rejected_by_validator():
    """Проверка отклонения опасных модулей (subprocess, socket, ctypes)."""
    dangerous_snippets = [
        ("hack_socket.py", "import socket\ns = socket.socket()\n"),
        ("hack_subproc.py", "import subprocess\nsubprocess.run('dir')\n"),
        ("hack_ctypes.py", "import ctypes\nctypes.windll.user32.MessageBoxW()\n"),
    ]

    for fname, code in dangerous_snippets:
        is_valid, msg = CodeValidator.validate_python_syntax(fname, code, is_entry_file=False)
        assert not is_valid, f"Опасный код '{fname}' прошел валидацию!"
        assert any(mod in msg for mod in ("socket", "subprocess", "ctypes")), (
            f"В сообщении об ошибке нет названия заблокированного модуля: '{msg}'"
        )
