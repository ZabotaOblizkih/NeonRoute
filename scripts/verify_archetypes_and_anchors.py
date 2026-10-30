import sys
import json
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

from services.tavern_parser import TavernParser
from services.roleplay_prompt_builder import RoleplayPromptBuilder

print("=" * 80)
print(" ПРОВЕРКА ВАЛИДАЦИИ: ТЕСТИРОВАНИЕ АРХЕТИПОВ И СИНХРОНИЗАЦИИ ЯКОРЕЙ")
print("=" * 80)

chars_dir = project_root / "characters"
card_files = list(chars_dir.glob("*.json"))

print(f"Всего карточек для проверки: {len(card_files)}\n")

for card_path in card_files:
    try:
        card = TavernParser.parse_file(str(card_path))
        anchor = RoleplayPromptBuilder.build_depth_anchor(card, user_name="Дэниэл", user_query="Привет")
        
        # Определяем ожидаемый тег
        tag = "Чистая проза"
        if "<stats>" in anchor: tag = "<stats>"
        elif "<meters>" in anchor: tag = "<meters>"
        elif "<choices>" in anchor: tag = "<choices>"
        
        print(f"📄 {card_path.name[:25]:<25} | Архетип: {card.archetype_label:<15} | Якорь: {tag}")
    except Exception as e:
        print(f"❌ Ошибка в {card_path.name}: {e}")

print("=" * 80)
