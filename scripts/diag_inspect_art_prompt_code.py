from pathlib import Path

p = Path("services/roleplay_flow_controller.py")
if p.exists():
    lines = p.read_text(encoding="utf-8").splitlines()
    print("=== 1. ИМПОРТЫ В services/roleplay_flow_controller.py ===")
    for i in range(min(40, len(lines))):
        if any(k in lines[i] for k in ("import ", "from ", "Task")):
            print(f"{i+1:3d}: {lines[i]}")

    print("\n=== 2. КАК ФОРМИРУЕТСЯ И ОТПРАВЛЯЕТСЯ ЗАДАЧА В _generate_ephemeral_rp_art_prompt ===")
    for i, l in enumerate(lines):
        if "def _generate_ephemeral_rp_art_prompt" in l:
            for j in range(i, min(len(lines), i + 75)):
                print(f"{j+1:4d}: {lines[j]}")
            break
