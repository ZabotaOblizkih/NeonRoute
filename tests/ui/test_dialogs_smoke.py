"""
Consolidated smoke & contract tests for simple FramelessDialog windows:
AboutDialog, HelpDialog, ImagePreviewDialog.
"""

import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QPixmap, QColor, QKeyEvent
from PyQt6.QtWidgets import QApplication, QMainWindow
from qframelesswindow import FramelessDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


class _DummyParent(QMainWindow):
    def __init__(self):
        super().__init__()
        self.background_pixmap = None


@pytest.fixture
def dummy_window(qapp):
    win = _DummyParent()
    yield win
    win.deleteLater()


@pytest.mark.parametrize("dialog_cls_path,title_substr", [
    ("ui.dialogs.about_dialog.AboutDialog", "О программе"),
    ("ui.dialogs.help_dialog.HelpDialog", "Горячие клавиши"),
])
def test_simple_frameless_dialog_init_palette_and_escape(qapp, dummy_window, dialog_cls_path, title_substr):
    import importlib
    from ui.common.palette_utils import extract_adaptive_palette

    mod_path, cls_name = dialog_cls_path.rsplit(".", 1)
    dialog_cls = getattr(importlib.import_module(mod_path), cls_name)

    default_pal = extract_adaptive_palette(None)
    dialog = dialog_cls(dummy_window)
    dialog.show()
    try:
        assert isinstance(dialog, FramelessDialog)
        assert title_substr in dialog.windowTitle()
        assert dialog._current_palette["bg"] == default_pal["bg"]

        tinted_pixmap = QPixmap(64, 64)
        tinted_pixmap.fill(QColor("#10B981"))
        dummy_window.background_pixmap = tinted_pixmap
        dialog.update_adaptive_palette()
        assert dialog._current_palette["bg"] != default_pal["bg"]

        esc_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        dialog.keyPressEvent(esc_event)
        assert dialog.isVisible() is False
    finally:
        dialog.deleteLater()


def test_help_dialog_search_filter(qapp, dummy_window):
    from ui.dialogs.help_dialog import HelpDialog

    dialog = HelpDialog(dummy_window)
    dialog.show()
    try:
        assert len(dialog._cards) > 0
        assert all(card.isVisible() for card in dialog._cards)

        dialog._on_search_changed("Ctrl + B")
        visible_cards = [c for c in dialog._cards if not c.isHidden()]
        assert 1 <= len(visible_cards) < len(dialog._cards)

        dialog._on_search_changed("")
        assert all(card.isVisible() for card in dialog._cards)
    finally:
        dialog.deleteLater()


def test_image_preview_dialog_contract_and_accordion(qapp, tmp_path):
    from ui.components.bubbles.image_preview_dialog import ImagePreviewDialog

    img_file = tmp_path / "art_with_params.png"
    pix = QPixmap(100, 150)
    pix.fill(QColor(80, 180, 220))
    pix.save(str(img_file), "PNG")

    dlg_plain = ImagePreviewDialog(img_file)
    dlg_plain.show()
    try:
        assert "Просмотр" in dlg_plain.windowTitle()
        assert (dlg_plain._orig_w, dlg_plain._orig_h) == (100, 150)
        assert dlg_plain.gen_info_widget is None
    finally:
        dlg_plain.close()

    gen_params = {
        "checkpoint": "cyberpunk_sdxl.safetensors",
        "seed": 483627075,
        "sampler_name": "LCM",
        "steps": 7,
    }
    dlg_params = ImagePreviewDialog(img_file, generation_params=gen_params)
    dlg_params.show()
    try:
        assert dlg_params.gen_info_widget is not None
        assert dlg_params.gen_info_widget.params["seed"] == 483627075
        base_h = dlg_params.height()
        dlg_params.gen_info_widget._toggle_collapse()
        assert dlg_params.gen_info_widget.is_expanded is True
        assert dlg_params.height() >= base_h
        dlg_params.gen_info_widget._toggle_collapse()
        assert dlg_params.gen_info_widget.is_expanded is False
        assert dlg_params.size() == dlg_params._base_dialog_size

        dlg_params.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert dlg_params.result() == 0
    finally:
        dlg_params.close()


def test_thought_spoiler_widget_ui(qapp):
    from ui.components.thought_spoiler_widget import ThoughtSpoilerWidget

    widget = ThoughtSpoilerWidget()
    widget.show()
    try:
        assert widget.is_collapsed is True
        assert widget.content_area.isHidden() is True

        widget.append_thought_chunk("Первая мысль... ")
        assert widget.is_collapsed is False
        assert widget.content_area.isHidden() is False
        assert "Анализ данных..." in widget.metrics_label.text()

        widget.append_thought_chunk("Вторая мысль.")
        assert "Первая мысль... Вторая мысль." in widget.get_thought_text()

        widget.finalize_thought(duration_sec=3.5, token_count=45)
        assert widget.is_collapsed is True
        assert widget.content_area.isHidden() is True
        assert "45 токенов • 3.5 сек" in widget.metrics_label.text()

        widget.toggle_collapsed()
        assert widget.is_collapsed is False
        widget.toggle_collapsed()
        assert widget.is_collapsed is True

        widget.set_thought_data("Мысли загруженные из session.json", duration=1.8, token_count=32)
        assert widget.is_collapsed is True
        assert "32 токенов • 1.8 сек" in widget.metrics_label.text()
        assert "Мысли загруженные" in widget.get_thought_text()
    finally:
        widget.deleteLater()


def test_knowledge_manager_dialog_shows_real_effective_skill_prompt(qapp, monkeypatch):
    monkeypatch.setattr("services.knowledge_manager.KnowledgeManager.get_documents", staticmethod(lambda: []))

    from ui.dialogs.knowledge_manager_dialog import KnowledgeManagerDialog

    dialog = KnowledgeManagerDialog(parent=None)
    dialog.app_config = {"user_name": "Виктор"}
    try:
        dialog._on_skill_selected("code_architect")
        shown_text = dialog.text_system_prompt.toPlainText()
        assert "[АКТИВНЫЙ СКИЛЛ: РАЗРАБОТКА И АРХИТЕКТУРА КОДА]" in shown_text
        assert "Эталонный образец:" in shown_text
        assert dialog.text_system_prompt.isEnabled() is True
        assert dialog.text_system_prompt.isReadOnly() is True
        assert dialog.btn_save_skill.isEnabled() is False

        dialog._on_add_skill_clicked()
        assert dialog.text_system_prompt.isEnabled() is True
        assert dialog.text_system_prompt.isReadOnly() is False
        assert dialog.btn_save_skill.isEnabled() is True
    finally:
        dialog.hide()
        dialog.deleteLater()
        qapp.processEvents()
