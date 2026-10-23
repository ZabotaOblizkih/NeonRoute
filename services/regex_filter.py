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
    _RE_MODEL_TOKENS = re.compile(r'<\|(?:im_start|im_end|endoftext)\|>', re.IGNORECASE)
    _RE_REMINDER = re.compile(r'(?:\n|\s)*🔔\s*Напоминаю:[^\n]*', re.IGNORECASE)
    _RE_MODEL_FLAG = re.compile(r'(?:\s*--model\s+[a-zA-Z0-9_\-]+)+\s*$', re.IGNORECASE)
    _RE_IMAGE_TAG = re.compile(r'\[IMAGE[\s_-]PROMPT:\s*([\s\S]*?)\]', re.IGNORECASE)

    RE_IMAGE_TAG = _RE_IMAGE_TAG

    @classmethod
    def extract_image_prompt(cls, text: Optional[str]) -> Tuple[Optional[str], str]:
        """
        Извлекает тег [IMAGE_PROMPT: ...] и возвращает кортеж:
        (extracted_prompt, clean_text)
        """
        if not text:
            return None, ""
        m = cls._RE_IMAGE_TAG.search(text)
        if m:
            raw_prompt = re.sub(r'^[\s\*_\-\#]+', '', m.group(1)).strip()
            clean_text = cls._RE_IMAGE_TAG.sub('', text)
            clean_text = re.sub(r'(?:\n\s*)(?:Элария|Assistant|Пользователь|User)\s*:\s*$', '', clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()
            return raw_prompt, clean_text
        return None, text.strip()

    @classmethod
    def process_output(cls, text: Optional[str], custom_rules: Optional[List[Dict[str, str]]] = None) -> str:
        if not text:
            return ""

        result = str(text)

        # 1. Clean reasoning tags
        result = cls._RE_THINK.sub('', result)
        result = cls._RE_UNCLOSED_THINK.sub('', result)

        # 2. Clean system tokens and artifacts
        result = cls._RE_MODEL_TOKENS.sub('', result)
        result = cls._RE_REMINDER.sub('', result)
        result = cls._RE_MODEL_FLAG.sub('', result)

        # 3. Format dialogue dashes (convert standard hyphen at line start to em-dash)
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

        return result.strip()
