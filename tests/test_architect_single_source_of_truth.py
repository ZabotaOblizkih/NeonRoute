"""
tests/test_architect_single_source_of_truth.py
Unit and integration tests for:
1. Matrix-based Architect prompt & dynamic Engineer system prompts.
2. Hard priority for engineer_model.
3. Extended workspace files for Architect (.json, .csv, .xlsx, .docx, .txt, .db, .sqlite).
4. Normalization and single source of truth format handling.
5. Dynamic No-Op Guard without hardcoded file names.
6. Auxiliary files do not hijack script_to_run when target_artifact exists.
7. Deterministic early completion for document generation.
8. OOP inheritance in cross-module contracts.
9. Intentional deletion/refactoring keywords in validate_code_integrity.
10. GUI smoke test exclusion for test files.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from services.engineer_prompts import (
    get_architect_prompt,
    get_engineer_system_prompt,
)
from services.subagent_engineer import SubagentEngineer
from services.code_engine import CodeValidator
from services.sandbox_manager import SandboxManager


@pytest.fixture
def workspace_tmp():
    ws_dir = SandboxManager.get_workspace_dir() / "_test_tmp_ssot"
    ws_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield ws_dir
    finally:
        import shutil
        if ws_dir.exists():
            shutil.rmtree(ws_dir, ignore_errors=True)


class TestArchitectMatrixAndDynamicPrompts:
    """1. Проверка матрицы форматов Архитектора и динамических системных промптов Инженера."""

    def test_architect_prompt_matrix_and_addons(self):
        prompt = get_architect_prompt(
            available_libs=["pygame", "openpyxl"],
            ws_files=["data.csv", "main.py"],
            injected_architect_rules="[Правила текущего скилла]",
            default_artifact="game.py"
        )
        assert "главный системный архитектор среды NeonRoute" in prompt
        assert "ПРАВИЛА И СТАНДАРТЫ ДЛЯ ТЕКУЩЕГО ТИПА ЗАДАЧИ:" in prompt
        assert "[Правила текущего скилла]" in prompt
        assert "ПРАВИЛА INTENT:" in prompt
        assert '"interactive_loop"' in prompt
        assert '"pure_logic_tdd"' in prompt
        assert '"game.py"' in prompt
        assert "[Доступные библиотеки: pygame, openpyxl]" in prompt
        assert "[Существующие файлы в рабочей директории: data.csv, main.py]" in prompt

    def test_dynamic_engineer_prompts(self):
        from services.engineer_skill_router import EngineerSkillRouter
        router = EngineerSkillRouter.get_instance()
        for skill_id in ("interactive_loop", "pure_logic_tdd", "batch_artifact", "service_backend", "cli_automation"):
            meta = router.skills[skill_id]
            p = get_engineer_system_prompt(injected_worker_rules=meta["worker_rules"])
            assert "ведущий инженер-разработчик NeonRoute" in p
            assert "СТАНДАРТЫ РЕАЛИЗАЦИИ ДЛЯ ДАННОГО АРХЕТИПА:" in p
            assert meta["worker_rules"] in p
            assert "ОБЩИЕ ПРАВИЛА КОДОГЕНЕРАЦИИ:" in p


class TestEngineerModelPriorityAndWsFiles:
    """2. Проверка приоритета engineer_model и сбора файлов данных."""

    def test_engineer_model_priority(self, workspace_tmp):
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = '```json\n{"format": "cli", "intent": "new", "target_artifact": "main.py", "verification_script": null, "plan": "CLI tool"}\n```'

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp), \
             patch("utils.config_manager.ConfigManager.load_config", return_value={"engineer_model": "custom_engineer:8b", "active_model": "general:7b"}), \
             patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True)), \
             patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, output="ok")):
            
            SubagentEngineer.run_worker(
                task_prompt="создай скрипт main.py",
                lm_client=mock_client
            )
            # Первый вызов к chat_completion должен быть с custom_engineer:8b
            call_kwargs = mock_client.chat_completion.call_args_list[0][1]
            assert call_kwargs["model"] == "custom_engineer:8b"

    def test_extended_workspace_data_files(self, workspace_tmp):
        (workspace_tmp / "prices.xlsx").write_bytes(b"PK\x03\x04")
        (workspace_tmp / "data.csv").write_text("a,b,c", encoding="utf-8")
        (workspace_tmp / "app.db").write_bytes(b"SQLite format 3\x00")
        (workspace_tmp / "ignored.bin").write_bytes(b"\x00")

        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            '```json\n{"format": "cli", "intent": "maintenance", "target_artifact": "main.py", "verification_script": null, "plan": "Read data"}\n```',
            '<<TOOL:write_file filename="main.py">>\nprint("done")\n<</TOOL:write_file>>\n<<TOOL:run_script filename="main.py">>'
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp), \
             patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True)), \
             patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, output="done")):
            
            SubagentEngineer.run_worker(
                task_prompt="прочитай data.csv и prices.xlsx",
                lm_client=mock_client,
                model_id="test_model"
            )

            # Проверяем, что в системный промпт Архитектора попали файлы данных
            arch_messages = mock_client.chat_completion.call_args_list[0][1]["messages"]
            arch_system = arch_messages[0]["content"]
            assert "prices.xlsx" in arch_system
            assert "data.csv" in arch_system
            assert "app.db" in arch_system
            assert "ignored.bin" not in arch_system


class TestArchitectVerdictDispatchAndAuxiliaryProtection:
    """3. Сквозная обработка вердикта Архитектора, защита от перехвата и детерминированное завершение."""

    def test_verification_script_cleared_for_pygame_and_tkinter(self, workspace_tmp):
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            '```json\n{"format": "pygame", "intent": "new", "target_artifact": "game.py", "verification_script": "test_game.py", "plan": "2D game"}\n```',
            '<<TOOL:write_file filename="game.py">>\nimport pygame\n<</TOOL:write_file>>\n<<TOOL:run_script filename="game.py">>'
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp), \
             patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True)), \
             patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, output="[Smoke-тест GUI успешен]")):
            
            res = SubagentEngineer.run_worker(
                task_prompt="создай игру на pygame",
                lm_client=mock_client,
                model_id="test_model"
            )
            assert res["artifact_format"] == "pygame"
            assert res["verification_script"] is None

    def test_auxiliary_file_does_not_hijack_existing_target(self, workspace_tmp):
        # target_artifact (game.py) уже есть на диске
        (workspace_tmp / "game.py").write_text("import pygame\nprint('game')\n", encoding="utf-8")

        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            '```json\n{"format": "pygame", "intent": "maintenance", "target_artifact": "game.py", "verification_script": null, "plan": "Add player"}\n```',
            # Инженер создает вспомогательный модуль player.py и забыл тег run_script
            '<<TOOL:write_file filename="player.py">>\nclass Player:\n    pass\n<</TOOL:write_file>>'
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp), \
             patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True)), \
             patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, output="game ok")) as mock_run:
            
            SubagentEngineer.run_worker(
                task_prompt="добавь класс игрока в игру",
                lm_client=mock_client,
                model_id="test_model"
            )
            # script_to_run должен быть game.py (target_artifact), а не player.py!
            mock_run.assert_called_with(filename="game.py")

    def test_deterministic_completion_for_excel_document(self, workspace_tmp):
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            '```json\n{"format": "excel", "intent": "new", "target_artifact": "make_doc.py", "verification_script": null, "plan": "Excel generator"}\n```',
            '<<TOOL:write_file filename="make_doc.py">>\nimport openpyxl\n<</TOOL:write_file>>\n<<TOOL:run_script filename="make_doc.py">>'
        ]

        # Имитируем успешное создание документа
        doc_file = workspace_tmp / "budget.xlsx"
        doc_file.write_bytes(b"ExcelContentMockBytes")

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp), \
             patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True)), \
             patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, return_code=0, output="OUTPUT_FILE: budget.xlsx")):
            
            res = SubagentEngineer.run_worker(
                task_prompt="сделай таблицу бюджета",
                lm_client=mock_client,
                model_id="test_model"
            )
            assert res["success"] is True
            assert res["artifact_format"] == "excel"


class TestCodeEngineDefectsFixes:
    """4. Проверка исправления дефектов в CodeEngine (наследование и ключевые слова удаления)."""

    def test_oop_inheritance_avoids_missing_method(self):
        # Класс-потомок Sprite вызывает унаследованный метод kill(), которого нет в теле Player
        files = [
            (
                "player.py",
                "import pygame\nclass Player(pygame.sprite.Sprite):\n    def __init__(self):\n        super().__init__()\n"
            ),
            (
                "game.py",
                "from player import Player\np = Player()\np.kill()\n"
            )
        ]
        err = CodeValidator.check_cross_module_contracts(files)
        # Так как Player наследуется от pygame.sprite.Sprite, ошибка не должна генерироваться!
        assert err is None

    def test_intentional_deletion_bypasses_anti_truncation_guard(self):
        orig_code = "class OldFeature:\n    def run(self):\n        print('Hello World')\n" * 15
        new_code = "# OldFeature removed\n"

        # Без ключевого слова удаления при сжатии > 75% и объеме > 300 симв - блокируется аномальное сжатие
        valid, err = CodeValidator.validate_code_integrity(
            "module.py", orig_code, new_code,
            task_prompt="добавь логов",
            is_maintenance=True
        )
        assert valid is False
        assert "Аномальное сокращение объёма" in err

        # С ключевым словом удаления ("удали", "delete", "remove") - разрешается!
        valid_del, err_del = CodeValidator.validate_code_integrity(
            "module.py", orig_code, new_code,
            task_prompt="удали класс OldFeature",
            is_maintenance=True
        )
        assert valid_del is True
        assert err_del is None

        # Архитектурный рефакторинг (перенос функций в класс, удаление main) больше не блокируется
        proc_orig = "def old_func():\n    return 42\n\ndef main():\n    print(old_func())\n"
        oop_new = "class Game:\n    def run(self):\n        return 42\n"
        valid_refactor, err_refactor = CodeValidator.validate_code_integrity(
            "module.py", proc_orig, oop_new,
            task_prompt="сделай рефакторинг на ООП",
            is_maintenance=True
        )
        assert valid_refactor is True
        assert err_refactor is None


class TestSandboxTestFileExclusionFromGuiSmoke:
    """5. Проверка исключения тестов из GUI Smoke-теста."""

    def test_run_script_excludes_test_file_from_gui_smoke(self, workspace_tmp):
        test_file = workspace_tmp / "test_gui_module.py"
        test_file.write_text(
            "import unittest\nimport tkinter as tk\n"
            "class MyTest(unittest.TestCase):\n"
            "    def test_tk(self):\n"
            "        self.assertTrue(True)\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8"
        )

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            res = SandboxManager.run_script("test_gui_module.py")
            # Должен выполниться как обычный скрипт процесса, а не возвращать [Smoke-тест GUI успешен]
            assert res.success is True
            assert "[Smoke-тест GUI успешен]" not in res.stdout
            assert "OK" in res.stderr or "Ran 1 test" in res.stderr
