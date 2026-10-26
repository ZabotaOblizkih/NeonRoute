import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

# 1. Смотрим, как assistant_flow_controller вызывает set_thought_data и set_text
af_path = root_dir / "ui" / "controllers" / "assistant_flow_controller.py"
af_lines = af_path.read_text(encoding="utf-8", errors="ignore").splitlines()

print("=" * 70)
print("1. ВЫЗОВЫ set_text И set_thought_data В ASSISTANT_FLOW_CONTROLLER")
print("=" * 70)
for idx in range(975, min(len(af_lines), 998)):
    print(f"{idx+1:04d}: {af_lines[idx]}")

# 2. Сквозной UI-тест ChatMessageWidget + ThoughtSpoilerWidget
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from ui.components.chat_bubble import ChatMessageWidget

print("\n" + "=" * 70)
print("2. СКВОЗНОЙ ТЕСТ ВИДЖЕТА ChatMessageWidget")
print("=" * 70)

bubble = ChatMessageWidget(
    sender="Элария",
    text="",
    is_user=False
)

print(f"Бабл создан. text_browser={getattr(bubble, 'text_browser', None) is not None}")

# Симулируем стриминг мыслей
thought_chunks = [
    "Факты из текста: Система NeonRoute v0.9.0-beta.\n",
    "Вывод: Ответить по существу."
]
for ch in thought_chunks:
    bubble.append_thought_chunk(ch)

spoiler = getattr(bubble, "thought_spoiler", None)
print(f"Спойлер после append_thought_chunk: created={spoiler is not None}")
if spoiler:
    print(f"  - text_browser спойлера: {repr(spoiler.text_browser.toPlainText())}")

# Завершаем мысли
bubble.finalize_thought(duration_sec=2.4, token_count=45)
if spoiler:
    print(f"Спойлер после finalize_thought: is_collapsed={spoiler._is_collapsed}, metrics={repr(spoiler.metrics_label.text())}")

# Симулируем стриминг чистового ответа
normal_chunks = ["Да, ", "NeonRoute — ", "это оркестратор."]
for ch in normal_chunks:
    bubble.append_stream_chunk(ch)

main_tb = getattr(bubble, "text_browser", None)
print(f"Основной text_browser во время стриминга: {repr(main_tb.toPlainText() if main_tb else None)}")

# Проверяем, что делает set_text и set_thought_data при финализации
bubble.set_text("Да, NeonRoute — это оркестратор.")
bubble.set_thought_data(
    text="Факты из текста: Система NeonRoute v0.9.0-beta.\nВывод: Ответить по существу.",
    duration=2.4,
    token_count=45
)

print("\nИТОГОВОЕ СОСТОЯНИЕ ВИДЖЕТОВ:")
print(f"  A. Основной text_browser: {repr(bubble.text_browser.toPlainText() if bubble.text_browser else None)}")
print(f"     Видимость: {bubble.text_browser.isVisible() if bubble.text_browser else False}")
if spoiler:
    print(f"  B. text_browser спойлера мыслей: {repr(spoiler.text_browser.toPlainText())}")
    print(f"     Видимость спойлера: {spoiler.isVisible()}, свернут: {spoiler._is_collapsed}")
print("=" * 70)

