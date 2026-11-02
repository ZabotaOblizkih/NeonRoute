import re
from pathlib import Path

lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8").splitlines()

print(f"[*] Анализ структуры цикла while turn < max_turns (строки 628–2246):")
blocks = []
current_block = ("Начало цикла", 628)

for idx in range(627, 2246):
    line = lines[idx]
    # Ловим ключевые этапы с отступом 12 пробелов (прямые дети while turn < max_turns)
    if re.match(r"^\s{12}#\s*===+", line) or re.match(r"^\s{12}#\s*[0-9]\.", line):
        blocks.append((current_block[0], current_block[1], idx))
        current_block = (line.strip(), idx + 1)
    elif re.match(r"^\s{12}(if|elif|try:|for)\s+", line) and "tool" in line.lower():
        if idx - current_block[1] > 40:
            blocks.append((current_block[0], current_block[1], idx))
            current_block = (line.strip()[:60], idx + 1)

blocks.append((current_block[0], current_block[1], 2246))

print(f"\n{'Блок / Фаза итерации':<55} | {'Строки':<12} | {'Объем':<8}")
print("-" * 80)
for name, start, end in blocks:
    size = end - start
    if size > 15:
        print(f"{name:<55} | {start:4d}–{end:4d}  | {size:4d} стр.")
