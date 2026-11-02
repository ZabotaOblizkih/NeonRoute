"""
Full-featured Forge Stable Diffusion and Art Director routes for NeonRoute Mobile.
Enforces 8 GB VRAM mutual exclusion via TaskQueueWorker and VRAMArbiter.
"""

import re
import uuid
import json
import time
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException

from config import HISTORY_DIR, OUTPUTS_DIR
from utils.file_utils import atomic_write_json
from services.art_director_config import load_art_director_models
from services.art_director.director import ArtDirector
from services.art_director.contracts import TargetSubject, VisualIntent
from services.art_director.prompting import extract_negations_from_prompt, ELARIA_VISUAL_DNA
from services.art_director.geometry import resolve_dimensions
from services.direct_command_parser import parse_direct_command_params
from services.forge_client import ForgeClient
from services.task_queue import TaskQueueWorker, InferenceTask, PRIORITY_NORMAL
from services.vram_arbiter import VRAMArbiter
from services.tavern_parser import CharacterCard
from services.session_manager import SessionManager
from services.rp_cast_manager import RPCastManager

from mobile_server.schemas import (
    ArtModelItem,
    ArtModelsResponse,
    SelectArtModelRequest,
    GenerateArtRequest,
    GenerateArtResponse,
    ArtProgressResponse,
    GenerationParamsPayload,
    DirectArtCommandRequest,
    ArtGalleryItem,
    ArtGalleryResponse,
    SceneBackgroundResponse,
)
from mobile_server.dependencies import (
    get_forge_client,
    get_art_director,
    get_task_queue_worker,
    get_vram_arbiter,
)

logger = logging.getLogger("NeonRoute.MobileServer.Art")
router = APIRouter()

# Server-level active model fallback cache
_active_art_models: Dict[str, str] = {"default": "auto"}


@router.get("/models", response_model=ArtModelsResponse)
async def get_forge_models(
    session_id: Optional[str] = None,
) -> ArtModelsResponse:
    """
    Returns list of registered Forge models including 'auto' Art Director option
    and style triggers from art_director_models.json.
    """
    raw_models = await asyncio.to_thread(load_art_director_models)

    # Determine active model
    active_id = "auto"
    if session_id:
        chat_file = HISTORY_DIR / f"chat_{session_id}.json"
        if chat_file.exists():
            try:
                with open(chat_file, "r", encoding="utf-8") as f:
                    sess_data = json.load(f)
                active_id = sess_data.get("forge_model_id") or _active_art_models.get(session_id) or "auto"
            except Exception:
                pass
    if active_id == "auto":
        active_id = _active_art_models.get("default", "auto")

    items: List[ArtModelItem] = [
        ArtModelItem(
            id="auto",
            name="✨ Арт-директор (Авто)",
            is_default=True,
            triggers=["авто", "арт-директор", "нейро-стиль"],
            description="Нейросеть сама подбирает чекпоинт, стиль и геометрию под сцену",
            checkpoint="auto",
            is_active=(active_id == "auto"),
        )
    ]

    for m in raw_models:
        mid = str(m.get("id") or m.get("name") or "")
        if not mid or mid == "auto":
            continue

        triggers_list = m.get("triggers") or m.get("styles") or []
        if isinstance(triggers_list, str):
            triggers_list = [t.strip() for t in triggers_list.split(",") if t.strip()]

        items.append(
            ArtModelItem(
                id=mid,
                name=m.get("name") or mid,
                is_default=bool(m.get("is_default", False)),
                triggers=triggers_list,
                description=m.get("description") or m.get("routing_description") or "",
                checkpoint=m.get("checkpoint"),
                is_active=(mid == active_id),
            )
        )

    return ArtModelsResponse(
        models=items,
        active_model_id=active_id,
        active_model=active_id,
    )


