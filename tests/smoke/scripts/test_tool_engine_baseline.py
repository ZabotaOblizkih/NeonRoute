import sys
import os
import inspect
import tempfile
import traceback
from pathlib import Path

# Добавляем корень проекта в sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

def run_baseline_test():
    print("=" * 60)
    print(">>> СТЕНД: Инспекция и Smoke-тестирование services.tool_engine")
    print("=" * 60)

    try:
        from services.tool_engine import ToolEngine
        print("[OK] Модуль services.tool_engine успешно импортирован.")
    except Exception as e:
        print(f"[FAIL] Ошибка импорта services.tool_engine: {e}")
        traceback.print_exc()
        sys.exit(1)

    # 1. Анализ сигнатуры и доступных методов
    init_sig = inspect.signature(ToolEngine.__init__)
    print(f"[*] Сигнатура ToolEngine.__init__: {init_sig}")
    methods = [m for m in dir(ToolEngine) if not m.startswith("_") and callable(getattr(ToolEngine, m))]
    print(f"[*] Найдено публичных методов ({len(methods)}): {', '.join(methods)}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        sandbox_path = Path(tmp_dir)
        print(f"[*] Временная изолированная песочница: {sandbox_path}")

        # 2. Инициализация экземпляра
        engine = None
        init_kwargs = {}
        for param in init_sig.parameters.values():
            if param.name in ("sandbox_dir", "workspace_root", "workspace_dir", "project_dir", "root_dir"):
                init_kwargs[param.name] = str(sandbox_path)
            elif param.name in ("session_id",):
                init_kwargs[param.name] = "baseline_test_sess"
            elif param.name in ("task_queue", "dispatcher"):
                init_kwargs[param.name] = None

        try:
            if init_kwargs:
                engine = ToolEngine(**init_kwargs)
            else:
                try:
                    engine = ToolEngine(str(sandbox_path))
                except TypeError:
                    engine = ToolEngine()
            print("[OK] Экземпляр ToolEngine успешно создан.")
        except Exception as e:
            print(f"[FAIL] Ошибка инициализации ToolEngine: {e}")
            traceback.print_exc()
            sys.exit(1)

        # 3. Тест write_file
        print("\n--- [1/5] Проверка write_file ---")
        test_file = "sample_module.py"
        initial_code = (
            "import math\n\n"
            "def calculate_area(radius: float) -> float:\n"
            "    return math.pi * (radius ** 2)\n\n"
            "class EngineRunner:\n"
            "    def get_version(self):\n"
            "        return '1.0.0'\n"
        )
        if hasattr(engine, "write_file"):
            try:
                res_write = engine.write_file(test_file, initial_code)
                print(f"    Результат write_file: {res_write}")
                written_path = sandbox_path / test_file
                if not written_path.exists():
                    # Проверяем, возможно движок положил в подпапку
                    matches = list(sandbox_path.glob(f"**/{test_file}"))
                    if matches:
                        written_path = matches[0]
                assert written_path.exists(), f"Файл {test_file} не найден на диске!"
                print(f"[PASS] write_file корректно записал файл: {written_path}")
            except Exception as e:
                print(f"[FAIL] write_file упал с ошибкой: {e}")
                traceback.print_exc()
                sys.exit(1)
        else:
            print("[WARN] Метод write_file не найден напрямую в ToolEngine!")

        # 4. Тест replace_symbol
        print("\n--- [2/5] Проверка replace_symbol ---")
        if hasattr(engine, "replace_symbol"):
            new_func = (
                "def calculate_area(radius: float) -> float:\n"
                "    # Обновленный расчет\n"
                "    return 3.14159 * (radius ** 2)\n"
            )
            try:
                res_sym = engine.replace_symbol(test_file, "calculate_area", new_func)
                print(f"    Результат replace_symbol: {res_sym}")
                content = written_path.read_text(encoding="utf-8")
                assert "Обновленный расчет" in content, "Новый символ не был применен в файле!"
                print("[PASS] replace_symbol корректно обновил узел AST.")
            except Exception as e:
                print(f"[FAIL] replace_symbol упал с ошибкой: {e}")
                traceback.print_exc()
                sys.exit(1)
        else:
            print("[INFO] Метод replace_symbol отсутствует в ToolEngine.")

        # 5. Тест patch_file
        print("\n--- [3/5] Проверка patch_file ---")
        if hasattr(engine, "patch_file"):
            search_block = "return '1.0.0'"
            replace_block = "return '2.0.0'"
            try:
                res_patch = engine.patch_file(test_file, search_block, replace_block)
                print(f"    Результат patch_file: {res_patch}")
                content = written_path.read_text(encoding="utf-8")
                assert "2.0.0" in content, "Патч не был применен!"
                print("[PASS] patch_file успешно изменил блок.")
            except Exception as e:
                print(f"[FAIL] patch_file упал с ошибкой: {e}")
                traceback.print_exc()
                sys.exit(1)
        else:
            print("[INFO] Метод patch_file отсутствует в ToolEngine.")

        # 6. Тест check_artifact_substance
        print("\n--- [4/5] Проверка check_artifact_substance ---")
        if hasattr(engine, "check_artifact_substance"):
            try:
                res_subst = engine.check_artifact_substance(test_file)
                print(f"    Результат check_artifact_substance: {res_subst}")
                print("[PASS] check_artifact_substance отработал штатно.")
            except Exception as e:
                print(f"[FAIL] check_artifact_substance упал с ошибкой: {e}")
                traceback.print_exc()
                sys.exit(1)
        else:
            print("[INFO] Метод check_artifact_substance отсутствует в ToolEngine.")

        # 7. Тест run_script
        print("\n--- [5/5] Проверка run_script ---")
        runner_file = "test_run.py"
        runner_code = "print('STATUS_OK_TOOL_TEST')\n"
        try:
            if hasattr(engine, "write_file"):
                engine.write_file(runner_file, runner_code)
            if hasattr(engine, "run_script"):
                res_run = engine.run_script(runner_file)
                print(f"    Результат run_script: {res_run}")
                print("[PASS] run_script успешно выполнил сценарий.")
        except Exception as e:
            print(f"[FAIL] run_script упал с ошибкой: {e}")
            traceback.print_exc()
            sys.exit(1)

    print("\n" + "=" * 60)
    print(">>> ИТОГ: Baseline стенд ToolEngine пройден без критических ошибок!")
    print("=" * 60)

if __name__ == "__main__":
    run_baseline_test()
