import ast
from pathlib import Path

target = Path(r"C:\NeonRoute\tests\test_mobile_art_full.py")

def apply_patch():
    if not target.exists():
        print(f"[ERROR] Файл {target} не найден!")
        return

    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    modified = False
    new_lines = []

    for i, line in enumerate(lines):
        new_lines.append(line)
        # Ищем строку с session_id внутри метода test_artdirector_pipeline_integration
        if '"session_id": session_id,' in line and not any('"model_id"' in lines[j] for j in range(max(0, i-2), min(len(lines), i+4))):
            indent = line[:len(line) - len(line.lstrip())]
            new_lines.append(f'{indent}"model_id": "cyberrealistic",\n')
            modified = True
            print(f"[MODIFIED] Добавлена строка: {indent}\"model_id\": \"cyberrealistic\",")

    if not modified:
        print("[INFO] Строка model_id уже присутствует или целевой блок не найден.")
        return

    new_content = "".join(new_lines)
    
    # Контроль целостности AST
    try:
        ast.parse(new_content)
        print("[OK] AST синтаксис валиден.")
    except SyntaxError as e:
        print(f"[FATAL] Ошибка синтаксиса: {e}. Файл не изменен.")
        return

    target.write_text(new_content, encoding="utf-8")
    print(f"[SUCCESS] Файл {target} успешно сохранен.")

if __name__ == "__main__":
    apply_patch()
