from pathlib import Path

p = Path("ui/controllers/roleplay_controller.py")
lines = p.read_text(encoding="utf-8").splitlines()

match_idx = None
for idx, line in enumerate(lines):
    if "def _translate_character_card_task" in line:
        match_idx = idx
        break

if match_idx is not None:
    print(f"=== _translate_character_card_task (строки {match_idx+1} - {match_idx+35}) ===")
    for i in range(match_idx, min(len(lines), match_idx + 35)):
        print(f"{i+1:4d}: {lines[i]}")
