from pathlib import Path

p_upd = Path("services/rp_scene_updater.py")
if p_upd.exists():
    lines = p_upd.read_text(encoding="utf-8").splitlines()
    
    print("=== 1. ИНВЕНТАРЬ И КВЕСТЫ В RP_SCENE_UPDATER (СТРОКИ 88..140) ===")
    for i in range(87, min(len(lines), 140)):
        print(f"{i+1:4d}: {lines[i]}")

    print("\n=== 2. СНЯТИЕ/ДОБАВЛЕНИЕ ПРАВИЛ И ОПАСНОСТЕЙ (СТРОКИ 180..220) ===")
    for i in range(179, min(len(lines), 220)):
        print(f"{i+1:4d}: {lines[i]}")
