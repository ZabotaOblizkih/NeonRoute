"""
Geometry and aspect ratio management for SD 1.5 and SDXL.
"""
import re
from typing import Any, Dict

ASPECT_RATIO_BUCKETS_SD15: Dict[str, Dict[str, Any]] = {
    "fullbody":  {"width": 448, "height": 768, "extra_neg": "", "force_ad": True},
    "solo":      {"width": 512, "height": 768, "extra_neg": "", "force_ad": True},
    "closeup":   {"width": 512, "height": 512, "extra_neg": "", "force_ad": True},
    "pair":      {"width": 768, "height": 512, "extra_neg": "", "force_ad": True},
    "scenery":   {"width": 768, "height": 512, "extra_neg": "", "force_ad": False},
    "cinematic": {"width": 768, "height": 448, "extra_neg": "", "force_ad": False},
}

ASPECT_RATIO_BUCKETS_SDXL: Dict[str, Dict[str, Any]] = {
    "fullbody":  {"width": 832,  "height": 1216, "extra_neg": "", "force_ad": True},
    "solo":      {"width": 896,  "height": 1152, "extra_neg": "", "force_ad": True},
    "closeup":   {"width": 1024, "height": 1024, "extra_neg": "", "force_ad": True},
    "pair":      {"width": 1152, "height": 896,  "extra_neg": "", "force_ad": True},
    "scenery":   {"width": 1216, "height": 832,  "extra_neg": "", "force_ad": False},
    "cinematic": {"width": 1344, "height": 768,  "extra_neg": "", "force_ad": False},
}

ASPECT_RATIO_BUCKETS = ASPECT_RATIO_BUCKETS_SD15

XL_DELIMITED_PATTERN = re.compile(
    r'(?i)(?:^|[\W_])(?:sdxl|pony|flux|illustrious|sd3|stable[\s_-]?diffusion[\s_-]?3)(?:[\W_\d]|$)'
)
XL_TOKEN_PATTERN = re.compile(
    r'(?i:(?:^|[\W_])xl(?:[\W_\d]|$))|(?:[a-zA-Z0-9]XL(?:[\W_\d]|$))'
)

def is_xl_model(identifier: Any) -> bool:
    """Отказоустойчивое распознавание SDXL/Pony/Flux без ложных срабатываний на 'pixel', 'complex'."""
    if not identifier:
        return False

    if isinstance(identifier, dict):
        arch = str(identifier.get("architecture") or (identifier.get("pipeline") or {}).get("architecture") or "").lower()
        if arch in ("sd15", "sd1", "sd_15"):
            return False
        if arch in ("sdxl", "pony", "flux", "illustrious", "sd3"):
            return True
        text = f"{identifier.get('checkpoint', '')} {identifier.get('name', '')} {identifier.get('id', '')}"
    elif isinstance(identifier, (list, tuple, set)):
        text = " ".join(str(x) for x in identifier if x)
    else:
        text = str(identifier)

    clean_text = re.sub(r'(?i)\b(pixel|complex|duplex|reflex|flexible|relax)\b', '', text)
    is_distilled_xl = any(k in clean_text.lower() for k in ("dmd", "mop", "_fp8", "lightning", "hyper-sd", "hyper_sd", "hypersd"))
    return bool(XL_DELIMITED_PATTERN.search(clean_text) or XL_TOKEN_PATTERN.search(clean_text)) or is_distilled_xl

def get_archetype_config(model_key: Any, archetype: str) -> Dict[str, Any]:
    is_xl = is_xl_model(model_key)
    buckets = ASPECT_RATIO_BUCKETS_SDXL if is_xl else ASPECT_RATIO_BUCKETS_SD15
    bucket = buckets.get(archetype, buckets.get("solo", {"width": 512, "height": 768}))
    return {
        "width": bucket["width"],
        "height": bucket["height"],
        "extra_negative": bucket.get("extra_neg", ""),
        "force_adetailer": bucket.get("force_ad", True),
        "is_xl": is_xl
    }

def detect_scene_archetype(prompt: str) -> str:
    p = prompt.lower()
    if any(m in p for m in ("close-up", "close up", "headshot", "face portrait", "macro face", "extreme close-up", "macro portrait")):
        return "closeup"
    if any(m in p for m in ("wide shot", "panorama", "panoramic", "horizon", "vista", "battlefield", "skyline", "aerial view", "landscape panorama")):
        return "cinematic"

    # Маркеры группы, парных сцен и оператора BREAK
    pair_markers = (
        "couple", "1man and 1woman", "1woman and 1man", "1boy and 1girl",
        "1girl and 1boy", "two people", "2people", "two girls", "2girls",
        "two women", "2women", "two men", "2men", "duo", "side by side",
        "standing together", "sitting together", "holding hands", "embracing",
        "sisters", "sisterly", "both girls", "together", " break "
    )
    if any(m in p for m in pair_markers):
        return "pair"

    scenery_markers = (
        "landscape", "scenery", "interior", "castle", "room", "tomb", "dungeon", "street",
        "cityscape", "environment", "fortress", "palace", "ruins", "forest", "mountains", "nature"
    )
    human_markers = ("1girl", "1woman", "1boy", "1man", "woman", "man", "person", "people", "girl", "boy", "knight", "soldier")
    if any(m in p for m in scenery_markers) and not any(h in p for h in human_markers):
        return "scenery"

    fullbody_markers = (
        "full body", "full-body", "standing", "full length", "head to toe", "long shot", "entire body", "boots", "dress"
    )
    if any(m in p for m in fullbody_markers):
        return "fullbody"

    return "solo"

def resolve_dimensions(prompt: str, model_key: Any = "realistic") -> tuple[int, int]:
    cfg = get_archetype_config(model_key, detect_scene_archetype(prompt))
    return cfg["width"], cfg["height"]
