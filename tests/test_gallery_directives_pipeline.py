# tests/test_gallery_directives_pipeline.py
"""
Unit and integration tests for the Dual-Directive Gallery Art Pass pipeline:
1. Architectural prompt directive registry (SD 1.5 vs SDXL).
2. Dynamic model architecture resolution (UI profile vs filename heuristic).
3. Specialized prompt compilation with model hints and flags.
4. ModelRouter dynamic default profile resolution (zero hardcoding).
5. normalize_model_choice boundary conditions (Manual vs Auto).
6. Hot-swap resiliency when models are deleted or new checkpoints downloaded.
"""

import pytest
from typing import Dict, Any

from services.model_router import ModelRouter, normalize_model_choice
from services.assistant_prompt_builder import (
    AssistantPromptBuilder,
    resolve_model_architecture,
    resolve_prompt_dialect,
    get_gallery_directive,
    get_cinematic_directive,
    get_art_directive,
    GALLERY_DIRECTIVES_BY_ARCH,
    GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE,
    GALLERY_SKILL_DIRECTIVE_SDXL_TEMPLATE,
    GALLERY_SKILL_DIRECTIVE_TEMPLATE,
    CINEMATIC_DIRECTIVES_BY_ARCH,
    CINEMATIC_SKILL_DIRECTIVE_SD15_TEMPLATE,
    CINEMATIC_SKILL_DIRECTIVE_SDXL_TEMPLATE,
    IMAGE_GENERATION_SKILLS,
)
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.skill_router import SkillRouter
from services.art_director import ArtDirector, VisualIntent
from services.assistant_flow import resolve_effective_thinking, assemble_assistant_context


def test_gallery_directives_registry():
    """Проверяет наличие обеих директив и сохранение обратной совместимости."""
    assert "sd15" in GALLERY_DIRECTIVES_BY_ARCH
    assert "sdxl" in GALLERY_DIRECTIVES_BY_ARCH
    # Обратная совместимость для внешних модулей
    assert GALLERY_SKILL_DIRECTIVE_TEMPLATE == GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE
    assert "[SYSTEM DIRECTIVE: SDXL Image Prompt Engineer]" in GALLERY_SKILL_DIRECTIVE_SDXL_TEMPLATE
    assert "[SYSTEM DIRECTIVE: Stable Diffusion 1.5 Prompt Generator]" in GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE


def test_resolve_model_architecture_from_profile():
    """Проверяет приоритет явного поля 'architecture' из профиля модели."""
    prof_xl = {"name": "AnimeModel", "architecture": "sdxl"}
    assert resolve_model_architecture("AnimeModel", prof_xl) == "sdxl"

    prof_15 = {"name": "ClassicPhoto", "architecture": "sd15"}
    assert resolve_model_architecture("ClassicPhoto", prof_15) == "sd15"

    # Вариации наименования
    prof_pony = {"name": "PonyStyle", "architecture": "PonyXL"}
    assert resolve_model_architecture("PonyStyle", prof_pony) == "sdxl"


def test_resolve_model_architecture_filename_heuristic():
    """Проверяет эвристическое определение архитектуры для незарегистрированных моделей."""
    assert resolve_model_architecture("juggernautXL_v9.safetensors") == "sdxl"
    assert resolve_model_architecture("my_pony_diffusion_v6.safetensors") == "sdxl"
    assert resolve_model_architecture("illustrious_real_v1.safetensors") == "sdxl"
    assert resolve_model_architecture("cyberrealistic_v42_v1.5.safetensors") == "sd15"
    assert resolve_model_architecture("dreamshaper_8_sd15.safetensors") == "sd15"
    # Безопасный fallback
    assert resolve_model_architecture("completely_unknown_custom_model.safetensors") == "sd15"


def test_build_gallery_art_prompt_sdxl():
    """Проверяет генерацию SDXL системного промпта со строгими правилами прозы."""
    prompt = AssistantPromptBuilder.build_gallery_art_prompt(
        user_query="девушка у окна",
        target_model_id="juggernaut_xl",
        target_model_desc="photorealism",
        architecture="sdxl"
    )
    assert "[SYSTEM DIRECTIVE: SDXL Image Prompt Engineer]" in prompt
    assert "thigh-up portrait" in prompt
    assert "medium shot" in prompt
    assert "NO booru/SD1.5 tags" in prompt
    assert "hands on hips" in prompt
    assert '"suggested_model": "juggernaut_xl"' in prompt
    assert "девушка у окна" in prompt
    assert "cowboy shot" not in prompt.lower()


