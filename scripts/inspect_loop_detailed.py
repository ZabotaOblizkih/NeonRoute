import re
from pathlib import Path

lines = Path("services/subagent_engineer.py").read_text(encoding="utf-8").splitlines()
print(f"[*] Сканирование комментариев внутри цикла (строки 628–2246):")

for idx in range(627, 2246):
    line = lines[idx]
    stripped = line.strip()
    if stripped.startswith("#") and not stripped.startswith("# ---") and len(stripped) > 5:
        # Показываем только содержательные заголовки блоков
        if any(w in stripped.lower() for w in ("шаг", "фаза", "проверка", "вызов", "инструмент", "валидац", "ошибк", "успех", "откат", "генерац", "healing", "zero-token", "ast", "run", "substance")):
            print(f"Строка {idx+1:4d} (отступ {len(line) - len(line.lstrip()):2d}): {stripped}")
