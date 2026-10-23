"""
services/sandbox_runner.py
Изолированный исполнитель скриптов песочницы NeonRoute.
Запускается через: python -m services.sandbox_runner <абсолютный_путь_к_скрипту>
"""

import os
import sys
import runpy
from pathlib import Path

def validate_module_syntax(code: str, filename: str = "<module>") -> bool:
    """
    Проверка корректности синтаксиса библиотечного модуля без требования точки входа.
    Модули/библиотеки проверяются только на синтаксическую валидность через ast.parse.
    """
    try:
        import ast
        ast.parse(code, filename=filename)
        return True
    except SyntaxError:
        return False


def main() -> None:
    # 1. Активация периметра безопасности (если модуль доступен)
    try:
        from services.sandbox_guard import install_guard
        install_guard()
    except Exception:
        pass
    if len(sys.argv) < 2:
        sys.stderr.write("Ошибка: Не указан целевой скрипт для запуска.\n")
        sys.exit(1)

    target_script = Path(sys.argv[1]).resolve()
    if not target_script.is_file():
        sys.stderr.write(f"Ошибка: Скрипт не найден: {target_script}\n")
        sys.exit(1)

    # Переключаем рабочую директорию в папку скрипта
    script_dir = str(target_script.parent.resolve())
    os.chdir(script_dir)

    # Добавляем директорию скрипта в начало sys.path для корректного импорта локальных модулей
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Выставляем честный sys.argv для скрипта, как будто он вызван напрямую
    sys.argv = [str(target_script)] + sys.argv[2:]

    # Исполнение через runpy:
    # - выставляет __file__ = str(target_script)
    # - задает __name__ = '__main__'
    # - сохраняет настоящие номера строк файла в stderr traceback
    try:
        runpy.run_path(str(target_script), run_name="__main__")
    except SystemExit as se:
        sys.exit(se.code)


if __name__ == "__main__":
    main()
