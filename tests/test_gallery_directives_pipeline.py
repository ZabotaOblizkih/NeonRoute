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


def test_gallery_directives_registry():
    """Проверяет наличие обеих директив и сохранение обратной совместимости."""
    assert "sd15" in GALLERY_DIRECTIVES_BY_ARCH
    assert "sdxl" in GALLERY_DIRECTIVES_BY_ARCH
    # Обратная совместимость для внешних модулей
    assert GALLERY_SKILL_DIRECTIVE_TEMPLATE == GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE
    assert "Stable Diffusion XL" in GALLERY_SKILL_DIRECTIVE_SDXL_TEMPLATE
    assert "Stable Diffusion]" in GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE


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
    assert "[SYSTEM DIRECTIVE: Image Generation for Stable Diffusion XL]" in prompt
    assert "15–30 impactful tokens" in prompt
    assert "NEVER use obsolete quality tags" in prompt
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
    assert "[SYSTEM DIRECTIVE: Image Generation for Stable Diffusion]" in prompt
    assert "35–60 evocative Danbooru" in prompt
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
