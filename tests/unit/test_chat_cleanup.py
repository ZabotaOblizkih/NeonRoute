import pytest
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QScrollArea
from PyQt6.QtCore import Qt

from ui.builders.chat_bubble_builder import ChatBubbleBuilderMixin
from ui.components.chat_bubble import ChatMessageWidget
from ui.components.assistant_chat_bubble import AssistantChatMessageWidget, CyberpunkArtPlaceholderWidget


class DummyChatHost(ChatBubbleBuilderMixin, QWidget):
    """Тестовый хост для проверки Mixin очистки чата."""
    def __init__(self):
        super().__init__()
        self.chat_container = QWidget(self)
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidget(self.chat_container)
        self._rendered_messages_offset = 0
        self._all_session_messages = []
        self._btn_load_more_widget = None


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_clear_chat_widgets_prevents_hwnd_leak(qapp):
    """
    Проверяет, что при зачистке чата виджеты не превращаются в unparented HWND окна (setParent(None)),
    а скрываются и безопасно отправляются в deleteLater().
    """
    host = DummyChatHost()
    msg_widget = ChatMessageWidget(sender="User", text="Hello world", is_user=True, parent=host.chat_container)
    host.chat_layout.addWidget(msg_widget)

    assert host.chat_layout.count() == 1
    assert not (msg_widget.windowFlags() & Qt.WindowType.Window)

    host._clear_chat_widgets()

    assert host.chat_layout.count() == 1  # 1 stretch item remaining
    assert msg_widget.isHidden()
    # КРИТИЧЕСКАЯ ПРОВЕРКА: виджет НЕ должен был получить setParent(None)
    # и стать top-level Windows окном с флагом Qt.Window!
    assert not (msg_widget.windowFlags() & Qt.WindowType.Window)


def test_assistant_chat_bubble_cleanup(qapp):
    """
    Проверяет, что у AssistantChatMessageWidget при вызове cleanup()
    останавливаются все фоновые таймеры и плейсхолдеры генерации.
    """
    widget = AssistantChatMessageWidget(sender="Assistant", text="Генерация...", is_user=False)
    widget.show_art_loading_placeholder()
    assert widget.art_placeholder is not None
    assert widget.art_placeholder._timer.isActive()

    # Вызов cleanup() должен остановить таймер и скрыть плейсхолдер
    widget.cleanup()
    assert widget.art_placeholder is None


def test_cyberpunk_placeholder_cleanup(qapp):
    """
    Проверяет корректность остановки таймера CyberpunkArtPlaceholderWidget
    при прямом вызове cleanup() и hideEvent.
    """
    ph = CyberpunkArtPlaceholderWidget()
    assert ph._timer.isActive()

    ph.stop()
    assert not ph._timer.isActive()

    ph.cleanup()
    assert not ph._timer.isActive()