@router.post("/active-model")
async def set_active_forge_model(req: SelectArtModelRequest):
    """
    Persists the selected Forge model for a session or globally.
    """
    model_id = req.model_id.strip()
    _active_art_models["default"] = model_id

    if req.session_id:
        _active_art_models[req.session_id] = model_id
        chat_file = HISTORY_DIR / f"chat_{req.session_id}.json"
        if chat_file.exists():
            def _update_session():
                try:
                    with open(chat_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    data["forge_model_id"] = model_id
                    atomic_write_json(chat_file, data)
                except Exception:
                    pass
            await asyncio.to_thread(_update_session)

    return {
        "success": True,
        "active_model_id": model_id,
        "active_model": model_id,
    }


@router.post("/generate", response_model=GenerateArtResponse)
async def generate_scene_art(
    req: GenerateArtRequest,
    forge: ForgeClient = Depends(get_forge_client),
    art_director: ArtDirector = Depends(get_art_director),
    task_queue: TaskQueueWorker = Depends(get_task_queue_worker),
    _arbiter: VRAMArbiter = Depends(get_vram_arbiter),
) -> GenerateArtResponse:
    """
    Main ArtDirector generation pipeline:
    - Extracts negations and cleans prompt
    - Resolves model passport and visual intent (Elaria visual DNA / Character DNA)
    - Applies geometry (SD 1.5 vs SDXL), lighting, and BREAK
    - Converts plan to Forge kwargs with ADetailer and Hires.fix
    - Runs sequentially via TaskQueueWorker under VRAMArbiter coordination (8GB VRAM safe)
    - Saves result to outputs/sessions/{session_id}/ and updates session history
    """
    is_ready = await asyncio.to_thread(forge.is_server_ready, timeout=0.8)
    if not is_ready:
        return GenerateArtResponse(
            success=False,
            error="SD Forge offline (порт 7860 недоступен). Убедитесь, что Forge запущен.",
        )

    chat_file = HISTORY_DIR / f"chat_{req.session_id}.json"
    session_data: Dict[str, Any] = {}
    if chat_file.exists():
        try:
            with open(chat_file, "r", encoding="utf-8") as f:
                session_data = json.load(f)
        except Exception:
            session_data = {}

    mode = session_data.get("mode", "assistant")
    char_data = session_data.get("character_data")
    character_card = CharacterCard.from_dict(char_data) if char_data else None

    # 1. Determine raw text
    raw_text = req.prompt or req.prompt_override or req.text or ""
    # Extract tags from [IMAGE_PROMPT: ...] if present
    match = re.search(r"\[IMAGE[\s_-]PROMPT:\s*([^\]]+)\]", raw_text, re.IGNORECASE)
    if match:
        raw_text = match.group(1).strip()
    elif not raw_text.strip():
        # Fallback to last assistant message in session
        msgs = session_data.get("messages", [])
        for m in reversed(msgs):
            if m.get("role") == "assistant" and m.get("content"):
                c = re.sub(r"<[^>]+>", "", m["content"]).strip()
                raw_text = " ".join(c.split()[:45])
                break
        if not raw_text.strip():
            raw_text = "beautiful cyberpunk portrait, highly detailed, dramatic lighting"

    # 2. Extract negations
    pos_clean, neg_extracted = extract_negations_from_prompt(raw_text)
    user_neg_parts = []
    if req.negative_prompt and req.negative_prompt.strip():
        user_neg_parts.append(req.negative_prompt.strip())
    if neg_extracted and neg_extracted.strip():
        user_neg_parts.append(neg_extracted.strip())
    combined_neg = ", ".join(user_neg_parts)

    # 3. Model selection
    chosen_model = req.model_id or session_data.get("forge_model_id") or _active_art_models.get(req.session_id) or "auto"
    pref_style = None if chosen_model == "auto" else chosen_model

    # 4. Build VisualIntent & ArtDirector Plan
    intent = VisualIntent(
        prompt=pos_clean,
        raw_user_query=raw_text,
        user_neg=combined_neg,
        aspect_ratio=req.aspect_ratio or "portrait",
        session_id=req.session_id,
        mode=mode,
        suggested_model=pref_style,
        character_card=character_card,
    )

    plan = await asyncio.to_thread(
        art_director.plan_from_visual_intent,
        visual_intent=intent,
        preferred_style=pref_style,
        session_id=req.session_id,
        mode=mode,
        character=character_card,
    )

    forge_kwargs = plan.to_forge_kwargs()

    # Destination directory
    sess_out_dir = OUTPUTS_DIR / "sessions" / req.session_id
    sess_out_dir.mkdir(parents=True, exist_ok=True)
    forge_kwargs["output_dir"] = sess_out_dir
    forge_kwargs["session_id"] = req.session_id

    # Apply any client parameter overrides
    if req.override_params and isinstance(req.override_params, dict):
        forge_kwargs.update(req.override_params)

    # 5. Enqueue in TaskQueueWorker (Priority 10 - PRIORITY_NORMAL)
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def _execute_forge_generation():
        try:
            # TaskQueueWorker automatically invokes VRAMArbiter.prepare_for_task('image')
            saved_path = forge.text_to_image(**forge_kwargs)
            loop.call_soon_threadsafe(fut.set_result, Path(saved_path))
        except Exception as exc:
            loop.call_soon_threadsafe(fut.set_exception, exc)

    inference_task = InferenceTask(
        name=f"ArtGen_{req.session_id}",
        task_type="image",
        func=_execute_forge_generation,
        priority=PRIORITY_NORMAL,
        metadata={"session_id": req.session_id},
    )
    task_queue.enqueue(inference_task)

    try:
        image_path: Path = await fut
    except Exception as exc:
        logger.error(f"[ArtRoute] Generation failed: {exc}", exc_info=True)
        return GenerateArtResponse(
            success=False,
            error=f"Ошибка генерации изображения: {exc}",
        )

    # 6. Build GenerationParamsPayload
    gen_params = GenerationParamsPayload(
        model_name=plan.model_title or plan.target_checkpoint or chosen_model,
        seed=int(forge_kwargs.get("seed", -1)),
        steps=int(forge_kwargs.get("steps", 28)),
        sampler=str(forge_kwargs.get("sampler_name", "Euler a")),
        cfg_scale=float(forge_kwargs.get("cfg_scale", 6.0)),
        denoising_strength=float(forge_kwargs.get("denoising_strength", 0.35)),
        prompt=str(forge_kwargs.get("prompt", "")),
        negative_prompt=str(forge_kwargs.get("negative_prompt", "")),
        image_path=str(image_path),
        width=forge_kwargs.get("width"),
        height=forge_kwargs.get("height"),
        hires_upscaler=forge_kwargs.get("hr_upscaler"),
        enable_hr=forge_kwargs.get("enable_hr"),
        enable_adetailer=forge_kwargs.get("enable_adetailer"),
    )

    image_url = f"/media/image?path={image_path.as_posix()}"

    # 7. Persist to session messages and chat_history
    if chat_file.exists():
        def _save_art_message():
            try:
                with open(chat_file, "r", encoding="utf-8") as f:
                    s_data = json.load(f)
                art_msg = {
                    "id": str(uuid.uuid4()),
                    "role": "assistant",
                    "content": f"[Сгенерирована сцена]\n![Иллюстрация]({image_url})",
                    "image_path": str(image_path),
                    "image_url": image_url,
                    "generation_params": gen_params.model_dump(),
                    "timestamp": datetime.now().isoformat(),
                }
                s_data.setdefault("messages", []).append(art_msg)
                s_data.setdefault("chat_history", []).append({
                    "role": "assistant",
                    "content": f"[Сгенерирована сцена: {gen_params.prompt[:80]}]",
                })
                s_data["updated_at"] = datetime.now().isoformat()
                atomic_write_json(chat_file, s_data)
                SessionManager.set_active_session(s_data)
            except Exception as e_save:
                logger.error(f"[ArtRoute] Failed to save art to session: {e_save}")

        await asyncio.to_thread(_save_art_message)

    return GenerateArtResponse(
        success=True,
        image_url=image_url,
        image_path=str(image_path),
        generation_params=gen_params,
    )


@router.post("/direct-command", response_model=GenerateArtResponse)
async def direct_art_command(
    req: DirectArtCommandRequest,
    forge: ForgeClient = Depends(get_forge_client),
    art_director: ArtDirector = Depends(get_art_director),
    task_queue: TaskQueueWorker = Depends(get_task_queue_worker),
) -> GenerateArtResponse:
    """
    Parses manual CLI command (!art / !арт / !img) with arguments:
    --model, --ar, --size, --steps, --cfg, --denoise, --hires, --ad, --seed, --neg
    and executes via TaskQueueWorker bypassing LLM inference.
    """
    cmd = req.command.strip()
    # Strip trigger prefix
    cmd_body = re.sub(r"^!(?:art|арт|img)\s*", "", cmd, flags=re.IGNORECASE).strip()

    pos_prompt, neg_prompt, cli_params = parse_direct_command_params(cmd_body)

    # Preferred style / model override
    model_override = cli_params.get("model_override")
    seed_override = cli_params.get("seed", -1)

    plan = await asyncio.to_thread(
        art_director.build_direct_plan,
        raw_prompt=pos_prompt,
        preferred_style=model_override,
        seed=seed_override,
    )

    forge_kwargs = plan.to_forge_kwargs()

    # Apply explicit CLI flags
    if "steps" in cli_params:
        forge_kwargs["steps"] = cli_params["steps"]
    if "cfg_scale" in cli_params:
        forge_kwargs["cfg_scale"] = cli_params["cfg_scale"]
    if "denoising_strength" in cli_params:
        forge_kwargs["denoising_strength"] = cli_params["denoising_strength"]
    if "enable_hr" in cli_params:
        forge_kwargs["enable_hr"] = cli_params["enable_hr"]
    if "hr_scale" in cli_params:
        forge_kwargs["hr_scale"] = cli_params["hr_scale"]
    if "enable_adetailer" in cli_params:
        forge_kwargs["enable_adetailer"] = cli_params["enable_adetailer"]
    if "seed" in cli_params:
        forge_kwargs["seed"] = cli_params["seed"]
    if "width" in cli_params and "height" in cli_params:
        forge_kwargs["width"] = cli_params["width"]
        forge_kwargs["height"] = cli_params["height"]

    if neg_prompt:
        existing_neg = forge_kwargs.get("negative_prompt", "")
        forge_kwargs["negative_prompt"] = f"{existing_neg}, {neg_prompt}".strip(" ,")

    sess_out_dir = OUTPUTS_DIR / "sessions" / req.session_id
    sess_out_dir.mkdir(parents=True, exist_ok=True)
    forge_kwargs["output_dir"] = sess_out_dir
    forge_kwargs["session_id"] = req.session_id

    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def _execute():
        try:
            saved = forge.text_to_image(**forge_kwargs)
            loop.call_soon_threadsafe(fut.set_result, Path(saved))
        except Exception as e:
            loop.call_soon_threadsafe(fut.set_exception, e)

    inference_task = InferenceTask(
        name=f"DirectArt_{req.session_id}",
        task_type="image",
        func=_execute,
        priority=PRIORITY_NORMAL,
        metadata={"session_id": req.session_id},
    )
    task_queue.enqueue(inference_task)

    try:
        img_path: Path = await fut
    except Exception as exc:
        return GenerateArtResponse(success=False, error=str(exc))

    gen_params = GenerationParamsPayload(
        model_name=plan.model_title or plan.target_checkpoint or (model_override or "Direct"),
        seed=int(forge_kwargs.get("seed", -1)),
        steps=int(forge_kwargs.get("steps", 28)),
        sampler=str(forge_kwargs.get("sampler_name", "Euler a")),
        cfg_scale=float(forge_kwargs.get("cfg_scale", 6.0)),
        denoising_strength=float(forge_kwargs.get("denoising_strength", 0.35)),
        prompt=str(forge_kwargs.get("prompt", pos_prompt)),
        negative_prompt=str(forge_kwargs.get("negative_prompt", neg_prompt)),
        image_path=str(img_path),
        width=forge_kwargs.get("width"),
        height=forge_kwargs.get("height"),
        hires_upscaler=forge_kwargs.get("hr_upscaler"),
        enable_hr=forge_kwargs.get("enable_hr"),
        enable_adetailer=forge_kwargs.get("enable_adetailer"),
    )

    image_url = f"/media/image?path={img_path.as_posix()}"

    # Update chat file
    chat_file = HISTORY_DIR / f"chat_{req.session_id}.json"
    if chat_file.exists():
        def _save():
            try:
                with open(chat_file, "r", encoding="utf-8") as f:
                    s = json.load(f)
                art_msg = {
                    "id": str(uuid.uuid4()),
                    "role": "assistant",
                    "content": f"[Прямой рендер: {cmd}]\n![Иллюстрация]({image_url})",
                    "image_path": str(img_path),
                    "image_url": image_url,
                    "generation_params": gen_params.model_dump(),
                    "timestamp": datetime.now().isoformat(),
                }
                s.setdefault("messages", []).append(art_msg)
                s["updated_at"] = datetime.now().isoformat()
                atomic_write_json(chat_file, s)
            except Exception:
                pass
        await asyncio.to_thread(_save)

    return GenerateArtResponse(
        success=True,
        image_url=image_url,
        image_path=str(img_path),
        generation_params=gen_params,
    )


@router.get("/progress", response_model=ArtProgressResponse)
async def get_art_progress(
    forge: ForgeClient = Depends(get_forge_client),
    task_queue: TaskQueueWorker = Depends(get_task_queue_worker),
) -> ArtProgressResponse:
    """
    Returns real-time generation telemetry from SD Forge /sdapi/v1/progress.
    """
    cur_task = task_queue.current_task
    is_active = (cur_task is not None and cur_task.task_type in ("image", "scene_art", "scene_background"))

    def _fetch_progress():
        try:
            import requests
            resp = requests.get(
                f"{forge.base_url}/sdapi/v1/progress",
                params={"skip_current_image": "true"},
                timeout=1.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                state = data.get("state", {})
                prog = float(data.get("progress", 0.0))
                step = int(state.get("sampling_step", 0))
                total = int(state.get("sampling_steps", 0))
                eta = float(data.get("eta_relative", 0.0))
                textinfo = str(state.get("textinfo") or "")
                return prog, step, total, eta, textinfo
        except Exception:
            pass
        return 0.0, 0, 0, 0.0, ""

    prog, step, total, eta, textinfo = await asyncio.to_thread(_fetch_progress)

    is_gen = bool(is_active or prog > 0.0 or step > 0)

    stage_name = None
    if textinfo:
        ti_low = textinfo.lower()
        if "adetailer" in ti_low or "face" in ti_low:
            stage_name = "Реставрация ADetailer (Лицо/Руки)"
        elif "hires" in ti_low:
            stage_name = "Hires.fix (Масштабирование деталей)"
        else:
            stage_name = f"Рендеринг (Шаг {step} из {total})"
    elif is_gen:
        stage_name = f"Рендеринг (Шаг {step} из {total})" if total > 0 else "Инициализация VRAM / Выгрузка LLM..."

    return ArtProgressResponse(
        is_generating=is_gen,
        progress=round(prog, 3),
        step=step,
        total_steps=total,
        eta_relative=round(eta, 1),
        stage_name=stage_name,
    )


@router.post("/interrupt")
async def interrupt_art_generation(
    forge: ForgeClient = Depends(get_forge_client),
    task_queue: TaskQueueWorker = Depends(get_task_queue_worker),
):
    """
    Immediately interrupts Forge txt2img inference and aborts TaskQueueWorker task.
    """
    def _interrupt():
        try:
            forge.interrupt()
        except Exception as e:
            logger.debug(f"[ArtRoute] Forge interrupt error: {e}")
        try:
            task_queue.abort_current()
        except Exception as e:
            logger.debug(f"[ArtRoute] TaskQueue abort error: {e}")

    await asyncio.to_thread(_interrupt)
    return {"success": True, "interrupted": True}


@router.post("/scene-background", response_model=SceneBackgroundResponse)
async def generate_scene_background(
    req: GenerateArtRequest,
    forge: ForgeClient = Depends(get_forge_client),
    art_director: ArtDirector = Depends(get_art_director),
    task_queue: TaskQueueWorker = Depends(get_task_queue_worker),
) -> SceneBackgroundResponse:
    """
    Generates pure environment scenery without humans (scenery, empty landscape, no humans, 768x512).
    Saves background for session in history/cast_{session_id}.json and updates chat viewport backdrop.
    """
    location = req.prompt or "cyberpunk city street at night, neon reflections, rain"

    bg_pos = f"masterpiece, best quality, scenic, {location}, empty landscape, architecture, atmospheric lighting, detailed background"
    bg_neg = "human, humans, person, 1girl, 1boy, character, portrait, anime girl, crowd, face"

    intent = VisualIntent(
        prompt=bg_pos,
        raw_user_query=location,
        user_neg=bg_neg,
        aspect_ratio="landscape",
        session_id=req.session_id,
        mode="roleplay",
        is_scene_background=True,
    )

    plan = await asyncio.to_thread(
        art_director.plan_from_visual_intent,
        visual_intent=intent,
        preferred_style="realistic",
        session_id=req.session_id,
        mode="roleplay",
    )

    forge_kwargs = plan.to_forge_kwargs()
    forge_kwargs["width"] = 768
    forge_kwargs["height"] = 512
    forge_kwargs["enable_adetailer"] = False  # No humans in background

    sess_out_dir = OUTPUTS_DIR / "sessions" / req.session_id
    sess_out_dir.mkdir(parents=True, exist_ok=True)
    forge_kwargs["output_dir"] = sess_out_dir
    forge_kwargs["session_id"] = req.session_id

    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def _run():
        try:
            p = forge.text_to_image(**forge_kwargs)
            loop.call_soon_threadsafe(fut.set_result, Path(p))
        except Exception as e:
            loop.call_soon_threadsafe(fut.set_exception, e)

    inference_task = InferenceTask(
        name=f"SceneBg_{req.session_id}",
        task_type="scene_background",
        func=_run,
        priority=PRIORITY_NORMAL,
        metadata={"session_id": req.session_id},
    )
    task_queue.enqueue(inference_task)

    try:
        bg_path: Path = await fut
    except Exception as exc:
        return SceneBackgroundResponse(success=False, error=str(exc))

    bg_url = f"/media/image?path={bg_path.as_posix()}"

    # Update cast_{session_id}.json & chat_{session_id}.json
    def _save_bg():
        try:
            cast_data = RPCastManager.load_cast(req.session_id)
            cast_data["scene_background_url"] = bg_url
            cast_data["location_dna"] = location
            RPCastManager.save_cast(req.session_id, cast_data)
        except Exception as e_cast:
            logger.debug(f"[ArtRoute] Cast update error: {e_cast}")

        chat_file = HISTORY_DIR / f"chat_{req.session_id}.json"
        if chat_file.exists():
            try:
                with open(chat_file, "r", encoding="utf-8") as f:
                    s_data = json.load(f)
                s_data["scene_background_url"] = bg_url
                atomic_write_json(chat_file, s_data)
            except Exception:
                pass

    await asyncio.to_thread(_save_bg)

    return SceneBackgroundResponse(
        success=True,
        image_url=bg_url,
        image_path=str(bg_path),
        location=location,
    )


@router.get("/gallery", response_model=ArtGalleryResponse)
async def get_art_gallery(
    session_id: Optional[str] = None,
) -> ArtGalleryResponse:
    """
    Returns list of all generated artworks with paths, dates, and generation metadata.
    """
    def _scan_images() -> List[ArtGalleryItem]:
        items: List[ArtGalleryItem] = []
        found_paths = set()

        # 1. Scan session directory if given
        if session_id:
            sess_dir = OUTPUTS_DIR / "sessions" / session_id
            if sess_dir.exists():
                for ext in ("*.png", "*.jpg", "*.webp"):
                    for p in sess_dir.glob(ext):
                        if p.is_file() and p not in found_paths:
                            found_paths.add(p)

        # 2. Scan all session directories and root outputs
        all_sess = OUTPUTS_DIR / "sessions"
        if all_sess.exists():
            for ext in ("*/*.png", "*/*.jpg", "*/*.webp"):
                for p in all_sess.glob(ext):
                    if p.is_file() and p not in found_paths:
                        found_paths.add(p)

        if OUTPUTS_DIR.exists():
            for ext in ("*.png", "*.jpg", "*.webp"):
                for p in OUTPUTS_DIR.glob(ext):
                    if p.is_file() and p not in found_paths:
                        found_paths.add(p)

        # Sort by mtime descending
        sorted_files = sorted(found_paths, key=lambda f: f.stat().st_mtime, reverse=True)

        for p in sorted_files[:100]:
            mtime_dt = datetime.fromtimestamp(p.stat().st_mtime)
            s_id = p.parent.name if p.parent.parent.name == "sessions" else None
            items.append(
                ArtGalleryItem(
                    id=p.stem,
                    filename=p.name,
                    url=f"/media/image?path={p.as_posix()}",
                    path=str(p),
                    session_id=s_id,
                    timestamp=mtime_dt.isoformat(),
                )
            )
        return items

    gallery_items = await asyncio.to_thread(_scan_images)
    return ArtGalleryResponse(items=gallery_items, total=len(gallery_items))
