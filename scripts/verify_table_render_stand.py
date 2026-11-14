import sys
import re
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtCore import Qt
from qfluentwidgets import SmoothScrollArea

from utils.markdown_table_parser import (
    parse_markdown_tables_to_qt_html,
    contains_markdown_table,
    _calculate_column_percentages,
)
from ui.components.chat_bubble import ChatMessageWidget
from ui.components.rp.rp_chat_bubble import RPChatMessageWidget
from ui.widgets.engineer_task_card import EngineerTaskCardWidget
from ui.components.chat_background import ChatBackgroundViewport

app = QApplication.instance() or QApplication(sys.argv)

T34_MD = """### Тактико-технические характеристики Т-34

| Параметр | Характеристика | Значение |
| :--- | :---: | ---: |
| **Тип двигателя** | Дизельный В-2-34 | 500 л.с. |
| Боевая масса | Средний танк | 30.9 т |
| Запас хода | По шоссе | 300 км |
"""

TWO_COL_MD = """### Основные параметры
| Параметр | Значение |
| :--- | ---: |
| Длина корпуса | 6.68 м |
| Ширина корпуса | 3.00 м |
| Боевая масса | 26.5 т |
"""

print("==================================================================")
print("=== NEONROUTE TABLE RENDERING & HEIGHT STAND ===")
print("==================================================================")

# --- STAGE 1: SSOT PARSER UNIT VERIFICATION ---
print("\n[STAGE 1] Testing SSOT Table Parser...")
t34_html = parse_markdown_tables_to_qt_html(T34_MD)
assert "<table" in t34_html, "HTML table tag not found!"
assert '<th width="' in t34_html, "Column width percentage attribute not found in <th>!"
assert 'align="center"' in t34_html, "Center alignment missing!"
assert 'align="right"' in t34_html, "Right alignment missing!"
assert "<strong>Тип двигателя</strong>" in t34_html or "Тип двигателя" in t34_html
match_t = re.search(r"<table\b.*?</table>", t34_html, flags=re.DOTALL | re.IGNORECASE)
assert match_t is not None
assert "\n" not in match_t.group(0), "Table contains newlines, risking <br> injection!"

# Check percentage distribution for 3 cols
pcts3 = _calculate_column_percentages(
    ["Параметр", "Характеристика", "Значение"],
    [["Тип двигателя", "Дизельный В-2-34", "500 л.с."]],
    3
)
assert sum(pcts3) == 100, f"Percentages sum {sum(pcts3)} != 100"
assert all(p >= 15 for p in pcts3), f"Column percentage too small: {pcts3}"

# Check percentage distribution for 2 cols
pcts2 = _calculate_column_percentages(
    ["Параметр", "Значение"],
    [["Длина корпуса", "6.68 м"]],
    2
)
assert sum(pcts2) == 100, f"Percentages sum {sum(pcts2)} != 100"
print(f"  OK: Column percentages 3-col: {pcts3}, 2-col: {pcts2}")
print("  OK: SSOT Parser correctly generates single-line HTML with proportional <th width>.")

# --- STAGE 2: QT DOCUMENT TABLE LAYOUT & COLUMN METRICS ---
print("\n[STAGE 2] Testing QTextDocument Layout & Metrics...")
from PyQt6.QtGui import QTextDocument
doc = QTextDocument()
doc.setDocumentMargin(6)
w_test = ChatMessageWidget(sender="Элария", text="stub", is_user=False)
doc.setHtml(w_test._markdown_to_html(T34_MD))
for target_width in [600, 780, 900]:
    doc.setTextWidth(target_width)
    h = doc.documentLayout().documentSize().height()
    print(f"  Target Width {target_width}px -> Document Height: {h:.1f}px")
    assert 90 <= h <= 240, f"Document height {h} at width {target_width} out of range [90, 240]"
print("  OK: QTextDocument maintains correct compact height without 1-char word-breaks.")

