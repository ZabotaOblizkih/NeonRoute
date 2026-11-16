"""
Unit tests for High-DPI wallpaper scaling and aliasing-free rendering.
Tests the 4-level progressive downscaling, devicePixelRatio buffer creation,
and ChatBackgroundViewport / WindowStateMixin integration.
"""

import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QWidget, QApplication

from ui.common.image_utils import (
    load_display_master_pixmap,
    progressive_scale_pixmap,
    create_hidpi_background_buffer,
)
from ui.components.chat_background import ChatBackgroundViewport
from ui.components.chat_stream_widget import ChatScrollArea
from ui.controllers.roleplay_controller import RoleplayControllerMixin
from ui.controllers.window_state_controller import WindowStateMixin


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(["-platform", "offscreen"])
    return app


class DummyWindow(QWidget, WindowStateMixin):
    def __init__(self):
        super().__init__()
        self.resize(800, 600)
        self.background_pixmap = None
        self.background_dim_opacity = 140
        self._bg_render_cache = None


def test_progressive_scale_pixmap_halving():
    # Source is 4000x2000, target is 1000x500 (> 2x downscale)
    pix = QPixmap(4000, 2000)
    pix.fill(QColor(200, 50, 50))
    target = QSize(1000, 500)

    scaled = progressive_scale_pixmap(pix, target, Qt.AspectRatioMode.KeepAspectRatioByExpanding)
    assert not scaled.isNull()
    assert scaled.width() >= 1000
    assert scaled.height() >= 500


def test_create_hidpi_background_buffer_dpr():
    # Test DPR 1.25 (common Windows scaling)
    pix = QPixmap(2000, 1000)
    pix.fill(QColor(50, 100, 150))
    logical_size = QSize(800, 600)

    buf = create_hidpi_background_buffer(
        pix,
        logical_size,
        dpr=1.25,
        dim_alpha=120,
        bg_tint=QColor(0, 0, 0)
    )
    assert buf is not None
    assert not buf.isNull()
    # Physical width should be 800 * 1.25 = 1000
    assert buf.width() == 1000
    # Physical height should be 600 * 1.25 = 750
    assert buf.height() == 750
    # DPR should be set to 1.25
    assert buf.devicePixelRatio() == 1.25


def test_create_hidpi_background_buffer_empty():
    assert create_hidpi_background_buffer(None, QSize(800, 600)) is None
    pix = QPixmap()
    assert create_hidpi_background_buffer(pix, QSize(800, 600)) is None
    pix_valid = QPixmap(100, 100)
    assert create_hidpi_background_buffer(pix_valid, QSize(0, 0)) is None


def test_load_display_master_pixmap_nonexistent():
    assert load_display_master_pixmap("non_existent_file.png") is None


def test_window_state_mixin_cache_update(qtbot):
    win = DummyWindow()
    qtbot.addWidget(win)

    # Initial state: no pixmap
    win._update_background_cache()
    assert win._bg_render_cache is None

    # Set pixmap
    pix = QPixmap(1920, 1080)
    pix.fill(QColor(30, 40, 50))
    win.background_pixmap = pix

    win._update_background_cache()
    assert win._bg_render_cache is not None
    assert not win._bg_render_cache.isNull()
    assert win._bg_render_cache.devicePixelRatio() >= 1.0


def test_chat_background_viewport_lifecycle(qtbot):
    vp = ChatBackgroundViewport()
    qtbot.addWidget(vp)
    vp.resize(600, 400)

    assert not vp.has_background()
    assert vp.get_dim_alpha() == 150

    pix = QPixmap(1600, 900)
    pix.fill(QColor(10, 20, 30))
    vp.set_background(pix, alpha=100)

    assert vp.has_background()
    assert vp.get_dim_alpha() == 100
    assert vp._scaled_pixmap is not None
    assert not vp._scaled_pixmap.isNull()

    # Test dimming change
    vp.set_dim_alpha(180)
    assert vp.get_dim_alpha() == 180

    # Test reset
    vp.set_background(None)
    assert not vp.has_background()
    assert vp._scaled_pixmap is None


def test_chat_scroll_area_scroll_contents_by_updates_viewport(qtbot):
    area = ChatScrollArea()
    qtbot.addWidget(area)
    vp = QWidget(area)
    area.setViewport(vp)

    update_called = []
    vp.update = lambda: update_called.append(True)

    # 1. dx == 0 and dy == 0: should NOT call update() (optimizing zero shifts)
    area.scrollContentsBy(0, 0)
    assert len(update_called) == 0

    # 2. dy != 0: must call update() to prevent BitBlt jitter
    area.scrollContentsBy(0, 10)
    assert len(update_called) == 1

    # 3. dx != 0: must call update()
    area.scrollContentsBy(-5, 0)
    assert len(update_called) == 2


class DummyRPWindow(QWidget, RoleplayControllerMixin):
    def __init__(self):
        super().__init__()
        self.current_session = None
        self.background_pixmap = None
        self.background_dim_opacity = 140
        self.chat_viewport = ChatBackgroundViewport(self)
        self.scroll_area = None


def test_apply_chat_background_transparency_and_session_switching(tmp_path, qtbot):
    win = DummyRPWindow()
    qtbot.addWidget(win)

    # Set a global window background
    global_pix = QPixmap(800, 600)
    global_pix.fill(QColor(50, 60, 70))
    win.background_pixmap = global_pix

    # 1. Session without custom scene background -> viewport must remain transparent (has_background == False)
    win.current_session = {"id": "sess_1", "background_image": None}
    win._apply_chat_background()
    assert not win.chat_viewport.has_background()

    # 2. Session with a valid personal scene background -> viewport must render scene background
    test_img = tmp_path / "scene.png"
    global_pix.save(str(test_img))
    win.current_session = {"id": "sess_2", "background_image": str(test_img)}
    win._apply_chat_background()
    assert win.chat_viewport.has_background()

    # 3. Switching back to session 1 without background -> viewport MUST reset to transparent mode
    win.current_session = {"id": "sess_1", "background_image": None}
    win._apply_chat_background()
    assert not win.chat_viewport.has_background()