def test_build_gallery_art_prompt_sd15():
    """Проверяет генерацию SD 1.5 системного промпта с Danbooru тегами."""
    prompt = AssistantPromptBuilder.build_gallery_art_prompt(
        user_query="пейзаж заката",
        target_model_id="dreamshaper_8",
        target_model_desc="artistic",
        architecture="sd15"
    )
    assert "[SYSTEM DIRECTIVE: Stable Diffusion 1.5 Prompt Generator]" in prompt
    assert "12 to 26 comma-separated English keyword tags" in prompt
    assert "hand anchor" in prompt.lower()
    assert "hands on hips" in prompt
    assert '"suggested_model": "dreamshaper_8"' in prompt
    assert "пейзаж заката" in prompt
    assert "thigh-up shot" in prompt
    assert "cowboy shot" not in prompt.lower()


def test_no_cowboy_shot_in_any_directive():
    """Проверяет полную замену 'cowboy shot' на 'thigh-up shot' во всех шаблонах директив и ключевых словах."""
    from services.art_director.prompting import HUMAN_KEYWORDS

    all_templates = [
        GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE,
        GALLERY_SKILL_DIRECTIVE_SDXL_TEMPLATE,
        CINEMATIC_SKILL_DIRECTIVE_SD15_TEMPLATE,
        CINEMATIC_SKILL_DIRECTIVE_SDXL_TEMPLATE,
    ]
    for tmpl in all_templates:
        assert "cowboy shot" not in tmpl.lower()

    # В SD 1.5 директивах проверяем присутствие thigh-up shot
    assert "thigh-up shot" in GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE
    assert "thigh-up shot" in CINEMATIC_SKILL_DIRECTIVE_SD15_TEMPLATE
    assert "thigh-up shot" in HUMAN_KEYWORDS
    assert "cowboy shot" not in HUMAN_KEYWORDS


def test_model_router_dynamic_default():
    """Проверяет, что дефолтная модель берется динамически из активных профилей."""
    router = ModelRouter.get_instance()
    def_prof = router.get_default_profile()
    assert def_prof is not None
    assert "name" in def_prof
    assert def_prof.get("architecture") in ("sd15", "sdxl")
    # Убеждаемся, что ID дефолта не жестко зашит
    assert def_prof.get("active", True) is not False


def test_normalize_model_choice():
    """Проверяет отделение режима 'Авто' от ручного выбора."""
    assert normalize_model_choice("✨ Арт-директор (Авто)") is None
    assert normalize_model_choice("default") is None
    assert normalize_model_choice("auto") is None
    assert normalize_model_choice("None") is None
    assert normalize_model_choice("") is None
    assert normalize_model_choice(None) is None
    assert normalize_model_choice("aamXLAnimeMix_v10_fp8e4") == "aamXLAnimeMix_v10_fp8e4"


def test_get_gallery_directive_formatting():
    """Проверяет корректное форматирование плейсхолдеров без исключений KeyError."""
    d_sdxl = get_gallery_directive("sdxl", model_hint="XL Hint", target_model_id="xl_test")
    assert "XL Hint" in d_sdxl
    assert '"suggested_model": "xl_test"' in d_sdxl

    d_sd15 = get_gallery_directive("sd15", model_hint="15 Hint", target_model_id="15_test")
    assert "15 Hint" in d_sd15
    assert '"suggested_model": "15_test"' in d_sd15


def test_cinematic_directive_sd15_default():
    """Вызов без указания архитектуры по умолчанию возвращает правила SD 1.5."""
    directive = RoleplayPromptBuilder.get_cinematic_scene_directive("dialogue", "test_model")
    assert "CRITICAL FORMAT RULES (SD 1.5" in directive
    assert "keyword tags in `scene_prompt`" in directive
    assert "1girl/1boy" not in directive
    assert "[IMAGE_PROMPT: 1girl, solo, amber eyes" in directive


def test_cinematic_directive_sdxl():
    """Вызов с указанием архитектуры 'sdxl' возвращает правила SDXL."""
    directive = RoleplayPromptBuilder.get_cinematic_scene_directive("dialogue", "test_model", architecture="sdxl")
    assert "CRITICAL FORMAT RULES (SDXL" in directive
    assert "obsolete quality tags" in directive
    assert "[IMAGE_PROMPT: medium waist-up portrait" in directive
    assert "[IMAGE_PROMPT: 1girl, solo" not in directive
    assert "1girl/1boy" not in directive


