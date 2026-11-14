"""
Chat inference routes: streaming LLM generation via Server-Sent Events (SSE).
"""

import uuid
import asyncio
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from services.art_director.director import ArtDirector
from services.forge_client import ForgeClient
from services.mode_coordinator import AppMode
from services.session_manager import SessionManager
from services.lm_studio import LMStudioClient
from services.task_queue import TaskQueueWorker

from mobile_server.schemas import SendMessageRequest
from mobile_server.dependencies import (
    get_art_director,
    get_forge_client,
    get_session_manager,
    get_lm_studio_client,
    get_task_queue_worker,
)
from mobile_server.services.chat_service import stream_chat_sse

router = APIRouter()


class RegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: str
    mode: Optional[str] = None


@router.post("/stream")
async def chat_stream(
    req: SendMessageRequest,
    _manager: SessionManager = Depends(get_session_manager),
    lm_client: LMStudioClient = Depends(get_lm_studio_client),
    task_queue: TaskQueueWorker = Depends(get_task_queue_worker),
    forge_client: ForgeClient = Depends(get_forge_client),
    art_director: ArtDirector = Depends(get_art_director),
):
    """
    Streams LLM completion via SSE for mobile clients.
    Executes inside TaskQueueWorker under VRAMArbiter 8 GB VRAM mutual exclusion.
    """
    session = await asyncio.to_thread(SessionManager.load_session, req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    user_msg = {
        "id": str(uuid.uuid4()),
        "role": "user",
        "content": req.text,
        "timestamp": datetime.now().isoformat(),
    }
    session.setdefault("messages", []).append(user_msg)
    session.setdefault("chat_history", []).append({"role": "user", "content": req.text})

    if not session.get("is_custom_titled") and session.get("title") in ("Новый диалог", "Диалог", "", None):
        char_name = (session.get("character_data") or {}).get("name", "")
        target_mode = AppMode.from_session(session, req.mode)
        session["title"] = SessionManager.generate_smart_title(
            req.text,
            mode=target_mode.value,
            character_name=char_name,
        )

    await asyncio.to_thread(SessionManager.save_session, session)

    return EventSourceResponse(
        stream_chat_sse(
            session_id=req.session_id,
            session=session,
            user_text=req.text,
            lm_client=lm_client,
            task_queue=task_queue,
            mode_override=req.mode,
            sampling_overrides=req.sampling_overrides,
            log_prefix="ChatStream",
            forge_client=forge_client,
            art_director=art_director,
        )
    )


@router.post("/regenerate")
async def chat_regenerate(
    req: RegenerateRequest,
    _manager: SessionManager = Depends(get_session_manager),
    lm_client: LMStudioClient = Depends(get_lm_studio_client),
    task_queue: TaskQueueWorker = Depends(get_task_queue_worker),
    forge_client: ForgeClient = Depends(get_forge_client),
    art_director: ArtDirector = Depends(get_art_director),
):
    """
    Deletes the last assistant message from the session and re-runs generation via SSE.
    """
    def _prepare_session():
        session = SessionManager.load_session(req.session_id)
        if not session:
            return None

        msgs = session.get("messages", [])
        hist = session.get("chat_history", [])
        if msgs and msgs[-1].get("role") == "assistant":
            msgs.pop()
        if hist and hist[-1].get("role") == "assistant":
            hist.pop()

        session["messages"] = msgs
        session["chat_history"] = hist
        SessionManager.save_session(session)
        return session

    session = await asyncio.to_thread(_prepare_session)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.get("messages"):
        raise HTTPException(status_code=400, detail="Cannot regenerate an empty dialog")

    last_user_text = ""
    for m in reversed(session.get("chat_history", [])):
        if m.get("role") == "user":
            last_user_text = m.get("content", "")
            break

    return EventSourceResponse(
        stream_chat_sse(
            session_id=req.session_id,
            session=session,
            user_text=last_user_text,
            lm_client=lm_client,
            task_queue=task_queue,
            mode_override=req.mode,
            log_prefix="ChatRegenerate",
            forge_client=forge_client,
            art_director=art_director,
        )
    )
