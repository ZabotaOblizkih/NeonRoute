import ast
import json
import pytest
from pathlib import Path

from services.tool_engine import ToolEngine
from services.tool_parser import ToolParser
from services.engineer_planner import EngineerPlanner
from services.sandbox_manager import SandboxManager


def test_replace_symbol_function_success(tmp_path, monkeypatch):
    monkeypatch.setattr(SandboxManager, "validate_read_file_path", lambda fn: tmp_path / Path(fn).name)
    test_file = tmp_path / "calc.py"
    initial_code = (
        "import math\n\n"
        "def compute(x):\n"
        "    return x * 2\n\n"
        "def helper():\n"
        "    return 'ok'\n"
    )
    test_file.write_text(initial_code, encoding="utf-8")

    new_code = (
        "def compute(x):\n"
        "    return x ** 2\n"
    )
    res = ToolEngine.replace_symbol("calc.py", "compute", new_code)
    assert res.success is True
    assert "заменен на новый код" in res.output

    updated = test_file.read_text(encoding="utf-8")
    assert "return x ** 2" in updated
    assert "return 'ok'" in updated
    # syntax valid
    ast.parse(updated)


def test_replace_symbol_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(SandboxManager, "validate_read_file_path", lambda fn: tmp_path / Path(fn).name)
    test_file = tmp_path / "module.py"
    initial_code = (
        "class OldClass:\n"
        "    def run(self):\n"
        "        pass\n\n"
        "class KeepClass:\n"
        "    pass\n"
    )
    test_file.write_text(initial_code, encoding="utf-8")

    res = ToolEngine.replace_symbol("module.py", "OldClass", "DELETED")
    assert res.success is True
    assert "удален из строк" in res.output

    updated = test_file.read_text(encoding="utf-8")
    assert "OldClass" not in updated
    assert "class KeepClass" in updated
    ast.parse(updated)


def test_replace_symbol_syntax_error_rollback(tmp_path, monkeypatch):
    monkeypatch.setattr(SandboxManager, "validate_read_file_path", lambda fn: tmp_path / Path(fn).name)
    test_file = tmp_path / "target.py"
    initial_code = (
        "def calculate():\n"
        "    return 100\n"
    )
    test_file.write_text(initial_code, encoding="utf-8")

    broken_code = "def calculate(:\n    return 100\n"
    res = ToolEngine.replace_symbol("target.py", "calculate", broken_code)
    assert res.success is False
    assert "SyntaxError" in res.output

    # File must remain untouched
    assert test_file.read_text(encoding="utf-8") == initial_code


def test_replace_symbol_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(SandboxManager, "validate_read_file_path", lambda fn: tmp_path / Path(fn).name)
    test_file = tmp_path / "foo.py"
    test_file.write_text("def existing(): pass\n", encoding="utf-8")

    res = ToolEngine.replace_symbol("foo.py", "non_existing", "def non_existing(): pass\n")
    assert res.success is False
    assert "не найден в AST" in res.output


def test_tool_parser_replace_symbol_parsing():
    text = (
        "Диагноз готов.\n"
        "<<TOOL:replace_symbol filename=\"main.py\" name=\"Worker\">>\n"
        "class Worker:\n"
        "    def work(self):\n"
        "        return 1\n"
        "<</TOOL:replace_symbol>>\n"
        "<<TOOL:run_script filename=\"main.py\">>\n"
    )
    calls = ToolParser.parse_tool_calls(text)
    assert len(calls) == 2
    assert calls[0].name == "replace_symbol"
    assert calls[0].params["filename"] == "main.py"
    assert calls[0].params["symbol_name"] == "Worker"
    assert "class Worker:" in calls[0].body
    assert calls[1].name == "run_script"

    stripped = ToolParser.strip_tool_tags(text)
    assert "Диагноз готов." in stripped
    assert "replace_symbol" not in stripped
    assert "class Worker" not in stripped