def test_cinematic_directive_backward_compatibility():
    """Проверяет, что все типы сцен (dialogue, action, intimate, exploration) корректно компилируются без KeyError для обеих архитектур."""
    scenes = ["dialogue", "action", "intimate", "exploration"]
    for scene in scenes:
        d_sd15 = RoleplayPromptBuilder.get_cinematic_scene_directive(scene, "test_model", architecture="sd15")
        assert d_sd15 and len(d_sd15) > 0
        assert "1girl/1boy" not in d_sd15
        assert "--model test_model" in d_sd15

        d_sdxl = RoleplayPromptBuilder.get_cinematic_scene_directive(scene, "test_model", architecture="sdxl")
        assert d_sdxl and len(d_sdxl) > 0
        assert "1girl/1boy" not in d_sdxl
        assert "--model test_model" in d_sdxl


def test_roleplay_flow_ephemeral_art_prompt_architecture_dispatch(monkeypatch):
    """Проверяет сквозную диспетчеризацию архитектуры SD 1.5 / SDXL в RoleplayFlowControllerMixin."""
    from unittest.mock import MagicMock
    from services.roleplay_flow_controller import RoleplayFlowControllerMixin
    from services.rp_cast_manager import RPCastManager
    from services.tavern_parser import CharacterCard

    controller = RoleplayFlowControllerMixin.__new__(RoleplayFlowControllerMixin)
    controller.lm_client = MagicMock()
    controller.lm_client.chat_completion.return_value = "[IMAGE_PROMPT: cinematic test art]"
    controller.active_character = CharacterCard(name="Лира", description="", visual_dna="amber eyes")

    structured_cast = {
        "scene_type": "dialogue",
        "environment": {"location_dna": "ancient forest"},
        "companion_state": {"visual_dna": "amber eyes", "current_outfit": "traveler cloak"},
        "inventory": []
    }
    monkeypatch.setattr(RPCastManager, "load_cast", MagicMock(return_value=structured_cast))

    # 1. SDXL ветка
    controller._generate_ephemeral_rp_art_prompt("sess1", "Test speech", target_model_id="juggernautXL_v9.safetensors")
    assert controller.lm_client.chat_completion.called
    call_kwargs_xl = controller.lm_client.chat_completion.call_args[1]
    messages_xl = call_kwargs_xl["messages"]
    sys_xl = messages_xl[0]["content"]
    user_xl = messages_xl[1]["content"]

    assert "CRITICAL FORMAT RULES (SDXL" in sys_xl
    assert "1girl/1boy" not in sys_xl
    assert "Generate a concise descriptive visual prompt (2-3 sentences) for this RP scene:" in user_xl
    assert "Output ONLY the descriptive prompt inside [IMAGE_PROMPT: ...]" in user_xl
    assert "Do NOT write prose or paragraphs" not in user_xl

    # 2. SD15 ветка
    controller._generate_ephemeral_rp_art_prompt("sess1", "Test speech", target_model_id="dreamshaper_8_sd15.safetensors")
    call_kwargs_15 = controller.lm_client.chat_completion.call_args[1]
    messages_15 = call_kwargs_15["messages"]
    sys_15 = messages_15[0]["content"]
    user_15 = messages_15[1]["content"]

    assert "CRITICAL FORMAT RULES (SD 1.5" in sys_15
    assert "1girl/1boy" not in sys_15
    assert "Generate concise, comma-separated Danbooru tags for this RP scene:" in user_15
    assert "CRITICAL: Output ONLY comma-separated tags inside [IMAGE_PROMPT: ...]. Do NOT write prose or paragraphs." in user_15


def test_intimate_directive_has_no_weapons_or_armor():
    """Проверяет, что в интимной мизансцене в секции Example полностью отсутствуют оружие и доспехи."""
    banned_words = ["dagger", "sword", "weapon", "sheathed", "tunic", "armor", "backpack"]

    for arch in ("sdxl", "sd15"):
        directive = RoleplayPromptBuilder.get_cinematic_scene_directive("intimate", "test_model", architecture=arch)
        assert "Example:" in directive
        example_part = directive.split("Example:")[1].lower()
        for word in banned_words:
            assert word not in example_part, f"Banned word '{word}' found in intimate example ({arch})"


def test_action_directive_has_active_weapons():
    """Проверяет, что в боевой мизансцене в секции Example оружие активно обнажено (sword или blade)."""
    for arch in ("sdxl", "sd15"):
        directive = RoleplayPromptBuilder.get_cinematic_scene_directive("action", "test_model", architecture=arch)
        assert "Example:" in directive
        example_part = directive.split("Example:")[1].lower()
        assert "sword" in example_part or "blade" in example_part, f"No active weapon found in action example ({arch})"


