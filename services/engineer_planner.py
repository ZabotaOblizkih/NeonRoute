"""
services/engineer_planner.py
Architect Phase and Planning Logic for NeonRoute Engineer Sub-Agent.
"""

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

from services.regex_filter import TextRegexFilter
from services.sandbox_manager import SandboxManager
from services.git_sandbox import GitSandboxManager
from services.engineer_context import EngineerContext
from services.engineer_skill_router import EngineerSkillRouter
from services.engineer_prompts import (
    scan_installed_libraries,
    get_architect_prompt,
    get_engineer_system_prompt,
)

logger = logging.getLogger("NeonRoute.EngineerPlanner")


@dataclass
class ArchitectPlan:
    artifact_format: str = "cli"
    artifact_type: str = "cli_tool"
    target_artifact: str = "main.py"
    verification_script: Optional[str] = None
    target_entry_file: str = "main.py"
    plan_desc: str = ""
    is_maintenance: bool = False
    is_ephemeral: bool = False
    plan_content: str = ""
    system_prompt: str = ""
    planned_files: List[str] = field(default_factory=list)
    intent: str = "new"
    target_symbol: Optional[str] = None
    is_ast_modification: bool = False
    task_type: str = "NEW_MODULE"
    target_file: str = "main.py"
    interface_spec: Any = field(default_factory=dict)
    skill_id: str = "pure_logic_tdd"


