"""
Backward compatibility helpers for legacy test suites and external integrations.
"""

import re
from typing import Dict, Any, List


ANIMATION_CARTOON_PATTERNS = [
    r'\bцифров(?:ой|ый|ом|ому|ого)\s+арт\b',
    r'\bарт[\s\-]стил[а-яё]*\b',
    r'\bвекторн(?:ый|ом|ому|ого)\s+арт\b',
    r'\bконцепт[\s\-]арт[а-яё]*\b',
    r'\bаниме\b',
    r'\bманга\b',
    r'\bвайфу\b',
    r'\bхентай\b',
    r'\bмульт(?:фильм|яшн)[а-яё]*\b',
    r'\bкомикс[а-яё]*\b',
    r'\bиллюстраци[а-яё]*\b',
    r'\b2d\b',
    r'\bchibi\b',
    r'\bчиби\b',
]


def is_animation_or_cartoon_style(text: str) -> bool:
    if not text:
        return False
    # Strip headers like 🎨 Арт:
    cleaned = re.sub(r'🎨\s*[Аа]рт\s*:', '', text)
    cleaned_lower = cleaned.lower()
    for pat in ANIMATION_CARTOON_PATTERNS:
        if re.search(pat, cleaned_lower, flags=re.IGNORECASE):
            return True
    return False


def is_model_realistic(model: Dict[str, Any]) -> bool:
    if not isinstance(model, dict):
        return False
    m_id = str(model.get("id", "")).lower()
    name = str(model.get("name", "")).lower()
    desc = str(model.get("description", "")).lower()
    ckpt = str(model.get("checkpoint", "")).lower()

    if "anime" in m_id or "meina" in ckpt or "meina" in name:
        return False
    if "fantasy" in m_id or "dreamshaper" in ckpt or "dreamshaper" in name:
        return False
    if "realistic" in m_id or "realistic" in name or "cyberrealistic" in ckpt or "photo" in desc:
        return True
    return False


def has_fantasy_artistic_priority(prompt: str) -> bool:
    lower = prompt.lower()
    fantasy_markers = [
        "эльф", "elf", "магия", "magic", "акварель", "watercolor",
        "фэнтези", "fantasy", "живопись", "digital painting", "enchanted",
        "волшебн", "дракон", "мистик", "масло", "oil painting"
    ]
    real_photo_explicit_markers = [
        "фото", "photo", "dslr", "35mm", "снимок", "сфотографируй", "косплей", "cosplay"
    ]
    return any(k in lower for k in fantasy_markers) and not any(k in lower for k in real_photo_explicit_markers)


def find_dreamshaper_model(models: List[Dict[str, Any]]) -> Dict[str, Any]:
    for m in models:
        ckpt = str(m.get("checkpoint", "")).lower()
        m_id = str(m.get("id", "")).lower()
        m_name = str(m.get("name", "")).lower()
        if "dreamshaper" in ckpt or "dreamshaper" in m_name or "fantasy" in m_id:
            return m
    return models[0] if models else {}


def find_cyberrealistic_model(models: List[Dict[str, Any]]) -> Dict[str, Any]:
    for m in models:
        ckpt = str(m.get("checkpoint", "")).lower()
        m_id = str(m.get("id", "")).lower()
        m_name = str(m.get("name", "")).lower()
        if "cyberrealistic" in ckpt or "cyberrealistic" in m_id or "cyber" in m_name:
            return m
    return models[0] if models else {}


def find_anime_model(models: List[Dict[str, Any]]) -> Dict[str, Any]:
    for m in models:
        ckpt = str(m.get("checkpoint", "")).lower()
        m_id = str(m.get("id", "")).lower()
        m_name = str(m.get("name", "")).lower()
        if "anime" in m_id or "meina" in ckpt or "meina" in m_name:
            return m
    return models[0] if models else {}


def find_realistic_vision_model(models: List[Dict[str, Any]]) -> Dict[str, Any]:
    for m in models:
        ckpt = str(m.get("checkpoint", "")).lower()
        m_id = str(m.get("id", "")).lower()
        m_name = str(m.get("name", "")).lower()
        if "realisticvision" in ckpt or m_id == "realistic" or "realistic vision" in m_name:
            return m
    return models[0] if models else {}


def sanitize_negative_for_model(style: str, negative_prompt: str) -> str:
    if not negative_prompt:
        return ""
    if style in ("realistic", "cyberrealistic"):
        pattern = r'\b(?:photorealistic|real\s+photo)\b'
        cleaned = re.sub(pattern, '', negative_prompt, flags=re.IGNORECASE)
        parts = [p.strip() for p in cleaned.split(',') if p.strip()]
        return ", ".join(parts)
    return negative_prompt