def test_art_director_no_duplicate_location_injection(monkeypatch):
    """Проверяет, что заглушка локации предотвращает принудительную склейку location_dna с промптом."""
    from services.art_director import ArtDirector
    from services.rp_cast_manager import RPCastManager
    from unittest.mock import MagicMock

    director = ArtDirector()
    mock_lm = MagicMock()
    raw_prompt = "1girl, solo, amber eyes, gentle smile"

    fake_cast = {
        "environment": {"location_dna": "enchanted forest, mossy ground"},
        "companion_state": {"visual_dna": "amber eyes"}
    }
    monkeypatch.setattr(RPCastManager, "load_cast", MagicMock(return_value=fake_cast))

    plan = director.plan_generation(
        raw_prompt=raw_prompt,
        preferred_style="realistic",
        lm_client=mock_lm,
        available_models=[{"title": "test_model.safetensors", "model_name": "test_model"}],
        session_id="test_sess_no_loc",
        mode="roleplay"
    )
    assert "enchanted forest, mossy ground" not in plan.positive_prompt


def test_skill_router_cinematic_routing():
    """Проверяет корректность маршрутизации SkillRouter для скилла cinematic vs gallery."""
    router = SkillRouter.get_instance()

    # Запросы на кинематографические кадры должны направляться в cinematic
    cinematic_queries = [
        "Нарисуй кинокадр с детективом под дождём",
        "Сделай кинокадр битвы в космосе",
        "кинематографичный кадр неонового города 21:9",
        "cinematic movie still of an astronaut on mars, anamorphic lens",
    ]
    for q in cinematic_queries:
        routed_skill, conf, _ = router.route(q)
        assert routed_skill == "cinematic", f"Expected 'cinematic' for '{q}', got '{routed_skill}'"
        assert conf >= 0.7

    # Обычные портреты и арты без кино-триггеров должны оставаться в gallery
    gallery_queries = [
        "Нарисуй портрет девушки",
        "нарисуй девушку у окна",
        "нарисуй котика в саду",
    ]
    for q in gallery_queries:
        routed_skill, conf, _ = router.route(q)
        assert routed_skill == "gallery", f"Expected 'gallery' for '{q}', got '{routed_skill}'"


def test_art_director_widescreen_and_ultrawide_geometry():
    """Проверяет разрешения холста для 16:9 (cinematic) и 21:9 (ultrawide) в ArtDirector."""
    ad = ArtDirector()

    # 1. SDXL геометрия
    # 16:9 -> 1344x768
    p_cine_xl = ad.plan_generation("test prompt --ar cinematic", preferred_style="juggernautXL_v9.safetensors")
    assert (p_cine_xl.width, p_cine_xl.height) == (1344, 768)

    # 21:9 (--ar ultrawide) -> 1536x640
    p_uw_xl = ad.plan_generation("test prompt --ar ultrawide", preferred_style="juggernautXL_v9.safetensors")
    assert (p_uw_xl.width, p_uw_xl.height) == (1536, 640)

    # 21:9 (--ar 21:9) -> 1536x640
    p_21_9_xl = ad.plan_generation("test prompt --ar 21:9", preferred_style="juggernautXL_v9.safetensors")
    assert (p_21_9_xl.width, p_21_9_xl.height) == (1536, 640)

    # 2. SD 1.5 геометрия
    # 16:9 -> 768x448
    p_cine_15 = ad.plan_generation("test prompt --ar cinematic", preferred_style="dreamshaper_8_sd15.safetensors")
    assert (p_cine_15.width, p_cine_15.height) == (768, 448)

    # 21:9 (--ar ultrawide) -> 768x320
    p_uw_15 = ad.plan_generation("test prompt --ar ultrawide", preferred_style="dreamshaper_8_sd15.safetensors")
    assert (p_uw_15.width, p_uw_15.height) == (768, 320)

    # 21:9 (--ar 21:9) -> 768x320
    p_21_9_15 = ad.plan_generation("test prompt --ar 21:9", preferred_style="dreamshaper_8_sd15.safetensors")
    assert (p_21_9_15.width, p_21_9_15.height) == (768, 320)

    # 3. plan_from_visual_intent
    vi_cine = VisualIntent(prompt="epic movie shot", aspect_ratio="cinematic")
    assert (ad.plan_from_visual_intent(vi_cine, preferred_style="juggernautXL_v9.safetensors").width,
            ad.plan_from_visual_intent(vi_cine, preferred_style="juggernautXL_v9.safetensors").height) == (1344, 768)

    vi_uw = VisualIntent(prompt="epic panoramic shot", aspect_ratio="ultrawide")
    assert (ad.plan_from_visual_intent(vi_uw, preferred_style="juggernautXL_v9.safetensors").width,
            ad.plan_from_visual_intent(vi_uw, preferred_style="juggernautXL_v9.safetensors").height) == (1536, 640)


