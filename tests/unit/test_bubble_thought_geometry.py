import time
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from qfluentwidgets import SmoothScrollArea

from ui.components.assistant_chat_bubble import AssistantChatMessageWidget
from ui.components.chat_background import ChatBackgroundViewport


def _make_scroll_host(qapp):
    scroll = SmoothScrollArea()
    scroll.resize(1265, 576)
    scroll.setWidgetResizable(True)
    scroll.setViewport(ChatBackgroundViewport(scroll))
    cont = QWidget()
    lay = QVBoxLayout(cont)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(12)
    lay.addStretch()
    scroll.setWidget(cont)
    scroll.show()
    qapp.processEvents()
    return scroll, cont, lay


def test_bubble_retains_expanded_width_after_thought_streaming_and_layout_request(qapp):
    """
    Проверяет, что бабл с блоком «Ход мыслей» не сжимается до ширины свёрнутой шапки
    спойлера при отложенном updateGeometry() / LayoutRequest после завершения ответа,
    а высота text_browser вмещает весь текст ответа без обрезания.
    """
    scroll, cont, lay = _make_scroll_host(qapp)
    try:
        msg = AssistantChatMessageWidget(sender="Элария", text="", is_user=False, font_size=14, parent=cont)
        lay.insertWidget(0, msg, alignment=Qt.AlignmentFlag.AlignTop)
        qapp.processEvents()

        thought = "Последовательность рассуждений модели для проверки геометрии. " * 12
        for i in range(0, len(thought), 80):
            msg.append_thought_chunk(thought[i:i + 80])
            qapp.processEvents()

        expanded_during_thought_w = msg.bubble_container.minimumWidth()
        assert expanded_during_thought_w >= 700

        msg.finalize_thought(7.7, 266)
        qapp.processEvents()
        # После автосворачивания мыслей ширина сохраняется
        assert msg.bubble_container.minimumWidth() >= expanded_during_thought_w
        assert msg.bubble_container.width() >= expanded_during_thought_w

        answer = (
            "Привет, Виктор! Поддерживаю! 😊\n\n"
            "Если у тебя есть документ для анализа или просто хочешь поработать над чем-то — "
            "начинай вести себя так, как будто самые важные вопросы уже решены. Я с тобой на каждом шаге.\n\n"
            "Так, если это именно про документ (и ты можешь его прикрепить), то я готова приступить! "
            "Если нет — давай уточним, над чем именно хочешь работать?"
        )
        msg.append_stream_chunk(answer[:60])
        qapp.processEvents()
        msg.append_stream_chunk(answer[60:])
        qapp.processEvents()
        msg.set_text(answer)
        msg.set_thought_data(thought, 7.7, 266)
        qapp.processEvents()

        # Триггерим отложенный LayoutRequest, который раньше сбрасывал minimumWidth из-за SetMinimumSize
        msg.text_browser.document().setModified(True)
        msg.bubble_container.updateGeometry()
        qapp.processEvents()

        assert msg.bubble_container.minimumWidth() >= expanded_during_thought_w
        assert msg.bubble_container.width() >= expanded_during_thought_w
        doc_h = msg.text_browser.document().size().height()
        assert msg.text_browser.height() >= doc_h
    finally:
        scroll.hide()
        scroll.deleteLater()
        qapp.processEvents()


def test_bubble_manual_thought_toggle_updates_height_and_preserves_width(qapp):
    """
    Проверяет, что ручное раскрытие и закрытие блока «Ход мыслей» сохраняет расширенную
    ширину бабла, но корректно сжимает высоту после закрытия спойлера.
    """
    scroll, cont, lay = _make_scroll_host(qapp)
    try:
        msg = AssistantChatMessageWidget(sender="Элария", text="", is_user=False, font_size=14, parent=cont)
        lay.insertWidget(0, msg, alignment=Qt.AlignmentFlag.AlignTop)

        answer = (
            "Привет, Виктор! Поддерживаю!\n\n"
            "Если у тебя есть документ для анализа или просто хочешь поработать над чем-то — "
            "начинай вести себя так, как будто самые важные вопросы уже решены.\n\n"
            "Если нет — давай уточним, над чем именно хочешь работать?"
        )
        thought = "Длинный ход мыслей модели для проверки раскрытия и сворачивания. " * 15
        msg.set_text(answer)
        msg.set_thought_data(thought, 5.2, 180)
        qapp.processEvents()

        collapsed_h = msg.bubble_container.height()

        msg.thought_spoiler.toggle_collapsed()
        time.sleep(0.03)
        qapp.processEvents()
        opened_w = msg.bubble_container.width()
        opened_h = msg.bubble_container.height()
        assert opened_h > collapsed_h + 60
        assert opened_w >= 768

        msg.thought_spoiler.toggle_collapsed()
        time.sleep(0.03)
        qapp.processEvents()
        closed_w = msg.bubble_container.width()
        closed_h = msg.bubble_container.height()
        assert closed_w == opened_w
        assert closed_h < opened_h - 60
    finally:
        scroll.hide()
        scroll.deleteLater()
        qapp.processEvents()
