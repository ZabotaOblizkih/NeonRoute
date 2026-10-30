import sys
import inspect
import re
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" АНАЛИЗ СИСТЕМНЫХ ПРОМПТОВ И УСЛОВИЙ ВЫЗОВА В NEONROUTE")
print("=" * 80)

builder_path = project_root / "services" / "roleplay_prompt_builder.py"

if not builder_path.exists():
    print(f"❌ Файл {builder_path} не найден!")
    sys.exit(1)

content = builder_path.read_text(encoding="utf-8")

# 1. Проверяем внешние файлы промптов, если есть папка prompts/
prompts_dir = project_root / "prompts"
if prompts_dir.exists():
    print(f"\n[1. ФАЙЛЫ В ПАПКЕ {prompts_dir}]:")
    p_files = list(prompts_dir.glob("*.*"))
    if p_files:
        for pf in p_files:
            print(f"  • {pf.name} ({pf.stat().st_size} байт)")
    else:
        print("  (папка пуста)")
else:
    print("\n[1. ВНЕШНЯЯ ПАПКА prompts/ НЕ ОБНАРУЖЕНА — ВСЁ В КОДЕ]")

# 2. Поиск строковых констант и шаблонов промптов в коде
print("\n[2. СИСТЕМНЫЕ ШАБЛОНЫ / КОНСТАНТЫ В roleplay_prompt_builder.py]:")

# Ищем константы CAPS_LOCK, словари или переменные с промптами
constants = re.findall(r'([A-Z0-9_]{3,}\s*:\s*str\s*=\s*(?:"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"]*"|\'[^\']*\'))', content)
if not constants:
    constants = re.findall(r'([A-Z0-9_]{3,}\s*=\s*(?:"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'))', content)

for c in constants:
    name = c.split("=")[0].split(":")[0].strip()
    preview = c.strip()[:200].replace("\n", " ")
    print(f"\n🔹 {name}:")
    print(f"   {preview}...")

# 3. Анализ метода build() и условий диспетчеризации (if / elif / match / get)
print("\n" + "=" * 80)
print("[3. ЛОГИКА ОПРЕДЕЛЕНИЯ ТИПА КАРТОЧКИ И УСЛОВИЯ ВЫЗОВА (DISPATCH)]: ")
print("=" * 80)

# Вытаскиваем метод build целиком
build_match = re.search(r'def build\s*\([\s\S]*?\n(?=\s{0,4}def |\Z)', content)
if build_match:
    build_code = build_match.group(0)
    print(f"• Метод build() найден ({len(build_code)} симв.).")
    
    # Ищем строки с ветвлениями, проверками тегов, имен и типов
    branches = []
    for line in build_code.splitlines():
        if any(kw in line for kw in ["if ", "elif ", "match ", "case ", "card_type", "archetype", "tags", "mode", "system_prompt"]):
            branches.append(line)
            
    if branches:
        print("\nКлючевые ветвления и условия в build():")
        for b in branches[:30]:
            print(f"  {b}")
    else:
        print("\nЯвных ветвлений if/elif по типам внутри build() не найдено. Выводим тело сборки:")
        print(build_code[:1500])
else:
    print("❌ Метод build() не найден по регулярке. Показываем все методы класса:")
    methods = re.findall(r'def\s+([a-zA-Z0-9_]+)\s*\(', content)
    print("Методы:", methods)

print("\n" + "=" * 80)
