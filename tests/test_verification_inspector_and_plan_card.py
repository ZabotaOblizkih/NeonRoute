import pytest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication
from ui.components.workspace_inspector import VerificationStatusWidget, WorkspaceInspectorWidget, CodeEditorViewer
from services.engineer_planner import ArchitectPlan, EngineerPlanner
from services.subagent_engineer import SubagentEngineer, CodeStreamExtractor, extract_clean_code
from services.sandbox_manager import SandboxManager

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_verification_status_widget_lifecycle(qapp):
    widget = VerificationStatusWidget()
    assert widget.badge_status.text() == "Ожидание"
    assert "готов к работе" in widget.lbl_title.text()
    assert "Тесты не запускались" in widget.lbl_details.text()

    # set_running
    widget.set_running("Запуск тестов...")
    assert widget.badge_status.text() == "ТЕСТ"
    assert "Проверка кода" in widget.lbl_title.text()
    assert widget.lbl_details.text() == "Запуск тестов..."

    # set_success
    widget.set_success("Все тесты пройдены")
    assert widget.badge_status.text() == "УСПЕХ"
    assert "Код стабилен" in widget.lbl_title.text()
    assert widget.lbl_details.text() == "Все тесты пройдены"

    # set_error
    widget.set_error("SyntaxError", "invalid syntax at line 42", line=42)
    assert widget.badge_status.text() == "СБОЙ"
    assert "[Строка 42]" in widget.lbl_title.text()
    assert "SyntaxError" in widget.lbl_title.text()
    assert "invalid syntax" in widget.lbl_details.text()

    # reset_status
    widget.reset_status()
    assert widget.badge_status.text() == "Ожидание"
    assert "готов к работе" in widget.lbl_title.text()


def test_workspace_inspector_has_verification_panel(qapp, tmp_path):
    with patch.object(SandboxManager, "get_workspace_dir", return_value=tmp_path):
        inspector = WorkspaceInspectorWidget()
        assert hasattr(inspector, "verification_panel")
        assert isinstance(inspector.verification_panel, VerificationStatusWidget)

        # mark_running
        inspector.mark_running("Проверка...")
        assert inspector.verification_panel.badge_status.text() == "ТЕСТ"

        # mark_success
        inspector.mark_success()
        assert inspector.verification_panel.badge_status.text() == "УСПЕХ"
        assert inspector.editor.error_lineno is None

        # show_error
        test_file = tmp_path / "test_app.py"
        test_file.write_text("print('hello')\nerror_here\n", encoding="utf-8")
        inspector.show_error("test_app.py", 2, "NameError: name 'error_here' is not defined")
        assert inspector.verification_panel.badge_status.text() == "СБОЙ"
        assert "[Строка 2]" in inspector.verification_panel.lbl_title.text()
        assert inspector.editor.error_lineno == 2


def test_code_editor_streaming_mode(qapp):
    editor = CodeEditorViewer()
    editor.start_streaming_mode("game.py")
    assert editor.isReadOnly() is True
    assert editor.toPlainText() == ""

    editor.append_stream_chunk("import pygame\n")
    editor.append_stream_chunk("pygame.init()\n")
    assert "import pygame\npygame.init()\n" in editor.toPlainText()

    clean_final = "import pygame\npygame.init()\nprint('Running')\n"
    editor.finish_streaming_mode(clean_final)
    assert editor.toPlainText() == clean_final
    assert editor.isReadOnly() is False


def test_workspace_inspector_streaming_signals(qapp, tmp_path):
    with patch.object(SandboxManager, "get_workspace_dir", return_value=tmp_path):
        inspector = WorkspaceInspectorWidget()
        assert hasattr(inspector, "sig_stream_started")
        assert hasattr(inspector, "sig_stream_chunk")
        assert hasattr(inspector, "sig_stream_finished")

        inspector.sig_stream_started.emit("game.py")
        assert "game.py" in inspector.lbl_current_file.text()
        assert "генерация..." in inspector.lbl_current_file.text()
        assert inspector.verification_panel.badge_status.text() == "ТЕСТ"

        inspector.sig_stream_chunk.emit("import math\n")
        assert "import math" in inspector.editor.toPlainText()

        inspector.sig_stream_finished.emit("game.py", "import math\nprint(math.pi)\n")
        assert inspector.editor.toPlainText() == "import math\nprint(math.pi)\n"
        assert inspector.lbl_current_file.text() == "📄 game.py"


