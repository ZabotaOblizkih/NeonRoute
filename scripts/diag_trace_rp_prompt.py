import json
from pathlib import Path
from services.tavern_parser import TavernParser
from services.roleplay_prompt_builder import RoleplayPromptBuilder

print("=== 1. СОДЕРЖИМОЕ И СТРУКТУРА КАРТОЧКИ (Isekai RPG.json) ===")
card_path = Path("characters/Isekai RPG.json")
if not card_path.exists():
    # Попробуем найти любую доступную карточку
    cards = list(Path("characters").glob("*.json"))
    card_path = cards[0] if cards else None

if card_path:
    print(f"Файл: {card_path.name}")
    card = TavernParser.parse_character_card(str(card_path))
    print(f"  Имя персонажа: '{card.name}'")
    print(f"  Архетип: '{getattr(card, 'archetype', 'Не указан')}'")
    print(f"  Длина description: {len(card.description or '')} симв.")
    print(f"  Длина personality: {len(card.personality or '')} симв.")
    print(f"  Длина scenario: {len(card.scenario or '')} симв.")
    print(f"  Длина system_prompt (промпт автора): {len(getattr(card, 'system_prompt', '') or '')} симв.")
    print(f"  Длина first_mes: {len(card.first_mes or '')} симв.")
    print(f"  Длина visual_dna: {len(getattr(card, 'visual_dna', '') or '')} симв.")
    if getattr(card, 'system_prompt', None):
        print(f"\n--- Авторский system_prompt из карточки ---\n{card.system_prompt[:300]}...")

print("\n=== 2. ЛОГИКА СБОРКИ В RoleplayPromptBuilder ===")
p_rpb = Path("services/roleplay_prompt_builder.py")
if p_rpb.exists():
    lines = p_rpb.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines):
        if any(k in l for k in ("def build_system_prompt", "def get_system_prompt", "def build_rp_prompt")):
            for j in range(i, min(len(lines), i + 60)):
                print(f"{j+1:3d}: {lines[j]}")
            break

print("\n=== 3. РЕЗУЛЬТАТ СБОРКИ СИСТЕМНОГО ПРОМПТА ДЛЯ КАРТОЧКИ ===")
if card_path:
    try:
        # Пытаемся вызвать построитель системного промпта
        if hasattr(RoleplayPromptBuilder, "build_system_prompt"):
            sys_prompt = RoleplayPromptBuilder.build_system_prompt(card)
        elif hasattr(RoleplayPromptBuilder, "get_roleplay_system_prompt"):
            sys_prompt = RoleplayPromptBuilder.get_roleplay_system_prompt(card)
        else:
            sys_prompt = "Не найден стандартный метод вызова"

        print(f"Итоговый размер системного промпта: {len(sys_prompt)} симв.")
        print("\n--- Начало системного промпта (первые 600 симв.) ---")
        print(sys_prompt[:600])
        print("\n--- Конец системного промпта (последние 600 симв.) ---")
        print(sys_prompt[-600:])
    except Exception as e:
        print(f"Ошибка вызова построителя: {e}")
