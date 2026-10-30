from pathlib import Path

p = Path("ui/dialogs/gallery/translation_mixin.py")
lines = p.read_text(encoding="utf-8").splitlines()
for i in range(255, min(len(lines), 295)):
    print(f"{i+1:4d}: {lines[i]}")
