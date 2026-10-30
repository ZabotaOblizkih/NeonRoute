from pathlib import Path

lines = Path("tests/test_gallery_translation_and_start.py").read_text(encoding="utf-8").splitlines()
for i in range(210, min(len(lines), 360)):
    print(f"{i+1:4d}: {lines[i]}")
