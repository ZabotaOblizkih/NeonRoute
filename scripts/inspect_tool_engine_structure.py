import re
from pathlib import Path

file_path = Path("services/tool_engine.py")
content = file_path.read_text(encoding="utf-8")
lines = content.splitlines()

non_empty = [l for l in lines if l.strip()]
print(f"[*] Всего строк в файле: {len(lines)}")
print(f"[*] Строк с реальным кодом/комментариями: {len(non_empty)}")
print(f"[*] Доля мусорных пустых строк: {(len(lines) - len(non_empty)) / len(lines):.1%}")

# Поиск классов и функций верхнего уровня
print("\n[*] Классы и методы верхнего уровня:")
for idx, line in enumerate(lines):
    if line.startswith("class ") or (line.startswith("def ") and not line.startswith("    ")):
        print(f"    Строка {idx+1:4d}: {line.strip()}")

# Поиск вызовов check_artifact_substance
print("\n[*] Поиск вызовов check_artifact_substance в проекте:")
for py_file in Path("services").glob("*.py"):
    text = py_file.read_text(encoding="utf-8")
    if "check_artifact_substance" in text:
        matches = [m.start() for m in re.finditer(r"check_artifact_substance", text)]
        print(f"    {py_file}: {len(matches)} вхождений")
