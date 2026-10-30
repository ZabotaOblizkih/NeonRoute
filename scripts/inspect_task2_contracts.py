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

# 1. Текущий метод в roleplay_controller.py (хвост _worker_fn, _on_done, сохранение)
print_section("ROLEPLAY CONTROLLER", "ui/controllers/roleplay_controller.py", 325, 395)

# 2. Обработка клика и коллбэки в translation_mixin.py
print_section("TRANSLATION MIXIN", "ui/dialogs/gallery/translation_mixin.py", 175, 235)
