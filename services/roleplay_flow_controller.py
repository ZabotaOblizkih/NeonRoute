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
from services.model_router import ModelRouter
from services.art_director import VisualIntent, TargetSubject
from ui.components.chat_bubble import ChatMessageWidget

logger = logging.getLogger("NeonRoute.RoleplayFlow")

RE_IMAGE_TAG = re.compile(r'\[IMAGE[\s_-]PROMPT:\s*([\s\S]*?)\]', re.IGNORECASE)


class RoleplayFlowControllerMixin:
    """Изолированный контроллер диалогового цикла и режиссуры ролевого режима (RP)."""

    @staticmethod
    def _should_trigger_roleplay_art(user_query: str) -> bool:
        """Проверяет явные и чувственные триггеры визуализации в контексте ролевой игры."""
        q = (user_query or "").strip().lower()
        if not q:
            return False

        exclusions = (
            "excel", "эксель", "таблиц", ".xlsx", "docx", "ворд", "word",
            "код", "скрипт", "pygame", "matplotlib", "график", "диаграмм",
            "баз данных", "sql", "функци", "класс", ".py"
        )
        if any(w in q for w in exclusions):
            return False

        explicit_triggers = (
            "нарисуй", "нарисуешь", "нарисовать", "сфотографируй", "сфоткай",
            "сделай фото", "сделай снимок", "сделай кадр", "сгенерируй",
            "сгенерируешь", "генерация арта", "изобрази", "изобразишь",
            "начерти", "иллюстрируй", "проиллюстрируй", "отрисуй", "зарисуй",
            "набросай", "визуализируй", "сделай арт", "создай арт", "сделай картинку",
            "создай картинку", "покажи картинку", "покажи рисунок", "покажи арт",
            "нарисуй мне", "сделай портрет", "draw", "paint", "sketch", "render", "take a photo", "generate art"
        )
        if any(w in q for w in explicit_triggers):
            return True

        sensual_triggers = (
            "хочу тебя видеть", "хочу увидеть тебя", "дай мне тебя увидеть", "дай взглянуть на тебя",
            "позволь увидеть тебя", "хочу посмотреть на тебя", "покажи себя", "покажись", "покажись мне",
            "предстань передо мной", "явись мне", "как ты выглядишь", "как ты сейчас выглядишь",
            "какая ты сейчас", "опиши свой вид", "в чем ты сейчас", "в чём ты сейчас", "в чем ты одета",
            "в чём ты одета", "что на тебе надето", "что на тебе сейчас", "в каком ты виде",
            "покажи свой наряд", "покажи свое платье", "покажи своё платье", "покажи свой образ",
            "покажи свой костюм", "покажи белье", "покажи бельё", "сними это", "сними с себя",
            "разденься", "сбрось одежду", "хочу увидеть твое тело", "хочу увидеть твоё тело",
            "покажи свое тело", "покажи своё тело", "покажи фигуру", "покажи свою фигуру",
            "покажи ножки", "покажи свои ножки", "хочу увидеть твою улыбку", "хочу увидеть твои глаза",
            "хочу увидеть твой взгляд", "посмотри на меня", "покажи свое лицо", "покажи своё лицо",
            "сделай селфи", "скинь селфи", "пришли селфи", "покажи селфи", "твое селфи", "твоё селфи",
            "пришли фото", "скинь фото", "в какой ты позе", "покажи эту позу", "встань в позу", "подойди ближе"
        )
        return any(w in q for w in sensual_triggers)

    @staticmethod
    def _get_roleplay_art_directive(
        target_model_id: str = "realistic", 
        target_model_desc: str = "", 
        companion_name: str = "",
        is_environment: bool = False,
        has_minor: bool = False
    ) -> str:
        if not target_model_desc and target_model_id:
            try:
                from services.model_router import ModelRouter
                router = ModelRouter.get_instance() if hasattr(ModelRouter, "get_instance") else ModelRouter()
                for m in getattr(router, "profiles", []):
                    if m.get("id") == target_model_id:
                        guidance = (m.get("llm_guidance") or "").strip()
                        target_model_desc = guidance if guidance else (m.get("routing_description") or m.get("name") or "")
                        break
            except Exception:
                pass
        model_hint = f"{target_model_id} ({target_model_desc})" if target_model_desc else target_model_id
        if has_minor:
            rules_text = (
                "ПРАВИЛА ТЕГА:\n"
                "- В СЦЕНЕ ПРИСУТСТВУЕТ НЕСОВЕРШЕННОЛЕТНИЙ. Категорически ЗАПРЕЩЕНЫ любые теги наготы, белья или сексуализации (nude, topless, lewd, erotic и т.д.).\n"
                "- Персонаж обязан быть полностью одет согласно канону (платье, туника, лохмотья рабыни: 'tattered clothes', 'ragged dress' и т.д.).\n"
                "- Используй только утвердительные английские Danbooru-теги через запятую."
            )
        else:
            rules_text = (
                "ПРАВИЛА ТЕГА:\n"
                "- Используй только утвердительные английские Danbooru-теги через запятую.\n"
                "- Для наготы или снятой одежды используй прямые теги: 'nude', 'topless', 'bottomless', 'bare legs', 'bare skin', 'barefoot', 'bareheaded'.\n"
                "- ЗАПРЕЩЕНО использовать 'no' или 'without'. Флаг '--neg <tags>' добавляй только если запрошено исключить конкретный фон/деталь."
            )
        if is_environment:
            who_hint = " окружение и обстановку сцены"
            reaction_rule = "1. Опиши отклик окружения и обстановку сцены на красивом литературном русском языке."
        else:
            who_hint = f" или образ персонажа {companion_name}" if companion_name else " или твой образ"
            reaction_rule = "1. Отыграй реакцию от первого лица в своем характере на красивом литературном русском языке."

        return (
            f"\n\n[РЕЖИССУРА СЦЕНЫ: Пользователь хочет видеть происходящее{who_hint}.\n"
            f"{reaction_rule}\n"
            f"2. Модель генерации: {model_hint}.\n"
            f"3. В самом конце ответа с новой строки добавь обязательный визуальный тег:\n"
            f"[IMAGE_PROMPT: <english_danbooru_tags> --model {target_model_id}]\n"
            f"{rules_text}\n"
            f"ОБРАЗЕЦ:\n"
            f"Я улыбнулась прохладному ветру...\n"
            f"[IMAGE_PROMPT: 1girl, solo, outdoors, smile, cinematic lighting --model {target_model_id}]"
        )

    def _enqueue_roleplay_llm_task(
        self,
        vision_content: Optional[List[Dict[str, Any]]] = None,
        art_guidance: Optional[str] = None
    ):
        """Формирует контекст ролевой игры с лимитом на 50 сообщений и передаёт задачу в TaskQueue."""
        model_name = self.lm_combo.currentText().strip()
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

        _should_art = (not vision_content and self._should_trigger_roleplay_art(_req))

        # Управление контекстом делегировано динамическому бюджету SessionManager (SSOT)
        curr_sess_id = self.current_session.get("id") if self.current_session else None
        sess_persona_id = self.current_session.get("user_persona_id") if self.current_session else None
        active_p = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=sess_persona_id)
        effective_user_name = active_p.get("name") or "Игрок"
        c_name = getattr(self.active_character, "name", "Спутница") if hasattr(self, "active_character") and self.active_character else "Спутница"
        is_env = getattr(self.active_character, "is_environment", False) if hasattr(self, "active_character") and self.active_character else False

        # Синхронизация каста и окружения
        active_cast_block, recalled_lore_block, meta_context_block = "", "", ""
        effective_world_facts = None
        if self.current_session:
            scan_blocks = RPCastManager.scan_and_tick(
                curr_sess_id,
                _req,
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

        is_dm = bool(self.current_session.get("is_dm_mode", False)) if self.current_session else False

        messages = SessionManager.get_lm_studio_messages(
            history=self.chat_history,
            is_roleplay=True,
            character_card=self.active_character,
            is_excel=False,
            is_word=False,
            vision_content=vision_content,
            history_limit=65,
            user_name=effective_user_name,
            session_id=curr_sess_id,
            session_persona_id=sess_persona_id,
            is_engineer=False,
            world_facts=effective_world_facts,
            art_guidance=art_guidance,
            active_cast_block=active_cast_block,
            recalled_lore_block=recalled_lore_block,
            meta_context_block=meta_context_block,
            is_dm_mode=is_dm
        )

        # Подмешивание режиссуры сцены при арт-триггере
        if _should_art:
            best_model_id = "realistic"
            best_desc = ""
            try:
                router = ModelRouter.get_instance() if hasattr(ModelRouter, "get_instance") else ModelRouter()
                matched_id, score = router.match_prompt(_req)
                if matched_id:
                    best_model_id = matched_id
                    for m in router.profiles:
                        if m.get("id") == matched_id:
                            guidance = (m.get("llm_guidance") or "").strip()
                            best_desc = guidance if guidance else (m.get("routing_description") or m.get("name") or "")
                            break
            except Exception:
                pass

            has_minor = False
            if self.active_character and getattr(self.active_character, "is_minor", False):
                has_minor = True
            elif hasattr(self, "rp_cast_mgr") and self.rp_cast_mgr:
                for ent in self.rp_cast_mgr.get_active_companions():
                    if ent.get("is_minor"):
                        has_minor = True
                        break
            elif curr_sess_id:
                for ent in RPCastManager.get_active_companions(session_id=curr_sess_id):
                    if ent.get("is_minor"):
                        has_minor = True
                        break

            _directive = self._get_roleplay_art_directive(
                target_model_id=best_model_id, 
                target_model_desc=best_desc, 
                companion_name=c_name,
                is_environment=is_env,
                has_minor=has_minor
            )
            if _directive and messages:
                for idx in reversed(range(len(messages))):
                    if messages[idx].get("role") == "user":
                        user_msg_copy = dict(messages[idx])
                        _c = user_msg_copy.get("content")
                        if isinstance(_c, str):
                            user_msg_copy["content"] = _c + _directive
                        messages[idx] = user_msg_copy
                        break

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

        default_max_tok = int(profile_sampling.get("max_tokens", 4096))
        raw_user_max = sampling.get("max_tokens", cfg_defaults.get("max_tokens", default_max_tok))
        try:
            target_max_tokens = max(128, min(int(raw_user_max or default_max_tok), MASTER_MAX_RESPONSE_TOKENS))
        except (ValueError, TypeError):
            target_max_tokens = min(default_max_tok, MASTER_MAX_RESPONSE_TOKENS)

        llm_kwargs = {
            "messages": messages,
            "model": model_name,
            "temperature": temp,
            "stop": None,
            "is_roleplay": True,
            "is_engineer": False,
            "context_length": target_ctx_len,
            "max_tokens": target_max_tokens,
            "min_p": min_p,
            "top_p": top_p,
            "repetition_penalty": rep_pen,
            "repeat_penalty": rep_pen,
            "stream": True
        }

        task = InferenceTask(
            name="LLM Ответ",
            task_type="llm",
            func=self.lm_client.chat_completion,
            kwargs=llm_kwargs,
            abort_fn=self.lm_client.abort,
            metadata={"is_roleplay": True, "has_vision": bool(vision_content)}
        )

        self._active_tasks[task.id] = task
        self.task_queue.enqueue(task)

    def _handle_roleplay_llm_response(self, text: str):
        """Обрабатывает сгенерированный ответ ролевой модели, статус и извлечение сцены."""
        sess_id = self.current_session.get("id") if getattr(self, "current_session", None) else None
        text = RoleplayStateManager.process_model_turn(sess_id, text)
        text = TextRegexFilter.process_output(text)
        text = re.sub(r'(?:\n|\s)*🔔\s*Напоминаю:[^\n]*', '', text).strip()
        text = re.sub(r'(?:\s*--model\s+[a-zA-Z0-9_\-]+)+\s*$', '', text).strip()

        # Поиск и парсинг тега [IMAGE_PROMPT: ...]
        img_match = RE_IMAGE_TAG.search(text)
        extracted_image_prompt = None
        if img_match:
            extracted_image_prompt = img_match.group(1).strip()
            # Срезаем все теги [IMAGE_PROMPT: ...], сохраняя окружающий художественный текст
            clean_text = RE_IMAGE_TAG.sub('', text).strip()
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

        self._sync_turn(role="assistant", content=text, replace_last=True)
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
            raw_prompt = extracted_image_prompt.strip()
            router = ModelRouter.get_instance() if hasattr(ModelRouter, "get_instance") else ModelRouter()
            active_models = {
                str(m.get("id", "")).lower().strip(): m.get("id")
                for m in router.profiles if m.get("active", True) and m.get("id")
            }

            model_match = re.search(r'(?:--model|--style)[:=\s]+["\']?([a-zA-Z0-9_\-]+)["\']?', raw_prompt, re.I)
            llm_style = model_match.group(1).lower().strip() if model_match else None
            raw_prompt = re.sub(r'(?:--model|--style)[:=\s]+["\']?[a-zA-Z0-9_\-]+["\']?', '', raw_prompt, flags=re.I).strip(' ,')

            neg_match = re.search(r'(?:--neg|--negative)[:=\s]+(.*?)(?=(?:--model|--style|$))', raw_prompt, re.I)
            custom_neg = neg_match.group(1).strip().strip(' ,') if neg_match else ""
            raw_prompt = re.sub(r'(?:--neg|--negative)[:=\s]+.*?(?=(?:--model|--style|$))', '', raw_prompt, flags=re.I).strip(' ,')

            from services.model_router import normalize_model_choice
            norm_combo = normalize_model_choice(getattr(self, "selected_forge_checkpoint", None))
            if norm_combo:
                pref_style = norm_combo
            elif llm_style and llm_style in active_models:
                pref_style = active_models[llm_style]
            else:
                pref_style = None

            # Ambiguity Gate: предотвращение генерации без визуальных объектов
            words = [w for w in re.findall(r'\b[а-яА-ЯёЁa-zA-Z0-9_-]+\b', raw_prompt.lower()) if len(w) > 2]
            stop_words = {"нарисуй", "покажи", "сделай", "картинку", "изображение", "арт", "draw", "picture", "image"}
            meaningful = [w for w in words if w not in stop_words]

            if meaningful:
                active_c_name = getattr(self.active_character, "name", None)
                v_intent = VisualIntent(
                    prompt=raw_prompt,
                    target_subject=TargetSubject.COMPANION,
                    user_neg=custom_neg,
                    suggested_model=pref_style,
                    seed=-1,
                    raw_user_query=getattr(self, "_current_user_request", ""),
                    session_id=self.current_session.get("id") if getattr(self, "current_session", None) else None,
                    mode="roleplay"
                )
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

        # 4. Формирование контекста под мизансцену
        if scene_type == "intimate":
            gear_status = "ЗАБЛОКИРОВАНО (nude, экипировка скрыта)"
            payload_lines = [
                "Generate concise, comma-separated Danbooru tags for this RP scene:",
                f"- Location: {cur_loc or 'Private quarters'}",
                "- State: strictly alone in private intimate sanctuary (characters undressed, bare skin)",
                f"- Subject: {clean_visual_summary or 'Unspecified'}",
                f"- Scene context: {last_assistant_message or 'Intimate moment'}",
                "CRITICAL: Output ONLY comma-separated tags inside [IMAGE_PROMPT: ...]. Do NOT write prose or paragraphs."
            ]
        else:
            inv_items = cast_data.get("inventory", []) if isinstance(cast_data, dict) else []
            inventory_str = ", ".join(str(i) for i in inv_items) if isinstance(inv_items, list) else str(inv_items)
            comp_outfit = comp_state.get("current_outfit", "") or "Стандартная каноничная одежда"
            
            gear_status = f"АКТИВНО ({inventory_str})" if inventory_str else "В ножнах/убрано"
            payload_lines = [
                "Generate concise, comma-separated Danbooru tags for this RP scene:",
                f"- Location: {cur_loc or 'Unspecified'}",
                f"- Outfit: {comp_outfit}",
                f"- Subject: {clean_visual_summary or 'Unspecified'}",
                f"- Scene context: {last_assistant_message or 'Dialogue scene'}"
            ]
            if inventory_str and scene_type in ("action", "exploration"):
                payload_lines.insert(2, f"- Equipment: {inventory_str}")
            payload_lines.append("CRITICAL: Output ONLY comma-separated tags inside [IMAGE_PROMPT: ...]. Do NOT write prose or paragraphs.")

        logger.info(
            f"[ArtDirector.Cinema] 🎥 Мизансцена: '{scene_type.upper()}' -> "
            f"Применена кино-директива: '{scene_type}'. Снаряжение: {gear_status}"
        )

        from services.forge_models import detect_style_from_checkpoint
        clean_target_style = detect_style_from_checkpoint(target_model_id) if target_model_id else "realistic"
        system_prompt = RoleplayPromptBuilder.get_cinematic_scene_directive(scene_type, clean_target_style)

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

        raw_text_clean = raw_text.strip()
        match = re.search(r'\[IMAGE[\s_-]PROMPT:\s*([\s\S]*?)\]', raw_text_clean, re.IGNORECASE)
        if match:
            prompt_content = match.group(1).strip()
        else:
            line_match = re.search(r'(?:^|\n)[\s*#_-]*IMAGE[\s_-]PROMPT:\s*([^\n\r]+)', raw_text_clean, re.IGNORECASE)
            prompt_content = line_match.group(1).strip() if line_match else raw_text_clean

        # Отсекаем описательные блоки Description и пустые переносы
        prompt_content = re.split(r'\[(?:Description|Narrative|Scene)\]|(?:\n\s*\n)', prompt_content, flags=re.IGNORECASE)[0]
        prompt_content = re.sub(r'^[\s\[]*IMAGE[\s_-]PROMPT:\s*', '', prompt_content, flags=re.IGNORECASE)

        # Срезаем флаг --model со всеми последующими словами до флага или конца строки (включая хвосты v5, v0.9 и т.д.)
        clean_prompt = re.sub(r'--model\s+[^-\n\r]+', '', prompt_content, flags=re.IGNORECASE).strip(' ,[]')
        logger.info(f"[RoleplayFlow.CinemaPrompt] ИТОГОВЫЙ ПРОМПТ ДЛЯ АРТ-ДИРЕКТОРА: '{clean_prompt}'")
        return clean_prompt

