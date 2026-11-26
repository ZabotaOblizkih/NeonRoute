"""
Full-featured Forge Stable Diffusion and Art Director routes for NeonRoute Mobile.
Enforces 8 GB VRAM mutual exclusion via TaskQueueWorker and VRAMArbiter.
"""

import re
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends

from config import OUTPUTS_DIR
from services.art_director_config import load_art_director_models
from services.art_director.director import ArtDirector
from services.art_director.contracts import VisualIntent, TargetSubject
from services.art_director.prompting import extract_negations_from_prompt
from services.forge_client import ForgeClient
from services.mode_coordinator import AppMode
from services.model_router import normalize_model_choice
from services.regex_filter import TextRegexFilter
from services.rp_cast_manager import RPCastManager
from services.session_manager import SessionManager
from services.task_queue import TaskQueueWorker
from services.tavern_parser import CharacterCard
from services.vram_arbiter import VRAMArbiter

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
from mobile_server.services.art_service import (
    build_media_image_url,
    enqueue_forge_generation_async,
    get_session_forge_model,
    save_art_message_to_session,
    set_session_forge_model,
)

logger = logging.getLogger("NeonRoute.MobileServer.Art")
router = APIRouter()


@router.get("/models", response_model=ArtModelsResponse)
async def get_forge_models(
    session_id: Optional[str] = None,
) -> ArtModelsResponse:
    """
    Returns list of registered Forge models including 'auto' Art Director option
    and style triggers from art_director_models.json.
    """
    raw_models = await asyncio.to_thread(load_art_director_models)
    active_id = await asyncio.to_thread(get_session_forge_model, session_id)

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
    model_id = await asyncio.to_thread(set_session_forge_model, req.model_id, req.session_id)
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
    Main ArtDirector generation pipeline running sequentially via TaskQueueWorker & VRAMArbiter.
    """
    is_ready = await asyncio.to_thread(forge.is_server_ready, timeout=0.8)
    if not is_ready:
        return GenerateArtResponse(
            success=False,
            error="SD Forge offline (порт 7860 недоступен). Убедитесь, что Forge запущен.",
        )

    session_data = await asyncio.to_thread(SessionManager.load_session, req.session_id) or {}
    mode = AppMode.from_session(session_data).value
    char_data = session_data.get("character_data")
    character_card = CharacterCard.from_dict(char_data) if char_data else None

    raw_text = req.prompt or req.prompt_override or req.text or ""
    extracted_tag, _ = TextRegexFilter.extract_image_prompt(
        raw_text,
        strip_thoughts=True,
        allow_markdown_fallback=True,
    )
    if extracted_tag:
        raw_text = extracted_tag
    elif not raw_text.strip():
        msgs = session_data.get("messages", [])
        for m in reversed(msgs):
            if m.get("role") == "assistant" and m.get("content"):
                c = re.sub(r"<[^>]+>", "", m["content"]).strip()
                raw_text = " ".join(c.split()[:45])
                break
        if not raw_text.strip():
            raw_text = "beautiful cyberpunk portrait, highly detailed, dramatic lighting"

    pos_clean, neg_extracted = extract_negations_from_prompt(raw_text)
    user_neg_parts = [p.strip() for p in (req.negative_prompt,) if p and p.strip()]
    if neg_extracted:
        user_neg_parts.extend(neg_extracted)
    combined_neg = ", ".join(user_neg_parts)

    chosen_model = req.model_id or get_session_forge_model(req.session_id, session_data)
    pref_style = normalize_model_choice(chosen_model)

    intent = VisualIntent(
        prompt=pos_clean,
        target_subject=TargetSubject.COMPANION if (mode == "roleplay" and character_card) else TargetSubject.SCENE_OBJECT,
        raw_user_query=raw_text,
        user_neg=combined_neg,
        aspect_ratio=req.aspect_ratio or "portrait",
        session_id=req.session_id,
        mode=mode,
        suggested_model=pref_style,
        character_card=character_card,
    )

    available_models = await asyncio.to_thread(forge.get_models)
    plan = await asyncio.to_thread(
        art_director.plan_from_visual_intent,
        visual_intent=intent,
        preferred_style=pref_style,
        available_models=available_models,
        session_id=req.session_id,
        mode=mode,
        character=character_card,
    )

    try:
        logger.info(
            f"[MobileArt] /generate session={req.session_id} model={chosen_model or 'auto'} "
            f"ar={req.aspect_ratio or 'portrait'}"
        )
        gen_res = await enqueue_forge_generation_async(
            task_queue=task_queue,
            forge=forge,
            art_director=art_director,
            task_name=f"ArtGen_{req.session_id}",
            task_type="image",
            session_id=req.session_id,
            mode=mode,
            plan=plan,
            override_params=req.override_params,
            preferred_style=pref_style,
            prompt_label=pos_clean,
        )
        image_path = gen_res["image_path"]
        forge_kwargs = gen_res["forge_kwargs"]
    except Exception as exc:
        logger.error(f"[ArtRoute] Generation failed: {exc}", exc_info=True)
        return GenerateArtResponse(
            success=False,
            error=f"Ошибка генерации изображения: {exc}",
        )

    gen_params = GenerationParamsPayload.from_plan(
        plan, forge_kwargs, image_path, fallback_model=chosen_model
    )
    image_url = await asyncio.to_thread(
        save_art_message_to_session,
        req.session_id,
        image_path,
        "Сгенерирована сцена",
        gen_params.model_dump(),
        f"[Сгенерирована сцена: {gen_params.prompt[:80]}]",
    )

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
    Executes manual CLI command (!art / !арт / !img) via ArtDirector.build_direct_plan
    and TaskQueueWorker bypassing LLM inference.
    """
    cmd = req.command.strip()
    plan = await asyncio.to_thread(art_director.build_direct_plan, raw_prompt=cmd)
    try:
        logger.info(f"[MobileArt] /direct-command session={req.session_id} cmd={cmd[:60]!r}")
        gen_res = await enqueue_forge_generation_async(
            task_queue=task_queue,
            forge=forge,
            art_director=art_director,
            task_name=f"DirectArt_{req.session_id}",
            task_type="image",
            session_id=req.session_id,
            plan=plan,
            prompt_label=cmd,
        )
        img_path = gen_res["image_path"]
        forge_kwargs = gen_res["forge_kwargs"]
    except Exception as exc:
        return GenerateArtResponse(success=False, error=str(exc))

    gen_params = GenerationParamsPayload.from_plan(
        plan,
        forge_kwargs,
        img_path,
        fallback_model="Direct",
        fallback_prompt=plan.positive_prompt,
        fallback_neg=plan.negative_prompt,
    )
    image_url = await asyncio.to_thread(
        save_art_message_to_session,
        req.session_id,
        img_path,
        f"Прямой рендер: {cmd}",
        gen_params.model_dump(),
    )

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
    cur_meta = (getattr(cur_task, "metadata", None) or {}) if cur_task is not None else {}
    is_gallery_prep = bool(cur_task is not None and cur_meta.get("is_gallery_art_pass"))
    is_cur_art = cur_task is not None and (
        cur_task.task_type in ("image", "scene_art", "scene_background") or is_gallery_prep
    )
    is_pending_art = bool(
        hasattr(task_queue, "has_pending_tasks")
        and task_queue.has_pending_tasks(("image", "scene_art", "scene_background"))
    )
    is_active = bool(is_cur_art or is_pending_art)

    snapshot = await asyncio.to_thread(forge.get_progress, 1.0)
    prog = float(snapshot["progress"])
    step = int(snapshot["step"])
    total = int(snapshot["total_steps"])
    eta = float(snapshot["eta_relative"])
    stage_name = snapshot["stage_name"]

    is_gen = bool(is_active or prog > 0.0 or step > 0)
    if is_gallery_prep and step == 0 and prog == 0.0:
        stage_name = "🧠 Подготовка тегов для Арт-директора..."
    elif is_pending_art and not is_cur_art and step == 0 and prog == 0.0:
        stage_name = "⏳ Ожидание очереди генерации в Forge..."
    elif is_cur_art and prog >= 0.99 and step == 0:
        stage_name = "✨ Постобработка (Hires / ADetailer / Сохранение)..."
    elif is_gen and not stage_name:
        stage_name = f"Рендеринг (Шаг {step} из {total})" if total > 0 else "Инициализация VRAM / Выгрузка LLM..."
    elif stage_name and total > 0 and stage_name == "🎨 Создание общего плана":
        stage_name = f"Рендеринг (Шаг {step} из {total})"

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
    session_id: Optional[str] = None,
    forge: ForgeClient = Depends(get_forge_client),
    task_queue: TaskQueueWorker = Depends(get_task_queue_worker),
):
    """
    Immediately interrupts Forge txt2img inference and aborts TaskQueueWorker task.
    """
    from mobile_server.services.chat_service import publish_session_event

    def _interrupt():
        cur_task = getattr(task_queue, "current_task", None)
        meta = getattr(cur_task, "metadata", None) if cur_task is not None else None
        target_sid = session_id or (meta.get("session_id") if isinstance(meta, dict) else None)
        try:
            forge.interrupt()
        except Exception as e:
            logger.debug(f"[ArtRoute] Forge interrupt error: {e}")
        try:
            task_queue.abort_current()
        except Exception as e:
            logger.debug(f"[ArtRoute] TaskQueue abort error: {e}")
        if target_sid:
            publish_session_event(target_sid, "art_interrupted", {"session_id": target_sid})

    logger.info(f"[MobileArt] /interrupt session={session_id or 'active'}")
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
    Generates pure environment scenery without humans (768x512).
    Saves background in history/cast_{session_id}.json and session via SessionManager.
    """
    location = req.prompt or "cyberpunk city street at night, neon reflections, rain"

    plan = await asyncio.to_thread(
        art_director.build_scene_background_plan,
        location_prompt=location,
        preferred_style="realistic",
        session_id=req.session_id,
        use_visual_intent=True,
    )

    try:
        logger.info(f"[MobileArt] /scene-background session={req.session_id} location={location[:60]!r}")
        gen_res = await enqueue_forge_generation_async(
            task_queue=task_queue,
            forge=forge,
            art_director=art_director,
            task_name=f"SceneBg_{req.session_id}",
            task_type="scene_background",
            session_id=req.session_id,
            plan=plan,
            prompt_label=location,
        )
        bg_path = gen_res["image_path"]
    except Exception as exc:
        return SceneBackgroundResponse(success=False, error=str(exc))

    bg_url = build_media_image_url(bg_path)

    def _save_bg():
        try:
            cast_data = RPCastManager.load_cast(req.session_id)
            cast_data.setdefault("environment", {})["location_dna"] = location
            cast_data["location_dna"] = location
            cast_data["scene_background_url"] = bg_url
            RPCastManager.save_cast(req.session_id, cast_data)
        except Exception as e_cast:
            logger.debug(f"[ArtRoute] Cast update error: {e_cast}")

        s_data = SessionManager.load_session(req.session_id)
        if isinstance(s_data, dict):
            s_data["background_image"] = str(bg_path.resolve())
            s_data["scene_background_url"] = bg_url
            SessionManager.save_session(s_data)

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
    Returns list of generated artworks with paths, dates, and URLs.
    """
    def _scan_images() -> List[ArtGalleryItem]:
        items: List[ArtGalleryItem] = []
        found_paths = set()

        def _collect(base_dir: Path, patterns: tuple[str, ...]):
            if base_dir.exists():
                for pat in patterns:
                    for p in base_dir.glob(pat):
                        if p.is_file():
                            found_paths.add(p)

        if session_id:
            sess_dir = OUTPUTS_DIR / "sessions" / session_id
            _collect(sess_dir, ("*.png", "*.jpg", "*.webp", "images/*.png", "images/*.jpg", "images/*.webp"))

        _collect(OUTPUTS_DIR / "sessions", ("*/*.png", "*/*.jpg", "*/*.webp", "*/images/*.png", "*/images/*.jpg", "*/images/*.webp"))
        _collect(OUTPUTS_DIR, ("*.png", "*.jpg", "*.webp"))

        sorted_files = sorted(found_paths, key=lambda f: f.stat().st_mtime, reverse=True)
        for p in sorted_files[:100]:
            mtime_dt = datetime.fromtimestamp(p.stat().st_mtime)
            if p.parent.name == "images" and p.parent.parent.parent.name == "sessions":
                s_id = p.parent.parent.name
            elif p.parent.parent.name == "sessions":
                s_id = p.parent.name
            else:
                s_id = None

            items.append(
                ArtGalleryItem(
                    id=p.stem,
                    filename=p.name,
                    url=build_media_image_url(p),
                    path=str(p),
                    session_id=s_id,
                    timestamp=mtime_dt.isoformat(),
                )
            )
        return items

    gallery_items = await asyncio.to_thread(_scan_images)
    return ArtGalleryResponse(items=gallery_items, total=len(gallery_items))
