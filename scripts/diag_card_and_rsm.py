import json
from pathlib import Path

# 1. Все поля карточки Isekai RPG.json
p_card = Path("characters/Isekai RPG.json")
if p_card.exists():
    with open(p_card, "r", encoding="utf-8") as f:
        raw_c = json.load(f)
    card_data = raw_c.get("data", raw_c)
    print("=== 1. ВСЕ ПОЛЯ КАРТОЧКИ ISEKAI RPG ===")
    print("Ключи карточки:", list(card_data.keys()))
    for k, v in card_data.items():
        if isinstance(v, str) and any(w in v.lower() for w in ("status", "статус", "inventory", "инвентар", "location", "локаци", "health", "gold", "level", "stat")):
            print(f"\n--- [КЛЮЧ: {k}] ---")
            print(v[:500] + ("..." if len(v) > 500 else ""))

# 2. Полный код services/roleplay_state_manager.py
p_rsm = Path("services/roleplay_state_manager.py")
if p_rsm.exists():
    print("\n=== 2. ПОЛНЫЙ КОД SERVICES/ROLEPLAY_STATE_MANAGER.PY ===")
    print(p_rsm.read_text(encoding="utf-8"))
