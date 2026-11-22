"""
services/subagent_engineer.py
Ephemeral Engineer Sub-Agent for NeonRoute.
Executes autonomous code generation, file manipulation, and data analysis in sandbox isolation.
Zero-State Persistence: Does not touch session history or LanceDB memory directly.
"""

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.git_sandbox import GitSandboxManager
from services.sandbox_manager import SandboxManager
from services.tool_engine import ToolEngine, sanitize_code
from services.regex_filter import TextRegexFilter
from services.thought_stream_parser import ThoughtStreamParser
from services.assistant_flow import resolve_effective_thinking
from services.engineer_tdd import CodeFirstTDDPipeline
from services.code_engine import CodeValidator, ASTSkeletonizer, AutoImporter
from services.engineer_prompts import (
    scan_installed_libraries,
    resolve_pip_package,
    get_engineer_system_prompt,
    get_architect_prompt,
    get_rca_prompt,
)
from services.engineer_planner import ArchitectPlan, EngineerPlanner
from services.engineer_skill_router import EngineerSkillRouter
from services.engineer_diagnostics import (
    extract_error_fingerprint,
    diagnose_runtime_error,
    extract_crash_slice,
    clean_traceback,
)
from services.engineer_context import (
    BINARY_EXTENSIONS,
    MAX_FULL_CODE_LINES,
    _RunResultWrapper,
    EngineerContext,
)


logger = logging.getLogger("NeonRoute.SubagentEngineer")


from services.engineer_stream import CodeStreamExtractor, extract_clean_code
from services.engineer_review import ReviewPipeline
from services.engineer_parser import EngineerResponseParser, ParsedTurnActions

