from pathlib import Path

# 1. Как rp_chat_bubble монтирует виджеты (строки 135..190)
p_rpc = Path("ui/components/rp/rp_chat_bubble.py")
if p_rpc.exists():
    lines = p_rpc.read_text(encoding="utf-8").splitlines()
    print("=== 1. RP_CHAT_BUBBLE.PY: МОНТИРОВАНИЕ ВИДЖЕТОВ (СТРОКИ 135..190) ===")
    for i in range(134, min(len(lines), 190)):
        print(f"{i+1:4d}: {lines[i]}")

# 2. main_window.py: строки 860..910 (вставка first_mes)
p_mw = Path("ui/main_window.py")
if p_mw.exists():
    lines_mw = p_mw.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. MAIN_WINDOW.PY: ВСТАВКА FIRST_MES (СТРОКИ 860..910) ===")
    for i in range(859, min(len(lines_mw), 910)):
        print(f"{i+1:4d}: {lines_mw[i]}")
