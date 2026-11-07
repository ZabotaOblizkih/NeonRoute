"""
Unit tests for core services (parsers, sandbox guards, diagnostics, code validation,
patch engine, task queue, and engineer context).
Consolidated from legacy batch stands into a unified, clean unit test suite.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest


# =========================================================================
# 1. Direct Command Parser
# =========================================================================

def test_direct_command_parser():
    from services.direct_command_parser import parse_direct_command_params, parse_user_negative_trigger
    cmd = (
        "cyberpunk city, neon rain --steps 35 --cfg 6.5 --denoise 0.45 --hires on "
        "--ad on --seed 4242 --model realistic --ar wide --negative bad quality, blurry"
    )
    pos, neg, params = parse_direct_command_params(cmd)
    assert pos == "cyberpunk city, neon rain"
    assert neg == "bad quality, blurry"
    assert params["steps"] == 35
    assert params["cfg_scale"] == 6.5
    assert params["enable_hr"] is True
    assert params["enable_adetailer"] is True
    assert params["seed"] == 4242
    assert params["model_override"] == "realistic"
    assert params["width"] == 768

    has_neg, p_text, n_text = parse_user_negative_trigger("portrait of girl negative: ugly")
    assert has_neg is True
    assert p_text == "portrait of girl"
    assert n_text == "ugly"


# =========================================================================
# 2. Sandbox Guard & Module Syntax Validation
# =========================================================================

def test_sandbox_guard_is_safe_path():
    from services.sandbox_guard import _is_safe_path
    ws_mock = Path("C:/NeonRoute/workspace").resolve()
    assert _is_safe_path(ws_mock / "script.py", ws_mock, allow_ws_root=True) is True
    assert _is_safe_path(ws_mock, ws_mock, allow_ws_root=False) is False
    assert _is_safe_path(Path("C:/Windows/System32/cmd.exe"), ws_mock) is False


def test_sandbox_runner_validate():
    from services.sandbox_runner import validate_module_syntax
    assert validate_module_syntax("def hello():\n    return 42\n") is True
    assert validate_module_syntax("def broken(:\n    return") is False


# =========================================================================
# 3. Asset Validator
# =========================================================================

def test_asset_validator(tmp_path):
    fake_forge = tmp_path / "webui_forge_cu121_torch231" / "webui"
    sd_dir = fake_forge / "models" / "Stable-diffusion"
    ad_dir = fake_forge / "models" / "adetailer"
    sd_dir.mkdir(parents=True)
    ad_dir.mkdir(parents=True)

    (sd_dir / "realisticVisionV60_v60B1_vae.safetensors").write_bytes(b"dummy")
    (ad_dir / "face_yolov8n.pt").write_bytes(b"dummy")

    with patch("services.asset_validator.FORGE_ROOT_DIR", tmp_path), \
         patch("services.asset_validator.FORGE_BASE_DIR", fake_forge), \
         patch("services.asset_validator.MODELS_SD_DIR", sd_dir), \
         patch("services.asset_validator.MODELS_ADETAILER_DIR", ad_dir):

        from services.asset_validator import AssetValidator
        report = AssetValidator.validate_all_assets()
        assert report["forge_installed"] is True
        assert report["checkpoints_found"]["Realistic Vision v6.0"] is True
        assert report["adetailer_found"]["face_yolov8n.pt"] is True
        assert report["adetailer_found"]["hand_yolov8n.pt"] is False
        assert report["all_critical_present"] is False
        assert AssetValidator.is_adetailer_available("face_yolov8n.pt") is not None
        AssetValidator.log_validation_summary()


# =========================================================================
# 4. Memory Extractor
# =========================================================================

def test_memory_extractor():
    from services.memory_extractor import MemoryExtractor
    assert MemoryExtractor._has_user_fact_potential("что ты умеешь делать?") is False
    assert MemoryExtractor._has_user_fact_potential("я работаю инженером") is True

    mock_lm = MagicMock()
    mock_lm.chat_completion.return_value = '{"facts": ["Пользователь работает инженером"]}'
    with patch("services.memory_extractor.memory_db") as mock_db:
        mock_db.add_fact.return_value = 1
        history = [{"role": "user", "text": "Я инженер"}]
        MemoryExtractor._run_extraction(history, lm_client=mock_lm)
        assert mock_db.add_fact.called


# =========================================================================
# 5. Engineer Diagnostics & Code Review
# =========================================================================

def test_engineer_diagnostics():
    from services.engineer_diagnostics import (
        clean_traceback,
        diagnose_runtime_error,
        extract_crash_slice,
    )
    fake_tb = (
        'Traceback (most recent call last):\n'
        '  File "sandbox_runner.py", line 1\n'
        '  File "task.py", line 12\n'
        'ZeroDivisionError'
    )
    cleaned = clean_traceback(fake_tb)
    assert "sandbox_runner.py" not in cleaned
    assert "task.py" in cleaned
    assert "ZeroDivisionError" in cleaned

    diag = diagnose_runtime_error("KeyError: 'no style with name FancyTitle'")
    assert "В python-docx нельзя указывать вымышленные стили" in diag

    diag_arg = diagnose_runtime_error("the following arguments are required: --input-file")
    assert "[СБОЙ АВТОНОМНОГО ЗАПУСКА CLI]" in diag_arg

    code = "x = 1\nres = x / 0\n"
    crash_slice = extract_crash_slice(code, "File 'test.py', line 2, in <module>", filename="test.py", window=1)
    assert "> " in crash_slice


def test_engineer_review(tmp_path):
    from services.engineer_review import ReviewPipeline
    ws_path = tmp_path / "workspace"
    ws_path.mkdir()
    (ws_path / "main.py").write_text("print('hello')", encoding="utf-8")

    mock_send = MagicMock()
    mock_lm = MagicMock()
    mock_lm.chat_completion.return_value = "Код выглядит чисто."
    plan_mock = MagicMock()
    plan_mock.target_artifact = "main.py"

    git_mock_module = MagicMock()
    git_mock_module.GitSandbox.checkpoint.return_value = True

    with patch.dict("sys.modules", {"services.git_sandbox": git_mock_module}):
        res = ReviewPipeline.run(
            plan=plan_mock,
            task_prompt="Проверь код",
            ws_dir=ws_path,
            client=mock_lm,
            target_model="local-model",
            safe_send_message=mock_send
        )
        assert res["success"] is True
        assert mock_send.called

        res_commit = ReviewPipeline.run(
            plan=plan_mock,
            task_prompt="Сделай коммит 'Init'",
            ws_dir=ws_path,
            client=mock_lm,
            target_model="local-model",
            safe_send_message=mock_send
        )
        assert res_commit["success"] is True
        assert "Git-чекпоинт" in res_commit["message"]


# =========================================================================
# 6. Code Validator & AST Linter
# =========================================================================

def test_code_validator():
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


# =========================================================================
# 7. Patch Engine & Transactional Self-Healing
# =========================================================================

def test_patch_engine(tmp_path):
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

    with patch("services.sandbox_manager.SandboxManager.validate_read_file_path", return_value=test_file):
        res_sh = SelfHealingEngine.patch_file_transactional(
            filename="app.py",
            search_block="def old():\n    pass",
            replace_block="def new():\n    return 42"
        )
        assert res_sh.success
        assert "def new():" in test_file.read_text(encoding="utf-8")


# =========================================================================
# 8. Engineer Parser & Actions
# =========================================================================

def test_engineer_parser():
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
    assert parsed3.write_blocks[0][0] == "script.py"


# =========================================================================
# 9. Sandbox Manager Paths & Traversal Defense
# =========================================================================

def test_sandbox_manager_paths():
    from services.sandbox_manager import SandboxManager

    # 1. Защита от Path Traversal (усечение выходов на уровень вверх)
    assert SandboxManager.clean_workspace_filename("../../../../windows/system32/cmd.exe") == "windows/system32/cmd.exe"

    # 2. Изоляция системных папок на чтение
    try:
        SandboxManager.validate_read_path("C:/Windows/System32")
        assert False, "Должен был заблокировать системную папку"
    except PermissionError:
        pass

    # 3. In-Memory валидация без обращения к диску
    fake_base = Path("C:/FakeNeonRoute").resolve()
    fake_file = fake_base / "test.txt"

    with patch("services.sandbox_manager.BASE_DIR", fake_base), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_file", return_value=True), \
         patch("pathlib.Path.stat") as mock_stat:

        mock_stat.return_value.st_size = 1024
        res = SandboxManager.validate_read_file_path(str(fake_file))
        assert res.name == "test.txt"


# =========================================================================
# 10. Tool Parser
# =========================================================================

def test_tool_parser():
    from services.tool_parser import ToolParser
    raw_text = '<<TOOL:write_file filename="test.py" extra="123">>print("OK")<</TOOL>>'
    calls = ToolParser.parse_tool_calls(raw_text)
    assert calls[0].name == "write_file"
    assert calls[0].params["filename"] == "test.py"

    clean_text = ToolParser.strip_tool_tags('Привет! <<TOOL:write_file filename="test.py">>\nprint("OK")\n<</TOOL>> Конец.')
    assert "Привет!" in clean_text and "Конец." in clean_text
    assert "TOOL" not in clean_text


# =========================================================================
# 11. Task Queue Worker
# =========================================================================

def test_task_queue():
    from services.task_queue import TaskQueueWorker, InferenceTask, PRIORITY_HIGH, PRIORITY_LOW
    queue = TaskQueueWorker()

    t_low = InferenceTask("low", "memory", lambda: None, priority=PRIORITY_LOW)
    t_high = InferenceTask("high", "llm", lambda: None, priority=PRIORITY_HIGH)

    queue.enqueue(t_low)
    queue.enqueue(t_high)

    item1 = queue._queue.get()
    item2 = queue._queue.get()
    assert item1[2].name == "high"
    assert item2[2].name == "low"


# =========================================================================
# 12. Engineer Context Pure Python Extractor
# =========================================================================

def test_engineer_context_pure_py():
    from services.engineer_context import EngineerContext

    md_text = "Here is the code:\n```python\ndef test():\n    pass\n```\nDone."
    code = EngineerContext.extract_pure_py(md_text)
    assert "def test():" in code

    raw_py = "import os\n\ndef main():\n    print('hello')"
    assert "import os" in EngineerContext.extract_pure_py(raw_py)
