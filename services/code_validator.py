"""
services/code_validator.py
Комплексная статическая AST-валидация Python-кода, Scope Guard,
Anti-Truncation Guard, Cross-Module контракты и In-Memory AST Linter.
"""

import ast
import builtins
import re
import symtable
import logging
from pathlib import Path
from typing import Optional, Tuple, List, Union, Dict
from services.sandbox_manager import SandboxManager

logger = logging.getLogger("NeonRoute.CodeValidator")


class CodeValidator:
    """
    Комплексная статическая AST-валидация Python-кода, Scope Guard,
    Anti-Truncation Guard, Cross-Module контракты и In-Memory AST Linter.
    """

    @classmethod
    def generate_ast_skeleton(cls, content: str) -> str:
        """
        Извлекает компактный архитектурный скелет через ASTSkeletonizer.
        """
        from services.code_engine import ASTSkeletonizer
        skel = ASTSkeletonizer.skeletonize(content)
        return skel if skel and skel.strip() else "... [нет публичных определений]"

    @classmethod
    def check_undefined_names(cls, content: str, filename: str) -> Optional[str]:
        """
        Semantic Scope Guard: находит необъявленные переменные (NameError) через symtable
        и AST-анализ до записи на диск и до падения рантайма.
        """
        if not filename.lower().endswith((".py", ".pyw")):
            return None

        try:
            tree = ast.parse(content, filename=filename)
            st = symtable.symtable(content, filename, "exec")
        except Exception:
            return None

        builtin_names = set(dir(builtins)) | {
            "__file__", "__name__", "__doc__", "__package__", "__spec__", "__annotations__"
        }

        # Символы, объявленные на уровне модуля
        module_globals = {
            s.get_name()
            for s in st.get_symbols()
            if s.is_assigned() or s.is_imported() or s.is_namespace()
        }

        # Учитываем переменные, объявленные через 'global x' внутри функций
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                for g_name in node.names:
                    module_globals.add(g_name)

        # Безопасный учет wildcard импортов (from module import *)
        wildcard_names = set()
        has_unknown_wildcard = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        mod_name = node.module or ""
                        try:
                            mod = __import__(mod_name, fromlist=["*"])
                            wildcard_names.update(dir(mod))
                        except Exception:
                            has_unknown_wildcard = True

        lines = content.splitlines()

        def find_symbol_line(func_name: str, var_name: str) -> Tuple[int, str]:
            """Находит точный номер строки и фрагмент кода первого обращения к необъявленной переменной."""
            try:
                for n in ast.walk(tree):
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func_name:
                        for sub in ast.walk(n):
                            if isinstance(sub, ast.Name) and sub.id == var_name and isinstance(sub.ctx, ast.Load):
                                lineno = getattr(sub, "lineno", 0)
                                line_txt = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
                                return lineno, line_txt
            except Exception:
                pass
            return 0, ""

        def scan_scope(scope, parent_func_names=None) -> Optional[str]:
            p_names = parent_func_names or set()
            if scope.get_type() == "function":
                func_name = scope.get_name()
                for sym in scope.get_symbols():
                    name = sym.get_name()
                    if sym.is_referenced() and not (
                        sym.is_local()
                        or sym.is_parameter()
                        or sym.is_free()
                        or sym.is_assigned()
                    ):
                        if (
                            name not in module_globals
                            and name not in builtin_names
                            and name not in wildcard_names
                            and name not in p_names
                            and not has_unknown_wildcard
                        ):
                            lineno, line_txt = find_symbol_line(func_name, name)
                            loc_str = f" на строке {lineno}: `{line_txt}`" if lineno > 0 else ""
                            return (
                                f"Семантическая ошибка (NameError): В функции/методе '{func_name}'{loc_str} "
                                f"используется необъявленная переменная '{name}'. "
                                f"Переменная не инициализирована локально, не передана в аргументах и отсутствует в модуле. "
                                f"Рецепт: объяви '{name} = ...' в начале функции '{func_name}' либо передай её как аргумент."
                            )
                p_names = p_names | set(scope.get_locals())

            for child in scope.get_children():
                err = scan_scope(child, p_names)
                if err:
                    return err
            return None

        return scan_scope(st)

    @classmethod
    def check_unreferenced_methods(cls, content: str, filename: str) -> Optional[str]:
        """Находит методы классов, которые объявлены, но ни разу не вызываются в файле."""
        if not filename.lower().endswith((".py", ".pyw")):
            return None
        try:
            tree = ast.parse(content, filename=filename)
        except Exception:
            return None

        framework_hooks = {
            "__init__", "__str__", "__repr__", "__call__", "__enter__", "__exit__",
            "run", "main", "start", "stop", "setup",
            "paintEvent", "closeEvent", "keyPressEvent", "keyReleaseEvent",
            "mousePressEvent", "mouseReleaseEvent", "resizeEvent"
        }

        defined_methods = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if (
                            item.name.startswith("_")
                            and not item.name.startswith("__")
                            and item.name not in framework_hooks
                            and not item.decorator_list
                            and not item.name.startswith("test_")
                        ):
                            defined_methods.append((node.name, item.name))

        if not defined_methods:
            return None

        all_refs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                all_refs.add(node.attr)
            elif isinstance(node, ast.Name):
                all_refs.add(node.id)

        for class_name, method_name in defined_methods:
            if method_name not in all_refs:
                return (
                    f"Архитектурная ошибка (UnwiredMethod): Метод '{method_name}' объявлен в классе '{class_name}', "
                    f"но ни разу не вызывается и не передаётся в качестве обработчика событий внутри скрипта. "
                    f"Обязательно добавь вызов self.{method_name}(...) в цикл обновления (update/run) или привяжи к событию."
                )
        return None

    @classmethod
    def validate_python_syntax(
        cls,
        filename: str,
        content: str,
        is_entry_file: bool = True
    ) -> Tuple[bool, Optional[str]]:
        """
        Многоуровневая AST-валидация Python-скрипта до сохранения на диск:
        1. Проверка синтаксиса через ast.parse.
        2. AST Safety Guard (VULN-02): блокировка опасных модулей.
        3. Проверка наличия точки входа (Entry Point) при наличии классов/функций (только если is_entry_file=True).
        4. Проверка запуска главного цикла событий (Event Loop) для оконных приложений (только если is_entry_file=True).
        5. Проверка областей видимости и необъявленных переменных (Scope Guard).
        6. AST Stub Guard: запрет пустых функций и методов без реализации.
        7. AST Reachability Guard: проверка вызова объявленных методов (при is_entry_file=True).
        8. OpenPyXL Anti-Pattern Guard (.formula, mutable styles).
        """
        if not filename.lower().endswith((".py", ".pyw")):
            return True, None

        # 1. Синтаксический разбор
        try:
            tree = ast.parse(content, filename=filename)
        except SyntaxError as e:
            err_line = e.lineno or "?"
            err_text = (e.text or "").strip()
            msg = f"SyntaxError на строке {err_line}: {e.msg}"
            if err_text:
                msg += f"\nСтрока с ошибкой: {err_text}"
            return False, msg

        # 1.5. AST Safety Guard (VULN-02): блокировка опасных модулей
        BANNED_MODULES = {"socket", "urllib", "requests", "ctypes", "winreg", "subprocess", "shutil"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod_root = alias.name.split(".")[0].lower()
                    if mod_root in BANNED_MODULES:
                        return False, (
                            f"Ошибка безопасности (AST Safety Guard): Импорт модуля '{alias.name}' "
                            f"в файле {filename} заблокирован. Использование сетевых и низкоуровневых системных "
                            f"библиотек запрещено политикой безопасности песочницы."
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mod_root = node.module.split(".")[0].lower()
                    if mod_root in BANNED_MODULES:
                        return False, (
                            f"Ошибка безопасности (AST Safety Guard): Импорт из модуля '{node.module}' "
                            f"в файле {filename} заблокирован. Использование сетевых и низкоуровневых системных "
                            f"библиотек запрещено политикой безопасности песочницы."
                        )

        # Тестовым файлам не требуется точка входа или оконный цикл (обратная совместимость)
        clean_fn = Path(filename).name.lower()
        is_test_or_verification = (
            clean_fn.startswith(("test_", "check_", "verify_", "validate_"))
            or clean_fn.endswith(("_test.py", "_tests.py", "_check.py", "_verify.py"))
            or filename.lower().startswith(("test_", "check_", "verify_", "validate_"))
            or filename.lower().endswith(("_test.py", "_tests.py", "_check.py", "_verify.py"))
        )
        if is_test_or_verification:
            is_entry_file = False
        elif is_entry_file:
            has_test_elements = False
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    has_test_elements = True
                    break
                elif isinstance(node, ast.Import):
                    if any(alias.name.split(".")[0].lower() in ("pytest", "unittest") for alias in node.names):
                        has_test_elements = True
                        break
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.split(".")[0].lower() in ("pytest", "unittest"):
                        has_test_elements = True
                        break
            if has_test_elements:
                is_entry_file = False

        # 2. Проверка точки входа (Entry Point) - только для главных исполняемых скриптов
        if is_entry_file:
            has_definitions = any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                for node in tree.body
            )
            if has_definitions:
                has_entry_point = False
                for node in tree.body:
                    if isinstance(node, ast.If):
                        has_entry_point = True
                        break
                    elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                        has_entry_point = True
                        break
                    elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                        has_entry_point = True
                        break
                    elif isinstance(node, (ast.For, ast.While, ast.Try)):
                        has_entry_point = True
                        break

                if not has_entry_point:
                    return False, (
                        "Структурная ошибка (MissingEntryPoint): В скрипте объявлены классы или функции, "
                        "но отсутствует исполняемый код верхнего уровня. "
                        "Обязательно добавь блок `if __name__ == '__main__':` с созданием объекта и вызовом метода запуска."
                    )

        # 3. Проверка оконных приложений (Event Loop) - только для главных исполняемых скриптов
        if is_entry_file:
            gui_frameworks = ("tkinter", "PyQt", "PySide", "pygame")
            is_gui = any(framework in content for framework in gui_frameworks)
            if is_gui:
                has_event_loop = False
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        func_name = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
                        if func_name in ("mainloop", "exec", "exec_"):
                            has_event_loop = True
                            break
                    elif isinstance(node, (ast.While, ast.For)) and "pygame" in content:
                        has_event_loop = True
                        break

                if not has_event_loop:
                    return False, (
                        "Ошибка оконного приложения (MissingEventLoop): Скрипт использует GUI, "
                        "но не содержит запуска цикла событий (например, root.mainloop() или app.exec()). "
                        "Без этого окно закроется мгновенно. Добавь вызов цикла в точке входа."
                    )

        # 3.5. Pygame Quality & Anti-Freeze Gate (только для entry file с pygame)
        if is_entry_file and "pygame" in content:
            def _get_call_path(fn_node) -> str:
                if isinstance(fn_node, ast.Name):
                    return fn_node.id
                if isinstance(fn_node, ast.Attribute):
                    prefix = _get_call_path(fn_node.value)
                    return f"{prefix}.{fn_node.attr}" if prefix else fn_node.attr
                return ""

            # Проверка запрета блокирующего фриза (pygame.time.delay / time.sleep)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.While, ast.For)):
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Call):
                            c_name = _get_call_path(sub.func)
                            if c_name in ("pygame.time.delay", "time.sleep") or (
                                isinstance(sub.func, ast.Attribute) and sub.func.attr == "delay" and "time" in _get_call_path(sub.func.value)
                            ) or (
                                isinstance(sub.func, ast.Attribute) and sub.func.attr == "sleep" and _get_call_path(sub.func.value) in ("time", "asyncio")
                            ):
                                return False, (
                                    f"Ошибка блокирующего фриза (BlockingFreeze): Обнаружен вызов '{c_name or 'pygame.time.delay'}' "
                                    f"в файле {filename}. Запрещено использовать блокирующие задержки в функциях, методах и игровом цикле. "
                                    f"Прыжки, тайминги и анимация должны управляться через скорости, гравитацию и счётчики кадров (clock.tick)."
                                )

            # Проверка минимальной полноты рендера Pygame (минимум 2 вызова отрисовки и display.flip / display.update)
            draw_calls_count = 0
            has_display_update = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    c_name = _get_call_path(node.func)
                    if c_name.startswith("pygame.draw.") or (
                        isinstance(node.func, ast.Attribute) and node.func.attr == "blit"
                    ) or c_name.endswith(".blit"):
                        draw_calls_count += 1
                    if c_name in ("pygame.display.flip", "pygame.display.update") or (
                        isinstance(node.func, ast.Attribute) and node.func.attr in ("flip", "update") and "display" in _get_call_path(node.func.value)
                    ):
                        has_display_update = True

            if not has_display_update:
                return False, (
                    f"Ошибка неполной реализации Pygame (MissingDisplayFlip): В файле {filename} отсутствует вызов "
                    f"pygame.display.flip() или pygame.display.update(). Игровой цикл обязан выводить кадры на экран."
                )
            if draw_calls_count < 2:
                return False, (
                    f"Ошибка неполной реализации Pygame (InsufficientRenderCalls): В файле {filename} обнаружено "
                    f"недостаточно вызовов отрисовки ({draw_calls_count} < 2). Игра должна отрисовывать как минимум игровой мир/платформы "
                    f"и самого игрока/персонажа через pygame.draw.* или .blit()."
                )

        # 4. Проверка областей видимости и необъявленных переменных (Scope Guard)
        sem_err = cls.check_undefined_names(content, filename)
        if sem_err:
            return False, sem_err

        # 5. AST Stub Guard: запрет пустых функций и методов без реализации
        func_parent_map = {}
        for cls_node in ast.walk(tree):
            if isinstance(cls_node, ast.ClassDef):
                for item in cls_node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        func_parent_map[id(item)] = cls_node.name

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent_cls = func_parent_map.get(id(node))
                qual_name = f"{parent_cls}.{node.name}" if parent_cls else node.name
                is_abstract = any(
                    (getattr(d, "id", "") == "abstractmethod" or getattr(d, "attr", "") == "abstractmethod")
                    for d in getattr(node, "decorator_list", [])
                )
                if not is_abstract:
                    stmts = [
                        s for s in node.body
                        if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant) and isinstance(s.value.value, str))
                    ]
                    if len(stmts) == 1:
                        allowed_pass_prefixes = ("__init__", "noop")
                        is_allowed_pass = any(node.name.startswith(pfx) or node.name == pfx.rstrip("_") for pfx in allowed_pass_prefixes)
                        if isinstance(stmts[0], ast.Pass) and not is_allowed_pass:
                            return False, (
                                f"Метод/функция '{qual_name}' в файле {filename} содержит пустую заглушку 'pass'. "
                                f"Реализуй полную рабочую логику либо удали пустой метод '{qual_name}' и его лишние вызовы, если он не нужен."
                            )
                        if isinstance(stmts[0], ast.Expr) and getattr(stmts[0].value, "value", None) is Ellipsis and not is_allowed_pass:
                            return False, (
                                f"Метод/функция '{qual_name}' в файле {filename} заменена на заглушку '...'. "
                                f"Запрещено сокращать логику. Выведи полную реализацию."
                            )

        # 6. AST Reachability Guard: проверка вызова объявленных методов (только для entry file)
        if is_entry_file:
            reach_err = cls.check_unreferenced_methods(content, filename)
            if reach_err:
                return False, reach_err

        # 7. OpenPyXL Anti-Pattern Guard
        if "openpyxl" in content or "Workbook" in content:
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        # Запрет cell.formula = ...
                        if isinstance(tgt, ast.Attribute) and tgt.attr == "formula":
                            return False, (
                                f"Антипаттерн openpyxl в {filename}: У объекта Cell нет свойства .formula. "
                                f"Формулы присваиваются строго через свойство .value: "
                                f"ws['A1'].value = '=SUM(...)'. Замени ВСЕ .formula на .value."
                            )
                        # Запрет мутации неизменяемых стилей cell.font.bold = True
                        if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Attribute):
                            if tgt.value.attr in ("font", "alignment", "border", "fill"):
                                return False, (
                                    f"Антипаттерн openpyxl в {filename}: Стили ячеек неизменяемы (cell.{tgt.value.attr}.{tgt.attr} = ... вызовет сбой). "
                                    f"Присваивай стиль целиком: cell.{tgt.value.attr} = {tgt.value.attr.capitalize()}(...)."
                                )

        return True, None

    @classmethod
    def validate_code_integrity(
        cls,
        filename: str,
        orig_content: Optional[str],
        new_content: str,
        task_prompt: str = "",
        is_maintenance: bool = True,
        is_ephemeral: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        Execution-First Integrity Guard:
        Отслеживает реальные обрывы генерации и лень модели (Lazy Tokens), 
        не блокируя архитектурный рефакторинг, перенос функций в классы и удаление точек входа.
        """
        # Для одноразовых документов или новых файлов проверка не требуется
        if not is_maintenance or is_ephemeral:
            return True, None

        if not orig_content or not orig_content.strip():
            return True, None

        if not filename.lower().endswith((".py", ".pyw")):
            return True, None

        # 1. Проверка синтаксической валидности нового кода (защита от обрыва генерации на полуслове)
        try:
            tree = ast.parse(new_content, filename=filename)
        except SyntaxError as e:
            err_line = getattr(e, "lineno", "?")
            return False, f"Синтаксическая ошибка в {filename} (строка {err_line}): {e.msg}. Исправь синтаксис."

        # 2. Детектор маркеров лени (Lazy Token Guard)
        lazy_patterns = [
            r'#\s*(?:\.\.\.|rest of (?:code|file)|existing code|остальной код|остальной файл|код без изменений)',
            r'#\s*(?:TODO|FIXME):\s*(?:implement|дописать|доделать|остальное)',
            r'//\s*(?:\.\.\.|rest of code)',
        ]
        for pat in lazy_patterns:
            if re.search(pat, new_content, re.IGNORECASE):
                return False, (
                    f"Обнаружен маркер сокращения кода в {filename} (найдено: '{pat}'). "
                    f"Запрещено сокращать код комментариями вроде '# rest of code'. "
                    f"Выведи полную рабочую версию файла без пропусков."
                )

        # 3. Детектор пустых функций-заглушек верхнего уровня (тело состоит строго из ...)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if len(node.body) == 1 and isinstance(node.body[0], ast.Expr):
                    if getattr(node.body[0].value, "value", None) is Ellipsis:
                        return False, (
                            f"Функция '{node.name}' в файле {filename} заменена на заглушку '...'. "
                            f"Запрещено оставлять пустые заглушки. Реализуй метод полностью либо удали его."
                        )

        # 4. Детектор аномального физического сжатия (файл усох более чем на 75% при исходном объёме > 300 символов)
        orig_len = len(orig_content.strip())
        new_len = len(new_content.strip())
        deletion_keywords = ("удали", "вырежи", "избавься", "переименуй", "объедини", "delete", "remove", "clean")
        is_explicit_delete = task_prompt and any(k in task_prompt.lower() for k in deletion_keywords)

        if not is_explicit_delete and orig_len > 300 and new_len < (orig_len * 0.25):
            return False, (
                f"Аномальное сокращение объёма {filename}: размер уменьшился с {orig_len} до {new_len} символов (более чем на 75%). "
                f"Похоже на обрыв генерации. Если удаление кода не планировалось, верни полную реализацию."
            )

        # Все эвристические сравнения dropped_classes и dropped_funcs УДАЛЕНЫ. 
        # Дальнейшую проверку работоспособности проводит песочница через реальный запуск (Execution-First).
        return True, None

    @classmethod
    def check_cross_module_contracts(cls, write_blocks: List[Tuple[str, str]]) -> Optional[str]:
        """Проверяет вызовы методов между модулями только при достоверном определении типа объекта."""
        ws_dir = SandboxManager.get_workspace_dir()
        known_classes: Dict[str, Tuple[str, str, set, bool]] = {}

        def _collect_classes(code: str, fname: str):
            try:
                t = ast.parse(code, filename=fname)
                for node in t.body:
                    if isinstance(node, ast.ClassDef):
                        methods = {item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
                        bases = [getattr(b, "id", getattr(b, "attr", "")) for b in getattr(node, "bases", [])]
                        known_classes[node.name] = (node.name, fname, methods, bool(bases))
            except Exception:
                pass

        if ws_dir.exists():
            for f in ws_dir.glob("*.py"):
                try:
                    _collect_classes(f.read_text(encoding="utf-8", errors="ignore"), f.name)
                except Exception:
                    pass

        for fn, cnt in write_blocks:
            if fn.lower().endswith((".py", ".pyw")):
                _collect_classes(cnt, fn)

        if not known_classes:
            return None

        for fn, cnt in write_blocks:
            if not fn.lower().endswith((".py", ".pyw")):
                continue
            try:
                tree = ast.parse(cnt, filename=fn)

                # 1. Реестр импортированных модулей и функций (исключаем из проверок классов)
                file_imports = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for a in node.names:
                            file_imports.add(a.asname or a.name.split('.')[0])
                    elif isinstance(node, ast.ImportFrom):
                        for a in node.names:
                            file_imports.add(a.asname or a.name)

                # 2. Карта экземпляров: имя переменной -> имя известного класса
                inst_map: Dict[str, str] = {}
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign):
                        if isinstance(node.value, ast.Call):
                            func_name = getattr(node.value.func, "id", None)
                            if func_name and func_name in known_classes:
                                for tgt in node.targets:
                                    if isinstance(tgt, ast.Name):
                                        inst_map[tgt.id] = func_name
                                    elif isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) and tgt.value.id == "self":
                                        inst_map[tgt.attr] = func_name

                # 3. Валидация вызовов методов строго по карте экземпляров
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        method_name = node.func.attr
                        val = node.func.value
                        obj_name = None
                        if isinstance(val, ast.Name):
                            obj_name = val.id
                        elif isinstance(val, ast.Attribute) and isinstance(val.value, ast.Name) and val.value.id == "self":
                            obj_name = val.attr

                        if not obj_name or obj_name in file_imports:
                            continue

                        target_cls_name = inst_map.get(obj_name)
                        if target_cls_name and target_cls_name in known_classes:
                            class_name, def_file, methods, has_bases = known_classes[target_cls_name]
                            if not has_bases and not method_name.startswith("__") and method_name not in methods:
                                return (
                                    f"Контрактная ошибка (MissingMethod): В файле '{fn}' вызывается метод '{method_name}' "
                                    f"у объекта '{obj_name}' (экземпляр класса '{class_name}' из '{def_file}'), "
                                    f"но метод '{method_name}' отсутствует в классе '{class_name}'. "
                                    f"Либо добавь рабочую реализацию `def {method_name}(self):` в класс '{class_name}' ({def_file}), "
                                    f"либо удали лишний вызов `{obj_name}.{method_name}()` в '{fn}', если метод не нужен."
                                )
            except Exception:
                pass
        return None

    @classmethod
    def apply_ast_linter_to_code(cls, content: str) -> str:
        """In-Memory AST Linter: внедряет недостающие стандартные и целевые импорты до Scope Guard."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return content

        module_imported = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    module_imported.add(a.asname or a.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_imported.add(node.module.split('.')[0])
                for a in node.names:
                    module_imported.add(a.asname or a.name)

        used_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        missing = []

        # Стандартные библиотеки Python: проверяем присутствие именно на уровне модуля
        std_libs = ("random", "math", "time", "json", "os", "sys", "pathlib", "re", "datetime")
        for lib in std_libs:
            if lib in used_names and lib not in module_imported:
                missing.append(f"import {lib}")

        # Целевые прикладные модули
        if ("Workbook" in used_names or "load_workbook" in used_names) and "openpyxl" not in module_imported:
            missing.append("import openpyxl")
        has_excel = any(k in module_imported or k in used_names for k in ("Workbook", "openpyxl", "load_workbook"))
        if has_excel:
            openpyxl_styles = {"Font", "PatternFill", "Alignment", "Border", "Side"}
            if any(s in used_names for s in openpyxl_styles) and "Font" not in module_imported:
                missing.append("from openpyxl.styles import Font, PatternFill, Alignment, Border, Side")
        chart_classes = {"BarChart", "LineChart", "PieChart", "Reference"}
        if any(c in used_names for c in chart_classes) and not any(c in module_imported for c in ("BarChart", "LineChart", "Reference")):
            missing.append("from openpyxl.chart import BarChart, LineChart, PieChart, Reference")
        if "docx" in used_names and "docx" not in module_imported:
            missing.append("import docx")
        if "Document" in used_names and "docx" not in module_imported and "Document" not in module_imported:
            missing.append("from docx import Document")
        if "Path" in used_names and "pathlib" not in module_imported:
            missing.append("from pathlib import Path")

        if missing:
            unique = list(dict.fromkeys(missing))
            import_block = "\n".join(unique)

            # Проверяем наличие директивы __future__
            lines = content.splitlines()
            future_line_idx = -1
            for idx, line in enumerate(lines):
                s_line = line.strip()
                if s_line.startswith("from __future__"):
                    future_line_idx = idx
                elif s_line and not s_line.startswith("#") and not (s_line.startswith('"""') or s_line.startswith("'''")):
                    if future_line_idx != -1:
                        break

            if future_line_idx >= 0:
                head = "\n".join(lines[:future_line_idx + 1])
                tail = "\n".join(lines[future_line_idx + 1:])
                return f"{head}\n{import_block}\n{tail}"

            return f"{import_block}\n\n{content}"
        return content

    @classmethod
    def apply_ast_linter_to_file(cls, file_path: Union[str, Path]) -> None:
        """Применяет AST Linter напрямую к файлу на диске."""
        p = Path(file_path)
        if p.is_file():
            content = p.read_text(encoding="utf-8")
            linted = cls.apply_ast_linter_to_code(content)
            if linted != content:
                p.write_text(linted, encoding="utf-8")


__all__ = ["CodeValidator"]
