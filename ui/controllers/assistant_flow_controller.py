"""
Assistant Flow & Turn Generation Controller (Mixin) for NeonRoute MainWindow.
Handles Elaria dialogue dispatch, 14K context management, VRAM headroom adaptation,
LanceDB passive memory extraction, and Visual Engine protocol callbacks.
"""

import re
import random
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Union

from services.session_manager import SessionManager, get_mode_profile
from services.token_budget_manager import TokenBudgetManager
from services.regex_filter import TextRegexFilter
from services.task_queue import InferenceTask
from services.model_router import ModelRouter, normalize_model_choice
from services.art_director import (
    VisualIntent,
    TargetSubject,
    parse_image_prompt_flags,
    detect_elaria_self_portrait_intent,
)
from services.assistant_prompt_builder import AssistantPromptBuilder
from services.assistant_flow import (
    RE_IMAGE_TAG,
    RE_GALLERY_TRIGGER,
    build_assistant_visual_intent,
    prepare_assistant_art_dispatch,
    resolve_gallery_art_temperature,
    sanitize_assistant_art_and_markdown,
    sanitize_messages_for_memory,
    should_trigger_assistant_art,
    _build_pending_knowledge_sources,
    run_assistant_inference_deferred,
)
from services.document_parser import DocumentParser, ParsedDocument
from services.thought_stream_parser import ThoughtStreamParser, ThinkParserState
from ui.components.chat_bubble import ChatMessageWidget

logger = logging.getLogger("NeonRoute.AssistantFlow")


