"""
Unit tests for ChatMediaGalleryDialog in NeonRoute.
Verifies Windows 11 Fluent Design modernization, thumbnail rendering,
real-time search filter, dynamic adaptive palette, and Escape key handling.
"""

from pathlib import Path
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QColor, QKeyEvent
from PyQt6.QtWidgets import QApplication, QWidget

from ui.dialogs.media_gallery_dialog import ChatMediaGalleryDialog, ThumbnailButton


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_media_gallery_dialog_init(qapp):
    dlg = ChatMediaGalleryDialog(session_id="all_sessions", image_paths=[])
    dlg.show()
    try:
        assert dlg.titleBar.closeBtn.isVisible()
        assert "Медиа галерея" in dlg.windowTitle()
        assert dlg.badge_lbl.text() == "0 файлов"
        assert dlg._empty_label.isVisible() is True
    finally:
        dlg.close()


def test_media_gallery_dialog_with_images(qapp, tmp_path):
    img1 = tmp_path / "art_alpha.png"
    img2 = tmp_path / "photo_beta.jpg"

    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 240, 255))
    pix.save(str(img1), "PNG")
    pix.save(str(img2), "JPEG")

    dlg = ChatMediaGalleryDialog(session_id="test_sess", image_paths=[img1, img2])
    dlg.show()
    try:
        assert len(dlg._thumbnail_buttons) == 2
        assert dlg.badge_lbl.text() == "2 файлов"
        assert dlg._empty_label.isVisible() is False

        btn = dlg._thumbnail_buttons[0]
        assert isinstance(btn, ThumbnailButton)
        assert btn.image_path.exists()
    finally:
        dlg.close()


def test_media_gallery_dialog_filter(qapp, tmp_path):
    img_cat = tmp_path / "cyber_cat.png"
    img_dog = tmp_path / "neon_dog.png"

    pix = QPixmap(32, 32)
    pix.fill(QColor(255, 0, 128))
    pix.save(str(img_cat), "PNG")
    pix.save(str(img_dog), "PNG")

    dlg = ChatMediaGalleryDialog(session_id="test_sess", image_paths=[img_cat, img_dog])
    dlg.show()
    try:
        # Filter for 'cat'
        dlg._filter_images("cat")
        assert dlg.badge_lbl.text() == "1 из 2"
        btn_cat = [b for b in dlg._thumbnail_buttons if "cat" in b.image_path.name][0]
        btn_dog = [b for b in dlg._thumbnail_buttons if "dog" in b.image_path.name][0]
        assert btn_cat.isVisible() is True
        assert btn_dog.isVisible() is False
        assert dlg._empty_label.isVisible() is False

        # Filter for non-existent
        dlg._filter_images("dragon")
        assert "ничего не найдено" in dlg._empty_label.text()
        assert dlg._empty_label.isVisible() is True

        # Clear filter
        dlg._filter_images("")
        assert dlg.badge_lbl.text() == "2 файлов"
        assert btn_cat.isVisible() is True
        assert btn_dog.isVisible() is True
        assert dlg._empty_label.isVisible() is False
    finally:
        dlg.close()


def test_media_gallery_dialog_adaptive_palette(qapp):
    parent = QWidget()
    pix = QPixmap(100, 100)
    pix.fill(QColor(0, 120, 215))
    parent.background_pixmap = pix

    dlg = ChatMediaGalleryDialog(session_id="all_sessions", image_paths=[], parent=parent)
    try:
        assert dlg._resolve_pixmap() is not None
        assert "bg" in dlg._current_palette
        assert "accent" in dlg._current_palette
        assert "card" in dlg._current_palette
    finally:
        dlg.close()


def test_media_gallery_dialog_escape_closes(qapp):
    dlg = ChatMediaGalleryDialog(session_id="all_sessions", image_paths=[])
    dlg.show()
    try:
        event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        dlg.keyPressEvent(event)
        assert dlg.result() == 0  # QDialog.Rejected
    finally:
        dlg.close()
