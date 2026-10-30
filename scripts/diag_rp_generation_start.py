from pathlib import Path
import re

# 1. Родительский метод строки 1323 в chat_flow_controller.py
p_cf = Path("ui/controllers/chat_flow_controller.py")
if p_cf.exists():
    lines = p_cf.read_text(encoding="utf-8").splitlines()
    print("=== 1. РОДИТЕЛЬСКИЙ МЕТОД СТРОКИ 1323 В CHAT_FLOW_CONTROLLER ===")
    for i in range(1325, 1200, -1):
        if lines[i].strip().startswith("def "):
            print(f"Найден: {lines[i].strip()} (строка {i+1})")
            for j in range(i, min(len(lines), i + 25)):
                print(f"{j+1:4d}: {lines[j]}")
            break

# 2. Как запускается генерация в roleplay_flow_controller.py
p_rp = Path("services/roleplay_flow_controller.py")
if p_rp.exists():
    lines_rp = p_rp.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. СТАРТ ГЕНЕРАЦИИ В ROLEPLAY_FLOW_CONTROLLER ===")
    for i, l in enumerate(lines_rp):
        if any(k in l for k in ("chat_completion", "stream=True", "generate_response", "def _send_roleplay", "def _execute")):
            if not l.strip().startswith("#"):
                print(f"{i+1:4d}: {l.strip()[:100]}")
                for j in range(i+1, min(len(lines_rp), i+15)):
                    print(f"      {lines_rp[j][:100]}")
                print("      ---")

# 3. Список реальных chat_<id>.json и cast_<id>.json в history/
print("\n=== 3. РЕАЛЬНЫЕ СЕССИИ В HISTORY/ ===")
hist = Path("history")
if hist.exists():
    chats = list(hist.glob("chat_*.json"))
    casts = list(hist.glob("cast_*.json"))
    print(f"Найдено чатов (chat_*.json): {len(chats)}")
    print(f"Найдено файлов каста/лора (cast_*.json): {len(casts)}")
    for c in chats[:3]:
        print(f"  Чат: {c.name} ({c.stat().st_size} байт)")
    for c in casts[:3]:
        print(f"  Каст/Лор: {c.name} ({c.stat().st_size} байт)")
