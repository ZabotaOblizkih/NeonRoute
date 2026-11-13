from .plan import GenerationPlan
from .geometry import (
    ASPECT_RATIO_BUCKETS,
    detect_scene_archetype,
    get_archetype_config,
    resolve_dimensions,
)
from .prompting import (
    HUMAN_KEYWORDS,
    ELARIA_VISUAL_DNA,
    ParsedImagePrompt,
    build_negative_prompt,
    parse_user_negative_trigger,
    parse_image_prompt_flags,
    detect_elaria_self_portrait_intent,
    sanitize_and_translate_tags,
)
from .contracts import TargetSubject, VisualIntent, SceneAnalysisContract
from .director import ArtDirector, ModelRegistrySelector
from .vram_policy import resolve_hires_second_pass_steps
from services.forge_models import MODEL_REGISTRY

from services.entity_mapper import (
    CATEGORIZED_ENTITIES,
    FALLBACK_ENTITY_MAP,
    fallback_cyrillic_to_english,
    load_entities_dictionary,
)

from .legacy_helpers import (
    is_model_realistic,
    is_animation_or_cartoon_style,
    ANIMATION_CARTOON_PATTERNS,
    has_fantasy_artistic_priority,
    find_dreamshaper_model,
    find_cyberrealistic_model,
    find_anime_model,
    find_realistic_vision_model,
    sanitize_negative_for_model,
)

__all__ = [
    "TargetSubject",
    "VisualIntent",
    "SceneAnalysisContract",
    "GenerationPlan",
    "ArtDirector",
    "ModelRegistrySelector",
    "MODEL_REGISTRY",
    "ASPECT_RATIO_BUCKETS",
    "detect_scene_archetype",
    "get_archetype_config",
    "resolve_dimensions",
    "HUMAN_KEYWORDS",
    "ELARIA_VISUAL_DNA",
    "ParsedImagePrompt",
    "build_negative_prompt",
    "parse_user_negative_trigger",
    "parse_image_prompt_flags",
    "detect_elaria_self_portrait_intent",
    "sanitize_and_translate_tags",
    "CATEGORIZED_ENTITIES",
    "FALLBACK_ENTITY_MAP",
    "fallback_cyrillic_to_english",
    "load_entities_dictionary",
    "is_model_realistic",
    "is_animation_or_cartoon_style",
    "ANIMATION_CARTOON_PATTERNS",
    "has_fantasy_artistic_priority",
    "find_dreamshaper_model",
    "find_cyberrealistic_model",
    "find_anime_model",
    "find_realistic_vision_model",
    "sanitize_negative_for_model",
    "resolve_hires_second_pass_steps",
]
