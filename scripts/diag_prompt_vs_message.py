import json
from pathlib import Path

# 1. Смотрим реальный текст реплик в сессии chat_dd66b935...
p_chat = Path("history/chat_dd66b935-69f8-4724-ba85-1b2a437c2db7.json")
if p_chat.exists():
    with open(p_chat, "r", encoding="utf-8") as f:
        chat = json.load(f)
    msgs = chat.get("messages", [])
    print(f"=== 1. ПОСЛЕДНИЕ РЕПЛИКИ В СЕССИИ (всего: {len(msgs)}) ===")
    for idx, m in enumerate(msgs[-2:], 1):
        role = "ИГРОК" if m.get("is_user") else "МОДЕЛЬ"
        txt = str(m.get("text") or m.get("content") or "").strip()
        print(f"\n--- [{role}] (первые 400 символов) ---")
        print(txt[:400])
        if len(txt) > 400:
            print("... [хвост реплики] ...")
            print(txt[-300:])

# 2. Инструкции промпта экстрактора по поводу локации и инвентаря
p_ext = Path("services/rp_cast_extractor.py")
if p_ext.exists():
    lines = p_ext.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. СИСТЕМНЫЙ ПРОМПТ ЭКСТРАКТОРА (ЛОКАЦИЯ И ИНВЕНТАРЬ) ===")
    for idx, l in enumerate(lines[:120], 1):
        if any(k in l.lower() for k in ("location", "item", "инвентар", "локаци", "rule", "quest")):
            print(f"{idx:3d}: {l.strip()}")
