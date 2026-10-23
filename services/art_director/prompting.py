import re
import logging
import threading
from services.direct_command_parser import parse_user_negative_trigger

logger = logging.getLogger("NeonRoute.ArtDirector.Prompting")
CYRILLIC_PATTERN = re.compile(r'[\u0400-\u04FF]')

ELARIA_VISUAL_DNA = (
    "stunning beautiful 24yo woman, high fashion model face, "
    "sharp jawline, alluring expressive emerald green eyes, flawless skin texture, "
    "glossy lips, sleek stylish platinum silver bob haircut, subtle futuristic glowing cyber-earpiece"
)

HUMAN_KEYWORDS = (
    "1boy", "1girl", "android", "angel", "assassin", "bare", "bare shoulders", "bikini",
    "blushing", "boudoir", "boy", "bra", "bride", "bust shot", "catgirl", "cheerleader",
    "cleavage", "collarbone", "corset", "cosplay", "cowboy shot", "curvy", "cyborg",
    "dancer", "dark elf", "demon", "demoness", "drow", "elf", "elven", "empress",
    "erotic", "exposed skin", "expression", "eyes", "face", "female", "freckles",
    "full body", "gaze", "geisha", "girl", "goddess", "guy", "headshot", "heroine",
    "human", "hunter", "huntress", "incubus", "intimate", "kitsune", "kneeling",
    "knight", "lady", "leaning", "lingerie", "lips", "looking at viewer", "lying down",
    "mage", "maid", "maiden", "male", "man", "mermaid", "midriff", "model", "necromancer",
    "neko", "nude", "nun", "nurse", "nymph", "pale skin", "panties", "pantyhose",
    "person", "portrait", "posing", "priestess", "princess", "queen", "reclining",
    "rogue", "schoolgirl", "seductive", "sensual", "siren", "smile", "smirk",
    "sorceress", "stockings", "student", "succubus", "sultry", "swimsuit", "tan skin",
    "thighhighs", "undressing", "upper body", "valkyrie", "vampire", "vampiress",
    "voluptuous", "waifu", "warrior", "witch", "wizard", "woman"
)


def extract_negations_from_prompt(prompt_text: str) -> tuple[str, list[str]]:
    """
    Находит конструкции отрицания ('without coat', 'no glasses', 'not wearing shoes')
    в позитивном промпте, удаляет их из текста и возвращает:
    (очищенный_позитивный_промпт, список_токенов_для_негатива).
    """
    if not prompt_text:
        return "", []

    extracted_negs = []
    # Поиск шаблонов: without <item>, no <item>, not wearing <item>
    pattern = re.compile(
        r'\b(?:without|no|not\s+wearing)\s+(?:a\s+|an\s+|the\s+)?([a-zA-Z0-9_\-]+(?:\s+[a-zA-Z0-9_\-]+)?)',
        re.IGNORECASE
    )

    def _replace_match(m):
        item = m.group(1).strip().lower()
        if item:
            # Расширение синонимов для частых категорий
            if item in ("coat", "jacket", "overcoat"):
                extracted_negs.extend(["coat", "jacket", "overcoat", "outerwear"])
            elif item in ("glasses", "sunglasses"):
                extracted_negs.extend(["glasses", "eyewear", "sunglasses"])
            elif item in ("shoes", "boots", "sneakers"):
                extracted_negs.extend(["shoes", "footwear", "boots"])
            elif item in ("hat", "cap", "beanie"):
                extracted_negs.extend(["hat", "headwear", "cap"])
            elif item in ("bra", "underwear", "panties"):
                extracted_negs.extend(["bra", "underwear", "panties"])
            else:
                extracted_negs.append(item)
        return ""

    cleaned = pattern.sub(_replace_match, prompt_text)
    # Удаление повисших запятых и двойных пробелов
    cleaned = re.sub(r',\s*,+', ',', cleaned)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip(' ,')

    return cleaned, list(dict.fromkeys(extracted_negs))


def build_negative_prompt(
    prompt: str,
    profile_id: str,
    canon_base_neg: str,
    user_neg: str = "",
    archetype: str = "solo",
    custom_base_neg: str = ""
) -> str:
    """
    Модульный генератор негативного промпта:
    1. Авторский канон модели (или ручной negative_prompt из art_director_models.json).
    2. Явный пользовательский ввод (--neg).
    """
    neg_tokens = []

    # ── СЛОЙ 1: АВТОРСКИЙ КАНОН / НАСТРОЙКИ МОДЕЛИ ИЗ JSON ──
    if custom_base_neg and custom_base_neg.strip():
        neg_tokens.append(custom_base_neg.strip())
    elif canon_base_neg and canon_base_neg.strip():
        neg_tokens.append(canon_base_neg.strip())
    elif profile_id == "artistic":
        neg_tokens.append("ugly, deformed, noisy, blurry, low contrast")
    elif profile_id == "anime":
        neg_tokens.append("easynegative, (worst quality, low quality:1.4), (normal quality:1.2), lowres, bad anatomy")
    else:
        neg_tokens.append("(worst quality, low quality:1.4), deformed iris, bad anatomy, extra limbs, badhandv4")

    # ── СЛОЙ 2: ЯВНЫЙ РУЧНОЙ ВВОД ПОЛЬЗОВАТЕЛЯ (--neg) ──
    if user_neg and user_neg.strip():
        neg_tokens.append(user_neg.strip())

    # Сборка и очистка от повторяющихся тегов с сохранением исходного порядка
    raw_combined = ", ".join(t for t in neg_tokens if t)
    seen = set()
    cleaned = []
    for chunk in raw_combined.split(","):
        token = chunk.strip()
        if token and token.lower() not in seen:
            seen.add(token.lower())
            cleaned.append(token)

    return ", ".join(cleaned)


_TRANSLATOR = None
_TRANSLATOR_LOCK = threading.Lock()

def _get_argos_ru_en_translator():
    """Argos Translate is deprecated. Returns None."""
    return None


def sanitize_and_translate_tags(prompt_text: str) -> str:
    """Безопасная очистка тегов от лишних символов и кириллицы (0 VRAM)."""
    if not prompt_text:
        return ""
    clean = re.sub(r'[\u0400-\u04ff]', '', prompt_text)
    raw_tags = [t.strip().lower() for t in re.split(r'[,;\n]+', clean) if t.strip()]
    cleaned_tags = list(dict.fromkeys(raw_tags))
    return ", ".join(cleaned_tags)


def warmup_argos():
    """No-op stub for backward compatibility after Argos Translate removal."""
    pass

