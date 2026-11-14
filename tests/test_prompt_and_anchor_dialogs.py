# -*- coding: utf-8 -*-
import pytest
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from ui.dialogs.prompt_viewer_dialog import PromptViewerDialog
from ui.dialogs.anchor_viewer_dialog import AnchorViewerDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


def test_prompt_viewer_dialog_initialization_and_window_status(qapp):
    card = {
        "name": "Элейна",
        "description": "Странствующая ведьма",
        "personality": "Любопытная, слегка надменная",
        "scenario": "Город магов"
    }
    dialog = PromptViewerDialog(
        parent=None,
        character_card=card,
        user_name="Игрок",
        current_prompt=None
    )

    # Contract: Dialog must be a top-level FramelessDialog window to avoid modal popup deadlock
    assert dialog.isWindow() is True
    assert dialog.windowTitle() == "📜 Системный промпт карточки"
    assert "Элейна" in dialog.prompt_edit.toPlainText()
    assert dialog.get_configured_prompt() is None  # Matches default -> None

    # Editing prompt
    custom_text = "# Custom system prompt for testing"
    dialog.prompt_edit.setPlainText(custom_text)
    assert dialog.get_configured_prompt() == custom_text

    # Reset to default
    dialog._on_reset_to_default()
    assert dialog.get_configured_prompt() is None
    assert "Элейна" in dialog.prompt_edit.toPlainText()


def test_anchor_viewer_dialog_initialization_and_window_status(qapp):
    card = {
        "name": "Элейна",
        "description": "Странствующая ведьма",
        "archetype": "Solo"
    }
    dialog = AnchorViewerDialog(
        parent=None,
        character_card=card,
        user_name="Игрок",
        current_anchor=None
    )

    # Contract: Dialog must be a top-level FramelessDialog window
    assert dialog.isWindow() is True
    assert dialog.windowTitle() == "⚓ Якорь сцены (Depth-0 Директива)"
    assert dialog.get_configured_anchor() is None

    # Editing anchor
    custom_anchor = "[Кастомный якорь сцены]"
    dialog.anchor_edit.setPlainText(custom_anchor)
    assert dialog.get_configured_anchor() == custom_anchor

    # Reset to default
    dialog._on_reset_to_default()
    assert dialog.get_configured_anchor() is None
