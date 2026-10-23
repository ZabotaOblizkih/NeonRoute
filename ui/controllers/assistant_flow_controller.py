"""
Assistant Flow & Turn Generation Controller (Mixin) for NeonRoute MainWindow.
Handles Elaria dialogue dispatch, 14K context management, VRAM headroom adaptation,
LanceDB passive memory extraction, and Visual Engine protocol callbacks.
"""

import re
import random
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from services.session_manager import SessionManager, get_mode_profile
from services.regex_filter import TextRegexFilter
from services.task_queue import InferenceTask
from services.model_router import ModelRouter, normalize_model_choice
from services.art_director import VisualIntent, TargetSubject
from ui.components.chat_bubble import ChatMessageWidget

logger = logging.getLogger("NeonRoute.AssistantFlow")

RE_IMAGE_TAG = re.compile(r'\[IMAGE[\s_-]PROMPT:\s*([\s\S]*?)\]', re.IGNORECASE)


class AssistantFlowControllerMixin:
    """Изолированный контроллер диалогового цикла и логики режима «💬 Ассистент» (Элария)."""

    @staticmethod
    def _should_trigger_assistant_art(user_query: str) -> bool:
        """Проверяет наличие явных запросов на визуализацию для Эларии (без ролевых триггеров)."""
        q = (user_query or "").strip().lower()
        if not q:
            return False

        # Защита от ложных срабатываний на коде и документах
        exclusions = (
            "excel", "эксель", "таблиц", ".xlsx", "docx", "ворд", "word",
            "код", "скрипт", "pygame", "matplotlib", "график", "диаграмм",
            "баз данных", "sql", "функци", "класс", ".py"
        )
        if any(w in q for w in exclusions):
            return False

        explicit_triggers = (
            "нарисуй", "нарисуешь", "нарисовать",
            "сфотографируй", "сфоткай", "сделай фото", "сделай снимок", "сделай кадр",
            "сгенерируй", "сгенерируешь", "генерация арта",
            "изобрази", "изобразишь", "начерти", "начертишь",
            "иллюстрируй", "проиллюстрируй", "проиллюстрируешь",
            "отрисуй", "зарисуй", "набросай", "визуализируй",
            "сделай арт", "создай арт", "сделай картинку", "создай картинку", "покажи картинку",
            "покажи рисунок", "покажи арт", "нарисуй мне", "сделай портрет",
            "draw", "paint", "sketch", "render", "take a photo", "generate art"
        )
        return any(w in q for w in explicit_triggers)

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

    def _enqueue_assistant_llm_task(
        self,
        is_excel: bool = False,
        is_word: bool = False,
        vision_content: Optional[List[Dict[str, Any]]] = None,
        art_guidance: Optional[str] = None
    ):
        """Формирует контекст Ассистента (14K) и передает задачу в TaskQueue."""
        self._requested_word_export = is_word
        self._requested_excel_export = is_excel

        if art_guidance is None:
            art_guidance = getattr(self, "_pre_routed_art_guidance", None)

        model_name = self.lm_combo.currentText().strip()
        if model_name.startswith("default"):
            model_name = None

        _req = getattr(self, "_current_user_request", "")
        if not _req and hasattr(self, "chat_history") and self.chat_history:
            for _h in reversed(self.chat_history):
                if _h.get("role") == "user":
                    _req = str(_h.get("content", ""))
                    break

        _should_art = (not vision_content and self._should_trigger_assistant_art(_req))

        mode_profile = get_mode_profile("assistant")
        base_history_limit = mode_profile.get("history_limit", 60)

        # ── ДИНАМИЧЕСКИЙ РАСЧЕТ БЮДЖЕТА ПАМЯТИ И ТОКЕНОВ ──
        if _should_art:
            effective_history_limit = 6
            effective_max_tokens = 512
            history_slice = self.chat_history[-effective_history_limit:] if len(self.chat_history) > effective_history_limit else self.chat_history
        else:
            free_vram = 4500
            if hasattr(self, "system_monitor") and self.system_monitor:
                try:
                    free_vram = self.system_monitor.get_free_vram_mb()
                except Exception:
                    free_vram = 4500

            if free_vram < 4200:
                effective_history_limit = max(12, int(base_history_limit * 0.4))
                effective_max_tokens = 512
            elif free_vram < 5500:
                effective_history_limit = max(20, int(base_history_limit * 0.7))
                effective_max_tokens = 768
            else:
                effective_history_limit = base_history_limit
                effective_max_tokens = mode_profile.get("sampling", {}).get("max_tokens", 1024)

            history_slice = self.chat_history[-effective_history_limit:] if len(self.chat_history) > effective_history_limit else self.chat_history

        curr_sess_id = self.current_session.get("id") if self.current_session else None
        sess_persona_id = self.current_session.get("user_persona_id") if self.current_session else None
        active_p = SessionManager.get_active_user_persona(is_roleplay=False, session_persona_id=sess_persona_id)
        effective_user_name = active_p.get("name") or "Пользователь"

        messages = SessionManager.get_lm_studio_messages(
            history=history_slice,
            is_roleplay=False,
            character_card=None,
            is_excel=is_excel,
            is_word=is_word,
            vision_content=vision_content,
            history_limit=effective_history_limit,
            user_name=effective_user_name,
            include_memory=(not _should_art),
            session_id=curr_sess_id,
            session_persona_id=sess_persona_id,
            is_engineer=False,
            world_facts=None,
            art_guidance=art_guidance,
            active_cast_block="",
            recalled_lore_block="",
            meta_context_block="",
            is_dm_mode=False
        )

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

            _directive = self._get_ephemeral_art_directive(target_model_id=best_model_id, target_model_desc=best_desc, is_roleplay=False)
            if _directive and messages:
                for idx in reversed(range(len(messages))):
                    if messages[idx].get("role") == "user":
                        user_msg_copy = dict(messages[idx])
                        _c = user_msg_copy.get("content")
                        if isinstance(_c, str):
                            user_msg_copy["content"] = _c + _directive
                        messages[idx] = user_msg_copy
                        break

        # ── ДВУХУРОВНЕВЫЙ СЭМПЛИНГ С ЗАЩИТОЙ ОТ МУТАЦИЙ РЕЖИМОВ ──
        sampling = (self.current_session.get("sampling_params") or {}) if self.current_session else {}
        is_custom = bool(self.current_session.get("is_custom_sampling", False)) if self.current_session else False

        cfg_defaults = getattr(self, "app_config", {}).get("default_sampling_assistant", {})
        prof_sampling = mode_profile.get("sampling", {})

        default_temp = cfg_defaults.get("temperature", prof_sampling.get("temperature", 0.70))
        default_min_p = cfg_defaults.get("min_p", prof_sampling.get("min_p", 0.05))
        default_rep_pen = cfg_defaults.get("repeat_penalty", prof_sampling.get("repeat_penalty", 1.05))
        default_max_tok = cfg_defaults.get("max_tokens", prof_sampling.get("max_tokens", 1024))

        if is_custom and "temperature" in sampling:
            temp = float(sampling.get("temperature", default_temp))
            min_p = float(sampling.get("min_p", default_min_p))
            rep_pen = float(sampling.get("repeat_penalty", default_rep_pen))
            user_max_tokens = sampling.get("max_tokens", default_max_tok)
            source_tag = f"Кастомный чат (PKM) [ID: {curr_sess_id}]"
        else:
            temp = float(default_temp)
            min_p = float(default_min_p)
            rep_pen = float(default_rep_pen)
            user_max_tokens = default_max_tok
            source_tag = "Глобальный дефолт config.json (или профиль режима)"

        target_max_tokens = int(user_max_tokens) if (user_max_tokens and not _should_art) else effective_max_tokens
        target_ctx_len = mode_profile.get("context_length", 14336)

        logger.info(
            f"[SamplingResolve] Режим 'assistant' | Сессия: {curr_sess_id} | "
            f"Источник: {source_tag} -> temp={temp}, min_p={min_p}, ctx={target_ctx_len}, max_tokens={target_max_tokens}"
        )

        llm_kwargs = {
            "messages": messages,
            "model": model_name,
            "temperature": temp,
            "stop": ["\n\nПользователь:", "\n\nUser:"],
            "is_roleplay": False,
            "is_engineer": False,
            "context_length": target_ctx_len,
            "max_tokens": target_max_tokens,
            "min_p": min_p,
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
            metadata={
                "is_excel": is_excel,
                "is_word": is_word,
                "is_roleplay": False,
                "has_vision": bool(vision_content)
            }
        )

        self._active_tasks[task.id] = task
        self.task_queue.enqueue(task)

    def _handle_assistant_llm_response(self, text: str):
        """Обрабатывает ответ Эларии, парсит арт-протокол, Ambiguity Gate и инициирует память."""
        img_match = RE_IMAGE_TAG.search(text)
        extracted_image_prompt = None
        if img_match:
            extracted_image_prompt = re.sub(r'^[\s\*_\-\#]+', '', img_match.group(1)).strip()
            clean_text = RE_IMAGE_TAG.sub('', text)
            clean_text = re.sub(r'(?:\n\s*)(?:Элария|Assistant|Пользователь|User)\s*:\s*$', '', clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()
            if not clean_text:
                clean_text = random.choice([
                    "Поймала образ. Сейчас соберу сцену... 🎨",
                    "Отличная задумка. Уже настраиваю рендер.",
                    "Секунду, перевожу мысль в кадр...",
                    "Взяла в работу. Сейчас покажу, как это выглядит."
                ])
            text = clean_text

        text = TextRegexFilter.process_output(text)
        text = re.sub(r'(?:\n|\s)*🔔\s*Напоминаю:[^\n]*', '', text).strip()
        text = re.sub(r'(?:\s*--(?:model|style|ar|neg|negative)\s+[^\n]+)+$', '', text, flags=re.I).strip()
        text = re.sub(r'(?:\*\*|__)?(?:Positive\s+Prompt|Позитивный\s+промпт)[:\s*]*', '', text, flags=re.IGNORECASE).strip()

        if not text:
            text = "Связь с моделью обновлена. Я готова, о чём поговорим?"

        sender_name = "Элария"
        avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None

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
            except (RuntimeError, AttributeError):
                logger.warning("[AssistantFlow] Не удалось отрисовать сообщение: UI чата пересоздается.")
                return

        self._sync_turn(role="assistant", content=text, replace_last=True)
        if hasattr(self, "_check_mode_lock"):
            self._check_mode_lock()

        # ── ОБРАБОТКА АРТ-ПРОТОКОЛА ──
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

            neg_match = re.search(r'(?:--neg|--negative)[:=\s]+(.*?)(?=(?:--model|--style|--ar|$))', raw_prompt, re.I)
            custom_neg = neg_match.group(1).strip().strip(' ,') if neg_match else ""
            raw_prompt = re.sub(r'(?:--neg|--negative)[:=\s]+.*?(?=(?:--model|--style|--ar|$))', '', raw_prompt, flags=re.I).strip(' ,')

            ar_match = re.search(r'--ar[:=\s]+([a-zA-Z0-9:]+)', raw_prompt, re.I)
            extracted_ar = ar_match.group(1).lower().strip() if ar_match else None
            raw_prompt = re.sub(r'--ar[:=\s]+[a-zA-Z0-9:]+', '', raw_prompt, flags=re.I).strip(' ,')

            # Разрешение модели: если в GUI выбран конкретный чекпоинт — передаем его, иначе None (для роутера)
            norm_combo = normalize_model_choice(getattr(self, "selected_forge_checkpoint", None))
            if norm_combo:
                effective_style = norm_combo
            elif llm_style and llm_style in active_models:
                effective_style = active_models[llm_style]
            else:
                effective_style = None

            # Извлечение исходного запроса пользователя
            last_req = getattr(self, "_current_user_request", "").strip()
            if not last_req and hasattr(self, "chat_history") and self.chat_history:
                for _h in reversed(self.chat_history):
                    if _h.get("role") == "user":
                        last_req = str(_h.get("content", "")).strip()
                        break
            low_u = last_req.lower()

            # Безопасное определение автопортрета (отсекаем вежливое обращение по имени)
            clean_user_intent = re.sub(r'^\s*элари[яею]?\b[\s,:-]*', '', low_u, flags=re.I).strip()
            is_self_portrait = bool(
                any(k in clean_user_intent for k in (
                    "себя", "твой портрет", "свой портрет", "твой образ", "свой образ",
                    "как ты выглядишь", "сделай селфи", "твое селфи", "твоё селфи", "автопортрет"
                ))
                or re.search(r'\b(нарисуй|покажи|изобрази)\s+элари[юея]\b', low_u, re.I)
                or re.search(r'\b(elaria|элария)\b', raw_prompt, re.I)
            )

            # Определение сида (Seed Lock)
            last_gen = getattr(self, "_last_art_meta", {}) or (
                self.current_session.get("last_generation", {}) if getattr(self, "current_session", None) else {}
            )
            base_seed = last_gen.get("seed", -1)
            is_seed_lock = bool(base_seed and base_seed > 0 and (
                any(k in low_u for k in [
                    "такую же", "такой же", "такое же", "ту же", "тот же",
                    "в той же позе", "в этой же позе", "в том же ракурсе",
                    "оставь как есть", "оставь позу", "оставь лицо",
                    "то же самое", "тоже самое"
                ]) or
                re.search(r'\bповтори\b', low_u)
            ))
            target_seed = base_seed if is_seed_lock else -1

            target_subject = TargetSubject.ELARIA if is_self_portrait else TargetSubject.SCENE_OBJECT

            v_intent = VisualIntent(
                prompt=raw_prompt,
                target_subject=target_subject,
                user_neg=custom_neg,
                suggested_model=effective_style,
                seed=target_seed,
                aspect_ratio=extracted_ar,
                raw_user_query=last_req,
                session_id=self.current_session.get("id") if getattr(self, "current_session", None) else None,
                mode="assistant"
            )
            msg_widget.visual_intent = v_intent

            target_char = "Элария" if is_self_portrait else None

            auto_art = self.app_config.get("auto_generate_art", False)
            if auto_art and hasattr(self, "pill_forge") and self.pill_forge.is_online:
                self._on_scene_art_requested(visual_intent=v_intent, widget=msg_widget, preferred_style=effective_style, character_name=target_char)
            else:
                msg_widget.add_art_proposal_chip(
                    raw_prompt,
                    lambda v=v_intent, w=msg_widget, s=effective_style, c=target_char: self._on_scene_art_requested(visual_intent=v, widget=w, preferred_style=s, character_name=c)
                )

        self._turn_bubble = None
        if not extracted_image_prompt:
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
            MemoryExtractor.extract_facts_async(messages, context=self)
        except Exception as e:
            logger.warning(f"[AssistantFlow] Ошибка вызова экстракции памяти: {e}")
