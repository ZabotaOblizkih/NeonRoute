"""
Shared Chat & SSE streaming pipeline for NeonRoute Mobile Server.
Unifies /api/chat/stream and /api/chat/regenerate through TaskQueueWorker,
VRAMArbiter, SessionManager, Deferred RAG, and Gallery Art Pass.
"""

import copy
import json
import logging
import asyncio
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from config import MODE_PROFILES, THINKING_MODEL_KEYWORDS, MASTER_MAX_RESPONSE_TOKENS
from services.assistant_flow import (
    RE_GALLERY_TRIGGER,
    StreamArtTagFilter,
    assemble_assistant_context,
    build_gallery_art_plan,
    prepare_assistant_art_dispatch,
    prepare_roleplay_inline_art_intent,
    resolve_effective_thinking,
    run_forge_art_generation,
    sanitize_assistant_art_and_markdown,
    should_trigger_assistant_art,
)
from services.assistant_prompt_builder import AssistantPromptBuilder, IMAGE_GENERATION_SKILLS
from services.context_window_builder import ContextWindowBuilder
from services.lm_studio import LMStudioClient
from services.mode_coordinator import AppMode
from services.regex_filter import TextRegexFilter
from services.roleplay_state_manager import RoleplayStateManager
from services.rp_cast_extractor import RPCastExtractor
from services.session_manager import SessionManager
from services.skill_router import SkillRouter
from services.task_queue import InferenceTask, PRIORITY_HIGH, PRIORITY_NORMAL, TaskQueueWorker
from services.thought_stream_parser import ThoughtStreamParser
from services.tavern_parser import CharacterCard
from utils.config_manager import ConfigManager

from mobile_server.dependencies import get_art_director, get_forge_client, get_task_queue_worker
from mobile_server.schemas import GenerationParamsPayload
from mobile_server.services import art_service
from mobile_server.services.art_service import (
    attach_art_to_session_message,
    get_session_forge_model,
)

logger = logging.getLogger("NeonRoute.MobileServer.ChatService")


