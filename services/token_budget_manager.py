"""
Token Budget Manager for NeonRoute.
Provides token estimation and traffic light styling for character cards and lorebooks.
Implements the two-circuit context management model:
- Circuit 1: Relative Token Budget (Ratio-based Tier 1 / Tier 2 / Tier 3)
- Circuit 2: Emergency Hardware Guard (VRAM < 600 MB cutoff)
"""

import re
from typing import Dict, Any, List, Tuple, Optional, Union


class ContextTierDecision:
    """Результат расчета двухконтурной модели бюджета контекста."""

    def __init__(
        self,
        history_limit: int,
        max_tokens: int,
        tier: int,
        ratio: float,
        is_emergency: bool,
        target_context_window: int,
        current_prompt_tokens: int,
        free_vram_mb: Optional[int] = None,
    ):
        self.history_limit = history_limit
        self.max_tokens = max_tokens
        self.tier = tier
        self.ratio = ratio
        self.is_emergency = is_emergency
        self.target_context_window = target_context_window
        self.current_prompt_tokens = current_prompt_tokens
        self.free_vram_mb = free_vram_mb

    def __iter__(self):
        # Позволяет распаковку: history_limit, max_tokens = decision
        return iter((self.history_limit, self.max_tokens))

    def __getitem__(self, key: Union[int, str]) -> Any:
        if key == 0 or key == "history_limit":
            return self.history_limit
        if key == 1 or key == "max_tokens":
            return self.max_tokens
        if key == 2 or key == "tier":
            return self.tier
        if key == "ratio":
            return self.ratio
        if key == "is_emergency":
            return self.is_emergency
        if key == "target_context_window":
            return self.target_context_window
        if key == "current_prompt_tokens":
            return self.current_prompt_tokens
        if key == "free_vram_mb":
            return self.free_vram_mb
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "history_limit": self.history_limit,
            "max_tokens": self.max_tokens,
            "ratio": self.ratio,
            "is_emergency": self.is_emergency,
            "target_context_window": self.target_context_window,
            "current_prompt_tokens": self.current_prompt_tokens,
            "free_vram_mb": self.free_vram_mb,
        }

    def __repr__(self) -> str:
        return (
            f"ContextTierDecision(tier={self.tier}, history_limit={self.history_limit}, "
            f"max_tokens={self.max_tokens}, ratio={self.ratio:.4f}, "
            f"is_emergency={self.is_emergency}, target_ctx={self.target_context_window})"
        )


class TokenBudgetManager:
    """Manages token estimation and traffic light thresholds for character cards, lorebooks, and context budgets."""

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
    def estimate_messages_tokens(cls, messages: List[Dict[str, Any]]) -> int:
        """Estimates total token count for a list of messages."""
        if not messages:
            return 0
        return sum(cls.estimate_message_tokens(m) for m in messages if isinstance(m, dict))

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

    @staticmethod
    def get_target_context_window(mode: str = "assistant") -> int:
        """
        Возвращает целевой контекст активного режима из config.MODE_PROFILES:
        - 14336 для Ассистента
        - 24576 для Ролевого режима (RP)
        - 28672 для Инженера
        """
        try:
            from config import MODE_PROFILES
        except ImportError:
            MODE_PROFILES = {}

        norm = (mode or "assistant").lower().strip()
        if norm.startswith("eng"):
            key = "engineer"
        elif norm in ("roleplay", "rp"):
            key = "roleplay"
        else:
            key = "assistant"

        profile = MODE_PROFILES.get(key, {})
        default_len = 28672 if key == "engineer" else (24576 if key == "roleplay" else 14336)
        return int(profile.get("context_length", default_len))

    @classmethod
    def calculate_context_budget(
        cls,
        current_prompt_tokens: Union[int, List[Dict[str, Any]]],
        mode: str = "assistant",
        free_vram_mb: Optional[int] = None,
        target_context_window: Optional[int] = None,
    ) -> ContextTierDecision:
        """
        Двухконтурная модель управления контекстом (Token Budget + Emergency VRAM Guard).

        Контур 1: Относительный бюджет токенов (Основной регулятор)
        - Целевой контекст активного режима из config.MODE_PROFILES (14336 / 24576 / 28672).
        - ratio = current_prompt_tokens / target_context_window.
        - Ступень 1 (ratio < 0.60): История = 60 сообщений, max_tokens = 1024.
        - Ступень 2 (0.60 <= ratio < 0.80): История = 42 сообщения, max_tokens = 768.
        - Ступень 3 (ratio >= 0.80): История = 24 сообщения, max_tokens = 512.

        Контур 2: Аварийный аппаратный предохранитель (Пожарный рубильник)
        - Если system_monitor.get_free_vram_mb() < 600 (критическая просадка из-за внешних программ):
          принудительно переключать параметры на Ступень 3 (24 сообщения, max_tokens = 512) независимо от значения ratio.
        """
        if isinstance(current_prompt_tokens, (list, tuple)):
            prompt_tokens = cls.estimate_messages_tokens(current_prompt_tokens)
        else:
            try:
                prompt_tokens = int(current_prompt_tokens or 0)
            except (ValueError, TypeError):
                prompt_tokens = 0

        prompt_tokens = max(0, prompt_tokens)

        if target_context_window is None or target_context_window <= 0:
            target_ctx = cls.get_target_context_window(mode)
        else:
            target_ctx = int(target_context_window)

        if target_ctx <= 0:
            target_ctx = 14336

        ratio = prompt_tokens / float(target_ctx)

        # Контур 1: Относительный бюджет токенов
        if ratio < 0.60:
            tier = 1
            history_limit = 60
            max_tokens = 1024
        elif ratio < 0.80:
            tier = 2
            history_limit = 42
            max_tokens = 768
        else:
            tier = 3
            history_limit = 24
            max_tokens = 512

        # Контур 2: Аварийный аппаратный предохранитель (< 600 МБ свободной VRAM)
        emergency_threshold = 600
        try:
            from config import EMERGENCY_VRAM_THRESHOLD_MB
            emergency_threshold = int(EMERGENCY_VRAM_THRESHOLD_MB)
        except Exception:
            pass

        is_emergency = False
        if free_vram_mb is not None:
            try:
                vram_val = int(free_vram_mb)
                if vram_val < emergency_threshold:
                    is_emergency = True
                    tier = 3
                    history_limit = 24
                    max_tokens = 512
            except (ValueError, TypeError):
                pass

        return ContextTierDecision(
            history_limit=history_limit,
            max_tokens=max_tokens,
            tier=tier,
            ratio=ratio,
            is_emergency=is_emergency,
            target_context_window=target_ctx,
            current_prompt_tokens=prompt_tokens,
            free_vram_mb=free_vram_mb,
        )

    @classmethod
    def get_context_tier(
        cls,
        current_prompt_tokens: Union[int, List[Dict[str, Any]]],
        mode: str = "assistant",
        free_vram_mb: Optional[int] = None,
        target_context_window: Optional[int] = None,
    ) -> ContextTierDecision:
        """Алиас для calculate_context_budget."""
        return cls.calculate_context_budget(
            current_prompt_tokens=current_prompt_tokens,
            mode=mode,
            free_vram_mb=free_vram_mb,
            target_context_window=target_context_window
        )
