from pathlib import Path
import re

lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8").splitlines()
print("--- Поиск границ парсера ответа модели ---")

start_idx = None
end_idx = None

for idx, line in enumerate(lines):
    if "# 1. Парсинг через теги write_file" in line:
        start_idx = idx
        print(f"[*] Начало парсера: строка {idx+1}: {line.strip()}")
    if start_idx is not None and "write_blocks = clean_blocks" in line:
        end_idx = idx
        print(f"[*] Конец парсера: строка {idx+1}: {line.strip()}")
        break

if start_idx and end_idx:
    print(f"\nРазмер парсера: {end_idx - start_idx} строк (строки {start_idx+1}–{end_idx+1})")
