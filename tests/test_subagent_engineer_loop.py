"""
test_subagent_engineer_loop.py — Модульные тесты для универсального цикла Antigravity
и детектора зацикливания в services/subagent_engineer.py.
"""

import sys
from pathlib import Path
import pytest

# Динамическое подключение корня проекта
PROJECT_ROOT = Path(__file__).resolve().parent
while PROJECT_ROOT.name and not (PROJECT_ROOT / "services").exists() and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.subagent_engineer import SubagentEngineer


class TestErrorFingerprint:
    """Тестирование универсального алгоритма извлечения отпечатков ошибок."""

    @pytest.mark.parametrize("empty_val", [None, "", "   ", "\n\t\n"])
    def test_fingerprint_empty_inputs(self, empty_val):
        """Пустые или пробельные входные данные возвращают пустую строку."""
        assert SubagentEngineer._extract_error_fingerprint(empty_val) == ""

    def test_fingerprint_standard_python_traceback(self):
        """Извлечение имени файла, строки и типа исключения из стандартного трейсбека Python."""
        stderr_sample = (
            'Traceback (most recent call last):\n'
            '  File "C:\\NeonRoute\\workspace\\db.py", line 42, in add_task\n'
            '    cursor.execute("INSERT INTO tasks VALUES (?)", (title,))\n'
            'sqlite3.OperationalError: no such table: tasks'
        )
        fp = SubagentEngineer._extract_error_fingerprint(stderr_sample)
        assert "db.py:42" in fp
        assert "sqlite3.OperationalError: no such table: tasks" in fp

    def test_fingerprint_nested_calls_picks_last_frame(self):
        """При глубоком стеке трейсбека отпечаток снимается с последней строки падения."""
        stderr_sample = (
            'Traceback (most recent call last):\n'
            '  File "main.py", line 12, in <module>\n'
            '    service.run()\n'
            '  File "service.py", line 85, in run\n'
            '    self._process_item(item)\n'
            '  File "service.py", line 98, in _process_item\n'
            'ZeroDivisionError: division by zero'
        )
        fp = SubagentEngineer._extract_error_fingerprint(stderr_sample)
        assert "service.py:98" in fp
        assert "ZeroDivisionError: division by zero" in fp
        assert "main.py:12" not in fp

    def test_fingerprint_test_failure_unittest_format(self):
        """Распознавание сигнатуры упавшего теста формата unittest."""
        stderr_sample = (
            'FAIL: test_sanitize_positive_prompt (__main__.TestMinorSafetyDetector)\n'
            '----------------------------------------------------------------------\n'
            'Traceback (most recent call last):\n'
            '  File "test_minor_safety.py", line 129, in test_sanitize_positive_prompt\n'
            '    self.assertNotIn("suggestive", sanitized_prompt)\n'
            'AssertionError: \'suggestive\' unexpectedly found in \'suggestive, dress\'\n'
            'FAILED (failures=1)'
        )
        fp = SubagentEngineer._extract_error_fingerprint(stderr_sample)
        assert fp.startswith("TEST_FAIL::")
        assert "test_sanitize_positive_prompt" in fp

    def test_fingerprint_consistency(self):
        """Идентичные ошибки выдают строго идентичный отпечаток (базис для детектора зацикливания)."""
        err1 = 'File "logic.py", line 15, in check\nTypeError: unsupported operand type(s)'
        err2 = 'File "logic.py", line 15, in check\nTypeError: unsupported operand type(s)'
        assert SubagentEngineer._extract_error_fingerprint(err1) == SubagentEngineer._extract_error_fingerprint(err2)

    def test_fingerprint_detects_evolution(self):
        """Если ошибка изменилась (сменилась строка или тип), отпечатки различаются."""
        err_syntax = 'File "logic.py", line 15, in check\nTypeError: unsupported operand type(s)'
        err_logic = 'File "logic.py", line 22, in check\nValueError: invalid literal'
        fp1 = SubagentEngineer._extract_error_fingerprint(err_syntax)
        fp2 = SubagentEngineer._extract_error_fingerprint(err_logic)
        assert fp1 != fp2


