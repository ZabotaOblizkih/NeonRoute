"""
Unit and regression tests for WindowStateMixin, Fullscreen toggle geometry caching,
Ghost QMenuBar prevention, and Splitter Layout synchronization.
"""

import os
import pytest
from unittest.mock import MagicMock
from PyQt6.QtCore import Qt, QRect, QEvent
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QSplitter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def isolated_main_window(qapp, monkeypatch):
    monkeypatch.setattr("services.lm_studio.LMStudioClient.check_health", lambda self: True)
    monkeypatch.setattr("services.layer_budgeter.LayerBudgeter.detect_gpu_topology", lambda: "PURE COMPUTE")
    monkeypatch.setattr("ui.main_window.MainWindow._start_background_services", lambda self: None)
    monkeypatch.setattr("services.environment_doctor.EnvironmentDoctor.check_missing", lambda: [])
    monkeypatch.setattr("services.process_manager.ProcessManager.start_lm_studio_async", lambda *a, **kw: None)

    from ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    yield win
    win.close()
    win.deleteLater()


def test_fullscreen_toggle_geometry_caching(isolated_main_window):
    """Verifies that normal geometry is cached and restored upon exiting fullscreen."""
    win = isolated_main_window
    initial_rect = QRect(100, 100, 1200, 750)
    win.setGeometry(initial_rect)

    # Toggle to Fullscreen
    win._toggle_fullscreen()
    assert win.isFullScreen()
    assert hasattr(win, "_pre_fullscreen_geometry")
    assert win._pre_fullscreen_geometry == initial_rect

    # Toggle back from Fullscreen
    win._toggle_fullscreen()
    assert not win.isFullScreen()
    assert win.geometry() == initial_rect


def test_fullscreen_toggle_preserves_maximized(isolated_main_window, monkeypatch):
    """Verifies that maximized state is remembered and restored rather than forcing normal state."""
    win = isolated_main_window
    monkeypatch.setattr(win, "isMaximized", lambda: True)
    show_max_mock = MagicMock()
    monkeypatch.setattr(win, "showMaximized", show_max_mock)

    # Enter fullscreen from maximized
    win._toggle_fullscreen()
    assert getattr(win, "_was_maximized_before_fullscreen", False) is True

    # Exit fullscreen
    win._toggle_fullscreen()
    assert show_max_mock.called


def test_window_state_change_does_not_inject_ghost_menubar(isolated_main_window):
    """Verifies that WindowStateChange does not construct a QMenuBar via self.menuBar()."""
    win = isolated_main_window
    # Confirm initially no menubar exists
    assert getattr(win, "_menu_bar", None) is None

    # Simulate WindowStateChange event via QEvent
    event = QEvent(QEvent.Type.WindowStateChange)
    win.changeEvent(event)

    # Must NOT have created a QMenuBar in QMainWindow
    from PyQt6.QtWidgets import QMenuBar
    menu_bars = win.findChildren(QMenuBar)
    assert len(menu_bars) == 0


def test_sync_splitter_layout_resilience(isolated_main_window):
    """Verifies that _sync_splitter_layout manages 2 vs 3 items properly at standard and engineer modes."""
    win = isolated_main_window
    assert hasattr(win, "main_splitter")

    win.resize(1280, 800)
    win._sync_splitter_layout(is_engineer=False)

    sizes = win.main_splitter.sizes()
    assert len(sizes) in (2, 3)
    assert 260 <= sizes[0] <= 280
    assert sizes[1] >= 500  # Chat width is protected

    # Switch to engineer mode
    win._sync_splitter_layout(is_engineer=True)
    sizes_eng = win.main_splitter.sizes()
    assert 260 <= sizes_eng[0] <= 280
    assert sizes_eng[1] >= 400
    if len(sizes_eng) >= 3:
        assert sizes_eng[2] >= 350


def test_sync_splitter_layout_ignored_when_minimized(isolated_main_window, monkeypatch):
    """Verifies that _sync_splitter_layout aborts safely if the window is minimized."""
    win = isolated_main_window
    monkeypatch.setattr(win, "isMinimized", lambda: True)
    mock_set_sizes = MagicMock()
    monkeypatch.setattr(win.main_splitter, "setSizes", mock_set_sizes)

    win._sync_splitter_layout(is_engineer=False)
    assert not mock_set_sizes.called
