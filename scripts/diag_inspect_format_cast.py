from pathlib import Path

p_cast = Path("services/rp_cast_manager.py")
lines = p_cast.read_text(encoding="utf-8").splitlines()

print("=== 1. РЕАЛИЗАЦИЯ format_cast_data_block ===")
for i, l in enumerate(lines):
    if "def format_cast_data_block" in l:
        for j in range(i, min(len(lines), i + 65)):
            print(f"{j+1:4d}: {lines[j]}")
        break

print("\n=== 2. РЕАЛИЗАЦИЯ load_cast (строки 410-445) ===")
for i in range(409, min(len(lines), 445)):
    print(f"{i+1:4d}: {lines[i]}")
