from pathlib import Path

file_path = Path("services/tool_engine.py")
lines = file_path.read_text(encoding="utf-8").splitlines()
print(f"Всего строк в {file_path}: {len(lines)}")

start = max(0, 500 - 1)
end = min(len(lines), 540)
print(f"\n--- Строки {start + 1}–{end} из {file_path} ---")
for idx in range(start, end):
    print(f"{idx + 1:4d}: {lines[idx]}")
