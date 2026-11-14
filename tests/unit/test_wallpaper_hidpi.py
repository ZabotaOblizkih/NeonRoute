"""
Unit tests for High-DPI wallpaper scaling and aliasing-free rendering.
Tests the 4-level progressive downscaling, devicePixelRatio buffer creation,
and ChatBackgroundViewport / WindowStateMixin integration.
"""

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QWidget

from ui.common.image_utils import (
    load_display_master_pixmap,
    progressive_scale_pixmap,
    create_hidpi_background_buffer,
)
from ui.components.chat_background import ChatBackgroundViewport
from ui.controllers.window_state_controller import WindowStateMixin


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
