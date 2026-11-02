"""
Chat inference routes: streaming LLM generation via Server-Sent Events (SSE).
"""

from pathlib import Path
import re
import uuid
import copy
import json
import logging
import asyncio
import threading
from datetime import datetime
from typing import Dict, Any, Optional
from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from config import HISTORY_DIR, MODE_PROFILES
from utils.file_utils import atomic_write_json
from services.session_manager import SessionManager
from services.context_window_builder import ContextWindowBuilder
from services.thought_stream_parser import ThoughtStreamParser
from services.tavern_parser import CharacterCard
from services.lm_studio import LMStudioClient
from services.rp_cast_extractor import RPCastExtractor
from services.art_director.director import ArtDirector
from services.art_director.contracts import VisualIntent
from services.task_queue import get_task_queue, InferenceTask, TaskQueueWorker, PRIORITY_NORMAL
from services.forge_client import ForgeClient
from services.assistant_prompt_builder import AssistantPromptBuilder
from services.skill_router import SkillRouter
from config import OUTPUTS_DIR

from mobile_server.schemas import SendMessageRequest
from mobile_server.dependencies import get_session_manager, get_lm_studio_client, get_task_queue_worker

logger = logging.getLogger("NeonRoute.MobileServer.Chat")
router = APIRouter()

# Соответствует: [GALERY], [GALLERY], [ГАЛЕРЕЯ], [ГАЛЕРИЯ], [ГАЛЛЕРЕЯ], [ГАЛЛЕРИЯ]
RE_GALLERY_TRIGGER = re.compile(
    r'\[(?:GALERY|GALLERY|\u0413\u0410\u041b\u0415\u0420\u0415\u042f|\u0413\u0410\u041b\u0415\u0420\u0418\u042f|\u0413\u0410\u041b\u041b\u0415\u0420\u0415\u042f|\u0413\u0410\u041b\u041b\u0415\u0420\u0418\u042f)\]',
    re.IGNORECASE
)
RE_IMAGE_TAG = re.compile(r'\[IMAGE[\s_-]PROMPT:\s*([\s\S]*?)\]', re.IGNORECASE)


