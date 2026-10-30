import sys
import re
from pathlib import Path

project_root = Path(".").resolve()

print("=" * 80)
print(" ПОИСК КОНТЕКСТНОГО МЕНЮ АВАТАРКИ В UI")
print("=" * 80)

ui_files = list((project_root / "ui").glob("**/*.py")) if (project_root / "ui").exists() else []
if not ui_files:
    ui_files = list(project_root.glob("*.py"))

matches = []
patterns = [
    r'customContextMenuRequested',
    r'contextMenuEvent',
    r'RoundMenu',
    r'avatar.*menu',
    r'menu.*avatar'
]

for file_path in ui_files:
    try:
        content = file_path.read_text(encoding="utf-8")
        for pat in patterns:
            found = re.findall(rf'.{{0,40}}{pat}.{{0,40}}', content, re.IGNORECASE)
            if found:
                matches.append((file_path.relative_to(project_root), pat, len(found)))
    except Exception:
        continue

if matches:
    # Схлопываем по файлам
    seen_files = {}
    for f, p, count in matches:
        seen_files.setdefault(f, []).append((p, count))
    
    for f, pats in seen_files.items():
        print(f"\n📄 {f}:")
        for p, count in pats:
            print(f"   • Паттерн '{p}': совпадений {count}")
else:
    print("Совпадений в папке ui/ не найдено.")

print("=" * 80)
