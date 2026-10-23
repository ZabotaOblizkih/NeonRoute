"""
Art Director Models Configuration Manager for NeonRoute
Handles loading, atomic saving, and default generation for data/art_director_models.json
"""

import os
import json
import logging
import uuid
import re
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from config import ART_DIRECTOR_MODELS_PATH

logger = logging.getLogger("NeonRoute.ArtDirectorConfig")

DEFAULT_ART_DIRECTOR_MODELS: Dict[str, Any] = {
    "models": [
        {
            "id": "realistic",
            "name": "Realistic Vision",
            "checkpoint": "realisticVisionV60B1_v51HyperVAE_pruned_fp16.safetensors",
            "routing_description": "Повседневные фотореалистичные кадры, люди, реальная жизнь, естественный будуар и студийные портреты, raw photo, dslr.",
            "is_default": True,
            "style_triggers": [],
            "active": True,
            "triggers": ["photo", "photorealistic", "portrait", "real life", "nature", "фото", "снимок", "портрет", "реализм", "dslr", "девуш", "женщин", "человек", "люд", "парн", "мужчин", "настоящ", "реальн"],
            "description": "Повседневные фотореалистичные кадры, люди, реальная жизнь, естественный будуар и студийные портреты."
        },
        {
            "id": "cyberrealistic",
            "name": "CyberRealistic Classic",
            "checkpoint": "cyberrealistic_classic.safetensors",
            "routing_description": "Технологичный фотореализм, киберпанк, глянец, латекс, броня, хай-тек будуар и контрастное неоновое освещение, cyberpunk, sci-fi.",
            "is_default": False,
            "style_triggers": [],
            "active": True,
            "triggers": ["киберпанк", "cyberpunk", "неон", "neon", "sci-fi", "футуризм", "андроид", "high-tech", "латекс", "ночной город"],
            "description": "Технологичный фотореализм, киберпанк, глянец, латекс, броня, хай-тек будуар и контрастное неоновое освещение."
        },
        {
            "id": "fantasy_art",
            "name": "DreamShaper 8",
            "checkpoint": "dreamshaper_8.safetensors",
            "routing_description": "Художественные арты, цифровая живопись, магия, сказочные расы, фэнтези и концепт-арт, magic, fantasy.",
            "is_default": False,
            "style_triggers": ["рисунок", "картина", "маслом", "акварель", "иллюстрация", "painting", "artstation"],
            "active": True,
            "triggers": ["art", "fantasy", "digital painting", "illustration", "concept art", "рисунок", "картина", "живопись", "фэнтези", "арт", "акварель", "масло", "эльф", "elf", "магия"],
            "description": "Художественные арты, цифровая живопись, магия, сказочные расы, фэнтези и концепт-арт."
        },
        {
            "id": "anime_meina",
            "name": "MeinaHentai",
            "checkpoint": "meinahentai_v5Final.safetensors",
            "routing_description": "Чистая 2D аниме-рисовка, манга, вайфу, стилизованный аниме-ролеплей и хентай, anime, 2d, waifu.",
            "is_default": False,
            "style_triggers": ["аниме", "anime", "манга", "manga", "2d", "2д", "вайфу", "тян", "селл-шейдинг"],
            "active": True,
            "triggers": ["anime", "аниме", "manga", "манга", "вайфу", "waifu", "хентай", "hentai", "2d", "чиби", "chibi", "в стиле аниме", "anime screencap"],
            "description": "Чистая 2D аниме-рисовка, манга, вайфу, стилизованный аниме-ролеплей и хентай."
        }
    ]
}


