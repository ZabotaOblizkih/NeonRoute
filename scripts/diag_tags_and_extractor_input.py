import json
from pathlib import Path

# 1. Смотрим карточку Isekai RPG: системный промпт, post_history_instructions, описание
p_card = Path("characters/Isekai RPG.json")
if p_card.exists():
    with open(p_card, "r", encoding="utf-8") as f:
        raw_c = json.load(f)
    card_data = raw_c.get("data", raw_c)
    print("=== 1. ТЕГИ И ИНСТРУКЦИИ В КАРТОЧКЕ ISEKAI RPG ===")
    for k in ("system_prompt", "post_history_instructions", "description", "mes_example"):
        val = card_data.get(k, "")
        if val:
            print(f"\n--- Поле карточки: {k} (длина: {len(val)}) ---")
            for line in val.splitlines():
                if any(tag in line.lower() for tag in ("location", "inventory", "status", "локация", "инвентарь", "тег", "<", "[", "health", "gold")):
                    print(f"  {line.strip()[:110]}")

# 2. Как RPCastExtractor формирует prompt для LLM (строки 320..395)
p_ext = Path("services/rp_cast_extractor.py")
if p_ext.exists():
    lines_ext = p_ext.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. СТРОКИ 320..380 В RP_CAST_EXTRACTOR (ЧТО УХОДИТ В МОДЕЛЬ) ===")
    for i in range(320, min(len(lines_ext), 380)):
        print(f"{i+1:4d}: {lines_ext[i]}")

# 3. Как вызывается extract_cast_async в roleplay_flow_controller (строки 430..470)
p_rp = Path("services/roleplay_flow_controller.py")
if p_rp.exists():
    lines_rp = p_rp.read_text(encoding="utf-8").splitlines()
    print("\n=== 3. ВЫЗОВ ЭКСТРАКТОРА В ROLEPLAY_FLOW_CONTROLLER ===")
    for i in range(435, min(len(lines_rp), 475)):
        print(f"{i+1:4d}: {lines_rp[i]}")
