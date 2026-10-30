from pathlib import Path

# 1. Точный код парсера <stats> в rpg_elements.py (строки 40..130)
p_rpg = Path("ui/components/rpg_elements.py")
if p_rpg.exists():
    lines = p_rpg.read_text(encoding="utf-8").splitlines()
    print("=== 1. RPG_ELEMENTS.PY: ПАРСИНГ ТЕГОВ (СТРОКИ 40..130) ===")
    for i in range(39, min(len(lines), 130)):
        print(f"{i+1:4d}: {lines[i]}")

# 2. Где в session_manager.py создается first_mes и сессия
p_sm = Path("services/session_manager.py")
if p_sm.exists():
    lines_sm = p_sm.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. SESSION_MANAGER.PY: СОЗДАНИЕ СЕССИИ И FIRST_MES ===")
    for i, l in enumerate(lines_sm):
        if "first_mes" in l or "def create_session" in l:
            for j in range(max(0, i-2), min(len(lines_sm), i+25)):
                print(f"{j+1:4d}: {lines_sm[j]}")
            print("---")
            break
