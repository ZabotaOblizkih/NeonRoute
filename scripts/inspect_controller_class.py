from pathlib import Path

p = Path("ui/controllers/roleplay_controller.py")
lines = p.read_text(encoding="utf-8").splitlines()
for i in range(min(50, len(lines))):
    if "class " in lines[i]:
        print(f"{i+1:4d}: {lines[i]}")
