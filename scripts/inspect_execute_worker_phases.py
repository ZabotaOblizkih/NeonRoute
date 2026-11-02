import re
from pathlib import Path

lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8").splitlines()

print(f"[*] Сигнатура execute_worker (строка 185):")
print(f"    {lines[184].strip()}")
for idx in range(185, 200):
    if lines[idx].strip().endswith(":"):
        print(f"    {lines[idx].strip()}")
        break
    print(f"    {lines[idx].strip()}")

print("\n[*] Карта ключевых фаз и заголовков внутри execute_worker:")
pattern = re.compile(r"^\s{8}(#\s*[=0-9А-ЯA-Z\-\[].*|if\s+.*:|for\s+.*:|while\s+.*:)")
for idx in range(185, 2246):
    line = lines[idx]
    # Ловим комментарии-разделители и ключевые условия верхнего уровня метода (отступ 8 пробелов)
    if re.match(r"^\s{8}#\s*[-=]{3,}", line) or re.match(r"^\s{8}#\s*[Фф]аза", line) or re.match(r"^\s{8}#\s*===+", line):
        print(f"Строка {idx+1:4d}: {line.strip()}")
    elif re.match(r"^\s{8}#\s*\[.+\]", line):
        print(f"Строка {idx+1:4d}: {line.strip()}")
    elif line.strip().startswith("for step in range") or line.strip().startswith("while "):
        print(f"Строка {idx+1:4d}: >>> ЦИКЛ: {line.strip()}")
