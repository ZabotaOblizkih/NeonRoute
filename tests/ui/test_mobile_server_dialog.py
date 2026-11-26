"""
Unit tests for MobileServerDialog and preview cursor lifecycle in NeonRoute.
Verifies FramelessDialog architecture, widget structure, QR code update,
clean cursor unwinding, and absence of deadlocks when opening after image preview.
"""

from pathlib import Path
import pytest
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap, QColor
from PyQt6.QtWidgets import QApplication, QWidget

from ui.dialogs.mobile_server_dialog import MobileServerDialog
from ui.components.bubbles.image_preview_dialog import ImagePreviewDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_mobile_server_dialog_initialization_and_ui(qapp):
    urls = ["http://192.168.1.100:8000", "http://100.64.0.5:8000", "http://127.0.0.1:8000"]
    dlg = MobileServerDialog(urls=urls)
    dlg.show()
    try:
        assert dlg.isWindow() is True
        assert dlg.titleBar.closeBtn.isVisible() is True
        assert "Мобильный сервер" in dlg.windowTitle()
        assert dlg.active_url == urls[0]
        assert dlg.url_lbl.text() == urls[0]
        assert hasattr(dlg, "addr_combo")
        assert dlg.addr_combo.count() == 3
        assert dlg.btn_copy is not None
        assert dlg.btn_done is not None
        assert dlg.btn_stop is not None
        assert dlg.stop_server_requested is False
    finally:
        dlg.close()


def test_mobile_server_dialog_combo_selection_updates_qr(qapp):
    urls = ["http://192.168.1.100:8000", "http://127.0.0.1:8000"]
    dlg = MobileServerDialog(urls=urls)
    dlg.show()
    try:
        assert dlg.active_url == urls[0]
        dlg.addr_combo.setCurrentIndex(1)
        assert dlg.active_url == urls[1]
        assert dlg.url_lbl.text() == urls[1]
    finally:
        dlg.close()


def test_mobile_server_dialog_stop_button_sets_flag(qapp):
    dlg = MobileServerDialog(urls=["http://127.0.0.1:8000"])
    dlg.show()
    try:
        assert dlg.stop_server_requested is False
        dlg._on_stop_clicked()
        assert dlg.stop_server_requested is True
        assert dlg.result() == 0  # Rejected
    finally:
        dlg.close()


def test_mobile_server_dialog_done_button_accepts(qapp):
    dlg = MobileServerDialog(urls=["http://127.0.0.1:8000"])
    dlg.show()
    try:
        assert dlg.stop_server_requested is False
        dlg.btn_done.click()
        assert dlg.stop_server_requested is False
        assert dlg.result() == 1  # Accepted
    finally:
        dlg.close()


def test_preview_dialog_to_mobile_server_dialog_lifecycle(qapp, tmp_path):
    """
    Verifies that opening ImagePreviewDialog, closing it, and then opening MobileServerDialog
    does not deadlock or corrupt mouse/cursor state.
    """
    img_file = tmp_path / "test_preview.png"
    pix = QPixmap(120, 120)
    pix.fill(QColor(0, 240, 255))
    pix.save(str(img_file), "PNG")

    parent_win = QWidget()
    parent_win.resize(600, 400)
    parent_win.show()

    # 1. Open and close image preview
    preview = ImagePreviewDialog(img_file, parent=parent_win)
    QTimer.singleShot(50, preview.reject)
    preview.exec()
    preview.deleteLater()

    # Verify no dangling override cursors
    assert qapp.overrideCursor() is None

    # 2. Open MobileServerDialog
    mob_dlg = MobileServerDialog(parent=parent_win, urls=["http://127.0.0.1:8000"])
    
    # Verify buttons are enabled and clickable
    assert mob_dlg.btn_done.isEnabled() is True
    assert mob_dlg.btn_stop.isEnabled() is True
    assert mob_dlg.btn_copy.isEnabled() is True

    # Simulate user clicking Done
    QTimer.singleShot(50, mob_dlg.btn_done.click)
    res = mob_dlg.exec()
    mob_dlg.deleteLater()

    assert res == 1
    assert mob_dlg.stop_server_requested is False
    assert qapp.overrideCursor() is None

    parent_win.close()
