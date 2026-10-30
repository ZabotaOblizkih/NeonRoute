import sys
import re
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" АНАЛИЗ ВСЕХ ВЕТОК СИСТЕМНЫХ ПРОМПТОВ И ДЕТЕКЦИИ ТИПОВ В TAVERN_PARSER")
print("=" * 80)

# 1. Как tavern_parser определяет флаги карточки
parser_path = project_root / "services" / "tavern_parser.py"
if parser_path.exists():
    p_content = parser_path.read_text(encoding="utf-8")
    print("\n[1. ЛОГИКА ДЕТЕКЦИИ ФЛАГОВ В services/tavern_parser.py]:")
    flag_lines = []
    for line in p_content.splitlines():
        if any(f in line for f in ["is_environment", "is_group", "is_system", "is_dating", "is_cyoa", "is_dm"]):
            flag_lines.append(line.strip())
    for fl in flag_lines[:25]:
        print(f"  {fl}")
else:
    print("❌ services/tavern_parser.py не найден")

# 2. Все ветки системных промптов в roleplay_prompt_builder.py
builder_path = project_root / "services" / "roleplay_prompt_builder.py"
b_content = builder_path.read_text(encoding="utf-8")

print("\n" + "=" * 80)
print("[2. ПОЛНЫЕ ТЕКСТЫ СИСТЕМНЫХ ПРОМПТОВ ПО ТИПАМ КАРТОЧЕК]:")
print("=" * 80)

# Ищем все ветки identity_anchor / bot_grammar
branches = re.findall(r'(#\s*\d+\.\s*ВЕТКА[\s\S]*?)(?=#\s*\d+\.\s*ВЕТКА|#\s*Обработка авторского|\Z)', b_content)

if branches:
    for i, b in enumerate(branches, 1):
        print(f"\n--- ВЕТКА {i} ---")
        # Печатаем первые строки каждой ветки
        clean_lines = [l for l in b.splitlines() if l.strip()]
        print("\n".join(clean_lines[:25]))
else:
    # Запасной поиск по elif
    match = re.search(r'(if is_env:[\s\S]*?)(?=card_custom_sys\s*=|\Z)', b_content)
    if match:
        print(match.group(1)[:3000])

print("\n" + "=" * 80)
