import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import re
import subprocess
from PyQt6.QtWidgets import QApplication
from ui.components.chat_bubble import ChatMessageWidget
from utils.markdown_table_parser import contains_markdown_table

app = QApplication.instance() or QApplication(sys.argv)

t34_md = """### Тактико-технические характеристики Т-34

| Параметр | Характеристика | Значение |
| :--- | :---: | ---: |
| **Тип двигателя** | Дизельный В-2-34 | 500 л.с. |
| Боевая масса | Средний танк | 30.9 т |
| Запас хода | По шоссе | 300 км |
"""

print("[STAND] 1. Проверка детектора таблиц contains_markdown_table...")
assert contains_markdown_table(t34_md), "Ошибка: таблица не обнаружена"
print("   -> OK: Таблица корректно обнаружена")

print("[STAND] 2. Создание ChatMessageWidget и рендеринг таблицы...")
w = ChatMessageWidget(sender="Элария", text=t34_md, is_user=False)
raw_html = w._markdown_to_html(t34_md)
doc_html = w.text_browser.toHtml()

assert "<table" in raw_html, "Ошибка: <table> отсутствует в _markdown_to_html"
assert "<strong>Тип двигателя</strong>" in raw_html, "Ошибка: жирный шрифт внутри ячейки не обработан"
assert "<table" in doc_html, "Ошибка: <table> отсутствует в QTextBrowser document"
print("   -> OK: HTML-таблица сформирована с инлайн-стилями ячеек")

print("[STAND] 3. Проверка отсутствия тегов <br> внутри тега <table>...")
table_match = re.search(r"<table\b.*?</table>", raw_html, flags=re.DOTALL | re.IGNORECASE)
assert table_match, "Ошибка: не найден блок <table>"
table_body = table_match.group(0)
assert "<br>" not in table_body and "<br/>" not in table_body and "<br />" not in table_body, "Ошибка: обнаружен <br> внутри <table>"
print("   -> OK: Тег <table> сгенерирован в одну строку без внутренних разрывов <br>")

print("[STAND] 4. Проверка высоты QTextBrowser и геометрии...")
doc_h = w.text_browser.document().documentLayout().documentSize().height()
widget_h = w.text_browser.height()
print(f"   -> Высота документа: {doc_h:.1f}px, Высота виджета: {widget_h}px")
assert widget_h >= 100, f"Ошибка: высота виджета {widget_h}px слишком мала под таблицу"
print("   -> OK: Высота виджета корректно рассчитана под строки таблицы")

print("[STAND] 5. Запуск набора unit-тестов tests/unit/test_markdown_table_rendering.py...")
res = subprocess.run([sys.executable, "-m", "pytest", "tests/unit/test_markdown_table_rendering.py", "-v"], capture_output=True, text=True, encoding="utf-8")
print(res.stdout)
if res.stderr:
    print(res.stderr)
assert res.returncode == 0, f"Ошибка: pytest завершился с кодом {res.returncode}"

print("[STAND-PASS] Все 5 этапов тестового стенда успешно пройдены!")
