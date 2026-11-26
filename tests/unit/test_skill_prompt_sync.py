from unittest.mock import MagicMock
import pytest

from services.skill_router import SkillRouter, ASSISTANT_SKILLS
from services.assistant_prompt_builder import AssistantPromptBuilder, SKILL_PROMPTS


def test_skill_router_syncs_builtin_prompts_with_assistant_prompt_builder():
    """
    Проверяет, что SkillRouter.get_all_skills() для всех встроенных скиллов
    синхронизирован с реальными промптами из AssistantPromptBuilder (SSOT),
    а не содержит устаревших однострочных заглушек.
    """
    router = SkillRouter(embedder=None)
    all_skills = router.get_all_skills()

    for skill_id in ASSISTANT_SKILLS:
        assert skill_id in all_skills
        synced_prompt = all_skills[skill_id].get("system_prompt", "")
        assert synced_prompt, f"Скилл '{skill_id}' не получил синхронизированный промпт"

        if skill_id == "gallery":
            assert "[SYSTEM DIRECTIVE:" in synced_prompt
            built_prompt = AssistantPromptBuilder.get_instance().build(
                user_name="Виктор",
                active_assistant_skill="gallery",
                include_memory=False,
                static_core_only=False
            )
            primary_effective = synced_prompt.split("\n\n---")[0]
            assert primary_effective in built_prompt
        elif skill_id in SKILL_PROMPTS:
            # Реальный боевой шаблон подставляется в LLM
            built_prompt = AssistantPromptBuilder.get_instance().build(
                user_name="Виктор",
                active_assistant_skill=skill_id,
                include_memory=False,
                static_core_only=False
            )
            effective_prompt = AssistantPromptBuilder.get_effective_skill_prompt(skill_id, user_name="Виктор")
            assert effective_prompt in built_prompt
            assert "[АКТИВНЫЙ СКИЛЛ:" in synced_prompt


def test_gallery_effective_prompt_reflects_selected_checkpoint():
    """Проверяет, что для скилла gallery отображается реальная боевая директива под архитектуру."""
    effective_sdxl = AssistantPromptBuilder.get_effective_skill_prompt(
        "gallery",
        selected_forge_checkpoint="ponyDiffusionV6XL.safetensors"
    )
    assert "[SYSTEM DIRECTIVE: SDXL Image Tag Translator]" in effective_sdxl
    assert "ponyDiffusionV6XL.safetensors" in effective_sdxl

    effective_auto = AssistantPromptBuilder.get_effective_skill_prompt(
        "gallery",
        selected_forge_checkpoint=""
    )
    assert "[SYSTEM DIRECTIVE:" in effective_auto
    assert "Альтернативный шаблон" in effective_auto
