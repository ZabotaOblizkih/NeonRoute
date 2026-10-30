from pathlib import Path

# 1. Куда подключен task_finished
print("=== 1. ПОДКЛЮЧЕНИЯ task_finished ===")
for p in Path(".").rglob("*.py"):
    if any(ign in p.parts for ign in [".git", "__pycache__", "venv", ".venv", "scripts"]):
        continue
    try:
        txt = p.read_text(encoding="utf-8")
        for i, l in enumerate(txt.splitlines(), 1):
            if "task_finished" in l and ("connect" in l or "emit" in l):
                print(f"{p}:{i:4d}: {l.strip()}")
    except Exception:
        pass

# 2. Что делает chat_completion в lm_studio.py при stream=True
p_lm = Path("services/lm_studio.py")
if p_lm.exists():
    lines_lm = p_lm.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. LM_STUDIO.PY: chat_completion И stream ===")
    for i, l in enumerate(lines_lm):
        if "def chat_completion(" in l:
            for j in range(i, min(len(lines_lm), i + 40)):
                print(f"{j+1:4d}: {lines_lm[j]}")
                if "return" in lines_lm[j]:
                    break
            break

# 3. Обработка стриминга токенов в chat_flow_controller.py
p_cf = Path("ui/controllers/chat_flow_controller.py")
if p_cf.exists():
    lines_cf = p_cf.read_text(encoding="utf-8").splitlines()
    print("\n=== 3. CHAT_FLOW_CONTROLLER: token_streamed ИЛИ _on_token_streamed ===")
    for i, l in enumerate(lines_cf):
        if any(k in l for k in ("token_streamed", "_on_token", "_stream_flush")):
            print(f"{i+1:4d}: {l.strip()[:100]}")
