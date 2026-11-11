"""
services/assistant_flow.py
Headless service layer for Assistant Flow operations:
- Deferred RAG context assembly and inference (run_assistant_inference_deferred)
- Markdown-safe art/trigger sanitization (sanitize_assistant_art_and_markdown)
- Memory sanitization (sanitize_messages_for_memory)
- Cascading sampling parameter resolution and Gallery Art Pass planning
"""
import re
import logging
from typing import Dict, Any, Optional, List, Tuple

from services.session_manager import SessionManager
from services.regex_filter import TextRegexFilter
from services.assistant_prompt_builder import AssistantPromptBuilder
from services.art_director import (
    ArtDirector,
    VisualIntent,
    TargetSubject,
    parse_image_prompt_flags,
    detect_elaria_self_portrait_intent,
)

logger = logging.getLogger("NeonRoute.AssistantFlow")

RE_IMAGE_TAG = TextRegexFilter.RE_IMAGE_TAG
RE_GALLERY_TRIGGER = re.compile(
    r'\[(?:GALERY|GALLERY|ГАЛЕРЕЯ|ГАЛЕРИЯ|ГАЛЛЕРЕЯ|ГАЛЛЕРИЯ)\]',
    re.IGNORECASE,
)


def sanitize_assistant_art_and_markdown(
    text: str,
    user_requested_art: bool = False,
) -> Tuple[str, bool, Optional[str]]:
    """
    Извлекает реальные арт-триггеры ([GALLERY], [IMAGE_PROMPT: ...]) и очищает ответ Эларии,
    не повреждая примеры тегов, CLI-флагов (--ar/--model) и списков (- ) внутри Markdown-кода.
    """
    if not text:
        return "", False, None

    masked_text, code_placeholders = TextRegexFilter.mask_markdown_code(text)
    masked_text = TextRegexFilter.strip_thought_blocks(masked_text, mask_code=False)
    thought_free_text = TextRegexFilter.restore_markdown_code(masked_text, code_placeholders)

    gallery_match = RE_GALLERY_TRIGGER.search(masked_text)
    has_gallery = gallery_match is not None
    if has_gallery:
        masked_text = RE_GALLERY_TRIGGER.sub('', masked_text)
        masked_text = re.sub(r'!\[.*?\]\(.*?\)', '', masked_text)
    elif user_requested_art and RE_GALLERY_TRIGGER.search(thought_free_text):
        has_gallery = True
        unwrapped = RE_GALLERY_TRIGGER.sub('', thought_free_text)
        unwrapped = re.sub(r'`\s*`', '', unwrapped)
        masked_text, code_placeholders = TextRegexFilter.mask_markdown_code(unwrapped)
        masked_text = re.sub(r'!\[.*?\]\(.*?\)', '', masked_text)

    if has_gallery:
        masked_text = re.sub(r'\n{3,}', '\n\n', masked_text).strip()

    # Очистка от сгенерированных моделью фейковых Markdown-картинок вида ![alt](url) вне блоков кода
    masked_text = re.sub(r'!\[.*?\]\(.*?\)', '', masked_text).strip()

    text_for_img = TextRegexFilter.restore_markdown_code(masked_text, code_placeholders)
    extracted_image_prompt, clean_after_img = TextRegexFilter.extract_image_prompt(
        text_for_img,
        strip_thoughts=False,
        allow_markdown_fallback=user_requested_art,
    )
    restored = TextRegexFilter.process_output(clean_after_img, format_dialogue_dashes=False)

    masked_final, final_placeholders = TextRegexFilter.mask_markdown_code(restored)
    masked_final = re.sub(r'(?:\n|\s)*🔔\s*Напоминаю:[^\n]*', '', masked_final).strip()

    is_art_output = bool(has_gallery or extracted_image_prompt or user_requested_art)
    if is_art_output:
        masked_final = re.sub(r'(?:\s*--(?:model|style|ar|neg|negative)\s+[^\n]+)+$', '', masked_final, flags=re.I).strip()
        masked_final = re.sub(r'(?:\*\*|__)?(?:Positive\s+Prompt|Позитивный\s+промпт)[:\s*]*', '', masked_final, flags=re.IGNORECASE).strip()
    else:
        # Вне арт-ответа убираем только явно висящие с новой строки служебные CLI-флаги, не трогая разговорные примеры
        masked_final = re.sub(r'(?:^|\n)\s*(?:--(?:model|style|ar|neg|negative)\s+[^\n]+)+$', '', masked_final, flags=re.I).strip()

    final_text = TextRegexFilter.restore_markdown_code(masked_final, final_placeholders).strip()
    return final_text, has_gallery, extracted_image_prompt


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