def build_chat_context_and_params(
    session: Dict[str, Any],
    session_id: str,
    user_text: str,
    mode_override: Optional[str] = None,
    sampling_overrides: Optional[Dict[str, Any]] = None,
    active_model_name: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], AppMode, Dict[str, Any], Optional[CharacterCard]]:
    """
    Assembles LLM context (with Deferred RAG in assistant mode) and sampling parameters
    using shared service-layer builders.
    """
    # Session's explicit mode/type takes precedence over potentially stale UI mode_override
    has_explicit_session_mode = bool(
        isinstance(session, dict) and (session.get("mode") or session.get("type") == "engineering")
    )
    target_mode = (
        AppMode.from_session(session)
        if has_explicit_session_mode
        else AppMode.from_session(session, mode_override)
    )
    is_roleplay = target_mode == AppMode.ROLEPLAY
    is_engineer = target_mode == AppMode.ENGINEER

    profile = copy.deepcopy(MODE_PROFILES.get(target_mode.value, MODE_PROFILES["assistant"]))

    card_obj: Optional[CharacterCard] = None
    if is_roleplay and session.get("character_data"):
        card_data = session["character_data"]
        card_obj = CharacterCard.from_dict(card_data) if isinstance(card_data, dict) else card_data

    selected_ckpt = get_session_forge_model(session_id, session)

    routed_skill = None
    if target_mode == AppMode.ASSISTANT:
        prompt_builder = AssistantPromptBuilder.get_instance()
        skill_router = getattr(prompt_builder, "router", None) or SkillRouter.get_instance()
        emb = getattr(skill_router, "embedder", None)
        if emb is not None and SkillRouter.get_instance().embedder is None:
            SkillRouter.get_instance(embedder=emb)

        routed_skill, _, _ = skill_router.route(user_text)
        is_art_skill = routed_skill in IMAGE_GENERATION_SKILLS
        profile["active_assistant_skill"] = routed_skill

        context_msgs, _, _ = assemble_assistant_context(
            actual_prompt=user_text,
            session_id=session_id,
            curr_sess=session,
            history=session.get("chat_history", []),
            include_memory=not is_art_skill,
            active_assistant_skill=routed_skill,
            selected_forge_checkpoint=selected_ckpt,
        )
    else:
        context_msgs = ContextWindowBuilder.get_lm_studio_messages(
            history=session.get("chat_history", []),
            is_roleplay=is_roleplay,
            is_engineer=is_engineer,
            character_card=card_obj,
            session_id=session_id,
            include_memory=False,
            user_query=user_text,
            task_text=user_text,
            mode=target_mode.value,
        )

    sampling_params = dict(profile.get("sampling", {}))
    if isinstance(session.get("sampling_params"), dict):
        sampling_params.update(session["sampling_params"])
    if sampling_overrides and isinstance(sampling_overrides, dict):
        sampling_params.update(sampling_overrides)

    # Determine active model identifier for reasoning detection and effective thinking resolution
    model_id = active_model_name
    if not isinstance(model_id, str) or not model_id.strip():
        try:
            cfg = ConfigManager.load_config()
            model_id = cfg.get("assistant_model_name") or cfg.get("active_model") or ""
        except Exception:
            model_id = ""

    # Match desktop AssistantFlow adaptation for thinking models (DeepSeek-R1, Qwen3, QwQ, Gemma4)
    if target_mode == AppMode.ASSISTANT:
        is_custom_sampling = bool(isinstance(session, dict) and session.get("is_custom_sampling"))
        model_lower = str(model_id or "").lower()
        is_thinking_model = any(tok in model_lower for tok in THINKING_MODEL_KEYWORDS)
        if is_thinking_model and not is_custom_sampling:
            if not (isinstance(sampling_overrides, dict) and "xtc_probability" in sampling_overrides):
                sampling_params["xtc_probability"] = 0.0
            if (
                not (isinstance(sampling_overrides, dict) and "max_tokens" in sampling_overrides)
                and int(sampling_params.get("max_tokens", 1024)) == 1024
            ):
                sampling_params["max_tokens"] = MASTER_MAX_RESPONSE_TOKENS

    enable_thinking = resolve_effective_thinking(
        mode=target_mode.value,
        model_name=model_id,
        active_skill=routed_skill if target_mode == AppMode.ASSISTANT else None,
        explicit_override=(
            sampling_overrides.get("enable_thinking")
            if isinstance(sampling_overrides, dict)
            else None
        ),
    )
    sampling_params["enable_thinking"] = enable_thinking

    if target_mode == AppMode.ASSISTANT and routed_skill in IMAGE_GENERATION_SKILLS:
        sampling_params["temperature"] = 0.4
        sampling_params["enable_thinking"] = False

    sampling_params["context_length"] = profile.get("context_length", 14336)
    sampling_params["is_roleplay"] = is_roleplay
    sampling_params["is_engineer"] = is_engineer
    return context_msgs, sampling_params, target_mode, profile, card_obj