def load_art_director_models() -> List[Dict[str, Any]]:
    """
    Loads list of registered models strictly from data/art_director_models.json.
    Eliminates all legacy lookups in config/art_director_models.json.
    """
    if not ART_DIRECTOR_MODELS_PATH.exists():
        save_art_director_models(DEFAULT_ART_DIRECTOR_MODELS["models"])
        return list(DEFAULT_ART_DIRECTOR_MODELS["models"])

    try:
        with open(ART_DIRECTOR_MODELS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        models = data.get("models")
        if isinstance(models, list) and len(models) > 0:
            return models
        else:
            logger.warning("art_director_models.json contained empty/invalid 'models' list, restoring defaults.")
            save_art_director_models(DEFAULT_ART_DIRECTOR_MODELS["models"])
            return list(DEFAULT_ART_DIRECTOR_MODELS["models"])
    except Exception as e:
        logger.error(f"Failed to read art_director_models.json: {e}. Restoring default configuration.")
        save_art_director_models(DEFAULT_ART_DIRECTOR_MODELS["models"])
        return list(DEFAULT_ART_DIRECTOR_MODELS["models"])


def save_art_director_models(models: List[Dict[str, Any]]) -> bool:
    """
    Saves registered models list to data/art_director_models.json atomically.
    Strictly UTF-8 without BOM.
    """
    ART_DIRECTOR_MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = ART_DIRECTOR_MODELS_PATH.with_suffix(".tmp")
    data = {"models": models}

    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        saved = False
        for attempt in range(5):
            try:
                temp_path.replace(ART_DIRECTOR_MODELS_PATH)
                saved = True
                break
            except (PermissionError, OSError):
                time.sleep(0.04 * (2 ** attempt))
        if not saved:
            logger.error(f"Не удалось сохранить {ART_DIRECTOR_MODELS_PATH} из-за блокировки Windows.")
            return False

        logger.info(f"Saved {len(models)} Art Director models to {ART_DIRECTOR_MODELS_PATH}")
        return True
    except Exception as e:
        logger.error(f"Failed to save art_director_models.json: {e}")
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        return False


def format_models_instruction(models: List[Dict[str, Any]]) -> str:
    """
    Форматирует соответствие между доступными чекпоинтами и целевыми style_domain для LLM.
    """
    style_map = {
        "realistic": "realistic",
        "cyberrealistic": "cyberrealistic",
        "fantasy_art": "artistic",
        "anime_meina": "anime"
    }
    lines = ["Available Model Families (match strictly to style_domain values):"]
    for m in models:
        m_id = m.get("id", "")
        name = m.get("name", "Unknown Model")
        desc = m.get("description", "")
        domain = style_map.get(m_id, "artistic" if "dream" in m.get("checkpoint", "").lower() else "realistic")
        lines.append(f"- style_domain=\"{domain}\" -> {name}: {desc}")
    return "\n".join(lines)


def find_best_model_heuristic(raw_prompt: str, models: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Heuristically matches user prompt with model triggers using a strict 4-step cascade:
    Step 1: Anime/2D markers  -> anime_meina (MeinaHentai)
    Step 2: Fantasy/art/magic -> fantasy_art (DreamShaper 8)
    Step 3: Sci-fi/neon/cyber -> cyberrealistic (CyberRealistic Classic)
    Step 4: Default           -> realistic (Realistic Vision)
    """
    if not models:
        return DEFAULT_ART_DIRECTOR_MODELS["models"][0]

    lower_prompt = raw_prompt.lower()

    # ---------- Step 1: Anime / 2D priority ----------
    anime_markers = [
        "аниме", "anime", "манга", "manga", "вайфу", "waifu",
        "хентай", "hentai", "2d", "чиби", "chibi", "в стиле аниме", "anime screencap"
    ]
    if any(k in lower_prompt for k in anime_markers):
        for m in models:
            m_id = m.get("id", "").lower()
            m_ckpt = m.get("checkpoint", "").lower()
            m_name = m.get("name", "").lower()
            if m_id == "anime_meina" or "meinahentai" in m_ckpt or "meina" in m_name:
                return m

    # ---------- Step 2: Fantasy / Art / Magic priority ----------
    fantasy_markers = [
        "эльф", "elf", "магия", "magic", "акварель", "watercolor",
        "фэнтези", "fantasy", "живопись", "digital painting", "enchanted",
        "волшебн", "дракон", "мистик", "масло", "oil painting"
    ]
    real_photo_explicit_markers = [
        "фото", "photo", "dslr", "35mm", "снимок", "сфотографируй", "косплей", "cosplay"
    ]
    if any(k in lower_prompt for k in fantasy_markers) and not any(k in lower_prompt for k in real_photo_explicit_markers):
        for m in models:
            m_id = m.get("id", "").lower()
            m_ckpt = m.get("checkpoint", "").lower()
            m_name = m.get("name", "").lower()
            if "dreamshaper" in m_ckpt or "dreamshaper" in m_name or "fantasy" in m_id:
                return m

    # ---------- Step 3: Sci-fi / Neon / Cyberpunk priority ----------
    cyber_markers = [
        "киберпанк", "cyberpunk", "неон", "neon", "sci-fi", "футуризм",
        "андроид", "high-tech", "латекс", "ночной город"
    ]
    if any(k in lower_prompt for k in cyber_markers):
        for m in models:
            m_id = m.get("id", "").lower()
            m_ckpt = m.get("checkpoint", "").lower()
            m_name = m.get("name", "").lower()
            if m_id == "cyberrealistic" or "cyberrealistic" in m_ckpt or "cyber" in m_name.lower():
                return m

    # Explicit real photo / cosplay priority
    if any(k in lower_prompt for k in real_photo_explicit_markers):
        for m in models:
            m_id = m.get("id", "").lower()
            m_ckpt = m.get("checkpoint", "").lower()
            if m_id == "realistic" or "realisticvision" in m_ckpt:
                return m

    # ---------- Trigger-score fallback (for ambiguous prompts) ----------
    best_model = None
    best_score = 0
    for m in models:
        score = 0
        triggers = m.get("triggers", [])
        for t in triggers:
            t_clean = t.strip().lower()
            if not t_clean:
                continue
            # Для кириллицы граница только в начале (ловит 'девуш-ка/ек', исключая совпадения внутри других слов)
            # Для латиницы строгая изоляция слова с двух сторон (исключает 'art' внутри 'apart')
            if re.search(r'[а-яА-ЯёЁ]', t_clean):
                pat = rf"\b{re.escape(t_clean)}"
            else:
                pat = rf"\b{re.escape(t_clean)}s?\b"

            if re.search(pat, lower_prompt):
                score += (len(t_clean.split()) * 2) + (1 if len(t_clean) > 3 else 0)
        desc = m.get("description", "").lower()
        desc_words = [w for w in re.split(r'[,.\s]+', desc) if len(w) > 3]
        for w in desc_words:
            if w in lower_prompt:
                score += 1
        if score > best_score:
            best_score = score
            best_model = m
    if best_model and best_score > 0:
        return best_model

    # ---------- Step 4: Default -> Realistic Vision ----------
    for m in models:
        m_id = m.get("id", "").lower()
        m_ckpt = m.get("checkpoint", "").lower()
        if m_id == "realistic" or "realisticvision" in m_ckpt:
            return m

    return models[0]

