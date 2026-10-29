"""
services/code_engine.py
Independent modular code engine for 7B-8B models in resource-constrained environments (8GB VRAM / 16GB RAM).

Contains:
1. ASTSkeletonizer: AST-based context compression and symbol extraction.
2. PatchApplier: Robust diff parser and applicator with fuzzy matching fallback.
3. ExecutionSandbox: Fast pre-flight linting and stderr traceback pruning.
4. SelfHealingEngine: Transactional coordinator for patches with automatic rollback on error.
5. CodeValidator: Comprehensive static AST and scope validation, linter, and contract checks.
"""

import ast
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, List, Union, Dict

from services.sandbox_manager import SandboxManager, ScriptExecutionResult

logger = logging.getLogger("NeonRoute.CodeEngine")


# ==============================================================================
# 1. ASTSkeletonizer
# ==============================================================================

class ASTSkeletonizer(ast.NodeTransformer):
    """
    Преобразует исходный Python-код в 100% синтаксически валидный скелет интерфейсов.
    Работает строго на уровне AST-узлов (без строковых срезов), исключая незакрытые кавычки.
    """

    @classmethod
    def skeletonize(cls, source_code: str) -> str:
        """
        Преобразует Python-код в компактный скелет:
        - Сохраняет import, from ... import, константы верхнего регистра (UPPER_CASE = ...).
        - Сохраняет структуру классов, декораторы, docstrings, сигнатуры методов и аннотации типов.
        - Заменяет тело всех функций и методов на синтаксический Ellipsis (...).
        - При ошибке парсинга безопасно возвращает исходный код.
        """
        if not source_code or not source_code.strip():
            return source_code

        try:
            tree = ast.parse(source_code)
            transformer = cls()
            transformed_tree = transformer.visit(tree)
            ast.fix_missing_locations(transformed_tree)
            return ast.unparse(transformed_tree)
        except Exception:
            return source_code

    def visit_Module(self, node: ast.Module) -> ast.Module:
        new_body = []
        is_first = True
        for stmt in node.body:
            # 1. Сохраняем docstring модуля
            if is_first and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                new_body.append(stmt)
            # 2. Импорты
            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                new_body.append(stmt)
            # 3. Константы верхнего регистра (MAX_LIMIT = 10, PORT: int = 8080)
            elif isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id.isupper() for t in stmt.targets):
                new_body.append(stmt)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id.isupper():
                new_body.append(stmt)
            # 4. Классы и функции верхнего уровня
            elif isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                transformed_stmt = self.visit(stmt)
                if transformed_stmt:
                    new_body.append(transformed_stmt)
            is_first = False

        node.body = new_body
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        doc = ast.get_docstring(node)
        new_body = []
        if doc:
            new_body.append(ast.Expr(value=ast.Constant(value=doc)))

        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                new_body.append(self._skeletonize_function(stmt))
            elif isinstance(stmt, ast.ClassDef):
                new_body.append(self.visit_ClassDef(stmt))
            elif isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id.isupper() for t in stmt.targets):
                new_body.append(stmt)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id.isupper():
                new_body.append(stmt)

        if not new_body:
            new_body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))

        node.body = new_body
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        return self._skeletonize_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        return self._skeletonize_function(node)

    def _skeletonize_function(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> Union[ast.FunctionDef, ast.AsyncFunctionDef]:
        doc = ast.get_docstring(node)
        new_body = []
        if doc:
            new_body.append(ast.Expr(value=ast.Constant(value=doc)))
        # Тело функции/метода полностью заменяется синтаксическим объектом Ellipsis (...)
        new_body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))
        node.body = new_body
        return node

    @classmethod
    def extract_symbol(cls, source_code: str, symbol_name: str) -> Optional[str]:
        """
        Извлекает полный исходный код конкретного класса, метода или функции по имени.
        Поддерживает:
        - Имя класса: "ClassName"
        - Имя функции: "func_name"
        - Имя метода: "ClassName.method_name" или просто "method_name"
        """
        if not source_code or not symbol_name:
            return None

        try:
            tree = ast.parse(source_code)
        except Exception:
            return None

        lines = source_code.splitlines()

        def get_full_node_source(node: ast.AST) -> str:
            start_l = getattr(node, "lineno", 1)
            # Учитываем декораторы
            if hasattr(node, "decorator_list") and getattr(node, "decorator_list"):
                start_l = node.decorator_list[0].lineno

            end_l = getattr(node, "end_lineno", len(lines))
            return "\n".join(lines[start_l - 1 : end_l])

        # Поддержка вложенных имен "ClassName.method_name"
        parts = symbol_name.split(".")
        if len(parts) == 2:
            target_class, target_method = parts[0], parts[1]
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == target_class:
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == target_method:
                            return get_full_node_source(sub)
            return None

        # 1. Поиск на верхнем уровне
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol_name:
                return get_full_node_source(node)

        # 2. Поиск внутри классов (если вызвали просто по имени метода)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == symbol_name:
                        return get_full_node_source(sub)

        return None


# ==============================================================================
# 3. ExecutionSandbox
# ==============================================================================

