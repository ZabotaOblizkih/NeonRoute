"""
tests/test_architect_artifact_classification_and_freeze.py
Unit and integration tests for:
1. Architect Phase 1 artifact_type contract (document, gui_app, cli_tool, module).
2. Forcible resetting of verification_script to None for non-module artifact types.
3. _is_document_task heuristic without blocking on "скрипт", "код", "программу".
4. is_ephemeral synchronization with artifact_type == "document".
5. Verification Freeze turn 2 revision on contract mismatch (TypeError/AssertionError) and freeze at turn >= 3.
6. engineer_controller seamless dispatch for table/report tasks without intent overwriting.
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
from services.sandbox_manager import SandboxManager
from ui.controllers.engineer_controller import EngineerControllerMixin


@pytest.fixture
def workspace_tmp():
    ws_dir = SandboxManager.get_workspace_dir() / "_test_tmp_arch_freeze"
    ws_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield ws_dir
    finally:
        import shutil
        if ws_dir.exists():
            shutil.rmtree(ws_dir, ignore_errors=True)


class TestDocumentHeuristicAndEphemeralSync:
    """Проверка снятия конфликта эвристик ключевых слов с фазой Архитектора."""

    def test_document_task_not_blocked_by_script_code_program(self):
        """Если в запросе есть маркеры документа/таблицы, слова 'скрипт', 'код', 'программу' не блокируют детекцию."""
        assert SubagentEngineer._is_document_task("написать скрипт для сметы") is True
        assert SubagentEngineer._is_document_task("напиши код для выгрузки отчета в эксель") is True
        assert SubagentEngineer._is_document_task("создай программу для формирования таблицы .xlsx") is True
        assert SubagentEngineer._is_document_task("сделай скрипт генерации документа ворд") is True
        assert SubagentEngineer._is_document_task("напиши код на python для расчета сметы") is True

    def test_non_document_tasks_rejected(self):
        """Задачи без маркеров документов или чистые игры/GUI корректно отклоняются."""
        assert SubagentEngineer._is_document_task("напиши игру тетрис на pygame") is False
        assert SubagentEngineer._is_document_task("сделай gui калькулятор на tkinter") is False
        assert SubagentEngineer._is_document_task("создай cli утилиту для пинга серверов") is False
        assert SubagentEngineer._is_document_task("реализуй алгоритм дейкстры") is False

    def test_is_ephemeral_synchronized_with_document_artifact(self, workspace_tmp):
        """is_ephemeral строго синхронизирован с artifact_type == 'document'."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # Архитектор классифицирует как document
            '```json\n{"artifact_type": "document", "intent": "document", "target_artifact": "make_doc.py", "verification_script": null, "plan": "Создание сметы"}\n```',
            # Инженер
            '<<TOOL:write_file filename="make_doc.py">>\nimport openpyxl\nwb = openpyxl.Workbook()\nwb.save("smeta.xlsx")\n<</TOOL:write_file>>\n<<TOOL:run_script filename="make_doc.py">>'
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            with patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True)), \
                 patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, output="OUTPUT_FILE: smeta.xlsx")):
                res = SubagentEngineer.run_worker(
                    task_prompt="написать скрипт для сметы",
                    lm_client=mock_client,
                    model_id="test_model"
                )
                assert res["artifact_type"] == "document"
                assert res["verification_script"] is None


