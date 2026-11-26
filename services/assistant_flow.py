"""
services/assistant_flow.py
Headless service layer for Assistant Flow operations:
- Deferred RAG context assembly and inference (run_assistant_inference_deferred)
- Markdown-safe art/trigger sanitization (sanitize_assistant_art_and_markdown)
- Memory sanitization (sanitize_messages_for_memory)
- Cascading sampling parameter resolution and Gallery Art Pass planning
"""
import re
import json
import time
import logging
from typing import Dict, Any, Optional, List, Tuple

from services.session_manager import SessionManager
from services.regex_filter import TextRegexFilter
from services.assistant_prompt_builder import (
    AssistantPromptBuilder,
    IMAGE_GENERATION_SKILLS,
    ART_DIRECTOR_ASSISTANT_JSON_SCHEMA,
)
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


def parse_structured_art_response(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    Безопасно парсит структурированный JSON-ответ арт-директора.
    Использует json.loads с очисткой от Markdown-блоков кода и автоматическое
    восстановление оборванного JSON по балансу кавычек и фигурных скобок.
    """
    if not raw_text or not isinstance(raw_text, str):
        return None

    clean = raw_text.strip()
    if not clean:
        return None

    # Очистка от блоков ```json ... ``` или ``` ... ```
    if "```" in clean:
        clean = re.sub(r'^```(?:json)?\s*', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'\s*```$', '', clean, flags=re.IGNORECASE).strip()

    if "{" not in clean:
        if "[IMAGE_PROMPT" in clean:
            logger.warning("[StructuredArt] Falling back to [IMAGE_PROMPT] legacy format.")
        return None

    data = None
    # Попытка 1: Прямой json.loads
    try:
        candidate = json.loads(clean)
        if isinstance(candidate, dict):
            data = candidate
    except Exception:
        pass

    # Попытка 2: Библиотека json_repair (если установлена)
    if not data:
        try:
            import json_repair
            repaired = json_repair.loads(clean)
            if isinstance(repaired, dict):
                data = repaired
        except Exception:
            pass

    # Попытка 3: Восстановление оборванного JSON (висячие кавычки и фигурные скобки)
    if not data:
        try:
            fixed_text = clean.rstrip()
            if fixed_text.count('"') % 2 != 0:
                fixed_text += '"'
            open_braces = fixed_text.count('{') - fixed_text.count('}')
            if open_braces > 0:
                fixed_text += '}' * open_braces
            candidate = json.loads(fixed_text)
            if isinstance(candidate, dict):
                data = candidate
        except Exception:
            pass

    # Попытка 4: Поиск первого валидного под-объекта через баланс скобок
    if not data:
        start_idx = clean.find("{")
        while start_idx != -1:
            depth = 0
            for idx in range(start_idx, len(clean)):
                if clean[idx] == "{":
                    depth += 1
                elif clean[idx] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate_str = clean[start_idx:idx + 1]
                        try:
                            candidate = json.loads(candidate_str)
                            if isinstance(candidate, dict) and "scene_prompt" in candidate:
                                data = candidate
                                break
                        except Exception:
                            pass
            if data:
                break
            start_idx = clean.find("{", start_idx + 1)

    if data and isinstance(data, dict):
        if "scene_prompt" in data:
            logger.info(
                f"[StructuredArt] JSON parsed successfully: ar={data.get('aspect_ratio')}, "
                f"face={bool(data.get('face_prompt'))}, hands={bool(data.get('hands_prompt'))}, "
                f"self={bool(data.get('is_self_portrait'))}"
            )
            return data

    if "[IMAGE_PROMPT" in raw_text:
        logger.warning("[StructuredArt] Falling back to [IMAGE_PROMPT] legacy format.")
    return None


def sanitize_assistant_art_and_markdown(
    text: str,
    user_requested_art: bool = False,
) -> Tuple[str, bool, Optional[str]]:
    """
    Извлекает реальные арт-триггеры ([GALLERY], [IMAGE_PROMPT: ...], Structured JSON) и очищает ответ Эларии,
    не повреждая примеры тегов, CLI-флагов (--ar/--model) и списков (- ) внутри Markdown-кода.
    """
    if not text:
        return "", False, None

    # Проверка на структурированный JSON арт-плана
    parsed_json = parse_structured_art_response(text)
    if parsed_json is not None:
        reply_ru = str(parsed_json.get("reply_ru") or "Секунду, перевожу образ в кадр...").strip()
        if not reply_ru:
            reply_ru = "Секунду, перевожу образ в кадр..."
        return reply_ru, True, json.dumps(parsed_json, ensure_ascii=False)

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
            # Исключаем явные команды управления памятью и системные подтверждения
            from services.memory_db import handle_memory_command
            if handle_memory_command(clean_text)[0] or clean_text.startswith((
                "Забыла:", "Удалила из памяти", "Записала в долговременную память", "Очистила всю память"
            )):
                continue

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

    t_rag_start = time.perf_counter()
    t_km_ms = 0.0
    t_mem_ms = 0.0

    if actual_prompt:
        t_km_start = time.perf_counter()
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
        finally:
            t_km_ms = (time.perf_counter() - t_km_start) * 1000

    include_memory = kwargs.get("include_memory", True)
    relevant_memory_facts: List[str] = []
    if include_memory and actual_prompt:
        t_mem_start = time.perf_counter()
        try:
            from services.memory_db import memory_db
            relevant_memory_facts = memory_db.search_relevant_facts(actual_prompt, top_k=5)
            if relevant_memory_facts:
                logger.info(
                    f"[AssistantFlow.Deferred] Память: извлечено {len(relevant_memory_facts)} факт(ов) для контекста"
                )
            else:
                logger.info(
                    "[AssistantFlow.Deferred] Память: релевантных фактов не обнаружено"
                )
        except Exception as e_mem_search:
            logger.warning(f"[AssistantFlow.Deferred] Ошибка поиска фактов памяти: {e_mem_search}")
        finally:
            t_mem_ms = (time.perf_counter() - t_mem_start) * 1000

    total_rag_ms = (time.perf_counter() - t_rag_start) * 1000
    logger.info(
        f"[AssistantFlow.Deferred] Сборка контекста RAG завершена за {total_rag_ms:.1f}мс "
        f"(KM: {t_km_ms:.1f}мс, Память: {t_mem_ms:.1f}мс, Документов: {len(matched_knowledge_docs)}, Фактов: {len(relevant_memory_facts)})"
    )

    if curr_sess is None and controller is not None:
        curr_sess = getattr(controller, "current_session", None)

    pending_sources: List[Dict[str, Any]] = []
    if relevant_memory_facts:
        pending_sources.append({
            "filename": "🧠 Долговременная память",
            "chunk_id": f"#{len(relevant_memory_facts)}",
            "snippet": "\n".join(f"• {f}" for f in relevant_memory_facts),
            "score": 1.0,
            "is_memory": True
        })

    if matched_knowledge_docs:
        pending_sources.extend(_build_pending_knowledge_sources(matched_knowledge_docs))
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
        pending_sources.extend(_build_pending_knowledge_sources(matched_knowledge_docs))
    else:
        if curr_sess and isinstance(curr_sess, dict) and curr_sess.get("active_knowledge_docs"):
            from services.hybrid_search import is_conversational_query
            if actual_prompt and not is_conversational_query(actual_prompt):
                curr_sess["active_knowledge_docs"] = []

    if controller:
        controller._pending_knowledge_sources = pending_sources if pending_sources else None

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
    if not history and curr_sess and isinstance(curr_sess, dict) and curr_sess.get("messages"):
        history = SessionManager.reconstruct_chat_history_from_messages(curr_sess["messages"], mode="assistant")

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
        mode="assistant",
        is_roleplay=False,
        character_card=None,
        is_excel=kwargs.get("is_excel", False),
        is_word=kwargs.get("is_word", False),
        vision_content=kwargs.get("vision_content"),
        history_limit=effective_history_limit,
        user_name=effective_user_name,
        include_memory=include_memory,
        relevant_facts=relevant_memory_facts,
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

    # messages[0] остается строго статичным префиксом (Static Prefix Anchoring).
    # Динамический knowledge_block упаковывается в <TurnContext> внутри последнего сообщения пользователя.

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


def resolve_effective_thinking(
    mode: str,
    model_name: str,
    active_skill: Optional[str] = None,
    explicit_override: Optional[bool] = None
) -> Optional[bool]:
    """
    Единый резолвер режима размышлений (Thinking / Reasoning):
    - Если explicit_override is not None -> вернуть explicit_override.
    - Если active_skill == 'gallery' -> вернуть False (принудительное отключение мыслей для SDXL).
    - Проверить _is_thinking_model по THINKING_MODEL_KEYWORDS. Если модель не из списка и thinking_mode == 'auto', вернуть None.
    - Прочитать thinking_mode из ConfigManager.load_config().get('thinking_mode', 'auto'):
      - 'off' -> False
      - 'on' -> True
      - 'auto' -> True для mode == 'engineer', False для mode in ('assistant', 'roleplay').
    """
    if explicit_override is not None:
        return bool(explicit_override)

    if active_skill in IMAGE_GENERATION_SKILLS:
        return False

    from config import THINKING_MODEL_KEYWORDS
    from utils.config_manager import ConfigManager

    _model_lower = str(model_name or "").lower()
    _is_thinking_model = any(k in _model_lower for k in THINKING_MODEL_KEYWORDS)

    cfg = ConfigManager.load_config()
    thinking_mode = str(cfg.get("thinking_mode", "auto")).lower().strip()

    if not _is_thinking_model and thinking_mode == "auto":
        return None

    if thinking_mode == "off":
        return False
    elif thinking_mode == "on":
        return True
    else:  # "auto"
        norm_mode = (mode or "assistant").lower().strip()
        if norm_mode.startswith("eng"):
            return True
        return False


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


def _extract_msg_negative(m: Dict[str, Any]) -> Optional[str]:
    """Извлекает негативный промпт (user_neg от пользователя/модели или fallback из generation_params)."""
    if not isinstance(m, dict):
        return None
    vi = m.get("visual_intent")
    if isinstance(vi, dict) and vi.get("user_neg"):
        neg = str(vi["user_neg"]).strip()
        if neg:
            return neg
    gen_params = m.get("generation_params")
    if isinstance(gen_params, dict) and gen_params.get("negative_prompt"):
        neg = str(gen_params["negative_prompt"]).strip()
        if neg:
            return neg
    if m.get("negative_prompt"):
        neg = str(m["negative_prompt"]).strip()
        if neg:
            return neg
    return None


def extract_last_image_prompt(history_or_messages: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    """
    Extracts the most recent image prompt generated by the assistant from chat_history or session messages.
    Preserves and appends --neg flag if negative prompt was generated or specified by user/model.
    Checks:
    1. Direct 'image_prompt' field in message metadata.
    2. Embedded [IMAGE_PROMPT: ...] tag in content or text.
    3. 'generation_params' dict ('positive_prompt' or 'prompt').
    4. 'visual_intent' dict ('prompt').
    """
    if not history_or_messages or not isinstance(history_or_messages, list):
        return None
    for m in reversed(history_or_messages):
        if not isinstance(m, dict):
            continue
        role = m.get("role") or ("user" if m.get("is_user") else "assistant")
        if role == "user":
            continue
        if m.get("image_prompt"):
            cand = str(m["image_prompt"]).strip()
            if cand:
                neg = _extract_msg_negative(m)
                if neg and "--neg" not in cand and "--negative" not in cand:
                    cand = f"{cand} --neg {neg}"
                return cand
        raw_text = str(m.get("content") if m.get("content") is not None else m.get("text", "")).strip()
        if raw_text:
            extracted, _ = TextRegexFilter.extract_image_prompt(raw_text, strip_thoughts=True, allow_markdown_fallback=True)
            if extracted:
                neg = _extract_msg_negative(m)
                if neg and "--neg" not in extracted and "--negative" not in extracted:
                    extracted = f"{extracted} --neg {neg}"
                return extracted
        gen_params = m.get("generation_params")
        if isinstance(gen_params, dict):
            pos = gen_params.get("positive_prompt") or gen_params.get("prompt")
            if pos and str(pos).strip():
                cand = str(pos).strip()
                neg = _extract_msg_negative(m)
                if neg and "--neg" not in cand and "--negative" not in cand:
                    cand = f"{cand} --neg {neg}"
                return cand
        vi = m.get("visual_intent")
        if isinstance(vi, dict) and vi.get("prompt"):
            pos = str(vi["prompt"]).strip()
            cand = pos
            if vi.get("aspect_ratio"):
                cand = f"{cand} --ar {vi['aspect_ratio']}"
            neg = vi.get("user_neg") or _extract_msg_negative(m)
            if neg and "--neg" not in cand and "--negative" not in cand:
                cand = f"{cand} --neg {neg}"
            return cand
    return None


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
    active_skill: str = "gallery",
    previous_image_prompt: Optional[str] = None,
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
        skill_name=active_skill,
    )

    if previous_image_prompt is None and session and isinstance(session, dict):
        hist_cand = session.get("chat_history") or session.get("messages")
        previous_image_prompt = extract_last_image_prompt(hist_cand)

    llm_messages = []
    if previous_image_prompt:
        llm_messages.append({"role": "assistant", "content": f"[IMAGE_PROMPT: {previous_image_prompt}]"})
    llm_messages.append({"role": "user", "content": art_llm_prompt})

    raw_tags = lm_client.chat_completion(
        messages=llm_messages,
        temperature=target_temperature,
        context_length=4096,
        max_tokens=512,
        stream=False,
        stop=["\n\nПользователь:", "\n\nUser:"],
        is_roleplay=False,
        is_engineer=False,
        enable_thinking=False,
        response_format=ART_DIRECTOR_ASSISTANT_JSON_SCHEMA,
    )

    clean_tags = TextRegexFilter.strip_thought_blocks(raw_tags or "", mask_code=True).strip()

    parsed_json = parse_structured_art_response(clean_tags)
    if parsed_json is not None:
        sp = str(parsed_json.get("scene_prompt") or "").strip()
        fp = parsed_json.get("face_prompt")
        hp = parsed_json.get("hands_prompt")
        neg = str(parsed_json.get("negative_prompt") or "").strip()
        ar = parsed_json.get("aspect_ratio") or "portrait"
        sug_model = parsed_json.get("suggested_model")
        is_self_portrait = bool(parsed_json.get("is_self_portrait"))
        if not is_self_portrait and user_query:
            is_self_portrait = detect_elaria_self_portrait_intent(user_query=user_query, raw_prompt=sp, has_self_flag=False)

        llm_style = resolve_effective_art_style(
            parsed_model_style=sug_model,
            selected_forge_checkpoint=selected_forge_checkpoint,
        ) or sug_model or best_model_id
        target_seed = resolve_seed_lock(user_query, session=session)
        v_intent = VisualIntent(
            prompt=sp,
            face_prompt=fp,
            hands_prompt=hp,
            target_subject=TargetSubject.ELARIA if is_self_portrait else TargetSubject.SCENE_OBJECT,
            user_neg=neg,
            suggested_model=llm_style,
            seed=target_seed,
            aspect_ratio=ar,
            session_id=session_id,
            mode=mode,
        )
    else:
        if "[IMAGE_PROMPT" not in clean_tags:
            logger.warning("[StructuredArt] Structured JSON not found, falling back to [IMAGE_PROMPT] legacy parser.")
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
    Потоковый фильтр арт-тегов ([GALLERY], [IMAGE_PROMPT: ...], Structured JSON) с удержанием неполного префикса
    в скользящем окне и защитой примеров внутри открытого Markdown-кода.
    """

    RE_GALLERY_FULL = RE_GALLERY_TRIGGER
    RE_GALLERY_PREFIX = re.compile(r'\[(?:[gGгГ][a-zA-Zа-яА-ЯёЁ]{0,9})?$', re.IGNORECASE)
    RE_IMAGE_FULL = re.compile(rf'\[{TextRegexFilter.IMAGE_TAG_LABEL_PATTERN}:\s*[^\s\]][^\]]*\]', re.IGNORECASE)
    RE_IMAGE_PREFIX = re.compile(
        r'\[(?:i(?:m(?:g|a(?:g(?:e)?)?)?)?|(?:image|img)[\s_-]?[^\]]*)$',
        re.IGNORECASE,
    )
    RE_REPLY_RU_KEY = re.compile(r'["\']reply_ru["\']\s*:\s*["\']')

    def __init__(self):
        self.buffer: str = ""
        self._json_mode: bool = False
        self._json_reply_emitted: int = 0
        self._json_reply_closed: bool = False

    @property
    def is_json_stream(self) -> bool:
        return self._json_mode

    @is_json_stream.setter
    def is_json_stream(self, val: bool) -> None:
        self._json_mode = bool(val)

    def reset(self) -> None:
        self.buffer = ""
        self._json_mode = False
        self._json_reply_emitted = 0
        self._json_reply_closed = False

    def process_buffer(self, buf: str, prior_text: str = "", force: bool = False) -> Tuple[str, bool]:
        """Фильтрует переданный буфер токенов, обновляя внутренний остаток self.buffer."""
        if not buf:
            return "", False

        # Проверка на JSON-стрим арт-плана (потоковое инкрементальное извлечение reply_ru)
        buf_lstrip = buf.lstrip()
        stripped_comb = (prior_text + buf).lstrip()
        for prefix in ("```json", "```"):
            if buf_lstrip.startswith(prefix):
                buf_lstrip = buf_lstrip[len(prefix):].lstrip()
            if stripped_comb.startswith(prefix):
                stripped_comb = stripped_comb[len(prefix):].lstrip()

        if buf_lstrip.startswith("{") or stripped_comb.startswith("{") or self._json_mode:
            self._json_mode = True
            self.buffer = buf if not force else ""

            if self._json_reply_closed:
                return "", True

            decoded = ""
            match = self.RE_REPLY_RU_KEY.search(buf)
            if match:
                quote_char = match.group(0)[-1]
                val_start = match.end()
                raw_chars = []
                i = val_start
                closed = False
                while i < len(buf):
                    ch = buf[i]
                    if ch == '\\':
                        if i + 1 < len(buf):
                            next_ch = buf[i + 1]
                            if next_ch == quote_char:
                                raw_chars.append(quote_char)
                                i += 2
                            elif next_ch == '\\':
                                raw_chars.append('\\')
                                i += 2
                            elif next_ch == '/':
                                raw_chars.append('/')
                                i += 2
                            elif next_ch == 'b':
                                raw_chars.append('\b')
                                i += 2
                            elif next_ch == 'f':
                                raw_chars.append('\f')
                                i += 2
                            elif next_ch == 'n':
                                raw_chars.append('\n')
                                i += 2
                            elif next_ch == 'r':
                                raw_chars.append('\r')
                                i += 2
                            elif next_ch == 't':
                                raw_chars.append('\t')
                                i += 2
                            elif next_ch == 'u':
                                if i + 5 < len(buf):
                                    hex_str = buf[i + 2:i + 6]
                                    try:
                                        raw_chars.append(chr(int(hex_str, 16)))
                                        i += 6
                                    except ValueError:
                                        break
                                else:
                                    break
                            else:
                                raw_chars.append(next_ch)
                                i += 2
                        else:
                            break
                    elif ch == quote_char:
                        closed = True
                        break
                    else:
                        raw_chars.append(ch)
                        i += 1

                if closed:
                    self._json_reply_closed = True
                decoded = "".join(raw_chars)

            new_piece = ""
            if len(decoded) > self._json_reply_emitted:
                new_piece = decoded[self._json_reply_emitted:]
                self._json_reply_emitted = len(decoded)

            if not force:
                return new_piece, True
            else:
                if self._json_reply_emitted == 0 and not new_piece:
                    parsed = parse_structured_art_response(buf)
                    if parsed and parsed.get("reply_ru"):
                        new_piece = str(parsed["reply_ru"]).strip()
                    else:
                        new_piece = "Секунду, перевожу образ в кадр..."
                return new_piece, True

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
    режима Ассистента (Seed Lock + ModelRouter + --self Эларии + Structured JSON).
    """
    raw_prompt = (extracted_image_prompt or "").strip()

    parsed_json = parse_structured_art_response(raw_prompt)
    if parsed_json is not None:
        sp = str(parsed_json.get("scene_prompt") or "").strip()
        fp = parsed_json.get("face_prompt")
        hp = parsed_json.get("hands_prompt")
        neg = str(parsed_json.get("negative_prompt") or "").strip()
        ar = parsed_json.get("aspect_ratio") or "portrait"
        sug_model = parsed_json.get("suggested_model")
        is_self = bool(parsed_json.get("is_self_portrait"))
        if not is_self and user_query:
            is_self = detect_elaria_self_portrait_intent(user_query=user_query, raw_prompt=sp, has_self_flag=False)

        effective_style = resolve_effective_art_style(
            parsed_model_style=sug_model,
            selected_forge_checkpoint=selected_forge_checkpoint,
        )
        target_seed = resolve_seed_lock(
            user_query=user_query,
            last_art_meta=last_art_meta,
            session=session,
        )
        resolved_sid = session_id or (session.get("id") if isinstance(session, dict) else None)
        v_intent = VisualIntent(
            prompt=sp,
            face_prompt=fp,
            hands_prompt=hp,
            target_subject=TargetSubject.ELARIA if is_self else TargetSubject.SCENE_OBJECT,
            user_neg=neg,
            suggested_model=effective_style or sug_model,
            seed=target_seed,
            aspect_ratio=ar,
            session_id=resolved_sid,
            mode="assistant",
        )
        target_char = "Элария" if is_self else None
        return v_intent, effective_style or sug_model, is_self, target_char

    if "[IMAGE_PROMPT" not in raw_prompt:
        logger.warning("[StructuredArt] Structured JSON not found, falling back to [IMAGE_PROMPT] legacy parser.")
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
            arbiter = VRAMArbiter.get_instance()
            if arbiter.forge_client is forge_client:
                arbiter.release_diffusion_for_llm(force=False, wait_stabilization=False)
                arbiter.schedule_llm_reload(delay=1.2, session_id=session_id, mode=mode)
            else:
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
