"""
Tests for end-to-end synchronization of header Forge model selection
('Арт-директор (Авто)' vs manual checkpoint) and preservation of
routing_description ('Описание стиля') even when llm_guidance is set.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from services.assistant_prompt_builder import AssistantPromptBuilder
from services.model_router import ModelRouter
from ui.controllers.assistant_flow_controller import (
    AssistantFlowControllerMixin,
    run_assistant_inference_deferred,
)


def _make_profiles():
    return [
        {
            "id": "sd15_anime",
            "name": "MeinaAnimeSD15",
            "architecture": "sd15",
            "checkpoint": "meina_v5.safetensors",
            "routing_description": "Prompt using dense Danbooru anime tags, cel shaded.",
            "llm_guidance": "anime-background-style-v2",
            "style_triggers": ["аниме"],
            "is_default": False,
            "active": True,
        },
        {
            "id": "sdxl_fantasy",
            "name": "DreamShaperXL",
            "architecture": "sdxl",
            "checkpoint": "dreamshaperXL.safetensors",
            "routing_description": "Prompt as cinematic dark fantasy art, tactile fabric textures.",
            "llm_guidance": "fantasy-ti-token",
            "style_triggers": ["фэнтези"],
            "is_default": True,
            "active": True,
        },
    ]


def test_auto_mode_preserves_routing_description_when_llm_guidance_present(monkeypatch):
    profiles = _make_profiles()
    router = ModelRouter.get_instance()
    monkeypatch.setattr(router, "profiles", profiles)
    monkeypatch.setattr(router, "match_prompt", lambda q: ("sd15_anime", 0.95))

    built = AssistantPromptBuilder.get_instance().build(
        user_name="Тест",
        user_query="Нарисуй аниме девушку",
        selected_forge_checkpoint="✨ Арт-директор (Авто)",
        include_memory=False,
        static_core_only=False,
    )

    assert "[SYSTEM DIRECTIVE: Stable Diffusion 1.5 Prompt Generator]" in built
    assert "Prompt using dense Danbooru anime tags, cel shaded." in built
    assert "--model sd15_anime" in built
    assert "anime-background-style-v2" not in built


def test_manual_header_selection_propagates_through_deferred_rag_to_llm(monkeypatch):
    profiles = _make_profiles()
    router = ModelRouter.get_instance()
    monkeypatch.setattr(router, "profiles", profiles)
    # Даже если авто-роутер отдал бы SD 1.5 аниме, ручной выбор должен победить
    monkeypatch.setattr(router, "match_prompt", lambda q: ("sd15_anime", 0.95))

    monkeypatch.setattr(
        "services.knowledge_manager.KnowledgeManager.find_relevant_documents",
        staticmethod(lambda *a, **kw: []),
    )

    ctrl = AssistantFlowControllerMixin.__new__(AssistantFlowControllerMixin)
    ctrl.selected_forge_checkpoint = "DreamShaperXL"
    ctrl.chat_history = []
    ctrl.current_session = {"id": "sess_manual", "metadata": {}}
    ctrl._active_tasks = {}
    ctrl.task_queue = MagicMock()
    ctrl.lm_client = MagicMock()
    ctrl.lm_client.chat_completion.return_value = "[IMAGE_PROMPT: fantasy castle --ar wide --model sdxl_fantasy]"
    ctrl.lm_combo = None
    ctrl.active_lms_model = "test-llm"
    ctrl._current_user_request = "Нарисуй аниме пейзаж"
    ctrl.app_config = {}
    ctrl.system_monitor = None

    ctrl._enqueue_assistant_llm_task()
    enqueued_task = ctrl.task_queue.enqueue.call_args[0][0]
    assert enqueued_task.kwargs["selected_forge_checkpoint"] == "DreamShaperXL"

    run_assistant_inference_deferred(**enqueued_task.kwargs)

    call_kwargs = ctrl.lm_client.chat_completion.call_args[1]
    assert "selected_forge_checkpoint" not in call_kwargs
    turn_msg = call_kwargs["messages"][-1]["content"]

    assert "[SYSTEM DIRECTIVE: SDXL Image Tag Translator]" in turn_msg
    assert "Prompt as cinematic dark fantasy art, tactile fabric textures." in turn_msg
    assert "--model sdxl_fantasy" in turn_msg


def test_enqueue_gallery_art_pass_respects_manual_and_auto_style_description(monkeypatch):
    profiles = _make_profiles()
    router = ModelRouter.get_instance()
    monkeypatch.setattr(router, "profiles", profiles)
    monkeypatch.setattr(router, "match_prompt", lambda q: ("sd15_anime", 0.95))

    ctrl = AssistantFlowControllerMixin.__new__(AssistantFlowControllerMixin)
    ctrl.lm_combo = None
    ctrl.current_session = {"id": "sess_pass"}
    ctrl.app_config = {}
    ctrl._active_tasks = {}
    ctrl.task_queue = MagicMock()
    ctrl.lm_client = MagicMock()

    # 1. Авто-режим -> выбирает sd15_anime и НЕ затирает routing_description тегом anime-background-style-v2
    ctrl.selected_forge_checkpoint = "✨ Арт-директор (Авто)"
    ctrl._enqueue_gallery_art_pass("Нарисуй аниме город")
    auto_task = ctrl.task_queue.enqueue.call_args[0][0]
    auto_prompt = auto_task.kwargs["messages"][0]["content"]
    assert "Prompt using dense Danbooru anime tags, cel shaded." in auto_prompt
    assert "--model sd15_anime" in auto_prompt

    # 2. Ручной выбор -> выбирает DreamShaperXL (SDXL) и его routing_description
    ctrl.selected_forge_checkpoint = "DreamShaperXL"
    ctrl._enqueue_gallery_art_pass("Нарисуй аниме город")
    manual_task = ctrl.task_queue.enqueue.call_args[0][0]
    manual_prompt = manual_task.kwargs["messages"][0]["content"]
    assert "[SYSTEM DIRECTIVE: SDXL Image Tag Translator]" in manual_prompt
    assert "Prompt as cinematic dark fantasy art, tactile fabric textures." in manual_prompt
    assert "--model sdxl_fantasy" in manual_prompt