def _build_pending_knowledge_sources(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Формирует список метаданных источников знаний для HUD-чипов сообщения."""
    return [
        {
            "filename": doc.get("filename", ""),
            "chunk_id": doc.get("chunk_id", doc.get("best_chunk_id", 0)),
            "snippet": (doc.get("snippet") or doc.get("content_snippet") or doc.get("content") or "")[:300],
            "score": float(doc.get("score", doc.get("relevance_score", 0.0))),
        }
        for doc in docs
    ]


def assemble_assistant_context(
    actual_prompt: str,
    session_id: Optional[str] = None,
    curr_sess: Optional[Dict[str, Any]] = None,
    controller: Optional[Any] = None,
    **kwargs
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """
    Общая сборка контекста Ассистента (Deferred RAG):
    - Поиск в базе знаний KnowledgeManager с проверкой документной анафоры
    - Срез истории и сборка сообщений через SessionManager / ContextWindowBuilder
    Возвращает: (assembled_messages, matched_knowledge_docs, knowledge_block).
    """
    matched_knowledge_docs: List[Dict[str, Any]] = []
    knowledge_block = ""

    raw_hist_for_rag = kwargs.get("history")
    if raw_hist_for_rag is None and controller and hasattr(controller, "chat_history"):
        raw_hist_for_rag = controller.chat_history
    elif raw_hist_for_rag is None and isinstance(curr_sess, dict):
        raw_hist_for_rag = curr_sess.get("chat_history")

    prev_user_query: Optional[str] = None
    if raw_hist_for_rag:
        for _msg in reversed(raw_hist_for_rag):
            if _msg.get("role") == "user":
                _cand = str(_msg.get("content", "")).strip()
                if _cand and _cand != actual_prompt.strip():
                    prev_user_query = _cand
                    break

    from services.knowledge_manager import KnowledgeManager

    if actual_prompt:
        try:
            matched_knowledge_docs = KnowledgeManager.find_relevant_documents(
                actual_prompt, threshold=0.3, previous_query=prev_user_query
            )
            if matched_knowledge_docs:
                for doc in matched_knowledge_docs:
                    fname = doc.get("filename", "")
                    if fname and not doc.get("content_snippet"):
                        snippet = KnowledgeManager.get_document_knowledge_snippet(fname, max_chars=2000)
                        if snippet:
                            doc["content_snippet"] = snippet
                knowledge_block = AssistantPromptBuilder.build_knowledge_context(matched_knowledge_docs)
                matched_names = [d.get("filename") for d in matched_knowledge_docs]
                logger.info(f"[AssistantFlow.Deferred] База знаний: найдено {len(matched_knowledge_docs)} релевантных док.: {matched_names}")
        except Exception as e_km:
            logger.warning(f"[AssistantFlow.Deferred] Ошибка поиска по базе знаний: {e_km}")

    if curr_sess is None and controller is not None:
        curr_sess = getattr(controller, "current_session", None)

    if matched_knowledge_docs:
        if controller:
            controller._pending_knowledge_sources = _build_pending_knowledge_sources(matched_knowledge_docs)
        if curr_sess and isinstance(curr_sess, dict):
            curr_sess["active_knowledge_docs"] = matched_knowledge_docs
    elif (
        not matched_knowledge_docs
        and curr_sess
        and isinstance(curr_sess, dict)
        and curr_sess.get("active_knowledge_docs")
        and KnowledgeManager.has_document_anaphora(actual_prompt)
    ):
        matched_knowledge_docs = curr_sess["active_knowledge_docs"]
        knowledge_block = AssistantPromptBuilder.build_knowledge_context(matched_knowledge_docs)
        if controller:
            controller._pending_knowledge_sources = _build_pending_knowledge_sources(matched_knowledge_docs)
    else:
        if controller:
            controller._pending_knowledge_sources = None
        if curr_sess and isinstance(curr_sess, dict) and curr_sess.get("active_knowledge_docs"):
            from services.hybrid_search import is_conversational_query
            if actual_prompt and not is_conversational_query(actual_prompt):
                curr_sess["active_knowledge_docs"] = []

    enable_reasoning = kwargs.get("enable_reasoning")
    if enable_reasoning is None:
        enable_reasoning = bool(matched_knowledge_docs)
        if controller:
            if hasattr(controller, "reasoning_switch") and hasattr(controller.reasoning_switch, "isChecked"):
                enable_reasoning = enable_reasoning or controller.reasoning_switch.isChecked()
            elif hasattr(controller, "enable_reasoning_switch") and hasattr(controller.enable_reasoning_switch, "isChecked"):
                enable_reasoning = enable_reasoning or controller.enable_reasoning_switch.isChecked()
            elif getattr(controller, "_enable_reasoning", False):
                enable_reasoning = True

    history = kwargs.get("history")
    if history is None:
        if controller and hasattr(controller, "chat_history") and controller.chat_history:
            history = controller.chat_history
        elif isinstance(curr_sess, dict) and curr_sess.get("chat_history"):
            history = curr_sess.get("chat_history", [])
        elif session_id:
            try:
                s = SessionManager.get_instance().load_session(session_id)
                history = s.get("chat_history", []) if s else []
            except Exception:
                history = []
        else:
            history = []
    if history is None:
        history = []

    effective_history_limit = kwargs.get("history_limit", 60)
    if actual_prompt and (not history or history[-1].get("content") != actual_prompt):
        full_history = list(history) + [{"role": "user", "content": actual_prompt}]
    else:
        full_history = list(history)
    history_slice = AssistantPromptBuilder.slice_history(full_history, max_history_messages=effective_history_limit)

    effective_user_name = kwargs.get("effective_user_name")
    sess_persona_id = kwargs.get("session_persona_id")
    if not effective_user_name:
        if sess_persona_id is None and curr_sess and isinstance(curr_sess, dict):
            sess_persona_id = curr_sess.get("user_persona_id")
        active_p = SessionManager.get_active_user_persona(is_roleplay=False, session_persona_id=sess_persona_id)
        effective_user_name = active_p.get("name") or "Пользователь"

    selected_forge_ckpt = (
        kwargs.get("selected_forge_checkpoint")
        if "selected_forge_checkpoint" in kwargs
        else (getattr(controller, "selected_forge_checkpoint", None) if controller else None)
    )

    assembled_messages = SessionManager.get_lm_studio_messages(
        history=history_slice,
        is_roleplay=False,
        character_card=None,
        is_excel=kwargs.get("is_excel", False),
        is_word=kwargs.get("is_word", False),
        vision_content=kwargs.get("vision_content"),
        history_limit=effective_history_limit,
        user_name=effective_user_name,
        include_memory=kwargs.get("include_memory", True),
        session_id=session_id,
        session_persona_id=sess_persona_id,
        is_engineer=False,
        world_facts=None,
        art_guidance=kwargs.get("art_guidance"),
        active_cast_block="",
        recalled_lore_block="",
        meta_context_block="",
        is_dm_mode=False,
        knowledge_block=knowledge_block,
        matched_knowledge_docs=matched_knowledge_docs,
        enable_reasoning=enable_reasoning,
        user_query=actual_prompt,
        task_text=actual_prompt,
        selected_forge_checkpoint=selected_forge_ckpt,
        active_assistant_skill=kwargs.get("active_assistant_skill"),
        session_metadata=kwargs.get("session_metadata") or (curr_sess.get("metadata") if isinstance(curr_sess, dict) else None),
    )

    if assembled_messages and assembled_messages[0].get("role") == "system":
        sys_content = assembled_messages[0].get("content", "")
        if knowledge_block and "<KnowledgeBase>" not in sys_content:
            sys_content = f"{sys_content}\n\n{knowledge_block}"
        assembled_messages[0]["content"] = sys_content

    return assembled_messages, matched_knowledge_docs, knowledge_block


def run_assistant_inference_deferred(
    prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
    lm_client: Optional[Any] = None,
    controller: Optional[Any] = None,
    **kwargs
) -> Any:
    """
    Фоновый исполнитель задачи Ассистента с отложенной сборкой контекста (Deferred RAG).
    Выполняется в рабочем потоке TaskQueueWorker:
    - Если messages уже передан: прямой вызов lm_client.chat_completion без повторного RAG.
    - Если messages is None: асинхронный поиск в KnowledgeManager и MemoryDB,
      сборка системного промпта и контекста через SessionManager, затем инференс.
    """
    client = lm_client or (getattr(controller, "lm_client", None) if controller else None)
    if client is None:
        raise ValueError("[DeferredRAG] Отсутствует lm_client для выполнения задачи инференса.")

    _ignored_keys = (
        "controller", "user_prompt", "prompt", "session_id", "history_limit",
        "effective_user_name", "include_memory", "vision_content", "art_guidance",
        "enable_reasoning", "session_persona_id", "is_excel", "is_word",
        "lm_client", "history", "selected_forge_checkpoint", "active_assistant_skill",
        "session_metadata",
    )

    # ── ВЕТКА 1: ГОТОВЫЙ КОНТЕКСТ (LEGACY / ТЕСТЫ) ──
    if messages is not None:
        call_kwargs = dict(kwargs)
        call_kwargs["messages"] = messages
        for k in _ignored_keys:
            call_kwargs.pop(k, None)
        return client.chat_completion(**call_kwargs)

    # ── ВЕТКА 2: ОТЛОЖЕННАЯ СБОРКА КОНТЕКСТА В ПОТОКЕ ВОРКЕРА ──
    actual_prompt = user_prompt or prompt or ""
    assembled_messages, _, _ = assemble_assistant_context(
        actual_prompt=actual_prompt,
        session_id=session_id,
        controller=controller,
        **kwargs
    )

    # Делегирование в LM Studio
    call_kwargs = dict(kwargs)
    call_kwargs["messages"] = assembled_messages
    call_kwargs["is_roleplay"] = False
    call_kwargs["is_engineer"] = False
    for k in _ignored_keys:
        call_kwargs.pop(k, None)

    return client.chat_completion(**call_kwargs)


def resolve_gallery_art_temperature(
    session: Optional[Dict[str, Any]] = None,
    req: Optional[Any] = None,
    profile: Optional[Dict[str, Any]] = None,
    default_temp: float = 0.8
) -> float:
    """
    Cascading temperature resolution for Gallery Art Pass:
    1. session.get("sampling_params", {}).get("temperature") (active user session)
    2. req.sampling_overrides.get("temperature") (request body overrides)
    3. profile.get("sampling", {}).get("temperature") (active mode/assistant profile)
    4. default_temp: 0.8
    """
    session_sampling = session.get("sampling_params") if isinstance(session, dict) and isinstance(session.get("sampling_params"), dict) else {}
    profile_sampling = profile.get("sampling") if isinstance(profile, dict) and isinstance(profile.get("sampling"), dict) else {}
    req_overrides = getattr(req, "sampling_overrides", {}) or {}
    if not isinstance(req_overrides, dict):
        req_overrides = {}

    resolved_temp = default_temp
    for candidate in (
        session_sampling.get("temperature"),
        req_overrides.get("temperature"),
        profile_sampling.get("temperature"),
    ):
        if candidate is not None:
            resolved_temp = candidate
            break

    try:
        return float(resolved_temp)
    except (TypeError, ValueError):
        return default_temp


def build_assistant_visual_intent(
    raw_prompt: str,
    user_query: str = "",
    effective_style: Optional[str] = None,
    seed: int = -1,
    session_id: Optional[str] = None,
    mode: str = "assistant",
    fallback_prompt: str = "",
) -> Tuple[VisualIntent, bool, Any]:
    """
    Единый SSOT-конструктор VisualIntent для режима Ассистента:
    разбирает флаги [IMAGE_PROMPT: ...] (--self, --model, --neg, --ar) и определяет автопортрет Эларии.
    Возвращает: (VisualIntent, is_self_portrait, parsed_flags).
    """
    parsed_flags = parse_image_prompt_flags(raw_prompt)
    clean_prompt = parsed_flags.prompt
    custom_neg = parsed_flags.negative
    extracted_ar = parsed_flags.aspect_ratio

    is_self_portrait = (
        mode == "assistant"
        and detect_elaria_self_portrait_intent(
            user_query=user_query,
            raw_prompt=clean_prompt,
            has_self_flag=parsed_flags.is_self_portrait,
        )
    )
    target_subject = TargetSubject.ELARIA if is_self_portrait else TargetSubject.SCENE_OBJECT

    intent = VisualIntent(
        prompt=clean_prompt or fallback_prompt,
        target_subject=target_subject,
        user_neg=custom_neg,
        suggested_model=effective_style,
        seed=seed,
        aspect_ratio=extracted_ar,
        raw_user_query=user_query,
        session_id=session_id,
        mode=mode,
    )
    return intent, is_self_portrait, parsed_flags


def build_gallery_art_plan(
    user_query: str,
    lm_client: Any,
    art_director: Optional[ArtDirector] = None,
    session: Optional[Dict[str, Any]] = None,
    req: Optional[Any] = None,
    profile: Optional[Dict[str, Any]] = None,
    selected_forge_checkpoint: Optional[str] = None,
    session_id: Optional[str] = None,
    mode: str = "assistant",
    check_config_fallback: bool = False,
    available_models: Optional[List[Dict[str, Any]]] = None,
):
    """
    Единый headless-пайплайн второго прохода Gallery Art Pass:
    1. Резолвит целевой профиль Forge (resolve_gallery_target_profile) и температуру.
    2. Генерирует Danbooru/SDXL теги через lm_client.chat_completion.
    3. Парсит SSOT-флаги (parse_image_prompt_flags), Seed Lock и намерение автопортрета (--self).
    4. Возвращает готовый GenerationPlan от ArtDirector.
    """
    best_model_id, target_arch, best_desc, _ = AssistantPromptBuilder.resolve_gallery_target_profile(
        effective_query=user_query,
        selected_forge_checkpoint=selected_forge_checkpoint,
        check_config_fallback=check_config_fallback,
    )
    target_temperature = resolve_gallery_art_temperature(session=session, req=req, profile=profile)

    art_llm_prompt = AssistantPromptBuilder.build_gallery_art_prompt(
        user_query=user_query,
        target_model_id=best_model_id,
        target_model_desc=best_desc,
        architecture=target_arch,
    )

    raw_tags = lm_client.chat_completion(
        messages=[{"role": "user", "content": art_llm_prompt}],
        temperature=target_temperature,
        context_length=4096,
        max_tokens=512,
        stream=False,
        stop=["\n\nПользователь:", "\n\nUser:"],
        is_roleplay=False,
        is_engineer=False,
    )

    clean_tags = TextRegexFilter.strip_thought_blocks(raw_tags or "", mask_code=True).strip()
    extracted_tags, _ = TextRegexFilter.extract_image_prompt(
        clean_tags,
        strip_thoughts=False,
        allow_markdown_fallback=True,
    )
    if not extracted_tags:
        extracted_tags = clean_tags

    parsed_flags = parse_image_prompt_flags(extracted_tags)
    llm_style = parsed_flags.model_style or best_model_id
    target_seed = resolve_seed_lock(user_query, session=session)
    v_intent, is_self_portrait, _ = build_assistant_visual_intent(
        raw_prompt=extracted_tags,
        user_query=user_query,
        effective_style=llm_style,
        seed=target_seed,
        session_id=session_id,
        mode=mode,
        fallback_prompt=extracted_tags,
    )

    ad = art_director or ArtDirector()
    return ad.plan_from_visual_intent(
        v_intent,
        preferred_style=llm_style,
        available_models=available_models,
        character_name="Элария" if is_self_portrait else None,
        session_id=session_id,
        mode=mode,
    )


def should_trigger_assistant_art(user_query: str) -> bool:
    """Проверяет наличие явных запросов на визуализацию для Эларии (без ролевых триггеров)."""
    q = (user_query or "").strip().lower()
    if not q:
        return False

    exclusions = (
        "excel", "эксель", "таблиц", ".xlsx", "docx", "ворд", "word",
        "код", "скрипт", "pygame", "matplotlib", "график", "диаграмм",
        "баз данных", "sql", "функци", "класс", ".py",
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
        "draw", "paint", "sketch", "render", "take a photo", "generate art",
    )
    return any(w in q for w in explicit_triggers)


class StreamArtTagFilter:
    """
    Потоковый фильтр арт-тегов ([GALLERY], [IMAGE_PROMPT: ...]) с удержанием неполного префикса
    в скользящем окне и защитой примеров внутри открытого Markdown-кода.
    """

    RE_GALLERY_FULL = RE_GALLERY_TRIGGER
    RE_GALLERY_PREFIX = re.compile(r'\[(?:[gGгГ][a-zA-Zа-яА-ЯёЁ]{0,9})?$', re.IGNORECASE)
    RE_IMAGE_FULL = re.compile(r'\[IMAGE[\s_-]PROMPT:\s*[^\s\]][^\]]*\]', re.IGNORECASE)
    RE_IMAGE_PREFIX = re.compile(
        r'\[(?:i(?:m(?:g|a(?:g(?:e)?)?)?)?|(?:image|img)[\s_-][^\]]*)$',
        re.IGNORECASE,
    )

    def __init__(self):
        self.buffer: str = ""

    def reset(self) -> None:
        self.buffer = ""

    def process_buffer(self, buf: str, prior_text: str = "", force: bool = False) -> Tuple[str, bool]:
        """Фильтрует переданный буфер токенов, обновляя внутренний остаток self.buffer."""
        if not buf:
            return "", False

        prior_in_fenced = (prior_text.count("```") % 2) == 1
        prior_last_line = prior_text.rsplit("\n", 1)[-1].replace("```", "")
        prior_in_inline = (prior_last_line.count("`") % 2) == 1
        prior_inside_code = prior_in_fenced or prior_in_inline

        if not prior_inside_code and "`" in buf:
            masked_buf, buf_placeholders = TextRegexFilter.mask_markdown_code(buf)
        else:
            masked_buf, buf_placeholders = buf, []

        prefix_probe = prior_text + buf
        in_fenced_code = (prefix_probe.count("```") % 2) == 1
        last_line = prefix_probe.rsplit("\n", 1)[-1].replace("```", "")
        in_inline_code = (last_line.count("`") % 2) == 1
        inside_markdown_code = in_fenced_code or in_inline_code

        has_art_trigger = (not prior_inside_code) and bool(
            self.RE_GALLERY_FULL.search(masked_buf) or self.RE_IMAGE_FULL.search(masked_buf)
        )
        if has_art_trigger:
            masked_buf = self.RE_GALLERY_FULL.sub('', masked_buf)
            masked_buf = self.RE_IMAGE_FULL.sub('', masked_buf)
        buf = TextRegexFilter.restore_markdown_code(masked_buf, buf_placeholders)

        chunk_to_render = buf
        if not force and not inside_markdown_code:
            partial_match = self.RE_GALLERY_PREFIX.search(buf) or self.RE_IMAGE_PREFIX.search(buf)
            if partial_match:
                split_idx = partial_match.start()
                chunk_to_render = buf[:split_idx]
                self.buffer = buf[split_idx:]
            else:
                self.buffer = ""
        else:
            self.buffer = ""

        return chunk_to_render, has_art_trigger

    def feed(self, chunk: str, prior_text: str = "") -> Tuple[str, bool]:
        """Принимает очередной чанк токенов, объединяет с остатком и возвращает готовый фрагмент."""
        if not chunk:
            return "", False
        return self.process_buffer(self.buffer + chunk, prior_text=prior_text, force=False)

    def flush(self, prior_text: str = "") -> Tuple[str, bool]:
        """Сбрасывает удержанный префикс при завершении стрима."""
        if not self.buffer:
            return "", False
        return self.process_buffer(self.buffer, prior_text=prior_text, force=True)


def resolve_effective_art_style(
    parsed_model_style: Optional[str],
    selected_forge_checkpoint: Optional[str] = None,
) -> Optional[str]:
    """Разрешает целевой стиль/чекпоинт: ручной выбор > --model из активных профилей > авто."""
    from services.model_router import ModelRouter, normalize_model_choice

    norm_combo = normalize_model_choice(selected_forge_checkpoint)
    if norm_combo:
        return norm_combo

    if parsed_model_style:
        router = ModelRouter.get_instance() if hasattr(ModelRouter, "get_instance") else ModelRouter()
        active_models = {
            str(m.get("id", "")).lower().strip(): m.get("id")
            for m in router.profiles
            if m.get("active", True) and m.get("id")
        }
        if parsed_model_style in active_models:
            return active_models[parsed_model_style]

    return None


def resolve_seed_lock(
    user_query: str = "",
    last_art_meta: Optional[Dict[str, Any]] = None,
    session: Optional[Dict[str, Any]] = None,
) -> int:
    """Определяет Seed Lock по фразам повторения кадра в последнем запросе пользователя."""
    low_u = (user_query or "").strip().lower()
    if not low_u:
        return -1

    last_gen = last_art_meta or (
        session.get("last_generation", {}) if isinstance(session, dict) else {}
    )
    base_seed = last_gen.get("seed", -1) if isinstance(last_gen, dict) else -1
    if not base_seed or base_seed <= 0:
        return -1

    repeat_phrases = (
        "такую же", "такой же", "такое же", "ту же", "тот же",
        "в той же позе", "в этой же позе", "в том же ракурсе",
        "оставь как есть", "оставь позу", "оставь лицо",
        "то же самое", "тоже самое",
    )
    if any(k in low_u for k in repeat_phrases) or re.search(r'\bповтори\b', low_u):
        return int(base_seed)
    return -1


def has_meaningful_art_prompt(raw_prompt: str) -> bool:
    """Ambiguity Gate: проверяет наличие осмысленных визуальных слов в промпте."""
    words = [w for w in re.findall(r'\b[а-яА-ЯёЁa-zA-Z0-9_-]+\b', (raw_prompt or "").lower()) if len(w) > 2]
    stop_words = {"нарисуй", "покажи", "сделай", "картинку", "изображение", "арт", "draw", "picture", "image"}
    return any(w not in stop_words for w in words)


def prepare_assistant_art_dispatch(
    extracted_image_prompt: str,
    user_query: str = "",
    selected_forge_checkpoint: Optional[str] = None,
    session: Optional[Dict[str, Any]] = None,
    last_art_meta: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> Tuple[VisualIntent, Optional[str], bool, Optional[str]]:
    """
    Готовит VisualIntent, effective_style, is_self_portrait и target_char для однопроходного
    режима Ассистента (Seed Lock + ModelRouter + --self Эларии).
    """
    raw_prompt = (extracted_image_prompt or "").strip()
    parsed_flags = parse_image_prompt_flags(raw_prompt)
    effective_style = resolve_effective_art_style(
        parsed_model_style=parsed_flags.model_style,
        selected_forge_checkpoint=selected_forge_checkpoint,
    )
    target_seed = resolve_seed_lock(
        user_query=user_query,
        last_art_meta=last_art_meta,
        session=session,
    )
    resolved_sid = session_id or (session.get("id") if isinstance(session, dict) else None)
    v_intent, is_self_portrait, _ = build_assistant_visual_intent(
        raw_prompt=raw_prompt,
        user_query=user_query,
        effective_style=effective_style,
        seed=target_seed,
        session_id=resolved_sid,
        mode="assistant",
    )
    target_char = "Элария" if is_self_portrait else None
    return v_intent, effective_style, is_self_portrait, target_char


def prepare_roleplay_inline_art_intent(
    extracted_image_prompt: str,
    user_query: str = "",
    selected_forge_checkpoint: Optional[str] = None,
    session_id: Optional[str] = None,
    character_card: Optional[Any] = None,
) -> Tuple[Optional[VisualIntent], Optional[str]]:
    """
    Готовит VisualIntent для inline-тега [IMAGE_PROMPT: ...] в ролевом режиме (TargetSubject.COMPANION)
    с проверкой Ambiguity Gate.
    """
    raw_prompt = (extracted_image_prompt or "").strip()
    parsed_flags = parse_image_prompt_flags(raw_prompt)
    clean_prompt = parsed_flags.prompt
    if not has_meaningful_art_prompt(clean_prompt):
        return None, None

    pref_style = resolve_effective_art_style(
        parsed_model_style=parsed_flags.model_style,
        selected_forge_checkpoint=selected_forge_checkpoint,
    )
    intent = VisualIntent(
        prompt=clean_prompt,
        target_subject=TargetSubject.COMPANION,
        user_neg=parsed_flags.negative,
        suggested_model=pref_style,
        seed=-1,
        aspect_ratio=parsed_flags.aspect_ratio,
        raw_user_query=user_query,
        session_id=session_id,
        mode="roleplay",
        character_card=character_card,
    )
    return intent, pref_style


def run_forge_art_generation(
    forge_client: Any,
    art_director: Optional[ArtDirector] = None,
    session_id: Optional[str] = None,
    mode: str = "assistant",
    visual_intent: Optional[Any] = None,
    plan: Optional[Any] = None,
    raw_prompt: str = "",
    preferred_style: Optional[str] = None,
    lm_client: Optional[Any] = None,
    character: Optional[Any] = None,
    character_name: Optional[str] = None,
    override_params: Optional[Dict[str, Any]] = None,
    output_dir: Optional[Any] = None,
    unload_after: bool = True,
    prompt_label: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Единый headless-исполнитель генерации изображения в SD WebUI Forge:
    1. Снимает список моделей forge_client.get_models() и строит GenerationPlan (если не передан).
    2. Освобождает LLM из 8 ГБ VRAM через VRAMArbiter.release_llm_for_diffusion().
    3. Выполняет forge_client.text_to_image в директорию сессии.
    4. Выгружает чекпоинт и освобождает VRAM в finally (при unload_after=True).
    5. Собирает last_meta для Seed Lock.
    """
    from pathlib import Path
    from services.vram_arbiter import VRAMArbiter

    ad = art_director or ArtDirector()
    if plan is None:
        available_models = forge_client.get_models() if forge_client else []
        if visual_intent is not None:
            plan = ad.plan_from_visual_intent(
                visual_intent=visual_intent,
                preferred_style=preferred_style,
                available_models=available_models,
                character_name=character_name,
                character=character,
                session_id=session_id,
                mode=mode,
            )
        else:
            plan = ad.plan_generation(
                raw_prompt=raw_prompt,
                preferred_style=preferred_style,
                lm_client=lm_client,
                character=character,
                available_models=available_models,
                character_name=character_name,
                session_id=session_id,
                mode=mode,
            )
            if getattr(plan, "is_ambiguous", False):
                return {
                    "image_path": None,
                    "plan": plan,
                    "checkpoint": None,
                    "forge_kwargs": {},
                    "is_ambiguous": True,
                }
        VRAMArbiter.get_instance().release_llm_for_diffusion()

    target_dir = output_dir or (SessionManager.get_session_images_dir(session_id) if session_id else None)
    forge_kwargs = plan.to_forge_kwargs(output_dir=target_dir, session_id=session_id)
    if override_params and isinstance(override_params, dict):
        forge_kwargs.update(override_params)

    img_path = None
    try:
        img_path = forge_client.text_to_image(**forge_kwargs)
    finally:
        if unload_after and forge_client is not None:
            try:
                forge_client.unload_checkpoint()
                forge_client.free_vram()
            except Exception as e_unl:
                logger.debug(f"[ForgeClient] Ошибка выгрузки чекпоинта: {e_unl}")

    forge_info = getattr(forge_client, "last_generation_info", None) or {}
    actual_seed = forge_info.get("seed", getattr(plan, "seed", -1))
    last_meta = {
        "seed": actual_seed if actual_seed is not None else -1,
        "prompt": prompt_label if prompt_label is not None else (getattr(visual_intent, "prompt", None) or raw_prompt or ""),
        "positive_prompt": getattr(plan, "positive_prompt", ""),
        "checkpoint": getattr(plan, "target_checkpoint", "") or "",
    }
    return {
        "image_path": Path(img_path) if img_path else None,
        "plan": plan,
        "checkpoint": getattr(plan, "target_checkpoint", None),
        "forge_kwargs": forge_kwargs,
        "last_meta": last_meta,
    }
