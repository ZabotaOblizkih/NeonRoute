"""
Macro Engine for NeonRoute.
Parses dynamic roleplay macros: {{user}}, {{char}}, {{random: ...}}, {{roll: ...}}, and Tavern/Chub standard tags.
Zero VRAM overhead, pure CPU execution.
"""

import re
import random
from typing import Optional


class MacroEngine:
    _RE_USER = re.compile(r'\{\{\s*(?:user|user_name)\s*\}\}|<(?:USER|USER_NAME)>', re.IGNORECASE)
    _RE_CHAR = re.compile(r'\{\{\s*(?:char|char_name|bot)\s*\}\}|<(?:CHAR|CHAR_NAME|BOT)>', re.IGNORECASE)
    _RE_START = re.compile(r'<START>', re.IGNORECASE)
    _RE_RANDOM = re.compile(r'\{\{\s*random\s*:\s*([^}]+)\}\}', re.IGNORECASE)
    _RE_ROLL = re.compile(r'\{\{\s*roll\s*:\s*(\d+)d(\d+)(?:\s*([+-])\s*(\d+))?\s*\}\}', re.IGNORECASE)

    @classmethod
    def process(
        cls,
        text: Optional[str],
        user_name: str = "User",
        char_name: str = "Assistant"
    ) -> str:
        if not text:
            return ""

        result = str(text)

        # 1. Замена разделителя примеров диалогов Chub / Tavern на маркер сцены
        result = re.sub(r'(?:\r?\n)*\s*<START>\s*(?:\r?\n)*', '\n--- [Пример сцены] ---\n', result, flags=re.IGNORECASE)
        result = result.lstrip('\r\n')

        # 2. Подстановка имён игрока и персонажа
        if user_name:
            result = cls._RE_USER.sub(user_name, result)
        if char_name:
            result = cls._RE_CHAR.sub(char_name, result)

        # 3. Обработка рандомных списков: {{random: opt1, opt2, opt3}}
        def _pick_random(match: re.Match) -> str:
            raw_options = match.group(1)
            options = [opt.strip() for opt in raw_options.split(',') if opt.strip()]
            return random.choice(options) if options else ""

        result = cls._RE_RANDOM.sub(_pick_random, result)

        # 4. Обработка бросков костей: {{roll: XdY[+Z]}}
        def _eval_roll(match: re.Match) -> str:
            count = min(max(1, int(match.group(1))), 20)
            sides = min(max(1, int(match.group(2))), 1000)
            sign = match.group(3)
            mod = int(match.group(4)) if match.group(4) else 0

            total = sum(random.randint(1, sides) for _ in range(count))
            if sign == "+":
                total += mod
            elif sign == "-":
                total -= mod
            return str(total)

        result = cls._RE_ROLL.sub(_eval_roll, result)
        return result