def test_cinematic_directives_compilation_and_sections():
    """Проверяет генерацию директив cinematic и структуру секций (Camera & Framing, Lighting & Atmosphere)."""
    assert IMAGE_GENERATION_SKILLS == frozenset({"gallery", "cinematic"})

    # SDXL Cinematic
    d_xl = get_cinematic_directive("sdxl", model_hint="Blade Runner style", target_model_id="xl_cyber")
    assert "[SYSTEM DIRECTIVE: SDXL Cinematic Frame Director]" in d_xl
    assert "### Core Rules" in d_xl
    assert "Widescreen Framing Ladder" in d_xl
    assert "Angle, Gaze, OTS & Light" in d_xl
    assert "Aspect Ratio, Exclusions & Self-Portrait" in d_xl
    assert "### Output JSON Schema" in d_xl
    assert "Blade Runner style" in d_xl
    assert '"suggested_model": "xl_cyber"' in d_xl
    assert '"aspect_ratio": "cinematic"' in d_xl

    # SD 1.5 Cinematic
    d_15 = get_cinematic_directive("sd15", model_hint="vintage 35mm film", target_model_id="sd15_vintage")
    assert "[SYSTEM DIRECTIVE: Stable Diffusion 1.5 Cinematic Frame Director]" in d_15
    assert "### Core Rules" in d_15
    assert "Widescreen Framing Ladder" in d_15
    assert "Angle, Gaze, OTS & Light" in d_15
    assert "vintage 35mm film" in d_15
    assert '"suggested_model": "sd15_vintage"' in d_15

    # get_art_directive полиморфная диспетчеризация
    assert "[SYSTEM DIRECTIVE: SDXL Cinematic Frame Director]" in get_art_directive("cinematic", "sdxl")
    assert "[SYSTEM DIRECTIVE: SDXL Image Prompt Engineer]" in get_art_directive("gallery", "sdxl")
    assert "[SYSTEM DIRECTIVE: Stable Diffusion 1.5 Cinematic Frame Director]" in get_art_directive("cinematic", "sd15")
    assert "[SYSTEM DIRECTIVE: Stable Diffusion 1.5 Prompt Generator]" in get_art_directive("gallery", "sd15")

    # AssistantPromptBuilder.build_gallery_art_prompt для skill_name="cinematic"
    p_xl = AssistantPromptBuilder.build_gallery_art_prompt(
        user_query="киберпанк улица ночью",
        target_model_id="xl_film",
        target_model_desc="neo-noir",
        architecture="sdxl",
        skill_name="cinematic"
    )
    assert "[SYSTEM DIRECTIVE: SDXL Cinematic Frame Director]" in p_xl
    assert '"suggested_model": "xl_film"' in p_xl
    assert "киберпанк улица ночью" in p_xl


def test_sampling_and_context_art_skills():
    """Проверяет ADR-19/ADR-20: temperature=0.4, enable_thinking=False, include_memory=False для gallery и cinematic."""
    from mobile_server.services.chat_service import build_chat_context_and_params

    # 1. resolve_effective_thinking
    assert resolve_effective_thinking("assistant", model_name="qwen3.5-35b", active_skill="cinematic") is False
    assert resolve_effective_thinking("assistant", model_name="qwen3.5-35b", active_skill="gallery") is False

    # 2. mobile_server build_chat_context_and_params для cinematic
    sess_cine = {"id": "test_cine", "mode": "assistant", "chat_history": []}
    msgs_cine, params_cine, _, prof_cine, _ = build_chat_context_and_params(
        session=sess_cine,
        session_id="test_cine",
        user_text="Нарисуй кинокадр с детективом под дождём",
        active_model_name="qwen3.5-35b",
    )
    assert prof_cine.get("active_assistant_skill") == "cinematic"
    assert params_cine["temperature"] == 0.4
    assert params_cine["enable_thinking"] is False
    sys_content_cine = msgs_cine[0]["content"] if msgs_cine else ""
    assert "[ДОЛГОСРОЧНАЯ ПАМЯТЬ" not in sys_content_cine

    # 3. mobile_server build_chat_context_and_params для gallery
    sess_gal = {"id": "test_gal", "mode": "assistant", "chat_history": []}
    msgs_gal, params_gal, _, prof_gal, _ = build_chat_context_and_params(
        session=sess_gal,
        session_id="test_gal",
        user_text="Нарисуй портрет девушки",
        active_model_name="qwen3.5-35b",
    )
    assert prof_gal.get("active_assistant_skill") == "gallery"
    assert params_gal["temperature"] == 0.4
    assert params_gal["enable_thinking"] is False
    sys_content_gal = msgs_gal[0]["content"] if msgs_gal else ""
    assert "[ДОЛГОСРОЧНАЯ ПАМЯТЬ" not in sys_content_gal


