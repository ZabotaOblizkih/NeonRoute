from pathlib import Path

# 1. Какой метод оборачивает строку 1452 в chat_flow_controller.py
p_cf = Path("ui/controllers/chat_flow_controller.py")
if p_cf.exists():
    lines = p_cf.read_text(encoding="utf-8").splitlines()
    for i in range(1451, 1400, -1):
        if lines[i].strip().startswith("def "):
            print(f"=== 1. РОДИТЕЛЬСКИЙ МЕТОД СТРОКИ 1452 В CHAT_FLOW_CONTROLLER: {lines[i].strip()} ===")
            for j in range(i, min(len(lines), i + 20)):
                print(f"{j+1:4d}: {lines[j]}")
            break

# 2. Что делает _handle_roleplay_llm_response со списком сообщений и сессией перед строкой 440
p_rp = Path("services/roleplay_flow_controller.py")
if p_rp.exists():
    lines_rp = p_rp.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. СТРОКИ 314..370 В ROLEPLAY_FLOW_CONTROLLER (_handle_roleplay_llm_response) ===")
    for i in range(313, min(len(lines_rp), 370)):
        print(f"{i+1:4d}: {lines_rp[i]}")

# 3. Как сохраняются сообщения в сессию (messages vs chat_history)
print("\n=== 3. КАК СООБЩЕНИЯ ЗАПИСЫВАЮТСЯ В current_session В РОЛЕВОМ РЕЖИМЕ ===")
for i, l in enumerate(lines_rp):
    if "current_session" in l and ("messages" in l or "chat_history" in l or "append" in l):
        print(f"roleplay_flow_controller.py:{i+1:4d}: {l.strip()}")
