from pathlib import Path

p_upd = Path("services/rp_scene_updater.py")
if p_upd.exists():
    lines = p_upd.read_text(encoding="utf-8").splitlines()
    
    print("=== 1. RP_SCENE_UPDATER.PY: СТРОКИ 210..245 (new_rule / world_state) ===")
    for i in range(max(0, 205), min(len(lines), 245)):
        print(f"{i+1:4d}: {lines[i]}")

    print("\n=== 2. RP_SCENE_UPDATER.PY: СТРОКИ 440..475 (story_milestone) ===")
    for i in range(max(0, 435), min(len(lines), 475)):
        print(f"{i+1:4d}: {lines[i]}")
