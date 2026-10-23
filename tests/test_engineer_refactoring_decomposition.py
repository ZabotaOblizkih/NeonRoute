"""
tests/test_engineer_refactoring_decomposition.py
Unit tests verifying the architectural decomposition of services/subagent_engineer.py
into services/engineer_prompts.py, services/engineer_diagnostics.py, and services/code_engine.py.
"""

import sys
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent
while PROJECT_ROOT.name and not (PROJECT_ROOT / "services").exists() and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 1. Проверка экспортов из engineer_prompts
from services.engineer_prompts import (
    PACKAGE_PIP_MAP,
    POPULAR_LIBS,
    CODE_CREATE_TRIGGERS,
    scan_installed_libraries,
    resolve_pip_package,
    get_engineer_system_prompt,
    MINI_ANTIGRAVITY_SYSTEM_PROMPT,
    get_architect_prompt,
    get_rca_prompt,
)

# 2. Проверка экспортов из engineer_diagnostics
from services.engineer_diagnostics import (
    extract_error_fingerprint,
    diagnose_runtime_error,
    extract_crash_slice,
)

# 3. Проверка экспортов из code_engine
from services.code_engine import (
    CodeValidator,
    ASTSkeletonizer,
)

# 4. Проверка обратной совместимости из subagent_engineer
from services.subagent_engineer import (
    SubagentEngineer,
    _extract_pure_py,
    _diagnose_error,
    _extract_error_fingerprint,
    _validate_python_syntax,
    _validate_code_integrity,
    _apply_ast_linter_to_code,
)


def test_engineer_prompts_structure():
    assert "docx" in PACKAGE_PIP_MAP
    assert PACKAGE_PIP_MAP["docx"] == "python-docx"
    assert "openpyxl" in POPULAR_LIBS
    assert isinstance(scan_installed_libraries(), list)
    assert resolve_pip_package("docx") == "python-docx"
    assert resolve_pip_package("numpy") == "numpy"
    assert resolve_pip_package("unknown_module_xyz") is None

    p_base = get_engineer_system_prompt()
    assert "Элария" in p_base
    assert "СТРОГИЕ ПРАВИЛА" in p_base
    assert "Элария" in MINI_ANTIGRAVITY_SYSTEM_PROMPT
    assert "СТРОГИЕ ПРАВИЛА" in MINI_ANTIGRAVITY_SYSTEM_PROMPT

    p_excel = get_engineer_system_prompt("Создай отчет в excel .xlsx")
    assert "СТАНДАРТ EXCEL" in p_excel

    p_word = get_engineer_system_prompt("Создай приказ в ворде .docx")
    assert "СТАНДАРТ WORD" in p_word

    p_arch = get_architect_prompt(["openpyxl"], ["main.py"])
    assert "главный системный архитектор" in p_arch
    assert "openpyxl" in p_arch
    assert "main.py" in p_arch

    p_rca = get_rca_prompt()
    assert "Первопричина сбоя" in p_rca


def test_engineer_diagnostics_functionality():
    fp = extract_error_fingerprint("Traceback (most recent call last):\n  File 'test.py', line 10\nZeroDivisionError: division by zero")
    assert "test.py:10" in fp
    assert "ZeroDivisionError" in fp

    diag_docx = diagnose_runtime_error("KeyError: 'no style with name custom'")
    assert "В python-docx нельзя указывать вымышленные стили" in diag_docx

    diag_openpyxl = diagnose_runtime_error("'Cell' object has no attribute 'formula'")
    assert "В openpyxl у ячеек нет свойства .formula" in diag_openpyxl


def test_extract_crash_slice():
    assert extract_crash_slice("", "Error") == ""
    assert extract_crash_slice("print(1)", "") == ""

    code = "\n".join(f"line_{i} = {i}" for i in range(1, 31))
    tb = (
        'Traceback (most recent call last):\n'
        '  File "C:\\workspace\\main.py", line 15, in <module>\n'
        'ZeroDivisionError: division by zero'
    )
    # Test with filename
    res = extract_crash_slice(code, tb, filename="main.py", window=3)
    assert "  15 > line_15 = 15  # <-- ОШИБКА" in res
    assert "  12   line_12 = 12" in res
    assert "  18   line_18 = 18" in res
    # Line 11 should be outside window (15 - 3 = 12)
    assert "line_11" not in res



def test_code_validator_methods():
    # 1. Syntax check
    valid, err = CodeValidator.validate_python_syntax("mod.py", "def test():\n    return 42\n", is_entry_file=False)
    assert valid is True
    assert err is None

    # Safety Guard
    invalid, s_err = CodeValidator.validate_python_syntax("mod.py", "import socket\n", is_entry_file=False)
    assert invalid is False
    assert "AST Safety Guard" in s_err

    # MissingEntryPoint
    invalid_ep, ep_err = CodeValidator.validate_python_syntax("main.py", "def foo():\n    return 1\n", is_entry_file=True)
    assert invalid_ep is False
    assert "MissingEntryPoint" in ep_err

    # Stub Guard
    invalid_stub, stub_err = CodeValidator.validate_python_syntax(
        "main.py",
        "def custom_logic():\n    pass\nif __name__ == '__main__':\n    custom_logic()\n",
        is_entry_file=True
    )
    assert invalid_stub is False
    assert "содержит пустую заглушку 'pass'" in stub_err

    # 2. Scope Guard
    sem_err = CodeValidator.check_undefined_names("def run():\n    return undefined_var_xyz + 1\n", "script.py")
    assert sem_err is not None
    assert "undefined_var_xyz" in sem_err

    # 3. Linter
    linted = CodeValidator.apply_ast_linter_to_code("wb = Workbook()\nws = wb.active\n")
    assert "import openpyxl" in linted

    # 4. Skeleton
    skel = CodeValidator.generate_ast_skeleton("class MyService:\n    def do_work(self, x: int):\n        return x * 2\n")
    assert "class MyService" in skel
    assert "def do_work" in skel
    assert "..." in skel


def test_subagent_engineer_backward_compatibility():
    # Проверка вызова через staticmethod класса
    assert SubagentEngineer.get_system_prompt() == get_engineer_system_prompt()
    assert SubagentEngineer._extract_error_fingerprint("") == ""
    assert callable(SubagentEngineer._diagnose_error)
    assert callable(SubagentEngineer._validate_python_syntax)
    assert callable(SubagentEngineer._validate_code_integrity)
    assert callable(SubagentEngineer._apply_ast_linter_to_code)

    # Проверка вызова через функции модуля
    assert _extract_pure_py("```python\nprint(1)\n```") == "print(1)"
    assert callable(_diagnose_error)
    assert callable(_extract_error_fingerprint)
    assert callable(_validate_python_syntax)
    assert callable(_validate_code_integrity)
    assert callable(_apply_ast_linter_to_code)
