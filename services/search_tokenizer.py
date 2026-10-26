"""
services/search_tokenizer.py

Автономный токенизатор с морфологической нормализацией (стемминг RU/EN)
и расщеплением составных технических терминов.
Не требует внешних зависимостей (NLTK/SpaCy), работает на чистом Python/re.
"""

import re
from typing import List, Set


class SearchTokenizer:
    """Токенизатор для BM25 и текстового индекса с поддержкой морфологии."""

    # Регулярные выражения для русского стеммера Портера
    _RU_VOWEL = re.compile(r"[аеиоуыэюя]")
    _RU_PERFECTIVEGERUND = re.compile(r"((ив|ивши|ившись|ыв|ывши|ывшись)|((?<=[ая])(в|вши|вшись)))$")
    _RU_ADJECTIVE = re.compile(r"(ее|ие|ые|ое|ими|ыми|ей|ий|ый|ой|ем|им|ым|ом|его|ого|ему|ому|их|ых|ую|юю|ая|яя|ою|ею)$")
    _RU_PARTICIPLE = re.compile(r"((ивш|ывш|ующ)|((?<=[ая])(ем|нн|вш|ющ|щ)))$")
    _RU_REFLEXIVE = re.compile(r"(ся|сь)$")
    _RU_VERB = re.compile(
        r"((ила|ыла|ена|ейте|уйте|ите|или|ыли|ей|уй|ил|ыл|им|ым|ен|ило|ыло|ено|ят|ует|уют|ит|ыт|ены|ить|ыть|ишь)|"
        r"((?<=[ая])(ла|на|ете|йте|ли|й|л|ем|н|ло|но|ет|ют|ны|ть|ешь|нно)))$"
    )
    _RU_NOUN = re.compile(
        r"(а|ев|ов|ие|ье|е|иями|ями|ами|еи|ии|и|ией|ей|ой|ий|й|иям|ям|ием|ем|ам|ом|о|у|ах|иях|ях|ы|ь|ию|ью|ю|я)$"
    )
    _RU_SUPERLATIVE = re.compile(r"(ейш|ейше)$")
    _RU_DERIVATIONAL = re.compile(r".*[^аеиоуыэюя]+[аеиоуыэюя]+.*ость?$")

    # Префиксы для декомпозиции составных технических слов
    _COMMON_PREFIXES = (
        "видео", "аудио", "микро", "макро", "мульти", "авто", "гипер",
        "супер", "квази", "псевдо", "суб", "транс", "интер", "де", "ре"
    )

    @classmethod
    def stem_ru(cls, word: str) -> str:
        """Стемминг русского слова (Porter Stemmer)."""
        w = word.lower().replace("ё", "е")
        match = cls._RU_VOWEL.search(w)
        if not match:
            return w

        start = match.end()
        rv = w[start:]
        if not rv:
            return w

        # Шаг 1: Perfective gerund -> Adjective/Participle/Verb -> Reflexive
        rv_sub = cls._RU_PERFECTIVEGERUND.sub("", rv)
        if rv_sub != rv:
            rv = rv_sub
        else:
            rv_sub = cls._RU_REFLEXIVE.sub("", rv)
            rv = rv_sub
            rv_adj = cls._RU_ADJECTIVE.sub("", rv)
            if rv_adj != rv:
                rv = rv_adj
                rv = cls._RU_PARTICIPLE.sub("", rv)
            else:
                rv_verb = cls._RU_VERB.sub("", rv)
                if rv_verb != rv:
                    rv = rv_verb
                else:
                    rv = cls._RU_NOUN.sub("", rv)

        # Шаг 2: Удаление "и"
        rv = re.sub(r"и$", "", rv)

        # Шаг 3: Derivational
        if cls._RU_DERIVATIONAL.match(rv):
            rv = re.sub(r"ость?$", "", rv)

        # Шаг 4: Superlative и двойное "нн"
        rv = cls._RU_SUPERLATIVE.sub("", rv)
        rv = re.sub(r"нн$", "н", rv)
        rv = re.sub(r"ь$", "", rv)

        return w[:start] + rv

    @classmethod
    def stem_en(cls, word: str) -> str:
        """Базовый стемминг английских окончаний."""
        w = word.lower()
        if len(w) <= 3:
            return w
        for suffix in ("sses", "ies", "ing", "ed", "ly", "es", "s"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                return w[:-len(suffix)]
        return w

    @classmethod
    def stem(cls, word: str) -> str:
        """Универсальный стеммер по языковому диапазону."""
        if not word:
            return ""
        if re.search(r"[а-яё]", word, re.I):
            return cls.stem_ru(word)
        if re.search(r"[a-z]", word, re.I):
            return cls.stem_en(word)
        return word.lower()

    @classmethod
    def decompose_compound_word(cls, word: str) -> List[str]:
        """Расщепление составных терминов (например: 'видеопамять' -> ['видео', 'память'])."""
        w = word.lower()
        parts: List[str] = []
        for prefix in cls._COMMON_PREFIXES:
            if w.startswith(prefix) and len(w) > len(prefix) + 3:
                root = w[len(prefix):]
                parts.append(prefix)
                parts.append(root)
                break
        return parts

    @classmethod
    def tokenize(cls, text: str) -> List[str]:
        """
        Полный контур токенизации:
        1. Шестнадцатеричные коды (0xc0000005)
        2. Идентификаторы snake_case, kebab-case, camelCase (vram_arbiter, GPUOwner)
        3. Слова с морфологическим стеммингом (RU/EN)
        4. Декомпозиция составных корней
        """
        if not text:
            return []

        text_clean = re.sub(r"[\r\n\t]+", " ", text)
        tokens: List[str] = []
        seen: Set[str] = set()

        def add_token(t: str):
            t_str = t.strip().lower()
            if len(t_str) >= 2 and t_str not in seen:
                seen.add(t_str)
                tokens.append(t_str)

        # 1. Hex-коды ошибок и адресов памяти
        for h in re.findall(r"0x[0-9a-fA-F]+", text_clean):
            add_token(h)

        # 2. Идентификаторы camelCase: GPUOwner -> ['gpu', 'owner']
        camel_tokens = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\W|$)", text_clean)
        for ct in camel_tokens:
            if len(ct) >= 2:
                add_token(ct)
                add_token(cls.stem(ct))

        # 3. Дефисные и подчеркнутые идентификаторы: vram_arbiter -> ['vram', 'arbiter']
        delimited = re.findall(r"[a-zA-Z0-9а-яА-ЯёЁ]+(?:[-_][a-zA-Z0-9а-яА-ЯёЁ]+)+", text_clean)
        for d in delimited:
            add_token(d)
            for sub in re.split(r"[-_]", d):
                if len(sub) >= 2:
                    add_token(sub)
                    add_token(cls.stem(sub))

        # 4. Все буквенно-цифровые слова
        words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text_clean)
        for w in words:
            add_token(w)
            stemmed = cls.stem(w)
            add_token(stemmed)

            # Декомпозиция составных терминов (видеопамять -> видео, память)
            comp_parts = cls.decompose_compound_word(w)
            for cp in comp_parts:
                add_token(cp)
                add_token(cls.stem(cp))

        return tokens