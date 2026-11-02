"""
engineer_parser.py — Парсинг ответов LLM, извлечение блоков кода, тегов инструментов и fallback-эвристики.
"""
import re
import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

from services.tool_engine import ToolEngine
from services.code_validator import CodeValidator
from services.engineer_context import EngineerContext, BINARY_EXTENSIONS

logger = logging.getLogger("NeonRoute.EngineerParser")

@dataclass
class ParsedTurnActions:
    """Результат парсинга действий хода инженера."""
    write_blocks: List[Tuple[str, str]] = field(default_factory=list)
    patch_blocks: List[Tuple[str, str]] = field(default_factory=list)
    replace_blocks: List[str] = field(default_factory=list)
    git_checkpoint_results: List[str] = field(default_factory=list)
    last_error: str = ""

    def has_any_actions(self) -> bool:
        return bool(self.write_blocks or self.patch_blocks or self.replace_blocks)

class EngineerResponseParser:
    """Парсер ответов LLM с поддержкой явных тегов и многоуровневых fallback-эвристик."""

    @staticmethod
    def parse_turn_actions(
        response_text: str,
        ws_dir: Path,
        plan: Any,
        task_prompt: str,
        context_hint: str,
        target_artifact: Optional[str],
        target_entry_file: Optional[str],
        script_to_run: Optional[str],
        turn: int,
        turn1_test_signature_mismatch: bool,
        verification_script: Optional[str],
        declared_entry_file: Optional[str],
        is_maintenance: bool,
        planned_files: List[str],
        file_backups: Dict[str, str]
    ) -> ParsedTurnActions:
        actions = ParsedTurnActions()
        response_text = str(response_text or "")

        # 1. Парсинг через теги write_file
        write_blocks = []
        write_pattern = r'<<TOOL:write_file\s+(?:filename|path|file)=["\'](?P<filename>[^"\']+)["\']\s*>>?[\r\n]*(?P<content>.*?)(?:<<?/+TOOL(?::write_file)?>>?|`+/+TOOL(?::write_file)?`+|$)'
        for m in re.finditer(write_pattern, response_text, flags=re.DOTALL | re.IGNORECASE):
            fn = m.group("filename").strip().strip("'\"`")
            cnt = m.group("content").strip()
            if fn and cnt:
                actions.write_blocks.append((fn, cnt))

        patch_blocks = []
        patch_pattern = r'<<TOOL:patch_file\s+(?:filename|path|file)=["\'](?P<filename>[^"\']+)["\'](?:\s+[^>\r\n]+?)?\s*>>?[\r\n]*(?P<body>.*?)(?:<<?/+TOOL(?::patch_file)?>>?|`+/+TOOL(?::patch_file)?`+|$)'
        for m in re.finditer(patch_pattern, response_text, flags=re.DOTALL | re.IGNORECASE):
            fn = m.group("filename").strip().strip("'\"`")
            body = m.group("body").strip()
            if fn and body:
                actions.patch_blocks.append((fn, body))

        replace_blocks = []
        git_checkpoint_results = []
        tool_calls = ToolEngine.parse_tool_calls(response_text)
        found_cp_tc = False
        for tc in tool_calls:
            if not actions.write_blocks and tc.name == "write_file":
                fn = tc.params.get("filename") or ""
                cnt = tc.params.get("content") or tc.body or ""
                if fn and cnt:
                    actions.write_blocks.append((fn, cnt))
            elif not patch_blocks and tc.name == "patch_file":
                fn = tc.params.get("filename") or ""
                body = tc.body or tc.params.get("diff") or ""
                if fn and body:
                    actions.patch_blocks.append((fn, body))
            elif tc.name == "replace_symbol":
                fn = tc.params.get("filename") or ""
                sym = tc.params.get("symbol_name") or tc.params.get("name") or ""
                code_body = tc.body or tc.params.get("code") or ""
                if fn and sym:
                    if fn not in file_backups:
                        orig_file = ws_dir / fn
                        if orig_file.exists() and orig_file.is_file():
                            try:
                                file_backups[fn] = orig_file.read_text(encoding="utf-8", errors="ignore")
                            except Exception as _e_bk:
                                logger.warning(f"SubagentEngineer: не удалось создать бэкап {fn}: {_e_bk}")
                    res = ToolEngine.replace_symbol(filename=fn, symbol_name=sym, new_code=code_body)
                    if not res.success:
                        actions.last_error = res.output
                        logger.warning(f"[SubagentEngineer] Сбой replace_symbol: {actions.last_error}")
                    else:
                        logger.info(f"[SubagentEngineer] {res.output}")
                        actions.replace_blocks.append(fn)
            elif tc.name == "git_checkpoint":
                found_cp_tc = True
                c_res = ToolEngine.execute_tool_call(tc)
                actions.git_checkpoint_results.append(c_res.output)

        if not found_cp_tc and "git_checkpoint" in response_text.lower():
            cp_pattern = r'<<TOOL:git_checkpoint(?:\s+(?P<params>[^>\r\n]+?))?>>?(?:(?P<body>.*?)(?:<<?/+TOOL(?::git_checkpoint)?>>?|`+/+TOOL(?::git_checkpoint)?`+|$))?'
            for m in re.finditer(cp_pattern, response_text, flags=re.DOTALL | re.IGNORECASE):
                params_str = (m.group("params") or "").strip()
                body_cnt = (m.group("body") or "").strip()
                from services.tool_parser import ToolCall, ToolParser
                p_dict = ToolParser._parse_attributes(params_str)
                if not p_dict and params_str:
                    p_dict["message"] = params_str.strip("'\"`")
                if body_cnt and "message" not in p_dict:
                    p_dict["message"] = body_cnt
                tc = ToolCall(name="git_checkpoint", params=p_dict, body=body_cnt)
                c_res = ToolEngine.execute_tool_call(tc)
                actions.git_checkpoint_results.append(c_res.output)

        if not actions.write_blocks and not patch_blocks and git_checkpoint_results:
            summary_out = "\n".join(git_checkpoint_results)
            logger.info(f"✅ [GitSandbox] Чекпоинт зафиксирован: {summary_out}")
            return {
                "success": True,
                "artifact_path": None,
                "file_name": "",
                "stdout": summary_out,
                "stderr": "",
                "summary": f"Чекпоинт успешно зафиксирован:\n{summary_out}",
                "artifact_format": self.artifact_format,
                "artifact_type": self.artifact_type,
                "target_artifact": target_artifact,
                "verification_script": self.verification_script,
                "verification_passed": False
            }

        # 1.5. Fallback-перехватчик точечной правки AST (replace_symbol) для файлов > 250 строк
        if not actions.write_blocks and not actions.patch_blocks and not actions.replace_blocks:
            cand_target = target_artifact or getattr(plan, "target_file", None) or getattr(plan, "target_artifact", None) or context_hint
            if not cand_target:
                m_pys = re.findall(r'\b([\w\-]+\.pyw?)\b', f"{task_prompt} {context_hint}")
                if m_pys:
                    cand_target = m_pys[0]

            if cand_target:
                cand_path = ws_dir / Path(cand_target).name
                if cand_path.is_file():
                    file_content = cand_path.read_text(encoding="utf-8", errors="ignore")
                    file_lines = file_content.splitlines()
                    if len(file_lines) > 250:
                        is_deleted = bool(re.search(r'\bDELETED\b', response_text))
                        if is_deleted:
                            target_sym = getattr(plan, "target_symbol", None)
                            if not target_sym:
                                try:
                                    tree_del = ast.parse(file_content)
                                    sym_names = [n.name for n in ast.walk(tree_del) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                                    for s_name in sym_names:
                                        if re.search(r'\b' + re.escape(s_name) + r'\b', task_prompt) or re.search(r'\b' + re.escape(s_name) + r'\b', response_text):
                                            target_sym = s_name
                                            break
                                except Exception:
                                    pass
                            if target_sym:
                                if cand_path.name not in file_backups:
                                    file_backups[cand_path.name] = file_content
                                res = ToolEngine.replace_symbol(filename=cand_path.name, symbol_name=target_sym, new_code="DELETED")
                                if res.success:
                                    logger.info(f"[SubagentEngineer] Fallback-перехватчик replace_symbol (DELETED): {res.output}")
                                    actions.replace_blocks.append(cand_path.name)
                                else:
                                    actions.last_error = res.output
                                    logger.warning(f"[SubagentEngineer] Fallback-перехватчик replace_symbol (DELETED) сбой: {actions.last_error}")
                        else:
                            m_blocks = re.findall(r'```(?:python|py)?\s*[\r\n]+([\s\S]*?)```', response_text, re.IGNORECASE)
                            code_body = m_blocks[0].strip() if m_blocks else ""
                            if not code_body:
                                code_body = EngineerContext.extract_pure_py(response_text, orig_content=file_content)
                            if code_body:
                                sym_name = None
                                import textwrap
                                try:
                                    tree_snip = ast.parse(textwrap.dedent(code_body))
                                    for n in tree_snip.body:
                                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                                            sym_name = n.name
                                            break
                                except Exception:
                                    pass
                                if not sym_name:
                                    m_s = re.search(r'^\s*(?:def|class)\s+([a-zA-Z_]\w*)', code_body, re.MULTILINE)
                                    if m_s:
                                        sym_name = m_s.group(1)
                                if not sym_name and getattr(plan, "target_symbol", None):
                                    sym_name = plan.target_symbol

                                if sym_name:
                                    try:
                                        tree_orig = ast.parse(file_content)
                                        existing_syms = {n.name for n in ast.walk(tree_orig) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
                                    except Exception:
                                        existing_syms = set()

                                    if sym_name in existing_syms:
                                        if cand_path.name not in file_backups:
                                            file_backups[cand_path.name] = file_content
                                        res = ToolEngine.replace_symbol(filename=cand_path.name, symbol_name=sym_name, new_code=code_body)
                                        if res.success:
                                            logger.info(f"[SubagentEngineer] Fallback-перехватчик replace_symbol (Markdown): {res.output}")
                                            actions.replace_blocks.append(cand_path.name)
                                        else:
                                            actions.last_error = res.output
                                            logger.warning(f"[SubagentEngineer] Fallback-перехватчик replace_symbol (Markdown) сбой: {actions.last_error}")

        # 2. Полный Fallback-парсер (если модель опустила теги инструментов)
        if not actions.write_blocks and not actions.patch_blocks and not actions.replace_blocks:
            def _has_py_indicators(code: str) -> bool:
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

            used_target_files = set()

            def _extract_hint_from_context(match_start: int, code_body: str) -> str:
                first_lines = code_body.splitlines()[:3]
                for fl in first_lines:
                    m_comm = re.search(r'#\s*(?:[a-zA-Z0-9_\-]+\s*[:=]\s*)?([a-zA-Z0-9_\-]+\.(?:pyw?|xlsx|docx|csv|json|txt))\b', fl, re.IGNORECASE)
                    if m_comm:
                        return m_comm.group(1).strip()
                start_ctx = max(0, match_start - 250)
                pre_text = response_text[start_ctx:match_start]
                m_pre = re.findall(r'\b([a-zA-Z0-9_\-]+\.(?:pyw?|xlsx|docx|csv|json|txt))\b', pre_text)
                return m_pre[-1].strip() if m_pre else ""

            def _resolve_target_fn(code_snippet: str, hint_fn: str = "", used_names: set = None) -> str:
                used = used_names if used_names is not None else set()
                if hint_fn:
                    clean_h = Path(hint_fn).name.strip().strip("'\"`")
                    if clean_h and clean_h not in used and Path(clean_h).suffix.lower() not in BINARY_EXTENSIONS:
                        return clean_h

                req_full = f"{task_prompt}\n{context_hint}\n{' '.join(planned_files)}"
                all_candidates = re.findall(r'\b([a-zA-Z0-9_\-]+\.pyw?)\b', req_full, re.IGNORECASE)
                unique_cands = [c for i, c in enumerate(all_candidates) if c not in all_candidates[:i]]
                available = [c for c in unique_cands if c not in used]

                if available:
                    viable = [
                        c for c in available
                        if not re.search(r'\b(?:from\s+' + re.escape(Path(c).stem) + r'\s+import|import\s+' + re.escape(Path(c).stem) + r')\b', code_snippet)
                    ]
                    target_pool = viable if viable else available
                    has_gui = any(k in code_snippet for k in ("tkinter", "PyQt", "pygame", "root", "mainloop"))
                    for cand in target_pool:
                        if has_gui and any(w in cand.lower() for w in ("app", "gui", "main", "ui")):
                            return cand
                        if not has_gui and any(w in cand.lower() for w in ("db", "model", "sqlite", "data")):
                            return cand
                    return target_pool[0]

                if script_to_run and script_to_run.lower().endswith((".py", ".pyw")) and script_to_run not in used:
                    return script_to_run

                base = unique_cands[0] if unique_cands else "script.py"
                if base in used:
                    stem, ext = Path(base).stem, Path(base).suffix or ".py"
                    idx = 2
                    while f"{stem}_{idx}{ext}" in used:
                        idx += 1
                    return f"{stem}_{idx}{ext}"
                return base

            has_named_blocks = bool(re.search(r'```[a-zA-Z0-9_\-]*\s+(?:filename|file)=', response_text, re.IGNORECASE))
            if has_named_blocks:
                md_pattern = r'```(?:(?P<lang>[a-zA-Z0-9_\-]+))?(?:\s+(?:filename|file)=["\']?(?P<fn>[^\s"\'>]+)["\']?|:(?P<fn2>[^\s\r\n]+))?\s*[\r\n]+(?P<code>[\s\S]*?)(?:```|$)'
                for m in re.finditer(md_pattern, response_text, re.IGNORECASE):
                    lang = (m.group("lang") or "").lower()
                    fn_hint = (m.group("fn") or m.group("fn2") or "").strip()
                    c_body = m.group("code").strip()
                    is_code_block = bool(c_body) and ((bool(fn_hint) and lang in ("python", "py", "python3")) or _has_py_indicators(c_body))
                    if is_code_block:
                        if not fn_hint:
                            fn_hint = _extract_hint_from_context(m.start(), c_body)
                        target_fn = _resolve_target_fn(c_body, fn_hint, used_target_files)
                        used_target_files.add(target_fn)
                        actions.write_blocks.append((target_fn, c_body))
                        logger.info(f"[SubagentEngineer] Перехвачен код из Markdown для {target_fn}")

            if not actions.write_blocks:
                orig_cnt = None
                if is_maintenance:
                    target_cand = target_entry_file or (list(file_backups.keys())[0] if len(file_backups) == 1 else None)
                    if target_cand:
                        orig_cnt = file_backups.get(target_cand)
                        if orig_cnt is None:
                            orig_p = ws_dir / target_cand
                            if orig_p.is_file():
                                try:
                                    orig_cnt = orig_p.read_text(encoding="utf-8", errors="ignore")
                                except Exception:
                                    pass
                code = EngineerContext.extract_pure_py(response_text, orig_content=orig_cnt)
                if code:
                    target_fn = _resolve_target_fn(code, used_names=used_target_files)
                    used_target_files.add(target_fn)
                    actions.write_blocks.append((target_fn, code))
                    logger.info(f"[SubagentEngineer] Перехвачен чистый Python-код для {target_fn}")

        clean_blocks = []
        for fn, cnt in actions.write_blocks:
            cnt_clean = re.sub(r'<<?/?(?:TOOL|tool)(?::\w+)?(?:\s+[^>\r\n]*?)?>>?', '', cnt)
            cnt_clean = re.sub(r'^```[a-zA-Z0-9_\-]*\s*', '', cnt_clean)
            cnt_clean = re.sub(r'\s*```$', '', cnt_clean).strip()
            ext = Path(fn).suffix.lower()
            if ext in BINARY_EXTENSIONS:
                if EngineerContext.has_python_code(cnt_clean):
                    fn = "make_doc.py"
                    logger.info(f"[SubagentEngineer] Self-Healing Router: код из бинарного блока {ext} перенаправлен в make_doc.py")
                else:
                    continue
            if fn.lower().endswith((".py", ".pyw")):
                orig_cnt = file_backups.get(fn)
                if orig_cnt is None:
                    orig_p = ws_dir / fn
                    if orig_p.exists() and orig_p.is_file():
                        try:
                            orig_cnt = orig_p.read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            orig_cnt = None
                cnt_clean = EngineerContext.extract_pure_py(cnt_clean, orig_content=orig_cnt)
                cnt_clean = CodeValidator.apply_ast_linter_to_code(cnt_clean)
            if fn and cnt_clean:
                clean_blocks.append((fn, cnt_clean))

        # Verification Freeze
        allow_turn2_revision = (turn == 2 and turn1_test_signature_mismatch)
        should_freeze = (turn >= 3) or (turn == 2 and not allow_turn2_revision)

        if should_freeze and self.verification_script and (ws_dir / Path(self.verification_script).name).is_file():
            vs_name = Path(self.verification_script).name.lower()
            c_before = len(clean_blocks)
            p_before = len(actions.patch_blocks)
            clean_blocks = [(fn, cnt) for fn, cnt in clean_blocks if Path(fn).name.lower() != vs_name]
            patch_blocks = [(fn, body) for fn, body in patch_blocks if Path(fn).name.lower() != vs_name]
            if len(clean_blocks) < c_before or len(patch_blocks) < p_before:
                logger.warning(f"🛡️ [Verification Freeze] Попытка модификации проверочного скрипта '{self.verification_script}' заблокирована на ходе {turn}.")
        elif allow_turn2_revision and self.verification_script:
            vs_name = Path(self.verification_script).name.lower()
            revised = any(Path(fn).name.lower() == vs_name for fn, _ in clean_blocks) or any(Path(fn).name.lower() == vs_name for fn, _ in patch_blocks)
            if revised:
                logger.info(f"🔄 [Verification Freeze] Разрешена однократная ревизия проверочного скрипта '{self.verification_script}' на ходе 2.")

        write_blocks = clean_blocks
        actions.write_blocks = clean_blocks
        return actions
