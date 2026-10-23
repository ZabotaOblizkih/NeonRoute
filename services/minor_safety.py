"""
Minor Safety Detector & Prompt Sanitizer for NeonRoute.
Provides deterministic age extraction, non-destructive prompt sanitization,
and child safety filters for SD WebUI Forge / ArtDirector.
"""

import re
from typing import List, Tuple, Optional


class MinorSafetyDetector:
    """Интеллектуальный анализатор возраста и шлюз безопасности контента."""

    # 1. Регулярные выражения для явного числового возраста
    _AGE_PATTERNS = [
        re.compile(r'\b(?:age|возраст|old|лет|года|годика)[\s:—–=~≈]*(\d{1,2})\b', re.IGNORECASE),
        re.compile(r'\b(\d{1,2})\s*(?:yo|y\.o\.|-year-old|years old|лет|года)\b', re.IGNORECASE),
        re.compile(r'[~≈]\s*(\d{1,2})\b', re.IGNORECASE),
    ]

    # 2. Семантические маркеры детского статуса (только однозначные термины)
    _MINOR_KEYWORDS = {
        "ребёнок", "ребенок", "дитя", "малыш", "малышка", "малолетняя", "малолетний",
        "школьница младших классов", "child", "kid", "toddler", "elementary school",
        "middle school", "loli", "shota"
    }

    # 3. Исключения (защита от ложных срабатываний)
    _FALSE_POSITIVE_SUBSTRINGS = [
        "childhood", "childish", "child of", "детство", "детская улыбка", "по-детски"
    ]

    # 4. Хирургический черный список (только нагота, гениталии и откровенный lewd)
    _LEWD_TAGS_PATTERN = re.compile(
        r'\b('
        r'nude|naked|topless|bottomless|nipples?|areola|pussy|genitals?|cameltoe|'
        r'lingerie|panties|bra|thong|micro bikini|underbust|erotic|lewd|nsfw|'
        r'seductive|undressing|sexualized'
        r')\b',
        re.IGNORECASE
    )

    # 5. Признаки наличия одежды (авторский белый список тканей, платьев и лохмотьев)
    _CLOTHING_TAGS_PATTERN = re.compile(
        r'\b('
        r'dress|skirt|tunic|robe|blouse|shirt|pants|shorts|boots|cloak|hood|'
        r'apron|tattered|ragged|rags|clothes|clothing|outfit|uniform|kimono|'
        r'hanfu|sweater|jacket|vest|habit|collar|shackles'
        r')\b',
        re.IGNORECASE
    )

    # Защитный инжекционный стек в Negative Prompt
    HARD_NEGATIVE_STACK = (
        "nude, naked, nsfw, lewd, suggestive, exposed skin, underwear, lingerie, "
        "bikini, cleavage, erotic, sexualized"
    )

    @classmethod
    def is_minor(cls, text: str) -> bool:
        """Определяет, относится ли текст/описание к несовершеннолетнему (возраст < 18)."""
        if not text:
            return False

        t_clean = text.lower()

        # Шаг 1: Проверка точных числовых указаний возраста
        for pat in cls._AGE_PATTERNS:
            for match in pat.finditer(t_clean):
                try:
                    age = int(match.group(1))
                    if 0 < age < 18:
                        return True
                    if age >= 18:
                        # Явно взрослый персонаж (например, Kai ≈25)
                        return False
                except ValueError:
                    continue

        # Шаг 2: Проверка на исключения-ложные срабатывания
        for fp in cls._FALSE_POSITIVE_SUBSTRINGS:
            t_clean = t_clean.replace(fp, " ")

        # Шаг 3: Семантические ключевые слова
        words = set(re.findall(r'\b[a-zA-Zа-яА-ЯёЁ_-]+\b', t_clean))
        if any(kw in words or kw in t_clean for kw in cls._MINOR_KEYWORDS):
            return True

        return False

    @classmethod
    def sanitize_positive_prompt(cls, prompt: str, context_hint: str = "") -> str:
        """
        Удаляет только сексуальные теги наготы.
        Сохраняет рваную одежду, лохмотья, ошейники, кандалы, босоногость и обычные наряды.
        """
        if not prompt:
            return ""

        # Удаляем запрещенные теги
        cleaned = cls._LEWD_TAGS_PATTERN.sub("", prompt)
        
        # Чистим двойные запятые и пробелы
        tokens = [t.strip() for t in cleaned.split(",") if t.strip()]
        cleaned_prompt = ", ".join(tokens)

        # Проверяем, остался ли персонаж одетым после удаления
        has_clothes = bool(cls._CLOTHING_TAGS_PATTERN.search(cleaned_prompt))
        
        if not has_clothes:
            # Аварийный контекстный фолбэк: не навязываем modern casual, если это фэнтези/рабы
            is_slave_or_poor = any(w in (cleaned_prompt + " " + context_hint).lower() 
                                   for w in ["slave", "shackles", "collar", "cage", "rags", "tattered"])
            fallback_outfit = "tattered clothes, ragged tunic" if is_slave_or_poor else "simple modest clothes, dress"
            cleaned_prompt = f"{cleaned_prompt}, {fallback_outfit}".strip(", ")

        return cleaned_prompt
