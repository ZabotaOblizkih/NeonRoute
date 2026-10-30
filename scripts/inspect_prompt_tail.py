import sys
import re
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

builder_path = project_root / "services" / "roleplay_prompt_builder.py"
content = builder_path.read_text(encoding="utf-8")

print("=" * 80)
print(" АНАЛИЗ ВТОРОЙ ПОЛОВИНЫ BUILD(): СПЕЦИАЛЬНЫЕ МЕХАНИКИ И СБОРКА ПРОМПТА")
print("=" * 80)

# Ищем место сразу после Ветки 3 (после identity_anchor одиночного персонажа)
match = re.search(r'(#\s*Обработка авторского system_prompt[\s\S]*?)(?=def |\Z)', content)

if match:
    tail_code = match.group(0)
    lines = tail_code.splitlines()
    print(f"Всего строк в хвосте сборщика: {len(lines)}\n")
    
    # Печатаем ключевые блоки хвоста
    for line in lines:
        print(line)
else:
    # Запасной срез от середины до конца build
    print("Поиск по маркеру не сработал, выводим срез с 300 по 500 строки:")
    all_lines = content.splitlines()
    for l in all_lines[280:450]:
        print(l)

print("\n" + "=" * 80)
