import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

def test_01_code_validator():
    from services.code_validator import CodeValidator
    
    # 1. Scope Guard (необъявленные переменные)
    bad_code = "def calc():\n    return unknown_var + 1"
    assert "unknown_var" in CodeValidator.check_undefined_names(bad_code, "script.py")
    
    # 2. Safety Guard (Запрет опасных импортов)
    unsafe_code = "import socket\nprint('hack')"
    is_valid, msg = CodeValidator.validate_python_syntax("script.py", unsafe_code, is_entry_file=False)
    assert not is_valid and "socket" in msg
    
    # 3. Entry Point Guard
    no_entry = "class App:\n    def start(self):\n        pass"
    is_valid, msg = CodeValidator.validate_python_syntax("main.py", no_entry, is_entry_file=True)
    assert not is_valid and "MissingEntryPoint" in msg
    
    # 4. AST Linter
    linted = CodeValidator.apply_ast_linter_to_code("res = math.pi + random.randint(1, 10)")
    assert "import math" in linted and "import random" in linted

def test_02_patch_engine(tmp_path):
    from services.patch_engine import PatchApplier, SelfHealingEngine
    
    orig = "def test():\n    x = 1\n    return x\n"
    sb = "    x = 1"
    rb = "    x = 2\n    y = 3"
    
    # Exact patch
    res = PatchApplier.apply_patch(orig, sb, rb)
    assert res.success
    assert "y = 3" in res.new_content
    
    # Fuzzy patch (одинарные кавычки в исходнике, двойные в патче)
    res_f = PatchApplier.apply_patch("print('hello')", 'print("hello")', "print('bye')")
    assert res_f.success
    assert "bye" in res_f.new_content
    
    # Transactional engine (имитация файла на диске)
    test_file = tmp_path / "app.py"
    test_file.write_text("def old():\n    pass\n", encoding="utf-8")
    
    # Мокаем SandboxManager, чтобы транзакция прошла в безопасной tmp_path
    with patch("services.sandbox_manager.SandboxManager.validate_read_file_path", return_value=test_file):
        res_sh = SelfHealingEngine.patch_file_transactional(
            filename="app.py",
            search_block="def old():\n    pass",
            replace_block="def new():\n    return 42"
        )
        assert res_sh.success
        assert "def new():" in test_file.read_text(encoding="utf-8")

def test_03_engineer_parser():
    from services.engineer_parser import EngineerResponseParser
    ws_mock = MagicMock()
    
    # Explicit TOOL tag
    resp_tool = '<<TOOL:write_file filename="app.py">>\nprint("hello")\n<</TOOL>>'
    parsed1 = EngineerResponseParser.parse_turn_actions(
        resp_tool, ws_mock, None, "", "", None, None, None, 1, False, None, None, False, [], {}
    )
    assert len(parsed1.write_blocks) == 1
    assert parsed1.write_blocks[0][0] == "app.py"
    
    # Markdown with explicit filename
    resp_md = '```python filename="core.py"\nimport sys\n```'
    parsed2 = EngineerResponseParser.parse_turn_actions(
        resp_md, ws_mock, None, "", "", None, None, None, 1, False, None, None, False, [], {}
    )
    assert len(parsed2.write_blocks) == 1
    assert parsed2.write_blocks[0][0] == "core.py"
    
    # Pure Python fallback (без тегов и filename)
    resp_fb = '```python\ndef run():\n    return 0\n```'
    parsed3 = EngineerResponseParser.parse_turn_actions(
        resp_fb, ws_mock, None, "", "", None, None, None, 1, False, None, None, False, [], {}
    )
    assert len(parsed3.write_blocks) == 1
    assert parsed3.write_blocks[0][0] == "script.py"  # Дефолтное безопасное имя
