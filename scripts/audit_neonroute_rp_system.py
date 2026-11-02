import json
import os
import re
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")

print("=" * 80)
print(" АУДИТ АРХИТЕКТУРЫ NEONROUTE: СИСТЕМНЫЙ ПРОМПТ, ЯКОРЯ, КАРТОЧКИ И КОНФИГ")
print("=" * 80)

# 1. АУДИТ CONFIG.JSON
config_path = ROOT / "config.json"
print("\n[1. ПРОВЕРКА CONFIG.JSON]")
if config_path.exists():
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        print("• Найден config.json")
        # Ищем секции с параметрами генерации / roleplay
        for key in ["roleplay", "generation", "samplers", "llm", "defaults", "modes"]:
            if key in cfg:
                print(f"  Раздел '{key}':\n  {json.dumps(cfg[key], indent=4, ensure_ascii=False)}")
        # Проверяем плоские ключи температуры
        flat_keys = {k: v for k, v in cfg.items() if any(p in k.lower() for p in ["temp", "top_p", "min_p", "xtc", "dry"])}
        if flat_keys:
            print(f"  Специфичные ключи генерации: {flat_keys}")
    except Exception as e:
        print(f"  Ошибка чтения config.json: {e}")
else:
    print("❌ config.json не найден в корне.")

# 2. ПОИСК ФАЙЛОВ, ОТВЕЧАЮЩИХ ЗА КАРТОЧКИ, ПРОМПТ И ЯКОРЯ
print("\n[2. АНАЛИЗ КОДОВОЙ БАЗЫ]")
py_files = list(ROOT.rglob("*.py"))
print(f"Всего файлов .py в проекте: {len(py_files)}")

# А. Ищем обработку mes_example
print("\n--- 2.A. Поддержка mes_example (примеры диалогов) ---")
mes_example_matches = []
for f in py_files:
    try:
        txt = f.read_text(encoding="utf-8", errors="ignore")
        if "mes_example" in txt or "example_dialogue" in txt:
            mes_example_matches.append(f)
    except Exception:
        pass

if mes_example_matches:
    print(f"Поле 'mes_example' упоминается в {len(mes_example_matches)} файлах:")
    for f in mes_example_matches:
        print(f"  • {f.relative_to(ROOT)}")
        txt = f.read_text(encoding="utf-8", errors="ignore")
        for line in txt.splitlines():
            if "mes_example" in line:
                print(f"      {line.strip()[:100]}")
else:
    print("❌ Поле 'mes_example' ВООБЩЕ НЕ ВСТРЕЧАЕТСЯ в кодовой базе. Парсер полностью игнорирует примеры диалогов.")

# Б. Ищем реализацию TavernParser
print("\n--- 2.B. Изучение TavernParser ---")
tavern_parser_files = [f for f in py_files if "tavern" in f.name.lower() or "card" in f.name.lower()]
for f in tavern_parser_files:
    txt = f.read_text(encoding="utf-8", errors="ignore")
    if "class TavernParser" in txt or "def parse" in txt:
        print(f"• Файл: {f.relative_to(ROOT)}")
        # Выведем список извлекаемых полей
        fields = re.findall(r'(\w+)\s*=\s*(?:data|card|json_data)\.get\([\'"](\w+)[\'"]', txt)
        if fields:
            print(f"  Извлекаемые поля карточки: {[field[1] for field in fields]}")

# В. Изучение RoleplayPromptBuilder
print("\n--- 2.C. Изучение RoleplayPromptBuilder ---")
prompt_builder_files = [f for f in py_files if "prompt" in f.name.lower() and "roleplay" in f.name.lower()]
for f in prompt_builder_files:
    print(f"• Файл: {f.relative_to(ROOT)}")
    txt = f.read_text(encoding="utf-8", errors="ignore")
    # Ищем метод сборки системного промпта
    methods = re.findall(r'def\s+([a-zA-Z0-9_]+)\s*\(self[^)]*\):', txt)
    print(f"  Методы класса: {methods[:10]}")
    # Проверяем, есть ли готовые пресеты или анти-годмодинг
    for keyword in ["godmod", "запрещено", "never speak for", "slow burn", "кинематограф"]:
        matches = [l.strip() for l in txt.splitlines() if keyword.lower() in l.lower()]
        if matches:
            print(f"  Найдено совпадение по '{keyword}':")
            for m in matches[:2]:
                print(f"    -> {m[:100]}")

# Г. Изучение механизма якорей (Depth-0 / RPFlow)
print("\n--- 2.D. Изучение инжектора якоря (RoleplayFlow / Anchor) ---")
flow_files = [f for f in py_files if "flow" in f.name.lower() or "anchor" in f.name.lower()]
for f in flow_files:
    txt = f.read_text(encoding="utf-8", errors="ignore")
    if "DepthAnchor" in txt or "depth" in txt.lower() and "anchor" in txt.lower():
        print(f"• Файл: {f.relative_to(ROOT)}")
        # Ищем, как якорь прикрепляется к контексту
        for i, line in enumerate(txt.splitlines()):
            if "DepthAnchor" in line or "depth_0" in line.lower() or "active_anchor" in line.lower():
                print(f"  L{i+1}: {line.strip()[:110]}")

print("\n" + "=" * 80)