def test_engineer_planner_ast_slice_for_large_file(tmp_path):
    # Create file with > 250 lines
    file_lines = ["# line\n"] * 240
    file_lines.append("def target_worker(data):\n    # docstring\n    return data * 10\n")
    file_lines.extend(["# trailing\n"] * 30)

    large_file = tmp_path / "big_service.py"
    large_file.write_text("".join(file_lines), encoding="utf-8")

    plan = EngineerPlanner.plan_architecture(
        task_prompt="Исправь ошибку в target_worker",
        ws_dir=tmp_path,
        client=None
    )

    # Since client is None, it defaults or if intent is maintenance
    # Let's verify with mock client returning maintenance intent
    class MockClient:
        def chat_completion(self, *args, **kwargs):
            return json.dumps({
                "artifact_type": "cli_tool",
                "intent": "maintenance",
                "target_artifact": "big_service.py",
                "verification_script": None,
                "plan": "Оптимизация worker"
            })

    plan_res = EngineerPlanner.plan_architecture(
        task_prompt="Исправь target_worker",
        ws_dir=tmp_path,
        client=MockClient()
    )

    assert "Крупный файл big_service.py" in plan_res.plan_content
    assert "РЕГЛАМЕНТ ТОЧЕЧНОЙ МОДИФИКАЦИИ УЗЛА 'target_worker'" in plan_res.plan_content
    assert "[ЦЕЛЕВОЙ УЗЕЛ НА ДИСКЕ" in plan_res.plan_content
    assert "replace_symbol" in plan_res.plan_content
    assert plan_res.is_ast_modification is True
    assert plan_res.target_symbol == "target_worker"
    assert "--- УРОВЕНЬ 1 (Импорты модуля) ---" in plan_res.plan_content
    assert "--- УРОВЕНЬ 2 (Каркас класса) ---" in plan_res.plan_content
    assert "--- УРОВЕНЬ 3 (Срез узла с окружением ±10 строк) ---" in plan_res.plan_content


def test_tool_parser_single_bracket_and_indentation_preservation():
    # Tag closed with single >
    text = (
        '<<TOOL:replace_symbol filename="service.py" name="process">\n'
        '    def process(self):\n'
        '        return True\n'
        '<</TOOL:replace_symbol>>'
    )
    calls = ToolParser.parse_tool_calls(text)
    assert len(calls) == 1
    call = calls[0]
    assert call.name == "replace_symbol"
    assert call.params["filename"] == "service.py"
    assert call.params["name"] == "process"
    assert call.params["symbol_name"] == "process"
    # First line indentation must be preserved
    assert call.body.startswith("    def process(self):")


def test_tool_engine_replace_symbol_class_method_indentation_normalization(tmp_path, monkeypatch):
    monkeypatch.setattr(SandboxManager, "validate_read_file_path", lambda fn: tmp_path / Path(fn).name)
    test_file = tmp_path / "handler.py"
    initial_code = (
        "class Handler:\n"
        "    def execute(self):\n"
        "        # old\n"
        "        return 1\n\n"
        "    def other(self):\n"
        "        return 2\n"
    )
    test_file.write_text(initial_code, encoding="utf-8")

    # LLM returned code dedented to 0 spaces
    dedented_replacement = (
        "def execute(self):\n"
        "    # new\n"
        "    return 42\n"
    )
    res = ToolEngine.replace_symbol("handler.py", "execute", dedented_replacement)
    assert res.success is True

    updated = test_file.read_text(encoding="utf-8")
    assert "    def execute(self):\n        # new\n        return 42\n" in updated
    assert "    def other(self):" in updated
    # AST must parse with no SyntaxError (unindent / indentation error)
    tree = ast.parse(updated)
    cls_node = [n for n in tree.body if isinstance(n, ast.ClassDef)][0]
    assert len(cls_node.body) == 2


def test_tool_engine_replace_symbol_large_724_lines_module_integrity(tmp_path, monkeypatch):
    monkeypatch.setattr(SandboxManager, "validate_read_file_path", lambda fn: tmp_path / Path(fn).name)
    test_file = tmp_path / "big_module.py"

    # Generate 724 lines file
    lines = ["import os\n", "import sys\n\n", "class Engine:\n"]
    for i in range(1, 100):
        lines.append(f"    def method_{i}(self):\n        return {i}\n\n")
    lines.append("    def target_method(self):\n        return 'old_value'\n\n")
    for i in range(101, 180):
        lines.append(f"    def method_{i}(self):\n        return {i}\n\n")
    # Pad to exactly 724 lines
    while len(lines) < 724:
        lines.append(f"# padding comment line {len(lines) + 1}\n")

    initial_content = "".join(lines)
    test_file.write_text(initial_content, encoding="utf-8")
    assert len(test_file.read_text(encoding="utf-8").splitlines()) >= 700

    new_code = (
        "def target_method(self):\n"
        "    return 'refactored_value'\n"
    )
    res = ToolEngine.replace_symbol("big_module.py", "target_method", new_code)
    assert res.success is True

    updated_content = test_file.read_text(encoding="utf-8")
    assert "return 'refactored_value'" in updated_content
    assert "return 'old_value'" not in updated_content
    assert "def method_1(self):" in updated_content
    assert "def method_99(self):" in updated_content
    assert "def method_179(self):" in updated_content
    ast.parse(updated_content)


