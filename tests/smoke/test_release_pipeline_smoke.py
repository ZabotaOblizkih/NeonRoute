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


def test_install_bat_referenced_scripts_exist():
    """
    Проверяет физическое наличие и синтаксическую валидность всех скриптов и файлов,
    вызываемых и проверяемых в install.bat:
    - requirements.txt (зависимости)
    - scripts/download_llama_server.py (загрузка движка LLM)
    - scripts/create_shortcuts.py (создание ярлыков)
    - run.bat и run_debug.bat (запуск приложения)
    """
    install_bat_path = BASE_DIR / "install.bat"
    assert install_bat_path.is_file(), "install.bat не найден в корне проекта!"
    install_bat_content = install_bat_path.read_text(encoding="utf-8")

    required_files = {
        "requirements.txt": BASE_DIR / "requirements.txt",
        "download_llama_server.py": BASE_DIR / "scripts" / "download_llama_server.py",
        "create_shortcuts.py": BASE_DIR / "scripts" / "create_shortcuts.py",
        "run.bat": BASE_DIR / "run.bat",
        "run_debug.bat": BASE_DIR / "run_debug.bat",
    }

    for name, file_path in required_files.items():
        assert file_path.is_file(), f"Критический файл для install.bat '{name}' отсутствует по пути: {file_path}"
        assert name in install_bat_content, f"install.bat не содержит упоминания целевого файла '{name}'"

    for py_script in [required_files["download_llama_server.py"], required_files["create_shortcuts.py"]]:
        code = py_script.read_text(encoding="utf-8")
        compiled = compile(code, str(py_script), "exec")
        assert compiled is not None, f"Ошибка компиляции скрипта {py_script.name}"


def test_create_shortcuts_script_execution_isolated(tmp_path, monkeypatch):
    """
    Проверяет выполнение scripts/create_shortcuts.py в полной изоляции:
    - Использует tmp_path вместо BASE_DIR;
    - Мокает create_shortcut_powershell, исключая создание реальных .lnk на рабочем столе;
    - Не оставляет временных файлов, фоновых процессов и утечек окружения.
    """
    import sys
    from scripts import create_shortcuts

    fake_run_bat = tmp_path / "run.bat"
    fake_run_bat.write_text("@echo off\r\n", encoding="utf-8")

    created_targets = []

    def fake_create_shortcut(target_path, link_path, working_dir, icon_path=None, description=""):
        created_targets.append({
            "target": target_path,
            "link": link_path,
            "workdir": working_dir,
            "desc": description,
        })
        return True

    monkeypatch.setattr(create_shortcuts, "BASE_DIR", tmp_path)
    monkeypatch.setattr(create_shortcuts, "create_shortcut_powershell", fake_create_shortcut)
    monkeypatch.setattr(sys, "argv", ["create_shortcuts.py", "--no-desktop"])

    exit_code = create_shortcuts.main()
    assert exit_code == 0
    assert len(created_targets) == 1
    assert created_targets[0]["link"] == tmp_path / "NeonRoute.lnk"
    assert created_targets[0]["target"] == fake_run_bat
    assert created_targets[0]["workdir"] == tmp_path


def test_install_bat_pipeline_contract():
    """
    Архитектурный тест-контракт для install.bat.
    Гарантирует соблюдение ключевых инвариантов инсталлятора с понятными диагностическими
    сообщениями для ИИ в случае регрессий:
    1. Поддержка автономного режима без зависания в CI (--unattended / CI guard перед pause);
    2. Поддержка оффлайн-установки через локальный кэш wheels (no-index, skip llama);
    3. Корректность 5-фазного пайплайна ([1/5] ... [5/5]);
    4. Безопасный код возврата exit /b при сбоях (не чистый exit, закрывающий терминал).
    """
    install_bat = (BASE_DIR / "install.bat").read_text(encoding="utf-8")

    # 1. CI / Unattended guard (защита от зависания тестов и CI в бесконечном pause)
    assert 'if not "%1"=="--unattended" if not defined CI pause' in install_bat, (
        "[Installer Contract Violation] В install.bat отсутствует или повреждён guard для команды 'pause'. "
        "Любой вызов 'pause' обязан оборачиваться в: 'if not \"%1\"==\"--unattended\" if not defined CI pause', "
        "иначе автоматические тесты и CI-пайплайны зависают по таймауту."
    )

    # 2. Оффлайн-контракт для wheels (дистрибуция без интернета)
    assert '--find-links=wheels' in install_bat, (
        "[Installer Contract Violation] install.bat обязан поддерживать оффлайн-установку зависимостей "
        "из папки wheels/ через флаг '--find-links=wheels'."
    )
    assert 'if exist "wheels\\" set "SKIP_LLAMA=1"' in install_bat, (
        "[Installer Contract Violation] При наличии папки wheels/ скачивание llama-server из сети "
        "должно автоматически отключаться ('if exist \"wheels\\\" set \"SKIP_LLAMA=1\"')."
    )

    # 3. 5-фазная последовательность этапов установки
    stages = ["[1/5]", "[2/5]", "[3/5]", "[4/5]", "[5/5]"]
    last_pos = -1
    for stage in stages:
        pos = install_bat.find(stage)
        assert pos != -1, f"[Installer Contract Violation] В install.bat отсутствует маркер этапа '{stage}'."
        assert pos > last_pos, (
            f"[Installer Contract Violation] Нарушена хронологическая последовательность этапов: "
            f"этап '{stage}' расположен раньше предыдущего шага."
        )
        last_pos = pos

    # 4. Безопасные коды возврата (exit /b, а не чистый exit, убивающий родительскую консоль)
    lines = install_bat.splitlines()
    for line_idx, line in enumerate(lines, start=1):
        clean = line.strip()
        if clean.lower().startswith("exit ") and not clean.lower().startswith("exit /b"):
            raise AssertionError(
                f"[Installer Contract Violation] Строка {line_idx}: '{clean}'. "
                f"В пакетных файлах необходимо использовать 'exit /b <код>', "
                f"чтобы не закрывать командную строку пользователя при ошибке."
            )


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
