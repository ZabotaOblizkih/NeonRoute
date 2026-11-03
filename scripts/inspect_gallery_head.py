from pathlib import Path

lines = Path("ui/dialogs/characters_gallery_dialog.py").read_text(encoding="utf-8").splitlines()
print("--- Строки 80–205 из characters_gallery_dialog.py ---")
for idx in range(79, min(205, len(lines))):
    l = lines[idx]
    if l.startswith("def ") or l.startswith("class ") or l.startswith("#"):
        print(f"Строка {idx+1:4d}: {l}")
