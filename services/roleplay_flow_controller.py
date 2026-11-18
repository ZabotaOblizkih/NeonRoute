"""
Roleplay Flow & Turn Generation Controller (Mixin) for NeonRoute MainWindow.
Handles dialogue dispatch, 50-message sliding window (24K context), RPCast scene integration,
sensual visual triggers, Ambiguity Gate, and reactive scene visual intent callbacks.
"""

import re
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from services.session_manager import SessionManager
from services.roleplay_state_manager import RoleplayStateManager
from services.rp_cast_manager import RPCastManager
from services.rp_cast_extractor import RPCastExtractor
from services.regex_filter import TextRegexFilter
from services.task_queue import InferenceTask
from services.assistant_flow import prepare_roleplay_inline_art_intent
from services.art_director import VisualIntent, TargetSubject
from services.thought_stream_parser import ThoughtStreamParser
from services.thought_styles import resolve_thought_style
from ui.components.chat_bubble import ChatMessageWidget

logger = logging.getLogger("NeonRoute.RoleplayFlow")

RE_IMAGE_TAG = re.compile(r'\[IMAGE[\s_-]PROMPT:\s*([\s\S]*?)\]', re.IGNORECASE)



def run_roleplay_inference_deferred(
    prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
    lm_client: Optional[Any] = None,
    controller: Optional[Any] = None,
    character_card: Optional[Any] = None,
    **kwargs
) -> Any:
    """
    Фоновый исполнитель задачи Ролевого режима с отложенной сборкой контекста.
    - Если messages уже передан: прямой вызов lm_client.chat_completion (Legacy / тесты).
    - Если messages is None: асинхронный поиск в памяти/лоре (RPCastManager.scan_and_tick),
      сборка сообщений через SessionManager и инъекция Depth-1 якоря.
    """
    client = lm_client or (getattr(controller, "lm_client", None) if controller else None)
    if client is None:
        raise ValueError("[DeferredRAG] Отсутствует lm_client для выполнения ролевой задачи.")

    if messages is not None:
        call_kwargs = dict(kwargs)
        call_kwargs["messages"] = messages
        for k in [
            "controller", "user_prompt", "prompt", "session_id", "history_limit",
            "effective_user_name", "include_memory", "vision_content", "art_guidance",
            "character_card", "lm_client", "history"
        ]:
            call_kwargs.pop(k, None)
        return client.chat_completion(**call_kwargs)

    actual_prompt = user_prompt or prompt or ""
    from services.session_manager import SessionManager
    from services.rp_cast_manager import RPCastManager
    from services.roleplay_prompt_builder import RoleplayPromptBuilder

    curr_sess = getattr(controller, "current_session", None) if controller else None
    curr_sess_id = session_id or (curr_sess.get("id") if curr_sess else None)
    sess_persona_id = curr_sess.get("user_persona_id") if curr_sess else None
    active_p = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=sess_persona_id)
    effective_user_name = active_p.get("name") or "Игрок"

    active_char = character_card or (getattr(controller, "active_character", None) if controller else None)
    c_name = getattr(active_char, "name", "Спутница") if active_char else "Спутница"
    is_env = getattr(active_char, "is_environment", False) if active_char else False

    active_cast_block, recalled_lore_block, meta_context_block = "", "", ""
    effective_world_facts = None
    if curr_sess:
        scan_blocks = RPCastManager.scan_and_tick(
            curr_sess_id,
            actual_prompt,
            user_name=effective_user_name,
            companion_name=c_name,
            is_environment=is_env
        )
        active_cast_block = scan_blocks.get("active_cast", "")
        recalled_lore_block = scan_blocks.get("recalled_lore", "")
        meta_context_block = scan_blocks.get("meta_context", "")
        effective_world_facts = RPCastManager.get_world_state(curr_sess_id)
        if not is_env and curr_sess_id:
            cast_meta = RPCastManager.load_cast(curr_sess_id, user_name=effective_user_name, companion_name=c_name)
            c_name = cast_meta.get("focus_companion", c_name)

    is_dm = bool(curr_sess.get("is_dm_mode", False)) if curr_sess else False
    custom_sys_prompt = curr_sess.get("custom_system_prompt") if curr_sess else None

    history = kwargs.get("history")
    if history is None:
        history = getattr(controller, "chat_history", []) if controller else []

    assembled_messages = SessionManager.get_lm_studio_messages(
        history=history,
        mode="roleplay",
        is_roleplay=True,
        character_card=active_char,
        is_excel=False,
        is_word=False,
        vision_content=kwargs.get("vision_content"),
        history_limit=kwargs.get("history_limit", 65),
        user_name=effective_user_name,
        session_id=curr_sess_id,
        session_persona_id=sess_persona_id,
        is_engineer=False,
        world_facts=effective_world_facts,
        art_guidance=kwargs.get("art_guidance"),
        active_cast_block="",
        recalled_lore_block=recalled_lore_block,
        meta_context_block=meta_context_block,
        is_dm_mode=is_dm,
        custom_system_prompt=custom_sys_prompt
    )

    if custom_sys_prompt and assembled_messages and assembled_messages[0].get("role") == "system":
        assembled_messages[0]["content"] = RoleplayPromptBuilder.process_macros(
            custom_sys_prompt, user_name=effective_user_name, char_name=c_name
        )

    if active_char and assembled_messages:
        custom_anchor = curr_sess.get("custom_depth_anchor") if curr_sess else None
        depth_anchor = RoleplayPromptBuilder.build_depth_anchor(
            active_char,
            user_name=effective_user_name,
            user_query=actual_prompt,
            custom_anchor=custom_anchor
        )
        logger.info(f"[RPFlow.DepthAnchor] ⚓ Активный Depth-1 якорь (кастомный={bool(custom_anchor)}):\n{depth_anchor.strip()}")
        if depth_anchor and depth_anchor.strip():
            for idx in reversed(range(len(assembled_messages))):
                if assembled_messages[idx].get("role") == "user":
                    anchor_msg = {"role": "system", "content": depth_anchor.strip()}
                    assembled_messages.insert(idx, anchor_msg)
                    break

    call_kwargs = dict(kwargs)
    call_kwargs["messages"] = assembled_messages
    call_kwargs["is_roleplay"] = True
    call_kwargs["is_engineer"] = False
    for k in [
        "controller", "user_prompt", "prompt", "session_id", "history_limit",
        "effective_user_name", "include_memory", "vision_content", "art_guidance",
        "character_card", "lm_client", "history"
    ]:
        call_kwargs.pop(k, None)

    return client.chat_completion(**call_kwargs)

