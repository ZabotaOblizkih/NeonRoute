from pathlib import Path

def print_section(title, file_path, start_line, end_line):
    print("=" * 70)
    print(f"  {title}: {file_path} (строки {start_line}-{end_line})")
    print("=" * 70)
    p = Path(file_path)
    if not p.exists():
        print(f"[!] Файл не найден: {p}")
        return
    lines = p.read_text(encoding="utf-8").splitlines()
    for i in range(max(0, start_line - 1), min(len(lines), end_line)):
        print(f"{i+1:4d}: {lines[i]}")

# 1. Начало метода перевода в roleplay_controller.py
print_section("ROLEPLAY CONTROLLER (НАЧАЛО _translate_character_card_task)", "ui/controllers/roleplay_controller.py", 250, 325)

# 2. Окончание _on_all_done в translation_mixin.py
print_section("TRANSLATION MIXIN (ХВОСТ _on_all_done)", "ui/dialogs/gallery/translation_mixin.py", 230, 280)
