from pathlib import Path

bubble_path = Path("ui/components/chat_bubble.py")
lines = bubble_path.read_text(encoding="utf-8", errors="ignore").splitlines()

print("=" * 70)
print("АНАЛИЗ ВСТРАИВАНИЯ text_browser И thought_spoiler В bubble_layout")
print("=" * 70)

# 1. Продолжение append_stream_chunk (строки 570-620)
print("\n[1] append_stream_chunk — как текст выводится на экран (строки 570-620):")
for idx in range(569, min(len(lines), 620)):
    print(f"{idx+1:04d}: {lines[idx]}")

# 2. Инициализация layout в ChatMessageBubble (__init__ или _init_ui)
print("\n[2] Инициализация структуры bubble_layout:")
for i, line in enumerate(lines):
    if "bubble_layout" in line and ("=" in line or "QVBoxLayout" in line or "QHBoxLayout" in line):
        start = max(0, i - 2)
        end = min(len(lines), i + 25)
        print(f"\n--- Инициализация layout (строки {start+1}-{end+1}) ---")
        for idx in range(start, end):
            print(f"{idx+1:04d}: {lines[idx]}")
        break

# 3. Метод _update_rendered_text (строки 1005-1055)
print("\n[3] _update_rendered_text — продолжение (строки 1005-1055):")
for idx in range(1004, min(len(lines), 1055)):
    print(f"{idx+1:04d}: {lines[idx]}")

print("=" * 70)
