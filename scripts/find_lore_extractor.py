import os
import re
from pathlib import Path

root = Path(".")
patterns = [
    r"lore",
    r"world_info",
    r"правила мира",
    r"лор",
    r"extractor",
    r"memory_extract",
    r"background_task",
    r"secondary",
    r"post_process",
    r"update_lore",
    r"extract_facts"
]
regex = re.compile("|".join(patterns), re.IGNORECASE)

ignore_dirs = {".git", ".pytest_cache", "__pycache__", "venv", ".venv", "env", "node_modules", "PyQt-Fluent-Widgets"}

print("=== ПОИСК МОДУЛЕЙ И ССЫЛОК НА СБОРЩИК ЛОРА/ПАМЯТИ ===")
matches = {}

for p in root.rglob("*.py"):
    if any(part in ignore_dirs for part in p.parts):
        continue
    try:
        text = p.read_text(encoding="utf-8")
        found = []
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line) and not line.strip().startswith("#"):
                found.append((i, line.strip()))
        if found:
            matches[p] = found
    except Exception:
        pass

for p, lines in matches.items():
    print(f"\n[ФАЙЛ]: {p} ({len(lines)} совпадений)")
    for lno, line in lines[:5]:  # Первые 5 строк для контекста
        print(f"  {lno:4d}: {line[:110]}")
    if len(lines) > 5:
        print(f"  ... и еще {len(lines) - 5} строк")

print("\n" + "=" * 60)
