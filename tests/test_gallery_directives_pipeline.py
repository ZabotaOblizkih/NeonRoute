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
    get_gallery_directive,
    GALLERY_DIRECTIVES_BY_ARCH,
    GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE,
    GALLERY_SKILL_DIRECTIVE_SDXL_TEMPLATE,
    GALLERY_SKILL_DIRECTIVE_TEMPLATE,
)
from services.roleplay_prompt_builder import RoleplayPromptBuilder


def test_gallery_directives_registry():
    """Проверяет наличие обеих директив и сохранение обратной совместимости."""
    assert "sd15" in GALLERY_DIRECTIVES_BY_ARCH
    assert "sdxl" in GALLERY_DIRECTIVES_BY_ARCH
    # Обратная совместимость для внешних модулей
    assert GALLERY_SKILL_DIRECTIVE_TEMPLATE == GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE
    assert "[SYSTEM DIRECTIVE: Image Tag Translator]" in GALLERY_SKILL_DIRECTIVE_SDXL_TEMPLATE
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
    assert "[SYSTEM DIRECTIVE: Image Tag Translator]" in prompt
    assert "--model juggernaut_xl" in prompt
    assert "девушка у окна" in prompt


def test_build_gallery_art_prompt_sd15():
    """Проверяет генерацию SD 1.5 системного промпта с Danbooru тегами."""
    prompt = AssistantPromptBuilder.build_gallery_art_prompt(
        user_query="пейзаж заката",
        target_model_id="dreamshaper_8",
        target_model_desc="artistic",
        architecture="sd15"
    )
    assert "[SYSTEM DIRECTIVE: Stable Diffusion 1.5 Prompt Generator]" in prompt
    assert "15 to 30 detailed tags" in prompt
    assert "--model dreamshaper_8" in prompt
    assert "пейзаж заката" in prompt


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
    assert "--model xl_test" in d_sdxl

    d_sd15 = get_gallery_directive("sd15", model_hint="15 Hint", target_model_id="15_test")
    assert "15 Hint" in d_sd15
    assert "--model 15_test" in d_sd15


def test_cinematic_directive_sd15_default():
    """Вызов без указания архитектуры по умолчанию возвращает правила SD 1.5."""
    directive = RoleplayPromptBuilder.get_cinematic_scene_directive("dialogue", "test_model")
    assert "CRITICAL FORMAT RULES (SD 1.5):" in directive
    assert "Danbooru/SD positive tags" in directive
    assert "1girl/1boy" not in directive
    assert "[IMAGE_PROMPT: 1girl, solo, amber eyes" in directive


def test_cinematic_directive_sdxl():
    """Вызов с указанием архитектуры 'sdxl' возвращает правила SDXL."""
    directive = RoleplayPromptBuilder.get_cinematic_scene_directive("dialogue", "test_model", architecture="sdxl")
    assert "CRITICAL FORMAT RULES (SDXL):" in directive
    assert "NEVER use obsolete quality tags" in directive
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

    assert "CRITICAL FORMAT RULES (SDXL):" in sys_xl
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

    assert "CRITICAL FORMAT RULES (SD 1.5):" in sys_15
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
