import sys
import ast
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

gallery_file = PROJECT_ROOT / "ui" / "dialogs" / "characters_gallery_dialog.py"
gallery_lines = gallery_file.read_text(encoding="utf-8").splitlines()

# 1. Находим границы выносимого блока (строки 72-406)
start_idx = None
end_idx = None

for idx, line in enumerate(gallery_lines):
    if line.startswith("class CardItemDelegate("):
        start_idx = idx
    if line.startswith("class CharactersGalleryDialog("):
        end_idx = idx
        break

if start_idx is None or end_idx is None:
    print(f"[FAIL] Границы не найдены (start={start_idx}, end={end_idx})")
    sys.exit(1)

print(f"[*] Вырезаем строки {start_idx+1}–{end_idx} ({end_idx - start_idx} строк)")
extracted_block = "\n".join(gallery_lines[start_idx:end_idx])

# 2. Формируем ui/dialogs/gallery/character_card_widget.py
imports_header = "\n".join(gallery_lines[:start_idx])
card_widget_code = f"""\"\"\"
ui/dialogs/gallery/character_card_widget.py — Виджет карточки персонажа, делегаты и рендеринг аватаров.
\"\"\"
{imports_header}

{extracted_block}
"""

target_file = PROJECT_ROOT / "ui" / "dialogs" / "gallery" / "character_card_widget.py"
target_file.write_text(card_widget_code.strip() + "\n", encoding="utf-8")

# Проверяем AST созданного модуля
try:
    ast.parse(target_file.read_text(encoding="utf-8"))
    print(f"[PASS] AST {target_file.relative_to(PROJECT_ROOT)} валиден на 100%!")
except SyntaxError as e:
    print(f"[FAIL] Ошибка синтаксиса в созданном файле: {e}")
    sys.exit(1)

# 3. Обновляем ui/dialogs/characters_gallery_dialog.py
reexport_lines = [
    "# === ВЫДЕЛЕННЫЙ ВИДЖЕТ КАРТОЧКИ И РЕНДЕРИНГ АВАТАРОВ ===",
    "from ui.dialogs.gallery.character_card_widget import (",
    "    CardItemDelegate,",
    "    render_seamless_avatar,",
    "    get_cached_circular_avatar,",
    "    CharacterCardWidget,",
    ")",
    ""
]

new_gallery_lines = gallery_lines[:start_idx] + reexport_lines + gallery_lines[end_idx:]
new_gallery_code = "\n".join(new_gallery_lines) + "\n"

# Проверяем AST обновленного файла
try:
    ast.parse(new_gallery_code)
    gallery_file.write_text(new_gallery_code, encoding="utf-8")
    print(f"[OK] {gallery_file.relative_to(PROJECT_ROOT)} успешно облегчен на {end_idx - start_idx} строк!")
except SyntaxError as e:
    print(f"[FAIL] Ошибка синтаксиса в characters_gallery_dialog.py: {e}")
    sys.exit(1)

# 4. Прогоняем все 36 тестов галереи
print("\n>>> Запуск сьюта 36 тестов галереи...")
res = subprocess.run([
    sys.executable, "-m", "pytest",
    "tests/test_characters_gallery.py",
    "tests/test_e2e_gallery_pipeline.py",
    "tests/test_gallery_extracted_modules_e2e.py",
    "tests/test_gallery_translation_and_start.py",
    "-v"
], capture_output=True, text=True)

print(res.stdout)
if res.returncode != 0:
    print(res.stderr)
    print("[FAIL] Тесты галереи провалены!")
    sys.exit(res.returncode)

print("[PASS] ВСЕ 36 ТЕСТОВ ГАЛЕРЕИ ПРОЙДЕНЫ НА 100%!")
