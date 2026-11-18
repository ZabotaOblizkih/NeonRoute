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


def test_cyberpunk_placeholder_calculate_target_size():
    """
    Проверяет динамический расчет геометрии плейсхолдера:
    портрет (solo), ultrawide, квадрат (closeup), 16:9, 9:16 и дефолтное соотношение.
    """
    # 1. Портрет через флаг --ar
    w_p, h_p = CyberpunkArtPlaceholderWidget.calculate_target_size(prompt="girl --ar portrait")
    assert h_p > w_p
    assert w_p >= 280
    assert (w_p, h_p) == (327, 420)

    # 2. Ultrawide через флаг --ar
    w_uw, h_uw = CyberpunkArtPlaceholderWidget.calculate_target_size(prompt="landscape --ar ultrawide")
    assert w_uw == 480
    assert h_uw <= 210
    assert (w_uw, h_uw) == (480, 200)

    # 3. Квадрат через флаг --ar
    w_sq, h_sq = CyberpunkArtPlaceholderWidget.calculate_target_size(prompt="icon --ar square")
    assert w_sq == h_sq == 340

    # 4. Явный aspect_ratio = 16:9 (cinematic)
    w_cin, h_cin = CyberpunkArtPlaceholderWidget.calculate_target_size(aspect_ratio="16:9")
    assert (w_cin, h_cin) == (480, 274)

    # 5. Явный aspect_ratio = 9:16 (fullbody)
    w_fb, h_fb = CyberpunkArtPlaceholderWidget.calculate_target_size(aspect_ratio="9:16")
    assert w_fb >= 280
    assert (w_fb, h_fb) == (287, 420)

    # 6. Без аргументов (обратная совместимость, cinematic)
    w_def, h_def = CyberpunkArtPlaceholderWidget.calculate_target_size()
    assert (w_def, h_def) == (480, 274)


def test_assistant_bubble_dynamic_placeholder_sizing(qapp):
    """
    Проверяет адаптацию размеров AssistantChatMessageWidget под переданный prompt и visual_intent.
    """
    # 1. Вызов с промптом portrait
    widget_p = AssistantChatMessageWidget(sender="Assistant", text="Генерирую...", is_user=False)
    widget_p.show_art_loading_placeholder(prompt="cyberpunk girl --ar portrait")
    assert widget_p.art_placeholder is not None
    assert widget_p.art_placeholder.target_width == 327
    assert widget_p.art_placeholder.target_height == 420
    assert widget_p.bubble_container.minimumWidth() >= 327
    widget_p.cleanup()
    assert widget_p.art_placeholder is None

    # 2. Вызов с fallback на visual_intent (ultrawide)
    widget_vi = AssistantChatMessageWidget(
        sender="Assistant",
        text="Генерирую...",
        is_user=False,
        visual_intent={"aspect_ratio": "ultrawide", "prompt": "city night skyline"}
    )
    widget_vi.show_art_loading_placeholder()
    assert widget_vi.art_placeholder is not None
    assert widget_vi.art_placeholder.target_width == 480
    assert widget_vi.art_placeholder.target_height == 200
    widget_vi.cleanup()
    assert widget_vi.art_placeholder is None

    # 3. Базовый класс ChatMessageWidget: сигнатура с aspect_ratio и prompt без ошибок
    base_msg = ChatMessageWidget(sender="User", text="test", is_user=True)
    base_msg.show_art_loading_placeholder(aspect_ratio="16:9", prompt="test prompt")
