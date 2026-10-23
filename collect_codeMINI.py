import os
import sys
from pathlib import Path

# Имя выходного файла
OUTPUT_FILENAME = "project_code_dump.txt"

# Расширения файлов, которые считаются кодом
ALLOWED_EXTENSIONS = {
    ".py",
    ".qss",   # Стили интерфейса (PyQt)
    # Раскомментируйте при необходимости:
    # ".json",
    # ".yaml",
    # ".yml",
    # ".sql",
    # ".js",
    # ".html",
}

# Папки, которые полностью исключаются из обхода
IGNORE_DIRS = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "venv",
    ".venv",
    "env",
    "tests",
    "test",
    "build",
    "dist",
    "node_modules",
    "cache",
    "logs",
    "temp",
    "tmp",
    "migrations",
}

# Маски названий файлов, которые нужно пропускать
IGNORE_FILE_PATTERNS = [
    "test_",
    "_test.py",
    "conftest.py",
]

def should_skip_file(file_name: str) -> bool:
    name_lower = file_name.lower()
    if name_lower == OUTPUT_FILENAME.lower() or name_lower == "collect_code.py":
        return True
    for pattern in IGNORE_FILE_PATTERNS:
        if pattern in name_lower:
            return True
    return False

def collect_project_code(root_dir: str):
    root_path = Path(root_dir).resolve()
    output_path = root_path / OUTPUT_FILENAME

    collected_count = 0
    total_lines = 0

    print(f"Сканирование директории: {root_path}")

    with open(output_path, "w", encoding="utf-8") as out_file:
        for root, dirs, files in os.walk(root_path):
            # Модифицируем dirs на месте, чтобы os.walk не заходил в ненужные каталоги
            dirs[:] = [d for d in dirs if d.lower() not in IGNORE_DIRS]

            for file in sorted(files):
                ext = Path(file).suffix.lower()
                
                # Проверяем расширение и паттерны исключения
                if ext not in ALLOWED_EXTENSIONS or should_skip_file(file):
                    continue

                full_path = Path(root) / file
                rel_path = full_path.relative_to(root_path)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as in_file:
                        content = in_file.read()
                except Exception as e:
                    print(f"Ошибка при чтении {rel_path}: {e}")
                    continue

                line_count = len(content.splitlines())
                total_lines += line_count
                collected_count += 1

                # Заголовок файла в результирующем дампе
                out_file.write(f"\n{'=' * 80}\n")
                out_file.write(f"FILE: {rel_path} ({line_count} lines)\n")
                out_file.write(f"{'=' * 80}\n\n")
                out_file.write(content)
                out_file.write("\n")

                print(f"Добавлен: {rel_path} ({line_count} строк)")

    print(f"\nГотово! Собрано файлов: {collected_count} (всего строк: {total_lines})")
    print(f"Результат сохранен в: {output_path}")

if __name__ == "__main__":
    target_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    collect_project_code(target_dir)
