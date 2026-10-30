from pathlib import Path

# 1. Как rp_chat_bubble.py рендерит RPGBundle (stats, meters, thought)
p_rpc = Path("ui/components/rp/rp_chat_bubble.py")
if p_rpc.exists():
    lines = p_rpc.read_text(encoding="utf-8").splitlines()
    print("=== 1. RP_CHAT_BUBBLE.PY: РЕНДЕР СТАТУСА И БАНДЛА ===")
    for i, l in enumerate(lines):
        if any(k in l for k in ("bundle", "stats_data", "set_text", "RPGBundle", "_apply_rpg")):
            for j in range(max(0, i-2), min(len(lines), i+35)):
                print(f"{j+1:4d}: {lines[j]}")
            print("---")
            break

# 2. session_manager.py: строки 180..260 (где создается first_mes)
p_sm = Path("services/session_manager.py")
if p_sm.exists():
    lines = p_sm.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. SESSION_MANAGER.PY: FIRST_MES И ИНИЦИАЛИЗАЦИЯ (180..260) ===")
    for i in range(180, min(len(lines), 260)):
        print(f"{i+1:4d}: {lines[i]}")
