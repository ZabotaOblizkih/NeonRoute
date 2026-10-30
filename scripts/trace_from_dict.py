import sys
import traceback
from pathlib import Path

ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))

from services.tavern_models import CharacterCard
from services.tavern_parser import TavernParser

target_json = Path("characters/Isekai RPG.json").resolve()
disk_card = TavernParser.parse_file(target_json)

mock_session = {
    "name": disk_card.name,
    "description": disk_card.description,
    "personality": disk_card.personality,
    "scenario": disk_card.scenario,
    "first_mes": disk_card.first_mes,
    "source_path": str(target_json),
    "description_ru": "",
    "personality_ru": "",
    "first_mes_ru": "",
    "scenario_ru": ""
}

print("=" * 70)
print("  ТРАССИРОВКА ВЫЗОВА from_dict")
print("=" * 70)

# Проверяем напрямую шаги, которые должен делать from_dict
cand = Path(mock_session["source_path"])
print(f"1. source_path: {cand}")
print(f"   Файл существует: {cand.exists()}")

import json
try:
    with open(cand, "r", encoding="utf-8") as f:
        raw = json.load(f)
    print(f"2. JSON прочитан успешно. Ключи: {list(raw.keys())[:5]}")
    
    parsed = TavernParser.parse_dict(raw, source_path=cand)
    print(f"3. parse_dict: {parsed is not None}")
    if parsed:
        print(f"   parsed.description_ru: len={len(parsed.description_ru or '')}")
        print(f"   parsed.personality_ru: len={len(parsed.personality_ru or '')}")
except Exception as e:
    print(f"[!] Ошибка на этапе ручной проверки: {e}")
    traceback.print_exc()

print("\n" + "=" * 70)
print("  РЕАЛЬНЫЙ ВЫЗОВ CharacterCard.from_dict(mock_session)")
print("=" * 70)

try:
    card = CharacterCard.from_dict(mock_session)
    print(f"Результат: description_ru len={len(card.description_ru or '')}")
    print(f"Результат: personality_ru len={len(card.personality_ru or '')}")
except Exception as e:
    print(f"[!] from_dict выбросил исключение: {e}")
    traceback.print_exc()

# Выводим реальный кусок кода from_dict из services/tavern_models.py (строки 510-610)
print("\n" + "=" * 70)
print("  ТЕКУЩИЙ КОД from_dict (строки 515-610)")
print("=" * 70)
lines = Path("services/tavern_models.py").read_text(encoding="utf-8").splitlines()
for idx in range(514, min(len(lines), 615)):
    print(f"{idx+1:4d}: {lines[idx]}")
