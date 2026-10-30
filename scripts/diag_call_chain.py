from pathlib import Path
import re

print("=== 1. ГДЕ ВЫЗЫВАЕТСЯ _handle_roleplay_llm_response? ===")
for p in Path(".").rglob("*.py"):
    if any(ign in p.parts for ign in [".git", "__pycache__", "venv", ".venv"]):
        continue
    try:
        txt = p.read_text(encoding="utf-8")
        for idx, line in enumerate(txt.splitlines(), 1):
            if "_handle_roleplay_llm_response" in line:
                print(f"{p}:{idx:4d}: {line.strip()}")
    except Exception:
        pass

print("\n=== 2. СИГНАЛЫ ЗАВЕРШЕНИЯ ГЕНЕРАЦИИ В ROLEPLAY_FLOW_CONTROLLER ===")
p_rp = Path("services/roleplay_flow_controller.py")
if p_rp.exists():
    txt_rp = p_rp.read_text(encoding="utf-8")
    for idx, line in enumerate(txt_rp.splitlines(), 1):
        if any(sig in line for sig in ("worker", "thread", "finished", "stream", "on_generation", "generate")):
            if not line.strip().startswith("#"):
                print(f"{p_rp}:{idx:4d}: {line.strip()[:110]}")

print("\n=== 3. СИГНАЛЫ ЗАВЕРШЕНИЯ ГЕНЕРАЦИИ В CHAT_FLOW_CONTROLLER ===")
p_cf = Path("ui/controllers/chat_flow_controller.py")
if p_cf.exists():
    txt_cf = p_cf.read_text(encoding="utf-8")
    for idx, line in enumerate(txt_cf.splitlines(), 1):
        if any(sig in line for sig in ("_worker", "generation_finished", "on_turn_end", "_on_generation_finished")):
            print(f"{p_cf}:{idx:4d}: {line.strip()[:110]}")