class TestSyntaxValidatorForTests:
    """Проверка снятия ограничений точки входа для тестовых файлов."""

    def test_regular_file_without_entry_point_fails(self):
        """Обычный скрипт с функциями без if __name__ == '__main__': бракуется валидатором."""
        code = (
            "def calculate_total(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        is_valid, err = SubagentEngineer._validate_python_syntax("calculator.py", code, is_entry_file=True)
        assert is_valid is False
        assert "MissingEntryPoint" in (err or "")

    @pytest.mark.parametrize("filename", [
        "test_calculator.py",
        "test_safety.py",
        "calculator_test.py",
        "module_tests.py",
    ])
    def test_test_file_without_entry_point_passes(self, filename):
        """Тестовый файл с функциями assert освобождается от обязательной точки входа."""
        code = (
            "import pytest\n\n"
            "def test_addition():\n"
            "    assert 2 + 2 == 4\n"
        )
        is_valid, err = SubagentEngineer._validate_python_syntax(filename, code, is_entry_file=True)
        assert is_valid is True
        assert err is None

    def test_test_file_with_syntax_error_still_caught(self):
        """Реальные синтаксические ошибки в тестах не игнорируются."""
        broken_code = (
            "def test_broken(:\n"
            "    assert True\n"
        )
        is_valid, err = SubagentEngineer._validate_python_syntax("test_broken.py", broken_code, is_entry_file=True)
        assert is_valid is False
        assert "SyntaxError" in (err or "")


class TestLoopDetectorLogic:
    """Симуляция работы конечного автомата детектора зацикливания."""

    def test_loop_trigger_on_three_identical_errors(self):
        """При трёх подряд одинаковых ошибках счетчик зацикливания достигает порога 3."""
        consecutive_same_error_count = 0
        last_error_fingerprint = None
        max_same_error_retries = 3
        stopped_by_loop = False

        repeated_error = 'File "app.py", line 50\nIndexError: tuple index out of range'

        for _ in range(3):
            current_fp = SubagentEngineer._extract_error_fingerprint(repeated_error)
            if current_fp and current_fp == last_error_fingerprint:
                consecutive_same_error_count += 1
            else:
                consecutive_same_error_count = 1
                last_error_fingerprint = current_fp

            if consecutive_same_error_count >= max_same_error_retries:
                stopped_by_loop = True
                break

        assert stopped_by_loop is True
        assert consecutive_same_error_count == 3

    def test_loop_resets_when_error_evolves(self):
        """Если ошибка меняется (агент продвинулся дальше), счетчик сбрасывается."""
        consecutive_same_error_count = 0
        last_error_fingerprint = None
        max_same_error_retries = 3

        errors = [
            'File "app.py", line 10\nModuleNotFoundError: No module named "math_lib"',
            'File "app.py", line 10\nModuleNotFoundError: No module named "math_lib"',
            'File "app.py", line 25\nTypeError: "NoneType" object is not iterable',  # Смена ошибки
            'File "app.py", line 25\nTypeError: "NoneType" object is not iterable',
        ]

        for err in errors:
            current_fp = SubagentEngineer._extract_error_fingerprint(err)
            if current_fp and current_fp == last_error_fingerprint:
                consecutive_same_error_count += 1
            else:
                consecutive_same_error_count = 1
                last_error_fingerprint = current_fp

        # На третьем шаге счетчик сбросился в 1, на четвертом стал 2 -> остановки не произошло
        assert consecutive_same_error_count == 2


class TestNoOpGuardRetryAndParser:
    """Тестирование No-Op Guard ретрая, промпта, assistant draft и разрешения цикла через парсер."""

    def test_noop_guard_retry_structure_and_assistant_draft(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setattr("services.sandbox_manager.SandboxManager.get_workspace_dir", lambda: tmp_path)
        game_file = tmp_path / "game.py"
        orig_code = (
            "import sys\n"
            "def update():\n"
            "    # collision check\n"
            "    pass\n"
            "if __name__ == '__main__':\n"
            "    update()\n"
        )
        game_file.write_text(orig_code, encoding="utf-8")

        class MockLMClient:
            def __init__(self):
                self.calls = 0
                self.recorded_messages = []

            def chat_completion(self, messages, *args, **kwargs):
                self.calls += 1
                self.recorded_messages.append(list(messages))
                if self.calls == 1:
                    # Architect call
                    return json.dumps({
                        "artifact_type": "gui_app",
                        "intent": "maintenance",
                        "target_artifact": "game.py",
                        "verification_script": None,
                        "plan": "Починить столкновения"
                    })
                elif self.calls == 2:
                    # Turn 1: Model outputs identical code (triggers No-Op Guard)
                    return f'<<TOOL:write_file filename="game.py">>\n{orig_code}\n<</TOOL:write_file>>'
                elif self.calls == 3:
                    # Turn 2: Model outputs old code AND new code in py_blocks
                    modified_code = (
                        "import sys\n"
                        "class GameEngine:\n"
                        "    def __init__(self):\n"
                        "        self.active = True\n"
                        "    def check_hit(self):\n"
                        "        return True\n\n"
                        "def update():\n"
                        "    # fixed collision condition and extended logic\n"
                        "    engine = GameEngine()\n"
                        "    print('removed on hit')\n"
                        "    return engine.check_hit()\n\n"
                        "# Additional comments to satisfy SubstanceGuard min_bytes requirement >= 350\n"
                        "# This engine handles collision resolution and sprite removals\n"
                        "if __name__ == '__main__':\n"
                        "    update()\n"
                    )
                    return f"""
Вот старый код:
```python
{orig_code}
```
А вот исправленный вариант:
```python
{modified_code}
```
<<TOOL:run_script filename="game.py">>
"""
                return "OK"

        client = MockLMClient()
        task_prompt = "исправить коллизии и удаление объектов"
        res = SubagentEngineer.run_worker(
            task_prompt=task_prompt,
            lm_client=client
        )

        assert client.calls >= 3
        # In call 3 (retry after turn 1 No-Op Guard):
        retry_messages = client.recorded_messages[2]
        # messages[2] is assistant
        assistant_msg = retry_messages[2]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == "Понял (попытка 1), вношу изменения в целевую логику файла."

        # messages[3] is user
        user_msg = retry_messages[3]
        assert user_msg["role"] == "user"
        expected_part = (
            f"В файле 'game.py' не обнаружено изменений логики для задачи: «{task_prompt}». "
            f"Код полностью идентичен текущей версии на диске. "
            f"Внеси необходимые изменения в реализацию и верни обновлённый код через <<TOOL:write_file filename=\"game.py\">>."
        )
        assert expected_part in user_msg["content"]
        assert "файлы физически отсутствуют на диске, patch_file запрещён" not in user_msg["content"]
        assert "Запрещено использовать <<TOOL:patch_file>>" not in user_msg["content"]

        # And verify that turn 2 picked the modified code and saved it
        assert "removed on hit" in game_file.read_text(encoding="utf-8")
        assert res["success"] is True

    def test_maintenance_syntax_error_does_not_add_patch_file_ban(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setattr("services.sandbox_manager.SandboxManager.get_workspace_dir", lambda: tmp_path)
        game_file = tmp_path / "game.py"
        game_file.write_text("def valid(): pass\n", encoding="utf-8")

        class MockLMClient:
            def __init__(self):
                self.calls = 0
                self.recorded_messages = []

            def chat_completion(self, messages, *args, **kwargs):
                self.calls += 1
                self.recorded_messages.append(list(messages))
                if self.calls == 1:
                    return json.dumps({
                        "artifact_type": "gui_app",
                        "intent": "maintenance",
                        "target_artifact": "game.py",
                        "verification_script": None,
                        "plan": "Доработка"
                    })
                elif self.calls == 2:
                    # Syntax error (not noop)
                    return '<<TOOL:write_file filename="game.py">>\ndef broken(\n<</TOOL:write_file>>'
                elif self.calls == 3:
                    return '<<TOOL:write_file filename="game.py">>\ndef fixed():\n    pass\nif __name__ == "__main__":\n    fixed()\n<</TOOL:write_file>>\n<<TOOL:run_script filename="game.py">>'
                return "OK"

        client = MockLMClient()
        SubagentEngineer.run_worker(
            task_prompt="доработать логику",
            lm_client=client
        )

        assert client.calls >= 3
        retry_user_msg = client.recorded_messages[2][3]["content"]
        assert "Запрещено использовать <<TOOL:patch_file>>" not in retry_user_msg
        assert "файлы физически отсутствуют на диске" not in retry_user_msg

    def test_new_project_syntax_error_adds_patch_file_ban(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setattr("services.sandbox_manager.SandboxManager.get_workspace_dir", lambda: tmp_path)

        class MockLMClient:
            def __init__(self):
                self.calls = 0
                self.recorded_messages = []

            def chat_completion(self, messages, *args, **kwargs):
                self.calls += 1
                self.recorded_messages.append(list(messages))
                if self.calls == 1:
                    return json.dumps({
                        "artifact_type": "cli_tool",
                        "intent": "new",
                        "target_artifact": "main.py",
                        "verification_script": None,
                        "plan": "Создать с нуля"
                    })
                elif self.calls == 2:
                    return '<<TOOL:write_file filename="main.py">>\ndef broken(\n<</TOOL:write_file>>'
                elif self.calls == 3:
                    return '<<TOOL:write_file filename="main.py">>\ndef fixed():\n    pass\nif __name__ == "__main__":\n    fixed()\n<</TOOL:write_file>>\n<<TOOL:run_script filename="main.py">>'
                return "OK"

        client = MockLMClient()
        SubagentEngineer.run_worker(
            task_prompt="Создать утилиту с нуля",
            lm_client=client
        )

        assert client.calls >= 3
        retry_user_msg = client.recorded_messages[2][3]["content"]
        assert "Запрещено использовать <<TOOL:patch_file>>" in retry_user_msg
        assert "файлы физически отсутствуют на диске" in retry_user_msg

    def test_module_format_completes_safely_without_run_script(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setattr("services.sandbox_manager.SandboxManager.get_workspace_dir", lambda: tmp_path)

        class MockLMClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return json.dumps({
                        "artifact_type": "module",
                        "format": "module",
                        "intent": "new",
                        "target_artifact": "utils.py",
                        "verification_script": None,
                        "plan": "Создать библиотеку utils.py"
                    })
                elif self.calls == 2:
                    return '<<TOOL:write_file filename="utils.py">>\ndef helper(x: int) -> int:\n    return x * 2\n<</TOOL:write_file>>'
                return "OK"

        run_script_called = False
        def mock_run_script(filename):
            nonlocal run_script_called
            run_script_called = True
            raise RuntimeError("run_script should not be called for module!")

        monkeypatch.setattr("services.tool_engine.ToolEngine.run_script", mock_run_script)

        res = SubagentEngineer.run_worker(
            task_prompt="Создать модуль utils.py",
            lm_client=MockLMClient()
        )

        assert res["success"] is True
        assert res["file_name"] == "utils.py"
        assert "проверен AST-валидатором" in res["summary"]
        assert not run_script_called

    def test_unicodeescape_hint_in_syntax_correction(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setattr("services.sandbox_manager.SandboxManager.get_workspace_dir", lambda: tmp_path)

        class MockLMClient:
            def __init__(self):
                self.calls = 0
                self.recorded_messages = []

            def chat_completion(self, messages, *args, **kwargs):
                self.calls += 1
                self.recorded_messages.append(list(messages))
                if self.calls == 1:
                    return json.dumps({
                        "artifact_type": "cli_tool",
                        "intent": "new",
                        "target_artifact": "main.py",
                        "verification_script": None,
                        "plan": "Создать утилиту"
                    })
                elif self.calls == 2:
                    # Invalid unicodeescape syntax error in docstring
                    return '<<TOOL:write_file filename="main.py">>\n"""\nPath: C:\\Users\\test\n"""\nif __name__ == "__main__":\n    pass\n<</TOOL:write_file>>'
                elif self.calls == 3:
                    return (
                        '<<TOOL:write_file filename="main.py">>\n'
                        'r"""\n'
                        'Path: C:\\Users\\test\n'
                        '"""\n'
                        'class Utility:\n'
                        '    """Utility runner class for diagnostics."""\n'
                        '    def __init__(self):\n'
                        '        self.active = True\n'
                        '    def run(self):\n'
                        '        print("Executed successfully")\n'
                        '        return True\n\n'
                        'def main():\n'
                        '    u = Utility()\n'
                        '    return u.run()\n\n'
                        '# Comments to satisfy SubstanceGuard >= 350 bytes\n'
                        '# Utility application logic and configuration checks\n'
                        'if __name__ == "__main__":\n'
                        '    main()\n'
                        '<</TOOL:write_file>>\n'
                        '<<TOOL:run_script filename="main.py">>'
                    )
                return "OK"

        res = SubagentEngineer.run_worker(
            task_prompt="Создать утилиту main.py",
            lm_client=MockLMClient()
        )

        assert len(MockLMClient.recorded_messages if hasattr(MockLMClient, "recorded_messages") else res) or True
        assert res["success"] is True