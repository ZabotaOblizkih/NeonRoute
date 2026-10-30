"""
tests/test_engineer_tdd_pipeline.py
Тесты для двухфазного Code-First TDD пайплайна:
1. Санитизатор кода sanitize_code (защита от самоимпортов).
2. Архитектор (EngineerPlanner):
   - Запрет генерации кода тестов в промпте
   - Строгий JSON: task_type, target_file, interface_spec
3. Работяга (SubagentEngineer):
   - Ветка NEW_MODULE: Код -> Тест (промпт-гард pytest, без фикстур) -> Двустороннее исцеление (нечётные: код, чётные: тесты)
   - Ветка INCREMENTAL_FEATURE: Pre-flight alert при падающей базе -> Правки -> Regression Check -> Дописывание тестов -> Финальная верификация
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from services.tool_engine import ToolEngine, sanitize_code
from services.engineer_planner import EngineerPlanner, ArchitectPlan
from services.engineer_prompts import get_architect_prompt
from services.subagent_engineer import SubagentEngineer
from services.engineer_tdd import CodeFirstTDDPipeline
from services.sandbox_manager import SandboxManager


@pytest.fixture
def workspace_tmp():
    ws_dir = SandboxManager.get_workspace_dir() / "_test_tmp_tdd"
    ws_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield ws_dir
    finally:
        import shutil
        if ws_dir.exists():
            shutil.rmtree(ws_dir, ignore_errors=True)


class TestCodeSanitizer:
    """1. Тестирование предохранителя от самоимпорта sanitize_code."""

    def test_sanitize_code_removes_from_self_import(self):
        raw = (
            "from task_manager import TaskManager\n"
            "import os\n"
            "class TaskManager:\n"
            "    pass\n"
        )
        cleaned = sanitize_code(raw, "task_manager.py")
        assert "from task_manager import TaskManager" not in cleaned
        assert "import os" in cleaned
        assert "class TaskManager:" in cleaned

    def test_sanitize_code_removes_import_self(self):
        raw = (
            "import math\n"
            "import utils\n"
            "def calculate(): return math.sqrt(4)\n"
        )
        cleaned = sanitize_code(raw, "utils.py")
        assert "import utils" not in cleaned
        assert "import math" in cleaned

    def test_sanitize_code_preserves_external_imports(self):
        raw = (
            "import pytest\n"
            "from datetime import datetime\n"
            "def test_foo(): pass\n"
        )
        cleaned = sanitize_code(raw, "test_task_manager.py")
        assert "import pytest" in cleaned
        assert "from datetime import datetime" in cleaned

    def test_tool_engine_write_file_auto_sanitizes(self, workspace_tmp):
        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            content = "from my_module import MyClass\nclass MyClass:\n    pass\n"
            res = ToolEngine.write_file("my_module.py", content)
            assert res.success is True
            saved_content = (workspace_tmp / "my_module.py").read_text(encoding="utf-8")
            assert "from my_module import MyClass" not in saved_content
            assert "class MyClass:" in saved_content


class TestArchitectCodeFirstContract:
    """2. Архитектурные требования к Архитектору (EngineerPlanner)."""

    def test_architect_prompt_forbids_python_test_code(self):
        prompt = get_architect_prompt()
        assert "ЗАПРЕТ НА ГЕНЕРАЦИЮ КОДА ТЕСТОВ" in prompt or "Архитектор категорически не пишет код на Python" in prompt
        assert "task_type" in prompt
        assert "NEW_MODULE" in prompt
        assert "INCREMENTAL_FEATURE" in prompt
        assert "interface_spec" in prompt

    def test_architect_parses_strict_tdd_json(self, workspace_tmp):
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = json.dumps({
            "task_type": "NEW_MODULE",
            "target_file": "staff_registry.py",
            "interface_spec": {
                "classes": [
                    {
                        "name": "StaffRegistry",
                        "methods": [
                            {
                                "name": "add_employee",
                                "args": ["self", "emp_id: int", "name: str"],
                                "return_type": "bool",
                                "exceptions": ["ValueError if emp_id < 0"]
                            }
                        ]
                    }
                ]
            },
            "plan": "Реестр сотрудников"
        })

        plan = EngineerPlanner.plan_architecture(
            task_prompt="Создай модуль staff_registry.py для учета сотрудников",
            client=mock_client,
            ws_dir=workspace_tmp
        )

        assert plan.task_type == "NEW_MODULE"
        assert plan.target_file == "staff_registry.py"
        assert plan.target_artifact == "staff_registry.py"
        assert isinstance(plan.interface_spec, dict)
        assert "classes" in plan.interface_spec
        assert plan.interface_spec["classes"][0]["name"] == "StaffRegistry"
        assert "interface_spec" in plan.plan_content

    def test_architect_parses_incremental_feature_json(self, workspace_tmp):
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = json.dumps({
            "task_type": "INCREMENTAL_FEATURE",
            "target_file": "staff_registry.py",
            "interface_spec": {
                "classes": [
                    {
                        "name": "StaffRegistry",
                        "methods": [
                            {
                                "name": "remove_employee",
                                "args": ["self", "emp_id: int"],
                                "return_type": "bool",
                                "exceptions": ["KeyError if emp_id not found"]
                            }
                        ]
                    }
                ]
            },
            "plan": "Добавление удаления сотрудников"
        })

        plan = EngineerPlanner.plan_architecture(
            task_prompt="Добавь метод remove_employee в staff_registry.py",
            client=mock_client,
            ws_dir=workspace_tmp
        )

        assert plan.task_type == "INCREMENTAL_FEATURE"
        assert plan.is_maintenance is True
        assert plan.target_file == "staff_registry.py"


class TestSubagentEngineerNewModulePipeline:
    """3. Ветка NEW_MODULE: Код -> Тест (промпт-гард pytest) -> Двустороннее исцеление."""

    def test_new_module_happy_path(self, workspace_tmp):
        class MockClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    # Архитектор
                    return json.dumps({
                        "task_type": "NEW_MODULE",
                        "target_file": "calculator.py",
                        "interface_spec": {
                            "classes": [{"name": "Calculator", "methods": [{"name": "add", "args": ["self", "a", "b"]}]}]
                        },
                        "plan": "Калькулятор"
                    })
                elif self.calls == 2:
                    # Шаг 1: Код реализации
                    return (
                        "<<TOOL:write_file filename=\"calculator.py\">>\n"
                        "from calculator import Calculator\n"  # Случайный самоимпорт для проверки санитизатора
                        "class Calculator:\n"
                        "    def add(self, a, b):\n"
                        "        return a + b\n"
                        "<</TOOL:write_file>>"
                    )
                elif self.calls == 3:
                    # Шаг 2: Тест (промпт-гард pytest)
                    # Проверяем, что промпт содержит запрет unittest и внешних фикстур
                    user_msg = str(messages[-1]["content"])
                    assert "СТРОГО функции pytest" in user_msg or "pytest" in user_msg
                    assert "ЗАПРЕЩЕНО указывать в аргументах функций внешние фикстуры" in user_msg
                    return (
                        "<<TOOL:write_file filename=\"test_calculator.py\">>\n"
                        "import pytest\n"
                        "from calculator import Calculator\n\n"
                        "def test_add():\n"
                        "    c = Calculator()\n"
                        "    assert c.add(2, 3) == 5\n"
                        "<</TOOL:write_file>>"
                    )
                return "OK"

        client = MockClient()
        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            res = SubagentEngineer.run_worker(
                task_prompt="Создай модуль calculator.py",
                lm_client=client
            )

        assert res["success"] is True
        assert res["target_artifact"] == "calculator.py"
        assert res["verification_script"] == "test_calculator.py"
        assert res["verification_passed"] is True

        # Проверяем, что код на диске был санитизирован
        calc_content = (workspace_tmp / "calculator.py").read_text(encoding="utf-8")
        assert "from calculator import Calculator" not in calc_content
        assert "class Calculator:" in calc_content

        test_content = (workspace_tmp / "test_calculator.py").read_text(encoding="utf-8")
        assert "def test_add():" in test_content

    def test_new_module_two_way_healing_odd_fixes_code_even_fixes_tests(self, workspace_tmp):
        """
        Проверка двустороннего исцеления:
        Попытка 1 (нечётная): модель исправляет код реализации.
        Попытка 2 (чётная): модель исправляет опечатку в assert теста.
        """
        class MockHealingClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    # Архитектор
                    return json.dumps({
                        "task_type": "NEW_MODULE",
                        "target_file": "greeter.py",
                        "interface_spec": {"classes": [{"name": "Greeter", "methods": [{"name": "greet"}]}]},
                        "plan": "Приветствие"
                    })
                elif self.calls == 2:
                    # Шаг 1: Код с ошибкой в реализации (возвращает bye вместо hello)
                    return (
                        "<<TOOL:write_file filename=\"greeter.py\">>\n"
                        "class Greeter:\n"
                        "    def greet(self, name):\n"
                        "        return f'Bye, {name}'\n"
                        "<</TOOL:write_file>>"
                    )
                elif self.calls == 3:
                    # Шаг 2: Тест с опечаткой в тесте
                    return (
                        "<<TOOL:write_file filename=\"test_greeter.py\">>\n"
                        "from greeter import Greeter\n"
                        "def test_greet():\n"
                        "    g = Greeter()\n"
                        "    assert g.greet('Alice') == 'WRONG_ASSERT'\n"
                        "<</TOOL:write_file>>"
                    )
                elif self.calls == 4:
                    # Попытка 1 исцеления (Нечётная): запрос на исправление кода реализации
                    user_msg = str(messages[-1]["content"])
                    assert "Попытка 1/15" in user_msg or "нечётная" in user_msg
                    return (
                        "<<TOOL:write_file filename=\"greeter.py\">>\n"
                        "class Greeter:\n"
                        "    def greet(self, name):\n"
                        "        return f'Hello, {name}'\n"
                        "<</TOOL:write_file>>"
                    )
                elif self.calls == 5:
                    # Попытка 2 исцеления (Чётная): запрос на исправление теста
                    user_msg = str(messages[-1]["content"])
                    assert "Попытка 2/15" in user_msg or "чётная" in user_msg or "структурой теста" in user_msg
                    return (
                        "<<TOOL:write_file filename=\"test_greeter.py\">>\n"
                        "from greeter import Greeter\n"
                        "def test_greet():\n"
                        "    g = Greeter()\n"
                        "    assert g.greet('Alice') == 'Hello, Alice'\n"
                        "<</TOOL:write_file>>"
                    )
                return "OK"

        client = MockHealingClient()
        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            res = SubagentEngineer.run_worker(
                task_prompt="Создай модуль greeter.py",
                lm_client=client
            )

        assert res["success"] is True
        assert res["turns"] == 3  # Успех на 3-м прогоне (после 2-х попыток исправления)
        final_test = (workspace_tmp / "test_greeter.py").read_text(encoding="utf-8")
        assert "Hello, Alice" in final_test


class TestSubagentEngineerIncrementalFeaturePipeline:
    """4. Ветка INCREMENTAL_FEATURE: Pre-flight -> Правки -> Regression Check -> Дописывание -> Верификация."""

    def test_incremental_preflight_baseline_fails_and_aborts(self, workspace_tmp):
        """Шаг 1: Если существующие тесты падают ДО правок — фаза немедленно прерывается с алертом."""
        # Создаем падающий существующий тест в workspace
        mod = workspace_tmp / "storage.py"
        mod.write_text("class Storage: pass\n", encoding="utf-8")
        test_file = workspace_tmp / "test_storage.py"
        test_file.write_text("def test_broken(): assert False, 'broken baseline'\n", encoding="utf-8")

        mock_client = MagicMock()
        mock_client.chat_completion.return_value = json.dumps({
            "task_type": "INCREMENTAL_FEATURE",
            "target_file": "storage.py",
            "interface_spec": {"classes": [{"name": "Storage", "methods": [{"name": "save"}]}]},
            "plan": "Доработка"
        })

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            res = SubagentEngineer.run_worker(
                task_prompt="Добавь метод save в storage.py",
                lm_client=mock_client
            )

        assert res["success"] is False
        assert "Pre-flight Baseline Check FAILED" in res["error"]
        assert "broken baseline" in res["error"]
        # Модель не должна вызываться для генерации кода после сбоя pre-flight!
        assert mock_client.chat_completion.call_count == 1  # Только вызов Архитектора

    def test_incremental_feature_regression_recovery_and_append_tests(self, workspace_tmp):
        """
        Шаги 1-5 INCREMENTAL_FEATURE:
        База зеленая -> Внесение правок -> Regression Check -> Дописывание тестов без дублирования импортов -> 100% Green.
        """
        mod = workspace_tmp / "account.py"
        mod.write_text(
            "class Account:\n"
            "    def __init__(self, balance=0):\n"
            "        self.balance = balance\n"
            "    def deposit(self, amount):\n"
            "        self.balance += amount\n"
            "        return self.balance\n",
            encoding="utf-8"
        )
        test_file = workspace_tmp / "test_account.py"
        test_file.write_text(
            "import pytest\n"
            "from account import Account\n\n"
            "def test_deposit():\n"
            "    acc = Account(10)\n"
            "    assert acc.deposit(5) == 15\n",
            encoding="utf-8"
        )

        class MockIncrementalClient:
            def __init__(self):
                self.calls = 0

            def chat_completion(self, messages, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    # Архитектор
                    return json.dumps({
                        "task_type": "INCREMENTAL_FEATURE",
                        "target_file": "account.py",
                        "interface_spec": {
                            "classes": [{"name": "Account", "methods": [{"name": "withdraw", "args": ["self", "amount"]}]}]
                        },
                        "plan": "Добавить withdraw"
                    })
                elif self.calls == 2:
                    # Шаг 2: Внесение правок (добавление withdraw с сохранением deposit)
                    return (
                        "<<TOOL:write_file filename=\"account.py\">>\n"
                        "class Account:\n"
                        "    def __init__(self, balance=0):\n"
                        "        self.balance = balance\n"
                        "    def deposit(self, amount):\n"
                        "        self.balance += amount\n"
                        "        return self.balance\n"
                        "    def withdraw(self, amount):\n"
                        "        if amount > self.balance:\n"
                        "            raise ValueError('Insufficient funds')\n"
                        "        self.balance -= amount\n"
                        "        return self.balance\n"
                        "<</TOOL:write_file>>"
                    )
                elif self.calls == 3:
                    # Шаг 4: Дописывание тестов строго для новых методов
                    user_msg = str(messages[-1]["content"])
                    assert "НЕ дублируй существующие импорты" in user_msg
                    return (
                        "import pytest\n"
                        "from account import Account\n\n"
                        "def test_withdraw_valid():\n"
                        "    acc = Account(20)\n"
                        "    assert acc.withdraw(5) == 15\n\n"
                        "def test_withdraw_insufficient():\n"
                        "    acc = Account(10)\n"
                        "    with pytest.raises(ValueError):\n"
                        "        acc.withdraw(50)\n"
                    )
                return "OK"

        client = MockIncrementalClient()
        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            res = SubagentEngineer.run_worker(
                task_prompt="Добавь метод withdraw в account.py",
                lm_client=client
            )

        assert res["success"] is True
        assert res["verification_passed"] is True

        # Проверяем файл тестов: старый test_deposit не затёрт, новый дописан, нет дублирования import pytest
        updated_test = (workspace_tmp / "test_account.py").read_text(encoding="utf-8")
        assert "def test_deposit():" in updated_test
        assert "def test_withdraw_valid():" in updated_test
        assert "def test_withdraw_insufficient():" in updated_test
        assert updated_test.count("import pytest") == 1  # Без дублирования импортов!
