"""
tests/test_execution_first_guard.py
Unit tests for the Execution-First paradigm and Anti-Truncation Guard cleanup:
1. Execution-First Code Integrity:
   - Syntax validation
   - Lazy Token detection (# rest of code, // ..., # TODO: implement)
   - Top-level stub '...' detection
   - Abnormal physical compression (> 75% on > 300 chars)
   - Freedom of refactoring (moving functions to classes, changing main, renaming)
2. Target artifact fixation in architect phase (is_maintenance + context_hint)
3. Guaranteed workspace rollback on run_res failure
"""

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.code_engine import CodeValidator
from services.subagent_engineer import SubagentEngineer
from services.sandbox_manager import SandboxManager


class TestExecutionFirstCodeIntegrity:
    """Проверка очищенного от эвристик CodeValidator.validate_code_integrity."""

    def test_ephemeral_or_non_maintenance_skips_check(self):
        orig = "def foo(): pass"
        new_code = "def bar(): pass"
        
        # is_maintenance = False
        valid, err = CodeValidator.validate_code_integrity(
            "test.py", orig, new_code, is_maintenance=False
        )
        assert valid is True
        assert err is None

        # is_ephemeral = True
        valid, err = CodeValidator.validate_code_integrity(
            "test.py", orig, new_code, is_maintenance=True, is_ephemeral=True
        )
        assert valid is True
        assert err is None

    def test_syntax_error_detected(self):
        orig = "def foo():\n    return 1\n"
        broken_new = "def foo():\n    return 1 +"  # Syntax error

        valid, err = CodeValidator.validate_code_integrity(
            "test.py", orig, broken_new, is_maintenance=True
        )
        assert valid is False
        assert "Синтаксическая ошибка" in err

    @pytest.mark.parametrize("lazy_marker", [
        "# ...",
        "# rest of code",
        "# rest of file",
        "# existing code",
        "# остальной код",
        "# остальной файл",
        "# код без изменений",
        "# TODO: implement",
        "# FIXME: дописать",
    ])
    def test_lazy_token_guard_blocks_truncation(self, lazy_marker):
        orig = "def calculate(a, b):\n    return a + b\n"
        new_code = f"def calculate(a, b):\n    return a + b\n{lazy_marker}\n"

        valid, err = CodeValidator.validate_code_integrity(
            "calc.py", orig, new_code, is_maintenance=True
        )
        assert valid is False
        assert "Обнаружен маркер сокращения кода" in err

    @pytest.mark.parametrize("c_lazy_marker", [
        "// ...",
        "// rest of code"
    ])
    def test_c_style_lazy_markers_rejected(self, c_lazy_marker):
        orig = "def calculate(a, b):\n    return a + b\n"
        new_code = f"def calculate(a, b):\n    return a + b\n{c_lazy_marker}\n"

        valid, err = CodeValidator.validate_code_integrity(
            "calc.py", orig, new_code, is_maintenance=True
        )
        assert valid is False
        # В зависимости от ast.parse или lazy guard, код обязательно отклоняется
        assert err is not None


    def test_top_level_ellipsis_stub_detected(self):
        orig = "def heavy_work():\n    print('working')\n"
        stub_new = "def heavy_work():\n    ...\n"

        valid, err = CodeValidator.validate_code_integrity(
            "worker.py", orig, stub_new, is_maintenance=True
        )
        assert valid is False
        assert "заменена на заглушку '...'" in err

    def test_abnormal_physical_shrink_without_deletion_keywords_blocked(self):
        orig = ("def long_module():\n    " + "print('data')\n    " * 40)
        assert len(orig.strip()) > 300
        new_code = "def foo(): pass"

        valid, err = CodeValidator.validate_code_integrity(
            "data.py", orig, new_code,
            task_prompt="добавь логов",
            is_maintenance=True
        )
        assert valid is False
        assert "Аномальное сокращение объёма" in err

    def test_abnormal_physical_shrink_with_deletion_keywords_allowed(self):
        orig = ("def long_module():\n    " + "print('data')\n    " * 40)
        assert len(orig.strip()) > 300
        new_code = "def foo(): pass"

        valid, err = CodeValidator.validate_code_integrity(
            "data.py", orig, new_code,
            task_prompt="удали весь лишний код и оставь только foo",
            is_maintenance=True
        )
        assert valid is True
        assert err is None

    def test_full_refactoring_freedom_no_false_positives(self):
        """
        Модель переносит функции в класс, меняет процедурный main на ООП,
        удаляет старые функции без ключевого слова 'delete'.
        """
        orig_code = (
            "def calculate_tax(amount):\n"
            "    return amount * 0.2\n\n"
            "def format_output(tax):\n"
            "    return f'Tax: {tax}'\n\n"
            "def main():\n"
            "    tax = calculate_tax(100)\n"
            "    print(format_output(tax))\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )
        # ООП рефакторинг: функции исчезли, появился класс TaxCalculator
        refactored_code = (
            "class TaxCalculator:\n"
            "    def __init__(self, rate=0.2):\n"
            "        self.rate = rate\n\n"
            "    def calculate(self, amount):\n"
            "        return amount * self.rate\n\n"
            "    def display(self, amount):\n"
            "        tax = self.calculate(amount)\n"
            "        print(f'Tax: {tax}')\n\n"
            "if __name__ == '__main__':\n"
            "    calc = TaxCalculator()\n"
            "    calc.display(100)\n"
        )

        valid, err = CodeValidator.validate_code_integrity(
            "tax.py", orig_code, refactored_code,
            task_prompt="переведи калькулятор налогов на ООП и настрой гибкую ставку",
            is_maintenance=True
        )
        # Больше нет ложных срабатываний "удалены функции: calculate_tax, format_output, main"
        assert valid is True
        assert err is None


class TestTargetArtifactFixation:
    """Проверка фиксации target_artifact в фазе Архитектора."""

    def test_target_artifact_fixed_from_context_hint_in_maintenance(self, tmp_path):
        ws_dir = tmp_path
        (ws_dir / "mini_game.py").write_text("# existing mini game", encoding="utf-8")

        mock_client = MagicMock()
        mock_client.chat_completion.return_value = (
            '```json\n{"format": "pygame", "intent": "maintenance", "target_artifact": "game.py", "plan": "update"}\n```'
        )

        with patch.object(SandboxManager, "get_workspace_dir", return_value=ws_dir):
            worker = SubagentEngineer()
            # Передаем context_hint="mini_game.py", где Архитектор вернул "game.py"
            # Мы ожидаем, что target_artifact будет переопределен на mini_game.py
            with patch("services.subagent_engineer.scan_installed_libraries", return_value=["pygame"]), \
                 patch("services.subagent_engineer.GitSandboxManager"):

                # Вызываем фазу или execute_worker с симуляцией быстрого выхода на 1-м ходе
                with patch.object(worker, "_inspect_workspace_file", return_value=""), \
                     patch("services.subagent_engineer.ToolEngine.run_script") as mock_run:
                    
                    mock_run.return_value = MagicMock(success=True, stdout="OK", error=None, output="OK")
                    mock_client.chat_completion.side_effect = [
                        # 1. Архитектор вернет target_artifact='game.py'
                        '```json\n{"format": "pygame", "intent": "maintenance", "target_artifact": "game.py", "plan": "update"}\n```',
                        # 2. Инженер вернет write_file mini_game.py и run_script
                        '<<TOOL:write_file filename="mini_game.py">>\nimport pygame\n<</TOOL:write_file>>\n<<TOOL:run_script filename="mini_game.py">>'
                    ]

                    res = worker.execute_worker(
                        task_prompt="добавь прыжок персонажа",
                        context_hint="mini_game.py",
                        lm_client=mock_client,
                        model_id="test-model"
                    )

                    assert worker.target_artifact == "mini_game.py"
                    assert res["target_artifact"] == "mini_game.py"


class TestGuaranteedRollbackOnFailure:
    """Проверка гарантированного отката при сбое выполнения скрипта."""

    def test_rollback_workspace_called_on_run_failure(self, tmp_path):
        ws_dir = tmp_path
        target_file = ws_dir / "app.py"
        target_file.write_text("print('Initial')", encoding="utf-8")

        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # Архитектор
            '```json\n{"format": "cli", "intent": "maintenance", "target_artifact": "app.py", "plan": "fix"}\n```',
            # Ход 1: Инженер пишет падающий код
            '<<TOOL:write_file filename="app.py">>\nraise RuntimeError("Crash")\n<</TOOL:write_file>>\n<<TOOL:run_script filename="app.py">>',
            # Архитектор RCA
            'RCA: syntax error',
            # Ход 2: Инженер исправляет
            '<<TOOL:write_file filename="app.py">>\nprint("Fixed")\n<</TOOL:write_file>>\n<<TOOL:run_script filename="app.py">>'
        ]

        with patch.object(SandboxManager, "get_workspace_dir", return_value=ws_dir), \
             patch("services.subagent_engineer.scan_installed_libraries", return_value=[]), \
             patch("services.subagent_engineer.GitSandboxManager") as mock_git:
            
            mock_git.is_git_available.return_value = False

            with patch("services.subagent_engineer.ToolEngine.run_script") as mock_run:
                # Первый запуск - сбой, второй запуск - успех
                fail_res = MagicMock(success=False, stdout="", error="RuntimeError: Crash", output="RuntimeError: Crash")
                success_res = MagicMock(success=True, stdout="Fixed", error=None, output="Fixed")
                mock_run.side_effect = [fail_res, success_res]

                with patch.object(SubagentEngineer, "_rollback_workspace", wraps=SubagentEngineer._rollback_workspace) as mock_rollback:
                    worker = SubagentEngineer()
                    res = worker.execute_worker(
                        task_prompt="исправь app.py",
                        context_hint="app.py",
                        lm_client=mock_client,
                        model_id="test-model"
                    )

                    # Убеждаемся, что _rollback_workspace вызывался после первого сбоя
                    assert mock_rollback.called
                    assert res["success"] is True
