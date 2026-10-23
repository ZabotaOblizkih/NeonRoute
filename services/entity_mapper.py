"""
NeonRoute - Entity Mapper & Cyrillic Guard
Детерминированное распознавание кириллических сущностей, сопоставление
тегов SD 1.5, очистка командных глаголов и Ambiguity Gate.
"""

import re
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional

from config import DATA_DIR

logger = logging.getLogger("NeonRoute.EntityMapper")

COMMAND_VERBS_PATTERN = re.compile(
    r'^(?:нарисуй(?:\s+мне|\s+пожалуйста)?|сгенерируй(?:\s+мне|\s+изображение|\s+картинку|\s+арт|\s+снимок|\s+фото)?|'
    r'покажи(?:\s+мне|\s+картинку|\s+арт)?|сделай(?:\s+мне|\s+картинку|\s+арт|\s+снимок|\s+фото)?|'
    r'изобрази|создай|сфотографируй|draw(?:\s+me|\s+an?\s+image\s+of|\s+a\s+picture\s+of)?|'
    r'generate(?:\s+an?\s+image\s+of)?|paint(?:\s+me)?|create(?:\s+an?\s+image\s+of)?)\s+',
    re.IGNORECASE
)

# ENTITIES_JSON_PATH = DATA_DIR / "entities.json"  # Deprecated and removed

CATEGORIZED_ENTITIES: Dict[str, Dict[str, str]] = {}
FALLBACK_ENTITY_MAP: Dict[str, str] = {}


def load_entities_dictionary(json_path: Optional[Path] = None) -> Tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
    """
    Возвращает встроенный системный словарь сущностей.
    Внешние файлы entities.json отключены.
    """
    fallback_minimal = {
        "эльф": "1girl, elf, pointy ears, beautiful elven features",
        "девушк": "1woman, young woman",
        "парен": "1man, young man",
        "люд": "people, realistic humans",
        "человек": "1person, realistic human",
        "брюнетк": "brunette, dark hair",
        "блондинк": "blonde, golden hair",
        "рыж": "redhead, ginger hair",
        "шатенк": "chestnut hair, brown hair",
        "коротк": "short hair",
        "стрижк": "stylish haircut",
        "черн": "black",
        "красн": "crimson red",
        "син": "midnight blue",
        "зелен": "emerald green",
        "бел": "white",
        "сидит": "sitting pose, seated",
        "кроват": "on bed, bedroom interior",
        "властн": "commanding gaze, looking down at viewer, dominant posture, smug confident expression",
        "манг": "manga style, monochrome, grayscale, screentone, ink lines, high contrast",
        "медвед": "bear, wild bear",
        "собак": "dog, domestic animal",
        "кошк": "cat, feline",
        "дирижабл": "steampunk airship, flying zeppelin, in the sky",
        "мотоцикл": "motorcycle, vehicle",
        "машин": "car, vehicle"
    }

    return fallback_minimal, {"builtin": fallback_minimal}


FALLBACK_ENTITY_MAP, CATEGORIZED_ENTITIES = load_entities_dictionary()


def sanitize_input_prompt(text: str) -> str:
    """Очищает входящий текст от командных глаголов, оставляя чистое семантическое ядро."""
    if not text:
        return ""
    cleaned = text.strip()
    for _ in range(2):
        cleaned = COMMAND_VERBS_PATTERN.sub('', cleaned).strip()
    return cleaned


def fallback_cyrillic_to_english(text: str) -> Tuple[str, bool, List[str], Set[str]]:
    """
    Автономный детерминированный конвертер кириллических сущностей в английские SD-теги.
    Возвращает: (translated_tags, is_ambiguous, unrecognized_words, matched_categories).
    """
    if not text:
        return "", True, [], set()

    lower_text = text.lower()
    lower_text = re.sub(r'[-–—]', ' ', lower_text)

    translated_tokens: List[str] = []
    recognized_phrases: List[str] = []
    matched_categories: Set[str] = set()

    # Сортировка по длине фразы для жадного сопоставления
    all_phrases = [(phrase, en_tags) for phrase, en_tags in FALLBACK_ENTITY_MAP.items()]
    all_phrases.sort(key=lambda x: len(x[0]), reverse=True)

    RU_CHAT_STOPWORDS = {
        "только", "сейчас", "буду", "будет", "пробовать", "поможешь", "вообще",
        "хочу", "хотел", "хотела", "получить", "сценах", "сцене", "сцена", "обычных",
        "обычный", "обычно", "просто", "также", "тоже", "очень", "давай", "можешь",
        "нужно", "надо", "чтобы", "если", "когда", "где", "как", "своей", "своих",
        "краю", "край", "взгляд", "сторону", "сторона", "вбок"
    }

    neg_rx_prefix = r'(?<![а-яА-ЯёЁa-zA-Z0-9])(?:не\s+только|не|кроме|без|not\s+only|without)\s+'

    for phrase, en_tags in all_phrases:
        p_words = phrase.split()
        pattern_parts = [re.escape(w) + r'[а-яА-ЯёЁa-zA-Z0-9]*' for w in p_words]
        core_pattern = r'\s+'.join(pattern_parts)
        pattern_str = r'(?<![а-яА-ЯёЁa-zA-Z0-9])' + core_pattern + r'(?![а-яА-ЯёЁa-zA-Z0-9])'
        neg_pattern_str = neg_rx_prefix + core_pattern + r'(?![а-яА-ЯёЁa-zA-Z0-9])'

        neg_rx = re.compile(neg_pattern_str, re.IGNORECASE)
        rx = re.compile(pattern_str, re.IGNORECASE)

        if neg_rx.search(lower_text):
            lower_text = neg_rx.sub(' ', lower_text)
            continue

        if rx.search(lower_text):
            translated_tokens.append(en_tags)
            recognized_phrases.append(phrase)
            lower_text = rx.sub(' ', lower_text)

    raw_latin = re.findall(r'[a-zA-Z0-9_\-]+', lower_text)
    latin_words = [w.strip('-_') for w in raw_latin if len(w.strip('-_')) > 0]
    translated_tokens.extend(latin_words)

    has_cyrillic = bool(re.search(r'[а-яА-ЯёЁ]', text))
    if not has_cyrillic:
        return ", ".join(filter(None, translated_tokens)), False, [], matched_categories

    unrecognized_words = [
        w for w in re.findall(r'[а-яА-ЯёЁ]+', lower_text) 
        if len(w) > 2 and w.lower() not in RU_CHAT_STOPWORDS
    ]

    is_ambiguous = (len(recognized_phrases) == 0 and len(latin_words) == 0 and len(unrecognized_words) > 0)

    if unrecognized_words:
        logger.warning(
            f"[EntityMapper] Нераспознанные слова в запросе: {unrecognized_words}. "
            f"Распознано сущностей: {recognized_phrases or 'ничего'}."
        )

    if is_ambiguous:
        logger.warning(
            f"[EntityMapper] 0 визуальных сущностей сопоставлено для текста: '{text}'. "
            f"Активирован Ambiguity Gate."
        )
        return "", True, unrecognized_words, matched_categories

    if not translated_tokens:
        translated_tokens = ["cinematic setting", "detailed composition", "high quality render"]

    return ", ".join(filter(None, translated_tokens)), False, unrecognized_words, matched_categories
