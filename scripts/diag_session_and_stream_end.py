from pathlib import Path

# 1. Как SessionManager сохраняет сессии и что делает с trigger_memory
p_sm = Path("services/session_manager.py")
if p_sm.exists():
    lines_sm = p_sm.read_text(encoding="utf-8").splitlines()
    print("=== 1. SESSION_MANAGER: save_session И trigger_memory ===")
    for i, l in enumerate(lines_sm):
        if "def save_session(" in l:
            for j in range(i, min(len(lines_sm), i + 35)):
                print(f"{j+1:4d}: {lines_sm[j]}")
            break

# 2. Где хранятся сессии и какой ключ у ID сессии
print("\n=== 2. СТРУКТУРА СЕССИИ И ПУТИ В SESSION_MANAGER ===")
for i, l in enumerate(lines_sm):
    if any(k in l for k in ("HISTORY_DIR", "SESSIONS_DIR", "SESSION_FILE", "session_id", "def create_session", "def get_current_session")):
        print(f"session_manager.py:{i+1:4d}: {l.strip()[:110]}")

# 3. Как завершается стриминг в lm_studio.py
p_lm = Path("services/lm_studio.py")
if p_lm.exists():
    lines_lm = p_lm.read_text(encoding="utf-8").splitlines()
    print("\n=== 3. LM_STUDIO: ЗАВЕРШЕНИЕ СТРИМИНГА И СИГНАЛЫ ===")
    for i, l in enumerate(lines_lm):
        if "token_streamed" in l or "stream=True" in l or "stream_finished" in l or "_handle_stream" in l:
            for j in range(max(0, i-2), min(len(lines_lm), i+15)):
                print(f"{j+1:4d}: {lines_lm[j]}")
            break

# 4. Что за метод оборачивает строку 1323 в chat_flow_controller.py
p_cf = Path("ui/controllers/chat_flow_controller.py")
if p_cf.exists():
    lines_cf = p_cf.read_text(encoding="utf-8").splitlines()
    print("\n=== 4. CHAT_FLOW_CONTROLLER: МЕТОД СО СТРОКОЙ 1323 ===")
    for i in range(1323, 1280, -1):
        if lines_cf[i].strip().startswith("def "):
            print(f"Родительский метод: {lines_cf[i].strip()} (строка {i+1})")
            for j in range(i, min(len(lines_cf), i + 25)):
                print(f"{j+1:4d}: {lines_cf[j]}")
            break
