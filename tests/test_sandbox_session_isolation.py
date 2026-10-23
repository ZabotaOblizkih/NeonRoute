"""
tests/test_sandbox_session_isolation.py
Регрессионные и интеграционные тесты для верификации изоляции сессий:
1. Корректность входа/выхода session_scope.
2. Гарантированный откат сессии при падении внутри блока (Exception Safety).
3. Поддержка вложенных сессий (LIFO стек восстановления).
4. Защита от мутации состояния UI-потока при диспатче задач контроллером.
5. Автоматическая очистка сессии воркером при штатном и аварийном завершении.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Подключение корня проекта
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.sandbox_manager import SandboxManager, WORKSPACE_DIR
from ui.controllers.engineer_controller import EngineerControllerMixin


class DummyMainWindow(EngineerControllerMixin):
    """Тестовая заглушка главного окна для проверки диспатчинга контроллера."""
    def __init__(self, ws_dir: Path):
        self.current_session = {"id": "test_sess_isolated_999", "messages": []}
        self.current_mode = "assistant"
        self._active_tasks = {}
        self.task_queue = MagicMock()
        self.lm_client = MagicMock()
        self._ws_dir = ws_dir

    def statusBar(self):
        m = MagicMock()
        m.showMessage = MagicMock()
        return m


class TestSandboxSessionIsolation:
    """Сьют проверок на утечки и устойчивость контекста сессий."""

    def test_01_session_scope_clean_exit(self):
        """Проверяет штатную установку сессии и её автоматический сброс после блока with."""
        assert SandboxManager.get_active_session_id() is None

        with SandboxManager.session_scope("custom_session_alpha"):
            assert SandboxManager.get_active_session_id() == "custom_session_alpha"
            expected_dir = WORKSPACE_DIR / "projects" / "custom_session_alpha"
            assert SandboxManager.get_workspace_dir() == expected_dir

        # После выхода сессия должна вернуться в None, а путь — в корень workspace
        assert SandboxManager.get_active_session_id() is None
        assert SandboxManager.get_workspace_dir() == WORKSPACE_DIR

    def test_02_session_scope_exception_resilience(self):
        """Проверяет, что при любом исключении внутри блока сессия гарантированно сбрасывается."""
        assert SandboxManager.get_active_session_id() is None

        with pytest.raises(ZeroDivisionError):
            with SandboxManager.session_scope("failing_session"):
                assert SandboxManager.get_active_session_id() == "failing_session"
                _ = 1 / 0  # Имитация фатального сбоя логики

        # Состояние обязано очиститься, несмотря на аварийный выход
        assert SandboxManager.get_active_session_id() is None
        assert SandboxManager.get_workspace_dir() == WORKSPACE_DIR

    def test_03_session_scope_nested_restoration(self):
        """Проверяет корректное восстановление родительской сессии при вложенных вызовах."""
        SandboxManager.set_active_session_id("parent_session")
        try:
            assert SandboxManager.get_active_session_id() == "parent_session"

            with SandboxManager.session_scope("child_session"):
                assert SandboxManager.get_active_session_id() == "child_session"

                with SandboxManager.session_scope("grandchild_session"):
                    assert SandboxManager.get_active_session_id() == "grandchild_session"

                # Вышли из grandchild — вернулся child
                assert SandboxManager.get_active_session_id() == "child_session"

            # Вышли из child — вернулся parent, а не сбросился в None
            assert SandboxManager.get_active_session_id() == "parent_session"
        finally:
            SandboxManager.set_active_session_id(None)

    def test_04_controller_dispatch_does_not_pollute_ui_state(self, tmp_path):
        """
        Регрессионный тест: вызов _dispatch_ephemeral_doc_worker в UI-потоке
        НЕ должен менять глобальный SandboxManager._active_session_id до старта воркера.
        """
        controller = DummyMainWindow(tmp_path)
        assert SandboxManager.get_active_session_id() is None

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=tmp_path):
            ok = controller._dispatch_ephemeral_doc_worker("создай смету в excel")
            assert ok is True

        # Главный инвариант: вызов диспатчера не оставил сессию висеть в памяти
        assert SandboxManager.get_active_session_id() is None, (
            "УТЕЧКА: Контроллер установил глобальный _active_session_id в UI-потоке!"
        )

    def test_05_worker_execution_lifecycle_cleanup(self, tmp_path):
        """
        Проверяет полный цикл выполнения фонового воркера:
        сессия активна во время работы, но очищается даже при ошибке внутри SessionManager.
        """
        controller = DummyMainWindow(tmp_path)

        with patch("services.sandbox_manager.SandboxManager.get_workspace_dir", return_value=tmp_path):
            controller._dispatch_ephemeral_doc_worker("построй график продаж")

        enqueued_task = controller.task_queue.enqueue.call_args[0][0]
        worker_func = enqueued_task.func
        worker_kwargs = enqueued_task.kwargs

        # 1. Прогон воркера с успешным результатом
        with patch("services.session_manager.SessionManager.run_ephemeral_engineer_worker", return_value={"success": True}):
            res = worker_func(**worker_kwargs)
            assert res["success"] is True
            assert SandboxManager.get_active_session_id() is None

        # 2. Прогон воркера с падением внутри генератора
        with patch("services.session_manager.SessionManager.run_ephemeral_engineer_worker", side_effect=RuntimeError("LLM Disconnected")):
            res = worker_func(**worker_kwargs)
            assert res["success"] is False
            # Проверяем, что блок finally сработал и сессия не утекла
            assert SandboxManager.get_active_session_id() is None
