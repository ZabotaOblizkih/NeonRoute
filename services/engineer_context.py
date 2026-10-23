"""
services/engineer_context.py
Context inspection, code extraction, and sandbox management for NeonRoute Engineer.
"""

import ast
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

from services.git_sandbox import GitSandboxManager
from services.sandbox_manager import SandboxManager
from services.code_engine import ASTSkeletonizer

logger = logging.getLogger("NeonRoute.EngineerContext")

BINARY_EXTENSIONS: Set[str] = {
    ".xlsx", ".docx", ".doc", ".xls", ".pdf", ".zip",
    ".png", ".jpg", ".jpeg", ".gif", ".exe", ".bin", ".db", ".sqlite"
}
MAX_FULL_CODE_LINES: int = 600


class _RunResultWrapper:
    def __init__(self, res):
        self._res = res
        if isinstance(res, dict):
            self.success = bool(res.get("success", False))
            self.stdout = str(res.get("stdout") or "")
            self.stderr = str(res.get("stderr") or "")
            self.output = str(res.get("output") or self.stdout or self.stderr)
            self.error = str(res.get("error") or self.stderr or "")
            self.return_code = res.get("return_code", 0 if self.success else 1)
            self.artifact = res.get("artifact")
        else:
            self.success = bool(getattr(res, "success", False))
            self.stdout = str(getattr(res, "stdout", "") or "")
            self.stderr = str(getattr(res, "stderr", "") or "")
            self.output = str(getattr(res, "output", "") or self.stdout or self.stderr)
            self.error = str(getattr(res, "error", "") or self.stderr or "")
            self.return_code = getattr(res, "return_code", 0 if self.success else 1)
            self.artifact = getattr(res, "artifact", None)

    def get(self, key, default=None):
        if isinstance(self._res, dict):
            return self._res.get(key, default)
        return getattr(self, key, default)

    def __getitem__(self, key):
        if isinstance(self._res, dict):
            return self._res[key]
        return getattr(self, key)


