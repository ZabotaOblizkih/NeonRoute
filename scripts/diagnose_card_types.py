import sys
import json
from pathlib import Path

project_root = Path(".").resolve()
sys.path.insert(0, str(project_root))

print("=" * 80)
print(" ДИАГНОСТИКА: ПОДДЕРЖИВАЕМЫЕ ТИПЫ И АРХЕТИПЫ КАРТОЧЕК В NEONROUTE")
print("=" * 80)

# 1. Анализ кода парсера
parser_file = project_root / "services" / "tavern_parser.py"
print("\n[1. АНАЛИЗ ПАРСЕРА services/tavern_parser.py]")
if parser_file.exists():
    content = parser_file.read_text(encoding="utf-8")
    has_v2 = "chara_card_v2" in content or "data" in content
    has_book = "character_book" in content or "lorebook" in content
    has_alt = "alternate_greetings" in content
    print(f"• Файл найден:             ДА ({len(content)} байт)")
    print(f"• Поддержка Tavern V2:     {'ДА' if has_v2 else 'НЕТ (только V1)'}")
    print(f"• Чтение Character Book:   {'ДА' if has_book else 'НЕТ'}")
    print(f"• Alternate Greetings:     {'ДА' if has_alt else 'НЕТ'}")
else:
    print("❌ services/tavern_parser.py не найден!")

# 2. Сканирование установленных карточек в папке characters/
chars_dir = project_root / "characters"
print(f"\n[2. СКАНИРОВАНИЕ КАРТОЧЕК В {chars_dir}]")

if not chars_dir.exists():
    print(f"❌ Директория {chars_dir} не найдена!")
    sys.exit(0)

card_files = list(chars_dir.glob("*.json"))
print(f"Найдено JSON-файлов: {len(card_files)}\n")

for card_path in card_files:
    try:
        with open(card_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        is_v2 = "spec" in data and data.get("spec") == "chara_card_v2"
        inner = data.get("data", data) if is_v2 else data
        
        name = inner.get("name", card_path.stem)
        tags = inner.get("tags", [])
        scenario = inner.get("scenario", "")
        desc = inner.get("description", "")
        has_lorebook = "character_book" in inner or "character_book" in data
        
        # Эвристика типа
        text_corpus = f"{name} {' '.join(tags)} {scenario} {desc}".lower()
        
        detected_type = "SOLO (Одиночная)"
        if any(w in text_corpus for w in ["rpg", "isekai", "stats", "litrpg", "мудрец", "статус"]):
            detected_type = "ISEKAI / LITRPG"
        elif any(w in text_corpus for w in ["party", "отряд", "группа", "класс 2-a", "одноклассник"]):
            detected_type = "GROUP / PARTY"
        elif any(w in text_corpus for w in ["detective", "mystery", "убийство", "расследование", "нуар"]):
            detected_type = "DETECTIVE"
            
        print(f"📄 {card_path.name}")
        print(f"   • Имя в игре:    {name}")
        print(f"   • Спецификация:  {'TavernCard V2' if is_v2 else 'TavernCard V1 (Legacy)'}")
        print(f"   • Встроенный лор:{'Есть (character_book)' if has_lorebook else 'Нет'}")
        print(f"   • Теги карточки: {tags if tags else '(нет тегов)'}")
        print(f"   • Распознано как:{detected_type}")
        print("-" * 60)
        
    except Exception as e:
        print(f"❌ Ошибка чтения {card_path.name}: {e}")

print("=" * 80)
