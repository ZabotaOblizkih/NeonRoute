import pytest
from PyQt6.QtWidgets import QApplication
from services.mode_coordinator import AppMode
from services.sandbox_manager import SandboxManager

@pytest.fixture(scope="session")
def qapp():
    """Headless Qt Application fixture."""
    app = QApplication.instance()
    if app is None:
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication(["-platform", "offscreen"])
    return app

def test_mainwindow_headless_lifecycle(qapp, monkeypatch):
    """Смок-тест инициализации MainWindow, контекста сессии и переключения режимов."""
    monkeypatch.setattr("services.lm_studio.LMStudioClient.check_health", lambda self: True)
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", lambda: "PURE COMPUTE")
    
    from ui.main_window import MainWindow

    # Подавляем сетевые фоновые сокеты для мгновенного offscreen старта
    monkeypatch.setattr(MainWindow, "_start_background_services", lambda self: None)

    window = MainWindow()
    window.show()

    try:
        # 1. Проверяем наличие ключевых полей состояния
        assert window is not None
        assert hasattr(window, "current_session")
        assert hasattr(window, "_turn_bubble")
        assert hasattr(window, "mode_coordinator")
        assert window.mode_coordinator.current_mode is not None

        # 2. Проверяем переключение режимов
        ok_eng = window.mode_coordinator.transition_to(AppMode.ENGINEER)
        assert ok_eng is True
        assert window.mode_coordinator.current_mode == AppMode.ENGINEER
        assert window.workspace_inspector.isVisible()

        ok_asst = window.mode_coordinator.transition_to(AppMode.ASSISTANT)
        assert ok_asst is True
        assert window.mode_coordinator.current_mode == AppMode.ASSISTANT
    finally:
        window._shutdown_completed = True
        window.close()
        # Сброс активной сессии для предотвращения утечки состояния
        SandboxManager.set_active_session_id(None)