_session_events_lock = threading.Lock()
_session_event_subscribers: Dict[str, List[Tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}


def publish_session_event(session_id: str, event_name: str, payload: Dict[str, Any]) -> None:
    """Thread-safe signal publisher from worker threads/routes to per-session SSE subscribers."""
    if not session_id:
        return
    data_str = json.dumps(payload, ensure_ascii=False)
    with _session_events_lock:
        subs = list(_session_event_subscribers.get(session_id, []))
    for loop, queue in subs:
        if loop.is_closed():
            continue
        try:
            loop.call_soon_threadsafe(queue.put_nowait, {"event": event_name, "data": data_str})
        except Exception:
            pass


async def subscribe_session_events(session_id: str) -> AsyncIterator[Dict[str, str]]:
    """Async SSE generator delivering real-time background task push events for session_id."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    entry = (loop, queue)
    with _session_events_lock:
        _session_event_subscribers.setdefault(session_id, []).append(entry)
    try:
        yield {"event": "connected", "data": json.dumps({"session_id": session_id})}
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=20.0)
                yield msg
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "{}"}
    finally:
        with _session_events_lock:
            subs = _session_event_subscribers.get(session_id)
            if subs and entry in subs:
                subs.remove(entry)
                if not subs:
                    _session_event_subscribers.pop(session_id, None)


def _execute_and_persist_forge_task(
    session_id: str,
    forge: Any,
    art_director: Optional[Any] = None,
    target_msg_id: Optional[str] = None,
    mode: str = "assistant",
    visual_intent: Optional[Any] = None,
    plan: Optional[Any] = None,
    preferred_style: Optional[str] = None,
    character: Optional[Any] = None,
    character_name: Optional[str] = None,
    fallback_label: str = "Иллюстрация сцены",
    prompt_label: Optional[str] = None,
) -> None:
    """Runs shared headless Forge generation, unloads VRAM, and attaches image inline to session."""
    if not forge.is_server_ready(timeout=1.0):
        logger.warning("[ChatStream] Forge server is not ready when executing task.")
        publish_session_event(
            session_id,
            "art_failed",
            {"session_id": session_id, "message_id": target_msg_id, "error": "SD Forge server is not ready"},
        )
        return

    sess_out_dir = art_service.OUTPUTS_DIR / "sessions" / session_id
    sess_out_dir.mkdir(parents=True, exist_ok=True)

    gen_res = run_forge_art_generation(
        forge_client=forge,
        art_director=art_director or get_art_director(),
        session_id=session_id,
        mode=mode,
        visual_intent=visual_intent,
        plan=plan,
        preferred_style=preferred_style,
        character=character,
        character_name=character_name,
        output_dir=sess_out_dir,
        unload_after=True,
        prompt_label=prompt_label,
    )
    img_path = gen_res.get("image_path")
    resolved_plan = gen_res.get("plan")
    f_kwargs = gen_res.get("forge_kwargs") or {}
    if not img_path or not resolved_plan:
        publish_session_event(
            session_id,
            "art_failed",
            {"session_id": session_id, "message_id": target_msg_id, "error": "Изображение не было сгенерировано"},
        )
        return

    gen_params = GenerationParamsPayload.from_plan(
        resolved_plan,
        f_kwargs,
        img_path,
        fallback_model=preferred_style or "Auto",
    )
    gen_params_dict = gen_params.model_dump()
    image_url = attach_art_to_session_message(
        session_id=session_id,
        image_path=Path(img_path),
        target_msg_id=target_msg_id,
        gen_params_dict=gen_params_dict,
        last_meta=gen_res.get("last_meta"),
        fallback_label=fallback_label,
    )
    publish_session_event(
        session_id,
        "art_completed",
        {
            "session_id": session_id,
            "message_id": target_msg_id,
            "image_url": image_url,
            "generation_params": gen_params_dict,
        },
    )


def _enqueue_visual_intent_art(
    session_id: str,
    task_queue: TaskQueueWorker,
    visual_intent: Any,
    forge_client: Optional[Any] = None,
    art_director: Optional[Any] = None,
    mode: str = "assistant",
    preferred_style: Optional[str] = None,
    character: Optional[Any] = None,
    character_name: Optional[str] = None,
    target_msg_id: Optional[str] = None,
    fallback_label: str = "Иллюстрация сцены",
) -> None:
    """Enqueues direct Forge rendering for a single-pass VisualIntent."""
    forge = forge_client or get_forge_client()

    def _run_forge():
        try:
            _execute_and_persist_forge_task(
                session_id=session_id,
                forge=forge,
                art_director=art_director,
                target_msg_id=target_msg_id,
                mode=mode,
                visual_intent=visual_intent,
                preferred_style=preferred_style,
                character=character,
                character_name=character_name,
                fallback_label=fallback_label,
                prompt_label=getattr(visual_intent, "prompt", ""),
            )
        except Exception as e_run:
            logger.error(f"[ChatStream] Single-pass Forge execution error: {e_run}", exc_info=True)
            publish_session_event(
                session_id,
                "art_failed",
                {"session_id": session_id, "message_id": target_msg_id, "error": str(e_run)},
            )

    task_queue.enqueue(
        InferenceTask(
            name=f"InlineArt_{session_id}",
            task_type="image",
            func=_run_forge,
            abort_fn=getattr(forge, "interrupt", None),
            priority=PRIORITY_NORMAL,
            metadata={"session_id": session_id},
        )
    )


def _trigger_gallery_art_pass(
    session_id: str,
    session: Dict[str, Any],
    user_text: str,
    profile: Dict[str, Any],
    target_mode: AppMode,
    lm_client: LMStudioClient,
    task_queue: TaskQueueWorker,
    sampling_overrides: Optional[Dict[str, Any]] = None,
    target_msg_id: Optional[str] = None,
    forge_client: Optional[Any] = None,
    art_director: Optional[Any] = None,
    active_skill: Optional[str] = None,
    previous_image_prompt: Optional[str] = None,
) -> None:
    """Runs Gallery Art Pass tag generation and Forge rendering sequentially via TaskQueueWorker."""
    selected_ckpt = get_session_forge_model(session_id, session)

    effective_skill = active_skill
    if not effective_skill or effective_skill not in IMAGE_GENERATION_SKILLS:
        skill_router = SkillRouter.get_instance()
        if skill_router and user_text:
            routed, _, _ = skill_router.route(user_text)
            effective_skill = routed if routed in IMAGE_GENERATION_SKILLS else "gallery"
        else:
            effective_skill = "gallery"

    class _ReqProxy:
        pass

    req_proxy = _ReqProxy()
    req_proxy.sampling_overrides = sampling_overrides or {}

    def _run_gallery_tags_and_enqueue_forge():
        try:
            forge = forge_client or get_forge_client()
            ad = art_director or get_art_director()
            available_models = forge.get_models() if forge else []
            prev_prompt = previous_image_prompt
            if prev_prompt is None and isinstance(session, dict):
                from services.assistant_flow import extract_last_image_prompt
                prev_prompt = extract_last_image_prompt(session.get("chat_history") or session.get("messages"))

            plan = build_gallery_art_plan(
                user_query=user_text,
                lm_client=lm_client,
                art_director=ad,
                session=session,
                req=req_proxy,
                profile=profile,
                selected_forge_checkpoint=selected_ckpt,
                session_id=session_id,
                mode=target_mode.value,
                check_config_fallback=False,
                available_models=available_models,
                active_skill=effective_skill,
                previous_image_prompt=prev_prompt,
            )

            if plan and getattr(plan, "positive_prompt", None):
                art_prompt_tag = f"[IMAGE_PROMPT: {plan.positive_prompt}]"
                if isinstance(session, dict):
                    c_hist = session.get("chat_history", [])
                    if isinstance(c_hist, list):
                        for h_msg in reversed(c_hist):
                            if h_msg.get("role") == "assistant":
                                cur_c = str(h_msg.get("content") or "").strip()
                                if cur_c.startswith("Секунду, перевожу образ в кадр"):
                                    h_msg["content"] = art_prompt_tag
                                elif "[IMAGE_PROMPT:" not in cur_c:
                                    h_msg["content"] = f"{cur_c}\n\n{art_prompt_tag}".strip()
                                break
                    s_msgs = session.get("messages", [])
                    if isinstance(s_msgs, list):
                        for s_msg in reversed(s_msgs):
                            if s_msg.get("id") == target_msg_id or s_msg.get("role") == "assistant" or not s_msg.get("is_user"):
                                s_msg["image_prompt"] = plan.positive_prompt
                                cur_t = str(s_msg.get("text") if s_msg.get("text") is not None else s_msg.get("content", "")).strip()
                                if cur_t.startswith("Секунду, перевожу образ в кадр"):
                                    s_msg["content"] = art_prompt_tag
                                    if "text" in s_msg:
                                        s_msg["text"] = art_prompt_tag
                                elif "[IMAGE_PROMPT:" not in cur_t:
                                    s_msg["content"] = f"{cur_t}\n\n{art_prompt_tag}".strip() if cur_t else art_prompt_tag
                                    if "text" in s_msg:
                                        s_msg["text"] = s_msg["content"]
                                break
                    SessionManager.save_session(session)

            def _run_forge():
                try:
                    _execute_and_persist_forge_task(
                        session_id=session_id,
                        forge=forge,
                        art_director=ad,
                        target_msg_id=target_msg_id,
                        mode=target_mode.value,
                        plan=plan,
                        fallback_label="Галерея Арт-директора",
                        prompt_label=user_text,
                    )
                except Exception as e_run:
                    logger.error(f"[ChatStream] Forge execution error: {e_run}", exc_info=True)
                    publish_session_event(
                        session_id,
                        "art_failed",
                        {"session_id": session_id, "message_id": target_msg_id, "error": str(e_run)},
                    )

            task_queue.enqueue(
                InferenceTask(
                    name=f"GalleryArt_{session_id}",
                    task_type="image",
                    func=_run_forge,
                    abort_fn=getattr(forge, "interrupt", None),
                    priority=PRIORITY_NORMAL,
                    metadata={"session_id": session_id},
                )
            )
        except Exception as e_gal:
            logger.error(f"[ChatStream] Gallery pass error: {e_gal}", exc_info=True)
            publish_session_event(
                session_id,
                "art_failed",
                {"session_id": session_id, "message_id": target_msg_id, "error": str(e_gal)},
            )

    task_queue.enqueue(
        InferenceTask(
            name=f"GalleryArtTags_{session_id}",
            task_type="llm",
            func=_run_gallery_tags_and_enqueue_forge,
            abort_fn=getattr(lm_client, "abort", None),
            priority=PRIORITY_NORMAL,
            metadata={"session_id": session_id, "is_gallery_art_pass": True},
        )
    )


def _trigger_rp_cast_extractor(
    session_id: str,
    session: Dict[str, Any],
    card_obj: Optional[CharacterCard],
) -> None:
    def _rp_extractor_worker():
        try:
            RPCastExtractor.extract_cast_async(
                session_id=session_id,
                recent_turns=session.get("chat_history", [])[-4:],
                companion_name=(session.get("character_data") or {}).get("name", "Элария"),
                character_card=card_obj,
            )
        except Exception as e_rpc:
            logger.debug(f"[ChatStream] RPCastExtractor error: {e_rpc}")

    threading.Thread(target=_rp_extractor_worker, daemon=True).start()


async def stream_chat_sse(
    session_id: str,
    session: Dict[str, Any],
    user_text: str,
    lm_client: LMStudioClient,
    task_queue: TaskQueueWorker,
    mode_override: Optional[str] = None,
    sampling_overrides: Optional[Dict[str, Any]] = None,
    log_prefix: str = "ChatStream",
    forge_client: Optional[Any] = None,
    art_director: Optional[Any] = None,
) -> AsyncIterator[Dict[str, str]]:
    """
    Executes LLM streaming inside TaskQueueWorker (enforcing VRAMArbiter mutual exclusion)
    and yields SSE events ('thought', 'thought_end', 'token', 'done', 'art_started', 'error').
    """
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    state_holder: Dict[str, Any] = {}

    def _run_inference_task():
        try:
            loaded_model = getattr(lm_client, "_last_loaded_model_id", None)
            context_msgs, sampling_params, target_mode, profile, card_obj = build_chat_context_and_params(
                session=session,
                session_id=session_id,
                user_text=user_text,
                mode_override=mode_override,
                sampling_overrides=sampling_overrides,
                active_model_name=loaded_model if isinstance(loaded_model, str) else None,
            )
            state_holder["target_mode"] = target_mode
            state_holder["profile"] = profile
            state_holder["card_obj"] = card_obj

            for token in lm_client.stream_chat_completion(context_msgs, **sampling_params):
                loop.call_soon_threadsafe(queue.put_nowait, ("token", token))
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
        except Exception as exc:
            logger.error(f"[{log_prefix}] Inference error: {exc}", exc_info=True)
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))

    inference_task = InferenceTask(
        name=f"{log_prefix}_{session_id}",
        task_type="llm",
        func=_run_inference_task,
        abort_fn=getattr(lm_client, "abort", None),
        priority=PRIORITY_HIGH,
        metadata={"session_id": session_id},
    )
    task_queue.enqueue(inference_task)

    parser = ThoughtStreamParser()
    art_filter = StreamArtTagFilter()
    emitted_normal_text = ""

    def _clean_stream_piece(piece: str, is_flush: bool = False) -> str:
        nonlocal emitted_normal_text
        if is_flush:
            chunk, _ = art_filter.flush(prior_text=emitted_normal_text)
        else:
            chunk, _ = art_filter.feed(piece, prior_text=emitted_normal_text)
        if chunk:
            chunk = re.sub(r"!\[.*?\]\(.*?\)", "", chunk)
            emitted_normal_text += chunk
        return chunk

    try:
        while True:
            kind, payload = await queue.get()

            if kind == "token":
                for ev, txt in parser.feed(payload):
                    if ev == "thought_chunk":
                        yield {"event": "thought", "data": txt}
                    elif ev == "end_think":
                        yield {"event": "thought_end", "data": ""}

                    elif ev == "normal_chunk":
                        to_yield = _clean_stream_piece(txt, is_flush=False)
                        if to_yield:
                            yield {"event": "token", "data": to_yield}

            elif kind == "done":
                for ev, txt in parser.finalize():
                    if ev == "thought_chunk":
                        yield {"event": "thought", "data": txt}
                    elif ev == "end_think":
                        yield {"event": "thought_end", "data": ""}

                    elif ev == "normal_chunk":
                        to_yield = _clean_stream_piece(txt, is_flush=False)
                        if to_yield:
                            yield {"event": "token", "data": to_yield}

                tail_yield = _clean_stream_piece("", is_flush=True)
                if tail_yield:
                    yield {"event": "token", "data": tail_yield}

                full_normal_raw = parser.normal_text
                thought_data = parser.get_thought_data()
                target_mode: AppMode = state_holder.get("target_mode") or AppMode.from_session(session, mode_override)
                profile: Dict[str, Any] = state_holder.get("profile") or MODE_PROFILES.get(
                    target_mode.value, MODE_PROFILES["assistant"]
                )
                card_obj: Optional[CharacterCard] = state_holder.get("card_obj")
                selected_ckpt = get_session_forge_model(session_id, session)

                has_gallery = False
                extracted_image_prompt: Optional[str] = None
                rp_visual_intent: Optional[Any] = None
                rp_pref_style: Optional[str] = None
                memory_content = full_normal_raw

                if target_mode == AppMode.ASSISTANT:
                    user_asked_art = should_trigger_assistant_art(user_text)
                    full_normal, has_gallery, extracted_image_prompt = sanitize_assistant_art_and_markdown(
                        full_normal_raw,
                        user_requested_art=user_asked_art,
                    )
                    if not full_normal and (has_gallery or extracted_image_prompt):
                        full_normal = "Секунду, перевожу образ в кадр..."
                    memory_content = full_normal
                    if extracted_image_prompt and "[IMAGE_PROMPT:" not in memory_content:
                        memory_content = (full_normal + f"\n\n[IMAGE_PROMPT: {extracted_image_prompt}]").strip()

                elif target_mode == AppMode.ROLEPLAY:
                    # Preserve <stats>, <meters>, <choices> and isolate RP domain logic
                    rp_raw = full_normal_raw
                    for tag in ("stats", "meters", "choices"):
                        open_tag = f"<{tag}>"
                        close_tag = f"</{tag}>"
                        if open_tag in rp_raw and close_tag not in rp_raw:
                            if "```" in rp_raw and rp_raw.count("```") % 2 != 0:
                                rp_raw += "\n```"
                            rp_raw += f"\n{close_tag}"

                    extracted_image_prompt, clean_rp = TextRegexFilter.extract_image_prompt(
                        rp_raw,
                        strip_thoughts=True,
                    )
                    if extracted_image_prompt is not None:
                        rp_raw = clean_rp
                    if extracted_image_prompt:
                        rp_visual_intent, rp_pref_style = prepare_roleplay_inline_art_intent(
                            extracted_image_prompt=extracted_image_prompt,
                            user_query=user_text,
                            selected_forge_checkpoint=selected_ckpt,
                            session_id=session_id,
                            character_card=card_obj,
                        )

                    full_normal = RoleplayStateManager.process_model_turn(session_id, rp_raw)
                    full_normal = TextRegexFilter.process_output(full_normal)
                    full_normal = re.sub(r'(?:\n|\s)*🔔\s*Напоминаю:[^\n]*', '', full_normal).strip()
                    full_normal = re.sub(r'(?:\s*--model\s+[a-zA-Z0-9_\-]+)+\s*$', '', full_normal).strip()
                    if not full_normal and extracted_image_prompt:
                        full_normal = "*Внимательно смотрит на вас, визуализируя образ...*"
                    memory_content = full_normal

                else:
                    full_normal = full_normal_raw.strip()
                    memory_content = full_normal

                asst_msg_id = str(uuid.uuid4())

                def _save_assistant():
                    asst_msg: Dict[str, Any] = {
                        "id": asst_msg_id,
                        "role": "assistant",
                        "content": full_normal,
                        "timestamp": datetime.now().isoformat(),
                    }
                    if thought_data:
                        asst_msg["thought"] = thought_data
                    if extracted_image_prompt:
                        asst_msg["image_prompt"] = extracted_image_prompt
                    if rp_visual_intent is not None and hasattr(rp_visual_intent, "to_dict"):
                        asst_msg["visual_intent"] = rp_visual_intent.to_dict()

                    session.setdefault("messages", []).append(asst_msg)
                    session.setdefault("chat_history", []).append({
                        "role": "assistant",
                        "content": memory_content,
                    })
                    SessionManager.save_session(session)

                    if target_mode == AppMode.ASSISTANT and not extracted_image_prompt:
                        try:
                            from services.memory_extractor import MemoryExtractor
                            from services.assistant_flow import sanitize_messages_for_memory
                            raw_msgs = session.get("messages", [])
                            clean_msgs = sanitize_messages_for_memory(raw_msgs)
                            if clean_msgs and len(clean_msgs) >= 2:
                                MemoryExtractor.extract_facts_async(clean_msgs, lm_client=lm_client)
                        except Exception as e_mem:
                            logger.warning(f"[MobileChatService] Ошибка вызова экстракции памяти: {e_mem}")

                await asyncio.to_thread(_save_assistant)
                yield {"event": "done", "data": full_normal}

                forge = forge_client or get_forge_client()
                ad = art_director or get_art_director()

                if target_mode == AppMode.ASSISTANT and extracted_image_prompt:
                    forge_ready = await asyncio.to_thread(forge.is_server_ready, 1.0)
                    if forge_ready:
                        v_intent, eff_style, _is_self, target_char = prepare_assistant_art_dispatch(
                            extracted_image_prompt=extracted_image_prompt,
                            user_query=user_text,
                            selected_forge_checkpoint=selected_ckpt,
                            session=session,
                            session_id=session_id,
                        )
                        yield {
                            "event": "art_started",
                            "data": json.dumps({
                                "session_id": session_id,
                                "message_id": asst_msg_id,
                                "trigger": "image_prompt",
                            }),
                        }
                        _enqueue_visual_intent_art(
                            session_id=session_id,
                            task_queue=task_queue,
                            visual_intent=v_intent,
                            forge_client=forge,
                            art_director=ad,
                            mode="assistant",
                            preferred_style=eff_style,
                            character_name=target_char,
                            target_msg_id=asst_msg_id,
                            fallback_label="Иллюстрация",
                        )
                    else:
                        yield {
                            "event": "art_error",
                            "data": json.dumps({
                                "session_id": session_id,
                                "error": "SD Forge offline (порт 7860 недоступен). Запустите Forge на ПК.",
                            }),
                        }

                elif target_mode == AppMode.ASSISTANT and has_gallery:
                    forge_ready = await asyncio.to_thread(forge.is_server_ready, 1.0)
                    if forge_ready:
                        yield {
                            "event": "art_started",
                            "data": json.dumps({
                                "session_id": session_id,
                                "message_id": asst_msg_id,
                                "trigger": "gallery",
                            }),
                        }
                        _trigger_gallery_art_pass(
                            session_id=session_id,
                            session=session,
                            user_text=user_text,
                            profile=profile,
                            target_mode=target_mode,
                            lm_client=lm_client,
                            task_queue=task_queue,
                            sampling_overrides=sampling_overrides,
                            target_msg_id=asst_msg_id,
                            forge_client=forge,
                            art_director=ad,
                            active_skill=profile.get("active_assistant_skill") or "gallery",
                        )
                    else:
                        yield {
                            "event": "art_error",
                            "data": json.dumps({
                                "session_id": session_id,
                                "error": "SD Forge offline (порт 7860 недоступен). Запустите Forge на ПК.",
                            }),
                        }

                elif target_mode == AppMode.ROLEPLAY and rp_visual_intent is not None:
                    auto_art = bool(ConfigManager.load_config().get("auto_generate_art", False))
                    if auto_art:
                        forge_ready = await asyncio.to_thread(forge.is_server_ready, 1.0)
                        if forge_ready:
                            yield {
                                "event": "art_started",
                                "data": json.dumps({
                                    "session_id": session_id,
                                    "message_id": asst_msg_id,
                                    "trigger": "rp_inline",
                                }),
                            }
                            _enqueue_visual_intent_art(
                                session_id=session_id,
                                task_queue=task_queue,
                                visual_intent=rp_visual_intent,
                                forge_client=forge,
                                art_director=ad,
                                mode="roleplay",
                                preferred_style=rp_pref_style,
                                character=card_obj,
                                character_name=(card_obj.name if card_obj else None),
                                target_msg_id=asst_msg_id,
                                fallback_label="Иллюстрация сцены",
                            )
                        else:
                            yield {
                                "event": "art_error",
                                "data": json.dumps({
                                    "session_id": session_id,
                                    "error": "SD Forge offline (порт 7860 недоступен). Запустите Forge на ПК.",
                                }),
                            }

                if target_mode == AppMode.ROLEPLAY:
                    _trigger_rp_cast_extractor(session_id, session, card_obj)

                break

            elif kind == "error":
                yield {"event": "error", "data": str(payload)}
                break

    except asyncio.CancelledError:
        logger.info(f"[{log_prefix}] Stream cancelled for session {session_id}")
        inference_task.abort()
        raise
