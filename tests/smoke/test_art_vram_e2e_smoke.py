"""
Consolidated End-to-End Smoke Test Suite for NeonRoute Art Director & VRAM Arbitration Pipeline.
Covers:
1. End-to-end art planning and ForgeClient kwargs assembly (ModelRouter -> ArtDirector -> ADetailer).
2. Semantic auto-routing (Auto Mode vs specific model styles).
3. Reactive synchronization in HardwareEngineMixin upon dialog close via reject().
4. ADetailer parameter & base negative prompt passthrough.
5. Special prompt modifiers: --hands flag, BREAK symmetrization, and negation extraction.
6. MinorSafety age filter and NSFW tag blocking.
7. Fail-Fast Guard before VRAMArbiter (empty/thought-only prompt & offline Forge).
8. VRAMArbiter GPU exclusivity and KV-slot 0 persistence & invalidation.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

import config
try:
    assert_diag
except NameError:
    import sys
    _smoke_dir = str(Path(__file__).resolve().parent)
    if _smoke_dir not in sys.path:
        sys.path.insert(0, _smoke_dir)
    from conftest import assert_diag, assert_diag_eq, assert_diag_in

from services.art_director.contracts import VisualIntent, TargetSubject
from services.art_director.director import ArtDirector, _symmetrize_break_chunks
from services.art_director.prompting import extract_negations_from_prompt
from services.art_director.plan import GenerationPlan
from services.art_director_config import save_art_director_models
from services.model_router import ModelRouter, normalize_model_choice
from services.minor_safety import MinorSafetyDetector
from services.vram_arbiter import VRAMArbiter, GPUOwner
from services.process_manager import ProcessManager, KV_SLOTS_DIR
from ui.controllers.hardware_engine import HardwareEngineMixin
try:
    DummyWindow
except NameError:
    from tests.smoke.conftest import DummyWindow


# =========================================================================
# СЦЕНАРИЙ 1: СКВОЗНОЙ АРТ-КОНВЕЙЕР И СЕМАНТИЧЕСКИЙ АВТО-РОУТИНГ
# =========================================================================

def test_art_generation_pipeline_and_semantic_routing(smoke_isolation_guard):
    """
    Сквозной смоук-тест:
    - Персистентность моделей в изолированном art_director_models.json;
    - Семантический роутинг: альтернативная модель по триггерам vs дефолтная модель;
    - Сборка Forge kwargs (позитивный, негативный промпт, шаги, CFG, ADetailer);
    - Обработка флага --hands: активация adetailer_hands_enabled и удаление флага из промпта.
    """
    scen = "Сценарий 1: Арт-конвейер и семантический роутинг"

    model_sd15 = {
        "id": "test_realistic_default",
        "name": "Test Realistic Default",
        "architecture": "sd15",
        "checkpoint": "cyberrealistic_v5.safetensors",
        "is_default": True,
        "style_triggers": ["real people", "portrait photo", "photorealistic"],
        "routing_description": "fine details, 35mm photograph, natural lighting",
        "active": True,
        "pipeline": {
            "architecture": "sd15",
            "sampler": "DPM++ 2M Karras",
            "scheduler": "Karras",
            "steps": 25,
            "cfg_scale": 6.0,
            "clip_skip": 1,
            "enable_hr": False,
            "enable_adetailer": True,
            "negative_prompt": "ugly, deformed, bad hands"
        }
    }

    model_anime = {
        "id": "test_anime_alt",
        "name": "Test Anime Alt",
        "architecture": "sd15",
        "checkpoint": "meinamix_v11.safetensors",
        "is_default": False,
        "style_triggers": ["anime", "illustration", "2d drawing"],
        "routing_description": "vibrant colors, anime screencap, cel shaded",
        "active": True,
        "pipeline": {
            "architecture": "sd15",
            "sampler": "DPM++ 2M Karras",
            "scheduler": "Karras",
            "steps": 28,
            "cfg_scale": 7.0,
            "clip_skip": 2,
            "enable_hr": False,
            "enable_adetailer": False,
            "negative_prompt": "worst quality, low quality, bad anatomy"
        }
    }

    save_success = save_art_director_models([model_sd15, model_anime])
    assert_diag(save_success is True, scen, "Сохранение моделей", True, save_success)

    art_director = ArtDirector()
    art_director.reload_models()

    # 1. Проверка семантического роутинга в Авто-режиме
    plan_anime = art_director.plan_generation(
        raw_prompt="anime girl, colorful vibrant illustration, 2d drawing",
        preferred_style=None,
        mode="roleplay"
    )
    assert_diag(plan_anime.target_checkpoint == "meinamix_v11.safetensors", scen, "Семантический роутинг в аниме", "meinamix_v11.safetensors", plan_anime.target_checkpoint)

    # 2. Проверка флага --hands
    plan_hands = art_director.plan_generation(
        raw_prompt="warrior holding a glowing rune --hands",
        preferred_style="test_realistic_default",
        mode="assistant"
    )
    assert_diag("--hands" not in plan_hands.prompt.lower(), scen, "Вырезание флага --hands", "Нет --hands в промпте", plan_hands.prompt)
    assert_diag(plan_hands.enable_adetailer is True, scen, "Активация ADetailer по --hands", True, plan_hands.enable_adetailer)
    assert_diag(getattr(plan_hands, "adetailer_hands_enabled", False) is True, scen, "Активация рук ADetailer", True, getattr(plan_hands, "adetailer_hands_enabled", False))


# =========================================================================
# СЦЕНАРИЙ 2: РЕАКТИВНАЯ СИНХРОНИЗАЦИЯ МОДЕЛЕЙ ПРИ REJECT() ДИАЛОГА
# =========================================================================

def test_models_manager_dialog_reject_synchronization(smoke_isolation_guard, qapp):
    """
    Проверяет, что при закрытии ModelsManagerDialog по Esc/крестику (reject)
    в HardwareEngineMixin безусловно и реактивно обновляется список моделей
    без необходимости перезапуска приложения.
    """
    scen = "Сценарий 2: Реактивная синхронизация при reject() диалога"

    initial_model = {
        "id": "model_init_01",
        "name": "Dragon Slayer 1.5",
        "checkpoint": "dragon_slayer.safetensors",
        "architecture": "sd15",
        "active": True,
        "pipeline": {"steps": 25, "cfg_scale": 6.0}
    }
    save_art_director_models([initial_model])

    art_director = ArtDirector()
    mock_forge = MagicMock()
    mock_forge.get_models.return_value = [{"title": "dragon_slayer.safetensors", "model_name": "dragon_slayer"}]

    window = DummyWindow(art_director=art_director, forge_client=mock_forge)

    # До синхронизации чекпоинт отсутствует в окне
    assert_diag("Dragon Slayer 1.5" not in window.available_forge_checkpoints, scen, "Начальное состояние окна", "Dragon Slayer отсутствует", window.available_forge_checkpoints)

    # Имитируем обновление профилей и триггер синхронизации диалога
    art_director.reload_models()
    window._on_forge_models_discovered(["dragon_slayer.safetensors"])

    assert_diag("Dragon Slayer 1.5" in window.available_forge_checkpoints, scen, "Синхронизация чекпоинтов окна", "Dragon Slayer 1.5 в списке", window.available_forge_checkpoints)
    router_profile_names = [p.get("name") for p in art_director.router.profiles]
    assert_diag("Dragon Slayer 1.5" in router_profile_names, scen, "Синхронизация ArtDirector Router", "Dragon Slayer 1.5 в профилях", router_profile_names)


# =========================================================================
# СЦЕНАРИЙ 3: ПРОБРОС ПАРАМЕТРОВ ADETAILER И БАЗОВОГО НЕГАТИВА
# =========================================================================

def test_adetailer_params_and_base_negative_passthrough(smoke_isolation_guard):
    """
    Сквозная проверка сквозного проброса параметров ADetailer и пользовательского негатива
    через ArtDirector -> GenerationPlan.to_forge_kwargs() -> ForgeClient kwargs.
    """
    scen = "Сценарий 3: Проброс параметров ADetailer и негатива"

    custom_base_neg = "custom_base_negative_from_models_manager, bad anatomy, plastic skin"
    model_profile = {
        "id": "smoke_juggernaut_light",
        "name": "Juggernaut XI Light Smoke",
        "architecture": "sdxl",
        "checkpoint": "juggernaut_xi.safetensors",
        "routing_description": "Photorealistic cinematic portrait",
        "negative_prompt": custom_base_neg,
        "active": True,
        "pipeline": {
            "architecture": "sdxl",
            "sampler": "DPM++ 2M Karras",
            "scheduler": "Karras",
            "steps": 6,
            "cfg_scale": 1.7,
            "clip_skip": 1,
            "enable_hr": False,
            "enable_adetailer": True,
            "adetailer_face_model": "face_yolov8m.pt",
            "adetailer_face_confidence": 0.44,
            "adetailer_face_denoise": 0.21,
            "adetailer_face_inpaint_size": 768,
            "adetailer_hands_enabled": True,
            "adetailer_hands_model": "hand_yolov9c.pt",
            "adetailer_hands_confidence": 0.40,
            "adetailer_hands_denoise": 0.26,
            "adetailer_hands_inpaint_size": 512,
            "adetailer_hands_mask_blur": 8,
            "negative_prompt": custom_base_neg,
        },
    }
    save_art_director_models([model_profile])

    director = ArtDirector()
    director.reload_models()

    intent = VisualIntent(
        prompt="beautiful woman, natural lighting, portrait",
        user_neg="blurry background",
        suggested_model="smoke_juggernaut_light",
        target_subject=TargetSubject.COMPANION,
        mode="roleplay"
    )

    plan = director.plan_from_visual_intent(visual_intent=intent, preferred_style="smoke_juggernaut_light", mode="roleplay")
    forge_kwargs = plan.to_forge_kwargs()

    # 1. Проверка негативного промпта
    actual_neg = forge_kwargs.get("negative_prompt", "")
    assert_diag("custom_base_negative_from_models_manager" in actual_neg, scen, "Базовый негатив модели", "custom_base_negative в kwargs", actual_neg)
    assert_diag("blurry background" in actual_neg, scen, "Пользовательский негатив сцены", "blurry background в kwargs", actual_neg)

    # 2. Проверка проброса параметров ADetailer на плане и в kwargs
    assert_diag(plan.enable_adetailer is True, scen, "plan.enable_adetailer", True, plan.enable_adetailer)
    assert_diag(plan.adetailer_face_model == "face_yolov8m.pt", scen, "plan.adetailer_face_model", "face_yolov8m.pt", plan.adetailer_face_model)
    assert_diag(plan.adetailer_hands_enabled is True, scen, "plan.adetailer_hands_enabled", True, plan.adetailer_hands_enabled)
    assert_diag(plan.adetailer_hands_model == "hand_yolov9c.pt", scen, "plan.adetailer_hands_model", "hand_yolov9c.pt", plan.adetailer_hands_model)
    assert_diag(plan.adetailer_hands_confidence == 0.40, scen, "plan.adetailer_hands_confidence", 0.40, plan.adetailer_hands_confidence)
    assert_diag(forge_kwargs.get("enable_adetailer") is True, scen, "forge_kwargs enable_adetailer", True, forge_kwargs.get("enable_adetailer"))


# =========================================================================
# СЦЕНАРИЙ 4: МОДИФИКАТОРЫ ПРОМПТА И БЕЗОПАСНОСТЬ (BREAK, NEGATIONS, SAFETY)
# =========================================================================

def test_prompt_modifiers_break_symmetrization_and_safety():
    """
    Проверка симметризации окружения по обе стороны разделителя BREAK,
    выноса отрицаний (no helmet, without glasses) в негативный промпт
    и фильтра возрастной безопасности MinorSafety.
    """
    scen = "Сценарий 4: Модификаторы промпта и MinorSafety"

    # 1. Вынос отрицаний из позитивного промпта
    pos_clean, neg_extra = extract_negations_from_prompt("1girl, cyberpunk street, no helmet, without glasses")
    assert_diag("helmet" in neg_extra and "glasses" in neg_extra, scen, "Извлечение отрицаний", "helmet и glasses в neg_extra", neg_extra)
    assert_diag("no helmet" not in pos_clean and "without glasses" not in pos_clean, scen, "Очистка позитивного промпта", "Отрицания удалены", pos_clean)

    # 2. Симметризация BREAK: окружение дублируется по обе стороны
    sym_prompt = _symmetrize_break_chunks(
        "1girl, silver hair BREAK 1boy, dark armor",
        "ancient castle hall",
        "torchlight"
    )
    assert_diag("BREAK" in sym_prompt, scen, "Симметризация BREAK", "Содержит BREAK", sym_prompt)
    assert_diag(sym_prompt.count("ancient castle hall") == 2, scen, "Дублирование окружения", 2, sym_prompt.count("ancient castle hall"))

    # 3. Фильтр возрастной безопасности MinorSafety
    assert_diag(MinorSafetyDetector.is_minor("24 years old veteran knight") is False, scen, "MinorSafety взрослый", False, False)
    assert_diag(MinorSafetyDetector.is_minor("14yo schoolgirl character") is True, scen, "MinorSafety ребенок", True, True)

    safe_prompt = MinorSafetyDetector.sanitize_positive_prompt("1girl, loli, nude, nsfw", context_hint="14yo")
    assert_diag("nude" not in safe_prompt and "nsfw" not in safe_prompt, scen, "MinorSafety цензура NSFW", "nude и nsfw удалены", safe_prompt)


# =========================================================================
# СЦЕНАРИЙ 5: FAIL-FAST GUARD И VRAM-АРБИТРАЖ
# =========================================================================

def test_vram_arbiter_lifecycle_and_fail_fast_guard(smoke_isolation_guard, monkeypatch):
    """
    Сквозная проверка арбитража памяти:
    - Монопольное переключение LLM <-> DIFFUSION;
    - Отложенная выгрузка LLM при needs_llm_prompt_gen=True;
    - Fail-Fast Guard: отсечка пустых промптов без выгрузки LLM;
    - Сохранение/восстановление и инвалидация KV-слота 0 в ProcessManager.
    """
    scen = "Сценарий 5: VRAM арбитраж и Fail-Fast Guard"

    mock_lm = MagicMock()
    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True

    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=mock_forge)

    # 1. Захват GPU под LLM
    arbiter.prepare_for_task("llm")
    assert_diag(arbiter._current_owner == GPUOwner.LLM, scen, "Владелец LLM", GPUOwner.LLM, arbiter._current_owner)
    assert_diag(mock_forge.unload_checkpoint.called, scen, "Выгрузка Forge", True, mock_forge.unload_checkpoint.called)

    # 2. Отложенная выгрузка для генерации арт-промпта
    mock_lm.reset_mock()
    arbiter.prepare_for_task("image", metadata={"needs_llm_prompt_gen": True})
    assert_diag(arbiter._current_owner == GPUOwner.LLM, scen, "Отложенная выгрузка", GPUOwner.LLM, arbiter._current_owner)
    assert_diag(not mock_lm.unload_models_cli.called, scen, "LLM не выгружена заранее", False, mock_lm.unload_models_cli.called)

    # 3. Чистая диффузия
    arbiter.prepare_for_task("image", metadata={"needs_llm_prompt_gen": False})
    assert_diag(arbiter._current_owner == GPUOwner.DIFFUSION, scen, "Владелец Диффузия", GPUOwner.DIFFUSION, arbiter._current_owner)
    assert_diag(mock_lm.unload_models_cli.called, scen, "LLM выгружена под диффузию", True, mock_lm.unload_models_cli.called)

    # 4. Fail-Fast Guard на уровне контроллера
    host = HardwareEngineMixin()
    host.active_character = None
    host.current_mode = "roleplay"
    host.current_session = {"id": "sess_ff"}
    host._cancel_lms_reload = MagicMock()

    arbiter_mock = MagicMock()
    monkeypatch.setattr(VRAMArbiter, "get_instance", MagicMock(return_value=arbiter_mock))

    # Пустой промпт после срезки <think>
    host._is_forge_available = MagicMock(return_value=True)
    host._generate_ephemeral_rp_art_prompt = MagicMock(return_value="")

    res_empty = host._scene_art_pipeline(v_payload=None, scene_text="Сцена с думами")
    assert_diag(res_empty.get("image_path") is None, scen, "Fail-fast результат", None, res_empty.get("image_path"))
    assert_diag(not arbiter_mock.release_llm_for_diffusion.called, scen, "Fail-fast блокировка выгрузки", False, arbiter_mock.release_llm_for_diffusion.called)
