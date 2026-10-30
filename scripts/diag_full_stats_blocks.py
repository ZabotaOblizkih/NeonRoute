import json
import re
from pathlib import Path

p_chat = Path("history/chat_dd66b935-69f8-4724-ba85-1b2a437c2db7.json")
if p_chat.exists():
    with open(p_chat, "r", encoding="utf-8") as f:
        chat = json.load(f)
    msgs = chat.get("messages", [])
    
    print("=== ПОЛНЫЕ БЛОКИ СТАТУСА И СЛУЖЕБНЫХ ТЕГОВ ИЗ ВСЕХ СООБЩЕНИЙ ===")
    for idx, m in enumerate(msgs, 1):
        role = "ИГРОК" if m.get("is_user") else "МОДЕЛЬ"
        txt = str(m.get("text") or m.get("content") or "")
        
        # 1. Ищем блоки <stats>...</stats>
        stats_blocks = re.findall(r'<stats>(.*?)</stats>', txt, flags=re.DOTALL | re.IGNORECASE)
        # 2. Ищем теги в квадратных скобках [STATUS: ...] / [SCENE: ...]
        bracket_tags = re.findall(r'(\[(?:STATUS|СТАТУС|SCENE|СЦЕНА|LOCATION|ЛОКАЦИЯ)[^\]]*\])', txt, flags=re.IGNORECASE)
        
        if stats_blocks or bracket_tags:
            print(f"\n>>> [СООБЩЕНИЕ #{idx} | {role}]")
            for sb in stats_blocks:
                print("--- <stats> ---")
                print(sb.strip())
                print("--- </stats> ---")
            for bt in bracket_tags:
                print(f"Тег: {bt}")

    # Также проверим первое сообщение (описание/завязка) — где именно модель упомянула локацию
    print("\n=== ПЕРВОЕ СООБЩЕНИЕ МОДЕЛИ (ПОЛНЫЙ ТЕКСТ) ===")
    if msgs:
        print(msgs[0].get("text", msgs[0].get("content", "")))