class RoleplayFlowControllerMixin:
    """Изолированный контроллер диалогового цикла и режиссуры ролевого режима (RP)."""

    @staticmethod
    def _should_trigger_roleplay_art(user_query: str) -> bool:
        """Deprecated: obsolete auto-art dialogue trigger."""
        return False

    @staticmethod
    def _get_roleplay_art_directive(
        target_model_id: str = "realistic", 
        target_model_desc: str = "", 
        companion_name: str = "",
        is_environment: bool = False,
        has_minor: bool = False
    ) -> str:
        """Deprecated: dialogue messages must not contain image prompt directives."""
        return ""

    def _run_roleplay_inference_deferred(self, *args, **kwargs):
        """Прокси к run_roleplay_inference_deferred с передачей self в качестве controller."""
        kwargs.setdefault("controller", self)
        if "lm_client" not in kwargs and hasattr(self, "lm_client"):
            kwargs["lm_client"] = self.lm_client
        return run_roleplay_inference_deferred(*args, **kwargs)

    def _enqueue_roleplay_llm_task(
        self,
        vision_content: Optional[List[Dict[str, Any]]] = None,
        art_guidance: Optional[str] = None
    ):
        """Формирует метаданные ролевой задачи и передаёт задачу в TaskQueue с отложенной сборкой (Deferred RAG)."""
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
                    c = _h.get("content", "")
                    if isinstance(c, list):
                        _req = " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text").strip()
                    else:
                        _req = str(c or "").strip()
                    break

        curr_sess_id = self.current_session.get("id") if self.current_session else None

        # Параметры сэмплинга ролевого режима (SSOT с защитой мастер-предохранителем)
        from config import MASTER_MAX_RESPONSE_TOKENS
        from services.session_manager import get_mode_profile

        mode_profile = get_mode_profile("roleplay") or {}
        profile_sampling = mode_profile.get("sampling", {})
        target_ctx_len = int(mode_profile.get("context_length", 24576))

        sampling = (self.current_session.get("sampling_params") or {}) if self.current_session else {}
        is_custom = bool(self.current_session.get("is_custom_sampling", False)) if self.current_session else False
        cfg_defaults = getattr(self, "app_config", {}).get("default_sampling_roleplay", {})

        if is_custom and "temperature" in sampling:
            temp = float(sampling.get("temperature", cfg_defaults.get("temperature", 1.10)))
            min_p = float(sampling.get("min_p", cfg_defaults.get("min_p", 0.05)))
            rep_pen = float(sampling.get("repeat_penalty", cfg_defaults.get("repeat_penalty", 1.02)))
        else:
            temp = float(cfg_defaults.get("temperature", profile_sampling.get("temperature", 1.10)))
            min_p = float(cfg_defaults.get("min_p", profile_sampling.get("min_p", 0.05)))
            rep_pen = float(cfg_defaults.get("repeat_penalty", profile_sampling.get("repeat_penalty", 1.02)))
        top_p_val = sampling.get("top_p", cfg_defaults.get("top_p", profile_sampling.get("top_p", 0.95)))
        top_p = float(top_p_val) if top_p_val is not None else 0.95
        dry_mult = float(sampling.get("dry_multiplier", cfg_defaults.get("dry_multiplier", profile_sampling.get("dry_multiplier", 0.80))))
        xtc_prob = float(sampling.get("xtc_probability", cfg_defaults.get("xtc_probability", profile_sampling.get("xtc_probability", 0.30))))
        xtc_thresh = float(sampling.get("xtc_threshold", cfg_defaults.get("xtc_threshold", profile_sampling.get("xtc_threshold", 0.10))))

        default_max_tok = int(profile_sampling.get("max_tokens", 4096))
        raw_user_max = sampling.get("max_tokens", cfg_defaults.get("max_tokens", default_max_tok))
        try:
            target_max_tokens = max(128, min(int(raw_user_max or default_max_tok), MASTER_MAX_RESPONSE_TOKENS))
        except (ValueError, TypeError):
            target_max_tokens = min(default_max_tok, MASTER_MAX_RESPONSE_TOKENS)

        from services.assistant_flow import resolve_effective_thinking
        enable_thinking = resolve_effective_thinking(
            mode="roleplay",
            model_name=model_name,
            active_skill=None,
            explicit_override=None
        )

        llm_kwargs = {
            "user_prompt": _req,
            "prompt": _req,
            "session_id": curr_sess_id,
            "messages": None,
            "model": model_name,
            "temperature": temp,
            "stop": None,
            "is_roleplay": True,
            "is_engineer": False,
            "enable_thinking": enable_thinking,
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
            "vision_content": vision_content,
            "art_guidance": art_guidance,
            "character_card": getattr(self, "active_character", None),
            "controller": self,
            "lm_client": self.lm_client
        }

        task = InferenceTask(
            name="LLM Ответ",
            task_type="llm",
            func=self._run_roleplay_inference_deferred,
            kwargs=llm_kwargs,
            abort_fn=self.lm_client.abort,
            metadata={"is_roleplay": True, "has_vision": bool(vision_content)}
        )

        logger.info(f"[RoleplayFlow] Задача инференса поставлена в очередь (Deferred RAG) [ID: {task.id}]")
        self._active_tasks[task.id] = task
        # Инициализация потокового парсера мыслей <think>...</think>
        self._thought_stream_parser = ThoughtStreamParser()
        active_card = getattr(self, "active_character", getattr(self, "active_card", None))
        if hasattr(self, "_turn_bubble") and self._turn_bubble and hasattr(self._turn_bubble, "apply_thought_style"):
            self._turn_bubble.apply_thought_style(resolve_thought_style(active_card))
        self.is_busy = True
        if hasattr(self, "_stream_flush_timer") and self._stream_flush_timer and not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start()
        self.task_queue.enqueue(task)

    def _handle_roleplay_llm_response(self, text: str):
        """Обрабатывает сгенерированный ответ ролевой модели, статус и извлечение сцены."""
        active_card = getattr(self, "active_character", getattr(self, "active_card", None))
        thought_style = resolve_thought_style(active_card)

        # 1. Финализация потокового парсера мыслей <think>...</think>
        thought_data = None
        if getattr(self, "_thought_stream_parser", None) is not None:
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
            thought_data = self._thought_stream_parser.get_thought_data()
            self._thought_stream_parser = None

        # Fallback спасения мыслей из текста (если ответ пришел не через стрим или теги остались в text)
        if thought_data is None and text:
            re_thought_body = r"<(think|analyze|thought|reasoning)>([\s\S]*?)(?:</\1>|$)"
            re_implicit_close = r"^([\s\S]*?)</(think|analyze|thought|reasoning)>"
            matches = list(re.finditer(re_thought_body, text, flags=re.IGNORECASE))
            if matches:
                th_parts = [m.group(2).strip() for m in matches if m.group(2).strip()]
                th_body = "\n\n".join(th_parts)
                if th_body:
                    thought_data = {
                        "content": th_body,
                        "duration": 0.0,
                        "token_count": max(1, len(th_body) // 4)
                    }
            else:
                imp_match = re.search(re_implicit_close, text, flags=re.IGNORECASE)
                if imp_match:
                    th_body = imp_match.group(1).strip()
                    if th_body:
                        thought_data = {
                            "content": th_body,
                            "duration": 0.0,
                            "token_count": max(1, len(th_body) // 4)
                        }

        # Применяем стиль мыслей и передаем thought_data текущему баблу, если он уже существует
        curr_bubble = getattr(self, "_turn_bubble", None)
        if curr_bubble is not None:
            if hasattr(curr_bubble, "apply_thought_style"):
                curr_bubble.apply_thought_style(thought_style)
            if thought_data and hasattr(curr_bubble, "set_thought_data"):
                curr_bubble.set_thought_data(
                    thought_data.get("content", ""),
                    thought_data.get("duration", 0.0),
                    thought_data.get("token_count", 0)
                )

        # Предохранитель парсера стрима: защита от незакрытых тегов (Задача 4)
        final_reply = text
        for tag in ["stats", "meters", "choices"]:
            open_tag = f"<{tag}>"
            close_tag = f"</{tag}>"
            if open_tag in final_reply and close_tag not in final_reply:
                # Если блок кода внутри тега остался открытым, закрываем его
                if "```" in final_reply and final_reply.count("```") % 2 != 0:
                    final_reply += "\n```"
                final_reply += f"\n{close_tag}"
        text = final_reply

        sess_id = self.current_session.get("id") if getattr(self, "current_session", None) else None
        text = RoleplayStateManager.process_model_turn(sess_id, text)
        text = TextRegexFilter.process_output(text)
        text = re.sub(r'(?:\n|\s)*🔔\s*Напоминаю:[^\n]*', '', text).strip()
        text = re.sub(r'(?:\s*--model\s+[a-zA-Z0-9_\-]+)+\s*$', '', text).strip()

        # Поиск и парсинг тега [IMAGE_PROMPT: ...]
        extracted_image_prompt, clean_text = TextRegexFilter.extract_image_prompt(
            text,
            strip_thoughts=True,
        )
        if extracted_image_prompt is not None:
            # Срезаем все теги [IMAGE_PROMPT: ...], сохраняя окружающий художественный текст
            if clean_text:
                text = clean_text
            else:
                text = "*Внимательно смотрит на вас, визуализируя образ...*"

        sender_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Персонаж"
        avatar = self.active_character.avatar_path if (hasattr(self, "active_character") and self.active_character) else None

        msg_widget = getattr(self, "_turn_bubble", None)
        if msg_widget is not None:
            try:
                msg_widget.set_text(text)
            except (RuntimeError, AttributeError):
                msg_widget = None

        if msg_widget is None:
            try:
                msg_widget = self._append_message(sender_name, text, is_user=False, avatar_path=avatar)
                self._turn_bubble = msg_widget
            except Exception as e:
                logger.warning(f"[RPFlow] Ошибка отображения бабла: {e}")
                return

        if thought_data and msg_widget is not None:
            if hasattr(msg_widget, "apply_thought_style"):
                msg_widget.apply_thought_style(thought_style)
            if hasattr(msg_widget, "set_thought_data"):
                msg_widget.set_thought_data(
                    thought_data.get("content", ""),
                    thought_data.get("duration", 0.0),
                    thought_data.get("token_count", 0)
                )

        self._sync_turn(role="assistant", content=text, replace_last=True, mode="roleplay", thought=thought_data)
        if hasattr(self, "_check_mode_lock"):
            self._check_mode_lock()

        # Сброс подсказки микро-события строго после успешной фиксации ответа модели в сессии
        if sess_id:
            try:
                cast_data = RPCastManager.load_cast(sess_id)
                if cast_data.get("pending_event_nudge"):
                    nudge_val = cast_data["pending_event_nudge"]
                    cast_data["pending_event_nudge"] = None
                    RPCastManager.save_cast(sess_id, cast_data)
                    logger.info(f"[RPFlow] Сброшен pending_event_nudge ('{nudge_val}') для сессии {sess_id}")
            except Exception as _e_nudge:
                logger.debug(f"[RPFlow] Ошибка сброса pending_event_nudge: {_e_nudge}")

        # Обработка визуального кадра сцены
        if extracted_image_prompt:
            sess_id_cur = self.current_session.get("id") if getattr(self, "current_session", None) else None
            v_intent, pref_style = prepare_roleplay_inline_art_intent(
                extracted_image_prompt=extracted_image_prompt,
                user_query=getattr(self, "_current_user_request", ""),
                selected_forge_checkpoint=getattr(self, "selected_forge_checkpoint", None),
                session_id=sess_id_cur,
            )

            if v_intent is not None:
                raw_prompt = v_intent.prompt
                active_c_name = getattr(self.active_character, "name", None)
                msg_widget.visual_intent = v_intent
                msg_widget.art_source = "inline_tag"
                if hasattr(msg_widget, "set_scene_art_state"):
                    msg_widget.set_scene_art_state(is_ready=True, cue_text=raw_prompt)
                auto_art = getattr(self, "app_config", {}).get("auto_generate_art", False)

                if auto_art and hasattr(self, "pill_forge") and self.pill_forge.is_online:
                    self._on_scene_art_requested(
                        visual_intent=v_intent,
                        widget=msg_widget,
                        preferred_style=pref_style,
                        character_name=active_c_name
                    )
                else:
                    def _trigger_art(v=v_intent, w=msg_widget, s=pref_style, c=active_c_name):
                        self._on_scene_art_requested(
                            visual_intent=v, widget=w, preferred_style=s, character_name=c
                        )
                    msg_widget.add_art_proposal_chip(raw_prompt, _trigger_art)

            else:
                if hasattr(msg_widget, "set_scene_art_state"):
                    msg_widget.set_scene_art_state(is_ready=False)
        else:
            if hasattr(msg_widget, "set_scene_art_state"):
                msg_widget.set_scene_art_state(is_ready=False)

        self._turn_bubble = None
        # Анализ сцены и извлечение памяти запускаются на каждом ходе независимо от арта
        self._trigger_roleplay_memory_extraction()
        self._current_user_request = ""

    def _trigger_roleplay_memory_extraction(self):
        """Асинхронно извлекает NPC, локации и факты сцены через RPCastExtractor."""
        if getattr(self, "_is_initializing", False) or getattr(self, "_is_switching_session", False):
            return
        if not getattr(self, "chat_history", None) or len(self.chat_history) < 2:
            return

        sess_id = self.current_session.get("id") if getattr(self, "current_session", None) else None
        if not sess_id:
            return

        messages = self.current_session.get("messages", []) if self.current_session else []
        if len(messages) < 2:
            return

        sess_persona_id = self.current_session.get("user_persona_id")
        active_p = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=sess_persona_id)
        u_name = active_p.get("name") or "Игрок"
        c_name = getattr(self.active_character, "name", "Спутница") if hasattr(self, "active_character") and self.active_character else "Спутница"
        is_env = getattr(self.active_character, "is_environment", False) if hasattr(self, "active_character") and self.active_character else False

        RPCastExtractor.extract_cast_async(
            messages,
            session_id=sess_id,
            lm_client=self.lm_client,
            context=self,
            user_name=u_name,
            companion_name=c_name,
            is_environment=is_env
        )

    def _on_scene_visual_intent_ready(self, session_id: str, visual_intent: Any):
        """Реактивный колбэк при готовности визуального кадра сцены из RPCastExtractor."""
        if not visual_intent:
            return

        v_intent = VisualIntent.from_dict(visual_intent) if isinstance(visual_intent, dict) else visual_intent
        if not v_intent or not getattr(v_intent, "prompt", None):
            return

        cur_id = self.current_session.get("id") if getattr(self, "current_session", None) else None
        if cur_id != session_id:
            return

        target_msg_id = v_intent.get("message_id") if isinstance(v_intent, dict) else getattr(v_intent, "message_id", None)
        target_widget = None
        if hasattr(self, "chat_layout") and self.chat_layout:
            for i in reversed(range(self.chat_layout.count())):
                item = self.chat_layout.itemAt(i)
                w = item.widget() if item else None
                if w and isinstance(w, ChatMessageWidget) and not getattr(w, "is_user", True):
                    if target_msg_id:
                        if getattr(w, "message_id", None) == target_msg_id:
                            target_widget = w
                            break
                    else:
                        target_widget = w
                        break

        if not target_widget:
            return

        if getattr(target_widget, "art_source", None) == "inline_tag":
            logger.debug("[RPFlow] Пропуск кадра от экстрактора: в сообщении уже активен арт из тега [IMAGE_PROMPT].")
            return

        target_widget.visual_intent = v_intent
        target_widget.art_source = "extractor"

        # Активация кнопки арт-сцены: кадр готов к рендеру
        if hasattr(target_widget, "set_scene_art_state"):
            target_widget.set_scene_art_state(is_ready=True, cue_text=v_intent.prompt)
        elif hasattr(target_widget, "set_scene_art_busy"):
            target_widget.set_scene_art_busy(False)

        # Синхронизация с историей сообщений на диске
        if self.current_session and "messages" in self.current_session:
            for m in reversed(self.current_session["messages"]):
                if not m.get("is_user") and m.get("role") != "user":
                    m["visual_intent"] = v_intent.to_dict() if hasattr(v_intent, "to_dict") else visual_intent
                    break
            SessionManager.save_session(self.current_session, trigger_memory=False)

        if hasattr(self, "_pending_scene_art_request") and self._pending_scene_art_request:
            pending = self._pending_scene_art_request
            self._pending_scene_art_request = None
            self._on_scene_art_requested(
                visual_intent=v_intent,
                widget=pending.get("widget") or target_widget,
                preferred_style=pending.get("preferred_style"),
                character_name=pending.get("character_name")
            )
            return

        auto_art = getattr(self, "app_config", {}).get("auto_generate_art", False)
        if auto_art and hasattr(self, "pill_forge") and self.pill_forge.is_online:
            self._on_scene_art_requested(visual_intent=v_intent, widget=target_widget)
        else:
            if hasattr(target_widget, "add_art_proposal_chip"):
                def _trigger_scene_art(v=v_intent, w=target_widget):
                    self._on_scene_art_requested(visual_intent=v, widget=w)
                target_widget.add_art_proposal_chip(v_intent.prompt, _trigger_scene_art)


    def _on_scene_visual_prompt_extracted(self, visual_prompt: str, session_id: str):
        if not visual_prompt:
            return
        v_intent = VisualIntent(
            prompt=visual_prompt.strip(),
            target_subject=TargetSubject.COMPANION,
            user_neg="",
            suggested_model=None,
            seed=-1,
            session_id=session_id,
            mode="roleplay"
        )
        self._on_scene_visual_intent_ready(session_id, v_intent)

    def _generate_ephemeral_rp_art_prompt(self, sess_id: Optional[str], last_assistant_message: str, target_model_id: str = "realistic") -> str:
        """
        Формирует изолированный английский промпт для SD через кинематографические архетипы сцены.
        Исключает передачу оружия и подставляет nude в интимных сценах.
        """
        from services.rp_cast_manager import RPCastManager
        from services.roleplay_prompt_builder import RoleplayPromptBuilder

        char_name = self.active_character.name if (hasattr(self, "active_character") and self.active_character) else "Спутница"
        cast_data = RPCastManager.load_cast(sess_id, companion_name=char_name) if sess_id else {}

        # 1. Считывание кино-архетипа
        scene_type = str(cast_data.get("scene_type") or "dialogue").lower().strip()
        if scene_type not in ("intimate", "action", "dialogue", "exploration"):
            scene_type = "dialogue"

        # 2. Локация и атмосфера
        env = cast_data.get("environment", {}) if isinstance(cast_data, dict) else {}
        cur_loc = cast_data.get("current_location") or env.get("location_dna", "")
        scene_ambience = env.get("scene_ambience", "")
        if scene_ambience:
            cur_loc = f"{cur_loc} ({scene_ambience})".strip() if cur_loc else scene_ambience

        # 3. Внешность персонажей
        comp_state = cast_data.get("companion_state", {}) if isinstance(cast_data, dict) else {}
        comp_dna = comp_state.get("visual_dna")
        if not comp_dna and hasattr(self, "active_character") and self.active_character:
            comp_dna = getattr(self.active_character, "visual_dna", "")

        visual_summary = f"{char_name}: {comp_dna}" if comp_dna else ""

        # Очищаем визуальное ДНК от технического синтаксиса LoRA перед передачей в ТЗ
        clean_visual_summary = re.sub(r'<lora:[^>]+>', '', visual_summary or '').strip(' ,')

        # Очищаем контекст реплики от технической разметки (<stats>, <thought>, [STATUS])
        # чтобы статы, уровни и мысли не попадали в Danbooru-промпт для Stable Diffusion
        raw_ctx = last_assistant_message or ""
        clean_ctx = re.sub(r'<stats>.*?</stats>', '', raw_ctx, flags=re.DOTALL | re.IGNORECASE)
        clean_ctx = re.sub(r'<thought>.*?</thought>', '', clean_ctx, flags=re.DOTALL | re.IGNORECASE)
        clean_ctx = re.sub(r'\[(?:СЦЕНА|SCENE|СТАТУС|STATUS|ШРАМ|SCAR|КВЕСТ|QUEST|IMAGE_PROMPT)[^\]]*\]', '', clean_ctx, flags=re.IGNORECASE)
        clean_ctx = re.sub(r'</?(?:stats|thought|meters|choices|check|terminal|sms|phone)>', '', clean_ctx, flags=re.IGNORECASE).strip()

        # 4. Формирование контекста под мизансцену
        from services.assistant_prompt_builder import resolve_model_architecture
        arch = resolve_model_architecture(target_model_id)

        prompt_task_header = (
            "Generate a concise descriptive visual prompt (2-3 sentences) for this RP scene:"
            if "xl" in arch.lower()
            else "Generate concise, comma-separated Danbooru tags for this RP scene:"
        )

        if scene_type == "intimate":
            gear_status = "ЗАБЛОКИРОВАНО (nude, экипировка скрыта)"
            payload_lines = [
                prompt_task_header,
                f"- Location: {cur_loc or 'Private quarters'}",
                "- State: strictly alone in private intimate sanctuary (characters undressed, bare skin)",
                f"- Subject: {clean_visual_summary or 'Unspecified'}",
                f"- Scene context: {clean_ctx or 'Intimate moment'}"
            ]
        else:
            inv_items = cast_data.get("inventory", []) if isinstance(cast_data, dict) else []
            inventory_str = ", ".join(str(i) for i in inv_items) if isinstance(inv_items, list) else str(inv_items)
            comp_outfit = comp_state.get("current_outfit", "") or "Стандартная каноничная одежда"

            gear_status = f"АКТИВНО ({inventory_str})" if inventory_str else "В ножнах/убрано"
            payload_lines = [
                prompt_task_header,
                f"- Location: {cur_loc or 'Unspecified'}",
                f"- Outfit: {comp_outfit}",
                f"- Subject: {clean_visual_summary or 'Unspecified'}",
                f"- Scene context: {clean_ctx or 'Dialogue scene'}"
            ]
            if inventory_str and scene_type in ("action", "exploration"):
                payload_lines.insert(2, f"- Equipment: {inventory_str}")

        if "xl" in arch.lower():
            payload_lines.append(
                "CRITICAL: Output ONLY the descriptive prompt inside [IMAGE_PROMPT: ...]. "
                "Do NOT write conversational filler or prose outside the brackets."
            )
        else:
            payload_lines.append(
                "CRITICAL: Output ONLY comma-separated tags inside [IMAGE_PROMPT: ...]. "
                "Do NOT write prose or paragraphs."
            )

        logger.info(
            f"[ArtDirector.Cinema] 🎥 Мизансцена: '{scene_type.upper()}' -> "
            f"Применена кино-директива: '{scene_type}'. Снаряжение: {gear_status}"
        )

        from services.forge_models import detect_style_from_checkpoint
        clean_target_style = detect_style_from_checkpoint(target_model_id) if target_model_id else "realistic"
        system_prompt = RoleplayPromptBuilder.get_cinematic_scene_directive(
            scene_type,
            clean_target_style,
            architecture=arch
        )

        user_payload = "\n".join(payload_lines)

        temp_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload}
        ]

        active_model = getattr(self.lm_client, "_last_loaded_model_id", None) or getattr(self, "active_lms_model", None)
        active_ctx = getattr(self.lm_client, "_current_loaded_ctx", 24576) or 24576

        logger.info(
            f"\n[RoleplayFlow.CinemaPrompt] >>> ВХОД ДЛЯ LLM ({active_model}):\n"
            f"[SYSTEM DIRECTIVE]:\n{system_prompt}\n"
            f"[USER CONTEXT]:\n{user_payload}"
        )

        try:
            raw_response = self.lm_client.chat_completion(
                messages=temp_messages,
                model=active_model,
                context_length=active_ctx,
                temperature=0.3,
                max_tokens=350,
                stream=False,
                emit_signal=False,
                is_roleplay=False
            )
            raw_text = raw_response if isinstance(raw_response, str) else str(raw_response or "")
        except Exception as e:
            logger.error(f"[RoleplayFlow] Ошибка генерации эфемерного арт-промпта РП: {e}")
            raw_text = ""

        logger.info(f"\n[RoleplayFlow.CinemaPrompt] <<< СЫРОЙ ВЫХОД LLM:\n{raw_text}\n")

        raw_text_clean = TextRegexFilter.strip_thought_blocks(raw_text, mask_code=True).strip()
        extracted_prompt, _ = TextRegexFilter.extract_image_prompt(
            raw_text_clean,
            strip_thoughts=False,
            allow_markdown_fallback=True,
        )
        if extracted_prompt is not None:
            prompt_content = extracted_prompt
        else:
            line_matches = list(re.finditer(r'(?:^|\n)[\s*#_-]*IMAGE[\s_-]PROMPT:\s*([^\n\r]+)', raw_text_clean, re.IGNORECASE))
            prompt_content = line_matches[-1].group(1).strip() if line_matches else raw_text_clean

        # Отсекаем описательные блоки Description и пустые переносы
        prompt_content = re.split(r'\[(?:Description|Narrative|Scene)\]|(?:\n\s*\n)', prompt_content, flags=re.IGNORECASE)[0]
        prompt_content = re.sub(r'^[\s\[]*IMAGE[\s_-]PROMPT:\s*', '', prompt_content, flags=re.IGNORECASE)

        # Срезаем флаг --model со всеми последующими словами до флага или конца строки (включая хвосты v5, v0.9 и т.д.)
        clean_prompt = re.sub(r'--model\s+[^-\n\r]+', '', prompt_content, flags=re.IGNORECASE).strip(' ,[]')
        logger.info(f"[RoleplayFlow.CinemaPrompt] ИТОГОВЫЙ ПРОМПТ ДЛЯ АРТ-ДИРЕКТОРА: '{clean_prompt}'")
        return clean_prompt

