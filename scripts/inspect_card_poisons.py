import sys
from pathlib import Path

ROOT = Path(r"C:\NeonRoute")
sys.path.insert(0, str(ROOT))

from services.tavern_parser import TavernParser

card_path = ROOT / "characters" / "Кай и Эйла.json"
card = TavernParser.parse_file(str(card_path))

print("=" * 80)
print("1. АВТОРСКИЙ СИСТЕМНЫЙ ПРОМПТ В КАРТОЧКЕ (system_prompt):")
print("=" * 80)
author_prompt = card.system_prompt or ""
print(f"Длина: {len(author_prompt)} симв.")
print(author_prompt)

print("\n" + "=" * 80)
print("2. ПРИМЕРЫ ДИАЛОГОВ (mes_example):")
print("=" * 80)
ex = card.mes_example_ru or card.mes_example or ""
print(f"Длина: {len(ex)} симв.")
print(ex[:600] + ("..." if len(ex) > 600 else ""))
print("=" * 80)
