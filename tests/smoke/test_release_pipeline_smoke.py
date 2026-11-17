import os
import re
from pathlib import Path
import pytest
from config import BASE_DIR
from scripts.build_release import (
    PORTABLE_PYTHON_ARCHIVE_NAME,
    get_stdlib_modules,
    parse_requirements_txt,
    audit_ast_dependencies,
    assemble_release,
)


def test_batch_files_hygiene():
    """
    Проверяет гигиену всех пакетных файлов (.bat) проекта:
    - Окончания строк строго Windows CRLF (\r\n);
    - Отсутствие UTF-8 BOM (\xef\xbb\xbf);
    - Отсутствие опасных спецсимволов (<, >, (, )) в некавыченных командах echo.
    """
    bat_files = [
        BASE_DIR / "install.bat",
        BASE_DIR / "run.bat",
        BASE_DIR / "run_debug.bat",
        BASE_DIR / "run_llama_server.bat",
        BASE_DIR / "build_release.bat",
    ]

    for bat in bat_files:
        assert bat.is_file(), f"Батник {bat.name} не найден!"
        raw_data = bat.read_bytes()

        # 1. Проверка отсутствия BOM
        assert not raw_data.startswith(b"\xef\xbb\xbf"), f"{bat.name} содержит UTF-8 BOM!"

        # 2. Проверка CRLF
        assert b"\r\n" in raw_data, f"{bat.name} не содержит CRLF!"
        lines_raw = raw_data.split(b"\n")
        for idx, line in enumerate(lines_raw[:-1]):
            assert line.endswith(b"\r"), f"{bat.name} строка {idx + 1} заканчивается на LF вместо CRLF"

        # 3. Проверка безопасного синтаксиса echo
        text = raw_data.decode("utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            s = line.strip()
            if s.lower().startswith("echo") and not s.lower().startswith("echo."):
                # Исключаем строки с явным перенаправлением в пайплайн (например, echo text | findstr) или закомментированные
                if "::" in s or "rem " in s.lower() or "| findstr" in s.lower():
                    continue
                # Удаляем кавыченные фрагменты перед проверкой
                unquoted = re.sub(r'"[^"]*"', "", s[4:])
                for bad_char in ["<", ">", "(", ")"]:
                    assert bad_char not in unquoted, (
                        f"{bat.name}:{line_no} содержит опасный символ '{bad_char}' в некавыченном echo: {s}"
                    )


def test_install_bat_embedded_python_priority():
    """
    Проверяет, что install.bat в первую очередь проверяет runtime\\python\\python.exe
    и при его наличии не создаёт venv\\.
    """
    install_bat = (BASE_DIR / "install.bat").read_text(encoding="utf-8")
    assert 'if exist "runtime\\python\\python.exe"' in install_bat
    assert 'set "PYTHON_BIN=runtime\\python\\python.exe"' in install_bat
    assert 'goto :python_ready' in install_bat


def test_install_bat_llama_server_downloader_integration():
    """
    Проверяет интеграцию install.bat со скриптом download_llama_server.py
    и поддержку флага --skip-llama и переменной NEONROUTE_SKIP_LLAMA=1.
    """
    install_bat = (BASE_DIR / "install.bat").read_text(encoding="utf-8")
    assert 'scripts\\download_llama_server.py --if-missing' in install_bat
    assert 'NEONROUTE_SKIP_LLAMA' in install_bat
    assert '--skip-llama' in install_bat


def test_run_bat_embedded_python_priority():
    """
    Проверяет, что run.bat и run_debug.bat в первую очередь запускают
    runtime\\python\\pythonw.exe и runtime\\python\\python.exe.
    """
    run_bat = (BASE_DIR / "run.bat").read_text(encoding="utf-8")
    assert 'if exist "runtime\\python\\pythonw.exe"' in run_bat
    assert 'start "" "runtime\\python\\pythonw.exe" main.py' in run_bat

    run_debug = (BASE_DIR / "run_debug.bat").read_text(encoding="utf-8")
    assert 'if exist "runtime\\python\\python.exe"' in run_debug
    assert '"runtime\\python\\python.exe" main.py' in run_debug


def test_ast_dependency_audit_smoke():
    """
    Проверяет Stage A пайплайна: аудит зависимостей через AST.
    Все импорты кодовой базы должны быть покрыты requirements.txt.
    """
    success, errors, _ = audit_ast_dependencies(BASE_DIR)
    assert success, f"AST аудит не прошёл: {errors}"


def test_art_director_models_excluded_from_release():
    """
    Проверяет, что art_director_models.json исключён из списка копируемых файлов
    в конвейере build_release.py, чтобы локальные чекпоинты разработчика не утекали в релиз.
    """
    build_script = (BASE_DIR / "scripts" / "build_release.py").read_text(encoding="utf-8")
    assert '"art_director_models.json"' not in build_script, (
        "art_director_models.json не должен присутствовать в списке копируемых в релиз файлов!"
    )


def test_hotfix_excluded_from_release():
    """
    Проверяет, что каталог hotfix_v098/ исключён из конвейера сборки релиза build_release.py
    и не попадёт в итоговый zip-архив дистрибутива.
    """
    build_script = (BASE_DIR / "scripts" / "build_release.py").read_text(encoding="utf-8")
    assert '"hotfix_v098"' not in build_script, (
        "hotfix_v098 не должен присутствовать в списке копируемых в релиз каталогов!"
    )
    # Проверяем, что в assemble_release явно не добавлены hotfix-директории
    from scripts.build_release import assemble_release
    # Проверяем список source_dirs в коде
    assert 'item.startswith("hotfix_")' in build_script
