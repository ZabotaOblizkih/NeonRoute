from pathlib import Path

# 1. Как rpg_elements.py и rp_chat_bubble.py обрабатывают <stats>
p_rpg = Path("ui/views/rpg_elements.py")
if p_rpg.exists():
    lines = p_rpg.read_text(encoding="utf-8").splitlines()
    print("=== 1. RPG_ELEMENTS.PY (<stats>) ===")
    for i, l in enumerate(lines):
        if "<stats>" in l.lower() or "stats" in l.lower() and "def " in l:
            for j in range(max(0, i-2), min(len(lines), i+30)):
                print(f"{j+1:4d}: {lines[j]}")
            print("---")
            break

p_rpc = Path("ui/views/rp_chat_bubble.py")
if p_rpc.exists():
    lines = p_rpc.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. RP_CHAT_BUBBLE.PY (<stats>) ===")
    for i, l in enumerate(lines):
        if "<stats>" in l or "stats" in l.lower() and "def " in l:
            for j in range(max(0, i-2), min(len(lines), i+30)):
                print(f"{j+1:4d}: {lines[j]}")
            print("---")
            break

# 3. services/roleplay_state_manager.py сейчас
p_sm = Path("services/roleplay_state_manager.py")
if p_sm.exists():
    print("\n=== 3. SERVICES/ROLEPLAY_STATE_MANAGER.PY (ТЕКУЩИЙ КОД) ===")
    print(p_sm.read_text(encoding="utf-8"))
