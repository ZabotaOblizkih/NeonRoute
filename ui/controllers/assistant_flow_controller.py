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
from services.art_director import VisualIntent, TargetSubject
from services.assistant_prompt_builder import AssistantPromptBuilder
from services.document_parser import DocumentParser, ParsedDocument
from services.thought_stream_parser import ThoughtStreamParser, ThinkParserState
from ui.components.chat_bubble import ChatMessageWidget

logger = logging.getLogger("NeonRoute.AssistantFlow")

RE_IMAGE_TAG = re.compile(r'\[IMAGE[\s_-]PROMPT:\s*([\s\S]*?)\]', re.IGNORECASE)
RE_GALLERY_TRIGGER = re.compile(
    r'\[(?:GALERY|GALLERY|ГАЛЕРЕЯ|ГАЛЕРИЯ|ГАЛЛЕРЕЯ|ГАЛЛЕРИЯ)\]',
    re.IGNORECASE
)



def sanitize_messages_for_memory(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Очищает историю сообщений от системных блоков базы знаний (<KnowledgeBase>, <Document>,
    <AttachedDocument>, цитат и технических сниппетов) перед передачей в MemoryExtractor.
    Гарантирует, что мануалы и таблицы не загрязняют долговременную память пользователя в LanceDB.
    """
    if not messages:
        return []

    sanitized: List[Dict[str, Any]] = []
    patterns = [
        re.compile(r"<KnowledgeBase>[\s\S]*?</KnowledgeBase>", re.IGNORECASE),
        re.compile(r"<Document[\s\S]*?</Document>", re.IGNORECASE),
        re.compile(r"<AttachedDocument[\s\S]*?</AttachedDocument>", re.IGNORECASE),
        re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE),
        re.compile(r"\[Источник:\s*[^\]]+\]", re.IGNORECASE),
        re.compile(r"\[Документ:\s*[^\]]+\]", re.IGNORECASE),
        re.compile(r"\[Chunk\s*#?[0-9a-zA-Z_-]+\]", re.IGNORECASE),
    ]

    for m in messages:
        if not isinstance(m, dict):
            continue
        m_copy = dict(m)
        raw_text = str(m_copy.get("text") or m_copy.get("content") or "")

        clean_text = raw_text
        for pat in patterns:
            clean_text = pat.sub("", clean_text)

        clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()

        if clean_text:
            if "content" in m:
                m_copy["content"] = clean_text
            if "text" in m:
                m_copy["text"] = clean_text
            if "content" not in m and "text" not in m:
                m_copy["content"] = clean_text
            sanitized.append(m_copy)

    return sanitized


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
        art_guidance: Optional[str] = None,
        enable_reasoning: Optional[bool] = None
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

        _should_art = False

        mode_profile = get_mode_profile("assistant")
        base_history_limit = mode_profile.get("history_limit", 60)

        # ── ДИНАМИЧЕСКИЙ РАСЧЕТ БЮДЖЕТА ПАМЯТИ И ТОКЕНОВ (ДВУХКОНТУРНАЯ МОДЕЛЬ) ──
        if _should_art:
            effective_history_limit = 6
            effective_max_tokens = 512
            history_slice = AssistantPromptBuilder.slice_history(self.chat_history, max_history_messages=effective_history_limit)
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

            history_slice = AssistantPromptBuilder.slice_history(self.chat_history, max_history_messages=effective_history_limit)

        curr_sess = getattr(self, "current_session", None) or {}
        curr_sess_id = curr_sess.get("id") if isinstance(curr_sess, dict) else (self.current_session.get("id") if self.current_session else None)
        sess_persona_id = curr_sess.get("user_persona_id") if isinstance(curr_sess, dict) else (self.current_session.get("user_persona_id") if self.current_session else None)
        active_p = SessionManager.get_active_user_persona(is_roleplay=False, session_persona_id=sess_persona_id)
        effective_user_name = active_p.get("name") or "Пользователь"

        # ── ПОИСК И ИНЪЕКЦИЯ ЗНАНИЙ ЭЛАРИИ (CPU, 0 МБ VRAM) ──
        matched_knowledge_docs: List[Dict[str, Any]] = []
        knowledge_block = ""
        if _req and not _should_art:
            try:
                from services.knowledge_manager import KnowledgeManager
                matched_knowledge_docs = KnowledgeManager.find_relevant_documents(_req, threshold=0.3)
                if matched_knowledge_docs:
                    for doc in matched_knowledge_docs:
                        fname = doc.get("filename", "")
                        if fname and not doc.get("content_snippet"):
                            snippet = KnowledgeManager.get_document_knowledge_snippet(fname, max_chars=2000)
                            if snippet:
                                doc["content_snippet"] = snippet
                    knowledge_block = AssistantPromptBuilder.build_knowledge_context(matched_knowledge_docs)
                    matched_names = [d.get("filename") for d in matched_knowledge_docs]
                    logger.info(f"[AssistantFlow] База знаний: найдено {len(matched_knowledge_docs)} релевантных документов для запроса: {matched_names}")

                    if curr_sess and isinstance(curr_sess, dict):
                        curr_sess["active_knowledge_docs"] = matched_knowledge_docs
            except Exception as e_km:
                logger.warning(f"[AssistantFlow] Ошибка поиска по базе знаний: {e_km}")

        if not matched_knowledge_docs and curr_sess.get("active_knowledge_docs"):
            matched_knowledge_docs = curr_sess["active_knowledge_docs"]
            knowledge_block = AssistantPromptBuilder.build_knowledge_context(matched_knowledge_docs)
            logger.info(f"[AssistantFlow] Использован сессионный контекст знаний: {len(matched_knowledge_docs)} док.")

        if matched_knowledge_docs:
            self._pending_knowledge_sources = [
                {
                    "filename": doc.get("filename", ""),
                    "chunk_id": doc.get("chunk_id", doc.get("best_chunk_id", 0)),
                    "snippet": (doc.get("snippet") or doc.get("content_snippet") or doc.get("content") or "")[:300],
                    "score": float(doc.get("score", doc.get("relevance_score", 0.0)))
                }
                for doc in matched_knowledge_docs
            ]
        else:
            self._pending_knowledge_sources = None

        if enable_reasoning is None:
            enable_reasoning = bool(matched_knowledge_docs)
            if hasattr(self, "reasoning_switch") and hasattr(self.reasoning_switch, "isChecked"):
                enable_reasoning = enable_reasoning or self.reasoning_switch.isChecked()
            elif hasattr(self, "enable_reasoning_switch") and hasattr(self.enable_reasoning_switch, "isChecked"):
                enable_reasoning = enable_reasoning or self.enable_reasoning_switch.isChecked()
            elif getattr(self, "_enable_reasoning", False):
                enable_reasoning = True

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
            is_dm_mode=False,
            knowledge_block=knowledge_block,
            matched_knowledge_docs=matched_knowledge_docs,
            enable_reasoning=enable_reasoning,
            user_query=_req,
            task_text=_req
        )

        # Дополнительная страховка наличия KnowledgeBase и REASONING_PROTOCOL в системном промпте
        if messages and messages[0].get("role") == "system":
            sys_content = messages[0].get("content", "")
            if knowledge_block and "<KnowledgeBase>" not in sys_content:
                sys_content = f"{sys_content}\n\n{knowledge_block}"
            if enable_reasoning and "<think>" not in sys_content:
                from services.assistant_prompt_builder import REASONING_PROTOCOL
                sys_content = f"{sys_content}\n\n{REASONING_PROTOCOL}"
            messages[0]["content"] = sys_content

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
        default_dry = cfg_defaults.get("dry_multiplier", prof_sampling.get("dry_multiplier", 0.80))

        if is_custom and "temperature" in sampling:
            temp = float(sampling.get("temperature", default_temp))
            min_p = float(sampling.get("min_p", default_min_p))
            rep_pen = float(sampling.get("repeat_penalty", default_rep_pen))
            user_max_tokens = sampling.get("max_tokens", default_max_tok)
            dry_mult = float(sampling.get("dry_multiplier", default_dry))
            source_tag = f"Кастомный чат (PKM) [ID: {curr_sess_id}]"
        else:
            temp = float(default_temp)
            min_p = float(default_min_p)
            rep_pen = float(default_rep_pen)
            user_max_tokens = default_max_tok
            dry_mult = float(default_dry)
            source_tag = "Глобальный дефолт config.json (или профиль режима)"

        if _should_art:
            target_max_tokens = 512
        elif not is_custom or "max_tokens" not in sampling:
            # Штатная динамическая подстановка ступени двухконтурной модели (1024, 768, 512)
            target_max_tokens = effective_max_tokens
        else:
            # Кастомный чат: ограничиваем сверху эффективным потолком ступени
            target_max_tokens = min(int(user_max_tokens), effective_max_tokens)
        target_ctx_len = mode_profile.get("context_length", 14336)

        logger.info(
            f"[SamplingResolve] Режим 'assistant' | Сессия: {curr_sess_id} | "
            f"Источник: {source_tag} -> temp={temp}, min_p={min_p}, dry_multiplier={dry_mult}, ctx={target_ctx_len}, max_tokens={target_max_tokens}"
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
            "dry_multiplier": dry_mult,
            "dry_base": 1.75,
            "dry_allowed_length": 2,
            "dry_penalty_last_n": 0,
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
                "has_vision": bool(vision_content),
                "knowledge_sources": getattr(self, "_pending_knowledge_sources", [])
            }
        )

        print(f"\n[DIAG_THINK][1. ENQUEUE] Messages count: {len(messages)}")
        sys_c = messages[0].get("content", "") if messages else ""
        print(f"[DIAG_THINK][1. ENQUEUE] Has <KnowledgeBase> in system: {'<KnowledgeBase>' in sys_c}")
        print(f"[DIAG_THINK][1. ENQUEUE] Has <think> directive in system: {'<think>' in sys_c}")
        print(f"[DIAG_THINK][1. ENQUEUE] Last message role: {messages[-1].get('role')} | content: {repr(messages[-1].get('content', ''))[:100]}")
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
        last_req = (raw_user_query or "").strip()
        if not last_req:
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
        if target_widget is not None:
            target_widget.visual_intent = v_intent

        target_char = "Элария" if is_self_portrait else None

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
        best_model_id = "realistic"
        best_desc = ""
        try:
            router = ModelRouter.get_instance() if hasattr(ModelRouter, "get_instance") else ModelRouter()
            matched_id, score = router.match_prompt(user_query)
            if matched_id:
                best_model_id = matched_id
                for m in router.profiles:
                    if m.get("id") == matched_id:
                        guidance = (m.get("llm_guidance") or "").strip()
                        best_desc = guidance if guidance else (m.get("routing_description") or m.get("name") or "")
                        break
        except Exception:
            pass

        full_prompt = AssistantPromptBuilder.build_gallery_art_prompt(user_query, best_model_id, best_desc)
        messages = [{"role": "user", "content": full_prompt}]

        model_name = None
        if hasattr(self, "lm_combo") and self.lm_combo:
            model_name = self.lm_combo.currentText().strip()
            if model_name.startswith("default"):
                model_name = None

        llm_kwargs = {
            "messages": messages,
            "model": model_name,
            "temperature": 0.40,
            "stop": ["\n\nПользователь:", "\n\nUser:"],
            "is_roleplay": False,
            "is_engineer": False,
            "context_length": 4096,
            "max_tokens": 512,
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
            self._thought_stream_parser = None

        # Фоллбэк: если в ответе присутствуют теги <think>...</think>
        if not thought_data and "<think>" in text.lower():
            m_th = re.search(r"<think>([\s\S]*?)(?:</think>|$)", text, flags=re.IGNORECASE)
            if m_th:
                th_body = m_th.group(1).strip()
                if th_body:
                    thought_data = {
                        "content": th_body,
                        "duration": 0.0,
                        "token_count": max(1, len(th_body) // 4)
                    }

        # Очищаем теги <think>...</think> из итогового текста ответа
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()

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

        if getattr(self, "_is_running_gallery_art_pass", False):
            self._is_running_gallery_art_pass = False
            target_widget = getattr(self, "_pending_gallery_art_widget", None)
            user_query = getattr(self, "_pending_gallery_user_query", "")
            self._pending_gallery_art_widget = None
            self._pending_gallery_user_query = None

            img_match = RE_IMAGE_TAG.search(text)
            if img_match:
                extracted = re.sub(r'^[\s\*_\-\#]+', '', img_match.group(1)).strip()
                self._dispatch_art_to_forge(extracted, target_widget, user_query)
            else:
                if target_widget and hasattr(target_widget, "hide_art_loading_placeholder"):
                    target_widget.hide_art_loading_placeholder()
            return

        gallery_match = RE_GALLERY_TRIGGER.search(text)
        has_gallery = gallery_match is not None
        if has_gallery:
            # Вырезаем сам маркер из любой позиции (в начале, в середине или в конце)
            text = RE_GALLERY_TRIGGER.sub('', text)
            # Вырезаем любые фейковые markdown-картинки вида ![alt](url)
            text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
            # Схлопываем избыточные переносы строк
            text = re.sub(r'\n{3,}', '\n\n', text).strip()
            # Если текста после очистки не осталось — оставляем пустую строку (без заглушек)
            if not text:
                text = ""

        # Очистка от сгенерированных моделью фейковых Markdown-картинок вида ![alt](url)
        text = re.sub(r'!\[.*?\]\(.*?\)', '', text).strip()

        img_match = RE_IMAGE_TAG.search(text)
        extracted_image_prompt = None
        if img_match:
            extracted_image_prompt = re.sub(r'^[\s\*_\-\#]+', '', img_match.group(1)).strip()
            clean_text = RE_IMAGE_TAG.sub('', text)
            clean_text = re.sub(r'(?:\n\s*)(?:Элария|Assistant|Пользователь|User)\s*:\s*$', '', clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()
            text = clean_text

        text = TextRegexFilter.process_output(text)
        text = re.sub(r'(?:\n|\s)*🔔\s*Напоминаю:[^\n]*', '', text).strip()
        text = re.sub(r'(?:\s*--(?:model|style|ar|neg|negative)\s+[^\n]+)+$', '', text, flags=re.I).strip()
        text = re.sub(r'(?:\*\*|__)?(?:Positive\s+Prompt|Позитивный\s+промпт)[:\s*]*', '', text, flags=re.IGNORECASE).strip()

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

        # Активация киберпанк-лоадера ожидания кадра в бабле
        if has_gallery and hasattr(msg_widget, "show_art_loading_placeholder"):
            msg_widget.show_art_loading_placeholder()

        print(f"[DIAG_THINK][3. FINALIZE] thought_data is None: {thought_data is None}")
        if thought_data:
            print(f"[DIAG_THINK][3. FINALIZE] thought content preview: {repr(thought_data.get('content', ''))[:120]}")
        print(f"[DIAG_THINK][3. FINALIZE] raw text preview: {repr(text)[:120]}")
        self._sync_turn(role="assistant", content=text, replace_last=True, thought=thought_data)
        if hasattr(self, "_check_mode_lock"):
            self._check_mode_lock()

        # ── ВТОРОЙ ПРОХОД: [GALERY] ──
        if has_gallery:
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