def test_extract_last_image_prompt():
    """Проверяет извлечение предыдущего арт-промпта из истории/сообщений по всем SSOT-каналам."""
    from services.assistant_flow import extract_last_image_prompt

    # 1. Из direct image_prompt в сообщении
    msgs_1 = [
        {"role": "user", "content": "Нарисуй арт"},
        {"role": "assistant", "content": "Готово!", "image_prompt": "masterpiece, 1girl, red hair"},
    ]
    assert extract_last_image_prompt(msgs_1) == "masterpiece, 1girl, red hair"

    # 2. Из тега [IMAGE_PROMPT: ...] внутри текста
    msgs_2 = [
        {"role": "user", "content": "Нарисуй арт"},
        {"role": "assistant", "content": "Вот твой кадр:\n\n[IMAGE_PROMPT: 1boy, cyber jacket --ar wide]"},
    ]
    assert extract_last_image_prompt(msgs_2) == "1boy, cyber jacket --ar wide"

    # 3. Из generation_params
    msgs_3 = [
        {"role": "user", "content": "Нарисуй арт"},
        {"role": "assistant", "content": "", "generation_params": {"positive_prompt": "cyberpunk city, neon"}},
    ]
    assert extract_last_image_prompt(msgs_3) == "cyberpunk city, neon"

    # 4. Фолбэк на visual_intent
    msgs_4 = [
        {"role": "user", "content": "Нарисуй арт"},
        {"role": "assistant", "content": "", "visual_intent": {"prompt": "retro futuristic car"}},
    ]
    assert extract_last_image_prompt(msgs_4) == "retro futuristic car"

    # 5. Извлечение негативного промпта через visual_intent (user_neg)
    msgs_5 = [
        {"role": "user", "content": "Нарисуй двух девушек"},
        {"role": "assistant", "content": "", "visual_intent": {"prompt": "two women, kissing", "user_neg": "solo, clothing, cartoon"}},
    ]
    assert extract_last_image_prompt(msgs_5) == "two women, kissing --neg solo, clothing, cartoon"

    # 6. Извлечение негативного промпта из generation_params (fallback)
    msgs_6 = [
        {"role": "user", "content": "Нарисуй пейзаж"},
        {"role": "assistant", "content": "Готово!", "image_prompt": "sunset mountains", "generation_params": {"positive_prompt": "sunset mountains", "negative_prompt": "fog, haze"}},
    ]
    assert extract_last_image_prompt(msgs_6) == "sunset mountains --neg fog, haze"

    # 7. Пустая история или отсутствие ассистента
    assert extract_last_image_prompt([]) is None
    assert extract_last_image_prompt([{"role": "user", "content": "Привет"}]) is None


def test_session_reconstruct_chat_history_from_messages():
    """Проверяет восстановление chat_history из session['messages'] с сохранением [IMAGE_PROMPT: ...]."""
    from services.session_manager import SessionManager

    messages = [
        {"role": "user", "text": "Нарисуй кота", "is_user": True},
        {
            "role": "assistant",
            "text": "",
            "is_user": False,
            "image_prompt": "cute cat, green eyes, highly detailed",
            "image_path": "outputs/cat.png"
        },
        {"role": "user", "text": "Теперь сделай его спящим", "is_user": True},
    ]

    reconstructed = SessionManager.reconstruct_chat_history_from_messages(messages)
    assert len(reconstructed) == 3
    assert reconstructed[0] == {"role": "user", "content": "Нарисуй кота"}
    assert reconstructed[1]["role"] == "assistant"
    assert "[IMAGE_PROMPT: cute cat, green eyes, highly detailed]" in reconstructed[1]["content"]
    assert reconstructed[2] == {"role": "user", "content": "Теперь сделай его спящим"}


