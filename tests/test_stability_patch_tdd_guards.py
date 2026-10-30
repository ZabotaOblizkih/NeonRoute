"""
tests/test_stability_patch_tdd_guards.py
Unit tests for the Consolidated Stability Patch:
1. Bootstrap Baseline in services/engineer_tdd.py
2. SubstanceGuard check_artifact_substance in services/tool_engine.py
3. IntentOverrideGuard in services/engineer_planner.py
4. Decomposed game matrix and intent rules in services/engineer_prompts.py
5. SubstanceGuard integration in services/subagent_engineer.py
"""

import ast
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.tool_engine import ToolEngine
from services.engineer_planner import EngineerPlanner, ArchitectPlan
from services.engineer_prompts import get_architect_prompt
from services.engineer_tdd import CodeFirstTDDPipeline
from services.subagent_engineer import SubagentEngineer


class TestToolEngineSubstanceGuard:
    """Проверка метода ToolEngine.check_artifact_substance."""

    def test_missing_file_rejected(self, tmp_path):
        missing = tmp_path / "non_existent.py"
        ok, msg = ToolEngine.check_artifact_substance(missing, min_bytes=300)
        assert not ok
        assert "не найден на диске" in msg

    def test_file_too_small_rejected(self, tmp_path):
        small = tmp_path / "stub.py"
        small.write_text("def run(): pass\n", encoding="utf-8")
        ok, msg = ToolEngine.check_artifact_substance(small, min_bytes=300)
        assert not ok
        assert "подозрительно мал" in msg

    def test_syntax_error_rejected(self, tmp_path):
        bad = tmp_path / "bad.py"
        bad_code = "def foo(\n" + "# padding line\n" * 25
        bad.write_text(bad_code, encoding="utf-8")
        assert len(bad_code.encode("utf-8")) > 300
        ok, msg = ToolEngine.check_artifact_substance(bad, min_bytes=300)
        assert not ok
        assert "Синтаксическая ошибка" in msg

    def test_no_functions_or_classes_rejected(self, tmp_path):
        empty_body = tmp_path / "empty_body.py"
        code = "import os\nimport sys\n" + "# comment\n" * 35 + "pass\n"
        empty_body.write_text(code, encoding="utf-8")
        assert len(code.encode("utf-8")) > 300
        ok, msg = ToolEngine.check_artifact_substance(empty_body, min_bytes=300)
        assert not ok
        assert "не содержит ни классов, ни функций" in msg

    def test_solid_file_passes(self, tmp_path):
        solid = tmp_path / "solid.py"
        code = (
            "import os\n"
            "class Calculator:\n"
            "    def __init__(self):\n"
            "        self.history = []\n"
            "    def add(self, a: int, b: int) -> int:\n"
            "        res = a + b\n"
            "        self.history.append(('add', a, b, res))\n"
            "        return res\n\n"
            "def run_calculator():\n"
            "    calc = Calculator()\n"
            "    print(calc.add(10, 20))\n"
            "    return True\n\n"
            "if __name__ == '__main__':\n"
            "    run_calculator()\n"
        )
        solid.write_text(code, encoding="utf-8")
        assert len(code.encode("utf-8")) >= 300
        ok, msg = ToolEngine.check_artifact_substance(solid, min_bytes=300)
        assert ok
        assert msg == "OK"


class TestIntentOverrideGuard:
    """Проверка работы IntentOverrideGuard при отсутствии файла на диске."""

    def test_clean_disk_overrides_maintenance_to_new(self, tmp_path):
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = json.dumps({
            "task_type": "INCREMENTAL_FEATURE",
            "target_file": "missing_module.py",
            "intent": "maintenance",
            "plan": "Доработка"
        })

        plan = EngineerPlanner.plan_architecture(
            task_prompt="доработай missing_module.py",
            client=mock_client,
            ws_dir=tmp_path
        )

        assert plan.intent == "new"
        assert plan.is_maintenance is False
        assert plan.task_type == "NEW_MODULE"

    def test_existing_file_preserves_maintenance(self, tmp_path):
        existing = tmp_path / "existing_module.py"
        existing.write_text("class Existing: pass\n", encoding="utf-8")

        mock_client = MagicMock()
        mock_client.chat_completion.return_value = json.dumps({
            "task_type": "INCREMENTAL_FEATURE",
            "target_file": "existing_module.py",
            "intent": "maintenance",
            "plan": "Доработка существующего модуля"
        })

        plan = EngineerPlanner.plan_architecture(
            task_prompt="доработай existing_module.py",
            client=mock_client,
            ws_dir=tmp_path
        )

        assert plan.intent == "maintenance"
        assert plan.is_maintenance is True
        assert plan.task_type == "INCREMENTAL_FEATURE"


class TestArchitectPromptsGameDecomposition:
    """Проверка обновления мета-промптов Архитектора с динамической инъекцией правил."""

    def test_pygame_core_and_game_separation(self):
        from services.engineer_skill_router import EngineerSkillRouter
        meta = EngineerSkillRouter.skills["interactive_loop"]
        prompt = get_architect_prompt(
            injected_architect_rules=meta["architect_rules"],
            default_artifact=meta["default_artifact"]
        )
        assert "<name>_core.py" in prompt
        assert "game.py" in prompt
        assert meta["architect_rules"] in prompt
        assert '"interactive_loop"' in prompt

    def test_new_intent_guidance(self):
        prompt = get_architect_prompt()
        assert '"new"' in prompt
        assert "создание новой программы, модуля или игры с нуля" in prompt


class TestBootstrapBaselineZeroByteTest:
    """Проверка запуска Bootstrap Baseline, когда test_file отсутствует или имеет 0 байт."""

    def test_bootstrap_baseline_triggered_for_zero_byte_test(self, tmp_path):
        mod_file = tmp_path / "calculator.py"
        mod_file.write_text(
            "class Calculator:\n"
            "    def add(self, a, b):\n"
            "        return a + b\n",
            encoding="utf-8"
        )
        zero_test = tmp_path / "test_calculator.py"
        zero_test.write_text("", encoding="utf-8")  # 0 bytes!

        plan = ArchitectPlan(
            task_type="INCREMENTAL_FEATURE",
            target_file="calculator.py",
            target_artifact="calculator.py",
            intent="maintenance",
            is_maintenance=True
        )

        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # 1. Bootstrap test generation
            "def test_calc_happy():\n    from calculator import Calculator\n    c = Calculator()\n    assert c.add(1, 2) == 3\n",
            # 2. Step 2 (feature modification)
            "class Calculator:\n    def add(self, a, b):\n        return a + b\n    def sub(self, a, b):\n        return a - b\n",
            # 3. Step 4 (test append)
            "def test_calc_sub():\n    from calculator import Calculator\n    c = Calculator()\n    assert c.sub(5, 2) == 3\n",
        ]

        messages_sent = []
        def on_msg(role, text):
            messages_sent.append(text)

        with patch("services.tool_engine.ToolEngine.run_pytest", return_value=MagicMock(success=True, output="1 passed")):
            res = CodeFirstTDDPipeline.run(
                plan=plan,
                client=mock_client,
                target_model="model",
                ws_dir=tmp_path,
                task_prompt="Добавь sub в calculator.py",
                on_message_callback=on_msg
            )

        assert any("Bootstrap Baseline" in m for m in messages_sent)
        assert res["success"] is True
        assert (tmp_path / "test_calculator.py").stat().st_size > 0