@router.post("/stream")
async def chat_stream(
    req: SendMessageRequest,
    _manager: SessionManager = Depends(get_session_manager),
    lm_client: LMStudioClient = Depends(get_lm_studio_client),
):
    """
    Streams LLM completion via SSE for mobile clients.
    Separates <think> thoughts and output tokens in real-time.
    """
    chat_file = HISTORY_DIR / f"chat_{req.session_id}.json"

    def _load_session():
        if not chat_file.exists():
            return None
        try:
            with open(chat_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    session = await asyncio.to_thread(_load_session)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 1. Append user message to session
    user_msg = {
        "id": str(uuid.uuid4()),
        "role": "user",
        "content": req.text,
        "timestamp": datetime.now().isoformat(),
    }
    session.setdefault("messages", []).append(user_msg)
    session.setdefault("chat_history", []).append({"role": "user", "content": req.text})

    # Update session title if default
    if not session.get("is_custom_titled") and session.get("title") in ("\u041d\u043e\u0432\u044b\u0439 \u0434\u0438\u0430\u043b\u043e\u0433", "\u0414\u0438\u0430\u043b\u043e\u0433", "", None):
        char_name = (session.get("character_data") or {}).get("name", "")
        smart_title = SessionManager.generate_smart_title(req.text, mode=req.mode, character_name=char_name)
        session["title"] = smart_title

    session["updated_at"] = datetime.now().isoformat()
    await asyncio.to_thread(atomic_write_json, chat_file, session)
    SessionManager.set_active_session(session)

    # 2. Build context window based on active mode
    mode = req.mode.lower().strip()
    is_roleplay = (mode == "roleplay")
    is_engineer = (mode in ("engineer", "engineering") or session.get("type") == "engineering")

    profile_key = "engineer" if is_engineer else ("roleplay" if is_roleplay else "assistant")
    profile = copy.deepcopy(MODE_PROFILES.get(profile_key, MODE_PROFILES["assistant"]))

    card_obj = None
    if is_roleplay and session.get("character_data"):
        card_data = session["character_data"]
        card_obj = CharacterCard.from_dict(card_data) if isinstance(card_data, dict) else card_data

    # Check SkillRouter for gallery trigger
    skill_router = SkillRouter.get_instance() if hasattr(SkillRouter, "get_instance") else SkillRouter()
    routed_skill, _, _ = skill_router.route(req.text)
    is_gallery_skill = (routed_skill == "gallery")

    def _build_context():
        msgs = ContextWindowBuilder.get_lm_studio_messages(
            history=session.get("chat_history", []),
            is_roleplay=is_roleplay,
            is_engineer=is_engineer,
            character_card=card_obj,
            session_id=req.session_id,
            include_memory=(not is_roleplay and not is_engineer and not is_gallery_skill),
            user_query=req.text,
            task_text=req.text,
            active_assistant_skill="gallery" if is_gallery_skill else None,
        )
        if is_gallery_skill and msgs and msgs[0].get("role") == "system":
            sys_content = msgs[0]["content"]
            if "\u0412\u0418\u0417\u0423\u0410\u041b\u0418\u0417\u0410\u0426\u0418\u042f" not in sys_content and "[GALERY]" not in sys_content:
                gallery_directive = AssistantPromptBuilder.SKILL_PROMPTS.get("gallery", "").format(
                    user_name="\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c"
                )
                msgs[0]["content"] = f"{sys_content}\n\n{gallery_directive}".strip()
        return msgs

    context_msgs = await asyncio.to_thread(_build_context)

    # 3. Configure sampling parameters
    sampling_params = dict(profile.get("sampling", {}))
    if isinstance(session.get("sampling_params"), dict):
        sampling_params.update(session["sampling_params"])
    if req.sampling_overrides and isinstance(req.sampling_overrides, dict):
        sampling_params.update(req.sampling_overrides)

    sampling_params["context_length"] = profile.get("context_length", 14336)

    # 4. Asynchronous queue for background generator
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def run_inference():
        try:
            for token in lm_client.stream_chat_completion(context_msgs, **sampling_params):
                loop.call_soon_threadsafe(queue.put_nowait, ("token", token))
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
        except Exception as exc:
            logger.error(f"[ChatStream] Inference error: {exc}", exc_info=True)
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))

    threading.Thread(target=run_inference, daemon=True).start()

    # 5. SSE generator
    async def event_generator():
        parser = ThoughtStreamParser()
        gallery_checked = False
        normal_lead_buf = ""

        def _clean_token_lead(buf: str) -> str:
            cleaned = RE_GALLERY_TRIGGER.sub("", buf)
            cleaned = re.sub(r"!\[.*?\]\(.*?\)", "", cleaned)
            return cleaned

        try:
            while True:
                kind, payload = await queue.get()

                if kind == "token":
                    events = parser.feed(payload)
                    for ev, txt in events:
                        if ev == "thought_chunk":
                            yield {"event": "thought", "data": txt}
                        elif ev == "end_think":
                            yield {"event": "thought_end", "data": ""}
                        elif ev == "normal_chunk":
                            if not gallery_checked:
                                normal_lead_buf += txt
                                if "]" in normal_lead_buf or len(normal_lead_buf) > 60:
                                    gallery_checked = True
                                    to_yield = _clean_token_lead(normal_lead_buf)
                                    normal_lead_buf = ""
                                    if to_yield:
                                        yield {"event": "token", "data": to_yield}
                            else:
                                yield {"event": "token", "data": txt}

                elif kind == "done":
                    rem = parser.finalize()
                    for ev, txt in rem:
                        if ev == "thought_chunk":
                            yield {"event": "thought", "data": txt}
                        elif ev == "end_think":
                            yield {"event": "thought_end", "data": ""}
                        elif ev == "normal_chunk":
                            if not gallery_checked:
                                normal_lead_buf += txt
                            else:
                                yield {"event": "token", "data": txt}

                    if not gallery_checked and normal_lead_buf:
                        gallery_checked = True
                        to_yield = _clean_token_lead(normal_lead_buf)
                        normal_lead_buf = ""
                        if to_yield:
                            yield {"event": "token", "data": to_yield}

                    full_normal = parser.normal_text
                    full_thought = parser.thought_text.strip() if parser.thought_text else None

                    # Check for gallery trigger
                    has_gallery = bool(RE_GALLERY_TRIGGER.search(full_normal))
                    if has_gallery:
                        # 1. Вырезаем сам маркер
                        full_normal = RE_GALLERY_TRIGGER.sub('', full_normal).strip()
                        # 2. Вырезаем любые галлюцинации модели в виде фейковых картинок (как в десктопе)
                        full_normal = re.sub(r'!\[.*?\]\(.*?\)', '', full_normal).strip()
                        # Схлопываем избыточные переносы строк
                        full_normal = re.sub(r'\n{3,}', '\n\n', full_normal).strip()

                    # Очистка от любых фейковых картинок вида ![alt](url)
                    full_normal = re.sub(r'!\[.*?\]\(.*?\)', '', full_normal).strip()

                    yield {"event": "done", "data": full_normal}

                    if has_gallery:
                        yield {"event": "art_started", "data": json.dumps({"session_id": req.session_id, "trigger": "gallery"})}

                    # Save generated assistant response to session
                    def _save_assistant():
                        asst_msg = {
                            "id": str(uuid.uuid4()),
                            "role": "assistant",
                            "content": full_normal,
                            "timestamp": datetime.now().isoformat(),
                        }
                        if full_thought:
                            asst_msg["thought"] = full_thought

                        session.setdefault("messages", []).append(asst_msg)
                        session.setdefault("chat_history", []).append({"role": "assistant", "content": full_normal})
                        session["updated_at"] = datetime.now().isoformat()
                        atomic_write_json(chat_file, session)
                        SessionManager.set_active_session(session)

                    await asyncio.to_thread(_save_assistant)

                    # Trigger Gallery Art Pass if gallery was detected
                    if has_gallery:
                        def _gallery_art_worker():
                            try:
                                logger.info(f"[ChatStream] Starting Gallery Art Pass (Two-Pass) for query: '{req.text}'")

                                # 1. Вспомогательный скрытый арт-промпт
                                art_llm_prompt = AssistantPromptBuilder.build_gallery_art_prompt(
                                    user_query=req.text,
                                    target_model_id="realistic",
                                    target_model_desc="CyberRealistic"
                                )

                                # 2. Скрытый вызов к LM Studio для генерации Danbooru-тегов
                                raw_tags = lm_client.chat_completion(
                                    messages=[{"role": "user", "content": art_llm_prompt}],
                                    temperature=0.4,
                                    context_length=4096,
                                    max_tokens=512,
                                    stream=False,
                                    stop=["\n\n\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c:", "\n\nUser:"],
                                    is_roleplay=False,
                                    is_engineer=False,
                                )

                                # 3. Извлечение сгенерированных тегов через RE_IMAGE_TAG
                                clean_tags = re.sub(r'<think>[\s\S]*?</think>', '', raw_tags, flags=re.IGNORECASE).strip()
                                img_match = RE_IMAGE_TAG.search(clean_tags or raw_tags)
                                if img_match:
                                    extracted_tags = re.sub(r'^[\s\*_\-\#]+', '', img_match.group(1)).strip()
                                else:
                                    extracted_tags = (clean_tags or raw_tags).strip()

                                logger.info(f"[ChatStream] Gallery Art Pass generated Danbooru tags: {extracted_tags}")

                                # 4. Извлечение опциональных флагов стиля, негатива и соотношения сторон (как в десктопе)
                                model_match = re.search(r'(?:--model|--style)[:=\s]+["\']?([a-zA-Z0-9_\-]+)["\']?', extracted_tags, re.I)
                                llm_style = model_match.group(1).lower().strip() if model_match else "realistic"
                                clean_prompt = re.sub(r'(?:--model|--style)[:=\s]+["\']?[a-zA-Z0-9_\-]+["\']?', '', extracted_tags, flags=re.I).strip(' ,')

                                neg_match = re.search(r'(?:--neg|--negative)[:=\s]+(.*?)(?=(?:--model|--style|--ar|$))', clean_prompt, re.I)
                                custom_neg = neg_match.group(1).strip().strip(' ,') if neg_match else ""
                                clean_prompt = re.sub(r'(?:--neg|--negative)[:=\s]+.*?(?=(?:--model|--style|--ar|$))', '', clean_prompt, flags=re.I).strip(' ,')

                                ar_match = re.search(r'--ar[:=\s]+([a-zA-Z0-9:]+)', clean_prompt, re.I)
                                extracted_ar = ar_match.group(1).lower().strip() if ar_match else None
                                clean_prompt = re.sub(r'--ar[:=\s]+[a-zA-Z0-9:]+', '', clean_prompt, flags=re.I).strip(' ,')

                                ad = ArtDirector()
                                v_intent = VisualIntent(
                                    prompt=clean_prompt or extracted_tags,
                                    user_neg=custom_neg,
                                    suggested_model=llm_style,
                                    aspect_ratio=extracted_ar,
                                    raw_user_query=req.text,
                                    session_id=req.session_id,
                                    mode=mode,
                                )
                                plan = ad.plan_from_visual_intent(
                                    v_intent,
                                    preferred_style=llm_style,
                                    session_id=req.session_id,
                                    mode=mode
                                )
                                f_kwargs = plan.to_forge_kwargs()
                                s_out = OUTPUTS_DIR / "sessions" / req.session_id
                                s_out.mkdir(parents=True, exist_ok=True)
                                f_kwargs["output_dir"] = s_out
                                f_kwargs["session_id"] = req.session_id

                                tq = get_task_queue() or get_task_queue_worker()
                                if not tq:
                                    logger.warning("[ChatStream] TaskQueue not available, skipping gallery art pass.")
                                    return

                                def _run_forge():
                                    try:
                                        fc = ForgeClient()
                                        if not fc.is_server_ready(timeout=1.0):
                                            logger.warning("[ChatStream] Forge server is not ready when executing task.")
                                            return
                                        saved_img = fc.text_to_image(**f_kwargs)
                                        img_url = f"/media/image?path={Path(saved_img).as_posix()}"
                                        art_record = {
                                            "id": str(uuid.uuid4()),
                                            "role": "assistant",
                                            "content": f"[\u0413\u0430\u043b\u0435\u0440\u0435\u044f \u0410\u0440\u0442-\u0434\u0438\u0440\u0435\u043a\u0442\u043e\u0440\u0430]\n![\u0418\u043b\u043b\u044e\u0441\u0442\u0440\u0430\u0446\u0438\u044f]({img_url})",
                                            "image_path": str(saved_img),
                                            "image_url": img_url,
                                            "generation_params": {
                                                "model_name": plan.model_title or plan.target_checkpoint or "Auto",
                                                "seed": f_kwargs.get("seed", -1),
                                                "steps": f_kwargs.get("steps", 28),
                                                "sampler": f_kwargs.get("sampler_name", "Euler a"),
                                                "cfg_scale": f_kwargs.get("cfg_scale", 6.0),
                                                "prompt": f_kwargs.get("prompt", ""),
                                                "negative_prompt": f_kwargs.get("negative_prompt", ""),
                                            },
                                            "timestamp": datetime.now().isoformat(),
                                        }
                                        if chat_file.exists():
                                            try:
                                                with open(chat_file, "r", encoding="utf-8") as f_in:
                                                    curr_s = json.load(f_in)
                                                curr_s.setdefault("messages", []).append(art_record)
                                                atomic_write_json(chat_file, curr_s)
                                            except Exception:
                                                pass
                                    except Exception as e_run:
                                        logger.error(f"[ChatStream] Forge execution error: {e_run}", exc_info=True)

                                tq.enqueue(InferenceTask(name=f"GalleryArt_{req.session_id}", task_type="image", func=_run_forge, priority=PRIORITY_NORMAL))
                            except Exception as e_gal:
                                logger.error(f"[ChatStream] Gallery pass error: {e_gal}", exc_info=True)

                        threading.Thread(target=_gallery_art_worker, daemon=True).start()

                    # Trigger RPCastExtractor in roleplay mode
                    if is_roleplay:
                        def _rp_extractor_worker():
                            try:
                                RPCastExtractor.extract_cast_async(
                                    session_id=req.session_id,
                                    recent_turns=session.get("chat_history", [])[-4:],
                                    companion_name=(session.get("character_data") or {}).get("name", "\u042d\u043b\u0430\u0440\u0438\u044f"),
                                    character_card=card_obj,
                                )
                            except Exception as e_rpc:
                                logger.debug(f"[ChatStream] RPCastExtractor error: {e_rpc}")

                        threading.Thread(target=_rp_extractor_worker, daemon=True).start()

                    break

                elif kind == "error":
                    yield {"event": "error", "data": str(payload)}
                    break

        except asyncio.CancelledError:
            logger.info(f"[ChatStream] Stream cancelled for session {req.session_id}")
            raise

    return EventSourceResponse(event_generator())


class RegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: str
    mode: Optional[str] = None


@router.post("/regenerate")
async def chat_regenerate(
    req: RegenerateRequest,
    _manager: SessionManager = Depends(get_session_manager),
    lm_client: LMStudioClient = Depends(get_lm_studio_client),
):
    """
    Deletes the last assistant message from the session and re-runs generation via SSE.
    """
    chat_file = HISTORY_DIR / f"chat_{req.session_id}.json"

    def _prepare_session():
        if not chat_file.exists():
            return None
        with open(chat_file, "r", encoding="utf-8") as f:
            session = json.load(f)

        msgs = session.get("messages", [])
        hist = session.get("chat_history", [])

        # Pop last assistant message if present
        if msgs and msgs[-1].get("role") == "assistant":
            msgs.pop()
        if hist and hist[-1].get("role") == "assistant":
            hist.pop()

        session["messages"] = msgs
        session["chat_history"] = hist
        session["updated_at"] = datetime.now().isoformat()
        atomic_write_json(chat_file, session)
        SessionManager.set_active_session(session)
        return session

    session = await asyncio.to_thread(_prepare_session)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # If no messages left, error
    if not session.get("messages"):
        raise HTTPException(status_code=400, detail="Cannot regenerate an empty dialog")

    mode = (req.mode or session.get("mode") or "assistant").lower().strip()
    is_roleplay = (mode == "roleplay")
    is_engineer = (mode in ("engineer", "engineering") or session.get("type") == "engineering")

    profile_key = "engineer" if is_engineer else ("roleplay" if is_roleplay else "assistant")
    profile = copy.deepcopy(MODE_PROFILES.get(profile_key, MODE_PROFILES["assistant"]))

    card_obj = None
    if is_roleplay and session.get("character_data"):
        card_data = session["character_data"]
        card_obj = CharacterCard.from_dict(card_data) if isinstance(card_data, dict) else card_data

    last_user_text = ""
    for m in reversed(session.get("chat_history", [])):
        if m.get("role") == "user":
            last_user_text = m.get("content", "")
            break

    skill_router = SkillRouter.get_instance() if hasattr(SkillRouter, "get_instance") else SkillRouter()
    routed_skill, _, _ = skill_router.route(last_user_text)
    is_gallery_skill = (routed_skill == "gallery")

    def _build_context():
        msgs = ContextWindowBuilder.get_lm_studio_messages(
            history=session.get("chat_history", []),
            is_roleplay=is_roleplay,
            is_engineer=is_engineer,
            character_card=card_obj,
            session_id=req.session_id,
            include_memory=(not is_roleplay and not is_engineer and not is_gallery_skill),
            user_query=last_user_text,
            task_text=last_user_text,
            active_assistant_skill="gallery" if is_gallery_skill else None,
        )
        if is_gallery_skill and msgs and msgs[0].get("role") == "system":
            sys_content = msgs[0]["content"]
            if "\u0412\u0418\u0417\u0423\u0410\u041b\u0418\u0417\u0410\u0426\u0418\u042f" not in sys_content and "[GALERY]" not in sys_content:
                gallery_directive = AssistantPromptBuilder.SKILL_PROMPTS.get("gallery", "").format(
                    user_name="\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c"
                )
                msgs[0]["content"] = f"{sys_content}\n\n{gallery_directive}".strip()
        return msgs

    context_msgs = await asyncio.to_thread(_build_context)

    sampling_params = dict(profile.get("sampling", {}))
    if isinstance(session.get("sampling_params"), dict):
        sampling_params.update(session["sampling_params"])
    sampling_params["context_length"] = profile.get("context_length", 14336)

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def run_inference():
        try:
            for token in lm_client.stream_chat_completion(context_msgs, **sampling_params):
                loop.call_soon_threadsafe(queue.put_nowait, ("token", token))
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
        except Exception as exc:
            logger.error(f"[ChatRegenerate] Inference error: {exc}", exc_info=True)
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))

    threading.Thread(target=run_inference, daemon=True).start()

    async def event_generator():
        parser = ThoughtStreamParser()
        gallery_checked = False
        normal_lead_buf = ""

        def _clean_token_lead(buf: str) -> str:
            cleaned = RE_GALLERY_TRIGGER.sub("", buf)
            cleaned = re.sub(r"!\[.*?\]\(.*?\)", "", cleaned)
            return cleaned

        try:
            while True:
                kind, payload = await queue.get()

                if kind == "token":
                    events = parser.feed(payload)
                    for ev, txt in events:
                        if ev == "thought_chunk":
                            yield {"event": "thought", "data": txt}
                        elif ev == "end_think":
                            yield {"event": "thought_end", "data": ""}
                        elif ev == "normal_chunk":
                            if not gallery_checked:
                                normal_lead_buf += txt
                                if "]" in normal_lead_buf or len(normal_lead_buf) > 60:
                                    gallery_checked = True
                                    to_yield = _clean_token_lead(normal_lead_buf)
                                    normal_lead_buf = ""
                                    if to_yield:
                                        yield {"event": "token", "data": to_yield}
                            else:
                                yield {"event": "token", "data": txt}

                elif kind == "done":
                    rem = parser.finalize()
                    for ev, txt in rem:
                        if ev == "thought_chunk":
                            yield {"event": "thought", "data": txt}
                        elif ev == "end_think":
                            yield {"event": "thought_end", "data": ""}
                        elif ev == "normal_chunk":
                            if not gallery_checked:
                                normal_lead_buf += txt
                            else:
                                yield {"event": "token", "data": txt}

                    if not gallery_checked and normal_lead_buf:
                        gallery_checked = True
                        to_yield = _clean_token_lead(normal_lead_buf)
                        normal_lead_buf = ""
                        if to_yield:
                            yield {"event": "token", "data": to_yield}

                    full_normal = parser.normal_text
                    full_thought = parser.thought_text.strip() if parser.thought_text else None

                    has_gallery = bool(RE_GALLERY_TRIGGER.search(full_normal))
                    if has_gallery:
                        full_normal = RE_GALLERY_TRIGGER.sub('', full_normal).strip()
                        full_normal = re.sub(r'!\[.*?\]\(.*?\)', '', full_normal).strip()
                        full_normal = re.sub(r'\n{3,}', '\n\n', full_normal).strip()

                    full_normal = re.sub(r'!\[.*?\]\(.*?\)', '', full_normal).strip()

                    yield {"event": "done", "data": full_normal}

                    def _save_assistant():
                        asst_msg = {
                            "id": str(uuid.uuid4()),
                            "role": "assistant",
                            "content": full_normal,
                            "timestamp": datetime.now().isoformat(),
                        }
                        if full_thought:
                            asst_msg["thought"] = full_thought

                        session.setdefault("messages", []).append(asst_msg)
                        session.setdefault("chat_history", []).append({"role": "assistant", "content": full_normal})
                        session["updated_at"] = datetime.now().isoformat()
                        atomic_write_json(chat_file, session)
                        SessionManager.set_active_session(session)

                    await asyncio.to_thread(_save_assistant)
                    break

                elif kind == "error":
                    yield {"event": "error", "data": str(payload)}
                    break

        except asyncio.CancelledError:
            logger.info(f"[ChatRegenerate] Stream cancelled for session {req.session_id}")
            raise

    return EventSourceResponse(event_generator())
