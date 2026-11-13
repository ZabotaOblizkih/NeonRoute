import re
import logging
import threading
from dataclasses import dataclass
from typing import Optional
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
    "cleavage", "collarbone", "corset", "cosplay", "curvy", "cyborg",
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
    "thigh-up shot", "thighhighs", "undressing", "upper body", "valkyrie", "vampire", "vampiress",
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


@dataclass
class ParsedImagePrompt:
    """Результат разбора директивы [IMAGE_PROMPT: ...] с флагами."""
    prompt: str
    negative: str = ""
    model_style: Optional[str] = None
    aspect_ratio: Optional[str] = None
    is_self_portrait: bool = False


_RE_IMG_MODEL_FLAG = re.compile(r'(?:--model|--style)[:=\s]+["\']?([a-zA-Z0-9_\-]+)["\']?', re.IGNORECASE)
_RE_IMG_NEG_FLAG = re.compile(
    r'(?:--neg|--negative)[:=\s]+(.*?)(?=(?:\s*--(?:model|style|ar|self|subject|hands)\b|$))',
    re.IGNORECASE,
)
_RE_IMG_AR_FLAG = re.compile(r'--ar[:=\s]+([a-zA-Z0-9:]+)', re.IGNORECASE)
_RE_IMG_SELF_FLAG = re.compile(r'(?:^|[\s,])--self\b', re.IGNORECASE)
_RE_IMG_SUBJECT_FLAG = re.compile(r'--subject[:=\s]+["\']?([a-zA-Z0-9_\-]+)["\']?', re.IGNORECASE)


def parse_image_prompt_flags(raw_prompt: str) -> ParsedImagePrompt:
    """
    Единый SSOT-парсер флагов внутри [IMAGE_PROMPT: ...]:
    извлекает --model/--style, --neg/--negative, --ar и маркер автопортрета --self / --subject elaria.
    """
    text = (raw_prompt or "").strip()
    if not text:
        return ParsedImagePrompt(prompt="")

    is_self_flag = bool(_RE_IMG_SELF_FLAG.search(text))
    subj_match = _RE_IMG_SUBJECT_FLAG.search(text)
    if subj_match and subj_match.group(1).strip().lower() in ("elaria", "self", "assistant"):
        is_self_flag = True

    neg_match = _RE_IMG_NEG_FLAG.search(text)
    custom_neg = neg_match.group(1).strip().strip(' ,') if neg_match else ""
    if neg_match:
        text = _RE_IMG_NEG_FLAG.sub('', text)

    model_match = _RE_IMG_MODEL_FLAG.search(text)
    model_style = model_match.group(1).lower().strip() if model_match else None
    if model_match:
        text = _RE_IMG_MODEL_FLAG.sub('', text)

    ar_match = _RE_IMG_AR_FLAG.search(text)
    aspect_ratio = ar_match.group(1).lower().strip() if ar_match else None
    if ar_match:
        text = _RE_IMG_AR_FLAG.sub('', text)

    text = _RE_IMG_SELF_FLAG.sub('', text)
    text = _RE_IMG_SUBJECT_FLAG.sub('', text)
    text = re.sub(r',\s*,+', ',', text)
    text = re.sub(r'\s{2,}', ' ', text).strip(' ,')

    return ParsedImagePrompt(
        prompt=text,
        negative=custom_neg,
        model_style=model_style,
        aspect_ratio=aspect_ratio,
        is_self_portrait=is_self_flag,
    )


_RE_LEADING_ELARIA_VOCATIVE = re.compile(r'^\s*элари[яею]?\b[\s,:-]*', re.IGNORECASE)
_RE_SELF_DIRECT_CMD = re.compile(
    r'(?:^|[.!?]\s*)(?:пожалуйста\s+)?(?:а\s+|ну\s+|можешь\s+|давай\s+)?'
    r'(?:нарисуй|нарисуешь|нарисовать|изобрази|изобразишь|изобразить|'
    r'покажи|покажешь|показать|сгенерируй|сгенерируешь|сгенерировать|'
    r'сделай|сделать|сфоткай|сфоткать|сфотографируй|сфотографировать|'
    r'отрисуй|отрисовать)'
    r'(?:\s+(?:мне|нам|пожалуйста|просто|ка|ли))*\s+'
    r'(?:(?:саму\s+)?себя|тебя|свой\s+(?:портрет|образ|автопортрет))\b',
    re.IGNORECASE,
)
_RE_ELARIA_DIRECT_OBJ = re.compile(
    r'\b(?:нарисуй|нарисуешь|нарисовать|покажи|покажешь|показать|'
    r'изобрази|изобразишь|изобразить|сфоткай|сфотографируй|отрисуй)\s+'
    r'(?:мне\s+)?(?:элари[юея]|саму\s+себя)\b',
    re.IGNORECASE,
)
_RE_SELF_PHRASES = re.compile(
    r'\b(?:как\s+ты\s+выглядишь|твой\s+(?:портрет|образ|автопортрет)|'
    r'тво[её]\s+(?:селфи|фото)|сделай\s+селфи|свой\s+автопортрет)\b'
    r'|^\s*автопортрет\s*$',
    re.IGNORECASE,
)
_RE_PROMPT_ELARIA_NAME = re.compile(r'\b(elaria|элария)\b', re.IGNORECASE)


def detect_elaria_self_portrait_intent(
    user_query: str = "",
    raw_prompt: str = "",
    has_self_flag: bool = False,
) -> bool:
    """
    Определяет, запрашивается ли автопортрет Эларии.
    Приоритет 1: явный семантический флаг --self / --subject elaria от LLM.
    Приоритет 2: упоминание имени Elaria/Элария в самом SD-промпте.
    Приоритет 3: строгий синтаксический фоллбэк по пользовательской команде
    (исключает ложные срабатывания на 'девушка держит себя в руках' и т.п.).
    """
    if has_self_flag:
        return True
    if raw_prompt and _RE_PROMPT_ELARIA_NAME.search(raw_prompt):
        return True

    low_u = (user_query or "").strip().lower()
    if not low_u:
        return False

    clean_user_intent = _RE_LEADING_ELARIA_VOCATIVE.sub('', low_u).strip()
    return bool(
        _RE_SELF_DIRECT_CMD.search(clean_user_intent)
        or _RE_ELARIA_DIRECT_OBJ.search(low_u)
        or _RE_SELF_PHRASES.search(clean_user_intent)
    )