# --- STAGE 3: COLD CHATMESSAGEWIDGET CREATION (UNMAPPED GEOMETRY) ---
print("\n[STAGE 3] Testing Cold ChatMessageWidget Creation (Fix for 1162px anomaly)...")
w3_cold = ChatMessageWidget(sender="Элария", text=T34_MD, is_user=False)
app.processEvents()
w3_h = w3_cold.text_browser.height()
w3_doc_h = w3_cold.text_browser.document().documentLayout().documentSize().height()
print(f"  3-Col Table (T-34): Widget Height = {w3_h}px, Doc Height = {w3_doc_h:.1f}px")
assert 90 <= w3_h <= 260, f"Cold 3-col widget height {w3_h}px violated [90, 260]!"

w2_cold = ChatMessageWidget(sender="Элария", text=TWO_COL_MD, is_user=False)
app.processEvents()
w2_h = w2_cold.text_browser.height()
w2_doc_h = w2_cold.text_browser.document().documentLayout().documentSize().height()
print(f"  2-Col Table: Widget Height = {w2_h}px, Doc Height = {w2_doc_h:.1f}px")
assert 90 <= w2_h <= 260, f"Cold 2-col widget height {w2_h}px violated [90, 260]!"
print("  OK: Cold start height anomaly completely resolved! (previously 1173px, now within 90..260px)")

# --- STAGE 4: LIVE BUBBLE IN SCROLLAREA & DYNAMIC RESIZING ---
print("\n[STAGE 4] Testing Live Bubble Container in ScrollArea & Resizing...")
scroll = SmoothScrollArea()
scroll.resize(1000, 700)
scroll.setWidgetResizable(True)
scroll.setViewport(ChatBackgroundViewport(scroll))
cont = QWidget()
lay = QVBoxLayout(cont)
lay.setContentsMargins(16, 16, 16, 16)
scroll.setWidget(cont)

live_msg = ChatMessageWidget(sender="Элария", text=T34_MD, is_user=False, parent=cont)
lay.addWidget(live_msg)
scroll.show()
app.processEvents()

live_h = live_msg.text_browser.height()
live_doc_h = live_msg.text_browser.document().documentLayout().documentSize().height()
print(f"  Initial Live Display (1000px): Widget Height = {live_h}px, Doc Height = {live_doc_h:.1f}px")
assert 90 <= live_h <= 260, f"Live widget height {live_h}px violated [90, 260]!"

# Dynamic resize test
for resize_w in [750, 900, 1200]:
    scroll.resize(resize_w, 700)
    app.processEvents()
    curr_h = live_msg.text_browser.height()
    print(f"  Resized Window to {resize_w}px -> Bubble text_browser height: {curr_h}px")
    assert 90 <= curr_h <= 260, f"Height {curr_h}px out of range after resize to {resize_w}px!"
print("  OK: Live resizing retains strictly bounded height [90, 260]px.")

# --- STAGE 5: STREAMING TOKEN INSERTION ---
print("\n[STAGE 5] Testing Streaming Token Insertion...")
stream_w = ChatMessageWidget(sender="Элария", text="", is_user=False)
app.processEvents()
chunks = [
    "### Тактико-технические характеристики Т-34\n\n",
    "| Параметр | Характеристика | Значение |\n",
    "| :--- | :---: | ---: |\n",
    "| **Тип двигателя** | Дизельный В-2-34 | 500 л.с. |\n",
    "| Боевая масса | Средний танк | 30.9 т |\n",
    "| Запас хода | По шоссе | 300 км |\n"
]
for chunk in chunks:
    stream_w.append_stream_chunk(chunk)
    app.processEvents()

stream_h = stream_w.text_browser.height()
print(f"  Streamed Table: Final Widget Height = {stream_h}px")
assert 90 <= stream_h <= 260, f"Streamed widget height {stream_h}px violated [90, 260]!"
print("  OK: Streaming accumulation builds valid table within [90, 260]px.")

print("\n==================================================================")
print(">>> ALL STAND CHECKS PASSED SUCCESSFULLY! <<<")
print("==================================================================")
