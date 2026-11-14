"""
Session management routes: CRUD operations over chat sessions via SessionManager.
"""

import json
import uuid
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from config import HISTORY_DIR
from services.mode_coordinator import AppMode
from services.session_manager import SessionManager, get_default_sampling_params
from services.tavern_parser import CharacterCard, TavernParser
from mobile_server.schemas import (
    SessionSummary,
    SessionDetailResponse,
    CreateSessionRequest,
    SessionMutationResponse,
)
from mobile_server.dependencies import get_session_manager
from mobile_server.services.art_service import build_media_image_url
from mobile_server.services.chat_service import subscribe_session_events

router = APIRouter()


def _get_sessions_sync() -> List[SessionSummary]:
    """Reads session metadata via SessionManager (SQLite WAL + mtime cache)."""
    raw_sessions = SessionManager.list_sessions(include_message_count=True)
    summaries: List[SessionSummary] = []

    for s in raw_sessions:
        sid = s.get("id")
        if not sid:
            continue

        summaries.append(
            SessionSummary(
                id=str(sid),
                title=s.get("title") or "Диалог",
                mode=AppMode.from_session(s).value,
                updated_at=s.get("updated_at") or s.get("created_at"),
                message_count=int(s.get("message_count") or 0),
            )
        )

    summaries.sort(key=lambda item: item.updated_at or "", reverse=True)
    return summaries