class AssistantFlowControllerMixin:
    """Изолированный контроллер диалогового цикла и логики режима «💬 Ассистент» (Элария)."""

    def process_assistant_attachments(
        self,
        attached_files: List[Union[str, Path]],
        user_text: str = ""
    ) -> Tuple[List[Path], List[Path], str, List[Dict[str, Any]]]:
        """
        Разделяет вложения на image_files и document_files.
        Парсит документы через DocumentParser на CPU, формирует контекст
        через AssistantPromptBuilder.build_document_context и готовит метаданные.

        :return: (image_files, document_files, full_user_query, docs_meta)
        """
        image_exts = {".png", ".jpg", ".jpeg", ".webp"}
        image_files: List[Path] = []
        document_files: List[Path] = []

        for f in attached_files:
            p = Path(f) if not isinstance(f, Path) else f
            if not p.exists():
                continue
            ext = p.suffix.lower()
            if ext in image_exts:
                image_files.append(p)
            elif DocumentParser.is_supported(p):
                document_files.append(p)

        doc_blocks: List[str] = []
        docs_meta: List[Dict[str, Any]] = []

        for p in document_files:
            parsed = DocumentParser.parse_file(p)
            if parsed:
                doc_blocks.append(AssistantPromptBuilder.build_document_context(parsed))
                docs_meta.append({
                    "filename": parsed.filename,
                    "extension": parsed.extension,
                    "summary": parsed.summary_header,
                    "is_truncated": parsed.is_truncated,
                    "size_kb": parsed.size_kb,
                    "page_count": parsed.page_count
                })
                logger.info(f"[AssistantFlow] Документ '{parsed.filename}' ({parsed.summary_header}) успешно извлечен и инжектирован.")

        # Очистка вложений в UI (input_panel.clear_attachments())
        if hasattr(self, "input_panel") and hasattr(self.input_panel, "clear_attachments"):
            self.input_panel.clear_attachments()
        elif hasattr(self, "attached_files"):
            self.attached_files.clear()
            if hasattr(self, "_update_attachments_ui"):
                self._update_attachments_ui()

        # Формирование итогового текста запроса пользователя
        clean_text = user_text.strip() if user_text else ""
        if doc_blocks:
            doc_context_str = "\n\n".join(doc_blocks)
            if clean_text:
                full_query = f"{clean_text}\n\n{doc_context_str}"
            else:
                full_query = f"Изучи прикрепленный документ и предоставь краткую выжимку его сути и содержания.\n\n{doc_context_str}"
        else:
            full_query = clean_text

        return image_files, document_files, full_query, docs_meta

    def handle_assistant_user_submit(
        self,
        text: Optional[str] = None,
        attached_files: Optional[List[Union[str, Path]]] = None
    ) -> bool:
        """
        Обработчик отправки сообщения пользователя в режиме «Ассистент».
        Считывает attached_files из ChatInputPanel, разделяет на изображения и документы,
        парсит документы через DocumentParser, инжектирует <AttachedDocument> в запрос LLM,
        очищает input_panel и сохраняет метаданные в сессии.
        """
        if text is not None and hasattr(self, "input_box") and self.input_box:
            self.input_box.setPlainText(text)

        raw_text = text if text is not None else (
            self.input_box.toPlainText().strip() if hasattr(self, "input_box") and self.input_box else ""
        )

        files_to_process: List[Union[str, Path]] = []
        if attached_files is not None:
            files_to_process = list(attached_files)
        elif hasattr(self, "input_panel") and hasattr(self.input_panel, "attached_files"):
            files_to_process = list(self.input_panel.attached_files)
        elif hasattr(self, "attached_files"):
            files_to_process = list(self.attached_files)

        if not raw_text and not files_to_process:
            return False

        if hasattr(self, "input_box") and self.input_box:
            self.input_box.clear()

        image_files, document_files, full_query, docs_meta = self.process_assistant_attachments(
            files_to_process, raw_text
        )

        self._current_user_request = raw_text or (full_query if not image_files else "Опиши изображение")

        # Обработка Vision-ветки с картинками
        vision_content: Optional[List[Dict[str, Any]]] = None
        primary_image = image_files[0] if image_files else None
        if primary_image:
            from services.vision_utils import VisionUtils
            image_payload = VisionUtils.prepare_image_payload(str(primary_image), max_dim=768)
            vision_content = [
                {"type": "text", "text": full_query or "Опиши изображение"},
                image_payload
            ]

        # Отображение сообщения пользователя в UI
        display_text = raw_text
        if not display_text:
            if image_files:
                display_text = "🖼️ [Прикреплено изображение]"
            elif document_files and docs_meta:
                display_text = f"📄 [{docs_meta[0]['summary']}]"
            else:
                display_text = "📄 [Прикреплен файл]"

        if hasattr(self, "_append_message"):
            self._append_message(
                "Вы",
                display_text,
                is_user=True,
                image_path=primary_image,
                file_paths=document_files or image_files
            )

        # Сохранение в chat_history
        user_history_entry = {
            "role": "user",
            "content": vision_content if vision_content else full_query
        }
        if hasattr(self, "chat_history") and isinstance(self.chat_history, list):
            self.chat_history.append(user_history_entry)

        # Сохранение метаданных прикрепленного документа в сессию
        if hasattr(self, "current_session") and isinstance(self.current_session, dict):
            if "messages" not in self.current_session:
                self.current_session["messages"] = []
            msg_meta: Dict[str, Any] = {
                "role": "user",
                "content": raw_text or full_query,
                "is_user": True,
            }
            if docs_meta:
                msg_meta["attached_documents"] = docs_meta
            if primary_image:
                msg_meta["image_path"] = str(primary_image)
            self.current_session["messages"].append(msg_meta)

            if hasattr(self, "_save_current_session"):
                self._save_current_session()

        # Запуск задачи LLM
        if hasattr(self, "_enqueue_assistant_llm_task"):
            self._enqueue_assistant_llm_task(
                vision_content=vision_content
            )
        return True

    @staticmethod
    def _should_trigger_assistant_art(user_query: str) -> bool:
        """Проверяет наличие явных запросов на визуализацию для Эларии (без ролевых триггеров)."""
        return should_trigger_assistant_art(user_query)

    @staticmethod
    def _get_ephemeral_art_directive(target_model_id: str = "realistic", target_model_desc: str = "", is_roleplay: bool = False) -> str:
        model_hint = f"{target_model_id} ({target_model_desc})" if target_model_desc else target_model_id

        return (
            f"\n\n[SYSTEM DIRECTIVE: Image Generation for Stable Diffusion]\n"
            f"The user has requested an image. Act as an expert prompt engineer for Stable Diffusion.\n"
            f"Target model / aesthetic profile: {model_hint}.\n\n"
            f"Prompt Engineering Rules:\n"
            f"1. Tag Language: STRICTLY English.\n"
            f"2. Structure: Dense comma-separated keywords and short phrases (NO full sentences or conversational filler).\n"
            f"3. Word Order Priority:\n"
            f"   [Main subject & action] -> [Environment & location] -> [Lighting & mood] -> [Style & medium] -> [Camera angle / framing] -> [Quality modifiers].\n"
            f"4. Density: 35–60 evocative Danbooru/SD tags without fluff.\n"
            f"5. Framing Flag: Always append one aspect ratio flag to the end:\n"
            f"   --ar wide (landscapes, rooms, sceneries, architecture, vehicles)\n"
            f"   --ar portrait (standing characters, full body, outfits, vertical interactions/embrace)\n"
            f"   --ar square (face close-ups, selfies, busts, single small items)\n"
            f"6. Do NOT write negative prompts (injected automatically).\n\n"
            f"Output Format:\n"
            f"1. Respond to the user warmly and concisely in RUSSIAN (1-2 natural conversational sentences).\n"
            f"2. Output the generation tag strictly on a new line at the very end in this format:\n"
            f"[IMAGE_PROMPT: <comma-separated SD positive tags> --ar <wide|portrait|square> --model {target_model_id}]\n\n"
            f"Example ending:\n"
            f"Секунду, перевожу образ в кадр...\n"
            f"[IMAGE_PROMPT: 1girl, female samurai, holding katana, detailed traditional armor with cyberpunk accents, neo-tokyo street, night, heavy rain, wet pavement, puddles with neon reflections, dramatic rim light, cinematic lighting, upper body shot, photorealistic, 8k uhd, sharp focus --ar portrait --model {target_model_id}]"
        )

    def _run_assistant_inference_deferred(self, *args, **kwargs):
        """Прокси к run_assistant_inference_deferred с передачей self в качестве controller."""
        kwargs.setdefault("controller", self)
        if "lm_client" not in kwargs and hasattr(self, "lm_client"):
            kwargs["lm_client"] = self.lm_client
        return run_assistant_inference_deferred(*args, **kwargs)

    def _enqueue_assistant_llm_task(
        self,
        is_excel: bool = False,
        is_word: bool = False,
        vision_content: Optional[List[Dict[str, Any]]] = None,
        art_guidance: Optional[str] = None,
        enable_reasoning: Optional[bool] = None
    ):
        """Формирует контекст Ассистента и передает задачу в TaskQueue с отложенной сборкой (Deferred RAG)."""
        self._requested_word_export = is_word
        self._requested_excel_export = is_excel

        if art_guidance is None:
            art_guidance = getattr(self, "_pre_routed_art_guidance", None)

        model_name = None
        if hasattr(self, "lm_combo") and self.lm_combo:
            model_name = self.lm_combo.currentText().strip()
        if not model_name:
            model_name = (
                getattr(self, "active_lms_model", "")
                or (self.current_session.get("model_id") if getattr(self, "current_session", None) else "")
                or getattr(self.lm_client, "_last_loaded_model_id", None)
                or ""
            )
        if model_name.startswith("default"):
            model_name = None

        _req = getattr(self, "_current_user_request", "")
        if not _req and hasattr(self, "chat_history") and self.chat_history:
            for _h in reversed(self.chat_history):
                if _h.get("role") == "user":
                    _req = str(_h.get("content", ""))
                    break

        _should_art = False

        mode_profile = get_mode_profile("assistant")
        base_history_limit = mode_profile.get("history_limit", 60)

        # ── ДИНАМИЧЕСКИЙ РАСЧЕТ БЮДЖЕТА ПАМЯТИ И ТОКЕНОВ (ДВУХКОНТУРНАЯ МОДЕЛЬ) ──
        if _should_art:
            effective_history_limit = 6
            effective_max_tokens = 512
        else:
            free_vram = None
            if hasattr(self, "system_monitor") and self.system_monitor:
                try:
                    free_vram = self.system_monitor.get_free_vram_mb()
                except Exception:
                    free_vram = None

            prompt_tokens = TokenBudgetManager.estimate_messages_tokens(self.chat_history)
            if _req and (not self.chat_history or self.chat_history[-1].get("content") != _req):
                prompt_tokens += TokenBudgetManager.estimate_text_tokens(_req) + 5

            decision = TokenBudgetManager.calculate_context_budget(
                current_prompt_tokens=prompt_tokens,
                mode="assistant",
                free_vram_mb=free_vram
            )
            effective_history_limit = decision.history_limit
            effective_max_tokens = decision.max_tokens

        curr_sess = getattr(self, "current_session", None) or {}
        curr_sess_id = curr_sess.get("id") if isinstance(curr_sess, dict) else (self.current_session.get("id") if self.current_session else None)
        sess_persona_id = curr_sess.get("user_persona_id") if isinstance(curr_sess, dict) else (self.current_session.get("user_persona_id") if self.current_session else None)
        active_p = SessionManager.get_active_user_persona(is_roleplay=False, session_persona_id=sess_persona_id)
        effective_user_name = active_p.get("name") or "Пользователь"

        # ── ДВУХУРОВНЕВЫЙ СЭМПЛИНГ С ЗАЩИТОЙ ОТ МУТАЦИЙ РЕЖИМОВ ──
        sampling = (self.current_session.get("sampling_params") or {}) if self.current_session else {}
        is_custom = bool(self.current_session.get("is_custom_sampling", False)) if self.current_session else False

        cfg_defaults = getattr(self, "app_config", {}).get("default_sampling_assistant", {})
        prof_sampling = mode_profile.get("sampling", {})

        default_temp = cfg_defaults.get("temperature", prof_sampling.get("temperature", 0.70))
        default_min_p = cfg_defaults.get("min_p", prof_sampling.get("min_p", 0.05))
        default_top_p = cfg_defaults.get("top_p", prof_sampling.get("top_p", 0.95))
        default_rep_pen = cfg_defaults.get("repeat_penalty", prof_sampling.get("repeat_penalty", 1.05))
        default_max_tok = cfg_defaults.get("max_tokens", prof_sampling.get("max_tokens", 1024))
        default_dry = cfg_defaults.get("dry_multiplier", prof_sampling.get("dry_multiplier", 0.80))
        default_xtc_prob = cfg_defaults.get("xtc_probability", prof_sampling.get("xtc_probability", 0.20))
        default_xtc_thresh = cfg_defaults.get("xtc_threshold", prof_sampling.get("xtc_threshold", 0.10))

        if is_custom and "temperature" in sampling:
            temp = float(sampling.get("temperature", default_temp))
            min_p = float(sampling.get("min_p", default_min_p))
            top_p = float(sampling.get("top_p", default_top_p))
            rep_pen = float(sampling.get("repeat_penalty", default_rep_pen))
            user_max_tokens = sampling.get("max_tokens", default_max_tok)
            dry_mult = float(sampling.get("dry_multiplier", default_dry))
            xtc_prob = float(sampling.get("xtc_probability", default_xtc_prob))
            xtc_thresh = float(sampling.get("xtc_threshold", default_xtc_thresh))
            source_tag = f"Кастомный чат (PKM) [ID: {curr_sess_id}]"
        else:
            temp = float(default_temp)
            min_p = float(default_min_p)
            top_p = float(default_top_p)
            rep_pen = float(default_rep_pen)
            user_max_tokens = default_max_tok
            dry_mult = float(default_dry)
            xtc_prob = float(default_xtc_prob)
            xtc_thresh = float(default_xtc_thresh)
            source_tag = "Глобальный дефолт config.json (или профиль режима)"

        _model_lower = str(model_name or "").lower()
        _is_thinking_model = any(m in _model_lower for m in ("r1", "qwq", "qwen3", "think", "reasoning"))
        if _is_thinking_model and not is_custom:
            xtc_prob = 0.0
            if effective_max_tokens == 1024:
                effective_max_tokens = 2048

        # Определение активного скилла для точечного сэмплинга
        sess_meta = curr_sess.get("metadata", {}) if isinstance(curr_sess, dict) else {}
        active_skill = (
            getattr(self, "active_assistant_skill", None)
            or (sess_meta.get("active_assistant_skill") if isinstance(sess_meta, dict) else None)
        )
        if not active_skill or active_skill == "auto":
            from services.assistant_prompt_builder import AssistantPromptBuilder
            builder = AssistantPromptBuilder.get_instance()
            if builder and hasattr(builder, "router") and builder.router and _req:
                routed_skill, _, _ = builder.router.route(_req)
                if routed_skill:
                    active_skill = routed_skill

        from services.assistant_flow import resolve_effective_thinking
        enable_thinking = resolve_effective_thinking(
            mode="assistant",
            model_name=model_name,
            active_skill=active_skill,
            explicit_override=enable_reasoning
        )

        # Точечный override строго на время генерации SDXL (gallery) в Фазе 1
        if active_skill == "gallery":
            temp = 0.4
            enable_thinking = False

        if _should_art:
            target_max_tokens = 512
        elif not is_custom or "max_tokens" not in sampling:
            target_max_tokens = effective_max_tokens
        else:
            target_max_tokens = min(int(user_max_tokens), effective_max_tokens)
        target_ctx_len = mode_profile.get("context_length", 14336)

        logger.info(
            f"[SamplingResolve] Режим 'assistant' | Сессия: {curr_sess_id} | "
            f"Источник: {source_tag} -> temp={temp}, min_p={min_p}, top_p={top_p}, dry_multiplier={dry_mult}, xtc_prob={xtc_prob}, xtc_thresh={xtc_thresh}, ctx={target_ctx_len}, max_tokens={target_max_tokens}"
        )

        llm_kwargs = {
            "user_prompt": _req,
            "prompt": _req,
            "session_id": curr_sess_id,
            "messages": None,
            "model": model_name,
            "temperature": temp,
            "stop": ["\n\nПользователь:", "\n\nUser:"],
            "is_roleplay": False,
            "is_engineer": False,
            "context_length": target_ctx_len,
            "max_tokens": target_max_tokens,
            "min_p": min_p,
            "top_p": top_p,
            "repetition_penalty": rep_pen,
            "repeat_penalty": rep_pen,
            "dry_multiplier": dry_mult,
            "dry_base": 1.75,
            "dry_allowed_length": 2,
            "dry_penalty_last_n": 0,
            "xtc_probability": xtc_prob,
            "xtc_threshold": xtc_thresh,
            "stream": True,
            "enable_thinking": enable_thinking,
            "is_excel": is_excel,
            "is_word": is_word,
            "vision_content": vision_content,
            "art_guidance": art_guidance,
            "enable_reasoning": enable_reasoning,
            "history_limit": effective_history_limit,
            "effective_user_name": effective_user_name,
            "session_persona_id": sess_persona_id,
            "selected_forge_checkpoint": getattr(self, "selected_forge_checkpoint", None),
            "controller": self,
            "lm_client": self.lm_client
        }

        task = InferenceTask(
            name="LLM Ответ",
            task_type="llm",
            func=self._run_assistant_inference_deferred,
            kwargs=llm_kwargs,
            abort_fn=self.lm_client.abort,
            metadata={
                "is_excel": is_excel,
                "is_word": is_word,
                "is_roleplay": False,
                "has_vision": bool(vision_content),
                "knowledge_sources": getattr(self, "_pending_knowledge_sources", []) or []
            }
        )

        logger.info(f"[AssistantFlow] Задача инференса поставлена в очередь (Deferred RAG) [ID: {task.id}]")
        self._active_tasks[task.id] = task
        # Инициализация потокового парсера мыслей <think>...</think>
        self._thought_stream_parser = ThoughtStreamParser()
        self.is_busy = True
        if hasattr(self, "_stream_flush_timer") and self._stream_flush_timer and not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start()
        self.task_queue.enqueue(task)

    def _dispatch_art_to_forge(self, extracted_image_prompt: str, target_widget: Any, raw_user_query: str):
        if not extracted_image_prompt:
            return

        if target_widget is None and hasattr(self, "chat_layout"):
            for i in reversed(range(self.chat_layout.count())):
                item = self.chat_layout.itemAt(i)
                w = item.widget() if item else None
                if w and getattr(w, "is_user", None) is False:
                    target_widget = w
                    break

        # Извлечение исходного запроса пользователя
        last_req = (raw_user_query or "").strip()
        if not last_req:
            last_req = getattr(self, "_current_user_request", "").strip()
        if not last_req and hasattr(self, "chat_history") and self.chat_history:
            for _h in reversed(self.chat_history):
                if _h.get("role") == "user":
                    last_req = str(_h.get("content", "")).strip()
                    break

        cur_sess = getattr(self, "current_session", None)
        sess_id = cur_sess.get("id") if isinstance(cur_sess, dict) else None
        last_meta = getattr(self, "_last_art_meta", None) or None

        v_intent, effective_style, _is_self, target_char = prepare_assistant_art_dispatch(
            extracted_image_prompt=extracted_image_prompt,
            user_query=last_req,
            selected_forge_checkpoint=getattr(self, "selected_forge_checkpoint", None),
            session=cur_sess if isinstance(cur_sess, dict) else None,
            last_art_meta=last_meta,
            session_id=sess_id,
        )
        raw_prompt = v_intent.prompt
        if target_widget is not None:
            target_widget.visual_intent = v_intent

        if hasattr(self, "pill_forge") and self.pill_forge.is_online:
            self._on_scene_art_requested(
                visual_intent=v_intent,
                widget=target_widget,
                preferred_style=effective_style,
                character_name=target_char
            )
        elif target_widget is not None and hasattr(target_widget, "add_art_proposal_chip"):
            target_widget.add_art_proposal_chip(
                raw_prompt,
                lambda v=v_intent, w=target_widget, s=effective_style, c=target_char: self._on_scene_art_requested(
                    visual_intent=v, widget=w, preferred_style=s, character_name=c
                )
            )

    def _handle_assistant_art_completed(self, task: Any, result: Dict[str, Any]) -> bool:
        """
        Изолированная обработка завершения генерации арта для режима «💬 Ассистент» (Элария).
        Выполняет INLINE-монтаж изображения в исходный бабл ответа, скрывает лоадер и синхронизирует сессию.
        """
        if not isinstance(result, dict) or "image_path" not in result:
            return False

        raw_path = result.get("image_path")
        img_path = Path(raw_path) if raw_path else None
        if not img_path or not img_path.exists():
            logger.warning("[AssistantFlow] Сгенерированное изображение отсутствует на диске: %s", raw_path)
            t_widget = task.metadata.get("target_widget") if (task and getattr(task, "metadata", None)) else None
            if t_widget:
                if hasattr(t_widget, "hide_art_loading_placeholder"):
                    t_widget.hide_art_loading_placeholder()
                if hasattr(t_widget, "set_scene_art_busy"):
                    t_widget.set_scene_art_busy(False)
            return True

        gen_params = self._extract_generation_params(result) if hasattr(self, "_extract_generation_params") else None
        plan = result.get("plan")
        sender_name = "Элария"

        # Сохранение параметров последней генерации (Seed Lock и метаданные)
        if gen_params:
            last_seed = gen_params.get("seed", -1)
            last_prompt = getattr(self, "_last_art_prompt", "") or (task.metadata.get("prompt") if (task and getattr(task, "metadata", None)) else "")
            last_meta = {
                "seed": last_seed,
                "prompt": last_prompt,
                "positive_prompt": gen_params.get("positive_prompt", ""),
                "checkpoint": gen_params.get("checkpoint", "")
            }
            self._last_art_meta = last_meta
            if getattr(self, "current_session", None) and isinstance(self.current_session, dict):
                self.current_session["last_generation"] = last_meta

        # Поиск целевого бабла (target_widget с фолбэком на chat_layout)
        target_widget = task.metadata.get("target_widget") if (task and getattr(task, "metadata", None)) else None
        widget_to_update = None
        if target_widget:
            try:
                _ = target_widget.objectName()
                widget_to_update = target_widget
            except (RuntimeError, AttributeError):
                widget_to_update = None

        if widget_to_update is None and hasattr(self, "chat_layout") and self.chat_layout:
            for i in reversed(range(self.chat_layout.count())):
                item = self.chat_layout.itemAt(i)
                w = item.widget() if item else None
                if w and isinstance(w, ChatMessageWidget) and not getattr(w, "is_user", True):
                    widget_to_update = w
                    break

        if widget_to_update is not None:
            try:
                # 1. Снятие состояния ожидания и скрытие лоадера
                if hasattr(widget_to_update, "hide_art_loading_placeholder"):
                    widget_to_update.hide_art_loading_placeholder()
                if hasattr(widget_to_update, "set_scene_art_busy"):
                    widget_to_update.set_scene_art_busy(False)

                # Защита от сервисной заглушки связи над готовым артом
                current_bubble_text = ""
                if hasattr(widget_to_update, "get_text"):
                    current_bubble_text = widget_to_update.get_text().strip()
                elif hasattr(widget_to_update, "text"):
                    t_val = widget_to_update.text
                    current_bubble_text = t_val().strip() if callable(t_val) else str(t_val).strip()

                if current_bubble_text == "Связь с моделью обновлена. Я готова, о чём поговорим?":
                    if hasattr(widget_to_update, "set_text"):
                        widget_to_update.set_text("")

                # 2. Монтаж картинки в контейнер бабла
                if hasattr(widget_to_update, "set_image"):
                    widget_to_update.set_image(str(img_path))
                elif hasattr(widget_to_update, "attach_file"):
                    widget_to_update.attach_file(img_path, generation_params=gen_params)

                # 3. Передача negative_prompt и generation_params
                neg_prompt = (getattr(plan, "negative_prompt", None) if plan else None) or (gen_params.get("negative_prompt") if gen_params else None)
                if neg_prompt and hasattr(widget_to_update, "set_negative_prompt"):
                    widget_to_update.set_negative_prompt(neg_prompt)
                if gen_params and hasattr(widget_to_update, "set_generation_params"):
                    widget_to_update.set_generation_params(gen_params)

                # 4. Обновление вложений в БД/истории
                if hasattr(self, "_update_message_attachment"):
                    self._update_message_attachment(widget_to_update, img_path, generation_params=gen_params)
            except RuntimeError:
                widget_to_update = None

        # 5. Аварийный вывод, если бабл не найден
        if widget_to_update is None:
            avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None
            neg = (getattr(plan, "negative_prompt", None) if plan else None) or (gen_params.get("negative_prompt") if gen_params else None)
            self._append_message(
                sender_name,
                "",
                is_user=False,
                avatar_path=avatar,
                image_path=img_path,
                negative_prompt=neg,
                generation_params=gen_params
            )

        # 6. Синхронизация сессии
        if getattr(self, "current_session", None) and isinstance(self.current_session, dict):
            messages = self.current_session.get("messages", [])
            if isinstance(messages, list) and messages:
                for m in reversed(messages):
                    if not m.get("is_user") and m.get("role") in ("assistant", None):
                        m["image_path"] = str(img_path)
                        if m.get("content") == "Связь с моделью обновлена. Я готова, о чём поговорим?":
                            m["content"] = ""
                        break
            if hasattr(self, "_save_current_session"):
                self._save_current_session()

        return True

    def _enqueue_gallery_art_pass(self, user_query: str):
        best_model_id = "default"
        best_desc = ""
        target_arch = None
        try:
            best_model_id, target_arch, best_desc, _ = AssistantPromptBuilder.resolve_gallery_target_profile(
                effective_query=user_query,
                selected_forge_checkpoint=getattr(self, "selected_forge_checkpoint", None),
                check_config_fallback=False,
            )
        except Exception as e:
            logger.warning(f"Failed to resolve target gallery model: {e}")
            best_model_id = "default"

        full_prompt = AssistantPromptBuilder.build_gallery_art_prompt(
            user_query=user_query,
            target_model_id=best_model_id,
            target_model_desc=best_desc,
            architecture=target_arch
        )
        messages = [{"role": "user", "content": full_prompt}]

        model_name = None
        if hasattr(self, "lm_combo") and self.lm_combo:
            model_name = self.lm_combo.currentText().strip()
            if model_name.startswith("default"):
                model_name = None

        session = getattr(self, "current_session", {}) or {}
        session_sampling = session.get("sampling_params") if isinstance(session, dict) and isinstance(session.get("sampling_params"), dict) else {}
        custom_art_temp = session_sampling.get("art_temperature") or session_sampling.get("temperature")
        if custom_art_temp is not None:
            try:
                target_temperature = float(custom_art_temp)
            except (TypeError, ValueError):
                target_temperature = 0.4
        else:
            target_temperature = 0.4

        logger.info(
            f"[AssistantFlow] Starting Gallery Art Pass for query: '{user_query}' "
            f"(target_model='{best_model_id}', temperature={target_temperature}, enable_thinking=False)"
        )

        llm_kwargs = {
            "messages": messages,
            "model": model_name,
            "temperature": target_temperature,
            "enable_thinking": False,
            "stop": ["\n\nПользователь:", "\n\nUser:"],
            "is_roleplay": False,
            "is_engineer": False,
            "context_length": 4096,
            "max_tokens": 2000,
            "stream": False
        }

        task = InferenceTask(
            name="Gallery Art Pass",
            task_type="llm",
            func=self.lm_client.chat_completion,
            kwargs=llm_kwargs,
            abort_fn=self.lm_client.abort,
            metadata={
                "silent": True,
                "is_gallery_art_pass": True,
                "is_roleplay": False
            }
        )

        if hasattr(self, "_active_tasks") and isinstance(self._active_tasks, dict):
            self._active_tasks[task.id] = task
        if hasattr(self, "task_queue") and self.task_queue:
            self.task_queue.enqueue(task)

    def _handle_assistant_llm_response(self, text: str):
        """Обрабатывает ответ Эларии, парсит арт-протокол, Ambiguity Gate, мысли <think> и инициирует память."""
        # 0. Сброс состояния занятости и остановка таймера стриминга
        self.is_busy = False
        if hasattr(self, "_stream_flush_timer") and self._stream_flush_timer and self._stream_flush_timer.isActive():
            self._stream_flush_timer.stop()

        # ── ВНЕОЧЕРЕДНОЙ ПЕРЕХВАТ 0: Служебный Gallery Art Pass (Zero Pollution) ──
        # Фоновый синтез промпта SD не должен загрязнять или перезаписывать бабл чата!
        if getattr(self, "_is_running_gallery_art_pass", False):
            self._is_running_gallery_art_pass = False
            target_widget = getattr(self, "_pending_gallery_art_widget", None)
            user_query = getattr(self, "_pending_gallery_user_query", "")
            self._pending_gallery_art_widget = None
            self._pending_gallery_user_query = None

            extracted, _ = TextRegexFilter.extract_image_prompt(
                text,
                strip_thoughts=True,
                allow_markdown_fallback=True,
            )
            if extracted:
                self._dispatch_art_to_forge(extracted, target_widget, user_query)
            else:
                if target_widget and hasattr(target_widget, "hide_art_loading_placeholder"):
                    target_widget.hide_art_loading_placeholder()
            return

        # 1. Финализация потокового парсера мыслей <think>...</think>
        thought_data = None
        parser_normal_text = ""
        if getattr(self, "_thought_stream_parser", None) is not None:
            parser_normal_text = getattr(self._thought_stream_parser, "normal_text", "").strip()
            rem_events = self._thought_stream_parser.finalize()
            if hasattr(self, "_turn_bubble") and self._turn_bubble:
                for ev_type, ev_data in rem_events:
                    if ev_type == "thought_chunk" and hasattr(self._turn_bubble, "append_thought_chunk"):
                        self._turn_bubble.append_thought_chunk(ev_data)
                    elif ev_type == "end_think" and hasattr(self._turn_bubble, "finalize_thought"):
                        self._turn_bubble.finalize_thought(
                            self._thought_stream_parser.duration,
                            self._thought_stream_parser.token_count
                        )
                    elif ev_type == "normal_chunk" and ev_data:
                        parser_normal_text += ev_data
            thought_data = self._thought_stream_parser.get_thought_data()
            # Финально обновляем накопленный normal_text после finalize()
            parser_normal_text = getattr(self._thought_stream_parser, "normal_text", "").strip() or parser_normal_text

        # ── [HistorySanitizer] Санитайзер истории и тегов рассуждений ──
        # Фаза 1: Спасение мыслей в thought_data (Rescue to UI spoiler, включая множественные и неявные теги)
        # Маскируем блоки Markdown-кода, чтобы упоминания <thought>/<think> внутри `...` не резали ответ
        re_thought_body = r"<(think|analyze|thought|reasoning)>([\s\S]*?)(?:</\1>|$)"
        re_thought_clean = r"<(think|analyze|thought|reasoning)>[\s\S]*?(?:</\1>|$)"
        re_implicit_close = r"^([\s\S]*?)</(think|analyze|thought|reasoning)>"
        masked_thought_src, thought_placeholders = TextRegexFilter.mask_markdown_code(text)
        # Защита от артефакта повторного/одинокого закрывающего тега в самом начале ответа
        masked_thought_src = re.sub(r"^\s*</(think|analyze|thought|reasoning)>\s*", "", masked_thought_src, flags=re.IGNORECASE)

        if thought_data is None:
            matches = list(re.finditer(re_thought_body, masked_thought_src, flags=re.IGNORECASE))
            if matches:
                th_parts = [
                    TextRegexFilter.restore_markdown_code(m.group(2), thought_placeholders).strip()
                    for m in matches
                ]
                th_parts = [p for p in th_parts if p]
                th_body = "\n\n".join(th_parts)
                tag_name = matches[0].group(1).lower()
                if th_body:
                    thought_data = {
                        "content": th_body,
                        "duration": round(getattr(self._thought_stream_parser, "duration", 0.0), 2),
                        "token_count": max(1, len(th_body) // 4),
                        "tag": tag_name
                    }
                    if hasattr(self, "_turn_bubble") and self._turn_bubble and hasattr(self._turn_bubble, "finalize_thought"):
                        self._turn_bubble.finalize_thought(
                            thought_data["duration"],
                            thought_data["token_count"]
                        )
                    logger.info(f"[HistorySanitizer] Спасён блок мыслей из тега <{tag_name}> ({len(th_body)} симв.) и направлен в UI спойлер.")
            else:
                imp_match = re.search(re_implicit_close, masked_thought_src, flags=re.IGNORECASE)
                if imp_match:
                    th_body = TextRegexFilter.restore_markdown_code(imp_match.group(1), thought_placeholders).strip()
                    tag_name = imp_match.group(2).lower()
                    if th_body:
                        thought_data = {
                            "content": th_body,
                            "duration": round(getattr(self._thought_stream_parser, "duration", 0.0), 2),
                            "token_count": max(1, len(th_body) // 4),
                            "tag": tag_name
                        }
                        if hasattr(self, "_turn_bubble") and self._turn_bubble and hasattr(self._turn_bubble, "finalize_thought"):
                            self._turn_bubble.finalize_thought(
                                thought_data["duration"],
                                thought_data["token_count"]
                            )
                        logger.info(f"[HistorySanitizer] Спасён неявный блок мыслей до </{tag_name}> ({len(th_body)} симв.) и направлен в UI спойлер.")

        self._thought_stream_parser = None

        # Фаза 2: Очистка текста ответа от тегов (включая осиротевшие закрывающие </think>)
        had_tags = bool(
            re.search(re_thought_clean, masked_thought_src, flags=re.IGNORECASE)
            or re.search(re_implicit_close, masked_thought_src, flags=re.IGNORECASE)
        )
        cleaned_masked = re.sub(re_thought_clean, "", masked_thought_src, flags=re.IGNORECASE)
        cleaned_masked = re.sub(re_implicit_close, "", cleaned_masked, flags=re.IGNORECASE)
        cleaned_masked = re.sub(r"</?(think|analyze|thought|reasoning)>", "", cleaned_masked, flags=re.IGNORECASE)
        cleaned_text = TextRegexFilter.restore_markdown_code(cleaned_masked, thought_placeholders).strip()

        if not cleaned_text and parser_normal_text:
            masked_norm, norm_placeholders = TextRegexFilter.mask_markdown_code(parser_normal_text)
            cleaned_norm = re.sub(r"</?(think|analyze|thought|reasoning)>", "", masked_norm, flags=re.IGNORECASE)
            cleaned_text = TextRegexFilter.restore_markdown_code(cleaned_norm, norm_placeholders).strip()

        # Фаза 3: Защита от утечки мыслей в бабл при исчерпании max_tokens на этапе рассуждений
        if not cleaned_text and thought_data and thought_data.get("content"):
            if getattr(self, "_turn_bubble", None) is not None:
                text = "*(Модель завершила глубокий анализ в блоке рассуждений выше, но лимит токенов ответа был исчерпан до вывода чистового текста.)*"
                logger.warning("[HistorySanitizer] Ответ модели целиком состоял из блока рассуждений. Мысли изолированы в спойлере, в бабл выведена сервисная ремарка.")
            else:
                text = thought_data["content"]
                logger.warning("[HistorySanitizer] Ответ модели целиком состоял из блока рассуждений. Текст восстановлен во избежание пустого сообщения.")
        else:
            text = cleaned_text
            if had_tags:
                logger.info(f"[HistorySanitizer] Текст реплики очищен от тегов рассуждений. Итоговая длина для сохранения в историю: {len(text)} симв.")

        # Если внешний text оказался пустым, но потоковый парсер собрал ответ — берем ответ из парсера
        if not text and parser_normal_text:
            text = parser_normal_text

        # Если текст всё еще пуст, проверяем, не был ли он уже отрисован в бабле текущего хода
        if not text and hasattr(self, "_turn_bubble") and self._turn_bubble:
            existing_bubble_text = ""
            if hasattr(self._turn_bubble, "get_text"):
                existing_bubble_text = self._turn_bubble.get_text().strip()
            elif hasattr(self._turn_bubble, "text"):
                t_val = self._turn_bubble.text
                existing_bubble_text = t_val().strip() if callable(t_val) else str(t_val).strip()
            if existing_bubble_text and existing_bubble_text != "Связь с моделью обновлена. Я готова, о чём поговорим?":
                text = existing_bubble_text

        # (Блок gallery_art_pass перемещен в самое начало метода для Zero Pollution)
        user_req = getattr(self, "_current_user_request", "")
        user_asked_art = bool(
            getattr(self, "_is_running_gallery_art_pass", False)
            or self._should_trigger_assistant_art(user_req)
        )
        text, has_gallery, extracted_image_prompt = sanitize_assistant_art_and_markdown(
            text,
            user_requested_art=user_asked_art,
        )

        msg_widget = getattr(self, "_turn_bubble", None)
        if msg_widget is None:
            msg_widget = getattr(self, "_current_streaming_widget", None)

        if msg_widget is None and hasattr(self, "chat_layout"):
            for i in reversed(range(self.chat_layout.count())):
                item = self.chat_layout.itemAt(i)
                w = item.widget() if item else None
                if w and getattr(w, "is_user", None) is False:
                    msg_widget = w
                    break

        # Фоллбэк: достаем текст из виджета только если основной text пуст
        if not text:
            widget_existing_text = ""
            if msg_widget is not None:
                if hasattr(msg_widget, "get_text"):
                    raw_val = msg_widget.get_text()
                    if isinstance(raw_val, str):
                        widget_existing_text = raw_val.strip()
                elif hasattr(msg_widget, "text"):
                    t_val = msg_widget.text
                    raw_val = t_val() if callable(t_val) else t_val
                    if isinstance(raw_val, str):
                        widget_existing_text = raw_val.strip()

            if widget_existing_text:
                clean_existing = RE_GALLERY_TRIGGER.sub('', widget_existing_text).strip()
                clean_existing = RE_IMAGE_TAG.sub('', clean_existing).strip()
                if clean_existing and clean_existing != "Связь с моделью обновлена. Я готова, о чём поговорим?":
                    text = clean_existing

        # Комплексный флаг арт-интента: тег галереи, прямой тег промпта или второй проход
        is_art_turn = bool(has_gallery or extracted_image_prompt or getattr(self, "_is_running_gallery_art_pass", False))

        # Подставлять дефолтную заглушку ТОЛЬКО если и text, и содержимое виджета действительно абсолютно пустые,
        # и при этом ход НЕ связан с генерацией изображения (не is_art_turn)
        if not text:
            if not is_art_turn:
                if not widget_existing_text:
                    text = "Связь с моделью обновлена. Я готова, о чём поговорим?"
            else:
                text = "Секунду, перевожу образ в кадр..."

        sender_name = "Элария"
        avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None

        if msg_widget is not None:
            try:
                if text or not widget_existing_text:
                    msg_widget.set_text(text)
                if thought_data and hasattr(msg_widget, "set_thought_data"):
                    msg_widget.set_thought_data(
                        thought_data.get("content", ""),
                        thought_data.get("duration", 0.0),
                        thought_data.get("token_count", 0)
                    )
                self._turn_bubble = msg_widget
            except (RuntimeError, AttributeError):
                msg_widget = None

        if msg_widget is None:
            try:
                msg_widget = self._append_message(sender_name, text, is_user=False, avatar_path=avatar)
                if thought_data and hasattr(msg_widget, "set_thought_data"):
                    msg_widget.set_thought_data(
                        thought_data.get("content", ""),
                        thought_data.get("duration", 0.0),
                        thought_data.get("token_count", 0)
                    )
                self._turn_bubble = msg_widget
            except (RuntimeError, AttributeError):
                logger.warning("[AssistantFlow] Не удалось отрисовать сообщение: UI чата пересоздается.")
                return

        self._pending_gallery_art_widget = msg_widget

        # Применяем метаданные источников знаний к HUD-контейнеру бабла
        sources = getattr(self, "_pending_knowledge_sources", None)
        if sources:
            if msg_widget and hasattr(msg_widget, "set_knowledge_sources"):
                msg_widget.set_knowledge_sources(sources)
            if hasattr(self, "chat_stream") and hasattr(self.chat_stream, "set_knowledge_sources"):
                self.chat_stream.set_knowledge_sources(sources)

        # Активация киберпанк-лоадера ожидания кадра в бабле (для [GALLERY] и [IMAGE_PROMPT])
        if (has_gallery or extracted_image_prompt) and hasattr(msg_widget, "show_art_loading_placeholder"):
            msg_widget.show_art_loading_placeholder()

        # Формируем контент для памяти модели: сохраняем [IMAGE_PROMPT: ...]
        memory_content = text
        if extracted_image_prompt and "[IMAGE_PROMPT:" not in memory_content:
            memory_content = text + "\n\n[IMAGE_PROMPT: " + extracted_image_prompt + "]"
            memory_content = memory_content.strip()

        print(f"[DIAG_THINK][3. FINALIZE] thought_data is None: {thought_data is None}")
        if thought_data:
            print(f"[DIAG_THINK][3. FINALIZE] thought content preview: {repr(thought_data.get('content', ''))[:120]}")
        print(f"[DIAG_THINK][3. FINALIZE] raw text preview (UI): {repr(text)[:120]}")
        print(f"[DIAG_THINK][3. FINALIZE] memory text preview (LLM): {repr(memory_content)[:120]}")
        self._sync_turn(role="assistant", content=memory_content, replace_last=True, thought=thought_data)
        if hasattr(self, "_check_mode_lock"):
            self._check_mode_lock()

        # ── ВТОРОЙ ПРОХОД: [GALERY] (только если модель не выдала готовый тег [IMAGE_PROMPT]) ──
        if has_gallery and not extracted_image_prompt:
            self._pending_gallery_art_widget = msg_widget
            target_query = getattr(self, "_current_user_request", "")
            if not target_query and hasattr(self, "chat_history") and self.chat_history:
                for _h in reversed(self.chat_history):
                    if _h.get("role") == "user":
                        target_query = str(_h.get("content", ""))
                        break
            self._pending_gallery_user_query = target_query
            self._is_running_gallery_art_pass = True
            if hasattr(self, "_last_llm_msg_text"):
                self._last_llm_msg_text = None
            self._enqueue_gallery_art_pass(target_query)

        # ── ОБРАБОТКА АРТ-ПРОТОКОЛА (ПРЯМОЙ ВЫЗОВ С ИЗВЛЕЧЕННЫМ ТЕГОМ) ──
        elif extracted_image_prompt:
            self._dispatch_art_to_forge(extracted_image_prompt, msg_widget, getattr(self, "_current_user_request", ""))

        self._turn_bubble = None
        self._pending_knowledge_sources = None
        if not extracted_image_prompt and not has_gallery:
            self._trigger_assistant_memory_extraction()
        self._current_user_request = ""

    def _trigger_assistant_memory_extraction(self):
        """Асинхронно извлекает долгосрочные факты о пользователе в LanceDB (CPU ONNX)."""
        if getattr(self, "_is_initializing", False) or getattr(self, "_is_switching_session", False):
            return
        if not getattr(self, "chat_history", None) or len(self.chat_history) < 2:
            return

        messages = self.current_session.get("messages", []) if getattr(self, "current_session", None) else []
        if not messages or len(messages) < 2:
            return

        try:
            from services.memory_extractor import MemoryExtractor
            clean_messages = sanitize_messages_for_memory(messages)
            if clean_messages and len(clean_messages) >= 2:
                MemoryExtractor.extract_facts_async(clean_messages, context=self)
        except Exception as e:
            logger.warning(f"[AssistantFlow] Ошибка вызова экстракции памяти: {e}")