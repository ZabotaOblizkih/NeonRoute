"""
test_patches_direct.py
Сквозное тестирование архитектурных патчей NeonRoute.
Запуск: python test_patches_direct.py  ИЛИ  pytest test_patches_direct.py -v
"""

import sys
import os
import shutil
import tempfile
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

# Настройка путей импорта для NeonRoute
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Отключаем лишний вывод логов во время тестов
logging.basicConfig(level=logging.ERROR)

PASSED = 0
FAILED = 0


def run_test(name: str, test_func):
    """Вспомогательный раннер для запуска без pytest."""
    global PASSED, FAILED
    try:
        test_func()
        print(f"  [PASS] {name}")
        PASSED += 1
    except AssertionError as e:
        print(f"  [FAIL] {name}: {e}")
        FAILED += 1
    except Exception as e:
        print(f"  [ERROR] {name}: {type(e).__name__} - {e}")
        FAILED += 1


# =========================================================================
# ТЕСТ 1: Инициализация GitSandbox и корректность .gitignore
# =========================================================================
def test_git_sandbox_init_and_gitignore():
    from services.git_sandbox import GitSandboxManager

    with tempfile.TemporaryDirectory() as tmp_dir:
        ws = Path(tmp_dir) / "workspace"
        ws.mkdir()

        ok = GitSandboxManager.init_repo(ws)
        assert ok is True, "Инициализация репозитория вернула False"

        gitignore = ws / ".gitignore"
        assert gitignore.is_file(), ".gitignore не был создан"

        content = gitignore.read_text(encoding="utf-8")
        assert ".neonroute_last_failure.json" in content, (
            ".neonroute_last_failure.json отсутствует в .gitignore"
        )
        assert "__pycache__/" in content, "__pycache__/ отсутствует в .gitignore"


# =========================================================================
# ТЕСТ 2: Память о сбоях (Запись -> Чтение -> Атомарное удаление)
# =========================================================================
def test_git_sandbox_failure_memory_lifecycle():
    from services.git_sandbox import GitSandboxManager

    with tempfile.TemporaryDirectory() as tmp_dir:
        ws = Path(tmp_dir) / "workspace"
        ws.mkdir()

        # 1. Запись сбоя
        GitSandboxManager.record_failure(
            ws_dir=ws,
            task_title="Таблица с формулами",
            turn=1,
            error_msg="NameError: Russian formula =СУММ",
            recipe="Используй только англоязычные формулы =SUM"
        )

        fail_file = ws / ".neonroute_last_failure.json"
        assert fail_file.is_file(), "Файл памяти сбоя не был создан на диске"

        # 2. Извлечение памяти (pop)
        data = GitSandboxManager.pop_last_failure(ws)
        assert data is not None, "Метод pop_last_failure вернул None"
        assert data["failed_turn"] == 1, f"Неверный ход: {data.get('failed_turn')}"
        assert "=СУММ" in data["error"], "Текст ошибки не сохранился в дампе"
        assert "=SUM" in data["recipe"], "Рецепт устранения не сохранился в дампе"

        # 3. Проверка однократности (файл должен быть удален после извлечения)
        assert not fail_file.exists(), (
            "Файл памяти сбоя не был удален после чтения (pop_last_failure)"
        )
        assert GitSandboxManager.pop_last_failure(ws) is None, (
            "Повторный вызов должен возвращать None"
        )


# =========================================================================
# ТЕСТ 3: Чекпоинт и откат (Rollback) мусорных файлов
# =========================================================================
def test_git_sandbox_checkpoint_and_rollback():
    from services.git_sandbox import GitSandboxManager

    with tempfile.TemporaryDirectory() as tmp_dir:
        ws = Path(tmp_dir) / "workspace"
        ws.mkdir()

        GitSandboxManager.init_repo(ws)

        # Стабильное состояние
        stable_file = ws / "app.py"
        stable_file.write_text("print('stable')", encoding="utf-8")
        GitSandboxManager.checkpoint(ws, "Initial stable code")

        # Модель вносит сломанные изменения и создает временный мусор
        stable_file.write_text("broken syntax !!!", encoding="utf-8")
        garbage_file = ws / "untracked_rubbish.py"
        garbage_file.write_text("# garbage", encoding="utf-8")

        # Откат
        rollback_ok = GitSandboxManager.rollback(ws)
        assert rollback_ok is True, "Откат завершился ошибкой"

        # Проверка целостности
        assert stable_file.read_text(encoding="utf-8") == "print('stable')", (
            "Исходный файл не вернулся к стабильной версии"
        )
        assert not garbage_file.exists(), (
            "Мусорный несохраненный файл не был удален при откате"
        )


