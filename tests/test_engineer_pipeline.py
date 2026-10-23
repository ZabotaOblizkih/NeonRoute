"""
tests/test_engineer_pipeline.py
Полный сьют из 26 юнит-тестов для верификации контура субагента «Инженер»:
- AST-скелетирование и инспекция контекста (ASTSkeletonizer)
- CodeValidator (Scope Guard, Event Loop, Entry Point, Anti-Truncation, Contracts, Linter)
- PatchApplier (Exact, Indent-Shift, Normalized Match, Fuzzy Matcher)
- ToolEngine (In-Memory AST Guard, patch_file, read_file)
- SandboxManager (Path traversal, isolation, blacklist, headless monkey-patch)
"""

import os
import sys
import unittest
import tempfile
import shutil
from pathlib import Path

# Добавляем корень проекта в путь импорта модулей
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.code_engine import (
    ASTSkeletonizer,
    PatchApplier,
    PatchResult,
    ExecutionSandbox,
    SelfHealingEngine,
    CodeValidator,
)
from services.tool_engine import ToolEngine, ToolCall
from services.sandbox_manager import SandboxManager


class TestEngineerPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = Path(tempfile.mkdtemp(prefix="neonroute_test_ws_"))
        SandboxManager.workspace_dir = cls.test_dir

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # 1. ASTSkeletonizer
    # --------------------------------------------------------------------------
    def test_01_ast_skeletonizer_functions_and_classes(self):
        """Проверяет компрессию кода: сохранение сигнатур, docstrings и замену тел на '...'."""
        source = (
            "import os\n"
            "MAX_RETRIES = 5\n\n"
            "class Engine:\n"
            '    """Базовый класс движка."""\n'
            "    def start(self, mode: str) -> bool:\n"
            '        """Запуск движка."""\n'
            "        x = 10\n"
            "        return True\n"
        )
        skel = ASTSkeletonizer.skeletonize(source)
        self.assertIn("import os", skel)
        self.assertIn("MAX_RETRIES = 5", skel)
        self.assertIn("class Engine:", skel)
        self.assertIn("Базовый класс движка.", skel)
        self.assertIn("def start(self, mode: str) -> bool:", skel)
        self.assertIn("...", skel)
        self.assertNotIn("x = 10", skel)

    def test_02_ast_skeletonizer_extract_symbol(self):
        """Проверяет точечное извлечение исходного кода функции или метода по имени."""
        source = (
            "def calculate_tax(amount):\n"
            "    return amount * 0.2\n\n"
            "class Player:\n"
            "    def jump(self):\n"
            "        self.y += 10\n"
        )
        fn_code = ASTSkeletonizer.extract_symbol(source, "calculate_tax")
        self.assertIsNotNone(fn_code)
        self.assertIn("def calculate_tax(amount):", fn_code)
        self.assertIn("return amount * 0.2", fn_code)

        method_code = ASTSkeletonizer.extract_symbol(source, "Player.jump")
        self.assertIsNotNone(method_code)
        self.assertIn("def jump(self):", method_code)
        self.assertIn("self.y += 10", method_code)

    # --------------------------------------------------------------------------
    # 2. CodeValidator: Синтаксис и Безопасность
    # --------------------------------------------------------------------------
    def test_03_code_validator_syntax_error_detection(self):
        """Проверяет детектор синтаксических ошибок (SyntaxError) до записи на диск."""
        broken_code = "def foo(:\n    pass\n"
        is_valid, msg = CodeValidator.validate_python_syntax("script.py", broken_code)
        self.assertFalse(is_valid)
        self.assertIn("SyntaxError", msg)

    def test_04_code_validator_ast_safety_guard_banned_modules(self):
        """Проверяет блокировку опасных системных и сетевых модулей (AST Safety Guard)."""
        banned_codes = [
            "import socket\nsocket.socket()",
            "from subprocess import Popen\nPopen(['calc'])",
            "import ctypes\nctypes.windll",
        ]
        for code in banned_codes:
            is_valid, msg = CodeValidator.validate_python_syntax("script.py", code)
            self.assertFalse(is_valid, f"Код должен быть заблокирован: {code}")
            self.assertIn("AST Safety Guard", msg)

    # --------------------------------------------------------------------------
    # 3. CodeValidator: Точка входа и Оконный цикл
    # --------------------------------------------------------------------------
    def test_05_code_validator_missing_entry_point(self):
        """Проверяет требование наличия точки входа в исполняемом скрипте."""
        code_without_entry = (
            "class App:\n"
            "    def run(self):\n"
            "        print('Running')\n"
        )
        is_valid, msg = CodeValidator.validate_python_syntax("app.py", code_without_entry, is_entry_file=True)
        self.assertFalse(is_valid)
        self.assertIn("MissingEntryPoint", msg)

    def test_06_code_validator_missing_event_loop(self):
        """Проверяет обязательность цикла событий для оконных приложений (Tkinter / Pygame)."""
        gui_code = (
            "import tkinter as tk\n"
            "root = tk.Tk()\n"
            "label = tk.Label(root, text='Hello')\n"
        )
        is_valid, msg = CodeValidator.validate_python_syntax("gui.py", gui_code, is_entry_file=True)
        self.assertFalse(is_valid)
        self.assertIn("MissingEventLoop", msg)

    # --------------------------------------------------------------------------
    # 4. CodeValidator: Scope Guard и Неиспользуемые методы
    # --------------------------------------------------------------------------
    def test_07_code_validator_undefined_names_scope_guard(self):
        """Проверяет обнаружение необъявленных переменных (NameError) через symtable."""
        code_with_name_error = (
            "def process():\n"
            "    return total_price * 1.2\n"
        )
        err = CodeValidator.check_undefined_names(code_with_name_error, "calc.py")
        self.assertIsNotNone(err)
        self.assertIn("total_price", err)
        self.assertIn("NameError", err)

    def test_08_code_validator_unreferenced_methods(self):
        """Проверяет детектор методов, которые объявлены в классе, но нигде не вызваны."""
        code_unwired = (
            "class Game:\n"
            "    def __init__(self):\n"
            "        pass\n"
            "    def _fire_bullet(self):\n"
            "        pass\n"
            "if __name__ == '__main__':\n"
            "    g = Game()\n"
        )
        err = CodeValidator.check_unreferenced_methods(code_unwired, "game.py")
        self.assertIsNotNone(err)
        self.assertIn("_fire_bullet", err)
        self.assertIn("UnwiredMethod", err)

    # --------------------------------------------------------------------------
    # 5. CodeValidator: Антипаттерны и Линтер
    # --------------------------------------------------------------------------
    def test_09_code_validator_openpyxl_anti_pattern_guard(self):
        """Проверяет перехват распространённых ошибок openpyxl (.formula и мутация стилей)."""
        code_formula = "import openpyxl\nws['A1'].formula = '=SUM(B1:B10)'\n"
        is_valid, msg = CodeValidator.validate_python_syntax("doc.py", code_formula, is_entry_file=False)
        self.assertFalse(is_valid)
        self.assertIn(".formula", msg)

        code_style = "import openpyxl\ncell.font.bold = True\n"
        is_valid2, msg2 = CodeValidator.validate_python_syntax("doc.py", code_style, is_entry_file=False)
        self.assertFalse(is_valid2)
        self.assertIn("неизменяемы", msg2)

    def test_10_code_validator_in_memory_ast_linter_missing_imports(self):
        """Проверяет автоматическое внедрение забытых стандартных и офисных импортов."""
        source = (
            "def generate():\n"
            "    t = time.time()\n"
            "    val = math.sqrt(16)\n"
            "    wb = Workbook()\n"
            "    return val\n"
        )
        linted = CodeValidator.apply_ast_linter_to_code(source)
        self.assertIn("import time", linted)
        self.assertIn("import math", linted)
        self.assertIn("import openpyxl", linted)

    # --------------------------------------------------------------------------
    # 6. CodeValidator: Anti-Truncation & Lazy Tokens
    # --------------------------------------------------------------------------
    def test_11_code_validator_lazy_token_guard(self):
        """Проверяет блокировку ленивых комментариев модели ('# rest of code')."""
        orig = "def a(): pass\ndef b(): pass\n"
        new_lazy = "def a(): pass\n# rest of code\n"
        is_intact, msg = CodeValidator.validate_code_integrity(
            "mod.py", orig, new_lazy, is_maintenance=True, is_ephemeral=False
        )
        self.assertFalse(is_intact)
        self.assertIn("сокращения кода", msg)

    def test_12_code_validator_anti_truncation_abnormal_shrinkage(self):
        """Проверяет перехват аномального физического сжатия файла более чем на 75%."""
        orig = "def compute():\n" + ("    x = 1\n" * 40)
        truncated = "def compute():\n    pass\n"
        is_intact, msg = CodeValidator.validate_code_integrity(
            "calc.py", orig, truncated, is_maintenance=True, is_ephemeral=False
        )
        self.assertFalse(is_intact)
        self.assertIn("Аномальное сокращение", msg)

    def test_13_code_validator_cross_module_contracts(self):
        """Проверяет вызовы несуществующих методов между проектируемыми файлами."""
        blocks = [
            ("player.py", "class Player:\n    def walk(self): pass\n"),
            ("main.py", "from player import Player\np = Player()\np.fly()\n"),
        ]
        err = CodeValidator.check_cross_module_contracts(blocks)
        self.assertIsNotNone(err)
        self.assertIn("MissingMethod", err)
        self.assertIn("fly", err)

    # --------------------------------------------------------------------------
    # 7. PatchApplier: Точный и Детерминированный сдвиг
    # --------------------------------------------------------------------------
    def test_14_patch_applier_exact_match(self):
        """Проверяет точное применение патча SEARCH/REPLACE."""
        content = "x = 10\ny = 20\nz = 30\n"
        diff = "<<<<<<< SEARCH\ny = 20\n=======\ny = 25\n>>>>>>> REPLACE"
        res = PatchApplier.apply_diff_text(content, diff)
        self.assertTrue(res.success)
        self.assertEqual(res.new_content, "x = 10\ny = 25\nz = 30\n")

    def test_15_patch_applier_indent_shift(self):
        """Проверяет автоматическую адаптацию отступов при вставке блока внутрь метода."""
        content = (
            "class Controller:\n"
            "    def update(self):\n"
            "        self.active = True\n"
            "        self.tick()\n"
        )
        diff = (
            "<<<<<<< SEARCH\n"
            "self.active = True\n"
            "self.tick()\n"
            "=======\n"
            "self.active = False\n"
            "self.reset()\n"
            ">>>>>>> REPLACE"
        )
        res = PatchApplier.apply_diff_text(content, diff)
        self.assertTrue(res.success)
        self.assertIn("        self.active = False\n        self.reset()", res.new_content)

    def test_16_patch_applier_multiple_blocks(self):
        """Проверяет последовательное применение нескольких диффов к одному файлу."""
        content = "step_1 = False\nstep_2 = False\n"
        diff = (
            "<<<<<<< SEARCH\nstep_1 = False\n=======\nstep_1 = True\n>>>>>>> REPLACE\n\n"
            "<<<<<<< SEARCH\nstep_2 = False\n=======\nstep_2 = True\n>>>>>>> REPLACE"
        )
        res = PatchApplier.apply_diff_text(content, diff)
        self.assertTrue(res.success)
        self.assertEqual(res.applied_count, 2)
        self.assertEqual(res.new_content, "step_1 = True\nstep_2 = True\n")

    def test_17_ast_skeletonizer_ellipsis_formatting(self):
        """Проверяет формат заглушек ASTSkeletonizer с многоточием '...'."""
        source = "def run():\n    a = 1\n    b = 2\n"
        skel = ASTSkeletonizer.skeletonize(source)
        self.assertTrue(skel.endswith("    ..."))

    # --------------------------------------------------------------------------
    # 8. SandboxManager: Изоляция путей и Безопасность
    # --------------------------------------------------------------------------
    def test_18_sandbox_manager_clean_workspace_filename(self):
        """Проверяет нормализацию имён файлов и срез path traversal (../)."""
        self.assertEqual(SandboxManager.clean_workspace_filename("../../evil.py"), "evil.py")
        self.assertEqual(SandboxManager.clean_workspace_filename("python main.py"), "main.py")
        self.assertEqual(SandboxManager.clean_workspace_filename("C:\\Windows\\system.py"), "Windows/system.py")

    def test_19_sandbox_manager_path_isolation_guards(self):
        """Проверяет запрет записи за пределы папки workspace."""
        safe_p = SandboxManager.get_workspace_dir() / "valid.py"
        self.assertEqual(SandboxManager.validate_write_path(safe_p), safe_p)

        with self.assertRaises(PermissionError):
            SandboxManager.validate_write_path("C:\\Windows\\System32\\cmd.exe")

    def test_20_sandbox_manager_ignored_directories(self):
        """Проверяет фильтрацию системных и служебных каталогов."""
        self.assertTrue(SandboxManager.is_ignored_directory(".git"))
        self.assertTrue(SandboxManager.is_ignored_directory("node_modules"))
        self.assertTrue(SandboxManager.is_ignored_directory("windows"))
        self.assertFalse(SandboxManager.is_ignored_directory("my_project"))

    # --------------------------------------------------------------------------
    # 9. Согласование контракта модулей и тестов
    # --------------------------------------------------------------------------
    def test_21_code_validator_test_files_bypass_entry_point(self):
        """Проверяет, что проверочные сьюты test_*.py освобождены от проверки точки входа."""
        test_file_code = (
            "import unittest\n"
            "class MyTests(unittest.TestCase):\n"
            "    def test_sample(self):\n"
            "        self.assertTrue(True)\n"
        )
        is_valid, err = CodeValidator.validate_python_syntax("test_module.py", test_file_code, is_entry_file=True)
        self.assertTrue(is_valid)
        self.assertIsNone(err)

    def test_22_execution_sandbox_traceback_pruner(self):
        """Проверяет очистку логов исключений от системного шума site-packages."""
        noisy_stderr = (
            'Traceback (most recent call last):\n'
            '  File "C:\\\\Python\\\\lib\\\\site-packages\\\\pkg.py", line 12, in <module>\n'
            '    internal_call()\n'
            '  File "main.py", line 45, in run\n'
            '    assert 1 == 0\n'
            'AssertionError\n'
        )
        pruned = ExecutionSandbox.prune_traceback(noisy_stderr)
        self.assertNotIn("site-packages", pruned)
        self.assertIn('File "main.py", line 45', pruned)
        self.assertIn("AssertionError", pruned)

    # --------------------------------------------------------------------------
    # 10. Fuzzy Matcher, In-Memory Guard и Monkey-Patch
    # --------------------------------------------------------------------------
    def test_23_patch_applier_fuzzy_normalized_quotes_and_spaces(self):
        """Проверяет нечёткий патчер: толерантность к кавычкам и пробелам вокруг операторов."""
        orig_code = (
            "def setup_game():\n"
            "    title = 'Neon Space Shooter'\n"
            "    speed = 10\n"
            "    return title, speed\n"
        )
        diff_text = (
            "<<<<<<< SEARCH\n"
            '    title="Neon Space Shooter"\n'
            "    speed=10\n"
            "=======\n"
            '    title = "Neon Space Shooter v2"\n'
            "    speed = 15\n"
            ">>>>>>> REPLACE"
        )
        res = PatchApplier.apply_diff_text(orig_code, diff_text)
        self.assertTrue(res.success, f"Патч должен примениться с нормализацией: {res.error}")
        self.assertIn("Neon Space Shooter v2", res.new_content)
        self.assertIn("speed = 15", res.new_content)
        self.assertTrue(res.is_fuzzy)

    def test_24_patch_applier_fuzzy_sequence_matcher_tolerance(self):
        """Проверяет Fuzzy Matcher: успешное нахождение блока при расхождении строк до 85%."""
        orig_code = (
            "class Weapon:\n"
            "    def __init__(self):\n"
            "        self.name = 'Laser'\n"
            "        self.damage = 25\n"
            "        self.cooldown = 0.5\n"
            "        self.bullets = []\n"
        )
        diff_text = (
            "<<<<<<< SEARCH\n"
            "    def __init__(self):\n"
            "        self.name = 'Laser'\n"
            "        self.damage = 25\n"
            "        # cooldown\n"
            "        self.cooldown = 0.5\n"
            "=======\n"
            "    def __init__(self):\n"
            "        self.name = 'Laser'\n"
            "        self.damage = 50\n"
            "        self.cooldown = 0.2\n"
            ">>>>>>> REPLACE"
        )
        res = PatchApplier.apply_diff_text(orig_code, diff_text)
        self.assertTrue(res.success, f"Fuzzy Matcher обязан сработать при схожести >85%: {res.error}")
        self.assertIn("self.damage = 50", res.new_content)
        self.assertIn("self.cooldown = 0.2", res.new_content)
        self.assertIn("self.bullets = []", res.new_content)
        self.assertTrue(res.is_fuzzy)

    def test_25_tool_engine_write_file_in_memory_ast_guard(self):
        """Проверяет in-memory AST-чекер: блокировку битого Python-кода до записи на диск."""
        broken_code = "def render_menu(window,:\n    pass\n"
        res = ToolEngine.write_file("broken_test_module.py", broken_code)

        self.assertFalse(res.success, "ToolEngine обязан отклонить файл с битым синтаксисом.")
        self.assertIn("ошибка синтаксиса до записи", res.output.lower())

        ws_dir = SandboxManager.get_workspace_dir()
        file_path = ws_dir / "broken_test_module.py"
        self.assertFalse(file_path.exists(), "Файл с ошибкой синтаксиса не должен попадать на диск.")

    def test_26_headless_monkey_patch_bootstrap_integrity(self):
        """Проверяет синтаксическую корректность встроенного раннера Headless Monkey-Patch."""
        import inspect
        source = inspect.getsource(SandboxManager.run_script)

        self.assertIn("monkey_patch_bootstrap", source)
        self.assertIn("pygame.event.get", source)
        self.assertIn("report_callback_exception", source)
        self.assertIn("winfo_children", source)

        valid_res, _ = ExecutionSandbox.preflight_lint("x = [i for i in range(10)]\n")
        invalid_res, lint_msg = ExecutionSandbox.preflight_lint("def invalid_func(\n")

        self.assertTrue(valid_res)
        self.assertFalse(invalid_res)
        self.assertIn("Линия 1", lint_msg)


if __name__ == "__main__":
    unittest.main()
