"""
tests/test_verification_contract_and_root_cause_loop.py
Тесты для архитектурного рефакторинга:
1. Снятие ограничения MissingEntryPoint для библиотечных модулей.
2. Контракт Архитектора (target_artifact, verification_script).
3. Root-Cause Loop: автоматический перезапуск упавшего верификатора при создании зависимостей.
4. Строгий критерий завершения задачи (предотвращение ложноположительного завершения).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent
while PROJECT_ROOT.name and not (PROJECT_ROOT / "services").exists() and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.subagent_engineer import SubagentEngineer
from services.sandbox_runner import validate_module_syntax
from services.sandbox_manager import SandboxManager


@pytest.fixture
def workspace_tmp():
    ws_dir = SandboxManager.get_workspace_dir() / "_test_tmp_vloop"
    ws_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield ws_dir
    finally:
        import shutil
        if ws_dir.exists():
            shutil.rmtree(ws_dir, ignore_errors=True)


class TestModuleSyntaxExemption:
    """Проверка снятия ограничения MissingEntryPoint для библиотечных модулей."""

    def test_pure_library_module_without_entry_point_passes(self):
        """Чистый библиотечный модуль без if __name__ == '__main__': проходит валидацию."""
        code = (
            "class StringVault:\n"
            "    def __init__(self):\n"
            "        self._vault = {}\n"
            "\n"
            "    def store(self, key: str, value: str) -> None:\n"
            "        self._vault[key] = value\n"
            "\n"
            "    def get(self, key: str) -> str:\n"
            "        return self._vault.get(key, '')\n"
        )
        is_valid, err = SubagentEngineer._validate_python_syntax("string_vault.py", code, is_entry_file=False)
        assert is_valid is True
        assert err is None

    def test_module_syntax_error_still_caught(self):
        """Синтаксические ошибки в модулях всегда отлавливаются валидатором."""
        broken_code = (
            "class BrokenVault:\n"
            "    def store(self, key:\n"
            "        pass\n"
        )
        is_valid, err = SubagentEngineer._validate_python_syntax("broken.py", broken_code, is_entry_file=False)
        assert is_valid is False
        assert "SyntaxError" in (err or "")

    def test_declared_entry_file_without_entry_point_fails(self):
        """Файл, объявленный Архитектором как точка входа, без entry point бракуется с MissingEntryPoint."""
        code = (
            "def calculate_total(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        is_valid, err = SubagentEngineer._validate_python_syntax("runner.py", code, is_entry_file=True)
        assert is_valid is False
        assert "MissingEntryPoint" in (err or "")

    def test_sandbox_runner_validate_module_syntax(self):
        """sandbox_runner.validate_module_syntax корректно валидирует синтаксис модулей."""
        valid_code = "class A:\n    pass\n"
        invalid_code = "class A:\n    pass\n" + "def broken("
        assert validate_module_syntax(valid_code) is True
        assert validate_module_syntax(invalid_code) is False


class TestArchitectContractAndRootCauseLoop:
    """Тестирование контракта Архитектора и цикла разрешения зависимостей (Root-Cause Loop)."""

    def test_architect_contract_parsing(self, workspace_tmp):
        """Архитектор корректно парсит target_artifact и verification_script."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # 1. Ответ Архитектора:
            '```json\n{"intent": "new", "target_artifact": "string_vault.py", "verification_script": "test_string_vault.py", "plan": "Реализация хранилища"}\n```',
            # 2. Ход 1 Инженера:
            '<<TOOL:write_file filename="string_vault.py">>\nclass StringVault:\n    pass\n<</TOOL:write_file>>\n'
            '<<TOOL:write_file filename="test_string_vault.py">>\ndef test_vault():\n    assert True\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="test_string_vault.py">>'
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            with patch("services.tool_engine.ToolEngine.write_file") as mock_write, \
                 patch("services.tool_engine.ToolEngine.run_script") as mock_run:

                mock_write.return_value = MagicMock(success=True, output="ok")
                mock_run.return_value = MagicMock(success=True, output="1 passed", stdout="1 passed", stderr="", return_code=0)

                res = SubagentEngineer.run_worker(
                    task_prompt="Создай модуль string_vault.py и тесты к нему",
                    lm_client=mock_client,
                    model_id="test_model"
                )

                assert res["success"] is True
                assert res["target_artifact"] == "string_vault.py"
                assert res["verification_script"] == "test_string_vault.py"
                assert res["verification_passed"] is True

    def test_root_cause_loop_reruns_verification_script_on_dependency_creation(self, workspace_tmp):
        """
        Если на ходе 1 скрипт верификации test_string_vault.py падает с ModuleNotFoundError (string_vault),
        а на ходе 2 создается модуль string_vault.py:
        песочница обязана автоматически перезапустить test_string_vault.py.
        """
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # 1. Архитектор:
            '```json\n{"intent": "new", "target_artifact": "string_vault.py", "verification_script": "test_string_vault.py", "plan": "Хранилище с тестами"}\n```',
            # 2. Ход 1 Инженера: создание только тестов
            '<<TOOL:write_file filename="test_string_vault.py">>\nimport string_vault\ndef test_ok():\n    assert string_vault.StringVault()\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="test_string_vault.py">>',
            # 3. Ход 2 Инженера: создание string_vault.py в ответ на ModuleNotFoundError (RCA Архитектора оптимизированно пропущен)
            '<<TOOL:write_file filename="string_vault.py">>\nclass StringVault:\n    def __init__(self):\n        pass\n<</TOOL:write_file>>'
        ]

        # Создаем файлы в workspace_tmp
        test_file = workspace_tmp / "test_string_vault.py"
        test_file.write_text("import string_vault\ndef test_ok(): pass\n", encoding="utf-8")

        vault_file = workspace_tmp / "string_vault.py"

        executed_scripts = []

        def fake_run_script(filename):
            executed_scripts.append(Path(filename).name)
            if Path(filename).name == "test_string_vault.py":
                if not vault_file.exists():
                    # Ход 1: модуля string_vault еще нет
                    return MagicMock(
                        success=False,
                        return_code=1,
                        output="ModuleNotFoundError: No module named 'string_vault'",
                        stderr="ModuleNotFoundError: No module named 'string_vault'",
                        stdout=""
                    )
                else:
                    # Ход 2: модуль создан, тест проходит
                    return MagicMock(
                        success=True,
                        return_code=0,
                        output="test_ok PASSED",
                        stdout="test_ok PASSED",
                        stderr=""
                    )
            return MagicMock(success=True, return_code=0, output="", stdout="", stderr="")

        def fake_write_file(filename, content):
            (workspace_tmp / filename).write_text(content, encoding="utf-8")
            return MagicMock(success=True, output="saved")

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            with patch("services.tool_engine.ToolEngine.write_file", side_effect=fake_write_file), \
                 patch("services.tool_engine.ToolEngine.run_script", side_effect=fake_run_script):

                res = SubagentEngineer.run_worker(
                    task_prompt="Реализуй string_vault.py и тесты к нему",
                    lm_client=mock_client,
                    model_id="test_model"
                )

                assert res["success"] is True
                assert res["verification_passed"] is True
                # Проверяем, что test_string_vault.py был перезапущен
                assert executed_scripts.count("test_string_vault.py") == 2
                assert executed_scripts[-1] == "test_string_vault.py"

    def test_strict_completion_fails_if_verification_script_fails(self, workspace_tmp):
        """
        Ложноположительное завершение блокируется: если скрипт верификации упал (assert failed),
        успешное создание вспомогательного файла не может закрыть задачу.
        """
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # 1. Архитектор:
            '```json\n{"intent": "new", "target_artifact": "string_vault.py", "verification_script": "test_string_vault.py", "plan": "Хранилище с тестами"}\n```',
            # 2. Ход 1: тест падает с AssertionError
            '<<TOOL:write_file filename="test_string_vault.py">>\ndef test_fail():\n    assert False\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="test_string_vault.py">>',
            # 3. RCA Архитектора:
            'Тест упал с ошибкой assert False.',
            # 4. Ход 2: модель создает модуль, но тест все равно упадет
            '<<TOOL:write_file filename="string_vault.py">>\nclass StringVault:\n    pass\n<</TOOL:write_file>>'
        ]

        test_file = workspace_tmp / "test_string_vault.py"
        test_file.write_text("def test_fail(): assert False\n", encoding="utf-8")

        def fake_run_script(filename):
            # Тест падает всегда
            return MagicMock(
                success=False,
                return_code=1,
                output="AssertionError: assert False",
                stderr="AssertionError: assert False",
                stdout=""
            )

        def fake_write_file(filename, content):
            (workspace_tmp / filename).write_text(content, encoding="utf-8")
            return MagicMock(success=True, output="saved")

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            with patch("services.tool_engine.ToolEngine.write_file", side_effect=fake_write_file), \
                 patch("services.tool_engine.ToolEngine.run_script", side_effect=fake_run_script):
                with patch("utils.config_manager.ConfigManager.load_config") as mock_cfg:
                    mock_cfg.return_value = {
                        "engineer_settings": {
                            "max_turns": 2,
                            "max_same_error_retries": 5
                        }
                    }

                    res = SubagentEngineer.run_worker(
                        task_prompt="Реализуй string_vault.py",
                        lm_client=mock_client,
                        model_id="test_model"
                    )

                    # Задача НЕ должна быть закрыта со статусом True!
                    assert res["success"] is False
                    assert res["verification_passed"] is False

    def test_no_verification_script_requires_target_artifact_success(self, workspace_tmp):
        """
        Если verification_script отсутствует, задача считается завершенной только если
        основной target_artifact успешно выполнен в песочнице с exit_code == 0.
        """
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # 1. Архитектор: без verification_script
            '```json\n{"intent": "new", "target_artifact": "app.py", "verification_script": null, "plan": "Простое приложение"}\n```',
            # 2. Ход 1: создает app.py и запускает его
            '<<TOOL:write_file filename="app.py">>\ndef main():\n    print("App running")\nif __name__ == "__main__":\n    main()\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="app.py">>'
        ]

        def fake_run_script(filename):
            return MagicMock(
                success=True,
                return_code=0,
                output="App running",
                stdout="App running",
                stderr=""
            )

        def fake_write_file(filename, content):
            (workspace_tmp / filename).write_text(content, encoding="utf-8")
            return MagicMock(success=True, output="saved")

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            with patch("services.tool_engine.ToolEngine.write_file", side_effect=fake_write_file), \
                 patch("services.tool_engine.ToolEngine.run_script", side_effect=fake_run_script):

                res = SubagentEngineer.run_worker(
                    task_prompt="Создай app.py",
                    lm_client=mock_client,
                    model_id="test_model"
                )

                assert res["success"] is True
                assert res["target_artifact"] == "app.py"
                assert res["file_name"] == "app.py"
