import json
from pathlib import Path

p_card = Path("characters/Isekai RPG.json")
if p_card.exists():
    with open(p_card, "r", encoding="utf-8") as f:
        raw_c = json.load(f)
    card_data = raw_c.get("data", raw_c)
    
    print("=== 1. ПОЛНЫЙ БЛОК PERSONALITY (ФОРМАТ СТАТОВ) ===")
    print(card_data.get("personality", ""))
    
    print("\n=== 2. ПОЛНЫЙ БЛОК MES_EXAMPLE (КАК КАРТОЧКА ПОДАЕТ ПРИМЕР) ===")
    print(card_data.get("mes_example", "")[:1200])

    print("\n=== 3. POST_HISTORY_INSTRUCTIONS (ЕСЛИ ЕСТЬ) ===")
    print(card_data.get("post_history_instructions", ""))
