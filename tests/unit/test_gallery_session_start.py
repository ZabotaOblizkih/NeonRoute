from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton

from ui.controllers.session_controller import SessionControllerMixin
from ui.controllers.roleplay_controller import RoleplayControllerMixin


class DummySessionHost(SessionControllerMixin, RoleplayControllerMixin, QWidget):
    """Тестовый хост для проверки жизненного цикла сессий и галереи."""
    def __init__(self):
        super().__init__()
        self.chat_layout = QVBoxLayout(self)
        self._greeting_cycler_widget = None
        self._active_gallery_dialog = None
        self._active_settings_dialog = None
        self.current_session = None
        self.active_character = None


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_remove_greeting_cycler_chip_hides_and_cleans_up(qapp):
    """
    Проверяет, что при удалении чипа смены завязок он сначала скрывается (.hide()),
    удаляется из layout и безопасно освобождается (.deleteLater()).
    """
    host = DummySessionHost()
    chip = QPushButton("🎲 Сменить завязку", host)
    host.chat_layout.addWidget(chip)
    host._greeting_cycler_widget = chip

    assert chip.isVisible() or not chip.isHidden()
    assert host.chat_layout.count() == 1

    host._remove_greeting_cycler_chip()

    assert host._greeting_cycler_widget is None
    assert chip.isHidden()
    assert host.chat_layout.count() == 0


def test_open_characters_gallery_lifecycle_and_delete_later(qapp):
    """
    Проверяет, что после завершения exec() диалог галереи персонажей
    гарантированно освобождается через deleteLater() и не утекает в памяти.
    """
    host = DummySessionHost()
    host._create_new_session = MagicMock()

    mock_dialog = MagicMock()
    mock_dialog.exec.return_value = 1
    mock_dialog.selected_card = MagicMock(name="TestCard")

    with patch("ui.dialogs.characters_gallery_dialog.CharactersGalleryDialog", return_value=mock_dialog):
        host._open_characters_gallery_dialog()

    mock_dialog.deleteLater.assert_called_once()
    assert host._active_gallery_dialog is None


def test_open_characters_gallery_from_settings_closes_settings(qapp):
    """
    Проверяет, что при выборе компаньона в галерее, открытой из окна настроек,
    родительское окно настроек (_active_settings_dialog) корректно закрывается.
    """
    host = DummySessionHost()
    host._create_new_session = MagicMock()

    mock_settings = MagicMock()
    host._active_settings_dialog = mock_settings

    mock_gallery = MagicMock()
    mock_gallery.exec.return_value = 1
    mock_gallery.selected_card = MagicMock(name="SelectedCompanion")

    with patch("ui.dialogs.characters_gallery_dialog.CharactersGalleryDialog", return_value=mock_gallery):
        host._open_characters_gallery_dialog()

    mock_settings.close.assert_called_once()
    mock_gallery.deleteLater.assert_called_once()

