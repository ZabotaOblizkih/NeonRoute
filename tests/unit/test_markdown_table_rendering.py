import re
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from qfluentwidgets import SmoothScrollArea

from utils.markdown_table_parser import (
    parse_markdown_tables_to_qt_html,
    contains_markdown_table
)
from ui.components.chat_bubble import ChatMessageWidget
from ui.components.rp.rp_chat_bubble import RPChatMessageWidget
from ui.widgets.engineer_task_card import EngineerTaskCardWidget
from ui.components.chat_background import ChatBackgroundViewport


T34_MARKDOWN = """### Тактико-технические характеристики Т-34

| Параметр | Характеристика | Значение |
| :--- | :---: | ---: |
| **Тип двигателя** | Дизельный В-2-34 | 500 л.с. |
| Боевая масса | Средний танк | 30.9 т |
| Запас хода | По шоссе | 300 км |
"""


def test_standard_table_rendering(qapp):
    """Проверка рендеринга таблицы Т-34 с жирным шрифтом **Тип двигателя** внутри ячеек."""
    w = ChatMessageWidget(sender="Элария", text=T34_MARKDOWN, is_user=False)
    raw_html = w._markdown_to_html(T34_MARKDOWN)
    doc_html = w.text_browser.toHtml()

    assert "<table" in raw_html
    assert "<strong>Тип двигателя</strong>" in raw_html
    assert "<table" in doc_html
    assert "Тип двигателя" in doc_html
    assert "font-weight:700" in doc_html
    assert "В-2-34" in doc_html
    assert "500 л.с." in doc_html


def test_column_alignments():
    """Проверка выравнивания колонок (left, center, right) из строки разделителя."""
    md = """| Лево | Центр | Право | Дефолт |
| :--- | :---: | ---: | --- |
| L | C | R | D |
"""
    parsed = parse_markdown_tables_to_qt_html(md)
    assert '<th align="left"' in parsed
    assert '<th align="center"' in parsed
    assert '<th align="right"' in parsed
    assert '<td align="left"' in parsed
    assert '<td align="center"' in parsed
    assert '<td align="right"' in parsed


def test_ignore_tables_in_code_blocks(qapp):
    """Проверка, что примеры таблиц внутри блоков кода ``` не превращаются в HTML-таблицы."""
    text_with_code = """Вот пример Markdown-таблицы в коде:
```markdown
| Code Col 1 | Code Col 2 |
| --- | --- |
| val 1 | val 2 |
```

А вот реальная таблица:
| Реальная 1 | Реальная 2 |
| :--- | ---: |
| Data 1 | Data 2 |
"""
    w = ChatMessageWidget(sender="Элария", text=text_with_code, is_user=False)
    raw_html = w.text_browser.toHtml()

    # Внутри документа должен быть только один реальный тег <table
    table_matches = re.findall(r"<table\b", raw_html, re.IGNORECASE)
    assert len(table_matches) == 1

    # В кодовом блоке сохраняются сырые пайпы
    assert "Code Col 1" in raw_html
    assert "val 1" in raw_html


def test_no_br_tags_inside_table(qapp):
    """Проверка отсутствия тегов <br> внутри сгенерированного блока <table ...>...</table>."""
    w = ChatMessageWidget(sender="Элария", text=T34_MARKDOWN, is_user=False)
    rendered_div = w._markdown_to_html(T34_MARKDOWN)

    # Находим блок таблицы
    match = re.search(r"<table\b.*?</table>", rendered_div, flags=re.DOTALL | re.IGNORECASE)
    assert match is not None, "Тег <table> не найден в _markdown_to_html"
    table_block = match.group(0)

    assert "<br>" not in table_block
    assert "<br/>" not in table_block
    assert "<br />" not in table_block


def test_contains_markdown_table_helper():
    """Проверка быстрого детектора таблиц contains_markdown_table."""
    assert contains_markdown_table(T34_MARKDOWN) is True
    assert contains_markdown_table("<table border='1'><tr><td>1</td></tr></table>") is True
    assert contains_markdown_table("Обычный текст без таблиц.") is False
    assert contains_markdown_table("Текст с одиночным | символом.") is False
    assert contains_markdown_table("") is False


def test_rp_and_engineer_table_rendering(qapp):
    """Проверка рендеринга таблиц в ролевом режиме (RPChatMessageWidget) и режиме Инженера (EngineerTaskCardWidget)."""
    rp_w = RPChatMessageWidget(sender="Элария", text=T34_MARKDOWN, is_user=False)
    rp_raw_html = rp_w._markdown_to_html(T34_MARKDOWN)
    rp_doc_html = rp_w.text_browser.toHtml()
    assert "<table" in rp_raw_html
    assert "<strong>Тип двигателя</strong>" in rp_raw_html
    assert "<table" in rp_doc_html
    assert "font-weight:700" in rp_doc_html

    eng_html = EngineerTaskCardWidget._markdown_to_html(T34_MARKDOWN)
    assert "<table" in eng_html
    assert "<strong>Тип двигателя</strong>" in eng_html


def test_chat_bubble_geometry_active_tier_and_height(qapp):
    """
    Проверка геометрии бабла ChatMessageWidget в PyQt6:
    - active_tier == 4 при наличии таблицы
    - в text_browser.toHtml() присутствует <table
    - высота text_browser.height() увеличивается под контент таблицы
    """
    scroll = SmoothScrollArea()
    scroll.resize(1200, 700)
    scroll.setWidgetResizable(True)
    scroll.setViewport(ChatBackgroundViewport(scroll))
    cont = QWidget()
    lay = QVBoxLayout(cont)
    lay.setContentsMargins(16, 16, 16, 16)
    scroll.setWidget(cont)
    scroll.show()
    qapp.processEvents()

    # 1. Бабл с одной короткой строкой (без таблицы)
    short_msg = ChatMessageWidget(sender="Элария", text="Привет!", is_user=False, parent=cont)
    lay.addWidget(short_msg)
    qapp.processEvents()
    short_h = short_msg.text_browser.height()

    # 2. Бабл с таблицей Т-34
    table_msg = ChatMessageWidget(sender="Элария", text=T34_MARKDOWN, is_user=False, parent=cont)
    lay.addWidget(table_msg)
    qapp.processEvents()

    # Проверка вычисления active_tier == 4
    # При active_tier == 4 ширина бабла масштабируется до asst_ratio = 0.98
    table_w = table_msg.bubble_container.width()
    available_w = max(260, (scroll.width() // 16) * 16 - 120 - 152)
    expected_max_w = min(int(available_w * 0.98), 1200)
    expected_max_w = (expected_max_w // 16) * 16

    assert table_msg.bubble_container.maximumWidth() == expected_max_w
    assert "<table" in table_msg.text_browser.toHtml()

    # Высота бабла с таблицей существенно больше высоты короткого бабла
    assert table_msg.text_browser.height() > short_h + 40
    assert table_msg.text_browser.height() >= 100