def test_context_window_builder_preserves_art_messages():
    """Проверяет, что ContextWindowBuilder не выкидывает пустые ответы ассистента с арт-метаданными и не склеивает ходы пользователя."""
    from services.context_window_builder import ContextWindowBuilder

    # История с пустым ответом ассистента, но с image_prompt
    history = [
        {"role": "user", "content": "Нарисуй девушку"},
        {
            "role": "assistant",
            "content": "",
            "image_prompt": "1girl, solo, smiling, sunset",
        },
        {"role": "user", "content": "То же самое, но ночью"},
    ]

    norm = ContextWindowBuilder.prepare_history_for_llm(history)
    assert len(norm) == 3
    assert norm[0]["role"] == "user"
    assert norm[1]["role"] == "assistant"
    assert "[IMAGE_PROMPT: 1girl, solo, smiling, sunset]" in norm[1]["content"]
    assert norm[2]["role"] == "user"

    # Проверяем сквозную сборку сообщений через normalize_role_alternation
    alternated = ContextWindowBuilder.normalize_role_alternation(norm)
    assert len(alternated) == 3
    assert alternated[0]["role"] == "user"
    assert alternated[1]["role"] == "assistant"
    assert alternated[2]["role"] == "user"


def test_build_gallery_art_plan_prepends_previous_image_prompt():
    """Проверяет передачу previous_image_prompt в Phase 2 Gallery Art Pass."""
    from unittest.mock import MagicMock
    from services.assistant_flow import build_gallery_art_plan

    mock_client = MagicMock()
    mock_client.chat_completion.return_value = "[IMAGE_PROMPT: 1girl, night, street lights]"

    mock_ad = MagicMock()
    mock_ad.plan_from_visual_intent.return_value = MagicMock(positive_prompt="1girl, night, street lights")

    session = {
        "chat_history": [
            {"role": "user", "content": "Нарисуй девушку"},
            {"role": "assistant", "content": "[IMAGE_PROMPT: 1girl, day, sunny park]"},
        ]
    }

    plan = build_gallery_art_plan(
        user_query="то же самое, но ночью",
        lm_client=mock_client,
        art_director=mock_ad,
        session=session,
    )

    # Проверяем вызов lm_client.chat_completion: должно быть 2 сообщения (assistant с прошлым промптом + user)
    mock_client.chat_completion.assert_called_once()
    call_kwargs = mock_client.chat_completion.call_args[1]
    sent_messages = call_kwargs["messages"]
    assert len(sent_messages) == 2
    assert sent_messages[0]["role"] == "assistant"
    assert sent_messages[0]["content"] == "[IMAGE_PROMPT: 1girl, day, sunny park]"
    assert sent_messages[1]["role"] == "user"
    assert "то же самое, но ночью" in sent_messages[1]["content"]
    assert plan.positive_prompt == "1girl, night, street lights"


def test_build_gallery_art_plan_prepends_previous_image_prompt_with_negative():
    """Проверяет передачу previous_image_prompt с флагом --neg в следующий ход генерации."""
    from unittest.mock import MagicMock
    from services.assistant_flow import build_gallery_art_plan

    mock_client = MagicMock()
    mock_client.chat_completion.return_value = "[IMAGE_PROMPT: 2girls, night --neg solo, single girl]"

    mock_ad = MagicMock()
    mock_ad.plan_from_visual_intent.return_value = MagicMock(positive_prompt="2girls, night", negative_prompt="solo, single girl")

    session = {
        "messages": [
            {"role": "user", "content": "Нарисуй двух девушек"},
            {
                "role": "assistant",
                "content": "Вот кадр:\n\n[IMAGE_PROMPT: 2girls, sunny park --neg solo, single girl]",
                "visual_intent": {"prompt": "2girls, sunny park", "user_neg": "solo, single girl"},
            },
        ]
    }

    plan = build_gallery_art_plan(
        user_query="сделай их ночью",
        lm_client=mock_client,
        art_director=mock_ad,
        session=session,
    )

    mock_client.chat_completion.assert_called_once()
    call_kwargs = mock_client.chat_completion.call_args[1]
    sent_messages = call_kwargs["messages"]
    assert len(sent_messages) == 2
    assert sent_messages[0]["role"] == "assistant"
    assert sent_messages[0]["content"] == "[IMAGE_PROMPT: 2girls, sunny park --neg solo, single girl]"
    assert sent_messages[1]["role"] == "user"
    assert "сделай их ночью" in sent_messages[1]["content"]


def test_prompt_dialect_sd15_always_tags():
    """Любая модель с архитектурой sd15 возвращает 'tags' независимо от routing_description."""
    assert resolve_prompt_dialect("sd15") == "tags"
    assert resolve_prompt_dialect("sd15", "") == "tags"
    assert resolve_prompt_dialect("sd15", "Realistic photo, 35mm lens, natural lighting") == "tags"
    assert resolve_prompt_dialect("1.5", "Photorealistic cinematic frame") == "tags"
    assert resolve_prompt_dialect("SD15", "Avoid all booru tags") == "tags"


