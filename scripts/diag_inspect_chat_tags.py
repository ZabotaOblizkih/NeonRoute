import json
from pathlib import Path

p_chat = Path("history/chat_dd66b935-69f8-4724-ba85-1b2a437c2db7.json")
if p_chat.exists():
    with open(p_chat, "r", encoding="utf-8") as f:
        chat = json.load(f)
    
    msgs = chat.get("messages", [])
    print(f"=== ТЕГИ СТАТУСА В СООБЩЕНИЯХ СЕССИИ (всего сообщений: {len(msgs)}) ===")
    for idx, m in enumerate(msgs, 1):
        txt = str(m.get("text") or m.get("content") or "")
        low = txt.lower()
        if any(k in low for k in ("<stats>", "[status", "[статус", "physical status:", "inventory:", "location:")):
            role = "ИГРОК" if m.get("is_user") else "МОДЕЛЬ"
            print(f"\n--- [СООБЩЕНИЕ #{idx} | {role}] ---")
            # Печатаем кусок со статусом
            for line in txt.splitlines():
                if any(k in line.lower() for k in ("<stats>", "</stats>", "[status", "[статус", "status:", "location:", "inventory:", "equipment:", "gold:", "objectives:", "уровень:", "локация:", "инвентарь:")):
                    print(f"  {line[:120]}")
