import sys
from pathlib import Path

# Гарантируем видимость корня проекта C:\NeonRoute для импорта services
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import json
from services.tavern_parser import TavernParser
from services.roleplay_prompt_builder import RoleplayPromptBuilder

print("=" * 80)
print(" ТЕСТОВЫЙ СТЕНД: АНАЛИЗ ПРОМПТА И ВХОДЯЩЕГО КОНТЕКСТА РОЛЕВОГО РЕЖИМА")
print("=" * 80)

# 1. Загрузка и аудит карточки
card_file = project_root / "characters" / "Isekai RPG.json"
if not card_file.exists():
    fallback_cards = list((project_root / "characters").glob("*.json"))
    if not fallback_cards:
        print("❌ ОШИБКА: Карточки в папке characters не найдены!")
        sys.exit(1)
    card_file = fallback_cards[0]

print(f"\n[1] КАРТОЧКА: {card_file.name}")
card = TavernParser.parse_character_card(str(card_file))

card_fields = {
    "Имя": card.name,
    "Архетип": getattr(card, "archetype", "Не указан"),
    "Description (Описание)": card.description or "",
    "Personality (Личность)": card.personality or "",
    "Scenario (Сценарий)": card.scenario or "",
    "System Prompt (Промпт автора)": getattr(card, "system_prompt", "") or "",
    "First Message (Приветствие)": card.first_mes or "",
    "Mes Example (Примеры диалога)": getattr(card, "mes_example", "") or "",
}

for field, val in card_fields.items():
    preview = (val[:90] + "...") if len(val) > 90 else (val or "<ПУСТО>")
    print(f"  • {field:30s} | Длина: {len(val):5d} симв. | {preview}")

# 2. Исследование методов RoleplayPromptBuilder
print("\n[2] МЕТОДЫ RoleplayPromptBuilder:")
builder_methods = [m for m in dir(RoleplayPromptBuilder) if not m.startswith("__")]
for m in builder_methods:
    print(f"  • {m}")

# 3. Сборка системного промпта
print("\n[3] ГЕНЕРАЦИЯ СИСТЕМНОГО ПРОМПТА ЧЕРЕЗ RoleplayPromptBuilder:")

built_sys_prompt = ""
# Ищем метод сборки системного промпта
for candidate in ("build_system_prompt", "get_system_prompt", "build_prompt", "build_roleplay_prompt"):
    if hasattr(RoleplayPromptBuilder, candidate):
        try:
            fn = getattr(RoleplayPromptBuilder, candidate)
            # Пробуем вызвать с карточкой
            import inspect
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())
            if len(params) == 1:
                built_sys_prompt = fn(card)
            elif "character" in params or "card" in params:
                built_sys_prompt = fn(card=card) if "card" in params else fn(character=card)
            else:
                built_sys_prompt = fn(card)
            print(f"  Успешно вызван метод: {candidate}(...) -> Получено {len(built_sys_prompt)} символов.")
            break
        except Exception as e:
            print(f"  Попытка вызова {candidate} завершилась: {e}")

if not built_sys_prompt:
    # Попробуем проинспектировать исходник
    p_code = Path("services/roleplay_prompt_builder.py").read_text(encoding="utf-8")
    print("  Анализ сигнатур в services/roleplay_prompt_builder.py...")
    for line in p_code.splitlines():
        if "def " in line:
            print(f"    {line.strip()}")

# 4. Проверка наполнения: что вошло, а что потерялось
if built_sys_prompt:
    print("\n[4] АУДИТ НАПОЛНЕНИЯ СИСТЕМНОГО ПРОМПТА:")
    checks = {
        "Базовые правила отыгрыша (Base RP Rules)": any(k in built_sys_prompt.lower() for k in ("ролевой", "персонаж", "диалог", "правила", "roleplay", "character")),
        "Имя персонажа": card.name.lower() in built_sys_prompt.lower(),
        "Description карточки": bool(card.description and card.description[:40] in built_sys_prompt),
        "Personality карточки": bool(card.personality and card.personality[:40] in built_sys_prompt),
        "Scenario карточки": bool(card.scenario and card.scenario[:40] in built_sys_prompt),
        "Авторский System Prompt карточки": bool(getattr(card, "system_prompt", None) and card.system_prompt[:40] in built_sys_prompt),
        "Форматирование статусов/тегов (<stats>, <thought>)": any(k in built_sys_prompt for k in ("<stats>", "<thought>", "<meters>"))
    }

    for label, ok in checks.items():
        status = "✅ ВКЛЮЧЕНО" if ok else "❌ ОТСУТСТВУЕТ / ПОТЕРЯНО"
        print(f"  {status:25s} | {label}")

    print("\n" + "=" * 80)
    print(" [5] ПОЛНЫЙ ТЕКСТ СИСТЕМНОГО ПРОМПТА, УХОДЯЩЕГО В МОДЕЛЬ:")
    print("=" * 80)
    print(built_sys_prompt)
    print("=" * 80)
