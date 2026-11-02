import glob
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")

# 1. Поиск карточки без жесткой кириллицы в пайпе
card_files = list((ROOT / "characters").glob("*Кай*.json"))
if not card_files:
    card_files = list((ROOT / "characters").glob("*.json"))

found_card = None
for cf in card_files:
    if "эйла" in cf.name.lower() or "kai" in cf.name.lower():
        found_card = cf
        break

if found_card:
    card_text = found_card.read_text(encoding="utf-8").lower()
    print(f"Файл карточки: {found_card.name}")
    print(f"• Есть ли 'slave collar' в исходной карточке: {'slave collar' in card_text}")
    print(f"• Есть ли 'slave market' в исходной карточке: {'slave market' in card_text}")
else:
    print("❌ Файл карточки сестер не найден")

# 2. Проверяем код билдера
builder_code = (ROOT / "services" / "roleplay_prompt_builder.py").read_text(encoding="utf-8")
print(f"• Присутствует ли '[Current Active Companions' в билдере: {'[Current Active Companions' in builder_code}")