class EngineerPlanner:
    """Планировщик архитектуры и контрактов инженерных задач."""

    @staticmethod
    def plan_architecture(
        task_prompt: str,
        context_hint: str = "",
        ws_dir: Optional[Path] = None,
        client: Optional[Any] = None,
        target_model: Optional[str] = None,
        session_history: Optional[List[Dict[str, str]]] = None,
        is_analysis: bool = False,
        context_length: int = 28672,
        gpu_ratio: str = "1.0",
        available_libs: Optional[List[str]] = None,
        temperature: float = 0.70,
        max_tokens: int = 1800,
        session_metadata: Optional[Dict[str, Any]] = None,
        active_engineer_skill: Optional[str] = None,
        enable_thinking: Optional[bool] = None,
        thought_chunk_callback: Optional[Any] = None,
        **kwargs: Any,
    ) -> ArchitectPlan:
        if enable_thinking is None and "enable_thinking" in kwargs:
            enable_thinking = kwargs.pop("enable_thinking")
        if thought_chunk_callback is None and "thought_chunk_callback" in kwargs:
            thought_chunk_callback = kwargs.pop("thought_chunk_callback")
        eff_max_tokens = max(int(max_tokens or 1800), 4096) if enable_thinking is True else max_tokens
        if ws_dir is None:
            ws_dir = SandboxManager.get_workspace_dir()

        # Для режима анализа вызов Архитектора пропускается
        if is_analysis:
            sys_prompt = get_engineer_system_prompt(artifact_format="cli", is_analysis=True)
            return ArchitectPlan(
                artifact_format="cli",
                artifact_type="cli_tool",
                target_artifact="main.py",
                verification_script=None,
                target_entry_file="main.py",
                plan_desc="",
                is_maintenance=False,
                is_ephemeral=False,
                plan_content="",
                system_prompt=sys_prompt,
                planned_files=[],
                task_type="INCREMENTAL_FEATURE",
                target_file="main.py",
                interface_spec={}
            )

        # 1. Фиксация baseline и учет памяти прошлых сбоев
        fail_context = ""
        last_failure = GitSandboxManager.pop_last_failure(ws_dir)
        if last_failure:
            fail_context = (
                f"\n\n[ПАМЯТЬ О ПРЕДЫДУЩЕМ СБОЕ В ЭТОМ ПРОЕКТЕ]:\n"
                f"В прошлый раз на ходе {last_failure.get('failed_turn')} задача упала с ошибкой: {last_failure.get('error')}.\n"
                f"Рекомендация: {last_failure.get('recipe', 'не повторяй эту ошибку')}."
            )

        data_exts = {".py", ".json", ".csv", ".xlsx", ".docx", ".txt", ".db", ".sqlite"}
        ws_files = [f.name for f in ws_dir.iterdir() if f.is_file() and f.suffix.lower() in data_exts] if ws_dir.exists() else []

        if available_libs is None:
            available_libs = scan_installed_libraries()

        # 1. Определение активного скилла (с приоритетом ручного выбора пользователя)
        manual_skill = active_engineer_skill or kwargs.get("active_engineer_skill")
        if not manual_skill and (session_metadata or kwargs.get("session_metadata")):
            s_meta = session_metadata or kwargs.get("session_metadata")
            if isinstance(s_meta, dict):
                manual_skill = s_meta.get("active_engineer_skill")
        if not manual_skill:
            try:
                active_sid = SandboxManager.get_active_session_id()
                if active_sid:
                    from services.session_manager import SessionManager
                    cur_s = SessionManager.load_session(active_sid)
                    if cur_s and isinstance(cur_s.get("metadata"), dict):
                        manual_skill = cur_s["metadata"].get("active_engineer_skill")
            except Exception:
                pass

        router = EngineerSkillRouter.get_instance()

        is_manual_skill = bool(manual_skill and manual_skill != "auto" and manual_skill in router.skills)
        if is_manual_skill:
            skill_id = manual_skill
            score = 1.0
            skill_meta = router.skills[skill_id]
            logger.info(f"📐 [Архитектор] Использован ПРИНУДИТЕЛЬНЫЙ скилл из сессии: '{skill_id}'")
        else:
            skill_id, score, skill_meta = router.route(task_prompt)
            logger.info(f"📐 [Архитектор] Векторно активирован скилл: '{skill_id}' (уверенность: {score:.3f})")

        # 2. Формирование промпта с инъекцией правил скилла
        ws_file_names = list(ws_files.keys()) if isinstance(ws_files, dict) else list(ws_files)
        architect_sys_prompt = get_architect_prompt(
            available_libs=available_libs,
            ws_files=ws_file_names,
            injected_architect_rules=skill_meta.get("architect_rules", ""),
            default_artifact=skill_meta.get("default_artifact", "app.py")
        )

        arch_messages = [
            {"role": "system", "content": architect_sys_prompt},
            {"role": "user", "content": f"Спроектируй решение для задачи:\n«{task_prompt}»\n{context_hint or ''}"}
        ]
        if session_history:
            hist_text = "\n[Недавний контекст диалога]:\n" + "\n".join(
                f"{'Пользователь' if m.get('role') == 'user' else 'Инженер'}: {m.get('content')}"
                for m in session_history[-3:]
            )
            arch_messages[1]["content"] += f"\n{hist_text}"

        # Инъекция компактного AST-скелета существующих .py файлов для Архитектора (чтобы исключить проектирование вслепую)
        py_candidates: List[Path] = []
        if context_hint:
            hint_p = ws_dir / Path(context_hint).name.strip()
            if hint_p.is_file() and hint_p.suffix.lower() in (".py", ".pyw"):
                py_candidates.append(hint_p)
        if not py_candidates and ws_dir and ws_dir.exists():
            py_candidates = [
                ws_dir / fn for fn in ws_file_names
                if fn.lower().endswith((".py", ".pyw")) and (ws_dir / fn).is_file()
            ][:2]
        if py_candidates:
            from services.code_validator import CodeValidator
            skel_blocks = []
            for py_p in py_candidates:
                try:
                    raw_py = py_p.read_text(encoding="utf-8", errors="ignore")
                    skel_str = CodeValidator.generate_ast_skeleton(raw_py)
                    if skel_str:
                        skel_blocks.append(f"[СУЩЕСТВУЮЩАЯ СТРУКТУРА ФАЙЛА {py_p.name}]:\n```python\n{skel_str}\n```")
                except Exception:
                    pass
            if skel_blocks:
                arch_messages[1]["content"] += "\n\n" + "\n\n".join(skel_blocks)

        parsed_format = "cli"
        intent = "new"
        target_artifact = "main.py"
        verification_script = None
        plan_desc = ""

        logger.info("📐 [Архитектор] Запуск проектирования архитектуры...")
        use_stream = bool(thought_chunk_callback and enable_thinking is not False)
        call_kwargs: Dict[str, Any] = {
            "messages": arch_messages,
            "model": target_model,
            "temperature": temperature,
            "max_tokens": eff_max_tokens,
            "context_length": context_length,
            "gpu_ratio": gpu_ratio,
            "stream": use_stream,
            "emit_signal": False
        }
        if use_stream:
            call_kwargs["chunk_callback"] = thought_chunk_callback
        if enable_thinking is not None:
            call_kwargs["enable_thinking"] = enable_thinking
        if enable_thinking is True:
            call_kwargs["respect_master_cap"] = False

        try:
            try:
                arch_resp = client.chat_completion(**call_kwargs)
            except TypeError:
                call_kwargs_min = dict(call_kwargs)
                call_kwargs_min.pop("context_length", None)
                call_kwargs_min.pop("gpu_ratio", None)
                call_kwargs_min.pop("respect_master_cap", None)
                try:
                    arch_resp = client.chat_completion(**call_kwargs_min)
                except TypeError:
                    arch_resp = client.chat_completion(
                        messages=arch_messages,
                        model=target_model,
                        temperature=temperature,
                        max_tokens=eff_max_tokens,
                        stream=False,
                        emit_signal=False
                    )

            clean_arch_resp = TextRegexFilter.strip_thought_blocks(str(arch_resp or ""))
            m_json = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', clean_arch_resp)
            raw_json = m_json.group(1) if m_json else clean_arch_resp
            if not m_json:
                m_bare = re.search(r'(\{[\s\S]*\})', clean_arch_resp)
                if m_bare:
                    raw_json = m_bare.group(1)
            data = json.loads(raw_json.strip())

            raw_vs = data.get("verification_script")
            if raw_vs and str(raw_vs).strip().lower() not in ("none", "null", ""):
                verification_script = str(raw_vs).strip()
            else:
                verification_script = None

            raw_task_type = str(data.get("task_type") or "").strip().upper()
            interface_spec = data.get("interface_spec") or {}
            if isinstance(interface_spec, dict):
                classes_list = interface_spec.get("classes")
                if isinstance(classes_list, list):
                    for cls_item in classes_list:
                        if isinstance(cls_item, dict) and isinstance(cls_item.get("methods"), list):
                            seen_m_names = set()
                            deduped_methods = []
                            for m in cls_item["methods"]:
                                if isinstance(m, dict):
                                    m_name = m.get("name")
                                    if m_name and m_name not in seen_m_names:
                                        seen_m_names.add(m_name)
                                        deduped_methods.append(m)
                            cls_item["methods"] = deduped_methods

                funcs_list = interface_spec.get("functions")
                if isinstance(funcs_list, list):
                    seen_fn_names = set()
                    cleaned_funcs = []
                    for fn in funcs_list:
                        if not isinstance(fn, dict):
                            continue
                        fn_name = fn.get("name")
                        if not fn_name or fn_name in seen_fn_names:
                            continue
                        seen_fn_names.add(fn_name)

                        fn_args = fn.get("args")
                        if isinstance(fn_args, list):
                            fn["args"] = [a for a in fn_args if a != "self"]
                        cleaned_funcs.append(fn)

                    interface_spec["functions"] = cleaned_funcs

            raw_format = str(data.get("format") or data.get("artifact_type") or "").strip().lower()

            parsed_format = EngineerContext.normalize_artifact_format(
                raw_format,
                f"{task_prompt} {context_hint}",
                has_verification_script=bool(verification_script),
                skill_id=skill_id
            )

            # Выставляем архетип с учетом реального формата артефакта, а не поверхностной метки "module"
            if is_manual_skill:
                if parsed_format in ("pygame", "tkinter"):
                    skill_id = "interactive_loop"
                elif parsed_format in ("excel", "word"):
                    skill_id = "batch_artifact"
                # Запрещено перезаписывать принудительный скилл на cli_automation или pure_logic_tdd
            else:
                if parsed_format in ("pygame", "tkinter") or raw_format == "interactive_loop":
                    skill_id = "interactive_loop"
                elif parsed_format in ("excel", "word"):
                    skill_id = "batch_artifact"
                elif parsed_format == "cli":
                    skill_id = "cli_automation"
                elif parsed_format == "module":
                    skill_id = "pure_logic_tdd"
                elif raw_format in ("pure_logic_tdd", "interactive_loop", "batch_artifact", "service_backend", "cli_automation"):
                    skill_id = raw_format
                else:
                    skill_id = "pure_logic_tdd"
            intent = str(data.get("intent") or "new").strip().lower()
            if raw_task_type == "INCREMENTAL_FEATURE":
                intent = "maintenance"
            elif raw_task_type == "NEW_MODULE":
                intent = "new"
            target_artifact = data.get("target_file") or data.get("target_artifact")
            if not target_artifact:
                m_py = re.findall(r'[\w\-]+\.py', task_prompt)
                if m_py:
                    target_artifact = m_py[0]
                elif parsed_format in ("excel", "word"):
                    target_artifact = "make_doc.py"
                elif parsed_format == "pygame":
                    target_artifact = "game.py"
                elif parsed_format == "tkinter":
                    target_artifact = "app.py"
                else:
                    target_artifact = "main.py"
            plan_desc = data.get("plan", "")
        except Exception as _e_arch:
            raw_task_type = ""
            interface_spec = {}
            logger.warning(f"📐 [Архитектор] Сбой фазы планирования, переход к эвристикам: {_e_arch}")
            m_py = re.findall(r'[\w\-]+\.py', task_prompt)
            extracted_target = m_py[0] if m_py else None
            extracted_vs = m_py[1] if len(m_py) > 1 else None
            has_vs = bool(extracted_vs)
            parsed_format = EngineerContext.normalize_artifact_format(
                "",
                f"{task_prompt} {context_hint}",
                has_verification_script=has_vs,
                skill_id=skill_id
            )
            intent = "maintenance" if (ws_files and any(k in task_prompt.lower() for k in ("добавь", "исправь", "почини", "почему", "не работает", "чтобы"))) else "new"
            if parsed_format in ("excel", "word"):
                target_artifact = "make_doc.py"
            elif parsed_format == "pygame":
                target_artifact = extracted_target or "game.py"
            elif parsed_format == "tkinter":
                target_artifact = extracted_target or "app.py"
            else:
                target_artifact = extracted_target or (ws_files[0] if (intent == "maintenance" and ws_files) else "main.py")
            verification_script = extracted_vs if parsed_format == "module" else None

        artifact_format = parsed_format
        if artifact_format in ("excel", "word", "pygame", "tkinter", "cli"):
            verification_script = None

        is_ephemeral = (artifact_format in ("excel", "word"))
        if is_ephemeral:
            target_artifact = "make_doc.py"

        TYPE_MAPPING = {
            "excel": "document",
            "word": "document",
            "pygame": "gui_app",
            "tkinter": "gui_app",
            "cli": "cli_tool",
            "module": "module"
        }
        artifact_type = TYPE_MAPPING.get(artifact_format, artifact_format)

        if context_hint:
            hint_name = Path(context_hint).name.strip()
            if hint_name.endswith((".py", ".pyw", ".xlsx", ".docx")) and (ws_dir / hint_name).is_file():
                target_artifact = hint_name
                is_maintenance = True
            else:
                is_maintenance = (intent == "maintenance" and bool(ws_files))
        else:
            is_maintenance = (intent == "maintenance" and bool(ws_files))

        target_entry_file = verification_script or target_artifact
        system_prompt = get_engineer_system_prompt(
            artifact_format=artifact_format,
            is_analysis=is_analysis,
            injected_worker_rules=router.skills.get(skill_id, {}).get("worker_rules", "")
        )

        if intent == "review":
            is_maintenance = False
            intent_val = "review"
            task_type = "INCREMENTAL_FEATURE"
        elif is_maintenance or raw_task_type == "INCREMENTAL_FEATURE":
            is_maintenance = True
            intent_val = "maintenance"
            task_type = "INCREMENTAL_FEATURE"
        else:
            intent_val = "new"
            task_type = "NEW_MODULE"
        target_file = target_artifact
        if context_hint and target_artifact == Path(context_hint).name.strip():
            logger.info(f"🔒 [Архитектор] Принудительно зафиксирован целевой файл доработки из контекста: {target_artifact}")

        logger.info(
            f"📐 [Архитектор] Результат: artifact_format={artifact_format}, intent={intent_val}, "
            f"target_artifact={target_artifact}, verification_script={verification_script}, "
            f"is_maintenance={is_maintenance}, is_ephemeral={is_ephemeral}"
        )
        if plan_desc:
            logger.info(f"📐 [Архитектор: Утверждённый план]: {plan_desc}")

        file_context = EngineerContext.inspect_workspace_file(
            task_prompt=task_prompt,
            context_hint=context_hint,
            is_new_intent=(not is_maintenance),
            target_file=target_entry_file if is_maintenance else None
        )

        planned_files: List[str] = []
        if target_artifact and target_artifact not in planned_files:
            planned_files.append(target_artifact)
        if verification_script and verification_script not in planned_files:
            planned_files.append(verification_script)

        plan_header = f"### УТВЕРЖДЁННЫЙ АРХИТЕКТУРНЫЙ ПЛАН (Формат: {artifact_format}, Intent: {intent_val}, Целевой артефакт: {target_artifact}"
        if verification_script:
            plan_header += f", Верификатор: {verification_script}"
        plan_header += f"):\n{plan_desc}"
        plan_content = f"\n\n{plan_header}"
        if interface_spec:
            spec_str = json.dumps(interface_spec, ensure_ascii=False, indent=2) if isinstance(interface_spec, (dict, list)) else str(interface_spec)
            plan_content += f"\n\nСпецификация интерфейса (interface_spec):\n```json\n{spec_str}\n```"

        if fail_context:
            plan_content = f"{fail_context}\n\n{plan_content}"

        run_instruction = (
            f"запусти скрипт верификации через <<TOOL:run_script filename=\"{verification_script}\">>."
            if verification_script else
            f"запусти главную точку входа через <<TOOL:run_script filename=\"{target_artifact}\">>."
        )

        target_file_path = (ws_dir / target_artifact) if ws_dir and target_artifact else None
        target_lines_count = 0
        if target_file_path and target_file_path.is_file():
            try:
                target_lines_count = len(target_file_path.read_text(encoding="utf-8", errors="ignore").splitlines())
            except Exception:
                target_lines_count = 0

        target_symbol: Optional[str] = None
        is_ast_modification: bool = False

        if is_maintenance and target_lines_count > 250 and target_file_path and target_file_path.is_file():
            # Находим целевой узел в AST
            try:
                raw_code = target_file_path.read_text(encoding="utf-8", errors="ignore")
                raw_lines = raw_code.splitlines(keepends=True)
                file_ast = ast.parse(raw_code)

                # Построение карты родителей
                parent_map = {}
                for parent in ast.walk(file_ast):
                    for child in ast.iter_child_nodes(parent):
                        parent_map[child] = parent

                # Поиск наиболее релевантного класса или функции по запросу
                matched_node = None
                p_lower = (task_prompt or "").lower()
                p_clean = re.sub(r'\b[\w\-]+\.pyw?\b', '', p_lower)
                candidates = []
                for node in ast.walk(file_ast):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if re.search(r'\b' + re.escape(node.name.lower()) + r'\b', p_clean):
                            type_weight = 2 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else 1
                            candidates.append((type_weight, len(node.name), node))
                        else:
                            doc = (ast.get_docstring(node) or "").lower()
                            if doc and any(re.search(r'\b' + re.escape(w) + r'\b', doc) for w in p_clean.split() if len(w) > 4):
                                type_weight = 2 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else 1
                                candidates.append((type_weight, 0, node))

                if candidates:
                    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
                    matched_node = candidates[0][2]

                if matched_node:
                    is_ast_modification = True
                    target_symbol = matched_node.name

                    # --- УРОВЕНЬ 1 (Импорты модуля) ---
                    import_snippets = []
                    for node in file_ast.body:
                        if isinstance(node, (ast.Import, ast.ImportFrom)):
                            s_line = node.lineno - 1
                            e_line = getattr(node, "end_lineno", node.lineno)
                            import_snippets.append("".join(raw_lines[s_line:e_line]))
                    imports_block = "".join(import_snippets).strip()
                    if not imports_block:
                        imports_block = "# (Импорты не обнаружены в начале модуля)"

                    # --- УРОВЕНЬ 2 (Каркас класса) ---
                    parent_node = parent_map.get(matched_node)
                    class_skeleton_lines = []
                    target_class = parent_node if isinstance(parent_node, ast.ClassDef) else (matched_node if isinstance(matched_node, ast.ClassDef) else None)
                    if target_class:
                        class_skeleton_lines.append(f"class {target_class.name}:")
                        for item in target_class.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                is_async = "async " if isinstance(item, ast.AsyncFunctionDef) else ""
                                try:
                                    params_str = ast.unparse(item.args)
                                    ret_ann = f" -> {ast.unparse(item.returns)}" if item.returns else ""
                                    class_skeleton_lines.append(f"    {is_async}def {item.name}({params_str}){ret_ann}: ...")
                                except Exception:
                                    class_skeleton_lines.append(f"    {is_async}def {item.name}(...): ...")
                    else:
                        class_skeleton_lines.append("# (Целевой узел находится на уровне модуля, родительский класс отсутствует)")
                    class_skeleton = "\n".join(class_skeleton_lines)

                    # --- УРОВЕНЬ 3 (Срез узла с окружением ±10 строк) ---
                    s_idx = matched_node.lineno - 1
                    e_idx = getattr(matched_node, "end_lineno", s_idx + 1)
                    node_code = "".join(raw_lines[s_idx:e_idx])
                    ctx_before = "".join(raw_lines[max(0, s_idx - 10) : s_idx])
                    ctx_after = "".join(raw_lines[e_idx : min(len(raw_lines), e_idx + 10)])

                    plan_content += (
                        f"\n\nПлан утверждён (Крупный файл {target_artifact}: {target_lines_count} строк > 250 строк).\n"
                        f"РЕГЛАМЕНТ ТОЧЕЧНОЙ МОДИФИКАЦИИ УЗЛА '{matched_node.name}':\n\n"
                        f"--- УРОВЕНЬ 1 (Импорты модуля) ---\n```python\n{imports_block}\n```\n\n"
                        f"--- УРОВЕНЬ 2 (Каркас класса) ---\n```python\n{class_skeleton}\n```\n\n"
                        f"--- УРОВЕНЬ 3 (Срез узла с окружением ±10 строк) ---\n"
                        f"[ОКРУЖЕНИЕ ДО УЗЛА]:\n```python\n{ctx_before}```\n\n"
                        f"[ЦЕЛЕВОЙ УЗЕЛ НА ДИСКЕ (строки {matched_node.lineno}-{e_idx})]:\n```python\n{node_code}```\n\n"
                        f"[ОКРУЖЕНИЕ ПОСЛЕ УЗЛА]:\n```python\n{ctx_after}```\n\n"
                        f"ВНИМАНИЕ: Полная перезапись файла запрещена!\n"
                        f"1. ФАЗА 1 (ДИАГНОЗ): 1–2 строки текстового анализа ошибки.\n"
                        f"2. ФАЗА 2 (КОД): вызов инструмента точечной замены:\n"
                        f"   <<TOOL:replace_symbol filename=\"{target_artifact}\" name=\"{matched_node.name}\">>\n"
                        f"   новый_код_узла_с_отступами (или DELETED для удаления)\n"
                        f"   <</TOOL:replace_symbol>>\n"
                        f"3. {run_instruction}"
                    )
                else:
                    if file_context:
                        plan_content += f"\n\nТекущий код файлов для доработки:\n{file_context}"
                    plan_content += (
                        f"\n\nПлан утверждён (Режим: Крупный файл, {target_lines_count} строк > порога 250 строк).\n"
                        f"ВНИМАНИЕ: Полная перезапись файла запрещена! "
                        f"Используй <<TOOL:replace_symbol filename=\"{target_artifact}\" name=\"<имя_узла>\">> для точечной правки.\n"
                        f"{run_instruction}"
                    )
            except Exception as e:
                logger.warning(f"Ошибка AST-локализации узла в планировщике: {e}")
                if file_context:
                    plan_content += f"\n\nТекущий код файлов для доработки:\n{file_context}"
                plan_content += f"\n\nПлан утверждён. {run_instruction}"
        elif is_maintenance:
            if file_context:
                plan_content += f"\n\nТекущий код файлов для доработки:\n{file_context}"
            plan_content += (
                f"\n\nПлан утверждён. Приступай к двухфазной доработке файла {target_artifact} ({target_lines_count} строк <= 250 строк):\n"
                f"1. ФАЗА 1: Краткий текстовый диагноз (в какой функции баг / что конкретно меняется).\n"
                f"2. ФАЗА 2: Полная сборка монолитного файла через <<TOOL:write_file filename=\"{target_artifact}\">> (без заглушек и без номеров строк).\n"
                f"3. {run_instruction}"
            )
        else:
            plan_content += (
                "\n\nПлан утверждён. Приступай к реализации (Фаза 2: Инженер). "
                "Создай все спроектированные файлы строго согласно плану через отдельные блоки <<TOOL:write_file filename=\"...\">> "
                f"и {run_instruction}"
            )

        plan = ArchitectPlan(
            artifact_format=artifact_format,
            artifact_type=artifact_type,
            target_artifact=target_artifact,
            verification_script=verification_script,
            target_entry_file=target_entry_file,
            plan_desc=plan_desc,
            is_maintenance=is_maintenance,
            is_ephemeral=is_ephemeral,
            plan_content=plan_content,
            system_prompt=system_prompt,
            planned_files=planned_files,
            intent=intent_val,
            target_symbol=target_symbol,
            is_ast_modification=is_ast_modification,
            task_type=task_type,
            target_file=target_file,
            interface_spec=interface_spec,
            skill_id=skill_id
        )
        plan.skill_id = skill_id

        # 4. IntentOverrideGuard: защита от ложного maintenance на чистом каталоге
        target_name = getattr(plan, "target_artifact", None) or getattr(plan, "target_file", None)
        if target_name and ws_dir:
            target_path = ws_dir / target_name
            if not target_path.is_file() or target_path.stat().st_size == 0:
                if getattr(plan, "intent", None) != "new" or getattr(plan, "task_type", None) != "NEW_MODULE":
                    logger.warning(f"🛡️ [IntentOverrideGuard] Файл '{target_name}' отсутствует. Оверрайд intent -> 'new', task_type -> 'NEW_MODULE'")
                    plan.intent = "new"
                    plan.is_maintenance = False
                    plan.task_type = "NEW_MODULE"

        return plan 


__all__ = ["ArchitectPlan", "EngineerPlanner"]