def test_subagent_engineer_fallback_interceptor_markdown(tmp_path, monkeypatch):
    from services.subagent_engineer import SubagentEngineer
    from services.engineer_planner import ArchitectPlan

    ws_dir = tmp_path
    monkeypatch.setattr(SandboxManager, "get_workspace_dir", lambda: ws_dir)
    monkeypatch.setattr(SandboxManager, "validate_read_file_path", lambda fn, **kwargs: ws_dir / Path(fn).name)

    mock_plan = ArchitectPlan(
        artifact_format="module",
        artifact_type="module",
        target_artifact="service_core.py",
        target_file="service_core.py",
        verification_script=None,
        target_entry_file=None,
        is_maintenance=True,
        is_ast_modification=True,
        target_symbol="buggy_node",
        plan_content="Исправь buggy_node в service_core.py",
        skill_id="pure_logic_tdd",
        task_type="INCREMENTAL_FEATURE",
    )
    monkeypatch.setattr(EngineerPlanner, "plan_architecture", lambda *args, **kwargs: mock_plan)

    large_target = ws_dir / "service_core.py"
    lines = ["import math\n", "\n", "class Core:\n"]
    for i in range(1, 70):
        lines.append(f"    def func_{i}(self):\n        return {i}\n\n")
    lines.append("    def buggy_node(self, a, b):\n        return a - b\n\n")
    while len(lines) < 300:
        lines.append(f"# line {len(lines)}\n")

    large_target.write_text("".join(lines), encoding="utf-8")

    class MockLM:
        def __init__(self):
            self.turn = 0

        def chat_completion(self, *args, **kwargs):
            self.turn += 1
            # Model returns Markdown block without <<TOOL:replace_symbol>>
            return (
                "Фаза 1: Диагноз. Ошибка найдена в вычитании.\n"
                "Фаза 2: Код:\n"
                "```python\n"
                "def buggy_node(self, a, b):\n"
                "    return a + b\n"
                "```\n"
            )

    worker = SubagentEngineer()
    res = worker.execute_worker(
        task_prompt="Исправь buggy_node в service_core.py",
        context_hint="service_core.py",
        lm_client=MockLM(),
        max_turns=1
    )

    updated = large_target.read_text(encoding="utf-8")
    assert "return a + b" in updated
    assert "return a - b" not in updated
    # Full file was NOT overwritten with 3 lines
    assert len(updated.splitlines()) > 250
    ast.parse(updated)


def test_subagent_engineer_fallback_interceptor_deleted(tmp_path, monkeypatch):
    from services.subagent_engineer import SubagentEngineer
    from services.engineer_planner import ArchitectPlan

    ws_dir = tmp_path
    monkeypatch.setattr(SandboxManager, "get_workspace_dir", lambda: ws_dir)
    monkeypatch.setattr(SandboxManager, "validate_read_file_path", lambda fn, **kwargs: ws_dir / Path(fn).name)

    mock_plan = ArchitectPlan(
        artifact_format="module",
        artifact_type="module",
        target_artifact="service_core.py",
        target_file="service_core.py",
        verification_script=None,
        target_entry_file=None,
        is_maintenance=True,
        is_ast_modification=True,
        target_symbol="obsolete_node",
        plan_content="Удали obsolete_node в service_core.py",
        skill_id="pure_logic_tdd",
        task_type="INCREMENTAL_FEATURE",
    )
    monkeypatch.setattr(EngineerPlanner, "plan_architecture", lambda *args, **kwargs: mock_plan)

    large_target = ws_dir / "service_core.py"
    lines = ["import math\n", "\n", "class Core:\n"]
    for i in range(1, 70):
        lines.append(f"    def func_{i}(self):\n        return {i}\n\n")
    lines.append("    def obsolete_node(self):\n        return 'dead'\n\n")
    while len(lines) < 300:
        lines.append(f"# line {len(lines)}\n")

    large_target.write_text("".join(lines), encoding="utf-8")

    class MockLM:
        def chat_completion(self, *args, **kwargs):
            return "Фаза 1: Узел устарел.\nФаза 2:\nDELETED"

    worker = SubagentEngineer()
    res = worker.execute_worker(
        task_prompt="Удали obsolete_node в service_core.py",
        context_hint="service_core.py",
        lm_client=MockLM(),
        max_turns=1
    )

    updated = large_target.read_text(encoding="utf-8")
    assert "obsolete_node" not in updated
    assert len(updated.splitlines()) > 250
    ast.parse(updated)