def _normalize_messages(raw_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalizes message objects and cleans system artifacts."""
    normalized: List[Dict[str, Any]] = []

    for m in raw_messages:
        if not isinstance(m, dict):
            continue

        role = m.get("role", "user")
        raw_content = m.get("content") if m.get("content") is not None else m.get("text", "")

        if isinstance(raw_content, list):
            text_parts = [
                str(p.get("text", ""))
                for p in raw_content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            content_str = " ".join(t.strip() for t in text_parts if t.strip())
        else:
            content_str = str(raw_content or "")

        image_path = m.get("image_path")
        image_url = m.get("image_url")
        if not image_url and image_path:
            image_url = build_media_image_url(Path(str(image_path)))
        gen_params = m.get("generation_params")

        raw_thought = m.get("thought")
        thought_text = (
            raw_thought.get("content", "")
            if isinstance(raw_thought, dict)
            else (raw_thought if isinstance(raw_thought, str) else "")
        )

        if (
            role == "assistant"
            and not content_str.strip()
            and not str(thought_text or "").strip()
            and not image_url
            and not gen_params
        ):
            continue

        normalized_msg: Dict[str, Any] = {
            "id": m.get("id") or str(uuid.uuid4()),
            "role": role,
            "content": content_str,
        }
        if thought_text and str(thought_text).strip():
            normalized_msg["thought"] = str(thought_text)
        if m.get("timestamp"):
            normalized_msg["timestamp"] = m["timestamp"]
        if image_url:
            normalized_msg["image_url"] = image_url
        if image_path:
            normalized_msg["image_path"] = str(image_path)
        if gen_params:
            normalized_msg["generation_params"] = gen_params

        normalized.append(normalized_msg)

    return normalized


@router.get("", response_model=List[SessionSummary])
async def list_sessions(
    _manager: SessionManager = Depends(get_session_manager),
) -> List[SessionSummary]:
    """
    Returns list of all chat sessions sorted by last modified timestamp.
    """
    return await asyncio.to_thread(_get_sessions_sync)


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session_detail(
    session_id: str,
    _manager: SessionManager = Depends(get_session_manager),
) -> SessionDetailResponse:
    """
    Returns details of a specific session including normalized message history.
    """
    data = await asyncio.to_thread(SessionManager.load_session, session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")

    effective_mode = AppMode.from_session(data).value
    raw_msgs = data.get("messages") or data.get("chat_history") or []
    normalized_msgs = _normalize_messages(raw_msgs)
    sampling = data.get("sampling_params") or get_default_sampling_params(effective_mode)

    return SessionDetailResponse(
        id=str(data.get("id", session_id)),
        title=str(data.get("title", "Диалог")),
        mode=effective_mode,
        messages=normalized_msgs,
        sampling_params=sampling,
        character_data=data.get("character_data"),
    )


@router.post("", response_model=SessionMutationResponse)
async def create_session(
    req: CreateSessionRequest,
    _manager: SessionManager = Depends(get_session_manager),
) -> SessionMutationResponse:
    """
    Creates a new session and persists it atomically via SessionManager.
    """
    def _create_and_save():
        card_obj: Optional[CharacterCard] = None
        if req.character_id:
            card_path = SessionManager.resolve_character_card_path(identifier=req.character_id)
            if card_path:
                try:
                    card_obj = TavernParser.parse_file(card_path)
                except Exception:
                    card_obj = None

        is_engineer = req.mode.strip().lower() in ("engineer", "engineering")
        mode_val = "assistant" if is_engineer else req.mode
        chosen_title = req.title or (card_obj.name if card_obj else None)

        new_sess = SessionManager.create_session(
            mode=mode_val,
            character_card=card_obj,
            title=chosen_title,
            append_greeting=True,
        )
        if is_engineer:
            new_sess["type"] = "engineering"
            new_sess["mode"] = "assistant"

        saved_path = SessionManager.save_session(new_sess)
        if not saved_path:
            raise IOError("Failed to write session via SessionManager")
        return new_sess["id"]

    try:
        new_id = await asyncio.to_thread(_create_and_save)
        return SessionMutationResponse(
            success=True,
            session_id=new_id,
            message="Session created successfully",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create session: {e}")


@router.delete("/{session_id}", response_model=SessionMutationResponse)
async def delete_session(
    session_id: str,
    _manager: SessionManager = Depends(get_session_manager),
) -> SessionMutationResponse:
    """
    Safely deletes a session file, SQLite records, and associated caches.
    """
    file_path = HISTORY_DIR / f"chat_{session_id}.json"

    def _check_exists():
        return file_path.exists() or session_id in SessionManager._sessions_cache

    exists = await asyncio.to_thread(_check_exists)
    if not exists:
        raise HTTPException(status_code=404, detail="Session not found")

    def _delete_sync():
        SessionManager.delete_session(session_id)
        if (
            SessionManager._current_session
            and str(SessionManager._current_session.get("id")) == session_id
        ):
            SessionManager._current_session = None

    await asyncio.to_thread(_delete_sync)

    return SessionMutationResponse(
        success=True,
        session_id=session_id,
        message="Session deleted successfully",
    )


@router.get("/{session_id}/cast")
async def get_session_cast(session_id: str):
    """
    Returns campaign status (location, quest, inventory, stats, companion, world_state)
    from history/cast_{session_id}.json.
    """
    cast_file = HISTORY_DIR / f"cast_{session_id}.json"

    def _read_cast():
        if not cast_file.exists():
            return {
                "session_id": session_id,
                "location": "Неизвестная локация",
                "quest": "Исследование мира",
                "inventory": [],
                "stats": {},
                "companion": "",
                "relationship_state": "Нейтральное",
                "world_state": [],
            }
        try:
            with open(cast_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "session_id": session_id,
                "location": data.get("current_location") or "Локация не указана",
                "quest": data.get("active_quest") or "Свободное действие",
                "inventory": data.get("inventory") or [],
                "stats": data.get("stats") or {},
                "companion": data.get("focus_companion") or "",
                "relationship_state": data.get("relationship_state") or "Знакомство",
                "world_state": data.get("world_state") or [],
            }
        except Exception:
            return {
                "session_id": session_id,
                "location": "Ошибка чтения файла каста",
                "quest": "",
                "inventory": [],
                "stats": {},
                "companion": "",
                "relationship_state": "",
                "world_state": [],
            }

    return await asyncio.to_thread(_read_cast)


@router.delete("/{session_id}/messages/last")
async def delete_last_message(session_id: str):
    """
    Removes the last message from the session (useful for retries/undo).
    """
    def _remove():
        data = SessionManager.load_session(session_id)
        if not data:
            raise HTTPException(status_code=404, detail="Session not found")

        msgs = data.get("messages", [])
        hist = data.get("chat_history", [])
        removed = msgs.pop() if msgs else None
        if hist:
            hist.pop()

        data["messages"] = msgs
        data["chat_history"] = hist
        SessionManager.save_session(data)
        return removed

    removed = await asyncio.to_thread(_remove)
    return {"success": True, "removed": removed}


@router.get("/{session_id}/events")
async def session_events_stream(session_id: str):
    """Real-time SSE push stream for background task notifications (art_completed, art_failed, art_interrupted)."""
    return EventSourceResponse(
        subscribe_session_events(session_id),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