class TestArchitectArtifactClassificationAndForcedReset:
    """Проверка контракта Архитектора и принудительного сброса verification_script."""

    def test_gui_app_verification_script_forcibly_cleared(self, workspace_tmp):
        """Для gui_app скрипт верификации, даже если возвращен моделью, сбрасывается в None."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # Модель ошибочно возвращает verification_script для gui_app
            '```json\n{"artifact_type": "gui_app", "intent": "new", "target_artifact": "game.py", "verification_script": "test_game.py", "plan": "Игра змейка"}\n```',
            # Инженер создает игру с циклом событий и запускает smoke-тест
            '<<TOOL:write_file filename="game.py">>\nimport pygame\nrunning = True\nwhile running:\n    for event in pygame.event.get():\n        if event.type == pygame.QUIT: running = False\n<</TOOL:write_file>>\n<<TOOL:run_script filename="game.py">>'
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            with patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True)), \
                 patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, output="[Smoke-тест GUI успешен]")):
                res = SubagentEngineer.run_worker(
                    task_prompt="создай игру змейка на pygame",
                    lm_client=mock_client,
                    model_id="test_model"
                )
                assert res["artifact_type"] == "gui_app"
                assert res["verification_script"] is None
                assert res["target_artifact"] == "game.py"

    def test_cli_tool_verification_script_forcibly_cleared(self, workspace_tmp):
        """Для cli_tool скрипт верификации принудительно сбрасывается в None."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            '```json\n{"artifact_type": "cli_tool", "intent": "new", "target_artifact": "cli.py", "verification_script": "test_cli.py", "plan": "Консольная утилита"}\n```',
            '<<TOOL:write_file filename="cli.py">>\nprint("hello")\n<</TOOL:write_file>>\n<<TOOL:run_script filename="cli.py">>'
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            with patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True)), \
                 patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, output="hello")):
                res = SubagentEngineer.run_worker(
                    task_prompt="напиши консольный скрипт cli.py",
                    lm_client=mock_client,
                    model_id="test_model"
                )
                assert res["artifact_type"] == "cli_tool"
                assert res["verification_script"] is None

    def test_module_retains_verification_script(self, workspace_tmp):
        """Для backend-модулей verification_script сохраняется и выполняется."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            '```json\n{"artifact_type": "module", "intent": "new", "target_artifact": "algo.py", "verification_script": "test_algo.py", "plan": "Алгоритм"}\n```',
            '<<TOOL:write_file filename="algo.py">>\ndef calc(): return 42\n<</TOOL:write_file>>\n<<TOOL:write_file filename="test_algo.py">>\nfrom algo import calc\ndef test(): assert calc() == 42\n<</TOOL:write_file>>\n<<TOOL:run_script filename="test_algo.py">>'
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            with patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True)), \
                 patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, output="1 passed")):
                res = SubagentEngineer.run_worker(
                    task_prompt="создай модуль algo.py с тестами",
                    lm_client=mock_client,
                    model_id="test_model"
                )
                assert res["artifact_type"] == "module"
                assert res["verification_script"] == "test_algo.py"
                assert res["verification_passed"] is True


class TestVerificationFreezeTurnRevision:
    """Проверка механизма ревизии верификатора на ходе 2 и заморозки на ходе 3."""

    def test_turn2_revision_allowed_on_type_or_assertion_error(self, workspace_tmp):
        """Если на ходе 1 тест упал с TypeError/AssertionError, на ходе 2 ревизия test_*.py разрешена."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # 1. Архитектор
            '```json\n{"artifact_type": "module", "intent": "new", "target_artifact": "vault.py", "verification_script": "test_vault.py", "plan": "Хранилище"}\n```',
            # 2. Ход 1: тест вызывает vault(x) с неверной сигнатурой (TypeError)
            '<<TOOL:write_file filename="vault.py">>\ndef get_val(key, default=None):\n    return default\n<</TOOL:write_file>>\n'
            '<<TOOL:write_file filename="test_vault.py">>\nfrom vault import get_val\ndef test_sig():\n    get_val()\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="test_vault.py">>',
            # 3. RCA Архитектора
            'Несоответствие сигнатуры функции get_val. Исправь вызов в test_vault.py или сигнатуру в vault.py.',
            # 4. Ход 2: Инженер исправляет test_vault.py (ревизия разрешена!)
            '<<TOOL:write_file filename="test_vault.py">>\nfrom vault import get_val\ndef test_sig():\n    assert get_val("k", "v") == "v"\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="test_vault.py">>'
        ]

        test_file = workspace_tmp / "test_vault.py"
        test_file.write_text("# initial", encoding="utf-8")

        written_test_contents = []

        def fake_write_file(filename, content):
            if filename == "test_vault.py":
                written_test_contents.append(content)
            (workspace_tmp / filename).write_text(content, encoding="utf-8")
            return MagicMock(success=True, output="ok")

        runs_count = [0]
        def fake_run_script(filename):
            runs_count[0] += 1
            if runs_count[0] == 1:
                return MagicMock(
                    success=False,
                    return_code=1,
                    output='TypeError: get_val() missing 1 required positional argument: "key"',
                    error='TypeError: get_val() missing 1 required positional argument: "key"',
                    stdout=""
                )
            else:
                return MagicMock(
                    success=True,
                    return_code=0,
                    output="test_sig PASSED",
                    stdout="test_sig PASSED",
                    error=""
                )

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            with patch("services.tool_engine.ToolEngine.write_file", side_effect=fake_write_file), \
                 patch("services.tool_engine.ToolEngine.run_script", side_effect=fake_run_script):
                res = SubagentEngineer.run_worker(
                    task_prompt="создай vault.py и test_vault.py",
                    lm_client=mock_client,
                    model_id="test_model"
                )
                assert res["success"] is True
                assert res["verification_passed"] is True
                # Проверяем, что на ходе 2 запись test_vault.py не была заблокирована Verification Freeze
                assert len(written_test_contents) == 2
                assert 'assert get_val("k", "v") == "v"' in written_test_contents[1]

    def test_freeze_blocks_test_modification_on_turn_3(self, workspace_tmp):
        """Начиная с хода 3 любая попытка модификации проверочного скрипта намертво блокируется."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # 1. Архитектор
            '```json\n{"artifact_type": "module", "intent": "new", "target_artifact": "calc.py", "verification_script": "test_calc.py", "plan": "Калькулятор"}\n```',
            # 2. Ход 1: начальная реализация, тест падает
            '<<TOOL:write_file filename="calc.py">>\ndef add(a, b): return a + b\n<</TOOL:write_file>>\n'
            '<<TOOL:write_file filename="test_calc.py">>\nfrom calc import add\ndef test(): assert add(2, 2) == 5\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="test_calc.py">>',
            # 3. RCA 1 (ход 1 -> 2)
            'Тест ожидает 5 вместо 4.',
            # 4. Ход 2: ревизия теста (разрешена, так как упал с AssertionError на 1 ходе)
            '<<TOOL:write_file filename="test_calc.py">>\nfrom calc import add\ndef test(): assert add(2, 2) == 4\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="test_calc.py">>',
            # 5. RCA 2 (ход 2 -> 3)
            'Все еще сбой рантайма',
            # 6. Ход 3: попытка снова изменить test_calc.py (должна быть намертво заблокирована!)
            '<<TOOL:write_file filename="test_calc.py">>\ndef test(): pass\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="calc.py">>'
        ]

        test_file = workspace_tmp / "test_calc.py"
        test_file.write_text("# initial", encoding="utf-8")

        written_tests_on_turn = []

        def fake_write_file(filename, content):
            if filename == "test_calc.py":
                written_tests_on_turn.append(content)
            (workspace_tmp / filename).write_text(content, encoding="utf-8")
            return MagicMock(success=True, output="ok")

        run_turn = [0]
        def fake_run_script(filename):
            run_turn[0] += 1
            if run_turn[0] == 1:
                return MagicMock(success=False, output="AssertionError: assert 4 == 5", error="AssertionError", stdout="")
            elif run_turn[0] == 2:
                return MagicMock(success=False, output="RuntimeError: fake failure", error="RuntimeError", stdout="")
            else:
                return MagicMock(success=True, output="calc.py ok", stdout="calc.py ok", error="")

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            with patch("services.tool_engine.ToolEngine.write_file", side_effect=fake_write_file), \
                 patch("services.tool_engine.ToolEngine.run_script", side_effect=fake_run_script):
                SubagentEngineer.run_worker(
                    task_prompt="создай calc.py и test_calc.py",
                    lm_client=mock_client,
                    model_id="test_model"
                )
                # На ходе 1: 1-я запись, на ходе 2: 2-я запись (разрешенная ревизия).
                # На ходе 3: запись test_calc.py ("def test(): pass") заблокирована!
                assert len(written_tests_on_turn) == 2
                assert "def test(): pass" not in [c.strip() for c in written_tests_on_turn]


class DummyMainWindow(EngineerControllerMixin):
    def __init__(self, ws_dir):
        self.current_session = {"id": "test_sess_001", "messages": []}
        self.current_mode = "assistant"
        self._active_tasks = {}
        self.task_queue = MagicMock()
        self.lm_client = MagicMock()
        self._status_msgs = []
        self._ws_dir = ws_dir

    def statusBar(self):
        m = MagicMock()
        m.showMessage = lambda msg, *args: self._status_msgs.append(msg)
        return m


class TestEngineerControllerSeamlessDispatch:
    """Проверка бесшовного проброса задач таблиц и отчетов в контроллере."""

    def test_is_ephemeral_doc_request_matches_table_and_report_keywords(self):
        controller = DummyMainWindow(Path("."))
        assert controller._is_ephemeral_doc_request("написать скрипт для сметы") is True
        assert controller._is_ephemeral_doc_request("создай таблицу расходов в excel") is True
        assert controller._is_ephemeral_doc_request("сформируй отчет по продажам") is True
        assert controller._is_ephemeral_doc_request("сделай файл document.docx") is True

    def test_dispatch_ephemeral_doc_worker_does_not_hijack_to_analysis_or_debug(self, workspace_tmp):
        """Запросы на генерацию таблиц/отчетов не сваливаются в read-only is_analysis и не путаются с отладкой."""
        controller = DummyMainWindow(workspace_tmp)

        # Создадим в воркспейсе существующий python скрипт, чтобы проверить что он не перехватит намерение
        (workspace_tmp / "main.py").write_text("print('old script')", encoding="utf-8")

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=workspace_tmp):
            ok = controller._dispatch_ephemeral_doc_worker(
                task_prompt="Проанализируй данные и сделай отчет по продажам в таблице excel"
            )
            assert ok is True
            enqueued_task = list(controller._active_tasks.values())[0]

            # Интенция не должна быть переопределена в read-only analysis
            assert enqueued_task.kwargs["is_analysis"] is False
            assert "Таблиц" in enqueued_task.name or "таблицы" in enqueued_task.name or "отчет" in enqueued_task.name.lower()
            # context_hint не должен перезаписываться существующим "main.py"
            assert enqueued_task.kwargs["hint"] != "main.py"
