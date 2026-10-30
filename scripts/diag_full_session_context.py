import json
from pathlib import Path

# 1. Все 7 сообщений сессии dd66b935
p_chat = Path("history/chat_dd66b935-69f8-4724-ba85-1b2a437c2db7.json")
if p_chat.exists():
    with open(p_chat, "r", encoding="utf-8") as f:
        chat = json.load(f)
    msgs = chat.get("messages", [])
    print(f"=== 1. ВСЕ СООБЩЕНИЯ СЕССИИ (всего: {len(msgs)}) ===")
    for i, m in enumerate(msgs, 1):
        role = "ИГРОК" if m.get("is_user") else "МОДЕЛЬ"
        txt = str(m.get("text") or m.get("content") or "").strip()
        print(f"\n[{i}] {role}:")
        print(txt[:300] + ("..." if len(txt) > 300 else ""))

# 2. Исходная карточка Isekai RPG (первое сообщение, сценарий, системный промпт)
print("\n=== 2. СТРУКТУРА КАРТОЧКИ ISEKAI RPG ===")
for p in Path("characters").rglob("*.json"):
    if "isekai" in p.name.lower():
        print(f"Файл карточки: {p}")
        try:
            with open(p, "r", encoding="utf-8") as f:
                cdata = json.load(f)
            data = cdata.get("data", cdata)
            print("  Имя:", data.get("name"))
            print("  first_mes (первые 250 симв):", str(data.get("first_mes") or "")[:250])
            print("  scenario (первые 200 симв):", str(data.get("scenario") or "")[:200])
        except Exception as e:
            print("  Ошибка чтения карточки:", e)
        break
