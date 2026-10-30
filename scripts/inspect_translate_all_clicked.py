from pathlib import Path

p = Path("ui/dialogs/gallery/translation_mixin.py")
lines = p.read_text(encoding="utf-8").splitlines()

match_idx = None
for idx, line in enumerate(lines):
    if "def _on_translate_all_clicked" in line:
        match_idx = idx
        break

if match_idx is not None:
    print(f"=== _on_translate_all_clicked (строки {match_idx + 1} - {min(len(lines), match_idx + 60)}) ===")
    for i in range(match_idx, min(len(lines), match_idx + 60)):
        print(f"{i+1:4d}: {lines[i]}")
else:
    print("[!] _on_translate_all_clicked не найден")
