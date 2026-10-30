from pathlib import Path

p = Path("ui/controllers/roleplay_controller.py")
lines = p.read_text(encoding="utf-8").splitlines()

# Находим начало и конец _translate_character_card_task
start_idx = None
end_idx = None
for idx, line in enumerate(lines):
    if "def _translate_character_card_task" in line:
        start_idx = idx
    if start_idx is not None and "def _restore_roleplay_character_from_session" in line:
        end_idx = idx
        break

print(f"Метод _translate_character_card_task: строки {start_idx + 1} - {end_idx}")
for i in range(start_idx, min(len(lines), start_idx + 45)):
    print(f"{i+1:4d}: {lines[i]}")
print("...")
for i in range(max(start_idx, end_idx - 35), end_idx):
    print(f"{i+1:4d}: {lines[i]}")
