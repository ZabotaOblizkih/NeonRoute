"""
Session management routes: CRUD operations over chat sessions.
"""

import json
import uuid
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException

from config import HISTORY_DIR, CHARACTERS_DIR
from utils.file_utils import atomic_write_json
from services.session_manager import SessionManager, get_default_sampling_params
from services.tavern_parser import CharacterCard, TavernParser
from mobile_server.schemas import (
    SessionSummary,
    SessionDetailResponse,
    CreateSessionRequest,
    SessionMutationResponse,
)
from mobile_server.dependencies import get_session_manager

router = APIRouter()


def _get_sessions_sync() -> List[SessionSummary]:
    """Reads session metadata synchronously from HISTORY_DIR."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    raw_sessions = SessionManager.list_sessions()
    summaries: List[SessionSummary] = []

    for s in raw_sessions:
        sid = s.get("id")
        if not sid:
            continue

        # Count messages in session file
        msg_count = 0
        file_path = HISTORY_DIR / f"chat_{sid}.json"
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                msgs = data.get("messages") or data.get("chat_history") or []
                msg_count = len(msgs)
            except Exception:
                msg_count = 0

        raw_mode = str(s.get("mode") or "assistant").lower()
        if s.get("type") == "engineering" or raw_mode in ("engineer", "engineering"):
            effective_mode = "engineer"
        elif raw_mode == "roleplay":
            effective_mode = "roleplay"
        else:
            effective_mode = "assistant"

        summaries.append(
            SessionSummary(
                id=str(sid),
                title=s.get("title") or "Диалог",
                mode=effective_mode,
                updated_at=s.get("updated_at") or s.get("created_at"),
                message_count=msg_count,
            )
        )

    # Sort descending by updated_at
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
            # Vision or multipart content
            text_parts = [
                str(p.get("text", ""))
                for p in raw_content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            content_str = " ".join(t.strip() for t in text_parts if t.strip())
        else:
            content_str = str(raw_content or "")

        # Skip purely empty assistant messages
        if role == "assistant" and not content_str.strip() and not m.get("thought"):
            continue

        normalized_msg = {
            "id": m.get("id") or str(uuid.uuid4()),
            "role": role,
            "content": content_str,
        }
        if m.get("thought"):
            normalized_msg["thought"] = m["thought"]
        if m.get("timestamp"):
            normalized_msg["timestamp"] = m["timestamp"]

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
    file_path = HISTORY_DIR / f"chat_{session_id}.json"

    def _load():
        if not file_path.exists():
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    data = await asyncio.to_thread(_load)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")

    raw_mode = str(data.get("mode") or "assistant").lower()
    if data.get("type") == "engineering" or raw_mode in ("engineer", "engineering"):
        effective_mode = "engineer"
    elif raw_mode == "roleplay":
        effective_mode = "roleplay"
    else:
        effective_mode = "assistant"

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
    Creates a new session and persists it atomically.
    """
    def _create_and_save():
        card_obj: Optional[CharacterCard] = None

        if req.character_id and CHARACTERS_DIR.exists():
            cid = req.character_id.strip()
            # Try finding the character file
            for p in CHARACTERS_DIR.glob("*.json"):
                if p.stem.lower() == cid.lower() or cid.lower() in p.name.lower():
                    try:
                        card_obj = TavernParser.parse_file(p)
                        break
                    except Exception:
                        pass

        mode_val = "assistant" if req.mode == "engineer" else req.mode
        chosen_title = req.title or (card_obj.name if card_obj else None)
        new_sess = SessionManager.create_session(
            mode=mode_val,
            character_card=card_obj,
            title=chosen_title,
        )

        if card_obj:
            greet = card_obj.first_mes_ru or card_obj.first_mes
            if greet and greet.strip():
                from services.macro_engine import MacroEngine
                from datetime import datetime
                greet_clean = MacroEngine.process(greet.strip(), user_name="Игрок", char_name=card_obj.name)
                greet_msg = {
                    "id": str(uuid.uuid4()),
                    "role": "assistant",
                    "content": greet_clean,
                    "timestamp": datetime.now().isoformat()
                }
                new_sess["messages"].append(greet_msg)
                new_sess["chat_history"].append({"role": "assistant", "content": greet_clean})

        if req.mode == "engineer":
            new_sess["type"] = "engineering"
            new_sess["mode"] = "assistant"

        target_path = HISTORY_DIR / f"chat_{new_sess['id']}.json"
        saved = atomic_write_json(target_path, new_sess)
        if not saved:
            raise IOError(f"Failed to write session file atomically to {target_path}")

        # Update cache
        SessionManager.set_active_session(new_sess)
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
    Safely deletes a session file and associated caches.
    """
    file_path = HISTORY_DIR / f"chat_{session_id}.json"

    def _check_exists():
        return file_path.exists() or session_id in SessionManager._sessions_cache

    exists = await asyncio.to_thread(_check_exists)
    if not exists:
        raise HTTPException(status_code=404, detail="Session not found")

    def _delete_sync():
        SessionManager.delete_session(session_id)
        SessionManager._sessions_cache.pop(session_id, None)
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
    chat_file = HISTORY_DIR / f"chat_{session_id}.json"

    def _remove():
        if not chat_file.exists():
            raise HTTPException(status_code=404, detail="Session not found")
        with open(chat_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        msgs = data.get("messages", [])
        hist = data.get("chat_history", [])

        if msgs:
            removed = msgs.pop()
        else:
            removed = None

        if hist:
            hist.pop()

        data["messages"] = msgs
        data["chat_history"] = hist
        atomic_write_json(chat_file, data)
        SessionManager.set_active_session(data)
        return removed

    removed = await asyncio.to_thread(_remove)
    return {"success": True, "removed": removed}

