import re
from pathlib import Path

print("=" * 70)
print("ПОИСК И АНАЛИЗ ВИДЖЕТОВ БАБЛА И МЕХАНИЗМА ВЫВОДА ТЕКСТА/МЫСЛЕЙ")
print("=" * 70)

# 1. Ищем файлы, содержащие append_thought_chunk или ThoughtSpoiler
root = Path(".")
matching_files = []
for p in root.rglob("*.py"):
    if ".git" in p.parts or "__pycache__" in p.parts or "venv" in p.parts:
        continue
    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
        if "append_thought_chunk" in content or "ThoughtSpoiler" in content or "finalize_thought" in content:
            matching_files.append((p, content))
    except Exception:
        pass

print("\n[1] Файлы с реализацией thought-виджетов:")
for p, _ in matching_files:
    print(f"   -> {p}")

# 2. Анализируем реализацию методов в найденных файлах
for p, content in matching_files:
    if "ui" in p.parts and ("widget" in p.name.lower() or "bubble" in p.name.lower() or "thought" in p.name.lower()):
        print(f"\n[2] Анализ виджета: {p}")
        lines = content.splitlines()
        for i, l in enumerate(lines):
            if any(k in l for k in ["def append_thought_chunk", "def finalize_thought", "def set_thought_data", "def set_text"]):
                start = max(0, i)
                end = min(len(lines), i + 25)
                print(f"\n--- {l.strip()} ({p.name}:{i+1}) ---")
                for idx in range(start, end):
                    print(f"{idx+1:04d}: {lines[idx]}")

# 3. Смотрим метод _flush_stream_buffer в chat_flow_controller.py
cf_path = Path("ui/controllers/chat_flow_controller.py")
if cf_path.exists():
    cf_lines = cf_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    print("\n[3] Реализация _flush_stream_buffer в chat_flow_controller.py:")
    for i, l in enumerate(cf_lines):
        if "def _flush_stream_buffer" in l:
            start = i
            end = min(len(cf_lines), i + 35)
            for idx in range(start, end):
                print(f"{idx+1:04d}: {cf_lines[idx]}")
            break

print("=" * 70)
