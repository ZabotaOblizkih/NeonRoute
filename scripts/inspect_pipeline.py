from pathlib import Path

p = Path("ui/dialogs/gallery/translation_mixin.py")
lines = p.read_text(encoding="utf-8").splitlines()

match_idx = None
for idx, line in enumerate(lines):
    if "def _execute_translation_pipeline" in line:
        match_idx = idx
        break

if match_idx is not None:
    for i in range(match_idx, min(len(lines), match_idx + 45)):
        print(f"{i+1:4d}: {lines[i]}")
else:
    print("[!] _execute_translation_pipeline не найден")
