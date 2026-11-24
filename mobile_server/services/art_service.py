"""
Shared Art & Forge orchestration helpers for NeonRoute Mobile Server.
Eliminates duplication across art routes and chat gallery passes.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote
import uuid

from config import OUTPUTS_DIR
from services.forge_client import ForgeClient
from services.model_router import normalize_model_choice
from services.session_manager import SessionManager
from services.task_queue import InferenceTask, PRIORITY_NORMAL, TaskQueueWorker

logger = logging.getLogger("NeonRoute.MobileServer.ArtService")

# Server-level active model fallback cache
_active_art_models: Dict[str, str] = {"default": "auto"}


def get_session_forge_model(
    session_id: Optional[str] = None,
    session: Optional[Dict[str, Any]] = None,
) -> str:
    """Resolves active Forge model id for a session with global fallback."""
    active_id = "auto"
    sess_data = session
    if sess_data is None and session_id:
        sess_data = SessionManager.load_session(session_id)
    if isinstance(sess_data, dict):
        active_id = sess_data.get("forge_model_id") or _active_art_models.get(session_id or "", "auto")
    elif session_id:
        active_id = _active_art_models.get(session_id, "auto")

    if normalize_model_choice(active_id) is None:
        return _active_art_models.get("default", "auto")
    return str(active_id)


def set_session_forge_model(model_id: str, session_id: Optional[str] = None) -> str:
    """Persists active Forge model globally and optionally in session."""
    clean_id = model_id.strip()
    _active_art_models["default"] = clean_id

    if session_id:
        _active_art_models[session_id] = clean_id
        sess_data = SessionManager.load_session(session_id)
        if isinstance(sess_data, dict):
            sess_data["forge_model_id"] = clean_id
            SessionManager.save_session(sess_data)
    return clean_id


def build_media_image_url(image_path: Path) -> str:
    """Builds safely URL-encoded /media/image link for a generated image."""
    encoded = quote(Path(image_path).as_posix(), safe="/:")
    return f"/media/image?path={encoded}"


async def enqueue_forge_generation_async(
    task_queue: TaskQueueWorker,
    forge: ForgeClient,
    art_director: Any,
    task_name: str,
    task_type: str,
    session_id: str,
    mode: str = "assistant",
    plan: Optional[Any] = None,
    override_params: Optional[Dict[str, Any]] = None,
    preferred_style: Optional[str] = None,
    prompt_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Submits unified run_forge_art_generation() to TaskQueueWorker and awaits its result."""
    from services.assistant_flow import run_forge_art_generation

    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    def _run():
        try:
            gen_res = run_forge_art_generation(
                forge_client=forge,
                art_director=art_director,
                session_id=session_id,
                mode=mode,
                plan=plan,
                preferred_style=preferred_style,
                override_params=override_params,
                output_dir=SessionManager.get_session_images_dir(session_id),
                unload_after=True,
                prompt_label=prompt_label,
            )
            loop.call_soon_threadsafe(fut.set_result, gen_res)
        except Exception as exc:
            loop.call_soon_threadsafe(fut.set_exception, exc)

    inference_task = InferenceTask(
        name=task_name,
        task_type=task_type,
        func=_run,
        abort_fn=getattr(forge, "interrupt", None),
        priority=PRIORITY_NORMAL,
        metadata={"session_id": session_id},
    )
    task_queue.enqueue(inference_task)
    return await fut


def attach_art_to_session_message(
    session_id: str,
    image_path: Path,
    target_msg_id: Optional[str] = None,
    gen_params_dict: Optional[Dict[str, Any]] = None,
    last_meta: Optional[Dict[str, Any]] = None,
    fallback_label: str = "Иллюстрация сцены",
) -> Optional[str]:
    """
    Attaches a generated artwork inline to the target assistant message in session
    (matching desktop _handle_assistant_art_completed), persists last_generation for Seed Lock,
    or falls back to appending a new art message if target_msg_id is not found.
    """
    image_url = build_media_image_url(image_path)
    sess = SessionManager.load_session(session_id)
    if not isinstance(sess, dict):
        return image_url

    if isinstance(last_meta, dict):
        sess["last_generation"] = last_meta

    messages = sess.get("messages", [])
    target_msg = None
    if isinstance(messages, list) and messages:
        if target_msg_id:
            for m in reversed(messages):
                if m.get("id") == target_msg_id:
                    target_msg = m
                    break
        if target_msg is None:
            for m in reversed(messages):
                if m.get("role") == "assistant" and not m.get("image_path"):
                    target_msg = m
                    break

    if target_msg is not None:
        target_msg["image_path"] = str(image_path)
        target_msg["image_url"] = image_url
        if gen_params_dict is not None:
            target_msg["generation_params"] = gen_params_dict
            pos_prompt = gen_params_dict.get("positive_prompt") or gen_params_dict.get("prompt")
            if pos_prompt:
                target_msg["image_prompt"] = pos_prompt
                c_hist = sess.get("chat_history", [])
                if isinstance(c_hist, list):
                    for h_msg in reversed(c_hist):
                        if h_msg.get("role") == "assistant":
                            cur_c = str(h_msg.get("content") or "").strip()
                            art_prompt_tag = f"[IMAGE_PROMPT: {pos_prompt}]"
                            if cur_c.startswith("Секунду, перевожу образ в кадр"):
                                h_msg["content"] = art_prompt_tag
                            elif "[IMAGE_PROMPT:" not in cur_c:
                                h_msg["content"] = f"{cur_c}\n\n{art_prompt_tag}".strip()
                            break
        SessionManager.save_session(sess)
        return image_url

    return save_art_message_to_session(
        session_id=session_id,
        image_path=image_path,
        content_label=fallback_label,
        gen_params_dict=gen_params_dict,
    )


def save_art_message_to_session(
    session_id: str,
    image_path: Path,
    content_label: str,
    gen_params_dict: Optional[Dict[str, Any]] = None,
    chat_history_summary: Optional[str] = None,
) -> Optional[str]:
    """Appends generated artwork message to session via SessionManager (SQLite WAL + JSON mirror)."""
    image_url = build_media_image_url(image_path)
    sess = SessionManager.load_session(session_id)
    if not isinstance(sess, dict):
        return image_url

    art_msg: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "content": f"[{content_label}]\n![Иллюстрация]({image_url})",
        "image_path": str(image_path),
        "image_url": image_url,
        "timestamp": datetime.now().isoformat(),
    }
    if gen_params_dict is not None:
        art_msg["generation_params"] = gen_params_dict
        pos_prompt = gen_params_dict.get("positive_prompt") or gen_params_dict.get("prompt")
        if pos_prompt:
            art_msg["image_prompt"] = pos_prompt
            if not chat_history_summary:
                chat_history_summary = f"[IMAGE_PROMPT: {pos_prompt}]"

    sess.setdefault("messages", []).append(art_msg)
    if chat_history_summary:
        sess.setdefault("chat_history", []).append({
            "role": "assistant",
            "content": chat_history_summary,
        })
    SessionManager.save_session(sess)
    return image_url
