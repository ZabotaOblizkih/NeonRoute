from pathlib import Path
import json

# 1. Проверяем жизненный цикл _active_sessions в rp_cast_extractor.py
p_ext = Path("services/rp_cast_extractor.py")
if p_ext.exists():
    lines_ext = p_ext.read_text(encoding="utf-8").splitlines()
    print("=== 1. ЖИЗНЕННЫЙ ЦИКЛ _active_sessions В RP_CAST_EXTRACTOR ===")
    for i, line in enumerate(lines_ext):
        if "_active_sessions" in line:
            print(f"{p_ext.name}:{i+1:4d}: {line.strip()}")

# 2. Проверяем реализацию _sync_turn
print("\n=== 2. ОПРЕДЕЛЕНИЕ И ЛОГИКА _sync_turn ===")
for p in [Path("services/roleplay_flow_controller.py"), Path("ui/controllers/chat_flow_controller.py")]:
    if p.exists():
        lines = p.read_text(encoding="utf-8").splitlines()
        for i, l in enumerate(lines):
            if "def _sync_turn(" in l:
                print(f"Найден в {p.name} (строка {i+1}):")
                for j in range(i, min(len(lines), i + 25)):
                    print(f"  {j+1:4d}: {lines[j]}")
                break

# 3. Проверяем структуру реального файла сессии в history/
print("\n=== 3. СТРУКТУРА КЛЮЧЕЙ В ФАЙЛАХ СЕССИЙ (history/) ===")
hist_dir = Path("history")
if hist_dir.exists():
    sessions = list(hist_dir.glob("session_*.json"))
    if sessions:
        last_sess = sorted(sessions, key=lambda s: s.stat().st_mtime, reverse=True)[0]
        try:
            with open(last_sess, "r", encoding="utf-8") as f:
                s_data = json.load(f)
            print(f"Последняя сессия: {last_sess.name}")
            print(f"Ключи сессии: {list(s_data.keys())}")
            has_msgs = "messages" in s_data
            has_chat = "chat_history" in s_data
            print(f"  'messages': присутствует={has_msgs}, длина={len(s_data.get('messages', [])) if has_msgs else 0}")
            print(f"  'chat_history': присутствует={has_chat}, длина={len(s_data.get('chat_history', [])) if has_chat else 0}")
        except Exception as e:
            print(f"Ошибка чтения сессии: {e}")
    else:
        print("В папке history/ нет файлов session_*.json")

# 4. Проверяем, кто вызывает _on_llm_response_received
print("\n=== 4. ПОДКЛЮЧЕНИЕ СИГНАЛОВ К _on_llm_response_received ===")
for p in Path(".").rglob("*.py"):
    if any(ign in p.parts for ign in [".git", "__pycache__", "venv", ".venv", "scripts"]):
        continue
    try:
        txt = p.read_text(encoding="utf-8")
        for i, l in enumerate(txt.splitlines(), 1):
            if "_on_llm_response_received" in l and not l.strip().startswith("def "):
                print(f"{p}:{i:4d}: {l.strip()}")
    except Exception:
        pass
