"""
Speech and Text Regex Filter for NeonRoute.
Cleans thinking tags (<think>, <thought>), normalizes dialogue punctuation,
strips model artifact tokens (<|im_end|>, <|im_start|>), system reminder markers,
model routing flags, and executes character-specific regex transformations with 0 ms CPU overhead.
"""

import re
from typing import Optional, List, Dict, Any, Tuple


class TextRegexFilter:
    _RE_THINK = re.compile(r'<(?:think|thought)>[\s\S]*?</(?:think|thought)>', re.IGNORECASE)
    _RE_UNCLOSED_THINK = re.compile(r'<(?:think|thought)>[\s\S]*$', re.IGNORECASE)
    _RE_THOUGHT_CLEAN = re.compile(r'<(think|analyze|thought|reasoning)>[\s\S]*?(?:</\1>|$)', re.IGNORECASE)
    _RE_IMPLICIT_THOUGHT_CLOSE = re.compile(r'^[\s\S]*?</(think|analyze|thought|reasoning)>', re.IGNORECASE)
    _RE_STRAY_THOUGHT_TAG = re.compile(r'</?(?:think|analyze|thought|reasoning)>', re.IGNORECASE)
    _RE_MODEL_TOKENS = re.compile(r'<\|(?:im_start|im_end|endoftext)\|>', re.IGNORECASE)
    _RE_REMINDER = re.compile(r'(?:\n|\s)*🔔\s*Напоминаю:[^\n]*', re.IGNORECASE)
    _RE_MODEL_FLAG = re.compile(r'(?:\s*--model\s+[a-zA-Z0-9_\-]+)+\s*$', re.IGNORECASE)
    IMAGE_TAG_LABEL_PATTERN = r'(?:IMAGE|IMG)[\s_-]?(?:PROMPT|PROMT|PROP)'
    _RE_IMAGE_TAG = re.compile(rf'\[{IMAGE_TAG_LABEL_PATTERN}:\s*([\s\S]*?)\]', re.IGNORECASE)
    _RE_FENCED_CODE = re.compile(r'```[\s\S]*?```')
    _RE_INLINE_CODE = re.compile(r'`[^`\r\n]+`')
    _RE_CLOSING_THINK_TAG = re.compile(r'</(?:think|analyze|thought|reasoning)>', re.IGNORECASE)
    _RE_OPENING_THINK_TAG = re.compile(r'<(?:think|analyze|thought|reasoning)>', re.IGNORECASE)
    _RE_GEMMA_CHANNEL_BLOCK = re.compile(r'<\|channel>thought[\s\S]*?(?:<channel\|>|<\|channel>(?!thought)|$)', re.IGNORECASE)
    _RE_GEMMA_STRAY_CHANNEL = re.compile(r'<\|channel>[a-zA-Z0-9_:-]*|<channel\|>', re.IGNORECASE)

    RE_IMAGE_TAG = _RE_IMAGE_TAG

    @classmethod
    def mask_markdown_code(cls, text: str) -> Tuple[str, List[Tuple[str, str]]]:
        """
        Временно заменяет закрытые блоки кода (```...```) и инлайн-код (`...`)
        безопасными плейсхолдерами, чтобы санитизаторы не портили Markdown-примеры.
        """
        if not text or "`" not in text:
            return text, []

        prefix = "__NR_MD_CODE_"
        while prefix in text:
            prefix += "_"

        placeholders: List[Tuple[str, str]] = []

        def _replace(match: re.Match) -> str:
            snippet = match.group(0)
            # Не маскируем участок, если он случайно пересекает границу реального блока мыслей
            if cls._RE_CLOSING_THINK_TAG.search(snippet) and not cls._RE_OPENING_THINK_TAG.search(snippet):
                return snippet
            token = f"{prefix}{len(placeholders)}__"
            placeholders.append((token, snippet))
            return token

        masked = cls._RE_FENCED_CODE.sub(_replace, text)
        masked = cls._RE_INLINE_CODE.sub(_replace, masked)
        return masked, placeholders

    @staticmethod
    def restore_markdown_code(text: str, placeholders: List[Tuple[str, str]]) -> str:
        """Возвращает замаскированные блоки Markdown-кода на место."""
        if not text or not placeholders:
            return text
        restored = text
        for token, original in placeholders:
            restored = restored.replace(token, original)
        return restored

    @classmethod
    def strip_thought_blocks(cls, text: Optional[str], *, mask_code: bool = True) -> str:
        """
        Удаляет блоки рассуждений (<think>, <thought>, <reasoning>, <analyze>),
        неявно открытые блоки (^...?</tag>) и незакрытые блоки в конце ответа,
        не затрагивая примеры тегов внутри Markdown-кода.
        """
        if not text:
            return ""

        placeholders: List[Tuple[str, str]] = []
        work = str(text)
        if mask_code:
            work, placeholders = cls.mask_markdown_code(work)

        work = cls._RE_THOUGHT_CLEAN.sub('', work)
        work = cls._RE_IMPLICIT_THOUGHT_CLOSE.sub('', work)
        work = cls._RE_GEMMA_CHANNEL_BLOCK.sub('', work)
        work = cls._RE_STRAY_THOUGHT_TAG.sub('', work)
        work = cls._RE_GEMMA_STRAY_CHANNEL.sub('', work)

        if mask_code:
            work = cls.restore_markdown_code(work, placeholders)
        return work

    @classmethod
    def extract_image_prompt(
        cls,
        text: Optional[str],
        strip_thoughts: bool = True,
        allow_markdown_fallback: bool = False,
    ) -> Tuple[Optional[str], str]:
        """
        Извлекает последний валидный тег [IMAGE_PROMPT: ...] вне блоков мыслей и Markdown-кода.
        При allow_markdown_fallback=True, если тег не найден вне кода, допускает извлечение
        из обёртки обратными кавычками (после удаления блоков мыслей).
        Возвращает кортеж: (extracted_prompt, clean_text)
        """
        if not text:
            return None, ""

        masked_text, placeholders = cls.mask_markdown_code(str(text))
        if strip_thoughts:
            masked_text = cls.strip_thought_blocks(masked_text, mask_code=False)

        matches = list(cls._RE_IMAGE_TAG.finditer(masked_text))
        if matches:
            last_match = matches[-1]
            raw_prompt = cls.restore_markdown_code(last_match.group(1), placeholders)
            extracted_prompt = re.sub(r'^[\s\*_\-\#]+', '', raw_prompt).strip()
            clean_masked = cls._RE_IMAGE_TAG.sub('', masked_text)
            clean_text = cls.restore_markdown_code(clean_masked, placeholders)
            clean_text = re.sub(r'(?:\n\s*)(?:Элария|Assistant|Пользователь|User)\s*:\s*$', '', clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()
            return extracted_prompt, clean_text

        restored_text = cls.restore_markdown_code(masked_text, placeholders)
        if allow_markdown_fallback:
            raw_matches = list(cls._RE_IMAGE_TAG.finditer(restored_text))
            if raw_matches:
                last_match = raw_matches[-1]
                extracted_prompt = re.sub(r'^[\s\*_\-\#]+', '', last_match.group(1)).strip()
                unwrapped = re.sub(
                    rf'`{{1,3}}(?:[a-zA-Z0-9_-]+\s*\n)?\s*\[{cls.IMAGE_TAG_LABEL_PATTERN}:\s*[\s\S]*?\]\s*`{{1,3}}',
                    '',
                    restored_text,
                    flags=re.IGNORECASE,
                )
                unwrapped = cls._RE_IMAGE_TAG.sub('', unwrapped)
                unwrapped = re.sub(r'(?:\n\s*)(?:Элария|Assistant|Пользователь|User)\s*:\s*$', '', unwrapped, flags=re.IGNORECASE)
                unwrapped = re.sub(r'\n{3,}', '\n\n', unwrapped).strip()
                return extracted_prompt, unwrapped

        return None, restored_text.strip()

    @classmethod
    def strip_image_tags(cls, text: Optional[str]) -> str:
        """
        Безопасно удаляет любые вариации арт-тегов ([IMAGE_PROMPT: ...], [IMAGE_PROP: ...] и т.д.)
        из текста с сохранением форматирования.
        """
        if not text:
            return ""
        cleaned = re.sub(
            rf'`{{1,3}}(?:[a-zA-Z0-9_-]+\s*\n)?\s*\[{cls.IMAGE_TAG_LABEL_PATTERN}:\s*[\s\S]*?\]\s*`{{1,3}}',
            '',
            str(text),
            flags=re.IGNORECASE,
        )
        cleaned = cls._RE_IMAGE_TAG.sub('', cleaned)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    @classmethod
    def process_output(
        cls,
        text: Optional[str],
        custom_rules: Optional[List[Dict[str, str]]] = None,
        format_dialogue_dashes: bool = True,
    ) -> str:
        if not text:
            return ""

        result, placeholders = cls.mask_markdown_code(str(text))

        # 1. Clean reasoning tags
        result = cls.strip_thought_blocks(result, mask_code=False)

        # 2. Clean system tokens and artifacts
        result = cls._RE_MODEL_TOKENS.sub('', result)
        result = cls._RE_REMINDER.sub('', result)
        result = cls._RE_MODEL_FLAG.sub('', result)

        # 3. Format dialogue dashes (только в ролевом режиме, чтобы не ломать Markdown-списки)
        if format_dialogue_dashes:
            result = re.sub(r'(^|\n)\s*-\s+', r'\1— ', result)

        # 4. Apply custom character regex presets if present
        if custom_rules and isinstance(custom_rules, list):
            for rule in custom_rules:
                if not isinstance(rule, dict):
                    continue
                pattern = rule.get("pattern") or rule.get("regex") or rule.get("find")
                replacement = rule.get("replacement") or rule.get("replace") or ""
                if pattern:
                    try:
                        result = re.sub(pattern, replacement, result)
                    except Exception:
                        pass

        result = cls.restore_markdown_code(result, placeholders)
        return result.strip()
