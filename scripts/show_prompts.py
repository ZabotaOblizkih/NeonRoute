from pathlib import Path
import re

print("=" * 60)
print("1. СИСТЕМНЫЕ ПРОМПТЫ В services/card_translator.py (Пошаговый)")
print("=" * 60)
trans_path = Path("services/card_translator.py")
if trans_path.exists():
    lines = trans_path.read_text(encoding="utf-8").splitlines()
    capturing = False
    buffer = []
    for idx, line in enumerate(lines):
        if any(kw in line.lower() for kw in ("system_prompt", "sys_prompt", "prompt =", "system\":")):
            print(f"\n--- [Строка {idx+1}] ---")
            for j in range(max(0, idx - 1), min(len(lines), idx + 25)):
                print(f"{j+1:4d}: {lines[j]}")
                if '"""' in lines[j] and j > idx:
                    break
else:
    print("services/card_translator.py не найден")

print("\n" + "=" * 60)
print("2. СИСТЕМНЫЙ ПРОМПТ В ui/controllers/roleplay_controller.py (Монолитный)")
print("=" * 60)
ctrl_path = Path("ui/controllers/roleplay_controller.py")
if ctrl_path.exists():
    lines = ctrl_path.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines):
        if "sys_prompt =" in line:
            for j in range(idx, min(len(lines), idx + 10)):
                print(f"{j+1:4d}: {lines[j]}")
                if ")" in lines[j]:
                    break
            break
