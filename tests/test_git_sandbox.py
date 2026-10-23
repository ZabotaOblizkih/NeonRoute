"""
tests/test_git_sandbox.py — Тесты интеграции локального Git в песочницу и калибровки контекста.
"""

import sys
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent
while PROJECT_ROOT.name and not (PROJECT_ROOT / "services").exists() and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.git_sandbox import GitSandboxManager
from services.subagent_engineer import SubagentEngineer
from services.sandbox_manager import SandboxManager


@pytest.fixture
def temp_ws(tmp_path):
    ws = tmp_path / "test_workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


class TestGitSandboxManagerUnit:
    """Модульные тесты для защищённого GitSandboxManager."""

    def test_is_git_available_false_when_no_git(self):
        """Если git отсутствует в системе, возвращается False."""
        with patch.object(GitSandboxManager, "get_git_executable", return_value=None):
            assert GitSandboxManager.is_git_available() is False

    def test_is_git_available_true_when_git_exists(self):
        """Если git найден, возвращается True."""
        with patch.object(GitSandboxManager, "get_git_executable", return_value="/usr/bin/git"):
            assert GitSandboxManager.is_git_available() is True

    def test_init_repo_creates_gitignore_and_zip_fallback_when_no_git(self, temp_ws):
        """При отсутствии git init_repo переключается в режим ZIP и возвращает True."""
        with patch.object(GitSandboxManager, "is_git_available", return_value=False):
            res = GitSandboxManager.init_repo(temp_ws)
            assert res is True
            assert (temp_ws / ".backups").is_dir()
            assert (temp_ws / ".gitignore").is_file()
            gitignore_text = (temp_ws / ".gitignore").read_text(encoding="utf-8")
            assert ".neonroute_last_failure.json" in gitignore_text
            assert ".backups/" in gitignore_text

    def test_init_repo_executes_git_commands_when_git_available(self, temp_ws):
        """init_repo вызывает git init, user.name, user.email, core.longpaths и baseline commit."""
        with patch.object(GitSandboxManager, "is_git_available", return_value=True), \
             patch.object(GitSandboxManager, "_run_git", return_value=(True, "")) as mock_run:

            res = GitSandboxManager.init_repo(temp_ws)
            assert res is True
            assert mock_run.call_count >= 5
            called_args = [c[0][1] for c in mock_run.call_args_list]
            assert any("init" in a for a in called_args)
            assert any("user.name" in a for a in called_args)
            assert any("user.email" in a for a in called_args)
            assert any("core.longpaths" in a for a in called_args)

    def test_checkpoint_git_mode(self, temp_ws):
        """checkpoint вызывает git add -A и git commit."""
        with patch.object(GitSandboxManager, "is_git_available", return_value=True), \
             patch.object(GitSandboxManager, "_run_git", return_value=(True, "")) as mock_run:

            res = GitSandboxManager.checkpoint(temp_ws, "turn_1")
            assert res is True
            assert mock_run.call_count == 2
            called_args = [c[0][1] for c in mock_run.call_args_list]
            assert called_args[0] == ["add", "-A"]
            assert called_args[1] == ["commit", "-m", "turn_1"]

    def test_checkpoint_zip_fallback(self, temp_ws):
        """checkpoint при отсутствии git сохраняет ZIP-снапшот."""
        test_file = temp_ws / "code.py"
        test_file.write_text("print('hello')", encoding="utf-8")

        with patch.object(GitSandboxManager, "is_git_available", return_value=False):
            res = GitSandboxManager.checkpoint(temp_ws, "turn_1")
            assert res is True
            zip_snap = temp_ws / ".backups" / "latest_checkpoint.zip"
            assert zip_snap.is_file()

    def test_rollback_git_mode(self, temp_ws):
        """rollback выполняет git reset --hard HEAD и git clean -fd."""
        with patch.object(GitSandboxManager, "is_git_available", return_value=True), \
             patch.object(GitSandboxManager, "_run_git", return_value=(True, "")) as mock_run:

            res = GitSandboxManager.rollback(temp_ws)
            assert res is True
            assert mock_run.call_count == 2
            called_args = [c[0][1] for c in mock_run.call_args_list]
            assert called_args[0] == ["reset", "--hard", "HEAD"]
            assert called_args[1] == ["clean", "-fd"]

    def test_rollback_zip_fallback(self, temp_ws):
        """rollback при отсутствии git восстанавливает файлы из ZIP-снапшота."""
        target = temp_ws / "app.py"
        target.write_text("STABLE_CODE", encoding="utf-8")

        with patch.object(GitSandboxManager, "is_git_available", return_value=False):
            GitSandboxManager.checkpoint(temp_ws, "stable")
            target.write_text("BROKEN_CODE", encoding="utf-8")
            res = GitSandboxManager.rollback(temp_ws)
            assert res is True
            assert target.read_text(encoding="utf-8") == "STABLE_CODE"

    def test_record_and_pop_failure(self, temp_ws):
        """record_failure сохраняет JSON журнала сбоя, а pop_last_failure атомарно его забирает."""
        GitSandboxManager.record_failure(
            ws_dir=temp_ws,
            task_title="Тестовая задача",
            turn=3,
            error_msg="NameError: name 'x' is not defined",
            recipe="Объяви переменную x"
        )
        fail_file = temp_ws / ".neonroute_last_failure.json"
        assert fail_file.is_file()

        info = GitSandboxManager.pop_last_failure(temp_ws)
        assert info is not None
        assert info["task"] == "Тестовая задача"
        assert info["failed_turn"] == 3
        assert "NameError" in info["error"]
        assert info["recipe"] == "Объяви переменную x"

        assert not fail_file.exists()
        assert GitSandboxManager.pop_last_failure(temp_ws) is None

    def test_create_backup_bundle_zip_fallback(self, temp_ws):
        """create_backup_bundle при отсутствии git создаёт zip-архив в backups."""
        (temp_ws / "file.txt").write_text("data", encoding="utf-8")
        with patch.object(GitSandboxManager, "is_git_available", return_value=False):
            b_path = GitSandboxManager.create_backup_bundle(temp_ws, "my_backup")
            assert b_path is not None
            assert b_path.exists()
            assert b_path.suffix == ".zip"

    def test_cleanup_stale_lock(self, temp_ws):
        """_cleanup_stale_lock корректно удаляет зависший index.lock."""
        git_dir = temp_ws / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        lock_file = git_dir / "index.lock"
        lock_file.write_text("locked", encoding="utf-8")
        assert lock_file.exists()

        GitSandboxManager._cleanup_stale_lock(temp_ws)
        assert not lock_file.exists()


