import json
from pathlib import Path

# 1. Что делает RoleplayStateManager
p_rsm = Path("services/roleplay_state_manager.py")
if p_rsm.exists():
    lines = p_rsm.read_text(encoding="utf-8").splitlines()
    print("=== 1. SERVICES/ROLEPLAY_STATE_MANAGER.PY ===")
    for i, l in enumerate(lines):
        if any(k in l for k in ("def process_model_turn", "def parse", "inventory", "location", "regex", "status")):
            print(f"{i+1:4d}: {l.strip()[:100]}")
            for j in range(i+1, min(len(lines), i+15)):
                if lines[j].strip().startswith("def "):
                    break
                print(f"      {lines[j].strip()[:100]}")
            print("      ---")

# 2. Поиск парсеров статус-блока и тегов RPG по всему services/
print("\n=== 2. ПОИСК ПАРСЕРОВ РПГ-СТАТУСА И ИНВЕНТАРЯ В SERVICES/ ===")
for p in Path("services").glob("*.py"):
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if any(k in txt.lower() for k in ("process_model_turn", "parse_status", "parse_inventory", "location_regex", "inventory_regex")):
        for i, l in enumerate(txt.splitlines(), 1):
            if any(k in l.lower() for k in ("def ", "regex", "re.compile", "inventory", "location")) and any(s in l.lower() for s in ("status", "parse", "stat", "inv", "loc")):
                print(f"{p.name}:{i:4d}: {l.strip()[:100]}")

# 3. Как инициализируется cast при создании сессии
print("\n=== 3. ИНИЦИАЛИЗАЦИЯ КАСТА В RPCastManager.load_cast / create_session ===")
p_cm = Path("services/rp_cast_manager.py")
if p_cm.exists():
    lines_cm = p_cm.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines_cm):
        if "def load_cast(" in l or "def init_cast(" in l or "def _create_default_cast" in l:
            for j in range(i, min(len(lines_cm), i + 35)):
                print(f"{j+1:4d}: {lines_cm[j]}")
            break

# 4. Проверяем все сессии в history/ — есть ли где-то заполненный статус/инвентарь
print("\n=== 4. СЕССИИ В HISTORY/ (ПОИСК СТАТУС-БЛОКОВ В ТЕКСТЕ) ===")
for p in Path("history").glob("chat_*.json"):
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        msgs = data.get("messages", [])
        status_msgs = [m for m in msgs if any(w in str(m.get("text", "")).lower() for w in ("инвентарь", "локация", "здоровье", "статус:", "уровень:"))]
        print(f"  {p.name}: сообщений={len(msgs)}, с RPG-статусом={len(status_msgs)}")
        if status_msgs:
            print(f"    Пример статуса из {p.name}:")
            txt = str(status_msgs[-1].get("text", ""))[:250]
            print(f"    {txt}")
    except Exception as e:
        pass
