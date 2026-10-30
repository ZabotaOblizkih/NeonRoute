import sys
from pathlib import Path

# 1. Смотрим импорты из qfluentwidgets в файле диалога
lines = Path("ui/dialogs/characters_gallery_dialog.py").read_text(encoding="utf-8").splitlines()
print("=== ИМПОРТЫ В characters_gallery_dialog.py ===")
for i in range(20, 50):
    if i < len(lines):
        print(f"{i+1:3d}: {lines[i]}")

# 2. Проверяем доступность виджетов прогресса в установленной библиотеке
import qfluentwidgets
widgets_available = [w for w in dir(qfluentwidgets) if "Progress" in w or "Button" in w or "Badge" in w or "Step" in w or "Ring" in w]
print("\n=== ДОСТУПНЫЕ ВИДЖЕТЫ В qfluentwidgets ===")
print(", ".join(sorted(widgets_available)))
