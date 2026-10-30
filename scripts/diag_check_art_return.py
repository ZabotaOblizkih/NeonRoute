from pathlib import Path

p = Path("services/roleplay_flow_controller.py")
lines = p.read_text(encoding="utf-8").splitlines()
print("=== ОКОНЧАНИЕ _generate_ephemeral_rp_art_prompt (строки 625..670) ===")
for i in range(624, min(len(lines), 670)):
    print(f"{i+1:4d}: {lines[i]}")
