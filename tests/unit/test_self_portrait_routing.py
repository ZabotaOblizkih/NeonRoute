"""
Unit and integration tests for Elaria self-portrait (--self) detection,
unified [IMAGE_PROMPT] flag parsing, and elimination of 'себя' false positives.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from services.art_director import (
    ArtDirector,
    ELARIA_VISUAL_DNA,
    TargetSubject,
    VisualIntent,
    detect_elaria_self_portrait_intent,
    parse_image_prompt_flags,
)
from services.assistant_prompt_builder import (
    AssistantPromptBuilder,
    GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE,
    GALLERY_SKILL_DIRECTIVE_SDXL_TEMPLATE,
)
from ui.controllers.assistant_flow_controller import AssistantFlowControllerMixin


def test_parse_image_prompt_flags_extracts_all_flags_in_any_order():
    raw = (
        "1girl, cozy sweater, warm monitor glow "
        "--neg glasses, hat --self --ar square --model realistic"
    )
    parsed = parse_image_prompt_flags(raw)

    assert parsed.prompt == "1girl, cozy sweater, warm monitor glow"
    assert parsed.negative == "glasses, hat"
    assert parsed.is_self_portrait is True
    assert parsed.aspect_ratio == "square"
    assert parsed.model_style == "realistic"
    assert "--self" not in parsed.prompt


def test_parse_image_prompt_flags_subject_elaria_and_hands():
    raw = "1girl, holding teacup --subject elaria --hands --ar portrait"
    parsed = parse_image_prompt_flags(raw)

    assert parsed.is_self_portrait is True
    assert parsed.aspect_ratio == "portrait"
    assert "--subject" not in parsed.prompt
    assert "--hands" in parsed.prompt


def test_detect_self_portrait_prevents_reflexive_sebya_false_positives():
    false_positive_queries = [
        "Девушка держит себя в руках",
        "Нарисуй девушку, которая держит себя в руках",
        "Элария, нарисуй девушку, которая держит себя в руках",
        "Парень смотрит на себя в зеркало",
        "Кот моет себя лапой на подоконнике",
        "Нарисуй автопортрет Ван Гога маслом",
        "Представь себе средневековый замок на скале",
        "Покажи картинку: спортсменка чувствует себя уверенно",
    ]
    for query in false_positive_queries:
        assert detect_elaria_self_portrait_intent(
            user_query=query,
            raw_prompt="1girl, calm woman, indoor lighting",
            has_self_flag=False,
        ) is False, f"Ложное срабатывание на запросе: {query}"


def test_detect_self_portrait_positive_cases():
    # 1. Приоритет флага --self при произвольной формулировке
    assert detect_elaria_self_portrait_intent(
        user_query="Покажи, какая ты в вечернем платье",
        raw_prompt="1girl, evening gown",
        has_self_flag=True,
    ) is True

    # 2. Фоллбэк при отсутствии --self на прямых командах самопортрета
    positive_queries = [
        "Нарисуй себя",
        "Элария, нарисуй себя у окна",
        "Элария, нарисуй мне саму себя в киберпанке",
        "Можешь нарисовать себя в кафе?",
        "Сделай селфи",
        "Покажи, как ты выглядишь",
        "Нарисуй Эларию",
        "Покажи твой портрет",
    ]
    for query in positive_queries:
        assert detect_elaria_self_portrait_intent(
            user_query=query,
            raw_prompt="1girl, solo, studio lighting",
            has_self_flag=False,
        ) is True, f"Не распознан автопортрет для запроса: {query}"


def test_dispatch_art_to_forge_ignores_sebya_for_third_party_girl():
    ctrl = AssistantFlowControllerMixin.__new__(AssistantFlowControllerMixin)
    ctrl.selected_forge_checkpoint = None
    ctrl._current_user_request = "Нарисуй девушку, которая держит себя в руках"
    ctrl.chat_history = []
    ctrl.current_session = {"id": "sess_test"}
    ctrl._last_art_meta = {}
    ctrl.pill_forge = SimpleNamespace(is_online=True)

    captured = {}

    def _capture_scene_art(**kwargs):
        captured.update(kwargs)

    ctrl._on_scene_art_requested = _capture_scene_art

    widget = MagicMock()
    ctrl._dispatch_art_to_forge(
        "1girl, composed woman, hands clasped, calm expression --ar portrait --model realistic",
        widget,
        "Нарисуй девушку, которая держит себя в руках",
    )

    intent = captured["visual_intent"]
    assert intent.target_subject == TargetSubject.SCENE_OBJECT
    assert captured["character_name"] is None

    director = ArtDirector()
    plan = director.plan_from_visual_intent(
        visual_intent=intent,
        preferred_style="realistic",
        character_name=captured["character_name"],
        mode="assistant",
    )
    assert ELARIA_VISUAL_DNA not in plan.positive_prompt
    assert "platinum silver bob haircut" not in plan.positive_prompt.lower()


def test_dispatch_art_to_forge_injects_elaria_dna_only_on_self_flag():
    ctrl = AssistantFlowControllerMixin.__new__(AssistantFlowControllerMixin)
    ctrl.selected_forge_checkpoint = None
    ctrl._current_user_request = "Покажи, какая ты в уютной студии"
    ctrl.chat_history = []
    ctrl.current_session = {"id": "sess_test"}
    ctrl._last_art_meta = {}
    ctrl.pill_forge = SimpleNamespace(is_online=True)

    captured = {}
    ctrl._on_scene_art_requested = lambda **kwargs: captured.update(kwargs)

    widget = MagicMock()
    ctrl._dispatch_art_to_forge(
        "1girl, solo, black turtleneck, warm monitor glow --self --ar square --model realistic",
        widget,
        "Покажи, какая ты в уютной студии",
    )

    intent = captured["visual_intent"]
    assert intent.target_subject == TargetSubject.ELARIA
    assert captured["character_name"] == "Элария"
    assert "--self" not in intent.prompt

    director = ArtDirector()
    plan = director.plan_from_visual_intent(
        visual_intent=intent,
        preferred_style="realistic",
        character_name=captured["character_name"],
        mode="assistant",
    )
    assert ELARIA_VISUAL_DNA in plan.positive_prompt
    assert "--self" not in plan.positive_prompt


def test_gallery_directives_preserve_structure_and_include_self_rule():
    assert "[SYSTEM DIRECTIVE: Stable Diffusion 1.5 Prompt Generator]" in GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE
    assert "[SYSTEM DIRECTIVE: SDXL Image Tag Translator]" in GALLERY_SKILL_DIRECTIVE_SDXL_TEMPLATE
    assert "is_self_portrait" in GALLERY_SKILL_DIRECTIVE_SD15_TEMPLATE
    assert "is_self_portrait" in GALLERY_SKILL_DIRECTIVE_SDXL_TEMPLATE

    built_sdxl = AssistantPromptBuilder.build_gallery_art_prompt(
        user_query="нарисуй себя",
        target_model_id="juggernaut_xl",
        architecture="sdxl",
    )
    assert "is_self_portrait" in built_sdxl
    assert '"suggested_model": "juggernaut_xl"' in built_sdxl