class ExecutionSandbox:
    """
    Пре-флайт проверка синтаксиса и очистка логов исключений.
    Интегрируется с SandboxManager для повторного использования контура безопасности.
    """

    @classmethod
    def preflight_lint(cls, code: str) -> Tuple[bool, str]:
        """
        Проверяет код через ast.parse().
        При SyntaxError или IndentationError возвращает:
        (False, "Линия X: Описание ошибки") мгновенно, без порождения системных процессов.
        """
        if not code or not code.strip():
            return True, ""

        try:
            ast.parse(code)
            return True, "Синтаксис корректен"
        except (SyntaxError, IndentationError) as e:
            lineno = getattr(e, "lineno", "?")
            msg = getattr(e, "msg", str(e))
            return False, f"Линия {lineno}: {msg}"
        except Exception as e:
            return False, f"Ошибка парсинга: {e}"

    @classmethod
    def prune_traceback(cls, stderr: str, max_lines: int = 15) -> str:
        """
        Фильтрует технический шум:
        - Удаляет пути к системным библиотекам Python (site-packages, <frozen importlib>).
        - Оставляет вызовы пользовательского скрипта и финальный стек исключения.
        - Ограничивает вывод последними max_lines строками.
        """
        if not stderr or not stderr.strip():
            return ""

        raw_lines = stderr.splitlines()
        filtered_lines: List[str] = []

        skip_next = False
        for line in raw_lines:
            # Пропуск шума frozen importlib и site-packages
            if "<frozen importlib" in line or "site-packages" in line:
                skip_next = True
                continue
            if skip_next:
                # Если предыдущая строка была File "...site-packages...", текущая обычно строка кода этой библиотеки
                if line.startswith("    ") and not line.strip().startswith("Traceback"):
                    skip_next = False
                    continue
                skip_next = False

            filtered_lines.append(line)

        # Берем последние max_lines строк
        if len(filtered_lines) > max_lines:
            truncated = filtered_lines[-max_lines:]
            # Если первая строка не заголовок Traceback, добавим лаконичную шапку
            if not any("Traceback" in l for l in truncated):
                truncated.insert(0, "[...стек вызовов сокращен...]")
            return "\n".join(truncated)

        return "\n".join(filtered_lines)

    @classmethod
    def run_isolated(cls, script_name: str, timeout: int = 30) -> ScriptExecutionResult:
        """
        Опирается на SandboxManager.run_script(script_name, timeout=timeout)
        для повторного использования контура безопасности sandbox_guard.
        """
        return SandboxManager.run_script(script_name, timeout=timeout)


STANDARD_AUTO_IMPORTS: Dict[str, str] = {
    "List": "from typing import List",
    "Dict": "from typing import Dict",
    "Tuple": "from typing import Tuple",
    "Optional": "from typing import Optional",
    "Union": "from typing import Union",
    "Any": "from typing import Any",
    "Callable": "from typing import Callable",
    "Set": "from typing import Set",
    "Path": "from pathlib import Path",
    "math": "import math",
    "random": "import random",
    "json": "import json",
    "time": "import time",
    "re": "import re",
    "sys": "import sys",
    "shutil": "import shutil",
    "datetime": "from datetime import datetime",
    "timedelta": "from datetime import timedelta",
    "defaultdict": "from collections import defaultdict",
    "Counter": "from collections import Counter",
    "deque": "from collections import deque",
    "dataclass": "from dataclasses import dataclass",
    "field": "from dataclasses import field",
    "StringIO": "from io import StringIO",
    "BytesIO": "from io import BytesIO",
}

class AutoImporter:
    """Инструмент тихой инъекции забытых стандартных импортов (Zero-Token Healing)."""

    @classmethod
    def resolve_import(cls, symbol: str) -> Optional[str]:
        return STANDARD_AUTO_IMPORTS.get(symbol)

    @classmethod
    def try_inject_import(cls, file_path: Path, missing_symbol: str) -> bool:
        stmt = cls.resolve_import(missing_symbol)
        if not stmt or not file_path.is_file():
            return False

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            # Проверяем, нет ли уже такой строки
            if stmt in content:
                return False

            lines = content.splitlines(keepends=True)
            insert_idx = 0

            # Пропускаем shebang (#!) и кодировку, если есть
            for i, line in enumerate(lines[:3]):
                if line.startswith("#!") or "coding:" in line:
                    insert_idx = i + 1

            lines.insert(insert_idx, f"{stmt}\n")
            file_path.write_text("".join(lines), encoding="utf-8")
            logger.info(f"✨ [Zero-Token Healing] Добавлен импорт: '{stmt}' в {file_path.name}")
            return True
        except Exception as e:
            logger.warning(f"[Zero-Token Healing] Не удалось внедрить импорт в {file_path.name}: {e}")
            return False


from services.code_validator import CodeValidator
from services.patch_engine import PatchResult, PatchApplier, SelfHealingEngine

__all__ = [
    "ASTSkeletonizer",
    "PatchResult",
    "PatchApplier",
    "ExecutionSandbox",
    "SelfHealingEngine",
    "CodeValidator",
    "AutoImporter",
]