class SubagentEngineer:
    """Изолированный исполнитель инженерных задач."""

    BINARY_EXTENSIONS = BINARY_EXTENSIONS


    ENGINEER_CONTEXT_LENGTH = 28672
    ENGINEER_GPU_RATIO = "1.0"
    ENGINEER_MAX_TOKENS = 8192
    CODER_TEMPERATURE: float = 0.20

    # Единый стандарт передачи кода модели (Sweet Spot для 7B/8B Q5)
    MAX_FULL_CODE_LINES = MAX_FULL_CODE_LINES
    CRASH_SLICE_WINDOW = 30

    active_verification_script: Optional[str] = None
    target_artifact: Optional[str] = None
    verification_script: Optional[str] = None
    artifact_type: Optional[str] = None
    artifact_format: Optional[str] = None

    # Псевдонимы методов для полной обратной совместимости
    get_system_prompt = staticmethod(get_engineer_system_prompt)
    _diagnose_error = staticmethod(diagnose_runtime_error)
    _extract_error_fingerprint = staticmethod(extract_error_fingerprint)
    _validate_python_syntax = staticmethod(CodeValidator.validate_python_syntax)
    _validate_code_integrity = staticmethod(CodeValidator.validate_code_integrity)
    _apply_ast_linter_to_code = staticmethod(CodeValidator.apply_ast_linter_to_code)
    _apply_ast_linter = staticmethod(CodeValidator.apply_ast_linter_to_file)
    _check_undefined_names = staticmethod(CodeValidator.check_undefined_names)
    _check_unreferenced_methods = staticmethod(CodeValidator.check_unreferenced_methods)
    _check_cross_module_contracts = staticmethod(CodeValidator.check_cross_module_contracts)
    _generate_ast_skeleton = staticmethod(CodeValidator.generate_ast_skeleton)
    _normalize_artifact_format = staticmethod(EngineerContext.normalize_artifact_format)
    _has_python_code = staticmethod(EngineerContext.has_python_code)
    _is_document_task = staticmethod(EngineerContext.is_document_task)
    _extract_pure_py = staticmethod(EngineerContext.extract_pure_py)
    _inspect_workspace_file = staticmethod(EngineerContext.inspect_workspace_file)
    _rollback_workspace = staticmethod(EngineerContext.rollback_workspace)
    sanitize_code = staticmethod(sanitize_code)
    run_tdd_pipeline = staticmethod(CodeFirstTDDPipeline.run)
    _run_new_module_tdd = staticmethod(CodeFirstTDDPipeline.run_new_module)
    _run_incremental_feature_tdd = staticmethod(CodeFirstTDDPipeline.run_incremental_feature)
    _extract_code_blocks = staticmethod(CodeFirstTDDPipeline.extract_code_blocks)
    _append_new_tests = staticmethod(CodeFirstTDDPipeline.append_new_tests)

    def __init__(self):
        self.active_verification_script: Optional[str] = None
        self.target_artifact: Optional[str] = None
        self.verification_script: Optional[str] = None
        self.artifact_type: Optional[str] = None
        self.artifact_format: Optional[str] = None

    @classmethod
    def run_worker(
        cls,
        task_prompt: str,
        context_hint: str = "",
        is_analysis: bool = False,
        image_path: Optional[str] = None,
        lm_client: Optional[Any] = None,
        model_id: Optional[str] = None,
        session_history: Optional[List[Dict[str, str]]] = None,
        on_message_callback: Optional[Any] = None,
        on_status_callback: Optional[Any] = None,
        inspector: Optional[Any] = None,
        on_thought_callback: Optional[Any] = None,
        **kwargs
    ) -> Dict[str, Any]:
        worker = cls()
        res = worker.execute_worker(
            task_prompt=task_prompt,
            context_hint=context_hint,
            is_analysis=is_analysis,
            image_path=image_path,
            lm_client=lm_client,
            model_id=model_id,
            session_history=session_history,
            on_message_callback=on_message_callback,
            on_status_callback=on_status_callback,
            inspector=inspector,
            on_thought_callback=on_thought_callback,
            **kwargs
        )
        cls.active_verification_script = worker.active_verification_script
        cls.target_artifact = worker.target_artifact
        cls.verification_script = worker.verification_script
        cls.artifact_type = worker.artifact_type
        cls.artifact_format = worker.artifact_format
        return res

    def execute_worker(
        self,
        task_prompt: str,
        context_hint: str = "",
        is_analysis: bool = False,
        image_path: Optional[str] = None,
        lm_client: Optional[Any] = None,
        model_id: Optional[str] = None,
        session_history: Optional[List[Dict[str, str]]] = None,
        on_message_callback: Optional[Any] = None,
        on_status_callback: Optional[Any] = None,
        inspector: Optional[Any] = None,
        on_thought_callback: Optional[Any] = None,
        **kwargs
    ) -> Dict[str, Any]:
        from services.vision_utils import VisionUtils

        if on_thought_callback is None and "on_thought_callback" in kwargs:
            on_thought_callback = kwargs.pop("on_thought_callback")

        system_prompt = get_engineer_system_prompt(artifact_format="cli", is_analysis=is_analysis)

        # Вызов пре-инспектора контекста (для режима анализа)
        file_context = SubagentEngineer._inspect_workspace_file(task_prompt, context_hint, is_new_intent=False) if is_analysis else None

        client = lm_client
        if client is None:
            from services.lm_studio import LMStudioClient
            from config import DEFAULT_LM_STUDIO_URL
            client = LMStudioClient(DEFAULT_LM_STUDIO_URL)

        target_model = (model_id or "").strip() if model_id else None
        if not target_model:
            try:
                from utils.config_manager import ConfigManager
                cfg = ConfigManager.load_config()
                target_model = (cfg.get("engineer_model") or "").strip() or None
            except Exception:
                target_model = None
        if not target_model:
            try:
                from utils.config_manager import ConfigManager
                cfg = ConfigManager.load_config()
                target_model = (cfg.get("active_model") or cfg.get("model_id") or "").strip() or None
            except Exception:
                target_model = None
        if not target_model and client:
            target_model = getattr(client, "_last_loaded_model_id", None)
            if not target_model and hasattr(client, "get_active_model_id"):
                try:
                    target_model = client.get_active_model_id()
                except Exception:
                    pass
        if not target_model and client and hasattr(client, "get_models"):
            try:
                models = client.get_models()
                if models:
                    target_model = models[0]
            except Exception:
                pass

        effective_thinking = resolve_effective_thinking(
            mode="engineer",
            model_name=target_model or "",
            explicit_override=kwargs.get("enable_thinking"),
        )

        def _safe_emit_thought(phase: str, ev_type: str, ev_data: str):
            if on_thought_callback and callable(on_thought_callback):
                try:
                    on_thought_callback(phase, ev_type, ev_data)
                except TypeError:
                    try:
                        on_thought_callback(ev_type, ev_data)
                    except Exception:
                        pass
                except Exception:
                    pass

        def make_thought_stream_handler(on_normal_chunk: Optional[Any] = None, phase: str = "worker"):
            parser = ThoughtStreamParser()
            def _handler(chunk: str):
                if not chunk:
                    return
                if on_thought_callback and callable(on_thought_callback):
                    events = parser.feed(chunk)
                    for ev_type, ev_data in events:
                        if ev_type == "start_think":
                            _safe_emit_thought(phase, "start", "")
                        elif ev_type == "thought_chunk":
                            _safe_emit_thought(phase, "chunk", ev_data)
                        elif ev_type == "end_think":
                            dur = getattr(parser, "duration", 0.0)
                            tc = getattr(parser, "token_count", 0)
                            _safe_emit_thought(phase, "end", f"{dur:.2f}:{tc}")
                        elif ev_type == "normal_chunk":
                            if on_normal_chunk and callable(on_normal_chunk):
                                on_normal_chunk(ev_data)
                else:
                    if on_normal_chunk and callable(on_normal_chunk):
                        on_normal_chunk(chunk)

            def _finalize():
                if on_thought_callback and callable(on_thought_callback):
                    events = parser.finalize()
                    for ev_type, ev_data in events:
                        if ev_type == "start_think":
                            _safe_emit_thought(phase, "start", "")
                        elif ev_type == "thought_chunk":
                            _safe_emit_thought(phase, "chunk", ev_data)
                        elif ev_type == "end_think":
                            dur = getattr(parser, "duration", 0.0)
                            tc = getattr(parser, "token_count", 0)
                            _safe_emit_thought(phase, "end", f"{dur:.2f}:{tc}")
                        elif ev_type == "normal_chunk":
                            if on_normal_chunk and callable(on_normal_chunk):
                                on_normal_chunk(ev_data)

            return _handler, _finalize

        is_vision_model = bool(target_model and any(k in str(target_model).lower() for k in ("vl", "vision", "4o", "omni", "pixtral")))

        messages = None
        if image_path and is_vision_model:
            img_file = Path(image_path).resolve()
            if img_file.is_file():
                try:
                    image_payload = VisionUtils.prepare_image_payload(img_file, max_dim=768)
                    prompt_text = f"Задача: {task_prompt}\nВнимательно изучи прикреплённое изображение-макет и воспроизведи его структуру, числа и заголовки в целевом документе."
                    if file_context:
                        prompt_text = f"{prompt_text}\n{file_context}"
                    user_content = [
                        {"type": "text", "text": prompt_text},
                        image_payload
                    ]
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ]
                except Exception as e:
                    logger.warning(f"Не удалось подготовить изображение для воркера: {e}")

        if messages is None:
            effective_hint = context_hint
            if image_path and not is_vision_model:
                logger.info(f"SubagentEngineer: модель {target_model} не поддерживает зрение. Макет {Path(image_path).name} не передаётся как картинка.")
                effective_hint = f"{context_hint} (файл: {Path(image_path).name})" if context_hint else f"файл: {Path(image_path).name}"

            base_user = f"{task_prompt}\nКонтекст: {effective_hint}" if (effective_hint and str(effective_hint).strip()) else task_prompt
            if file_context:
                base_user = f"{base_user}\n{file_context}"

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": base_user}
            ]

        if session_history:
            hist_text = "\n[Недавний контекст диалога]:\n" + "\n".join(
                f"{'Пользователь' if m.get('role') == 'user' else 'Инженер'}: {m.get('content')}"
                for m in session_history[-3:]
            )
            if isinstance(messages[1]["content"], list):
                for item in messages[1]["content"]:
                    if isinstance(item, dict) and item.get("type") == "text":
                        item["text"] += f"\n{hist_text}"
            else:
                messages[1]["content"] += f"\n{hist_text}"

        from utils.config_manager import ConfigManager
        app_cfg = ConfigManager.load_config()
        eng_cfg = app_cfg.get("engineer_settings", {})
        max_turns = int(eng_cfg.get("max_turns", 15))
        max_same_error_retries = int(eng_cfg.get("max_same_error_retries", 3))
        max_code_lines = int(eng_cfg.get("max_full_code_lines", SubagentEngineer.MAX_FULL_CODE_LINES))
        slice_window = int(eng_cfg.get("crash_slice_window", SubagentEngineer.CRASH_SLICE_WINDOW))

        turn = 0
        last_error_fingerprint = None
        consecutive_same_error_count = 0
        consecutive_no_action_turns = 0
        loop_succeeded = False
        turn1_test_signature_mismatch = False

        last_error = ""
        last_rejected_code_context = ""
        script_to_run = None
        stdout = ""
        stderr = ""
        artifact_path = None
        response_text = ""
        run_res = None
        write_blocks: List[Tuple[str, str]] = []
        patch_blocks: List[Tuple[str, str]] = []
        replace_blocks: List[str] = []
        file_backups: Dict[str, str] = {}

        def _build_goal_anchor(extra_instruction: str = "") -> str:
            anchor = (
                f"\n\n### ЦЕЛЬ ЗАДАЧИ\n"
                f"Исходный запрос: «{task_prompt}».\n"
                f"Требование: обязательно сохрани требуемую бизнес-логику и новые поля из задачи, не откатывая код."
            )
            res = f"{extra_instruction}\n{anchor}" if extra_instruction else anchor
            logger.debug(f"[Инженер.Anchor] Внедрен Bottom Error Anchor ({len(res)} симв.).")
            return res

        # (Удалена мёртвая функция _get_compact_assistant_code)

        from services.session_manager import SessionManager, get_default_sampling_params

        active_sid = getattr(SandboxManager, "get_active_session_id", lambda: None)()
        sess = SessionManager.load_session(active_sid) if active_sid else None

        if sess and isinstance(sess.get("sampling_params"), dict) and "temperature" in sess["sampling_params"]:
            raw_temp = float(sess["sampling_params"]["temperature"])
        else:
            raw_temp = float(get_default_sampling_params("engineer").get("temperature", 0.70))

        # Предохранитель только от поломки токенизатора при экстремальных значениях
        architect_temp = max(0.05, min(1.50, raw_temp))
        worker_temp = min(architect_temp, SubagentEngineer.CODER_TEMPERATURE)

        if client and hasattr(client, "ensure_model_loaded"):
            try:
                client.ensure_model_loaded(
                    preferred_model=target_model,
                    context_length=SubagentEngineer.ENGINEER_CONTEXT_LENGTH,
                    gpu_ratio=SubagentEngineer.ENGINEER_GPU_RATIO
                )
            except Exception as _e_load:
                logger.warning(f"SubagentEngineer: ошибка предзагрузки модели: {_e_load}")

        planned_files: List[str] = []
        target_entry_file: Optional[str] = None

        # === ФАЗА 1: АРХИТЕКТОР (Планирование архитектуры и выбор контракта) ===
        ws_dir = SandboxManager.get_workspace_dir()
        GitSandboxManager.ensure_baseline(ws_dir, task_prompt)

        arch_th_handler, arch_th_finalize = make_thought_stream_handler(None, phase="architect")
        try:
            plan = EngineerPlanner.plan_architecture(
                task_prompt=task_prompt,
                context_hint=context_hint,
                ws_dir=ws_dir,
                client=client,
                target_model=target_model,
                session_history=session_history,
                is_analysis=is_analysis,
                context_length=SubagentEngineer.ENGINEER_CONTEXT_LENGTH,
                gpu_ratio=SubagentEngineer.ENGINEER_GPU_RATIO,
                available_libs=scan_installed_libraries(),
                temperature=architect_temp,
                max_tokens=1800,
                session_metadata=sess.get("metadata") if sess else None,
                enable_thinking=effective_thinking,
                thought_chunk_callback=arch_th_handler,
            )
        finally:
            arch_th_finalize()
        logger.info(f"📐 [Архитектор: Утверждённый план]: {plan.plan_content}")

        format_badge = "🎮 Pygame" if plan.artifact_format == "pygame" else f"📦 {plan.artifact_format.upper()}"
        # Для отображения в UI извлекаем чистое описание без служебных директив <<TOOL:...>> для LLM
        clean_plan_body = (plan.plan_desc or "").strip()
        if not clean_plan_body:
            raw_c = re.sub(r'<<TOOL:\w+.*?>>', '', plan.plan_content)
            raw_c = re.sub(r'<<?/+TOOL(?::\w+)?>>?', '', raw_c)
            raw_c = re.sub(r'запусти (?:главную точку входа|скрипт верификации) через \.?\s*', '', raw_c, flags=re.IGNORECASE)
            raw_c = re.sub(r'### УТВЕРЖДЁННЫЙ АРХИТЕКТУРНЫЙ ПЛАН.*?\n', '', raw_c)
            clean_plan_body = raw_c.strip()

        if plan.intent == "review":
            clean_plan_card = (
                f"### 🔍 Инспекция проекта: `{plan.target_artifact}`\n"
                f"**Формат:** {format_badge} | **Действие:** `Анализ / Git Diff`\n\n"
                f"> **Цель запроса:**\n"
                f"> {clean_plan_body}\n\n"
                f"⏳ *Выполняю анализ состояния рабочей области...*"
            )
        else:
            clean_plan_card = (
                f"### 📐 Архитектурный план: `{plan.target_artifact}`\n"
                f"**Формат:** {format_badge} | **Действие:** `{plan.intent}`\n\n"
                f"> **Техническое задание:**\n"
                f"> {clean_plan_body}\n\n"
                f"⏳ *Приступаю к автономной реализации кода...*"
            )

        def _safe_send_message(role: str, text: str):
            if on_message_callback and callable(on_message_callback):
                try:
                    on_message_callback(role, text)
                except TypeError:
                    try:
                        on_message_callback(text)
                    except Exception:
                        pass
                except Exception as _cb_err:
                    logger.warning(f"Error calling on_message_callback: {_cb_err}")

        _safe_send_message("assistant", clean_plan_card)

        artifact_format = plan.artifact_format
        self.artifact_format = plan.artifact_format
        self.artifact_type = plan.artifact_type
        self.target_artifact = plan.target_artifact
        self.verification_script = plan.verification_script
        self.active_verification_script = plan.verification_script
        target_artifact = plan.target_artifact
        target_entry_file = plan.target_entry_file
        is_maintenance = plan.is_maintenance
        is_ephemeral = plan.is_ephemeral
        planned_files = plan.planned_files

        declared_entry_file = plan.verification_script if plan.verification_script else (None if getattr(plan, "artifact_format", None) == "module" else plan.target_artifact)
        verification_passed = False

        # При подготовке системного промпта рабочего:
        skill_id = getattr(plan, "skill_id", "pure_logic_tdd")
        if getattr(plan, "artifact_format", None) in ("module", "pure_logic_tdd"):
            skill_id = "pure_logic_tdd"
        router = EngineerSkillRouter.get_instance()
        skill_meta = router.skills.get(skill_id, {})
        worker_rules = skill_meta.get("worker_rules", "")

        # Формируем системный промпт инженера с правилами архетипа
        system_prompt = get_engineer_system_prompt(
            artifact_format=plan.artifact_format,
            injected_worker_rules=worker_rules
        )
        messages[0]["content"] = system_prompt
        if plan.plan_content:
            if isinstance(messages[1]["content"], list):
                for item in messages[1]["content"]:
                    if isinstance(item, dict) and item.get("type") == "text":
                        item["text"] += plan.plan_content
            else:
                messages[1]["content"] += plan.plan_content

        last_turn_diff = ""
        has_sent_start_status = False

        # === ВЕТКА ИНСПЕКЦИИ / REVIEW / GIT (БЕЗ ПЕРЕЗАПИСИ КОДА) ===
        # === ВЕТКА ИНСПЕКЦИИ / REVIEW / GIT (Делегировано в ReviewPipeline) ===
        if plan.intent == "review":
            return ReviewPipeline.run(
                plan=plan,
                task_prompt=task_prompt,
                ws_dir=ws_dir,
                client=client,
                target_model=target_model,
                safe_send_message=_safe_send_message,
                context_length=SubagentEngineer.ENGINEER_CONTEXT_LENGTH,
                gpu_ratio=SubagentEngineer.ENGINEER_GPU_RATIO
            )

        # === ДВУХФАЗНЫЙ CODE-FIRST TDD ПАЙПЛАЙН (NEW_MODULE / INCREMENTAL_FEATURE) ===
        # Управление режимом TDD через архетип (вместо старого блэклиста):
        is_tdd_mode = (
            getattr(plan, "task_type", None) in ("NEW_MODULE", "INCREMENTAL_FEATURE")
            and getattr(plan, "interface_spec", None)
            and skill_id == "pure_logic_tdd"
            and getattr(plan, "artifact_format", None) in ("module", "pure_logic_tdd")
            and getattr(plan, "artifact_format", None) not in ("pygame", "tkinter", "excel", "word", "cli")
        ) or kwargs.get("enable_tdd", False)
        if is_tdd_mode:
            logger.info(f"🧪 [Code-First TDD] Активирован двухфазный пайплайн для {plan.task_type} ({plan.target_file or plan.target_artifact})")
            tdd_th_handler, tdd_th_finalize = make_thought_stream_handler(None, phase="worker")
            try:
                res = CodeFirstTDDPipeline.run(
                    plan=plan,
                    client=client,
                    target_model=target_model,
                    ws_dir=ws_dir,
                    task_prompt=task_prompt,
                    context_hint=context_hint,
                    on_message_callback=on_message_callback,
                    on_status_callback=on_status_callback,
                    inspector=inspector,
                    worker_temp=worker_temp,
                    enable_thinking=effective_thinking,
                    thought_chunk_callback=tdd_th_handler,
                    **kwargs
                )
            finally:
                tdd_th_finalize()
            self.active_verification_script = res.get("verification_script")
            self.target_artifact = res.get("target_artifact")
            self.verification_script = res.get("verification_script")
            return res

        while turn < max_turns:
            turn += 1
            logger.info(f"[Инженер] Автономный ход {turn}/{max_turns}")
            ws_dir = SandboxManager.get_workspace_dir()
            GitSandboxManager.create_checkpoint(ws_dir, f"turn_{turn}")
            stall_steps = max(consecutive_same_error_count, consecutive_no_action_turns)
            if stall_steps > 0:
                current_temp = min(0.50, round(worker_temp + 0.15 * stall_steps, 2))
                logger.info(
                    f"[Инженер.АнтиСтупор] Эскалация температуры сэмплера: {worker_temp:.2f} -> {current_temp:.2f} "
                    f"(шаг ступора={stall_steps}, повторных ошибок={consecutive_same_error_count})"
                )
            else:
                current_temp = worker_temp

            # Оповестить инспектор о старте:
            if inspector and hasattr(inspector, "sig_stream_started"):
                inspector.sig_stream_started.emit(target_artifact or "script.py")

            # В центральный чат отправить однократное сообщение статуса:
            if not has_sent_start_status:
                has_sent_start_status = True
                _safe_send_message("assistant", f"🛠️ **Инженер**: Приступаю к реализации `{target_artifact or 'script.py'}`...")

            # Настроить стриминг токенов через extractor:
            extractor = CodeStreamExtractor(
                on_code_chunk_callback=lambda ch: inspector.sig_stream_chunk.emit(ch) if (inspector and hasattr(inspector, "sig_stream_chunk")) else None
            )
            worker_th_handler, worker_th_finalize = make_thought_stream_handler(extractor.feed, phase="worker")

            try:
                try:
                    response_text = client.chat_completion(
                        messages=messages,
                        model=target_model,
                        temperature=current_temp,
                        max_tokens=SubagentEngineer.ENGINEER_MAX_TOKENS,
                        context_length=SubagentEngineer.ENGINEER_CONTEXT_LENGTH,
                        gpu_ratio=SubagentEngineer.ENGINEER_GPU_RATIO,
                        stream=True,
                        emit_signal=False,
                        chunk_callback=worker_th_handler,
                        enable_thinking=effective_thinking,
                        respect_master_cap=False,
                        top_p=0.95,
                        min_p=0.05,
                        dry_multiplier=0.80,
                        dry_base=1.75,
                        dry_allowed_length=3,
                        dry_penalty_last_n=512,
                    )
                except TypeError:
                    try:
                        response_text = client.chat_completion(
                            messages=messages,
                            model=target_model,
                            temperature=current_temp,
                            max_tokens=SubagentEngineer.ENGINEER_MAX_TOKENS,
                            context_length=SubagentEngineer.ENGINEER_CONTEXT_LENGTH,
                            gpu_ratio=SubagentEngineer.ENGINEER_GPU_RATIO,
                            stream=True,
                            emit_signal=False,
                            chunk_callback=worker_th_handler,
                            enable_thinking=effective_thinking,
                            respect_master_cap=False,
                        )
                    except TypeError:
                        try:
                            response_text = client.chat_completion(
                                messages=messages,
                                model=target_model,
                                temperature=current_temp,
                                max_tokens=SubagentEngineer.ENGINEER_MAX_TOKENS,
                                stream=True,
                                emit_signal=False,
                                chunk_callback=worker_th_handler,
                                enable_thinking=effective_thinking,
                                respect_master_cap=False,
                            )
                        except TypeError:
                            try:
                                response_text = client.chat_completion(
                                    messages=messages,
                                    model=target_model,
                                    temperature=current_temp,
                                    max_tokens=SubagentEngineer.ENGINEER_MAX_TOKENS,
                                    stream=True,
                                    emit_signal=False,
                                    chunk_callback=worker_th_handler,
                                    enable_thinking=effective_thinking,
                                )
                            except TypeError:
                                response_text = client.chat_completion(
                                    messages=messages,
                                    model=target_model,
                                    temperature=current_temp,
                                    max_tokens=SubagentEngineer.ENGINEER_MAX_TOKENS,
                                    stream=False,
                                    emit_signal=False,
                                )
                                if response_text and not extractor.in_code_block:
                                    extractor.feed(str(response_text))
            except Exception as e:
                logger.error(f"LM Studio call failed in SubagentEngineer: {e}")
                return {
                    "success": False,
                    "artifact_path": None,
                    "file_name": target_artifact or "",
                    "stdout": stdout,
                    "stderr": str(e),
                    "summary": f"Ошибка вызова модели: {e}",
                    "artifact_format": self.artifact_format,
                    "artifact_type": self.artifact_type,
                    "target_artifact": target_artifact,
                    "verification_script": self.verification_script,
                    "verification_passed": False
                }
            finally:
                worker_th_finalize()

            extractor.finalize()
            response_text = str(response_text or "")

            # === ПАРСИНГ ОТВЕТА МОДЕЛИ (Делегировано в EngineerResponseParser) ===
            parsed_actions = EngineerResponseParser.parse_turn_actions(
                response_text=response_text,
                ws_dir=ws_dir,
                plan=plan,
                task_prompt=task_prompt,
                context_hint=context_hint,
                target_artifact=target_artifact,
                target_entry_file=target_entry_file,
                script_to_run=script_to_run,
                turn=turn,
                turn1_test_signature_mismatch=turn1_test_signature_mismatch,
                verification_script=self.verification_script,
                declared_entry_file=declared_entry_file,
                is_maintenance=is_maintenance,
                planned_files=planned_files,
                file_backups=file_backups
            )
            write_blocks = parsed_actions.write_blocks
            patch_blocks = parsed_actions.patch_blocks
            replace_blocks = parsed_actions.replace_blocks
            git_checkpoint_results = parsed_actions.git_checkpoint_results
            if parsed_actions.last_error:
                last_error = parsed_actions.last_error

            clean_text_no_thoughts = TextRegexFilter.strip_thought_blocks(response_text)
            if write_blocks:
                final_code_to_emit = write_blocks[-1][1]
            else:
                final_code_to_emit = extract_clean_code(clean_text_no_thoughts)

            if inspector and hasattr(inspector, "sig_stream_finished"):
                inspector.sig_stream_finished.emit(target_artifact or "script.py", final_code_to_emit)


            has_run_scripts = bool(re.search(r'<<TOOL:run_script', response_text, re.IGNORECASE))
            has_any_actions = bool(write_blocks or patch_blocks or replace_blocks or has_run_scripts)

            if not has_any_actions:
                consecutive_no_action_turns += 1
                logger.warning(f"⚠️ [Ход без действий] Модель не вызвала инструменты ({consecutive_no_action_turns}/3)")
                if consecutive_no_action_turns >= 3:
                    no_action_report = (
                        "⛔ [Останов по отсутствию действий]: Инженер 3 хода подряд возвращает текстовый ответ "
                        "без вызова инструментов кодогенерации (write_file, patch_file, replace_symbol, run_script).\n"
                        "Сессия остановлена для предотвращения зацикливания диалога."
                    )
                    logger.error(no_action_report)
                    ws_dir = SandboxManager.get_workspace_dir()
                    GitSandboxManager.record_failure(
                        ws_dir=ws_dir,
                        task_title=task_prompt,
                        turn=turn,
                        error_msg="No actions executed for 3 consecutive turns"
                    )
                    SubagentEngineer._rollback_workspace(ws_dir, file_backups)
                    return {
                        "success": False,
                        "artifact_path": None,
                        "file_name": target_entry_file or "",
                        "stdout": stdout,
                        "stderr": "Отсутствуют действия модели 3 хода подряд.",
                        "summary": no_action_report
                    }

                clean_summary = ToolEngine.strip_tool_tags(response_text).strip() if hasattr(ToolEngine, "strip_tool_tags") else response_text.strip()
                if turn < max_turns and not is_analysis:
                    target_fn_hint = target_entry_file or target_artifact or 'app.py'
                    if "replace_symbol" in (last_error or ""):
                        retry_msg = f"Ошибка применения replace_symbol:\n{last_error}\nИсправь параметры или код узла."
                    else:
                        retry_msg = (
                            f"В ответе отсутствуют созданные файлы или вызовы инструментов. Оформи полный код через "
                            f"<<TOOL:write_file filename=\"{target_fn_hint}\">>код<</TOOL:write_file>> "
                            f"и запусти его через <<TOOL:run_script filename=\"{target_fn_hint}\">>."
                        )
                        if last_error:
                            clean_err = clean_traceback(last_error)
                            retry_msg += f"\n\nТекущая ошибка, которую нужно устранить:\n{clean_err[-1200:]}"
                            if last_rejected_code_context and last_rejected_code_context not in retry_msg:
                                retry_msg += last_rejected_code_context
                    target_fn_for_draft = target_fn_hint
                    assistant_draft = (
                        f"Приступаю к исправлению кода и полной перезаписи файла через инструмент:\n"
                        f"<<TOOL:write_file filename=\"{target_fn_for_draft}\">>\n"
                        f"# (предыдущая ревизия {target_fn_for_draft} отклонена проверкой)\n"
                        f"<</TOOL:write_file>>"
                    )
                    retry_msg += "\n\nВАЖНО: Не пиши текстовых подтверждений согласия без кода! Сразу выведи полный исправленный код внутри блока <<TOOL:write_file filename=\"...\">> и вызови <<TOOL:run_script filename=\"...\">>."
                    messages = [
                        messages[0],
                        messages[1],
                        {"role": "assistant", "content": assistant_draft},
                        {"role": "user", "content": _build_goal_anchor(retry_msg)}
                    ]
                    continue
                else:
                    break
            else:
                consecutive_no_action_turns = 0

            # Сохранение снимка существующих файлов перед первой попыткой
            ws_dir = SandboxManager.get_workspace_dir()
            for fn, _ in write_blocks:
                if fn not in file_backups:
                    orig_file = ws_dir / fn
                    if orig_file.exists() and orig_file.is_file():
                        try:
                            file_backups[fn] = orig_file.read_text(encoding="utf-8", errors="ignore")
                        except Exception as _e_bk:
                            logger.warning(f"SubagentEngineer: не удалось создать бэкап {fn}: {_e_bk}")

            for fn, _ in patch_blocks:
                if fn not in file_backups:
                    orig_file = ws_dir / fn
                    if orig_file.exists() and orig_file.is_file():
                        try:
                            file_backups[fn] = orig_file.read_text(encoding="utf-8", errors="ignore")
                        except Exception as _e_bk:
                            logger.warning(f"SubagentEngineer: не удалось создать бэкап {fn}: {_e_bk}")

            candidate_entry = script_to_run
            if not candidate_entry:
                run_match_pre = re.search(r'<<TOOL:run_script\s+(?:filename|script)=["\'](?P<filename>[^"\']+)["\']\s*>>?', response_text, re.IGNORECASE)
                if run_match_pre:
                    candidate_entry = run_match_pre.group("filename").strip().strip("'\"`")
                if not candidate_entry:
                    tc_list = ToolEngine.parse_tool_calls(response_text)
                    for tc in tc_list:
                        if tc.name == "run_script":
                            candidate_entry = tc.params.get("filename") or tc.params.get("script") or ""
                            if candidate_entry:
                                break

            if candidate_entry and not target_entry_file:
                target_entry_file = candidate_entry

            syntax_error_detected = False
            syntax_err = ""

            # Проектный No-Op Guard
            if is_maintenance and len(write_blocks) > 0:
                any_project_file_changed = False
                for fn, cnt in write_blocks:
                    orig_cnt = file_backups.get(fn)
                    if orig_cnt is None:
                        orig_p = ws_dir / fn
                        if orig_p.exists() and orig_p.is_file():
                            try:
                                orig_cnt = orig_p.read_text(encoding="utf-8", errors="ignore")
                            except Exception:
                                orig_cnt = None
                    if orig_cnt is None or orig_cnt.strip() != cnt.strip():
                        any_project_file_changed = True
                        break

                if not any_project_file_changed:
                    goal = task_prompt.strip() if task_prompt and task_prompt.strip() else "исправить поведение"
                    syntax_error_detected = True
                    syntax_err = (
                        f"Код файлов проекта полностью идентичен предыдущей версии на диске (0 изменений). "
                        f"Ты скопировал старый код без правок. Задача: «{goal}». "
                        f"Найди целевую логику и внеси необходимые изменения."
                    )
                    last_error = syntax_err

            if not syntax_error_detected:
                for fn, cnt in write_blocks:
                    fn_name = Path(fn).name
                    fn_lower = fn_name.lower()

                    if self.verification_script:
                        is_entry = (fn_lower == Path(self.verification_script).name.lower())
                    elif getattr(self, "artifact_format", None) == "module" or getattr(plan, "artifact_format", None) == "module":
                        is_entry = False
                    elif declared_entry_file:
                        is_entry = (fn_lower == Path(declared_entry_file).name.lower())
                    else:
                        is_entry = False

                    is_valid, s_err = CodeValidator.validate_python_syntax(fn, cnt, is_entry_file=is_entry)
                    if not is_valid:
                        syntax_error_detected = True
                        syntax_err = s_err or ""
                        logger.warning(f"SubagentEngineer: обнаружен невалидный синтаксис в {fn} (is_entry={is_entry}) до записи на диск: {syntax_err}")
                        m_l = re.search(r'line\s+(\d+)', syntax_err, re.I)
                        s_line = int(m_l.group(1)) if m_l else 0
                        if on_status_callback and callable(on_status_callback):
                            on_status_callback("error", fn, s_line, syntax_err)
                        last_error = syntax_err
                        break

                    # Проверка целостности кода (Anti-Truncation Guard)
                    orig_cnt = file_backups.get(fn)
                    if not orig_cnt:
                        orig_p = SandboxManager.get_workspace_dir() / fn
                        if orig_p.exists() and orig_p.is_file():
                            try:
                                orig_cnt = orig_p.read_text(encoding="utf-8", errors="ignore")
                            except Exception:
                                orig_cnt = None

                    is_intact, int_err = CodeValidator.validate_code_integrity(
                        fn, orig_cnt, cnt,
                        task_prompt=task_prompt,
                        is_maintenance=is_maintenance,
                        is_ephemeral=is_ephemeral
                    )
                    if not is_intact:
                        syntax_error_detected = True
                        syntax_err = int_err or ""
                        logger.warning(f"SubagentEngineer: сработал Anti-Truncation Guard для {fn}: {syntax_err}")
                        logger.warning(
                            f"[Anti-Truncation RAW] fn={fn}, cnt_len={len(cnt)}, cnt={cnt!r}, "
                            f"raw_len={len(response_text)}, raw_head={response_text[:450]!r}, raw_tail={response_text[-450:]!r}"
                        )
                        last_error = syntax_err
                        break

            if not syntax_error_detected:
                contract_err = CodeValidator.check_cross_module_contracts(write_blocks)
                if contract_err:
                    syntax_error_detected = True
                    syntax_err = contract_err
                    logger.warning(f"SubagentEngineer: сработал Cross-Module AST Guard: {syntax_err}")
                    last_error = syntax_err

            if syntax_error_detected:
                logger.warning(f"SubagentEngineer: обнаружен невалидный синтаксис: {last_error}")
                current_fp = extract_error_fingerprint(last_error)
                if current_fp and current_fp == last_error_fingerprint:
                    consecutive_same_error_count += 1
                    logger.warning(f"⚠️ [Детектор зацикливания] Повторение ошибки ({consecutive_same_error_count}/{max_same_error_retries}): {current_fp}")
                else:
                    consecutive_same_error_count = 1
                    last_error_fingerprint = current_fp

                if consecutive_same_error_count >= max_same_error_retries:
                    loop_report = (
                        f"⛔ [Останов по зацикливанию]: Инженер {max_same_error_retries} раза подряд наступил на идентичную ошибку:\n"
                        f"«{current_fp}»\n\n"
                        f"Автоматическое исправление остановлено для предотвращения деградации кода.\n"
                        f"Детали последней ошибки:\n{last_error}"
                    )
                    if any(k in last_error for k in ("Экран полностью статичен", "Экран однотонный", "слились с цветом фона", "объекты игры не отрисовываются")):
                        loop_report += (
                            "\n\n💡 Возможная причина: цвет игрока/объектов совпал с цветом фона "
                            "(например, зелёный игрок на зелёном фоне), из-за чего персонаж стал невидим. "
                            "Уточните разные контрастные цвета для фона и игрока."
                        )
                    logger.error(loop_report)
                    ws_dir = SandboxManager.get_workspace_dir()
                    GitSandboxManager.record_failure(
                        ws_dir=ws_dir,
                        task_title=task_prompt,
                        turn=turn,
                        error_msg=last_error or "Loop detected"
                    )
                    SubagentEngineer._rollback_workspace(ws_dir, file_backups)
                    return {
                        "success": False,
                        "artifact_path": None,
                        "file_name": target_entry_file or target_artifact or "",
                        "stdout": stdout,
                        "stderr": last_error,
                        "summary": loop_report
                    }

                # Формируем подсказку с экранированием путей Windows при unicodeescape ошибках
                extra_hint = ""
                if "unicodeescape" in last_error.lower():
                    extra_hint = "\nПодсказка: используй сырые строки r'''...''' для docstring с путями Windows (C:\\...) либо экранируй обратные слэши: C:\\\\NeonRoute."

                is_noop_error = "полностью идентичен" in syntax_err
                target_name = target_artifact or target_entry_file or (write_blocks[0][0] if write_blocks else "script.py")

                if is_noop_error:
                    goal = task_prompt.strip() if task_prompt and task_prompt.strip() else "исправить поведение"
                    diag_msg = (
                        f"В файле '{target_name}' не обнаружено изменений логики для задачи: «{goal}». "
                        f"Код полностью идентичен текущей версии на диске. "
                        f"Внеси необходимые изменения в реализацию и верни обновлённый код через <<TOOL:write_file filename=\"{target_name}\">>."
                    )
                elif "содержит пустую заглушку 'pass'" in syntax_err:
                    diag_msg = (
                        f"[ПУСТОЙ МЕТОД-ЗАГЛУШКА]\n{syntax_err}\n\n"
                        f"Реализуй в нём рабочую логику либо удали пустой метод вместе с его вызовом, если логика там не требуется."
                        f" Верни полный исправленный код через <<TOOL:write_file filename=\"{target_name}\">> и запусти его через <<TOOL:run_script filename=\"{target_entry_file or target_name}\">>."
                    )
                elif "сокращение кода" in syntax_err or "заменена на заглушку '...'" in syntax_err:
                    diag_msg = (
                        f"[ОШИБКА ЦЕЛОСТНОСТИ ФАЙЛА]\n{syntax_err}\n\n"
                        f"Сохрани ВСЮ существующую логику и методы без заглушек '...'."
                        f" Верни полный исправленный код через <<TOOL:write_file filename=\"{target_name}\">> и запусти его через <<TOOL:run_script filename=\"{target_entry_file or target_name}\">>."
                    )
                elif "Семантическая ошибка" in syntax_err or "NameError" in syntax_err:
                    diag_msg = (
                        f"[СЕМАНТИЧЕСКАЯ ОШИБКА В КОДЕ]\n{syntax_err}\n\n"
                        f"Исправь ошибку: обязательно добавь недостающий import либо инициализируй переменную перед использованием."
                        f" Верни полный исправленный код через <<TOOL:write_file filename=\"{target_name}\">> и запусти его через <<TOOL:run_script filename=\"{target_entry_file or target_name}\">>."
                    )
                elif "SyntaxError" in syntax_err or "IndentationError" in syntax_err:
                    patch_opt = f" либо через <<TOOL:patch_file filename=\"{target_name}\">>" if is_maintenance else ""
                    diag_msg = (
                        f"[СИНТАКСИЧЕСКАЯ ОШИБКА В КОДЕ]\n{last_error}{extra_hint}\n\n"
                        f"Исправь синтаксис и верни корректный код через <<TOOL:write_file filename=\"{target_name}\">>{patch_opt}."
                    )
                else:
                    patch_opt = f" либо через <<TOOL:patch_file filename=\"{target_name}\">>" if is_maintenance else ""
                    diag_msg = (
                        f"[ОШИБКА ВАЛИДАЦИИ КОДА]\n{last_error}{extra_hint}\n\n"
                        f"Устрани указанную проблему и верни корректный код через <<TOOL:write_file filename=\"{target_name}\">>{patch_opt}."
                    )

                if turn < max_turns:
                    ws_dir = SandboxManager.get_workspace_dir()
                    SubagentEngineer._rollback_workspace(ws_dir, file_backups)
                    if not (is_maintenance and not is_noop_error) and not is_noop_error:
                        no_patch_notice = (
                            "\n\nВнимание: файлы ещё НЕ сохранены на диск из-за ошибки валидации. "
                            "Верни полный код файлов строго через <<TOOL:write_file filename=\"...\">>. "
                            "Запрещено использовать <<TOOL:patch_file>>, так как файлы физически отсутствуют на диске."
                        )
                        diag_msg += no_patch_notice

                    # Предоставляем модели контекст отклонённого кода, чтобы исключить генерацию вслепую
                    rejected_code = ""
                    if write_blocks:
                        rejected_code = next((c for fn, c in write_blocks if fn == target_name), write_blocks[0][1])
                    if rejected_code:
                        lines_rej = rejected_code.splitlines()
                        if len(lines_rej) <= max_code_lines:
                            last_rejected_code_context = f"\n\n[ОТКЛОНЁННЫЙ ВАРИАНТ КОДА С ОШИБКОЙ ({len(lines_rej)} строк)]:\n```python\n{rejected_code}\n```"
                        else:
                            skel = ASTSkeletonizer.skeletonize(rejected_code)
                            last_rejected_code_context = f"\n\n[АРХИТЕКТУРНЫЙ СКЕЛЕТ КОДА]:\n```python\n{skel}\n```"
                            m_line = re.search(r'на строке (\d+)', syntax_err)
                            if m_line:
                                err_l = int(m_line.group(1))
                                s_idx = max(0, err_l - slice_window)
                                e_idx = min(len(lines_rej), err_l + slice_window)
                                last_rejected_code_context += f"\n\n[КОНТЕКСТ МЕСТА СБОЯ (строки {s_idx+1}-{e_idx})]:\n```python\n{chr(10).join(lines_rej[s_idx:e_idx])}\n```"
                        diag_msg += last_rejected_code_context
                        specific_err = syntax_err or last_error
                        diag_msg += (
                            f"\n\n🚨 КОНКРЕТНОЕ ТРЕБОВАНИЕ К ИСПРАВЛЕНИЮ ЭТОГО КОДА:\n"
                            f"{specific_err}\n"
                            "Запрещено возвращать приведённый выше код без изменений! Обязательно внеси указанные правки в код перед записью в <<TOOL:write_file>>."
                        )

                    # Динамическая эскалация промпта при повторении идентичной ошибки
                    if consecutive_same_error_count >= 2:
                        escalation_warning = (
                            f"🚨 ВНИМАНИЕ: Ошибка повторяется ({consecutive_same_error_count}/{max_same_error_retries})!\n"
                            f"Твой предыдущий вариант кода СНОВА отклонён валидатором из-за той же проблемы.\n"
                            f"Категорически запрещено повторять структуру из предыдущего ответа. "
                        )
                        if "NameError" in last_error or "UnboundLocalError" in last_error or "Семантическая ошибка" in last_error:
                            escalation_warning += "Внимательно изучи приведённый выше код с ошибкой и исправь инициализацию всех используемых коллекций и переменных прямо внутри вызываемой функции.\n\n"
                        else:
                            escalation_warning += "Внимательно изучи причину ошибки выше и измени структуру кода так, чтобы устранить её корень (например, удали лишний вызов или перепиши проблемный метод).\n\n"
                        diag_msg = escalation_warning + diag_msg

                    diag_msg += "\n\nВАЖНО: Не пиши текстовых подтверждений согласия без кода! Сразу выведи полный исправленный код внутри блока <<TOOL:write_file filename=\"...\">> и вызови <<TOOL:run_script filename=\"...\">>."
                    clean_user_prompt = _build_goal_anchor(diag_msg)

                    target_fn_for_draft = target_name
                    assistant_draft = (
                        f"Приступаю к исправлению кода и полной перезаписи файла через инструмент:\n"
                        f"<<TOOL:write_file filename=\"{target_fn_for_draft}\">>\n"
                        f"# (предыдущая ревизия {target_fn_for_draft} отклонена проверкой)\n"
                        f"<</TOOL:write_file>>"
                    )

                    messages = [
                        messages[0],
                        messages[1],
                        {"role": "assistant", "content": assistant_draft},
                        {"role": "user", "content": clean_user_prompt}
                    ]
                    continue
                else:
                    break

            # 3. Запись файлов
            syntax_error_found = False
            for filename, content in write_blocks:
                if filename.lower().endswith((".py", ".pyw")):
                    try:
                        ast.parse(content)
                    except SyntaxError as e:
                        syntax_error_found = True
                        failed_l = getattr(e, "lineno", 0) or 0
                        syntax_err = f"SyntaxError в файле {filename} на строке {failed_l}: {e.msg or e}"
                        logger.warning(f"[SubagentEngineer] Ход {turn}/{max_turns}: {syntax_err}")
                        last_error = syntax_err
                        if on_status_callback and callable(on_status_callback):
                            on_status_callback("error", filename, failed_l, str(e))
                        break

            if syntax_error_found:
                # Откатываем рабочую область и переходим на следующий ход автономного цикла
                ws_dir = SandboxManager.get_workspace_dir()
                SubagentEngineer._rollback_workspace(ws_dir, file_backups)
                current_fp = extract_error_fingerprint(last_error)
                if current_fp and current_fp == last_error_fingerprint:
                    consecutive_same_error_count += 1
                else:
                    consecutive_same_error_count = 1
                    last_error_fingerprint = current_fp

                if turn < max_turns:
                    diag_msg = (
                        f"❌ [СИНТАКСИЧЕСКАЯ ОШИБКА НА ХОДЕ {turn}/{max_turns}]\n"
                        f"{last_error}\n\n"
                        f"Твой предыдущий вариант кода отклонён синтаксическим валидатором AST. "
                        f"Внимательно исправь указанную строку (проверь баланс кавычек, двоеточия и отступы) "
                        f"и верни корректный код через <<TOOL:write_file filename=\"{target_entry_file or filename}\">>."
                    )
                    clean_user_prompt = _build_goal_anchor(diag_msg)
                    messages = [
                        messages[0],
                        messages[1],
                        {"role": "assistant", "content": "Понял, исправляю синтаксическую ошибку в коде."},
                        {"role": "user", "content": clean_user_prompt}
                    ]
                    continue
                else:
                    return {
                        "success": False,
                        "artifact_path": None,
                        "file_name": target_entry_file or "main.py",
                        "stdout": stdout,
                        "stderr": last_error,
                        "summary": f"Исчерпан лимит ходов ({max_turns}). Не удалось устранить синтаксическую ошибку: {last_error}"
                    }

            for filename, content in write_blocks:
                write_res = ToolEngine.write_file(filename=filename, content=content)
                if not write_res.success:
                    ws_dir = SandboxManager.get_workspace_dir()
                    SubagentEngineer._rollback_workspace(ws_dir, file_backups)
                    return {
                        "success": False,
                        "artifact_path": None,
                        "file_name": filename,
                        "stdout": stdout,
                        "stderr": write_res.output,
                        "summary": f"Ошибка записи файла {filename}: {write_res.output}"
                    }

            # 3.1. Применение патчей к существующим файлам
            patch_failed = False
            for filename, diff_content in patch_blocks:
                patch_res = ToolEngine.patch_file(filename=filename, diff_body=diff_content)
                if not patch_res.success:
                    patch_failed = True
                    last_error = patch_res.output
                    logger.warning(f"SubagentEngineer: сбой применения patch_file к {filename}: {last_error}")
                    break

            if patch_failed:
                current_fp = extract_error_fingerprint(last_error)

                if current_fp and current_fp == last_error_fingerprint:
                    consecutive_same_error_count += 1
                    logger.warning(f"⚠️ [Детектор зацикливания] Повторение ошибки ({consecutive_same_error_count}/{max_same_error_retries}): {current_fp}")
                else:
                    consecutive_same_error_count = 1
                    last_error_fingerprint = current_fp

                if consecutive_same_error_count >= max_same_error_retries:
                    loop_report = (
                        f"⛔ [Останов по зацикливанию]: Инженер {max_same_error_retries} раза подряд наступил на идентичную ошибку:\n"
                        f"«{current_fp}»\n\n"
                        f"Автоматическое исправление остановлено для предотвращения деградации кода.\n"
                        f"Детали последней ошибки:\n{last_error}"
                    )
                    if any(k in last_error for k in ("Экран полностью статичен", "Экран однотонный", "слились с цветом фона", "объекты игры не отрисовываются")):
                        loop_report += (
                            "\n\n💡 Возможная причина: цвет игрока/объектов совпал с цветом фона "
                            "(например, зелёный игрок на зелёном фоне), из-за чего персонаж стал невидим. "
                            "Уточните разные контрастные цвета для фона и игрока."
                        )
                    logger.error(loop_report)
                    ws_dir = SandboxManager.get_workspace_dir()
                    GitSandboxManager.record_failure(
                        ws_dir=ws_dir,
                        task_title=task_prompt,
                        turn=turn,
                        error_msg=last_error or "Loop detected"
                    )
                    SubagentEngineer._rollback_workspace(ws_dir, file_backups)
                    return {
                        "success": False,
                        "artifact_path": None,
                        "file_name": target_entry_file or "",
                        "stdout": stdout,
                        "stderr": last_error,
                        "summary": loop_report
                    }

                if turn < max_turns:
                    ws_dir = SandboxManager.get_workspace_dir()
                    SubagentEngineer._rollback_workspace(ws_dir, file_backups)
                    enriched_err = diagnose_runtime_error(last_error)

                    file_on_disk = ""
                    target_p = ws_dir / filename
                    if target_p.is_file():
                        try:
                            file_on_disk = target_p.read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            pass

                    patch_msg = f"Не удалось применить патч к '{filename}':\n{enriched_err}\n\n"
                    if file_on_disk:
                        patch_msg += f"[АКТУАЛЬНЫЙ КОД {filename} НА ДИСКЕ]:\n```python\n{file_on_disk}\n```\n\n"
                    patch_msg += f"Сверь строки из SEARCH с актуальным кодом на диске либо верни полный файл через <<TOOL:write_file filename=\"{filename}\">>."

                    messages = [
                        messages[0],
                        messages[1],
                        {"role": "assistant", "content": f"Понял, исправляю ошибку применения патча в {filename}."},
                        {"role": "user", "content": _build_goal_anchor(patch_msg)}
                    ]
                    continue
                else:
                    break

            # 4. Поиск запускаемого скрипта
            script_to_run = None
            ws_dir = SandboxManager.get_workspace_dir()

            if self.active_verification_script:
                avs_name = Path(self.active_verification_script).name
                if (ws_dir / avs_name).is_file() or any(Path(fn).name.lower() == avs_name.lower() for fn, _ in write_blocks):
                    script_to_run = avs_name
                    logger.info(f"🔄 [Root-Cause Loop] Зависимость создана. Автоматический перезапуск активного верификатора: {script_to_run}")

            if not script_to_run and self.verification_script:
                vs_name = Path(self.verification_script).name
                if (ws_dir / vs_name).is_file() or any(Path(fn).name.lower() == vs_name.lower() for fn, _ in write_blocks):
                    script_to_run = vs_name

            # Если задача спроектирована как библиотечный модуль без верификатора — не запускаем его вхолостую
            if not script_to_run:
                if artifact_format == "module" and not (self.active_verification_script or self.verification_script):
                    target_file_name = target_artifact or target_entry_file or (write_blocks[0][0] if write_blocks else (patch_blocks[0][0] if patch_blocks else (replace_blocks[0] if replace_blocks else "module.py")))
                    target_p = ws_dir / Path(target_file_name).name
                    logger.info(f"✅ [Модуль] Библиотечный файл '{target_file_name}' успешно пропатчен/сохранён и прошёл AST-валидацию (автономный запуск не требуется).")
                    return {
                        "success": True,
                        "artifact_path": str(target_p.resolve()) if target_p.exists() else None,
                        "file_name": target_file_name,
                        "stdout": "Модуль успешно обновлён и синтаксически валиден.",
                        "stderr": "",
                        "summary": f"Файл {target_file_name} успешно доработан и проверен AST-валидатором.",
                        "artifact_format": self.artifact_format,
                        "artifact_type": self.artifact_type,
                        "target_artifact": target_artifact,
                        "verification_script": self.verification_script,
                        "verification_passed": False
                    }

            if not script_to_run:
                run_match = re.search(r'<<TOOL:run_script\s+(?:filename|script)=["\'](?P<filename>[^"\']+)["\']\s*>>?', response_text, re.IGNORECASE)
                if run_match:
                    script_to_run = run_match.group("filename").strip().strip("'\"`")

            if not script_to_run:
                tool_calls = ToolEngine.parse_tool_calls(response_text)
                for tc in tool_calls:
                    if tc.name == "run_script":
                        script_to_run = tc.params.get("filename") or tc.params.get("script") or ""
                        if script_to_run:
                            break

            if not script_to_run and patch_blocks:
                for fn, _ in patch_blocks:
                    if fn.lower().endswith((".py", ".pyw")):
                        script_to_run = fn
                        break

            if not script_to_run and replace_blocks:
                for fn in replace_blocks:
                    if fn.lower().endswith((".py", ".pyw")):
                        script_to_run = fn
                        break

            if not script_to_run and write_blocks:
                py_files = [fn for fn, _ in write_blocks if fn.lower().endswith((".py", ".pyw"))]
                if py_files:
                    scored = []
                    for fn in py_files:
                        cnt = next((c for f, c in write_blocks if f == fn), "")
                        score = 0
                        if any(k in cnt for k in ("tkinter", "PyQt", "pygame")):
                            score += 10
                        if 'if __name__ == "__main__"' in cnt or "if __name__ == '__main__'" in cnt:
                            score += 5
                        if any(w in fn.lower() for w in ("app", "main", "gui", "run")):
                            score += 3
                        if any(w in fn.lower() for w in ("db", "model", "util")):
                            score -= 3
                        scored.append((score, fn))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    script_to_run = scored[0][1]

            if not script_to_run and write_blocks:
                script_to_run = write_blocks[0][0]

            if script_to_run and not script_to_run.lower().endswith((".py", ".pyw")):
                py_candidates = [fn for fn, _ in write_blocks if fn.lower().endswith((".py", ".pyw"))]
                script_to_run = py_candidates[0] if py_candidates else None

            newly_written_py = [fn for fn, _ in write_blocks if fn.lower().endswith((".py", ".pyw"))]
            if newly_written_py:
                if not script_to_run or script_to_run not in newly_written_py:
                    if self.verification_script and self.verification_script in newly_written_py:
                        script_to_run = self.verification_script
                    elif target_artifact and target_artifact in newly_written_py:
                        script_to_run = target_artifact
                    elif self.active_verification_script and (ws_dir / self.active_verification_script).is_file():
                        script_to_run = self.active_verification_script
                    elif target_artifact and (ws_dir / target_artifact).is_file():
                        script_to_run = target_artifact
                    else:
                        script_to_run = "make_doc.py" if "make_doc.py" in newly_written_py else ("main.py" if "main.py" in newly_written_py else newly_written_py[0])

            if not script_to_run:
                if self.active_verification_script and (ws_dir / self.active_verification_script).is_file():
                    script_to_run = self.active_verification_script
                elif self.verification_script and (ws_dir / self.verification_script).is_file():
                    script_to_run = self.verification_script
                elif target_artifact and (ws_dir / target_artifact).is_file():
                    script_to_run = target_artifact
                else:
                    if turn < max_turns:
                        diag_msg = (
                            "Файлы записаны, но скрипт не был запущен для проверки. "
                            f"Запусти скрипт через <<TOOL:run_script filename=\"{self.verification_script or target_artifact or 'main.py'}\">>."
                        )
                        messages = [
                            messages[0],
                            messages[1],
                            {"role": "assistant", "content": "Файлы сохранены на диск. Запускаю скрипт на проверку."},
                            {"role": "user", "content": _build_goal_anchor(diag_msg)}
                        ]
                        continue
                    else:
                        loop_succeeded = False
                        break

            # 5. Применение безопасного AST-линтера перед запуском
            if script_to_run and script_to_run.lower().endswith((".py", ".pyw")):
                ws_dir = SandboxManager.get_workspace_dir()
                f_path = ws_dir / script_to_run
                if f_path.exists():
                    try:
                        c_text = f_path.read_text(encoding="utf-8", errors="ignore")
                        linted_text = CodeValidator.apply_ast_linter_to_code(c_text)
                        if linted_text != c_text:
                            f_path.write_text(linted_text, encoding="utf-8")
                    except Exception:
                        pass

            # 6. Запуск скрипта
            if on_status_callback and callable(on_status_callback):
                on_status_callback("running", script_to_run, 0, "")
            _safe_send_message("assistant", f"✅ Файл `{target_artifact or script_to_run}` сформирован. Запускаю верификацию в песочнице...")
            raw_run_res = ToolEngine.run_script(filename=script_to_run)
            run_res = _RunResultWrapper(raw_run_res)

            # === ZERO-TOKEN HEALING (Тихий автоимпорт) ===
            if not run_res.success and script_to_run:
                raw_err = run_res.output or run_res.error or ""
                m_name = re.search(r"NameError:\s+name\s+['\"](\w+)['\"]\s+is not defined", raw_err)
                if m_name:
                    missing_symbol = m_name.group(1)
                    target_file_path = ws_dir / script_to_run
                    if AutoImporter.try_inject_import(target_file_path, missing_symbol):
                        logger.info(f"⚡ [Zero-Token Healing] Повторный запуск {script_to_run} после добавления '{missing_symbol}'...")
                        raw_run_res = ToolEngine.run_script(filename=script_to_run)
                        run_res = _RunResultWrapper(raw_run_res)

            stdout = run_res.stdout or ""
            if not stdout and run_res.output and not run_res.output.startswith("[Скрипт завершен"):
                stdout = run_res.output
            stderr = run_res.error or ""
            exit_code = getattr(run_res, "return_code", getattr(raw_run_res, "exit_code", 0 if run_res.success else 1))

            if not run_res.success:
                ws_dir = SandboxManager.get_workspace_dir()
                # Извлекаем git diff до отката рабочей области
                last_turn_diff = GitSandboxManager.get_working_diff(ws_dir)
                executed_code = ""
                f_path = ws_dir / script_to_run
                if f_path.is_file():
                    try:
                        executed_code = f_path.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        pass
                if not executed_code:
                    for fn, cnt in write_blocks:
                        if Path(fn).name.lower() == Path(script_to_run).name.lower():
                            executed_code = cnt
                            break

                clean_code = executed_code

                SubagentEngineer._rollback_workspace(ws_dir, file_backups, write_blocks=write_blocks)

                clean_lines = clean_code.splitlines() if clean_code else []
                line_count = len(clean_lines)

                last_error = run_res.output or run_res.error or "Неизвестная ошибка выполнения"
                self.active_verification_script = script_to_run
                m_l = re.search(r'line\s+(\d+)', last_error, re.I)
                f_line = int(m_l.group(1)) if m_l else 0
                if on_status_callback and callable(on_status_callback):
                    on_status_callback("error", script_to_run, f_line, last_error)

                if turn == 1 and self.verification_script:
                    vs_name = Path(self.verification_script).name.lower()
                    if Path(script_to_run).name.lower() == vs_name:
                        err_text = f"{last_error} {run_res.error or ''}"
                        if any(sig_err in err_text for sig_err in ("TypeError", "AssertionError")):
                            turn1_test_signature_mismatch = True
                            logger.info(
                                f"ℹ️ [Verification Freeze] Скрипт верификации '{self.verification_script}' упал на 1-м ходе "
                                f"из-за расхождения сигнатур/контракта (TypeError/AssertionError). Разрешена ревизия на ходе 2."
                            )
                current_fp = extract_error_fingerprint(last_error)

                if current_fp and current_fp == last_error_fingerprint:
                    consecutive_same_error_count += 1
                    logger.warning(f"⚠️ [Детектор зацикливания] Повторение ошибки ({consecutive_same_error_count}/{max_same_error_retries}): {current_fp}")
                else:
                    consecutive_same_error_count = 1
                    last_error_fingerprint = current_fp

                if consecutive_same_error_count >= max_same_error_retries:
                    loop_report = (
                        f"⛔ [Останов по зацикливанию]: Инженер {max_same_error_retries} раза подряд наступил на идентичную ошибку:\n"
                        f"«{current_fp}»\n\n"
                        f"Автоматическое исправление остановлено для предотвращения деградации кода.\n"
                        f"Детали последней ошибки:\n{last_error}"
                    )
                    if any(k in last_error for k in ("Экран полностью статичен", "Экран однотонный", "слились с цветом фона", "объекты игры не отрисовываются")):
                        loop_report += (
                            "\n\n💡 Возможная причина: цвет игрока/объектов совпал с цветом фона "
                            "(например, зелёный игрок на зелёном фоне), из-за чего персонаж стал невидим. "
                            "Уточните разные контрастные цвета для фона и игрока."
                        )
                    logger.error(loop_report)
                    ws_dir = SandboxManager.get_workspace_dir()
                    GitSandboxManager.record_failure(
                        ws_dir=ws_dir,
                        task_title=task_prompt,
                        turn=turn,
                        error_msg=last_error or "Loop detected"
                    )
                    SubagentEngineer._rollback_workspace(ws_dir, file_backups)
                    return {
                        "success": False,
                        "artifact_path": None,
                        "file_name": target_entry_file or "",
                        "stdout": stdout,
                        "stderr": last_error,
                        "summary": loop_report
                    }

                enriched_error = diagnose_runtime_error(last_error)

                if turn < max_turns:
                    logger.warning(f"SubagentEngineer: сбой в {script_to_run}: {last_error.strip()}. Запуск анализа...")

                    skip_rca = any(k in last_error for k in (
                        "SyntaxError", "IndentationError", "No module named", "NameError", "UnboundLocalError"
                    ))
                    rca_text = ""
                    if not skip_rca and turn < max_turns:
                        logger.info("🔍 [Архитектор] Анализ причины падения и выработка плана исправления...")
                        rca_sys_prompt = get_rca_prompt()
                        rca_user_content = (
                            f"Задача: «{task_prompt}»\n"
                            f"Ошибка выполнения {script_to_run}:\n{enriched_error}\n\n"
                        )
                        rca_code = executed_code or clean_code
                        if line_count <= max_code_lines:
                            rca_user_content += f"Код скрипта ({script_to_run}):\n```python\n{rca_code}\n```\n\n"
                        else:
                            skel = ASTSkeletonizer.skeletonize(rca_code)
                            crash_slice = extract_crash_slice(rca_code, last_error, filename=script_to_run, window=slice_window)
                            rca_user_content += f"Скелет файла ({script_to_run}):\n```python\n{skel}\n```\n\n"
                            if crash_slice:
                                rca_user_content += f"Место сбоя ({script_to_run}):\n```python\n{crash_slice}\n```\n\n"

                        rca_messages = [
                            {"role": "system", "content": rca_sys_prompt},
                            {"role": "user", "content": rca_user_content}
                        ]
                        try:
                            try:
                                rca_resp = client.chat_completion(
                                    messages=rca_messages,
                                    model=target_model,
                                    temperature=0.10,
                                    max_tokens=600,
                                    repeat_penalty=1.10,
                                    stream=False,
                                    emit_signal=False,
                                    enable_thinking=(False if effective_thinking is not None else None)
                                )
                            except TypeError:
                                rca_resp = client.chat_completion(
                                    messages=rca_messages,
                                    model=target_model,
                                    temperature=0.10,
                                    max_tokens=600,
                                    stream=False,
                                    emit_signal=False
                                )
                            rca_text = TextRegexFilter.strip_thought_blocks(str(rca_resp or "")).strip()
                            if rca_text:
                                logger.info(f"🔍 [Архитектор: Разбор ошибки и план решения]:\n{rca_text}")
                        except Exception as _e_rca:
                            logger.warning(f"🔍 [Архитектор] Сбой этапа анализа ошибки: {_e_rca}")

                    logger.info(f"🔄 [Self-Correction] Ход {turn} завершился ошибкой. Подготовка к ходу {turn + 1}...")

                    diag_body = f"Скрипт {script_to_run} упал с ошибкой:\n{enriched_error}\n\n"
                    if rca_text:
                        diag_body += f"АНАЛИЗ АРХИТЕКТОРА:\n{rca_text}\n\n"
                    if last_turn_diff:
                        diag_body += f"[ПОСЛЕДНИЕ ИЗМЕНЕНИЯ В КОДЕ (git diff)]:\n```diff\n{last_turn_diff}\n```\n\n"
                    if turn == 1 and turn1_test_signature_mismatch:
                        diag_body += (
                            f"[РЕВИЗИЯ ВЕРИФИКАЦИИ]: Обнаружено расхождение интерфейсов или сигнатур вызова между тестом и кодом ({self.verification_script}). "
                            "На текущем ходе 2 разрешена однократная согласованная правка как целевого модуля, так и скрипта верификации под единый контракт.\n\n"
                        )

                    if line_count <= max_code_lines:
                        last_rejected_code_context = (
                            f"\n\n[ТЕКУЩАЯ ВЕРСИЯ {script_to_run} НА ДИСКЕ ({line_count} строк)]:\n"
                            f"```python\n{clean_code}\n```"
                        )
                        diag_body += f"{last_rejected_code_context.lstrip()}\n\n"
                    else:
                        skel = ASTSkeletonizer.skeletonize(clean_code)
                        crash_slice = extract_crash_slice(executed_code or clean_code, last_error, filename=script_to_run, window=slice_window)
                        last_rejected_code_context = f"\n\n[АРХИТЕКТУРНЫЙ СКЕЛЕТ ФАЙЛА {script_to_run}]:\n```python\n{skel}\n```"
                        if crash_slice:
                            last_rejected_code_context += f"\n\n[КОНТЕКСТ МЕСТА СБОЯ]:\n```python\n{crash_slice}\n```"
                        diag_body += f"{last_rejected_code_context.lstrip()}\n\n"

                    specific_err = last_error or enriched_error
                    diag_body += (
                        f"🚨 КОНКРЕТНОЕ ТРЕБОВАНИЕ К ИСПРАВЛЕНИЮ ЭТОГО КОДА:\n"
                        f"{specific_err}\n"
                        "Запрещено возвращать приведённый выше код без изменений! Обязательно внеси указанные правки в код перед записью в <<TOOL:write_file>>.\n\n"
                    )

                    if line_count <= max_code_lines:
                        diag_body += (
                            f"Устрани ошибку и верни полный обновлённый файл через <<TOOL:write_file filename=\"{script_to_run}\">> "
                            f"и запусти его через <<TOOL:run_script filename=\"{self.active_verification_script or script_to_run}\">>."
                        )
                    else:
                        diag_body += (
                            f"Внеси точечное исправление через <<TOOL:patch_file filename=\"{script_to_run}\">> либо верни полный файл через <<TOOL:write_file filename=\"{script_to_run}\">> "
                            f"и запусти его через <<TOOL:run_script filename=\"{self.active_verification_script or script_to_run}\">>."
                        )

                    diag_body += "\n\nВАЖНО: Не пиши текстовых подтверждений согласия без кода! Сразу выведи полный исправленный код внутри блока <<TOOL:write_file filename=\"...\">> и вызови <<TOOL:run_script filename=\"...\">>."
                    runtime_prompt = _build_goal_anchor(diag_body)

                    target_fn_for_draft = script_to_run
                    assistant_draft = (
                        f"Приступаю к исправлению кода и полной перезаписи файла через инструмент:\n"
                        f"<<TOOL:write_file filename=\"{target_fn_for_draft}\">>\n"
                        f"# (предыдущая ревизия {target_fn_for_draft} отклонена проверкой)\n"
                        f"<</TOOL:write_file>>"
                    )

                    messages = [
                        messages[0],
                        messages[1],
                        {"role": "assistant", "content": assistant_draft},
                        {"role": "user", "content": runtime_prompt}
                    ]
                    continue
                else:
                    break
            else:
                # Скрипт script_to_run выполнился с кодом 0
                logger.info(f"✅ Скрипт {script_to_run} успешно выполнен с exit_code == 0.")
                if on_status_callback and callable(on_status_callback):
                    on_status_callback("success", script_to_run, 0, "")

                # Детерминированное завершение для документов (excel, word)
                if self.artifact_format in ("excel", "word") and Path(script_to_run).name.lower() == "make_doc.py":
                    created_docs = [
                        f for f in ws_dir.iterdir()
                        if f.is_file() and f.suffix.lower() in (".xlsx", ".docx") and f.stat().st_size > 0
                    ]
                    if created_docs:
                        self.active_verification_script = None
                        loop_succeeded = True
                        logger.info(
                            f"🎉 [Документ создан]: Найден сгенерированный документ {created_docs[0].name} "
                            f"({created_docs[0].stat().st_size} байт). Завершение цикла."
                        )
                        GitSandboxManager.checkpoint(ws_dir, f"Turn {turn}: generated document {created_docs[0].name}")
                        break

                if exit_code == 0:
                    target_file = ws_dir / (self.target_artifact or "app.py")
                    active_spec = getattr(plan, "interface_spec", None) if not getattr(plan, "is_ephemeral", False) else None
                    is_solid, substance_err = ToolEngine.check_artifact_substance(
                        target_file,
                        min_bytes=350,
                        interface_spec=active_spec,
                        strict_contract=bool(self.verification_script)
                    )
                    if not is_solid:
                        logger.warning(f"🚨 [SubstanceGuard REJECT] Файл забракован: {substance_err}")
                        last_error = substance_err
                        current_fp = extract_error_fingerprint(substance_err)
                        if current_fp and current_fp == last_error_fingerprint:
                            consecutive_same_error_count += 1
                            logger.warning(f"⚠️ [Детектор зацикливания] Повторение ошибки ({consecutive_same_error_count}/{max_same_error_retries}): {current_fp}")
                        else:
                            consecutive_same_error_count = 1
                            last_error_fingerprint = current_fp

                        if consecutive_same_error_count >= max_same_error_retries:
                            loop_report = (
                                f"⛔ [Останов по зацикливанию]: Инженер {max_same_error_retries} раза подряд наступил на идентичную ошибку:\n"
                                f"«{current_fp}»\n\n"
                                f"Автоматическое исправление остановлено для предотвращения деградации кода.\n"
                                f"Детали последней ошибки:\n{substance_err}"
                            )
                            logger.error(loop_report)
                            GitSandboxManager.record_failure(
                                ws_dir=ws_dir,
                                task_title=task_prompt,
                                turn=turn,
                                error_msg=substance_err or "Loop detected"
                            )
                            SubagentEngineer._rollback_workspace(ws_dir, file_backups)
                            return {
                                "success": False,
                                "artifact_path": None,
                                "file_name": target_entry_file or "",
                                "stdout": stdout,
                                "stderr": substance_err,
                                "summary": loop_report
                            }

                        rej_content = ""
                        try:
                            if target_file.is_file():
                                rej_content = target_file.read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            rej_content = ""
                        SubagentEngineer._rollback_workspace(ws_dir, file_backups)
                        rej_ctx = ""
                        if rej_content:
                            rej_lines = rej_content.splitlines()
                            if len(rej_lines) <= max_code_lines:
                                rej_ctx = f"\n\n[ОТКЛОНЁННЫЙ ВАРИАНТ КОДА С ОШИБКОЙ ({len(rej_lines)} строк)]:\n```python\n{rej_content}\n```"
                            else:
                                skel = ASTSkeletonizer.skeletonize(rej_content)
                                rej_ctx = f"\n\n[АРХИТЕКТУРНЫЙ СКЕЛЕТ КОДА]:\n```python\n{skel}\n```"
                        elif last_rejected_code_context:
                            rej_ctx = last_rejected_code_context
                        last_rejected_code_context = rej_ctx

                        target_fn_hint = target_entry_file or target_artifact or target_file.name
                        diag_msg = (
                            f"Файл '{target_file.name}' забракован SubstanceGuard:\n{substance_err}\n\n"
                            f"Устрани замечание, сохрани полную реализацию без заглушек и с выполнением всех обязательных контрактов."
                        )
                        if rej_ctx:
                            diag_msg += rej_ctx
                            diag_msg += (
                                f"\n\n🚨 КОНКРЕТНОЕ ТРЕБОВАНИЕ К ИСПРАВЛЕНИЮ ЭТОГО КОДА:\n"
                                f"{substance_err}\n"
                                "Запрещено возвращать приведённый выше код без изменений! Обязательно внеси указанные правки в код перед записью в <<TOOL:write_file>>."
                            )
                        diag_msg += (
                            f"\n\nВерни исправленный код через <<TOOL:write_file filename=\"{target_fn_hint}\">> "
                            f"и запусти его через <<TOOL:run_script filename=\"{target_fn_hint}\">>."
                        )
                        diag_msg += "\n\nВАЖНО: Не пиши текстовых подтверждений согласия без кода! Сразу выведи полный исправленный код внутри блока <<TOOL:write_file filename=\"...\">> и вызови <<TOOL:run_script filename=\"...\">>."

                        target_fn_for_draft = target_fn_hint
                        assistant_draft = (
                            f"Приступаю к исправлению кода и полной перезаписи файла через инструмент:\n"
                            f"<<TOOL:write_file filename=\"{target_fn_for_draft}\">>\n"
                            f"# (предыдущая ревизия {target_fn_for_draft} отклонена проверкой)\n"
                            f"<</TOOL:write_file>>"
                        )
                        messages = [
                            messages[0],
                            messages[1],
                            {"role": "assistant", "content": assistant_draft},
                            {"role": "user", "content": _build_goal_anchor(diag_msg)}
                        ]
                        continue
                    else:
                        logger.info(f"✅ [SubstanceGuard PASS] Артефакт '{target_file.name}' прошёл валидацию.")
                        last_rejected_code_context = ""
                        last_error = ""

                # Проверяем критерий завершения задачи:
                # 1. Если verification_script задан — он обязан быть выполнен с exit_code == 0
                if self.verification_script:
                    vs_name = Path(self.verification_script).name.lower()
                    if Path(script_to_run).name.lower() == vs_name:
                        verification_passed = True
                        self.active_verification_script = None
                        loop_succeeded = True
                        if turn > 1:
                            logger.info(f"🎉 [Root-Cause Loop] Скрипт верификации {self.verification_script} успешно пройден на ходе {turn}.")
                        GitSandboxManager.checkpoint(ws_dir, f"Turn {turn}: verified by {self.verification_script}")
                        break
                    else:
                        vs_path = ws_dir / Path(self.verification_script).name
                        if vs_path.is_file():
                            logger.info(f"🔄 [Root-Cause Loop] Модуль {script_to_run} выполнен успешно. Автоматический перезапуск верификатора {self.verification_script}...")
                            self.active_verification_script = self.verification_script
                            script_to_run = Path(self.verification_script).name
                            if on_status_callback and callable(on_status_callback):
                                on_status_callback("running", script_to_run, 0, "")
                            raw_v_res = ToolEngine.run_script(filename=script_to_run)
                            v_res = _RunResultWrapper(raw_v_res)
                            if v_res.success:
                                stdout = v_res.stdout or stdout
                                stderr = v_res.error or ""
                                verification_passed = True
                                self.active_verification_script = None
                                loop_succeeded = True
                                if on_status_callback and callable(on_status_callback):
                                    on_status_callback("success", script_to_run, 0, "")
                                logger.info(f"🎉 [Root-Cause Loop] Верификатор {self.verification_script} успешно завершился с exit_code == 0!")
                                GitSandboxManager.checkpoint(ws_dir, f"Turn {turn}: verified by {self.verification_script}")
                                break
                            else:
                                last_error = v_res.output or v_res.error or "Ошибка верификатора"
                                stdout = v_res.stdout or stdout
                                stderr = v_res.error or ""
                                m_vl = re.search(r'line\s+(\d+)', last_error, re.I)
                                vf_line = int(m_vl.group(1)) if m_vl else 0
                                if on_status_callback and callable(on_status_callback):
                                    on_status_callback("error", script_to_run, vf_line, last_error)
                                if turn == 1 and self.verification_script:
                                    err_text = f"{last_error} {v_res.error or ''}"
                                    if any(sig_err in err_text for sig_err in ("TypeError", "AssertionError")):
                                        turn1_test_signature_mismatch = True
                                        logger.info(
                                            f"ℹ️ [Verification Freeze] Скрипт верификации '{self.verification_script}' упал на 1-м ходе "
                                            f"из-за расхождения сигнатур/контракта (TypeError/AssertionError). Разрешена ревизия на ходе 2."
                                        )
                                current_fp = extract_error_fingerprint(last_error)
                                if current_fp and current_fp == last_error_fingerprint:
                                    consecutive_same_error_count += 1
                                else:
                                    consecutive_same_error_count = 1
                                    last_error_fingerprint = current_fp
                        else:
                            logger.warning(f"⚠️ [Root-Cause Loop] Файл {script_to_run} создан, но verification_script {self.verification_script} еще отсутствует на диске.")
                            if turn < max_turns:
                                need_vs_msg = (
                                    f"Файл «{script_to_run}» успешно записан и выполнен. "
                                    f"Однако задача НЕ завершена: согласно плану требуется создать и выполнить скрипт верификации «{self.verification_script}». "
                                    f"Создай «{self.verification_script}» через <<TOOL:write_file filename=\"{self.verification_script}\">> "
                                    f"и запусти его через <<TOOL:run_script filename=\"{self.verification_script}\">>."
                                )
                                messages = [
                                    messages[0],
                                    messages[1],
                                    {"role": "assistant", "content": f"Файл {script_to_run} выполнен. Создаю скрипт верификации {self.verification_script}."},
                                    {"role": "user", "content": _build_goal_anchor(need_vs_msg)}
                                ]
                                continue
                            else:
                                loop_succeeded = False
                                break

                # 2. Если verification_script отсутствует
                else:
                    tgt_name = Path(target_artifact).name.lower() if target_artifact else ""
                    if Path(script_to_run).name.lower() == tgt_name:
                        self.active_verification_script = None
                        loop_succeeded = True
                        if turn > 1:
                            logger.info(f"🎉 [Self-Correction] Целевой артефакт {target_artifact} успешно выполнен на ходе {turn}.")
                        GitSandboxManager.checkpoint(ws_dir, f"Turn {turn}: verified step for {target_artifact}")
                        break
                    else:
                        tgt_path = ws_dir / Path(target_artifact).name if target_artifact else None
                        if tgt_path and tgt_path.is_file():
                            logger.info(f"🔄 [Root-Cause Loop] Вспомогательный модуль {script_to_run} выполнен. Запуск целевого артефакта {target_artifact}...")
                            self.active_verification_script = Path(target_artifact).name
                            script_to_run = Path(target_artifact).name
                            raw_t_res = ToolEngine.run_script(filename=script_to_run)
                            t_res = _RunResultWrapper(raw_t_res)
                            if t_res.success:
                                stdout = t_res.stdout or stdout
                                stderr = t_res.error or ""
                                self.active_verification_script = None
                                loop_succeeded = True
                                GitSandboxManager.checkpoint(ws_dir, f"Turn {turn}: verified step for {target_artifact}")
                                break
                            else:
                                last_error = t_res.output or t_res.error or f"Ошибка выполнения {target_artifact}"
                                stdout = t_res.stdout or stdout
                                stderr = t_res.error or ""
                                current_fp = extract_error_fingerprint(last_error)
                                if current_fp and current_fp == last_error_fingerprint:
                                    consecutive_same_error_count += 1
                                else:
                                    consecutive_same_error_count = 1
                                    last_error_fingerprint = current_fp
                        else:
                            if turn < max_turns:
                                need_tgt_msg = (
                                    f"Файл «{script_to_run}» выполнен. Основной целевой артефакт «{target_artifact}» "
                                    f"еще не создан или не выполнен. Создай и запусти «{target_artifact}»."
                                )
                                messages = [
                                    messages[0],
                                    messages[1],
                                    {"role": "assistant", "content": f"Вспомогательный модуль {script_to_run} выполнен. Перехожу к созданию {target_artifact}."},
                                    {"role": "user", "content": _build_goal_anchor(need_tgt_msg)}
                                ]
                                continue
                            else:
                                loop_succeeded = False
                                break

                # Если перезапуск верификатора / целевого артефакта упал с ошибкой:
                if not loop_succeeded:
                    if consecutive_same_error_count >= max_same_error_retries:
                        loop_report = (
                            f"⛔ [Останов по зацикливанию]: Инженер {max_same_error_retries} раза подряд наступил на идентичную ошибку:\n"
                            f"«{current_fp}»\n\n"
                            f"Автоматическое исправление остановлено для предотвращения деградации кода.\n"
                            f"Детали последней ошибки:\n{last_error}"
                        )
                        logger.error(loop_report)
                        ws_dir = SandboxManager.get_workspace_dir()
                        GitSandboxManager.record_failure(
                            ws_dir=ws_dir,
                            task_title=task_prompt,
                            turn=turn,
                            error_msg=last_error or "Loop detected"
                        )
                        SubagentEngineer._rollback_workspace(ws_dir, file_backups)
                        return {
                            "success": False,
                            "artifact_path": None,
                            "file_name": target_artifact or target_entry_file or "",
                            "stdout": stdout,
                            "stderr": last_error,
                            "summary": loop_report,
                            "artifact_format": self.artifact_format,
                            "artifact_type": self.artifact_type,
                            "target_artifact": target_artifact,
                            "verification_script": self.verification_script,
                            "verification_passed": False
                        }

                    enriched_error = diagnose_runtime_error(last_error)
                    if turn < max_turns:
                        logger.warning(f"SubagentEngineer: сбой в {script_to_run}: {last_error.strip()}. Подготовка к следующему ходу...")

                        ws_dir = SandboxManager.get_workspace_dir()

                        # 1. Чтение кода упавшего верификатора script_to_run
                        vs_code = ""
                        vs_file = ws_dir / script_to_run
                        if vs_file.is_file():
                            try:
                                vs_code = vs_file.read_text(encoding="utf-8", errors="ignore")
                            except Exception:
                                pass

                        vs_lines = vs_code.splitlines() if vs_code else []
                        vs_crash_slice = extract_crash_slice(vs_code, last_error, filename=script_to_run, window=slice_window) if vs_code else ""

                        vs_section = ""
                        if vs_code:
                            if len(vs_lines) <= max_code_lines:
                                vs_section = f"[ИСХОДНЫЙ КОД СКРИПТА ВЕРИФИКАЦИИ {script_to_run} ({len(vs_lines)} строк)]:\n```python\n{vs_code}\n```\n\n"
                                if vs_crash_slice:
                                    vs_section += f"[СТРОКА СБОЯ В {script_to_run}]:\n```python\n{vs_crash_slice}\n```\n\n"
                            else:
                                skel_vs = ASTSkeletonizer.skeletonize(vs_code)
                                vs_section = f"[АРХИТЕКТУРНЫЙ СКЕЛЕТ СКРИПТА ВЕРИФИКАЦИИ {script_to_run}]:\n```python\n{skel_vs}\n```\n\n"
                                if vs_crash_slice:
                                    vs_section += f"[СТРОКА СБОЯ В {script_to_run}]:\n```python\n{vs_crash_slice}\n```\n\n"

                        # 2. Чтение кода целевого модуля target_artifact
                        tgt_fn = target_artifact or target_entry_file or ""
                        tgt_code = ""
                        if tgt_fn:
                            tgt_file = ws_dir / Path(tgt_fn).name
                            if tgt_file.is_file():
                                try:
                                    tgt_code = tgt_file.read_text(encoding="utf-8", errors="ignore")
                                except Exception:
                                    pass

                        tgt_section = ""
                        if tgt_code:
                            tgt_lines = tgt_code.splitlines()
                            tgt_crash_slice = extract_crash_slice(tgt_code, last_error, filename=tgt_fn, window=slice_window)
                            if len(tgt_lines) <= max_code_lines:
                                tgt_section = f"[ИСХОДНЫЙ КОД ЦЕЛЕВОГО МОДУЛЯ {tgt_fn} ({len(tgt_lines)} строк)]:\n```python\n{tgt_code}\n```\n\n"
                                if tgt_crash_slice:
                                    tgt_section += f"[МЕСТО ОШИБКИ В ЦЕЛЕВОМ МОДУЛЕ {tgt_fn}]:\n```python\n{tgt_crash_slice}\n```\n\n"
                            else:
                                skel_tgt = ASTSkeletonizer.skeletonize(tgt_code)
                                tgt_section = f"[АРХИТЕКТУРНЫЙ СКЕЛЕТ ЦЕЛЕВОГО МОДУЛЯ {tgt_fn}]:\n```python\n{skel_tgt}\n```\n\n"
                                if tgt_crash_slice:
                                    tgt_section += f"[КОНТЕКСТ МЕСТА ОШИБКИ В ЦЕЛЕВОМ МОДУЛЕ {tgt_fn}]:\n```python\n{tgt_crash_slice}\n```\n\n"

                        # 3. Структурированный контекст диалога:
                        # 1) Лог ошибки (enriched_error)
                        # 2) Исходный код упавшего скрипта верификации с указанием строки сбоя
                        # 3) Исходный код целевого модуля
                        # 4) Чёткая инструкция: исправить контракт взаимодействия между целевым модулем и тестом
                        diag_body = (
                            f"[ОШИБКА ВЫПОЛНЕНИЯ]:\n{enriched_error}\n\n"
                        )
                        if last_turn_diff:
                            diag_body += f"[ПОСЛЕДНИЕ ИЗМЕНЕНИЯ (git diff)]:\n```diff\n{last_turn_diff}\n```\n\n"
                        if vs_section:
                            diag_body += vs_section
                        if tgt_section:
                            diag_body += tgt_section

                        diag_body += (
                            f"[ИНСТРУКЦИЯ ПО ИСПРАВЛЕНИЮ КОНТРАКТА]:\n"
                            f"Исправь контракт взаимодействия между целевым модулем ({tgt_fn or 'основной код'}) "
                            f"и скриптом верификации ({script_to_run}). Устрани расхождения в интерфейсах, именах методов, типах "
                            f"или сигнатурах функций. Верни обновлённые файлы через отдельные блоки <<TOOL:write_file filename=\"...\">> "
                            f"и повторно запусти скрипт верификации через <<TOOL:run_script filename=\"{self.active_verification_script or script_to_run}\">>."
                        )

                        messages = [
                            messages[0],
                            messages[1],
                            {"role": "assistant", "content": f"Понял, устраняю ошибку верификатора в {script_to_run}."},
                            {"role": "user", "content": _build_goal_anchor(diag_body)}
                        ]
                        continue
                    else:
                        break

        # Завершение по превышению лимита ходов
        if not loop_succeeded:
            ws_dir = SandboxManager.get_workspace_dir()
            GitSandboxManager.record_failure(
                ws_dir=ws_dir,
                task_title=task_prompt,
                turn=turn,
                error_msg=last_error or f"Max turns reached ({max_turns})"
            )
            SubagentEngineer._rollback_workspace(ws_dir, file_backups, write_blocks=write_blocks)
            final_summary = f"Достигнут максимальный лимит шагов задачи ({max_turns}). Последняя ошибка:\n{last_error}"
            enriched_err = diagnose_runtime_error(last_error)
            m_missing = re.search(r"No module named ['\"]([^'\"]+)['\"]", enriched_err)
            if m_missing:
                missing_lib = m_missing.group(1).split('.')[0]
                pip_pkg = resolve_pip_package(missing_lib)
                if pip_pkg:
                    final_summary = (
                        f"📦 Для запуска скрипта требуется внешняя библиотека «{missing_lib}».\n"
                        f"Файл сохранён в песочнице.\n"
                        f"Команда для установки: pip install {pip_pkg}"
                    )
                else:
                    final_summary = (
                        f"❌ Ошибка архитектуры: скрипт ссылается на локальный модуль «{missing_lib}», "
                        f"но файл «{missing_lib}.py» не был создан в песочнице за {max_turns} шагов."
                    )

            return {
                "success": False,
                "artifact_path": None,
                "file_name": target_artifact or target_entry_file or "",
                "stdout": stdout,
                "stderr": last_error,
                "summary": final_summary,
                "artifact_format": self.artifact_format,
                "artifact_type": self.artifact_type,
                "target_artifact": target_artifact,
                "verification_script": self.verification_script,
                "verification_passed": False
            }

        # Режим анализа
        if is_analysis is True:
            MAX_STDOUT_CHARS = 2500
            safe_stdout = stdout[:MAX_STDOUT_CHARS] if len(stdout) > MAX_STDOUT_CHARS else stdout
            synth_messages = [
                {
                    "role": "system",
                    "content": "Ты — персональный ассистент Элария. Кратко и четко ответь на вопрос на основе данных."
                },
                {
                    "role": "user",
                    "content": f"Вопрос: {task_prompt}\n\nДанные:\n{safe_stdout}"
                }
            ]
            try:
                final_resp = client.chat_completion(
                    messages=synth_messages,
                    model=target_model,
                    temperature=architect_temp,
                    stream=False,
                    emit_signal=False,
                    enable_thinking=effective_thinking
                )
            except TypeError:
                try:
                    final_resp = client.chat_completion(
                        messages=synth_messages,
                        temperature=architect_temp,
                        stream=False,
                        emit_signal=False,
                        enable_thinking=effective_thinking
                    )
                except TypeError:
                    final_resp = client.chat_completion(messages=synth_messages, temperature=architect_temp, stream=False, emit_signal=False)
            clean_synth = TextRegexFilter.strip_thought_blocks(ToolEngine.strip_tool_tags(final_resp or "")).strip()
            return {
                "success": True,
                "artifact_path": None,
                "file_name": "",
                "stdout": stdout,
                "stderr": stderr,
                "summary": clean_synth
            }

        # 7. Надежное определение созданного файла
        match = re.search(r'OUTPUT_FILE:\s*([^\r\n]+)', stdout)
        if match:
            raw_p = Path(match.group(1).strip().strip("'\"`"))
            cand_p = raw_p if raw_p.is_absolute() else (SandboxManager.get_workspace_dir() / raw_p).resolve()
            if cand_p.exists() and cand_p.is_file():
                artifact_path = cand_p

        if not artifact_path and run_res and hasattr(run_res, "artifact") and run_res.artifact:
            p_art = Path(run_res.artifact)
            if p_art.exists():
                artifact_path = p_art.resolve()

        # Резервный поиск из исходного кода скрипта
        if not artifact_path:
            script_code = ""
            for fn, cnt in write_blocks:
                if fn == script_to_run:
                    script_code = cnt
                    break
            if not script_code and write_blocks:
                script_code = write_blocks[0][1]

            if script_code:
                doc_pattern = r'["\']([^"\'\r\n]+\.(?:xlsx|docx|pdf|csv|json|txt|html|png|jpg|pptx|xlsm))["\']'
                candidates = re.findall(doc_pattern, script_code, re.IGNORECASE)
                for cand in candidates:
                    cand_clean = cand.strip()
                    if cand_clean.lower().endswith((".py", ".pyw")):
                        continue
                    p_cand = Path(cand_clean)
                    if p_cand.is_absolute() and p_cand.exists() and p_cand.is_file():
                        artifact_path = p_cand.resolve()
                        break
                    ws_cand = (SandboxManager.get_workspace_dir() / p_cand.name).resolve()
                    if ws_cand.exists() and ws_cand.is_file():
                        artifact_path = ws_cand
                        break

        # 8. Гарантированная привязка карточки файла для целевого артефакта
        if not artifact_path and target_artifact:
            target_p = (SandboxManager.get_workspace_dir() / target_artifact).resolve()
            if target_p.exists() and target_p.is_file():
                artifact_path = target_p

        if not artifact_path and script_to_run:
            py_path = (SandboxManager.get_workspace_dir() / script_to_run).resolve()
            if py_path.exists() and py_path.is_file():
                artifact_path = py_path

        file_name = artifact_path.name if artifact_path else (target_artifact or script_to_run or "")
        clean_summary = ToolEngine.strip_tool_tags(response_text).strip() if hasattr(ToolEngine, "strip_tool_tags") else response_text.strip()
        # Исключаем простыни кода из центрального чата
        clean_summary = re.sub(r'```(?:python|py)?[\s\S]*?```', '', clean_summary).strip()

        # Формирование информативного summary при пустом ответе модели или наличии консольного вывода
        if not clean_summary:
            if verification_passed and self.verification_script:
                clean_summary = f"Скрипт верификации `{self.verification_script}` успешно выполнен. Артефакт `{file_name}` верифицирован."
            elif stdout and stdout.strip():
                clean_summary = f"Скрипт `{file_name}` успешно выполнен.\n\n**Вывод консоли:**\n```text\n{stdout.strip()}\n```"
            else:
                clean_summary = f"Скрипт `{file_name}` успешно скомпилирован и выполнен."
        else:
            if stdout and stdout.strip():
                clean_out = stdout.strip()
                if clean_out not in clean_summary:
                    if "успешно выполнен" not in clean_summary:
                        clean_summary = f"{clean_summary}\n\nСкрипт `{file_name}` успешно выполнен.\n\n**Вывод консоли:**\n```text\n{clean_out}\n```"
                    else:
                        clean_summary = f"{clean_summary}\n\n**Вывод консоли:**\n```text\n{clean_out}\n```"
            elif "успешно выполнен" not in clean_summary and "успешно скомпилирован" not in clean_summary:
                clean_summary = f"{clean_summary}\n\nСкрипт `{file_name}` успешно выполнен."

        return {
            "success": True,
            "artifact_path": artifact_path,
            "file_name": file_name,
            "stdout": stdout,
            "stderr": stderr,
            "summary": clean_summary,
            "artifact_format": self.artifact_format,
            "artifact_type": self.artifact_type,
            "target_artifact": target_artifact,
            "verification_script": self.verification_script,
            "verification_passed": verification_passed
        }


# Re-exports на уровне модуля для 100% обратной совместимости
_extract_pure_py = SubagentEngineer._extract_pure_py
_diagnose_error = diagnose_runtime_error
_extract_error_fingerprint = extract_error_fingerprint
_validate_python_syntax = CodeValidator.validate_python_syntax
_validate_code_integrity = CodeValidator.validate_code_integrity
_apply_ast_linter_to_code = CodeValidator.apply_ast_linter_to_code
scan_installed_libraries = scan_installed_libraries
