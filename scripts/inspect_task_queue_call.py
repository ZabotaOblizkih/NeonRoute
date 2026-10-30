from pathlib import Path

p = Path("ui/controllers/roleplay_controller.py")
lines = p.read_text(encoding="utf-8").splitlines()

# Выводим строки с 300 по 330 и с 385 по 415
for i in range(295, min(len(lines), 330)):
    print(f"{i+1:4d}: {lines[i]}")

print("-" * 50)
for i in range(385, min(len(lines), 415)):
    print(f"{i+1:4d}: {lines[i]}")
