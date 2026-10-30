import json
from pathlib import Path

card_path = Path("characters/Isekai RPG.json")
if not card_path.exists():
    print("❌ Файл не найден!")
    exit()

data = json.loads(card_path.read_text(encoding="utf-8"))
card = data.get("data", data)

print("=" * 80)
print(f" АНАТОМИЯ КАРТОЧКИ: {card.get('name')}")
print("=" * 80)

print(f"\n[1. SYSTEM PROMPT КАРТОЧКИ] ({len(card.get('system_prompt', ''))} симв.):")
print(card.get('system_prompt', '(пусто)')[:600])

print(f"\n[2. DESCRIPTION] ({len(card.get('description', ''))} симв.):")
print(card.get('description', '(пусто)')[:600])

print(f"\n[3. ПОИСК 'СИОН' / 'SHION' ПО ВСЕЙ КАРТОЧКЕ]:")
full_dump = json.dumps(card, ensure_ascii=False)
for word in ["Сион", "сион", "Shion", "shion", "Sion"]:
    count = full_dump.count(word)
    if count > 0:
        print(f"  • Найдено упоминание '{word}': {count} раз(а)")

first_mes = card.get("first_mes_ru") or card.get("first_mes") or ""
print(f"\n[4. ХВОСТ СТАРТОВОГО СООБЩЕНИЯ (first_mes)] (последние 600 симв.):")
print("-" * 80)
print(first_mes[-600:])
print("-" * 80)
