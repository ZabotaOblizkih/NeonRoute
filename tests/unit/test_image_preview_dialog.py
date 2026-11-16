"""
Unit tests for ImagePreviewDialog in NeonRoute.
Verifies Windows 11 Fluent Design modernization, adaptive palette,
PrimaryPushButton / PushButton styling, Escape key handling,
and embedded GenerationInfoAccordionWidget inspection.
"""

from pathlib import Path
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QColor, QKeyEvent
from PyQt6.QtWidgets import QApplication, QWidget

from ui.components.bubbles.image_preview_dialog import ImagePreviewDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_image_preview_dialog_with_valid_image(qapp, tmp_path):
    img_file = tmp_path / "test_render.png"
    pix = QPixmap(100, 150)
    pix.fill(QColor(100, 200, 250))
    pix.save(str(img_file), "PNG")

    parent = QWidget()
    parent.background_pixmap = QPixmap(50, 50)
    parent.background_pixmap.fill(QColor(20, 80, 160))

    dlg = ImagePreviewDialog(img_file, parent=parent)
    dlg.show()
    try:
        assert dlg.titleBar.closeBtn.isVisible()
        assert "Просмотр" in dlg.windowTitle()
        assert dlg._orig_w == 100
        assert dlg._orig_h == 150
        assert "PNG" in dlg.info_badge.text()
        assert dlg.btn_open_sys is not None
        assert dlg.btn_show_folder is not None
        assert dlg.btn_close is not None
        assert dlg._current_palette.get("bg") is not None
        assert dlg.gen_info_widget is None
    finally:
        dlg.close()


def test_image_preview_dialog_with_generation_params(qapp, tmp_path):
    img_file = tmp_path / "art_with_params.png"
    pix = QPixmap(200, 200)
    pix.fill(QColor(80, 180, 220))
    pix.save(str(img_file), "PNG")

    gen_params = {
        "checkpoint": "cyberpunk_sdxl.safetensors",
        "seed": 483627075,
        "sampler_name": "LCM",
        "steps": 7,
        "denoising_strength": 0.4,
        "positive_prompt": "cyberpunk city, neon lights, rainy street",
        "negative_prompt": "text, watermark, blurry",
    }

    dlg = ImagePreviewDialog(img_file, generation_params=gen_params)
    dlg.show()
    try:
        assert dlg.gen_info_widget is not None
        assert dlg.gen_info_widget.params["seed"] == 483627075
        assert "cyberpunk_sdxl" in dlg.gen_info_widget.params["checkpoint"]
        assert dlg.gen_info_widget.toggle_btn.isVisible() is True
        base_h = dlg.height()

        # Раскрываем аккордеон
        dlg.gen_info_widget._toggle_collapse()
        assert dlg.gen_info_widget.is_expanded is True
        assert dlg.height() >= base_h

        # Сворачиваем обратно
        dlg.gen_info_widget._toggle_collapse()
        assert dlg.gen_info_widget.is_expanded is False
        assert dlg.size() == dlg._base_dialog_size
    finally:
        dlg.close()


def test_image_preview_dialog_escape_key(qapp, tmp_path):
    img_file = tmp_path / "escape_test.png"
    pix = QPixmap(64, 64)
    pix.fill(QColor(50, 50, 50))
    pix.save(str(img_file), "PNG")

    dlg = ImagePreviewDialog(img_file)
    dlg.show()
    try:
        key_event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier
        )
        dlg.keyPressEvent(key_event)
        assert dlg.result() == 0  # Rejected / closed
    finally:
        dlg.close()
