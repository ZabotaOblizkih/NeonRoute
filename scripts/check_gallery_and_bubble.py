import re
from pathlib import Path

af_path = Path("ui/controllers/assistant_flow_controller.py")
lines = af_path.read_text(encoding="utf-8", errors="ignore").splitlines()

print("=" * 70)
print("АНАЛИЗ БЛОКА ГАЛЕРЕИ И ЗАЩИТЫ ОТ ЗАГЛУШКИ (строки 895-975)")
print("=" * 70)

# 1. Определение регулярки RE_GALLERY_TRIGGER
print("\n[1] Определение RE_GALLERY_TRIGGER в файле:")
for i, line in enumerate(lines[:120]):
    if "RE_GALLERY_TRIGGER" in line or "GALERY" in line or "GALLERY" in line:
        print(f"   line {i+1:03d}: {line}")

# 2. Детальный код строк 895-975
print("\n[2] Листинг строк 895-975 в assistant_flow_controller.py:")
for idx in range(894, min(len(lines), 975)):
    print(f"{idx+1:04d}: {lines[idx]}")

# 3. Как устроен метод get_text() в ChatMessageBubble
bubble_path = Path("ui/widgets/chat_message_bubble.py")
if bubble_path.exists():
    b_text = bubble_path.read_text(encoding="utf-8", errors="ignore")
    print("\n[3] Методы чтения текста в ChatMessageBubble:")
    for m in re.finditer(r"def\s+(get_text|text|raw_text)\s*\([^)]*\):", b_text):
        print(f"   Найден метод: {m.group(0)}")
else:
    print("\n[3] Файл chat_message_bubble.py не найден по прямому пути.")

print("=" * 70)
