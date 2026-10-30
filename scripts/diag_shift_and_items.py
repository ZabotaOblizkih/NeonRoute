from pathlib import Path

p_upd = Path("services/rp_scene_updater.py")
if p_upd.exists():
    lines = p_upd.read_text(encoding="utf-8").splitlines()
    print("=== RP_SCENE_UPDATER.PY: СТРОКИ 135..180 ===")
    for i in range(134, min(len(lines), 180)):
        print(f"{i+1:4d}: {lines[i]}")
