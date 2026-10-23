"""
Token Budget Manager for NeonRoute.
Provides token estimation and traffic light styling for character cards and lorebooks.
"""

import re
from typing import Dict, Any, List, Tuple, Optional


class TokenBudgetManager:
    """Manages token estimation and traffic light thresholds for character cards and lorebooks."""

    @staticmethod
    def estimate_text_tokens(text: str) -> int:
        """
        Estimates token count for arbitrary text.
        Cyrillic: ~2.0 chars/token, Latin/other: ~3.3 chars/token.
        Includes 12% safety margin for fragmentation and punctuation.
        """
        if not text:
            return 0
        
        # Подсчет кириллицы и латиницы/спецсимволов
        cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
        other = len(text) - cyrillic
        
        # Реалистичные коэффициенты для Qwen/Llama BPE:
        # Кириллица: ~2.0 символа на токен
        # Латиница/Спецсимволы/Синтаксис: ~3.3 символа на токен
        raw_tokens = (cyrillic / 2.0) + (other / 3.3)
        
        # Буфер безопасности 12% на фрагментацию редких слов и пунктуацию
        return int(raw_tokens * 1.12) + 2

    @classmethod
    def estimate_message_tokens(cls, message: Dict[str, Any]) -> int:
        """
        Estimates token count for a single message dictionary {"role": ..., "content": ...}.
        Adds 5 service tokens for dialogue format tags (<|im_start|>role ... <|im_end|>).
        """
        if not isinstance(message, dict) or not message:
            return 0
        content = message.get("content", "")
        if content is None:
            content = ""
        if isinstance(content, list):
            text_parts = [str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text"]
            # 800 токенов на изображение + 5 токенов на служебные теги диалогового формата
            return cls.estimate_text_tokens(" ".join(text_parts)) + 800 + 5
        if not isinstance(content, str):
            return 0
        # 5 токенов на служебные теги диалогового формата (<|im_start|>role ... <|im_end|>)
        return cls.estimate_text_tokens(content) + 5

    @classmethod
    def estimate_card_budget(
        cls,
        card_fields: Dict[str, Any],
        lore_texts: Optional[List[str]] = None
    ) -> Tuple[int, int]:
        """
        Estimates base card tokens and lore tokens.
        Returns (base_tokens, lore_tokens).
        """
        if isinstance(card_fields, dict):
            base_parts = [str(v) for v in card_fields.values() if v]
        elif isinstance(card_fields, (list, tuple)):
            base_parts = [str(v) for v in card_fields if v]
        else:
            base_parts = [str(card_fields or "")]

        base_text = " ".join(base_parts)
        base_tokens = cls.estimate_text_tokens(base_text)

        lore_tokens = 0
        if lore_texts:
            lore_text = " ".join(str(t) for t in lore_texts if t)
            lore_tokens = cls.estimate_text_tokens(lore_text)

        return base_tokens, lore_tokens

    @staticmethod
    def get_traffic_light(
        effective_tokens: int,
        is_lorebook: bool = False,
        entry_count: int = 0
    ) -> Dict[str, str]:
        """
        Returns traffic light style and status dictionary:
        {"color": str, "border": str, "badge": str, "status": str}
        Thresholds:
        - <= 2300: 🟢 (#00FF66, "rgba(0, 255, 102, 0.3)")
        - <= 4500: 🟡 (#FFE600, "rgba(255, 230, 0, 0.4)")
        - < 7000:  🟡 (#F59E0B, "rgba(245, 158, 11, 0.4)")
        - >= 7000: 🔴 (#FF007F, "rgba(255, 0, 127, 0.5)")
        """
        if effective_tokens <= 2300:
            color = "#00FF66"
            border = "rgba(0, 255, 102, 0.3)"
            badge = "🟢"
            if is_lorebook:
                status = f"🟢 Оптимальный объем лорбука: ~{effective_tokens} токенов ({entry_count} статей)"
            else:
                status = f"🟢 Оптимальный объем: ~{effective_tokens} токенов"
        elif effective_tokens <= 4500:
            color = "#FFE600"
            border = "rgba(255, 230, 0, 0.4)"
            badge = "🟡"
            if is_lorebook:
                status = f"🟡 Умеренный лорбук: ~{effective_tokens} токенов ({entry_count} статей)"
            else:
                status = f"🟡 Умеренный объем: ~{effective_tokens} токенов"
        elif effective_tokens < 7000:
            color = "#F59E0B"
            border = "rgba(245, 158, 11, 0.4)"
            badge = "🟡"
            if is_lorebook:
                status = f"🟡 Плотный лорбук: ~{effective_tokens} токенов ({entry_count} статей)"
            else:
                status = f"🟡 Плотный лор: ~{effective_tokens} токенов"
        else:
            color = "#FF007F"
            border = "rgba(255, 0, 127, 0.5)"
            badge = "🔴"
            if is_lorebook:
                status = f"🔴 Объемный лорбук: ~{effective_tokens} токенов ({entry_count} статей, 7000+)"
            else:
                status = f"🔴 Объемный лор: ~{effective_tokens} токенов (7000+)"

        return {
            "color": color,
            "border": border,
            "badge": badge,
            "status": status
        }
