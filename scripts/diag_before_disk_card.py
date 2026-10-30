from pathlib import Path

models_path = Path("services/tavern_models.py")
lines = models_path.read_text(encoding="utf-8").splitlines()

for i in range(500, 565):
    if i < len(lines):
        print(f"{i+1:4d}: {lines[i]}")