class EngineerContext:
    """Утилиты анализа файлов, контекста и песочницы для инженера NeonRoute."""

    @staticmethod
    def normalize_artifact_format(raw_val: str, task_text: str = "", has_verification_script: bool = False) -> str:
        """Нормализует формат задачи по фиксированной матрице форматов NeonRoute."""
        val = (raw_val or "").strip().lower()
        if val in ("excel", "word", "pygame", "tkinter", "cli", "module"):
            return val
        t = (task_text or "").lower()
        if val in ("document", "docs", "doc", "spreadsheet", "table"):
            if any(w in t for w in (".docx", "word", "ворд", "договор", "приказ")):
                return "word"
            return "excel"
        if val in ("gui_app", "gui", "game", "2d_game"):
            if any(w in t for w in ("tkinter", "tk", "ttk", "canvas", "калькулятор")):
                return "tkinter"
            return "pygame"
        if val in ("cli_tool", "cli_script", "script", "console"):
            return "cli"
        if val in ("module", "library", "algo", "algorithm", "data_structure"):
            return "module"
        if has_verification_script:
            return "module"
        # Сопоставление с ближайшим форматом по ключевым словам
        if any(k in t for k in (".xlsx", ".xls", "excel", "эксель")):
            return "excel"
        if any(k in t for k in (".docx", ".doc", "word", "ворд")):
            return "word"
        if any(k in t for k in ("pygame", "тетрис", "змейк")):
            return "pygame"
        if any(k in t for k in ("tkinter", "customtkinter")):
            return "tkinter"
        if any(k in t for k in ("модуль", "module", "класс", "структур", "алгоритм")):
            return "module"
        return "cli"

    @staticmethod
    def has_python_code(code: str) -> bool:
        """Определяет, содержит ли текст реальный исполняемый код Python."""
        if not code or not code.strip():
            return False
        try:
            t = ast.parse(code)
            if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom, ast.Assign, ast.If, ast.While, ast.For)) for n in t.body):
                return True
        except SyntaxError:
            pass
        strong_py = (
            r'^\s*(?:import\s+[a-zA-Z0-9_.]+|from\s+[a-zA-Z0-9_.]+\s+import)',
            r'^\s*def\s+[a-zA-Z_]\w*\s*\(',
            r'^\s*class\s+[a-zA-Z_]\w*',
            r'^\s*if\s+__name__\s*==\s*[\'"]__main__[\'"]'
        )
        return any(re.search(pat, line) for line in code.splitlines() for pat in strong_py)

    @staticmethod
    def is_document_task(task_text: str) -> bool:
        """Фасад обратной совместимости для внешних вызовов (например, из engineer_controller)."""
        t = (task_text or "").lower()
        return any(ext in t for ext in ('.xlsx', '.docx', '.xls', '.doc', 'excel', 'эксель', 'word', 'ворд', 'таблиц', 'смет'))

    @staticmethod
    def extract_pure_py(raw_text: str, orig_content: Optional[str] = None) -> str:
        """Надежный многоуровневый парсер Python-кода из ответов LLM."""
        if not raw_text or not raw_text.strip():
            return ""

        text = raw_text.strip()

        # 1. Поиск всех закрытых блоков кода с явным указанием python/py
        py_blocks = re.findall(r'```(?:python|py)\s*[\r\n]+([\s\S]*?)```', text, re.IGNORECASE)
        if py_blocks:
            if orig_content is not None and len(py_blocks) > 1:
                orig_stripped = orig_content.strip()
                diff_blocks = [b for b in py_blocks if b.strip() != orig_stripped]
                if diff_blocks:
                    return max(diff_blocks, key=len).strip()
            return max(py_blocks, key=len).strip()

        # 2. Поиск любых закрытых блоков ``` ... ```
        any_blocks = re.findall(r'```\s*[\r\n]+([\s\S]*?)```', text)
        if any_blocks:
            if orig_content is not None and len(any_blocks) > 1:
                orig_stripped = orig_content.strip()
                diff_blocks = [b for b in any_blocks if b.strip() != orig_stripped]
                if diff_blocks:
                    return max(diff_blocks, key=len).strip()
            return max(any_blocks, key=len).strip()

        # 3. Случай оборванного ответа (незакрытый блок в конце генерации)
        unclosed = re.search(r'```(?:python|py)?\s*[\r\n]+([\s\S]+)$', text, re.IGNORECASE)
        if unclosed:
            return unclosed.group(1).strip()

        # 4. Если блок кода не был обернут в markdown вовсе:
        # Проверяем, не является ли весь текст целиком валидным Python-кодом (защита констант до import)
        try:
            t_check = ast.parse(text)
            if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom, ast.Assign, ast.If, ast.While, ast.For)) for n in t_check.body):
                return text
        except SyntaxError:
            pass

        lines = text.splitlines()
        # Ищем первую строку, начиная с которой блок валидно компилируется через AST
        for idx in range(len(lines)):
            sub_text = "\n".join(lines[idx:]).strip()
            if not sub_text:
                continue
            try:
                t_sub = ast.parse(sub_text)
                if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom, ast.Assign, ast.If, ast.While, ast.For)) for n in t_sub.body):
                    return sub_text
            except SyntaxError:
                continue

        code_start = -1
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(('import ', 'from ', 'def ', 'class ', 'if __name__')):
                code_start = idx
                break

        if code_start != -1:
            return "\n".join(lines[code_start:]).strip()

        return text

    @staticmethod
    def inspect_workspace_file(
        task_prompt: str,
        context_hint: str = "",
        is_new_intent: bool = False,
        target_file: Optional[str] = None,
        max_lines: int = MAX_FULL_CODE_LINES
    ) -> Optional[str]:
        """Сбор файлового контекста с порогом AST-скелетирования для файлов > max_lines строк."""
        if is_new_intent or EngineerContext.is_document_task(f"{task_prompt} {context_hint}"):
            return None

        combined = f"{task_prompt}\n{context_hint}"
        ws_dir = SandboxManager.get_workspace_dir()
        if not ws_dir.exists():
            return None

        found_paths: List[Path] = []

        # 1. Приоритет: файл, явно определенный Архитектором
        if target_file:
            tf_path = (ws_dir / target_file).resolve()
            if tf_path.is_file() and tf_path.parent == ws_dir:
                found_paths.append(tf_path)

        # 2. Файлы, упомянутые в запросе пользователя или подсказке
        if not found_paths:
            candidates = re.findall(r'\b([a-zA-Z0-9_\-]+\.[a-zA-Z0-9_]+)\b', combined)
            for cand in set(candidates):
                p = (ws_dir / cand).resolve()
                if p.is_file() and p.parent == ws_dir:
                    found_paths.append(p)

        # 3. Fallback: поиск главного исполняемого файла в каталоге
        if not found_paths:
            entry_cand = ws_dir / "main.py"
            if entry_cand.is_file():
                found_paths.append(entry_cand)
            else:
                for py_file in ws_dir.glob("*.py"):
                    try:
                        text = py_file.read_text(encoding="utf-8", errors="ignore")
                        if "__main__" in text:
                            found_paths.append(py_file)
                            break
                    except Exception:
                        pass

        if not found_paths:
            return None

        # Поиск связанных локальных файлов через AST импорты
        local_deps: Set[Path] = set()
        for target_path in found_paths:
            if target_path.suffix.lower() in (".py", ".pyw"):
                try:
                    c_text = target_path.read_text(encoding="utf-8", errors="ignore")
                    tree = ast.parse(c_text)
                    for node in ast.walk(tree):
                        mod_name = None
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                mod_name = alias.name.split(".")[0]
                                cand_dep = (ws_dir / f"{mod_name}.py").resolve()
                                if cand_dep.is_file() and cand_dep not in found_paths:
                                    local_deps.add(cand_dep)
                        elif isinstance(node, ast.ImportFrom) and node.module:
                            mod_name = node.module.split(".")[0]
                            cand_dep = (ws_dir / f"{mod_name}.py").resolve()
                            if cand_dep.is_file() and cand_dep not in found_paths:
                                local_deps.add(cand_dep)
                except Exception:
                    pass

        blocks: List[str] = []

        # 1. Основные целевые файлы
        for target_path in found_paths:
            try:
                content = target_path.read_text(encoding="utf-8", errors="ignore")
                lines = content.splitlines()
                line_count = len(lines)

                if line_count <= max_lines:
                    blocks.append(
                        f"\n\n[ТЕКУЩАЯ ВЕРСИЯ ФАЙЛА НА ДИСКЕ: {target_path.name} ({line_count} строк)]\n"
                        f"```python\n{content}\n```\n"
                        f"Внимание: строго сохраняй существующие имена атрибутов, классов и логику при модификации."
                    )
                else:
                    skel = ASTSkeletonizer.skeletonize(content)
                    blocks.append(
                        f"\n\n[АРХИТЕКТУРНЫЙ СКЕЛЕТ ФАЙЛА: {target_path.name} (всего {line_count} строк)]\n"
                        f"```python\n{skel}\n```\n"
                        f"Внимание: используй существующие сигнатуры и архитектуру классов."
                    )
            except Exception as e:
                logger.warning(f"Ошибка инспекции файла {target_path.name}: {e}")

        # 2. Связанные зависимые модули (компактные AST-скелеты)
        for dep_path in sorted(local_deps):
            try:
                dep_content = dep_path.read_text(encoding="utf-8", errors="ignore")
                skel = ASTSkeletonizer.skeletonize(dep_content)
                blocks.append(
                    f"\n\n[АРХИТЕКТУРНЫЙ СКЕЛЕТ СВЯЗАННОГО МОДУЛЯ: {dep_path.name}]\n"
                    f"```python\n{skel}\n```\n"
                    f"Внимание: используй существующие классы и функции этого модуля при работе с кодом."
                )
            except Exception as e:
                logger.warning(f"Ошибка инспекции зависимого модуля {dep_path.name}: {e}")

        return "".join(blocks) if blocks else None

    @staticmethod
    def rollback_workspace(*args, **kwargs) -> bool:
        """
        Универсальный атомарный откат песочницы.
        Безопасен к любым старым сигнатурам вызова (file_backups, write_blocks, ws_dir).
        """
        ws_dir = None
        for arg in args:
            if isinstance(arg, (Path, str)):
                try:
                    p = Path(arg)
                    if p.is_dir() or p.parent.exists():
                        ws_dir = p
                        break
                except Exception:
                    pass
        if ws_dir is None:
            ws_dir = kwargs.get("ws_dir") or SandboxManager.get_workspace_dir()

        res = GitSandboxManager.rollback(Path(ws_dir))
        file_backups = kwargs.get("file_backups")
        if not file_backups:
            for arg in args:
                if isinstance(arg, dict):
                    file_backups = arg
                    break

        write_blocks = kwargs.get("write_blocks")
        if not write_blocks:
            for arg in args:
                if isinstance(arg, list):
                    write_blocks = arg
                    break

        if file_backups and (not res or not GitSandboxManager.is_git_available()):
            for b_name, b_content in file_backups.items():
                try:
                    (Path(ws_dir) / b_name).write_text(b_content, encoding="utf-8")
                except Exception as _e_rb:
                    logger.warning(f"EngineerContext: ошибка отката бэкапа {b_name}: {_e_rb}")
            if write_blocks:
                for fn, _ in write_blocks:
                    if fn not in file_backups:
                        try:
                            (Path(ws_dir) / fn).unlink(missing_ok=True)
                        except Exception:
                            pass
        return bool(res)


__all__ = [
    "BINARY_EXTENSIONS",
    "MAX_FULL_CODE_LINES",
    "_RunResultWrapper",
    "EngineerContext",
]