def test_code_stream_extractor():
    chunks_received = []
    extractor = CodeStreamExtractor(on_code_chunk_callback=lambda ch: chunks_received.append(ch))

    # Send intro text
    extractor.feed("Конечно! Вот код программы:\n\n```python\n")
    assert chunks_received == []  # Header only, no code yet

    # Send code chunks
    extractor.feed("import pygame\n")
    extractor.feed("import sys\n")
    extractor.feed("pygame.init()\n```\nНадеюсь, код полезен!")
    extractor.finalize()

    collected_code = "".join(chunks_received)
    assert "Конечно!" not in collected_code
    assert "```" not in collected_code
    assert "Надеюсь" not in collected_code
    assert "import pygame\nimport sys\npygame.init()\n" in collected_code


def test_architect_plan_intent():
    plan = ArchitectPlan(
        artifact_format="pygame",
        target_artifact="game.py",
        plan_desc="Создать игру Тетрис",
        intent="new",
        plan_content="Создать тетрис на pygame"
    )
    assert plan.intent == "new"
    assert plan.artifact_format == "pygame"
    assert plan.target_artifact == "game.py"


def test_clean_plan_card_formatting_and_isolation(qapp, tmp_path):
    plan_mock = ArchitectPlan(
        artifact_format="pygame",
        target_artifact="game.py",
        intent="new",
        plan_content="Реализовать игровой цикл с обработкой клавиш"
    )

    with patch.object(EngineerPlanner, "plan_architecture", return_value=plan_mock), \
         patch.object(SandboxManager, "get_workspace_dir", return_value=tmp_path):

        received_messages = []
        status_updates = []

        inspector = WorkspaceInspectorWidget()

        class StreamingMockLMClient:
            def chat_completion(self, messages, chunk_callback=None, **kwargs):
                chunks = [
                    "Привет! Вот скрипт:\n```python\n",
                    "import pygame\n",
                    "pygame.init()\n",
                    "```\nГотово!"
                ]
                if chunk_callback:
                    for ch in chunks:
                        chunk_callback(ch)
                return "".join(chunks)

        mock_client = StreamingMockLMClient()

        def msg_cb(*args):
            received_messages.append(args[-1])

        def status_cb(status, fn, line, msg):
            status_updates.append((status, fn, line, msg))

        # Run execute_worker directly
        worker = SubagentEngineer()
        with patch.object(SubagentEngineer, "_rollback_workspace"):
            with patch("services.tool_engine.ToolEngine.run_script") as mock_run:
                mock_res = MagicMock()
                mock_res.success = True
                mock_res.exit_code = 0
                mock_res.stdout = "Game initialized"
                mock_res.error = ""
                mock_res.output = "Game initialized"
                mock_run.return_value = mock_res

                res = worker.execute_worker(
                    task_prompt="Создай игру",
                    lm_client=mock_client,
                    on_message_callback=msg_cb,
                    on_status_callback=status_cb,
                    inspector=inspector
                )

        assert len(received_messages) >= 2
        plan_card = received_messages[0]
        # Verify card structure
        assert "### 📐 Архитектурный план: `game.py`" in plan_card
        assert "**Формат:** 🎮 Pygame" in plan_card
        assert "**Действие:** `new`" in plan_card
        assert "> **Техническое задание:**" in plan_card
        assert "Реализовать игровой цикл с обработкой клавиш" in plan_card
        assert "⏳ *Приступаю к автономной реализации кода...*" in plan_card

        # Verify raw JSON is isolated and NOT in the clean plan card
        assert "```json" not in plan_card

        # Verify status messages in chat contain clean progress statuses without source code spam
        for msg in received_messages:
            assert "import pygame" not in msg

        # Verify streaming populated the inspector editor!
        assert "import pygame" in inspector.editor.toPlainText()
        assert inspector.editor.isReadOnly() is False

        # Verify status updates were called (running then success)
        assert any(s[0] == "running" for s in status_updates)
        assert any(s[0] == "success" for s in status_updates)

