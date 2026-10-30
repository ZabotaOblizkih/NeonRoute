import sys
import re
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

builder_path = project_root / "services" / "roleplay_prompt_builder.py"
content = builder_path.read_text(encoding="utf-8")

print("=" * 80)
print(" ДЕТАЛИЗАЦИЯ ТИПОВ КАРТОЧЕК И ПРИВЯЗАННЫХ СИСТЕМНЫХ ПРОМПТОВ")
print("=" * 80)

# 1. Поиск блока детекции флагов (где определяются is_card_sys, is_env и т.д.)
print("\n[1. УСЛОВИЯ АКТИВАЦИИ ФЛАГОВ (КАК ОПРЕДЕЛЯЕТСЯ ТИП)]:\n")
flag_names = ["is_card_sys", "is_card_cyoa", "is_card_dating", "is_card_dm", "is_env", "is_card_grp"]

for line in content.splitlines():
    if any(f"{f} =" in line or f"{f}:" in line for f in flag_names):
        print(f"  {line.strip()}")

# 2. Поиск блоков промптов для каждой ветки
print("\n" + "=" * 80)
print("[2. СИСТЕМНЫЕ ПРОМПТЫ ДЛЯ КАЖДОГО ТИПА КАРТОЧКИ]:\n")

sections = re.findall(r'(if\s+is_env[\s\S]*?)(?=def |\Z)', content)
if not sections:
    # Ищем блок сборки системного промпта по веткам if is_env / elif is_card_grp
    match = re.search(r'(if\s+is_env:[\s\S]*?)(?=messages\s*=|history\s*=|return\s+|\Z)', content)
    if match:
        full_block = match.group(1)
        # Выводим по веткам
        parts = re.split(r'\n\s*(elif\s+is_card_\w+:|elif\s+is_\w+:|else:)', full_block)
        current_title = "if is_env (Сценарий / Мир)"
        for part in parts:
            if part.startswith("elif") or part.startswith("else"):
                current_title = part.strip()
                print("-" * 80)
                print(f"🔹 ВЕТКА: {current_title}")
                print("-" * 80)
            else:
                # Печатаем первые 400 символов промпта ветки
                lines = [l for l in part.splitlines() if l.strip()]
                preview = "\n".join(lines[:15])
                print(preview)
                print("...\n")
else:
    print(sections[0][:2000])

print("=" * 80)
