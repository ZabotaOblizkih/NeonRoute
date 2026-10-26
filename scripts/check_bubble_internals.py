from pathlib import Path

bubble_path = Path("ui/components/chat_bubble.py")
lines = bubble_path.read_text(encoding="utf-8", errors="ignore").splitlines()

print("=" * 70)
print("АНАЛИЗ МЕТОДОВ РЕНДЕРИНГА ТЕКСТА В CHAT_BUBBLE.PY")
print("=" * 70)

# 1. Поиск append_stream_chunk
for i, line in enumerate(lines):
    if "def append_stream_chunk" in line:
        start = max(0, i - 2)
        end = min(len(lines), i + 35)
        print(f"\n[1] Реализация append_stream_chunk (строки {start+1}-{end+1}):")
        for idx in range(start, end):
            print(f"{idx+1:04d}: {lines[idx]}")
        break

# 2. Поиск get_or_create_thought_spoiler и структуры layout
for i, line in enumerate(lines):
    if "def get_or_create_thought_spoiler" in line:
        start = max(0, i - 2)
        end = min(len(lines), i + 35)
        print(f"\n[2] Создание и размещение thought_spoiler (строки {start+1}-{end+1}):")
        for idx in range(start, end):
            print(f"{idx+1:04d}: {lines[idx]}")
        break

# 3. Как устроен _update_rendered_text
for i, line in enumerate(lines):
    if "def _update_rendered_text" in line:
        start = max(0, i - 2)
        end = min(len(lines), i + 35)
        print(f"\n[3] Метод _update_rendered_text (строки {start+1}-{end+1}):")
        for idx in range(start, end):
            print(f"{idx+1:04d}: {lines[idx]}")
        break

print("=" * 70)