def test_sandbox_manager_git_diff_and_commit(tmp_path):
    import subprocess
    # Configure git in tmp_path
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(tmp_path), capture_output=True, check=True)

    test_file = tmp_path / "app.py"
    test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(tmp_path), capture_output=True, check=True)

    # Modify line 2 and 3
    test_file.write_text("line 1\nmodified 2\nmodified 3\nline 4\nline 5\n", encoding="utf-8")

    # Check line ranges
    ranges = SandboxManager.get_changed_line_ranges(tmp_path, "app.py")
    assert "2" in ranges and "3" in ranges and "3" in ranges

    # Commit manual edit
    ok = SandboxManager.commit_manual_edit(tmp_path, "app.py", "fix line 2 and 3", ranges)
    assert ok is True

    # Verify git log
    log = subprocess.run(["git", "log", "-1", "--pretty=%B"], cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert f"manual: fix line 2 and 3 (app.py, {ranges})" in log


def test_sandbox_manager_verify_single_file(tmp_path):
    # 1. Valid file
    valid_file = tmp_path / "valid.py"
    valid_file.write_text("print('All good')\n", encoding="utf-8")
    ok, err_type, err_msg, err_line = SandboxManager.verify_single_file(tmp_path, "valid.py")
    assert ok is True
    assert err_line is None

    # 2. Syntax error
    bad_syntax = tmp_path / "syntax_err.py"
    bad_syntax.write_text("def broken(:\n    pass\n", encoding="utf-8")
    ok, err_type, err_msg, err_line = SandboxManager.verify_single_file(tmp_path, "syntax_err.py")
    assert ok is False
    assert err_type == "SyntaxError"
    assert err_line == 1

    # 3. Runtime error
    bad_runtime = tmp_path / "runtime_err.py"
    bad_runtime.write_text("x = 10\nraise ValueError('Custom crash')\n", encoding="utf-8")
    ok, err_type, err_msg, err_line = SandboxManager.verify_single_file(tmp_path, "runtime_err.py")
    assert ok is False
    assert err_type == "ValueError"
    assert err_line == 2


def test_code_editor_viewer_shortcuts(qapp):
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import Qt

    editor = CodeEditorViewer()
    assert editor.isReadOnly() is False

    saved = []
    tested = []
    editor.sig_save_requested.connect(lambda: saved.append(True))
    editor.sig_test_requested.connect(lambda: tested.append(True))

    # Ctrl+S
    ev_save = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    editor.keyPressEvent(ev_save)
    assert len(saved) == 1

    # F5
    ev_f5 = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier)
    editor.keyPressEvent(ev_f5)
    assert len(tested) == 1

    # Ctrl+Enter
    ev_ctrl_enter = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    editor.keyPressEvent(ev_ctrl_enter)
    assert len(tested) == 2


def test_workspace_inspector_manual_editing_and_testing(qapp, tmp_path):
    import subprocess
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(tmp_path), capture_output=True, check=True)

    test_file = tmp_path / "main_test.py"
    test_file.write_text("print('initial')\n", encoding="utf-8")
    subprocess.run(["git", "add", "main_test.py"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True, check=True)

    with patch.object(SandboxManager, "get_workspace_dir", return_value=tmp_path):
        inspector = WorkspaceInspectorWidget()
        assert hasattr(inspector, "btn_save")
        assert hasattr(inspector, "btn_test")
        assert "Ctrl+S" in inspector.btn_save.toolTip()
        assert "F5" in inspector.btn_test.toolTip()

        inspector.open_file(test_file)
        assert inspector.is_dirty is False
        assert inspector.current_file_path == test_file
        assert "main_test.py" in inspector.lbl_current_file.text()
        assert "*" not in inspector.lbl_current_file.text()

        # Modify text in editor
        inspector.editor.setPlainText("print('initial')\nprint('modified line')\n")
        assert inspector.is_dirty is True
        assert "*" in inspector.lbl_current_file.text()

        # Save manual edits
        from ui.components.workspace_inspector import InputDialog
        with patch.object(InputDialog, "exec", return_value=True),              patch.object(InputDialog, "text", return_value="added line"):
            inspector.save_manual_edits()

        assert inspector.is_dirty is False
        assert "*" not in inspector.lbl_current_file.text()
        assert "print('modified line')" in test_file.read_text(encoding="utf-8")
        assert "Сохранено:" in inspector.verification_panel.lbl_details.text()

        # Run manual test on valid code
        inspector.run_manual_test()
        assert inspector.verification_panel.badge_status.text() == "УСПЕХ"
        assert inspector.editor.error_lineno is None

        # Modify with syntax error and run test
        inspector.editor.setPlainText("def broken_syntax(\n")
        with patch.object(InputDialog, "exec", return_value=False):
            inspector.run_manual_test()

        assert inspector.verification_panel.badge_status.text() == "СБОЙ"
        assert inspector.editor.error_lineno == 1