class TestSubagentEngineerGitIntegration:
    """Интеграционные тесты вызовов GitSandboxManager в subagent_engineer.py."""

    def test_init_repo_called_on_execute_worker_start(self, temp_ws):
        """При старте execute_worker репозиторий инициализируется через GitSandboxManager.init_repo(ws_dir)."""
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = '```json\n{"intent": "new", "target_artifact": "app.py", "plan": "plan"}\n```'

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=temp_ws), \
             patch("services.git_sandbox.GitSandboxManager.init_repo") as mock_init, \
             patch("services.git_sandbox.GitSandboxManager.create_checkpoint"):
            engineer = SubagentEngineer()
            engineer.execute_worker(
                task_prompt="создай скрипт app.py",
                lm_client=mock_client,
                model_id="test_model"
            )
            mock_init.assert_called_once_with(temp_ws)

    def test_create_checkpoint_called_before_each_turn(self, temp_ws):
        """Перед ходом создается чекпоинт: GitSandboxManager.create_checkpoint(ws_dir, f'turn_{turn}')."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # 1. Архитектор:
            '```json\n{"intent": "new", "target_artifact": "app.py", "verification_script": null, "plan": "plan"}\n```',
            # 2. Ход 1 Инженера:
            '<<TOOL:write_file filename="app.py">>\ndef main():\n    print("Running app")\nif __name__ == "__main__": main()\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="app.py">>'
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=temp_ws), \
             patch("services.git_sandbox.GitSandboxManager.init_repo"), \
             patch("services.git_sandbox.GitSandboxManager.create_checkpoint") as mock_cp, \
             patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True, output="ok")), \
             patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, return_code=0, output="ok", stdout="ok", stderr="")):

            engineer = SubagentEngineer()
            res = engineer.execute_worker(
                task_prompt="создай скрипт app.py",
                lm_client=mock_client,
                model_id="test_model"
            )

            assert res["success"] is True
            mock_cp.assert_any_call(temp_ws, "turn_1")

    def test_rollback_on_syntax_error_when_git_available(self, temp_ws):
        """При синтаксической ошибке и доступном Git вызывается GitSandboxManager.rollback."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # 1. Архитектор:
            '```json\n{"intent": "new", "target_artifact": "app.py", "verification_script": null, "plan": "plan"}\n```',
            # 2. Ход 1 Инженера с битым синтаксисом:
            '<<TOOL:write_file filename="app.py">>\ndef broken(:\n<</TOOL:write_file>>',
            # 3. Ход 2: правильный код
            '<<TOOL:write_file filename="app.py">>\ndef main():\n    print("Running app")\nif __name__ == "__main__": main()\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="app.py">>'
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=temp_ws), \
             patch("services.git_sandbox.GitSandboxManager.is_git_available", return_value=True), \
             patch("services.git_sandbox.GitSandboxManager.init_repo"), \
             patch("services.git_sandbox.GitSandboxManager.create_checkpoint"), \
             patch("services.git_sandbox.GitSandboxManager.rollback") as mock_rb, \
             patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True, output="ok")), \
             patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, return_code=0, output="ok", stdout="ok", stderr="")):

            engineer = SubagentEngineer()
            res = engineer.execute_worker(
                task_prompt="создай скрипт app.py",
                lm_client=mock_client,
                model_id="test_model"
            )

            assert res["success"] is True
            assert mock_rb.called
            mock_rb.assert_called_with(temp_ws)

    def test_rollback_on_patch_failure_when_git_available(self, temp_ws):
        """При ошибке применения patch_file вызывается GitSandboxManager.rollback(ws_dir)."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            # 1. Архитектор:
            '```json\n{"intent": "maintenance", "target_artifact": "app.py", "verification_script": null, "plan": "plan"}\n```',
            # 2. Ход 1: неудачный патч
            '<<TOOL:patch_file filename="app.py">>\n<<<<<<< SEARCH\nmissing\n=======\nreplacement\n>>>>>>> REPLACE\n<</TOOL:patch_file>>',
            # 3. Ход 2: рабочий write_file
            '<<TOOL:write_file filename="app.py">>\ndef old():\n    print("old")\ndef main():\n    print("Running app")\nif __name__ == "__main__": main()\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="app.py">>'
        ]

        (temp_ws / "app.py").write_text("def old():\n    print('old')\n", encoding="utf-8")

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=temp_ws), \
             patch("services.git_sandbox.GitSandboxManager.is_git_available", return_value=True), \
             patch("services.git_sandbox.GitSandboxManager.init_repo"), \
             patch("services.git_sandbox.GitSandboxManager.create_checkpoint"), \
             patch("services.git_sandbox.GitSandboxManager.rollback") as mock_rb, \
             patch("services.tool_engine.ToolEngine.patch_file", return_value=MagicMock(success=False, output="Hunk failed")), \
             patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True, output="ok")), \
             patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, return_code=0, output="ok", stdout="ok", stderr="")):

            engineer = SubagentEngineer()
            res = engineer.execute_worker(
                task_prompt="обнови app.py",
                lm_client=mock_client,
                model_id="test_model"
            )

            assert res["success"] is True
            assert mock_rb.called

    def test_fallback_file_backups_when_git_unavailable(self, temp_ws):
        """Если Git недоступен, откат выполняется через file_backups."""
        orig_file = temp_ws / "app.py"
        orig_file.write_text("ORIGINAL_CONTENT\n", encoding="utf-8")

        file_backups = {"app.py": "ORIGINAL_CONTENT\n"}

        # Имитируем повреждение файла
        orig_file.write_text("CORRUPTED_CONTENT\n", encoding="utf-8")

        with patch.object(GitSandboxManager, "is_git_available", return_value=False):
            SubagentEngineer._rollback_workspace(temp_ws, file_backups)

        # Файл должен быть восстановлен из backups
        assert orig_file.read_text(encoding="utf-8") == "ORIGINAL_CONTENT\n"


class TestContextCalibration:
    """Тестирование калибровки передачи контекста файлов под 7B модели."""

    def test_file_under_or_equal_600_lines_passed_fully(self, temp_ws):
        """Файлы <= 600 строк передаются целиком."""
        py_file = temp_ws / "small_module.py"
        content = "\n".join([f"# line {i}" for i in range(1, 601)])
        py_file.write_text(content, encoding="utf-8")

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=temp_ws):
            res = SubagentEngineer._inspect_workspace_file(
                task_prompt="доработай small_module.py",
                target_file="small_module.py"
            )

            assert res is not None
            assert "[ТЕКУЩАЯ ВЕРСИЯ ФАЙЛА НА ДИСКЕ: small_module.py (600 строк)]" in res
            assert "# line 1" in res
            assert "# line 600" in res

    def test_file_over_600_lines_passed_as_ast_skeleton(self, temp_ws):
        """Файлы > 600 строк передаются строго через компактный AST-скелет."""
        py_file = temp_ws / "large_module.py"
        lines = [
            "class LargeProcessor:",
            "    def process(self, data):",
            "        x = 1"
        ]
        lines.extend([f"        x += {i}" for i in range(598)])
        lines.append("        return x")
        py_file.write_text("\n".join(lines), encoding="utf-8")

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=temp_ws):
            res = SubagentEngineer._inspect_workspace_file(
                task_prompt="доработай large_module.py",
                target_file="large_module.py"
            )

            assert res is not None
            assert "[АРХИТЕКТУРНЫЙ СКЕЛЕТ ФАЙЛА: large_module.py" in res
            assert "class LargeProcessor:" in res
            assert "x += 100" not in res


class TestToolEngineGitCheckpoint:
    """Тестирование инструмента git_checkpoint в ToolEngine."""

    def test_execute_git_checkpoint_invokes_manager(self, temp_ws):
        from services.tool_engine import ToolEngine, ToolCall
        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=temp_ws), \
             patch.object(GitSandboxManager, "checkpoint", return_value=True) as mock_cp, \
             patch.object(GitSandboxManager, "create_backup_bundle", return_value=temp_ws / "backup.bundle"):

            res_str = ToolEngine.execute_git_checkpoint("Test message", make_backup=True)
            assert "зафиксирован в Git" in res_str
            assert "Локальный офлайн-бэкап сохранён" in res_str
            mock_cp.assert_called_once_with(temp_ws, "Test message")

    def test_execute_tool_call_git_checkpoint(self, temp_ws):
        from services.tool_engine import ToolEngine, ToolCall
        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=temp_ws), \
             patch.object(GitSandboxManager, "checkpoint", return_value=True), \
             patch.object(GitSandboxManager, "create_backup_bundle", return_value=temp_ws / "backup.bundle"):

            call = ToolCall(name="git_checkpoint", params={"message": "manual checkpoint", "backup": "true"})
            res = ToolEngine.execute_tool_call(call)
            assert res.success is True
            assert "manual checkpoint" in res.output

    def test_subagent_engineer_handles_git_checkpoint_only(self, temp_ws):
        """Если модель вернула только git_checkpoint без файлов, возвращается успех."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            '```json\n{"intent": "new", "target_artifact": "app.py", "plan": "plan"}\n```',
            '<<TOOL:git_checkpoint message="Checkpoint after milestone">>Хорошо, зафиксировал состояние.'
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=temp_ws), \
             patch("services.git_sandbox.GitSandboxManager.init_repo"), \
             patch("services.git_sandbox.GitSandboxManager.create_checkpoint"), \
             patch("services.git_sandbox.GitSandboxManager.checkpoint", return_value=True):

            engineer = SubagentEngineer()
            res = engineer.execute_worker(
                task_prompt="сделай чекпоинт",
                lm_client=mock_client,
                model_id="test_model"
            )

            assert res["success"] is True
            assert "Чекпоинт успешно зафиксирован" in res["summary"]

    def test_checkpoint_on_successful_target_artifact(self, temp_ws):
        """При успешном выполнении целевого артефакта вызывается checkpoint."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            '```json\n{"intent": "new", "target_artifact": "app.py", "verification_script": null, "plan": "plan"}\n```',
            '<<TOOL:write_file filename="app.py">>\ndef main():\n    print("ok")\nif __name__ == "__main__": main()\n<</TOOL:write_file>>\n'
            '<<TOOL:run_script filename="app.py">>'
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=temp_ws), \
             patch("services.git_sandbox.GitSandboxManager.init_repo"), \
             patch("services.git_sandbox.GitSandboxManager.create_checkpoint"), \
             patch("services.git_sandbox.GitSandboxManager.checkpoint") as mock_cp, \
             patch("services.tool_engine.ToolEngine.write_file", return_value=MagicMock(success=True, output="ok")), \
             patch("services.tool_engine.ToolEngine.run_script", return_value=MagicMock(success=True, return_code=0, output="ok", stdout="ok", stderr="")):

            engineer = SubagentEngineer()
            res = engineer.execute_worker(
                task_prompt="создай app.py",
                lm_client=mock_client,
                model_id="test_model"
            )

            assert res["success"] is True
            mock_cp.assert_any_call(temp_ws, "Turn 1: verified step for app.py")

    def test_record_failure_on_loop_detected(self, temp_ws):
        """При зацикливании ошибки вызывается GitSandboxManager.record_failure."""
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = [
            '```json\n{"intent": "new", "target_artifact": "app.py", "plan": "plan"}\n```',
            '<<TOOL:write_file filename="app.py">>\ndef broken(:\n<</TOOL:write_file>>',
            '<<TOOL:write_file filename="app.py">>\ndef broken(:\n<</TOOL:write_file>>',
            '<<TOOL:write_file filename="app.py">>\ndef broken(:\n<</TOOL:write_file>>',
        ]

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=temp_ws), \
             patch("services.git_sandbox.GitSandboxManager.init_repo"), \
             patch("services.git_sandbox.GitSandboxManager.create_checkpoint"), \
             patch("services.git_sandbox.GitSandboxManager.record_failure") as mock_rf, \
             patch("services.subagent_engineer.SubagentEngineer._rollback_workspace"):

            engineer = SubagentEngineer()
            res = engineer.execute_worker(
                task_prompt="создай app.py",
                lm_client=mock_client,
                model_id="test_model"
            )

            assert res["success"] is False
            assert mock_rf.called
            assert mock_rf.call_args[1]["ws_dir"] == temp_ws
            assert "SyntaxError" in mock_rf.call_args[1]["error_msg"] or "invalid syntax" in mock_rf.call_args[1]["error_msg"]
