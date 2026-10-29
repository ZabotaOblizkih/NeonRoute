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

from services.sandbox_manager import SandboxManager
from services.git_sandbox import GitSandboxManager
from services.engineer_context import EngineerContext
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
        max_tokens: int = 1200,
    ) -> ArchitectPlan:
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
                planned_files=[]
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
        architect_sys_prompt = get_architect_prompt(available_libs=available_libs, ws_files=ws_files)

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

        parsed_format = "cli"
        intent = "new"
        target_artifact = "main.py"
        verification_script = None
        plan_desc = ""

        logger.info("📐 [Архитектор] Запуск проектирования архитектуры...")
        try:
            try:
                arch_resp = client.chat_completion(
                    messages=arch_messages,
                    model=target_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    context_length=context_length,
                    gpu_ratio=gpu_ratio,
                    stream=False,
                    emit_signal=False
                )
            except TypeError:
                arch_resp = client.chat_completion(
                    messages=arch_messages,
                    model=target_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                    emit_signal=False
                )

            m_json = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', str(arch_resp or ""))
            raw_json = m_json.group(1) if m_json else str(arch_resp or "")
            if not m_json:
                m_bare = re.search(r'(\{[\s\S]*\})', str(arch_resp or ""))
                if m_bare:
                    raw_json = m_bare.group(1)
            data = json.loads(raw_json.strip())

            raw_vs = data.get("verification_script")
            if raw_vs and str(raw_vs).strip().lower() not in ("none", "null", ""):
                verification_script = str(raw_vs).strip()
            else:
                verification_script = None

            raw_format = str(data.get("format") or data.get("artifact_type") or "").strip()
            parsed_format = EngineerContext.normalize_artifact_format(
                raw_format,
                f"{task_prompt} {context_hint}",
                has_verification_script=bool(verification_script)
            )
            intent = str(data.get("intent") or "new").strip().lower()
            target_artifact = data.get("target_artifact") or data.get("target_file")
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
            logger.warning(f"📐 [Архитектор] Сбой фазы планирования, переход к эвристикам: {_e_arch}")
            m_py = re.findall(r'[\w\-]+\.py', task_prompt)
            extracted_target = m_py[0] if m_py else None
            extracted_vs = m_py[1] if len(m_py) > 1 else None
            has_vs = bool(extracted_vs)
            parsed_format = EngineerContext.normalize_artifact_format(
                "",
                f"{task_prompt} {context_hint}",
                has_verification_script=has_vs
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
        system_prompt = get_engineer_system_prompt(artifact_format=artifact_format, is_analysis=is_analysis)

        if intent == "review":
            is_maintenance = False
            intent_val = "review"
        elif is_maintenance:
            intent_val = "maintenance"
        else:
            intent_val = "new"
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

        if fail_context:
            plan_content = f"{fail_context}\n\n{plan_content}"
        if file_context and is_maintenance:
            plan_content += f"\n\nТекущий код файлов для доработки:\n{file_context}"

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

        if is_maintenance and target_lines_count > 250 and target_file_path and target_file_path.is_file():
            # Находим целевой узел в AST
            try:
                raw_code = target_file_path.read_text(encoding="utf-8", errors="ignore")
                raw_lines = raw_code.splitlines(keepends=True)
                file_ast = ast.parse(raw_code)

                # Поиск наиболее релевантного класса или функции по запросу
                matched_node = None
                p_lower = (task_prompt or "").lower()
                for node in ast.walk(file_ast):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if node.name.lower() in p_lower:
                            matched_node = node
                            break
                        doc = (ast.get_docstring(node) or "").lower()
                        if any(w in doc for w in p_lower.split() if len(w) > 4):
                            matched_node = node
                            break

                if matched_node:
                    s_idx = matched_node.lineno - 1
                    e_idx = getattr(matched_node, "end_lineno", s_idx + 1)
                    node_code = "".join(raw_lines[s_idx:e_idx])
                    ctx_before = "".join(raw_lines[max(0, s_idx - 10) : s_idx])
                    ctx_after = "".join(raw_lines[e_idx : min(len(raw_lines), e_idx + 10)])

                    plan_content += (
                        f"\n\nПлан утверждён (Крупный файл {target_artifact}: {target_lines_count} строк > 250 строк).\n"
                        f"РЕГЛАМЕНТ ТОЧЕЧНОЙ МОДИФИКАЦИИ УЗЛА '{matched_node.name}':\n"
                        f"[ОКРУЖЕНИЕ ДО УЗЛА]:\n```python\n{ctx_before}```\n\n"
                        f"[ЦЕЛЕВОЙ УЗЕЛ НА ДИСКЕ (строки {matched_node.lineno}-{e_idx})]:\n```python\n{node_code}```\n\n"
                        f"[ОКРУЖЕНИЕ ПОСЛЕ УЗЛА]:\n```python\n{ctx_after}```\n\n"
                        f"ВНИМАНИЕ: Полная перезапись файла запрещена!\n"
                        f"1. ФАЗА 1 (ДИАГНОЗ): Напиши 1-2 строки: подтверди изменение/удаление узла '{matched_node.name}'.\n"
                        f"2. ФАЗА 2 (КОД): Верни блок точечной замены:\n"
                        f"   <<TOOL:replace_symbol filename=\"{target_artifact}\" name=\"{matched_node.name}\">>\n"
                        f"   новый_код_узла_с_отступами (или DELETED для удаления)\n"
                        f"   <</TOOL:replace_symbol>>\n"
                        f"3. {run_instruction}"
                    )
                else:
                    plan_content += (
                        f"\n\nПлан утверждён (Режим: Крупный файл, {target_lines_count} строк > порога 250 строк).\n"
                        f"ВНИМАНИЕ: Полная перезапись файла запрещена! "
                        f"Используй <<TOOL:replace_symbol filename=\"{target_artifact}\" name=\"<имя_узла>\">> для точечной правки.\n"
                        f"{run_instruction}"
                    )
            except Exception as e:
                logger.warning(f"Ошибка AST-локализации узла в планировщике: {e}")
                plan_content += f"\n\nПлан утверждён. {run_instruction}"
        elif is_maintenance:
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

        return ArchitectPlan(
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
            intent=intent_val
        )


__all__ = ["ArchitectPlan", "EngineerPlanner"]
