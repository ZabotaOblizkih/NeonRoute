from pathlib import Path

lines = Path("tests/test_gallery_translation_and_start.py").read_text(encoding="utf-8").splitlines()
for idx, line in enumerate(lines):
    if "class TestTranslationParsing" in line:
        for j in range(idx, min(len(lines), idx + 80)):
            print(f"{j+1:4d}: {lines[j]}")
        break
