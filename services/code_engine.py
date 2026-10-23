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

class ASTSkeletonizer:
    """
    Преобразует исходный Python-код в компактный скелет без потери контекста интерфейсов.
    """

    @classmethod
    def skeletonize(cls, source_code: str) -> str:
        """
        Преобразует Python-код в компактный скелет.
        - Сохраняет: import, from ... import, константы UPPER_CASE = ...,
          классы с базовыми классами и docstrings.
        - Сохраняет сигнатуры методов и функций, декораторы, аннотации типов и docstring.
        - Заменяет тело методов/функций на `...` (Ellipsis).
        - При SyntaxError возвращает исходный код без падения программы.
        """
        if not source_code or not source_code.strip():
            return source_code

        try:
            tree = ast.parse(source_code)
        except (SyntaxError, IndentationError, Exception):
            return source_code

        lines = source_code.splitlines()

        def get_docstring_node(body_nodes: list) -> Optional[ast.Expr]:
            if (
                body_nodes
                and isinstance(body_nodes[0], ast.Expr)
                and isinstance(body_nodes[0].value, ast.Constant)
                and isinstance(body_nodes[0].value.value, str)
            ):
                return body_nodes[0]
            return None

        def format_docstring(doc_expr: ast.Expr, indent_str: str) -> str:
            val = doc_expr.value.value  # type: ignore
            # Формируем тройные кавычки
            clean_val = val.replace('"""', r'\"\"\"')
            if "\n" in clean_val:
                doc_lines = clean_val.split("\n")
                formatted = [f'{indent_str}"""{doc_lines[0]}']
                for dl in doc_lines[1:]:
                    formatted.append(f"{indent_str}{dl}")
                formatted[-1] += '"""'
                return "\n".join(formatted)
            else:
                return f'{indent_str}"""{clean_val}"""'

        def is_upper_case_target(target: ast.AST) -> bool:
            if isinstance(target, ast.Name):
                return target.id.isupper()
            return False

        def get_source_slice(start_lineno: int, end_lineno: int) -> str:
            # 1-indexed to 0-indexed slice
            s = max(0, start_lineno - 1)
            e = min(len(lines), end_lineno)
            return "\n".join(lines[s:e])

        def get_func_header(fn_node: Union[ast.FunctionDef, ast.AsyncFunctionDef], indent_str: str) -> str:
            # Сигнатура функции/метода включая декораторы
            start_l = fn_node.lineno
            if fn_node.decorator_list:
                start_l = fn_node.decorator_list[0].lineno

            # Ищем где заканчивается заголовок (строка с двоеточием перед телом)
            first_body_node = fn_node.body[0] if fn_node.body else None
            if first_body_node:
                # Заголовок заканчивается до первой строчки тела
                body_start_l = first_body_node.lineno
                # Берём строки от start_l до body_start_l
                header_lines = lines[start_l - 1 : body_start_l]
                combined = "\n".join(header_lines)
                # Ищем двоеточие, отделяющее заголовок
                colon_idx = combined.rfind(":")
                if colon_idx != -1:
                    header_text = combined[: colon_idx + 1]
                else:
                    header_text = combined
            else:
                header_text = lines[start_l - 1]

            return header_text.rstrip()

        def process_function(
            fn_node: Union[ast.FunctionDef, ast.AsyncFunctionDef],
            indent_level: int = 0
        ) -> List[str]:
            res: List[str] = []
            indent_str = "    " * indent_level
            body_indent = "    " * (indent_level + 1)

            header = get_func_header(fn_node, indent_str)
            res.append(header)

            doc = get_docstring_node(fn_node.body)
            if doc:
                res.append(format_docstring(doc, body_indent))

            res.append(f"{body_indent}...")
            return res

        def process_class(cls_node: ast.ClassDef, indent_level: int = 0) -> List[str]:
            res: List[str] = []
            indent_str = "    " * indent_level
            body_indent = "    " * (indent_level + 1)

            start_l = cls_node.lineno
            if cls_node.decorator_list:
                start_l = cls_node.decorator_list[0].lineno

            first_body = cls_node.body[0] if cls_node.body else None
            if first_body:
                header_lines = lines[start_l - 1 : first_body.lineno]
                combined = "\n".join(header_lines)
                colon_idx = combined.rfind(":")
                header_text = combined[: colon_idx + 1] if colon_idx != -1 else combined
            else:
                header_text = lines[start_l - 1]

            res.append(header_text.rstrip())

            doc = get_docstring_node(cls_node.body)
            if doc:
                res.append(format_docstring(doc, body_indent))

            has_children = False
            for item in cls_node.body:
                if item == doc:
                    continue
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    res.extend(process_function(item, indent_level + 1))
                    has_children = True
                elif isinstance(item, ast.ClassDef):
                    res.extend(process_class(item, indent_level + 1))
                    has_children = True
                elif isinstance(item, ast.Assign):
                    if any(is_upper_case_target(t) for t in item.targets):
                        code = get_source_slice(item.lineno, getattr(item, "end_lineno", item.lineno))
                        res.append(code)
                        has_children = True
                elif isinstance(item, ast.AnnAssign):
                    if is_upper_case_target(item.target):
                        code = get_source_slice(item.lineno, getattr(item, "end_lineno", item.lineno))
                        res.append(code)
                        has_children = True

            if not has_children and not doc:
                res.append(f"{body_indent}...")

            return res

        output_blocks: List[str] = []

        # Обход верхнего уровня
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                code = get_source_slice(node.lineno, getattr(node, "end_lineno", node.lineno))
                output_blocks.append(code)

            elif isinstance(node, ast.Assign):
                # Сохраняем константы ВЕРХНЕГО РЕГИСТРА
                if any(is_upper_case_target(t) for t in node.targets):
                    code = get_source_slice(node.lineno, getattr(node, "end_lineno", node.lineno))
                    output_blocks.append(code)

            elif isinstance(node, ast.AnnAssign):
                if is_upper_case_target(node.target):
                    code = get_source_slice(node.lineno, getattr(node, "end_lineno", node.lineno))
                    output_blocks.append(code)

            elif isinstance(node, ast.ClassDef):
                cls_lines = process_class(node, 0)
                output_blocks.append("\n".join(cls_lines))

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_lines = process_function(node, 0)
                output_blocks.append("\n".join(fn_lines))

        return "\n\n".join(output_blocks)

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


from services.code_validator import CodeValidator
from services.patch_engine import PatchResult, PatchApplier, SelfHealingEngine

__all__ = [
    "ASTSkeletonizer",
    "PatchResult",
    "PatchApplier",
    "ExecutionSandbox",
    "SelfHealingEngine",
    "CodeValidator",
]
