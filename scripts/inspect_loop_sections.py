import re
from pathlib import Path

lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8").splitlines()
print("[*] Секции внутри цикла while turn < max_turns (строки 628–2246):")

for idx in range(627, 2246):
    line = lines[idx]
    # Ищем разделители и комментарии внутри цикла
    if re.match(r"^\s{12}#\s*[-=]{3,}", line) or re.match(r"^\s{12}#\s*\[[0-9A-Za-zА-Яа-я\s\.\/]+\]", line) or re.match(r"^\s{12}#\s*ШАГ", line, re.I):
        print(f"Строка {idx+1:4d}: {line.strip()}")
    elif line.strip().startswith("if tool_call") or line.strip().startswith("elif tool_call") or "AutoImporter" in line:
        print(f"Строка {idx+1:4d}: >>> {line.strip()[:80]}")
