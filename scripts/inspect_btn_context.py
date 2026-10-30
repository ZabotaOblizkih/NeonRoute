from pathlib import Path

p = Path("ui/dialogs/characters_gallery_dialog.py")
lines = p.read_text(encoding="utf-8").splitlines()

print("=== 1. ИМПОРТЫ QFLUENTWIDGETS В ДИАЛОГЕ ГАЛЕРЕИ ===")
for i in range(min(50, len(lines))):
    if "qfluentwidgets" in lines[i] or "FIF" in lines[i]:
        print(f"{i+1:4d}: {lines[i]}")

print("\n=== 2. СТРОКИ ВОКРУГ КНОПКИ (700-740) ===")
for i in range(695, min(len(lines), 740)):
    print(f"{i+1:4d}: {lines[i]}")
