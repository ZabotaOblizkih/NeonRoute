"""
Tool Engine for Mini-Antigravity in NeonRoute
Parses, dispatches, and executes autonomous tools inside Sandbox Workspace (C:\\NeonRoute\\workspace)
and Read-Only System Storage.
"""
import os
import re
import sys
import ast
import time
import shutil
import logging
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple, Union
from services.sandbox_manager import SandboxManager, ScriptExecutionResult
from services.code_engine import ASTSkeletonizer, PatchApplier, SelfHealingEngine
from services.tool_parser import ToolCall, ToolResult, ToolParser
from config import OUTPUTS_DIR
logger = logging.getLogger("NeonRoute.ToolEngine")
__all__ = ["ToolEngine", "ToolCall", "ToolResult", "ToolParser", "sanitize_code"]


def sanitize_code(raw_code: str, module_name: str) -> str:
    """Вырезает случайные рекурсивные самоимпорты вида 'from foo import ...' или 'import foo'."""
    lines = []
    clean_mod = module_name.replace(".py", "")
    for line in raw_code.splitlines():
        if re.match(rf'^\s*(from\s+{clean_mod}\s+import|import\s+{clean_mod}\b)', line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


class ToolEngine:
    """
    Dispatcher and execution engine for Mini-Antigravity tools.
    Strictly isolated to C:\\NeonRoute\\workspace for writes/scripts.
    """
    sanitize_code = staticmethod(sanitize_code)

    @classmethod

    def set_active_session_id(cls, session_id: Optional[str]) -> None:
        SandboxManager.set_active_session_id(session_id)

    @classmethod

    def get_active_session_id(cls) -> Optional[str]:
        return SandboxManager.get_active_session_id()

    @classmethod

    def write_file(cls, filename: str, content: str) -> ToolResult:
        try:
            # Предохранитель от самоимпорта sanitize_code
            clean_name = SandboxManager.clean_workspace_filename(filename)
            if clean_name.lower().endswith((".py", ".pyw")):
                content = sanitize_code(content, clean_name)
            # Дешёвый in-memory AST-чекер перед записью на диск и запуском песочницы
            if clean_name.lower().endswith((".py", ".pyw")):
                from services.code_engine import ExecutionSandbox
                is_valid, lint_msg = ExecutionSandbox.preflight_lint(content)
                if not is_valid:
                    err_msg = f"[Ошибка синтаксиса до записи]: {lint_msg}"
                    logger.warning(f"ToolEngine: {err_msg} в {clean_name}")
                    return ToolResult(
                        tool_name="write_file",
                        success=False,
                        output=err_msg,
                        stderr=err_msg,
                        exit_code=1
                    )
            msg = SandboxManager.write_file(filename, content)
            is_err = msg.startswith("Ошибка:") or msg.startswith("[Ошибка")
            target_path = (SandboxManager.get_workspace_dir() / clean_name).resolve()
            if is_err:
                logger.warning(msg)
            else:
                logger.info(msg)
            return ToolResult(
                tool_name="write_file",
                success=not is_err,
                output=msg,
                artifact_path=target_path
            )
        except Exception as e:
            err_msg = f"Ошибка записи файла: {e}"
            logger.error(err_msg)
            return ToolResult(tool_name="write_file", success=False, output=err_msg)

    @classmethod
    def run_script(cls, filename: str, args: Optional[Union[str, List[str]]] = None) -> ToolResult:
        """Делегирует исполнение в изолированный SandboxManager с поддержкой CLI-аргументов."""
        res = SandboxManager.run_script(filename, args=args)
        return ToolResult(
            tool_name="run_script",
            success=res.success,
            output=res.output,
            artifact_path=res.artifact_path,
            is_script_run=True,
            stdout=res.stdout,
            stderr=res.stderr,
            exit_code=res.return_code
        )

    @classmethod

    def run_pytest(cls, filename: str, timeout: int = 45) -> ToolResult:
        """Делегирует запуск тестов pytest в SandboxManager."""
        res = SandboxManager.run_pytest(filename, timeout=timeout)
        return ToolResult(
            tool_name="run_pytest",
            success=res.success,
            output=res.output,
            artifact_path=res.artifact_path,
            is_script_run=True,
            stdout=res.stdout,
            stderr=res.stderr,
            exit_code=res.return_code
        )

    @staticmethod

    def check_artifact_substance(file_path: Path, min_bytes: int = 300) -> Tuple[bool, str]:
        """Проверяет, что созданный артефакт содержит реальный код, а не пустую заглушку."""
        file_path = Path(file_path)
        if not file_path.is_file():
            return False, f"Файл '{file_path.name}' не создан на диске (не найден на диске)."
        raw_text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
        byte_len = len(raw_text.encode("utf-8"))
        if byte_len < min_bytes:
            return False, f"Файл '{file_path.name}' слишком мал ({byte_len} байт < {min_bytes} байт, подозрительно мал). Реализация отсутствует."
        try:
            tree = ast.parse(raw_text)
            definitions = [
                n for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            if not definitions:
                return False, f"Файл '{file_path.name}' не содержит ни классов, ни функций (только импорты или pass, не содержит классов или функций)."

            # Проверка на пустые методы-заглушки (методы класса / функции, тело которых состоит только из pass, ... или docstring)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    is_abstract = any(
                        (getattr(d, "id", "") == "abstractmethod" or getattr(d, "attr", "") == "abstractmethod")
                        for d in getattr(node, "decorator_list", [])
                    )
                    if is_abstract:
                        continue
                    stmts = [
                        s for s in node.body
                        if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant) and isinstance(s.value.value, str))
                    ]
                    if not stmts:
                        return False, (
                            f"Функция/метод '{node.name}' в файле '{file_path.name}' является пустой заглушкой "
                            f"(содержит только docstring без тела реализации)."
                        )
                    if len(stmts) == 1:
                        stmt = stmts[0]
                        if isinstance(stmt, ast.Pass):
                            return False, (
                                f"Функция/метод '{node.name}' в файле '{file_path.name}' содержит только 'pass' "
                                f"вместо рабочей реализации."
                            )
                        if isinstance(stmt, ast.Expr) and (
                            getattr(stmt.value, "value", None) is Ellipsis or
                            (isinstance(getattr(stmt, "value", None), ast.Constant) and stmt.value.value is Ellipsis)
                        ):
                            return False, (
                                f"Функция/метод '{node.name}' в файле '{file_path.name}' заменена на заглушку '...' "
                                f"вместо рабочей реализации."
                            )
        except SyntaxError as e:
            return False, f"Синтаксическая ошибка в '{file_path.name}': {e}"
        return True, "OK"

    @classmethod

    def find_files(
        cls,
        query: str = "",
        ext: Optional[str] = None,
        search_root: Optional[str] = None,
        content_query: Optional[str] = None,
        **kwargs
    ) -> ToolResult:
        try:
            q_lower = query.strip().lower() if query else ""
            c_lower = content_query.strip().lower() if content_query else ""
            target_ext = f".{ext.lstrip('.').lower()}" if ext else None
            matches: List[Path] = []
            seen_paths = set()
            MAX_CONTENT_FILE_SIZE = 15 * 1024 * 1024
            def match_content(p_file: Path) -> bool:
                if not c_lower:
                    return True
                if p_file.name.startswith("~$") or p_file.name.startswith("."):
                    return False
                try:
                    f_size = p_file.stat().st_size
                    if f_size > MAX_CONTENT_FILE_SIZE or f_size == 0:
                        return False
                except Exception:
                    return False
                f_suffix = p_file.suffix.lower()
                c_clean = " ".join(c_lower.split())
                zip_office_exts = {".docx", ".xlsx", ".xlsm", ".pptx", ".odt", ".ods"}
                if f_suffix in zip_office_exts:
                    try:
                        import zipfile
                        import xml.etree.ElementTree as ET
                        with zipfile.ZipFile(p_file) as z:
                            xml_parts = [n for n in z.namelist() if n.endswith(".xml")]
                            full_chunks = []
                            for part in xml_parts:
                                try:
                                    xml_bytes = z.read(part)
                                    tree = ET.fromstring(xml_bytes)
                                    text_nodes = [elem.text for elem in tree.iter() if elem.text]
                                    if text_nodes:
                                        full_chunks.append(" ".join(text_nodes))
                                except Exception:
                                    continue
                            combined = " ".join(" ".join(full_chunks).lower().split())
                            if c_clean in combined:
                                return True
                    except Exception:
                        pass
                    return False
                if f_suffix in {".doc", ".xls", ".ppt", ".rtf"}:
                    try:
                        raw_bytes = p_file.read_bytes()
                        for enc in ("cp1251", "utf-16le", "utf-8"):
                            try:
                                dec_text = raw_bytes.decode(enc, errors="ignore").lower()
                                dec_text = " ".join(dec_text.split())
                                if c_clean in dec_text:
                                    return True
                            except Exception:
                                continue
                    except Exception:
                        pass
                    return False
                code_exts = {
                    ".py", ".pyw", ".ipynb", ".json", ".csv", ".tsv", ".txt", ".md",
                    ".sql", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log",
                    ".html", ".htm", ".xml", ".css", ".scss", ".js", ".jsx", ".ts", ".tsx",
                    ".bat", ".cmd", ".ps1", ".sh", ".bash", ".zsh",
                    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".cs", ".rs", ".go", ".java", ".kt"
                }
                if f_suffix in code_exts or f_suffix == "" or len(f_suffix) <= 5:
                    for enc in ("utf-8", "cp1251", "latin-1", "utf-16"):
                        try:
                            with open(p_file, "r", encoding=enc, errors="ignore") as f:
                                sample = f.read(5 * 1024 * 1024).lower()
                                sample = " ".join(sample.split())
                                if c_clean in sample:
                                    return True
                            break
                        except Exception:
                            continue
                return False
            def check_file(p_file: Path):
                if p_file in seen_paths or p_file.name.startswith("~$"):
                    return
                f_name = p_file.name.lower()
                matches_query = not q_lower or (q_lower in f_name)
                matches_ext = not target_ext or p_file.suffix.lower() == target_ext
                if matches_query and matches_ext and match_content(p_file):
                    seen_paths.add(p_file)
                    matches.append(p_file)
            user_home = Path.home()
            target_roots: List[Path] = []
            if search_root:
                sr_norm = str(search_root).strip().lower()
                if sr_norm in ("documents", "документы", "docs", "~/documents"):
                    target_roots.append(user_home / "Documents")
                elif sr_norm in ("desktop", "рабочий стол", "~/desktop"):
                    target_roots.append(user_home / "Desktop")
                elif sr_norm in ("downloads", "загрузки", "~/downloads"):
                    target_roots.append(user_home / "Downloads")
                elif sr_norm in ("workspace", "песочница"):
                    target_roots.append(SandboxManager.get_workspace_dir())
                else:
                    r_cand = Path(search_root).resolve()
                    if r_cand.exists():
                        target_roots.append(r_cand)
            if not target_roots:
                active_sid = SandboxManager.get_active_session_id()
                sess_dir = (OUTPUTS_DIR / "sessions" / active_sid).resolve() if active_sid else None
                target_roots = [
                    user_home / "Documents",
                    SandboxManager.get_workspace_dir(),
                    user_home / "Downloads",
                    user_home / "Desktop"
                ]
                if sess_dir and sess_dir.exists():
                    target_roots.insert(1, sess_dir)
            for p_dir in target_roots:
                if not p_dir.exists():
                    continue
                try:
                    for root, dirs, files in os.walk(str(p_dir)):
                        try:
                            rel_depth = len(Path(root).relative_to(p_dir).parts)
                            if rel_depth >= 3:
                                dirs.clear()
                        except ValueError:
                            pass
                        ignored_custom = {
                            "neonroute", ".git", "venv", ".venv", "__pycache__",
                            "history", "logs", "node_modules", "appdata", "site-packages"
                        }
                        dirs[:] = [d for d in dirs if not SandboxManager.is_ignored_directory(d) and d.lower() not in ignored_custom]
                        for f in files:
                            check_file(Path(root) / f)
                            if len(matches) >= 50:
                                break
                        if len(matches) >= 50:
                            break
                except Exception as e:
                    logger.warning(f"Error scanning dir {p_dir}: {e}")
                if len(matches) >= 50:
                    break
            if not matches:
                desc = []
                if query:
                    desc.append(f"по имени '{query}'")
                if content_query:
                    desc.append(f"с содержимым '{content_query}'")
                desc_str = " ".join(desc) if desc else "по заданным критериям"
                res_text = f"Файлы {desc_str} не найдены."
                return ToolResult(tool_name="find_files", success=True, output=res_text)
            nl = chr(10)
            lines = [f"Найдено совпадений: {len(matches)}"]
            for m_path in matches:
                lines.append(f"- {m_path}")
            return ToolResult(tool_name="find_files", success=True, output=nl.join(lines))
        except Exception as e:
            err = f"Ошибка поиска файлов: {e}"
            logger.error(err)
            return ToolResult(tool_name="find_files", success=False, output=err)

    @classmethod

    def read_file(
        cls,
        filename: str,
        start_line: int = 1,
        max_lines: int = 100,
        session_id: Optional[str] = None
    ) -> ToolResult:
        try:
            target_path = SandboxManager.validate_read_file_path(filename, session_id=session_id)
            content = None
            for enc in ("utf-8", "cp1251", "utf-8-sig"):
                try:
                    with open(target_path, "r", encoding=enc) as f:
                        content = f.read()
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if content is None:
                with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            all_lines = content.splitlines()
            total_lines = len(all_lines)
            start_l = max(1, int(start_line)) if str(start_line).isdigit() else 1
            max_l = max(1, min(int(max_lines), 500)) if str(max_lines).isdigit() else 100
            if total_lines == 0:
                output = "[строки 0-0 из 0]:\n(файл пуст)"
            elif start_l > total_lines:
                output = f"[строки {start_l}-{start_l} из {total_lines}]:\n(начальная строка выходит за пределы файла)"
            else:
                slice_lines = all_lines[start_l - 1 : start_l - 1 + max_l]
                end_l = start_l + len(slice_lines) - 1
                formatted_lines = [
                    f"{start_l + i:04d} | {line}"
                    for i, line in enumerate(slice_lines)
                ]
                header = f"[строки {start_l}-{end_l} из {total_lines}]:"
                output = f"{header}\n" + "\n".join(formatted_lines)
            return ToolResult(
                tool_name="read_file",
                success=True,
                output=output,
                artifact_path=target_path
            )
        except (PermissionError, FileNotFoundError, IsADirectoryError, ValueError) as e:
            return ToolResult(tool_name="read_file", success=False, output=str(e))
        except Exception as e:
            err = f"Ошибка чтения файла: {e}"
            logger.error(err)
            return ToolResult(tool_name="read_file", success=False, output=err)

    @classmethod

    def read_document(cls, path: str, max_chars: int = 3000) -> ToolResult:
        try:
            target_path = SandboxManager.validate_read_path(path)
            if not target_path.is_file():
                return ToolResult(tool_name="read_document", success=False, output=f"Путь не является файлом: {target_path}")
            readable_exts = {
                ".txt", ".py", ".json", ".csv", ".md", ".log",
                ".yaml", ".yml", ".ini", ".toml", ".js", ".html",
                ".css", ".xml", ".bat", ".cmd", ".sql", ".sh",
                ".docx", ".xlsx"
            }
            if target_path.suffix.lower() not in readable_exts:
                return ToolResult(
                    tool_name="read_document",
                    success=False,
                    output=f"Файл {target_path.name} не является поддерживаемым текстовым документом ({target_path.suffix})."
                )
            content = None
            ext = target_path.suffix.lower()
            if ext == ".docx":
                try:
                    import docx
                    doc = docx.Document(target_path)
                    paras = [p.text for p in doc.paragraphs if p.text.strip()]
                    content = "\n\n".join(paras)
                except Exception as e:
                    return ToolResult(tool_name="read_document", success=False, output=f"Ошибка чтения .docx документа: {e}")
            elif ext == ".xlsx":
                try:
                    import openpyxl
                    wb = openpyxl.load_workbook(target_path, data_only=True)
                    try:
                        sheets_text = []
                        for sheet_name in wb.sheetnames:
                            sheet = wb[sheet_name]
                            sheets_text.append(f"--- Лист: {sheet_name} ---")
                            for row in sheet.iter_rows(values_only=True):
                                if any(cell is not None for cell in row):
                                    row_str = " | ".join(str(cell) if cell is not None else "" for cell in row)
                                    sheets_text.append(row_str)
                        content = "\n".join(sheets_text)
                    finally:
                        wb.close()
                except Exception as e:
                    return ToolResult(tool_name="read_document", success=False, output=f"Ошибка чтения .xlsx таблицы: {e}")
            else:
                for enc in ("utf-8", "cp1251", "latin-1"):
                    try:
                        with open(target_path, "r", encoding=enc) as f:
                            content = f.read()
                        break
                    except UnicodeDecodeError:
                        continue
            if content is None:
                return ToolResult(tool_name="read_document", success=False, output=f"Не удалось декодировать текстовый файл: {target_path.name}")
            total_len = len(content)
            if total_len > max_chars:
                displayed = content[:max_chars]
                output = f"{displayed}\n\n[Обрезано: показано {max_chars} из {total_len} символов...]"
            else:
                output = content
            return ToolResult(
                tool_name="read_document",
                success=True,
                output=output,
                artifact_path=target_path
            )
        except Exception as e:
            err = f"Ошибка чтения документа: {e}"
            logger.error(err)
            return ToolResult(tool_name="read_document", success=False, output=err)

    @classmethod

    def open_explorer(cls, path: str) -> ToolResult:
        try:
            target = SandboxManager.validate_read_path(path)
            norm = os.path.normpath(str(target))
            if sys.platform == "win32":
                import ctypes
                if target.is_file():
                    ctypes.windll.shell32.ShellExecuteW(None, "open", "explorer.exe", f'/select,"{norm}"', None, 1)
                else:
                    ctypes.windll.shell32.ShellExecuteW(None, "open", norm, None, None, 1)
            else:
                subprocess.Popen(["xdg-open", norm], shell=False)
            if target.is_file():
                msg = f"Проводник открыт с выделением файла: {norm}"
            else:
                msg = f"Проводник открыт в папке: {norm}"
            return ToolResult(tool_name="open_explorer", success=True, output=msg, artifact_path=target)
        except Exception as e:
            err = f"Ошибка открытия проводника: {e}"
            logger.error(err)
            return ToolResult(tool_name="open_explorer", success=False, output=err)

    @classmethod

    def summarize_folder(cls, path: str) -> ToolResult:
        try:
            target = SandboxManager.validate_read_path(path)
            if not target.is_dir():
                return ToolResult(tool_name="summarize_folder", success=False, output=f"Путь не является папкой: {target}")
            subdirs: List[str] = []
            ext_counts: Dict[str, int] = {}
            total_files = 0
            total_bytes = 0
            for entry in target.iterdir():
                if entry.is_dir():
                    if not SandboxManager.is_ignored_directory(entry.name):
                        subdirs.append(entry.name)
                elif entry.is_file():
                    total_files += 1
                    try:
                        total_bytes += entry.stat().st_size
                    except Exception:
                        pass
                    ext = entry.suffix.lower() or "(без расширения)"
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1
            size_mb = total_bytes / (1024 * 1024)
            lines = [
                f"**Аудит папки:** `{target}`",
                f"- Всего файлов: {total_files} ({size_mb:.2f} МБ)",
                f"- Подпапок первого уровня ({len(subdirs)}): {', '.join(subdirs[:10]) if subdirs else 'нет'}"
            ]
            if len(subdirs) > 10:
                lines[-1] += f" ...и еще {len(subdirs) - 10}"
            if ext_counts:
                ext_items = sorted(ext_counts.items(), key=lambda x: x[1], reverse=True)[:8]
                ext_str = ", ".join(f"`{k}`: {v}" for k, v in ext_items)
                lines.append(f"- Распределение по типам: {ext_str}")
            return ToolResult(
                tool_name="summarize_folder",
                success=True,
                output="\n".join(lines),
                artifact_path=target
            )
        except Exception as e:
            err = f"Ошибка аудита папки: {e}"
            logger.error(err)
            return ToolResult(tool_name="summarize_folder", success=False, output=err)

    @classmethod

    def copy_file(cls, src_path: str, dst_path: Optional[str] = None) -> ToolResult:
        try:
            if not src_path or not src_path.strip():
                return ToolResult(tool_name="copy_file", success=False, output="Ошибка: Не указан исходный файл.")
            raw_src = src_path.strip().strip("'\"`")
            src = Path(raw_src)
            ws_dir = SandboxManager.get_workspace_dir().resolve()
            sid = SandboxManager.get_active_session_id()
            session_dir = (OUTPUTS_DIR / "sessions" / sid).resolve() if sid else (OUTPUTS_DIR / "sessions").resolve()
            if not src.is_absolute():
                cand_ws = (ws_dir / src).resolve()
                if cand_ws.exists() and cand_ws.is_file():
                    src = cand_ws
                elif sid and (session_dir / src).resolve().exists() and (session_dir / src).resolve().is_file():
                    src = (session_dir / src).resolve()
                else:
                    src = cand_ws
            else:
                src = src.resolve()
            if not src.exists() or not src.is_file():
                return ToolResult(tool_name="copy_file", success=False, output=f"Ошибка: Исходный файл не найден: {src_path}")
            # Разрешено копировать ТОЛЬКО файлы внутри ws_dir или папки сессии
            allowed = False
            try:
                if src.is_relative_to(ws_dir):
                    allowed = True
            except (AttributeError, ValueError):
                pass
            if not allowed and session_dir.exists():
                try:
                    if src.is_relative_to(session_dir):
                        allowed = True
                except (AttributeError, ValueError):
                    pass
            if not allowed:
                return ToolResult(tool_name="copy_file", success=False, output="Доступ за пределы рабочей зоны запрещен")
            if not dst_path or not dst_path.strip():
                dst = (ws_dir / src.name).resolve()
            else:
                clean_dst = SandboxManager.clean_workspace_filename(dst_path)
                dst = (ws_dir / clean_dst).resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            with open(dst, "ab") as f:
                f.flush()
                os.fsync(f.fileno())
            size_bytes = dst.stat().st_size
            msg = f"Файл успешно скопирован в рабочую песочницу: {dst} ({size_bytes} байт)"
            logger.info(msg)
            return ToolResult(tool_name="copy_file", success=True, output=msg, artifact_path=dst)
        except Exception as e:
            err = f"Ошибка копирования файла: {e}"
            logger.error(err)
            return ToolResult(tool_name="copy_file", success=False, output=err)

    @classmethod

    def get_ast_skeleton(cls, filename: str) -> ToolResult:
        try:
            target_path = SandboxManager.validate_read_file_path(filename)
            with open(target_path, "r", encoding="utf-8") as f:
                source_code = f.read()
            skeleton_text = ASTSkeletonizer.skeletonize(source_code)
            return ToolResult(
                tool_name="get_ast_skeleton",
                success=True,
                output=skeleton_text,
                artifact_path=target_path
            )
        except Exception as e:
            err_msg = f"[Ошибка get_ast_skeleton]: {e}"
            logger.error(err_msg)
            return ToolResult(tool_name="get_ast_skeleton", success=False, output=err_msg)

    @classmethod

    def patch_file(cls, filename: str, search_block: str = "", replace_block: str = "", diff_body: str = "") -> ToolResult:
        try:
            if not filename or not str(filename).strip():
                return ToolResult(tool_name="patch_file", success=False, output="[Ошибка patch_file]: Не указано имя файла")
            target_path = SandboxManager.validate_read_file_path(filename)
            clean_name = target_path.name
            res = SelfHealingEngine.patch_file_transactional(
                filename=filename,
                search_block=search_block,
                replace_block=replace_block,
                diff_text=diff_body
            )
            if res.success:
                mode_str = " (fuzzy match)" if res.is_fuzzy else ""
                msg = f"[Успешно]: Файл {clean_name} пропатчен{mode_str} (применено замен: {res.applied_count})."
                logger.info(msg)
                return ToolResult(tool_name="patch_file", success=True, output=msg, artifact_path=target_path)
            else:
                err_msg = f"[Ошибка patch_file]: {res.error}"
                logger.warning(err_msg)
                return ToolResult(tool_name="patch_file", success=False, output=err_msg, artifact_path=target_path)
        except Exception as e:
            err = f"[Ошибка patch_file]: {e}"
            logger.error(err)
            return ToolResult(tool_name="patch_file", success=False, output=err)

    @classmethod

    def replace_symbol(cls, filename: str, symbol_name: str, new_code: str) -> ToolResult:
        """
        Точечно заменяет или удаляет класс/метод в файле по AST-координатам:
        - Находит узел (ClassDef, FunctionDef, AsyncFunctionDef) с именем symbol_name.
        - Если new_code.strip() == "DELETED" — удаляет строки узла.
        - Иначе — замещает диапазон строк новым кодом.
        - Валидирует итоговый файл через ast.parse() перед сохранением.
        """
        try:
            if not filename or not str(filename).strip():
                return ToolResult(tool_name="replace_symbol", success=False, output="[Ошибка replace_symbol]: Не указано имя файла")
            if not symbol_name or not str(symbol_name).strip():
                return ToolResult(tool_name="replace_symbol", success=False, output="[Ошибка replace_symbol]: Не указано имя символа")
            target_path = SandboxManager.validate_read_file_path(filename)
            clean_name = target_path.name
            if not target_path.exists() or not target_path.is_file():
                return ToolResult(tool_name="replace_symbol", success=False, output=f"[Ошибка replace_symbol]: Файл {clean_name} не найден")
            content = target_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines(keepends=True)
            try:
                tree = ast.parse(content)
            except SyntaxError as e:
                return ToolResult(tool_name="replace_symbol", success=False, output=f"[Ошибка replace_symbol]: Исходный файл поврежден: {e}")
            target_node = None
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name == symbol_name:
                        target_node = node
                        break
            if not target_node:
                return ToolResult(tool_name="replace_symbol", success=False, output=f"[Ошибка replace_symbol]: Символ '{symbol_name}' не найден в AST файла {clean_name}")
            start_idx = target_node.lineno - 1
            end_idx = getattr(target_node, "end_lineno", start_idx + 1)
            orig_line = lines[start_idx]
            orig_indent_len = len(orig_line) - len(orig_line.lstrip())
            base_indent = " " * orig_indent_len
            clean_new_code = (new_code or "").strip("\r\n")
            if clean_new_code == "DELETED" or not clean_new_code.strip():
                updated_lines = lines[:start_idx] + lines[end_idx:]
                action_desc = f"удален из строк {start_idx + 1}–{end_idx}"
            else:
                import textwrap
                dedented = textwrap.dedent(clean_new_code)
                normalized_code = textwrap.indent(dedented, base_indent)
                if not normalized_code.endswith("\n"):
                    normalized_code += "\n"
                updated_lines = lines[:start_idx] + [normalized_code] + lines[end_idx:]
                action_desc = f"заменен на новый код (строки {start_idx + 1}–{end_idx})"
            updated_content = "".join(updated_lines)
            # Pre-flight проверка синтаксиса
            try:
                ast.parse(updated_content)
            except SyntaxError as e:
                return ToolResult(
                    tool_name="replace_symbol",
                    success=False,
                    output=f"[Ошибка replace_symbol]: Результирующий код содержит SyntaxError: {e}"
                )
            # Фиксация на диск
            target_path.write_text(updated_content, encoding="utf-8")
            msg = f"[Успешно]: Символ '{symbol_name}' в файле {clean_name} {action_desc}."
            logger.info(msg)
            return ToolResult(tool_name="replace_symbol", success=True, output=msg, artifact_path=target_path)
        except Exception as e:
            err = f"[Ошибка replace_symbol]: {e}"
            logger.error(err)
            return ToolResult(tool_name="replace_symbol", success=False, output=err)

    @classmethod

    def execute_git_checkpoint(cls, message: str, make_backup: bool = True) -> str:
        from services.git_sandbox import GitSandboxManager
        from services.sandbox_manager import SandboxManager
        ws_dir = SandboxManager.get_workspace_dir()
        msg = (message or "").strip() or "Manual user checkpoint"
        ok = GitSandboxManager.checkpoint(ws_dir, msg)
        status = f"Чекпоинт '{msg}' зафиксирован в Git." if ok else "Не удалось зафиксировать чекпоинт."
        if make_backup:
            b_path = GitSandboxManager.create_backup_bundle(ws_dir, label=msg)
            if b_path:
                status += f"\nЛокальный офлайн-бэкап сохранён: {b_path.name}"
        return status

    @classmethod

    def set_reminder(cls, text: str, time: str) -> str:
        if not text or not time:
            return "[Ошибка]: Необходимо указать text и time для напоминания."
        from datetime import datetime, timedelta
        from services.memory_db import memory_db
        now = datetime.now()
        target_dt = None
        clean_time = time.strip()
        rel_match = re.match(r"^\+?(\d+)\s*([smhd])$", clean_time.lower())
        if rel_match:
            val = int(rel_match.group(1))
            unit = rel_match.group(2)
            unit_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
            target_dt = now + timedelta(**{unit_map[unit]: val})
        if not target_dt:
            try:
                import dateparser
                settings = {
                    'PREFER_DATES_FROM': 'future',
                    'RELATIVE_BASE': now,
                    'RETURN_AS_TIMEZONE_AWARE': False
                }
                norm_time = re.sub(r'\b(?:в|at|к)\s+', '', clean_time, flags=re.IGNORECASE).strip()
                norm_time = re.sub(r'\s+года?\b', '', norm_time, flags=re.IGNORECASE).strip()
                target_dt = dateparser.parse(norm_time, languages=['ru', 'en'], settings=settings)
            except Exception:
                pass
        if not target_dt:
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%H:%M:%S", "%H:%M"]:
                try:
                    parsed = datetime.strptime(clean_time, fmt)
                    if fmt in ["%H:%M:%S", "%H:%M"]:
                        target_dt = now.replace(hour=parsed.hour, minute=parsed.minute, second=getattr(parsed, "second", 0), microsecond=0)
                        if target_dt < now:
                            target_dt += timedelta(days=1)
                    elif fmt == "%d.%m.%Y":
                        target_dt = parsed.replace(hour=9, minute=0, second=0, microsecond=0)
                    else:
                        target_dt = parsed
                    break
                except ValueError:
                    continue
        if not target_dt:
            return f"[Ошибка]: Не удалось распознать время '{time}'."
        target_iso = target_dt.strftime("%Y-%m-%d %H:%M:%S")
        r_id = memory_db.add_reminder(text, target_iso)
        if r_id != -1:
            return f"[Успешно]: Напоминание #{r_id} установлено на {target_iso}: «{text}»."
        return "[Ошибка]: Не удалось сохранить напоминание в базу данных."

    @classmethod

    def execute_tool_call(cls, call: ToolCall, session_id: Optional[str] = None) -> ToolResult:
        name = call.name.lower()
        p = call.params
        if name == "write_file":
            filename = p.get("filename") or p.get("path") or p.get("file") or p.get("name") or "output.txt"
            content = p.get("content") or (call.body or "")
            return cls.write_file(filename=filename, content=content)
        elif name == "run_script":
            raw_fn = (
                p.get("filename")
                or p.get("path")
                or p.get("script")
                or p.get("file")
                or p.get("command")
                or p.get("name")
                or ""
            )
            return cls.run_script(filename=raw_fn)
        elif name == "find_files":
            query = p.get("query") or ""
            ext = p.get("ext")
            search_root = p.get("search_root") or p.get("root") or p.get("path")
            content_query = p.get("content_query") or p.get("content") or p.get("text")
            return cls.find_files(query=query, ext=ext, search_root=search_root, content_query=content_query)
        elif name == "read_file":
            fn = p.get("filename") or p.get("path") or p.get("file") or p.get("name") or ""
            try:
                st = int(p.get("start_line", 1))
            except (ValueError, TypeError):
                st = 1
            try:
                ml = int(p.get("max_lines", 100))
            except (ValueError, TypeError):
                ml = 100
            call_sid = p.get("session_id") or session_id
            return cls.read_file(filename=fn, start_line=st, max_lines=ml, session_id=call_sid)
        elif name == "read_document":
            path = p.get("path") or p.get("filename") or ""
            max_chars = int(p.get("max_chars", 3000))
            return cls.read_document(path=path, max_chars=max_chars)
        elif name == "open_explorer":
            path = p.get("path") or p.get("filename") or ""
            return cls.open_explorer(path=path)
        elif name == "summarize_folder":
            path = p.get("path") or p.get("folder") or ""
            return cls.summarize_folder(path=path)
        elif name == "get_ast_skeleton":
            filename = p.get("filename") or p.get("path") or p.get("file") or p.get("name") or ""
            return cls.get_ast_skeleton(filename=filename)
        elif name == "patch_file":
            filename = p.get("filename") or p.get("path") or p.get("file") or p.get("name") or ""
            search_block = p.get("search") or p.get("search_block") or ""
            replace_block = p.get("replace") or p.get("replace_block") or ""
            diff_body = call.body or p.get("diff") or p.get("diff_text") or ""
            return cls.patch_file(filename=filename, search_block=search_block, replace_block=replace_block, diff_body=diff_body)
        elif name == "replace_symbol":
            filename = p.get("filename") or p.get("path") or p.get("file") or p.get("name") or ""
            symbol_name = p.get("symbol_name") or p.get("name") or p.get("symbol") or ""
            new_code = call.body or p.get("code") or p.get("new_code") or ""
            return cls.replace_symbol(filename=filename, symbol_name=symbol_name, new_code=new_code)
        elif name == "copy_file":
            src = p.get("src") or p.get("source") or p.get("path") or p.get("file") or p.get("from") or ""
            dst = p.get("dst") or p.get("dest") or p.get("destination") or p.get("target") or p.get("to")
            return cls.copy_file(src_path=src, dst_path=dst)
        elif name == "set_reminder":
            r_text = p.get("text") or p.get("message") or p.get("remind") or p.get("content") or (call.body or "")
            r_time = p.get("time") or p.get("remind_at") or p.get("at") or p.get("when") or ""
            out = cls.set_reminder(text=r_text, time=r_time)
            is_err = out.startswith("[Ошибка") or out.startswith("Ошибка")
            return ToolResult(tool_name="set_reminder", success=not is_err, output=out)
        elif name == "git_checkpoint":
            msg = p.get("message") or p.get("msg") or p.get("m") or (call.body or "")
            b_val = p.get("backup", "true")
            make_backup = str(b_val).strip().lower() not in ("false", "0", "no")
            out = cls.execute_git_checkpoint(message=msg, make_backup=make_backup)
            is_err = "Не удалось" in out
            return ToolResult(tool_name="git_checkpoint", success=not is_err, output=out)
        else:
            return ToolResult(
                tool_name=name,
                success=False,
                output=f"Неизвестный инструмент: '{name}'"
            )

    @classmethod

    def execute_tool(cls, name: str, params: Dict[str, Any], content: str = "") -> str:
        call = ToolCall(name=name, params=dict(params), body=content)
        res = cls.execute_tool_call(call)
        return res.output
    _parse_attributes = staticmethod(ToolParser._parse_attributes)
    normalize_tool_text = staticmethod(ToolParser.normalize_tool_text)
    parse_tool_calls = staticmethod(ToolParser.parse_tool_calls)
    truncate_after_tool_call = staticmethod(ToolParser.truncate_after_tool_call)
    parse_tool_call = staticmethod(ToolParser.parse_tool_call)
    strip_tool_tags = staticmethod(ToolParser.strip_tool_tags)
    has_tool_call = staticmethod(ToolParser.has_tool_call)
