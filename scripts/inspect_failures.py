from pathlib import Path

# 1. Проверяем импорт CardProgressBridge в roleplay_controller.py
rp_lines = Path("ui/controllers/roleplay_controller.py").read_text(encoding="utf-8").splitlines()
print("=== roleplay_controller.py (первые 35 строк) ===")
for i in range(min(35, len(rp_lines))):
    print(f"{i+1:3d}: {rp_lines[i]}")

# 2. Проверяем chub_client.py вокруг personality и tavern_personality
chub_lines = Path("services/chub_client.py").read_text(encoding="utf-8").splitlines()
print("\n=== services/chub_client.py (поиск tavern_personality) ===")
for idx, line in enumerate(chub_lines):
    if "tavern_personality" in line or "personality" in line:
        for j in range(max(0, idx - 2), min(len(chub_lines), idx + 8)):
            print(f"{j+1:4d}: {chub_lines[j]}")
        break

# 3. Смотрим тесты в test_gallery_translation_and_start.py
test_lines = Path("tests/test_gallery_translation_and_start.py").read_text(encoding="utf-8").splitlines()
print("\n=== test_gallery_translation_and_start.py (строки 160-200) ===")
for i in range(160, min(len(test_lines), 205)):
    print(f"{i+1:4d}: {test_lines[i]}")
