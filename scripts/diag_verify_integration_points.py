from pathlib import Path

# 1. Точное место вызова process_model_turn в roleplay_flow_controller.py
p_rp = Path("services/roleplay_flow_controller.py")
if p_rp.exists():
    lines = p_rp.read_text(encoding="utf-8").splitlines()
    print("=== 1. ROLEPLAY_FLOW_CONTROLLER: СТРОКИ 314..335 ===")
    for i in range(313, min(len(lines), 335)):
        print(f"{i+1:4d}: {lines[i]}")

# 2. Обработка story_milestone в rp_scene_updater.py
p_upd = Path("services/rp_scene_updater.py")
if p_upd.exists():
    lines_upd = p_upd.read_text(encoding="utf-8").splitlines()
    print("\n=== 2. RP_SCENE_UPDATER: СТРОКИ 442..455 ===")
    for i in range(441, min(len(lines_upd), 455)):
        print(f"{i+1:4d}: {lines_upd[i]}")