def test_prompt_dialect_sdxl_danbooru_tags():
    """SDXL-модели с позитивным указанием booru/danbooru тегов (например WAI-Nsfw-Illustrious-17) возвращают 'tags'."""
    illustrious_desc = (
        "### Prompt using dense Danbooru tags (1girl, character/series tags, pose, angle, lighting) "
        "with quality boosters: masterpiece, best quality, very aesthetic, absurdres. "
        "Strictly avoid photo, realistic, 3d, 35mm, sketch."
    )
    assert resolve_prompt_dialect("sdxl", illustrious_desc) == "tags"

    # Варианты с booru, e621, 1boy
    assert resolve_prompt_dialect("sdxl", "Anime art based on e621 tags and clean lineart") == "tags"
    assert resolve_prompt_dialect("sdxl", "Stylized anime 1boy solo booru tags") == "tags"


def test_prompt_dialect_sdxl_avoid_booru_tags_returns_phrases():
    """SDXL-модели с фразами 'Avoid booru tags' (например Juggernaut_XI) не должны ложно определяться как tags."""
    juggernaut_desc = (
        "### Prompt as cinematic editorial photograph using concise comma-separated descriptive phrases, "
        "explicit shot framing, detailed environment background, natural directional lighting, "
        "authentic skin and material textures, shallow depth of field. "
        "Avoid anime, 3d render, painting, booru tags, numeric weights, and buzzwords like masterpiece or 8k."
    )
    assert resolve_prompt_dialect("sdxl", juggernaut_desc) == "phrases"

    # Вариант со strictly avoid
    strictly_avoid_desc = "Photorealistic 8k portrait. Strictly avoid booru tags, 1girl, anime."
    assert resolve_prompt_dialect("sdxl", strictly_avoid_desc) == "phrases"

    # Вариант с never use
    never_use_desc = "Never use booru tags or 1girl. Use natural descriptive sentences."
    assert resolve_prompt_dialect("sdxl", never_use_desc) == "phrases"

    # Варианты с 'Do not use' и русскими запретами 'не используй'
    assert resolve_prompt_dialect("sdxl", "Do not use booru tags. High quality portrait.") == "phrases"
    assert resolve_prompt_dialect("sdxl", "Don't use booru tags, write natural phrases.") == "phrases"
    assert resolve_prompt_dialect("sdxl", "Не используй danbooru. Красивое фото природы.") == "phrases"
    assert resolve_prompt_dialect("sdxl", "Реалистичный стиль; не использовать danbooru теги!") == "phrases"

    # Чистое описание фотореализма без упоминания booru
    photo_desc = "35mm film photograph, raw authentic skin texture, natural daylight."
    assert resolve_prompt_dialect("sdxl", photo_desc) == "phrases"


def test_prompt_dialect_sdxl_positive_tags_with_prohibitions():
    """Проверяет, что маркеры danbooru в позитивных фрагментах определяются как tags, даже если есть другие запреты."""
    assert resolve_prompt_dialect("sdxl", "Anime style, no realistic skin, using Danbooru tags") == "tags"
    assert resolve_prompt_dialect("sdxl", "Anime style, no realistic skin, using Danbooru tags. Avoid 3D.") == "tags"
    assert resolve_prompt_dialect("sdxl", "Не используй реализм. Чистый Danbooru стиль, 1girl anime.") == "tags"


def test_get_art_directive_dialect_dispatch():
    """Проверяет диспетчеризацию шаблонов в get_art_directive на основе диалекта."""
    illustrious_desc = "Dense Danbooru anime tags, 1girl. Strictly avoid photo."
    juggernaut_desc = "Cinematic photograph. Avoid booru tags."

    # Illustrious (SDXL arch, но dialect == 'tags') -> получает SD15 теговый шаблон
    art_dir_tags = get_art_directive("gallery", "sdxl", model_hint=illustrious_desc)
    assert "[SYSTEM DIRECTIVE: Stable Diffusion 1.5 Prompt Generator]" in art_dir_tags
    assert "STYLE GUIDANCE" in art_dir_tags

    # Juggernaut (SDXL arch, dialect == 'phrases') -> получает SDXL фразовый шаблон
    art_dir_phrases = get_art_directive("gallery", "sdxl", model_hint=juggernaut_desc)
    assert "[SYSTEM DIRECTIVE: SDXL Image Prompt Engineer]" in art_dir_phrases
    assert "STYLE GUIDANCE" in art_dir_phrases


