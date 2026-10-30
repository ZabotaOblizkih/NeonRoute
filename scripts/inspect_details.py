from pathlib import Path

# 1. chub_client.py (строки 260-320)
chub_lines = Path("services/chub_client.py").read_text(encoding="utf-8").splitlines()
print("=== services/chub_client.py (260-320) ===")
for i in range(259, min(len(chub_lines), 320)):
    print(f"{i+1:4d}: {chub_lines[i]}")

# 2. test_gallery_translation_and_start.py (строки 170-210)
t_lines = Path("tests/test_gallery_translation_and_start.py").read_text(encoding="utf-8").splitlines()
print("\n=== tests/test_gallery_translation_and_start.py (170-215) ===")
for i in range(169, min(len(t_lines), 215)):
    print(f"{i+1:4d}: {t_lines[i]}")