# =========================================================================
# ТЕСТ 4: EnvironmentDoctor (Каталог и тайминг < 50 мс)
# =========================================================================
def test_environment_doctor_catalog_and_performance():
    from services.environment_doctor import EnvironmentDoctor, DEPENDENCY_CATALOG

    # Проверка обязательных зависимостей
    for required in ("git", "pygame", "customtkinter", "openpyxl", "docx"):
        assert required in DEPENDENCY_CATALOG, f"Зависимость '{required}' отсутствует в каталоге"

    import time
    t0 = time.perf_counter()
    missing = EnvironmentDoctor.check_missing(verbose=False)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert isinstance(missing, list), "check_missing должен возвращать список"
    assert elapsed_ms < 50.0, f"Сканирование длилось слишком долго: {elapsed_ms:.2f} мс (>50 мс)"


# =========================================================================
# ТЕСТ 5: Выполнение инструмента git_checkpoint через ToolEngine
# =========================================================================
def test_tool_engine_git_checkpoint():
    from services.tool_engine import ToolEngine, ToolCall
    from services.sandbox_manager import SandboxManager
    from services.git_sandbox import GitSandboxManager

    with tempfile.TemporaryDirectory() as tmp_dir:
        ws = Path(tmp_dir) / "workspace"
        ws.mkdir()

        with patch.object(SandboxManager, "get_workspace_dir", return_value=ws):
            GitSandboxManager.init_repo(ws)

            call = ToolCall(
                name="git_checkpoint",
                params={"message": "Тестовый коммит через тул", "backup": "false"},
                body=""
            )
            res = ToolEngine.execute_tool_call(call)

            assert res.success is True, f"Инструмент git_checkpoint завершился с ошибкой: {res.output}"
            assert "зафиксирован" in res.output.lower(), f"Неожиданный ответ тула: {res.output}"


# =========================================================================
# ТЕСТ 6: Подмешивание памяти сбоя в контекст EngineerPlanner
# =========================================================================
def test_engineer_planner_injects_failure_context():
    from services.engineer_planner import EngineerPlanner, ArchitectPlan
    from services.git_sandbox import GitSandboxManager
    from services.sandbox_manager import SandboxManager

    with tempfile.TemporaryDirectory() as tmp_dir:
        ws = Path(tmp_dir) / "workspace"
        ws.mkdir()

        # Записываем сбой
        GitSandboxManager.record_failure(
            ws_dir=ws,
            task_title="Создание скрипта",
            turn=2,
            error_msg="ZeroDivisionError: division by zero",
            recipe="Добавь проверку делителя на ноль"
        )

        mock_client = MagicMock()
        mock_client.chat_completion.return_value = json.dumps({
            "format": "cli",
            "intent": "new",
            "target_artifact": "main.py",
            "plan": "Реализация с проверкой делителя"
        })

        with patch.object(SandboxManager, "get_workspace_dir", return_value=ws):
            plan = EngineerPlanner.plan_architecture(
                task_prompt="Исправь вычисление формулы",
                ws_dir=ws,
                client=mock_client,
                target_model="test_model"
            )

            assert isinstance(plan, ArchitectPlan), "План не является объектом ArchitectPlan"
            assert "[ПАМЯТЬ О ПРЕДЫДУЩЕМ СБОЕ В ЭТОМ ПРОЕКТЕ]" in plan.plan_content, (
                "Блок памяти о сбое не был включен в plan_content"
            )
            assert "ZeroDivisionError" in plan.plan_content, (
                "Текст ошибки не был обнаружен в тексте плана"
            )


# =========================================================================
# ТЕСТ 7: Наличие метода open_dependency_dialog в MainWindow
# =========================================================================
def test_main_window_has_open_dependency_dialog():
    from ui.main_window import MainWindow

    assert hasattr(MainWindow, "open_dependency_dialog"), (
        "В классе MainWindow отсутствует метод open_dependency_dialog! "
        "Это вызовет AttributeError при нажатии на меню Справка."
    )
    assert callable(getattr(MainWindow, "open_dependency_dialog")), (
        "Атрибут open_dependency_dialog должен быть вызываемым методом"
    )


# =========================================================================
# ТОЧКА ВХОДА (Запуск напрямую)
# =========================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("  ЗАПУСК АВТОНОМНОГО ТЕСТИРОВАНИЯ СИСТЕМЫ NEONROUTE")
    print("=" * 70)

    run_test("1. Инициализация GitSandbox и .gitignore", test_git_sandbox_init_and_gitignore)
    run_test("2. Жизненный цикл памяти о сбоях (Record -> Pop -> Clean)", test_git_sandbox_failure_memory_lifecycle)
    run_test("3. Атомарный откат файлов и удаление мусора (Rollback)", test_git_sandbox_checkpoint_and_rollback)
    run_test("4. Каталог и скорость EnvironmentDoctor (< 50 мс)", test_environment_doctor_catalog_and_performance)
    run_test("5. Инструмент <<TOOL:git_checkpoint>> в ToolEngine", test_tool_engine_git_checkpoint)
    run_test("6. Инъекция памяти ошибок в ArchitectPlan", test_engineer_planner_injects_failure_context)
    run_test("7. Метод open_dependency_dialog в MainWindow", test_main_window_has_open_dependency_dialog)

    print("=" * 70)
    print(f"  ИТОГ: Успешно: {PASSED} | Ошибок: {FAILED}")
    print("=" * 70)

    if FAILED > 0:
        sys.exit(1)