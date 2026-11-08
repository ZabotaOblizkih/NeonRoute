"""
Thought Stream Parser and State Machine for NeonRoute Assistant.
Parses LLM streaming output with <think>...</think> tags on CPU (0 MB VRAM).
Features:
1. States: NORMAL_TEXT, IN_THINK, THINK_FINISHED.
2. Safe boundary buffering for tags broken across network chunks (e.g. '<th' + 'ink>').
3. Support for implicit opening <think> (when injected by llama-server template) and multiple <think> blocks.
4. Fail-safe circuit breaker: forces </think> closure if thought length exceeds 16384 chars / ~4096 tokens.
5. Duration and token count calculation for ThoughtSpoilerWidget and session persistence.
"""

import re
import time
from enum import Enum
from typing import List, Tuple, Dict, Any, Optional


class ThinkParserState(str, Enum):
    NORMAL_TEXT = "NORMAL_TEXT"
    IN_THINK = "IN_THINK"
    THINK_FINISHED = "THINK_FINISHED"


class ThoughtStreamParser:
    """
    Потоковая state-machine для перехвата и разделения рассуждений <think>...</think>
    и чистового ответа модели в реальном времени.
    """

    MAX_THINK_CHARS = 16384
    MAX_THINK_TOKENS = 4096

    THINK_OPEN_TAG = "<think>"
    THINK_CLOSE_TAG = "</think>"
    OPEN_TAGS = ("<think>", "<thought>", "<reasoning>", "<analyze>")
    CLOSE_TAGS = ("</think>", "</thought>", "</reasoning>", "</analyze>")
    ALL_TAGS = OPEN_TAGS + CLOSE_TAGS

    def __init__(self):
        self.state: ThinkParserState = ThinkParserState.NORMAL_TEXT
        self.buffer: str = ""
        self.thought_text: str = ""
        self.normal_text: str = ""

        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.duration: float = 0.0
        self.token_count: int = 0
        self._failsafe_triggered: bool = False

    def feed(self, chunk: str) -> List[Tuple[str, str]]:
        """
        Принимает очередной чанк потока LLM.
        Возвращает последовательность событий:
        - ("start_think", "")
        - ("thought_chunk", thought_str)
        - ("reset_normal", "")
        - ("end_think", "")
        - ("normal_chunk", normal_str)
        """
        if not chunk:
            return []

        events: List[Tuple[str, str]] = []
        combined = self.buffer + chunk
        self.buffer = ""

        while combined:
            if self.state in (ThinkParserState.NORMAL_TEXT, ThinkParserState.THINK_FINISHED):
                if self._failsafe_triggered:
                    # Если сработал предохранитель 16K, больше в IN_THINK не входим, вычищая шальные теги
                    clean_c = re.sub(r"</?(think|thought|reasoning|analyze)>", "", combined, flags=re.IGNORECASE)
                    if clean_c:
                        self.normal_text += clean_c
                        events.append(("normal_chunk", clean_c))
                    break

                open_match = re.search(r"<(think|thought|reasoning|analyze)>", combined, re.IGNORECASE)
                close_match = re.search(r"</(think|thought|reasoning|analyze)>", combined, re.IGNORECASE)

                # Сценарий неявного <think> (когда llama-server вставил <think> в промпт, и в потоке пришел </think>)
                if (
                    self.state == ThinkParserState.NORMAL_TEXT
                    and close_match
                    and (not open_match or close_match.start() < open_match.start())
                ):
                    pre_thought = self.normal_text + combined[:close_match.start()]
                    self.normal_text = ""
                    if self.start_time is None:
                        self.start_time = time.time()
                    events.append(("start_think", ""))
                    if self.thought_text and pre_thought:
                        self.thought_text += "\n" + pre_thought
                    else:
                        self.thought_text += pre_thought
                    if pre_thought:
                        events.append(("thought_chunk", pre_thought))
                    events.append(("reset_normal", ""))
                    self._finalize_timing()
                    events.append(("end_think", ""))
                    self.state = ThinkParserState.THINK_FINISHED
                    combined = combined[close_match.end():]
                    continue

                if open_match:
                    pre_text = combined[:open_match.start()]
                    if pre_text:
                        if self.state == ThinkParserState.NORMAL_TEXT or pre_text.strip():
                            self.normal_text += pre_text
                            events.append(("normal_chunk", pre_text))

                    was_finished = (self.state == ThinkParserState.THINK_FINISHED)
                    self.state = ThinkParserState.IN_THINK
                    if self.start_time is None:
                        self.start_time = time.time()

                    if was_finished and self.thought_text:
                        self.thought_text += "\n"
                        events.append(("thought_chunk", "\n"))
                    else:
                        events.append(("start_think", ""))

                    combined = combined[open_match.end():]
                    continue
                else:
                    # Проверяем, не заканчивается ли combined префиксом любого открывающего или закрывающего тега
                    held = False
                    comb_lower = combined.lower()
                    max_k = 0
                    for tag in self.ALL_TAGS:
                        for k in range(min(len(tag) - 1, len(combined)), 0, -1):
                            tail = comb_lower[-k:]
                            if tag.startswith(tail):
                                if k > max_k:
                                    max_k = k
                    if max_k > 0:
                        self.buffer = combined[-max_k:]
                        to_emit = combined[:-max_k]
                        if to_emit:
                            self.normal_text += to_emit
                            events.append(("normal_chunk", to_emit))
                        held = True

                    if not held:
                        self.normal_text += combined
                        events.append(("normal_chunk", combined))
                    break

            elif self.state == ThinkParserState.IN_THINK:
                # Игнорируем вложенные открывающие <think> внутри уже открытого блока мыслей
                combined = re.sub(r"<(think|thought|reasoning|analyze)>", "", combined, flags=re.IGNORECASE)
                if not combined:
                    break

                # Ищем закрывающий тег </think> или </thought>
                match = re.search(r"</(think|thought|reasoning|analyze)>", combined, re.IGNORECASE)
                if match:
                    th_part = combined[:match.start()]
                    if th_part:
                        self.thought_text += th_part
                        events.append(("thought_chunk", th_part))

                    self._finalize_timing()
                    events.append(("end_think", ""))
                    self.state = ThinkParserState.THINK_FINISHED

                    combined = combined[match.end():]
                    continue
                else:
                    # Проверяем fail-safe лимит по объему мыслей
                    curr_len = len(self.thought_text)
                    if curr_len + len(combined) >= self.MAX_THINK_CHARS:
                        cutoff = max(0, self.MAX_THINK_CHARS - curr_len)
                        th_part = combined[:cutoff]
                        norm_part = combined[cutoff:]

                        if th_part:
                            self.thought_text += th_part
                            events.append(("thought_chunk", th_part))

                        self._failsafe_triggered = True
                        self._finalize_timing()
                        events.append(("end_think", ""))
                        self.state = ThinkParserState.THINK_FINISHED

                        if norm_part:
                            self.normal_text += norm_part
                            events.append(("normal_chunk", norm_part))
                        break

                    # Проверяем, не заканчивается ли combined префиксом закрывающего или открывающего тега
                    held = False
                    comb_lower = combined.lower()
                    max_k = 0
                    for tag in self.ALL_TAGS:
                        for k in range(min(len(tag) - 1, len(combined)), 0, -1):
                            tail = comb_lower[-k:]
                            if tag.startswith(tail):
                                if k > max_k:
                                    max_k = k
                    if max_k > 0:
                        self.buffer = combined[-max_k:]
                        to_emit = combined[:-max_k]
                        if to_emit:
                            self.thought_text += to_emit
                            events.append(("thought_chunk", to_emit))
                        held = True

                    if not held:
                        self.thought_text += combined
                        events.append(("thought_chunk", combined))
                    break

        return events

    def finalize(self) -> List[Tuple[str, str]]:
        """
        Завершает работу парсера, сбрасывает остаточный буфер.
        """
        events: List[Tuple[str, str]] = []
        if self.buffer:
            if self.state == ThinkParserState.IN_THINK:
                self.thought_text += self.buffer
                events.append(("thought_chunk", self.buffer))
                self._finalize_timing()
                events.append(("end_think", ""))
                self.state = ThinkParserState.THINK_FINISHED
            else:
                self.normal_text += self.buffer
                events.append(("normal_chunk", self.buffer))
            self.buffer = ""
        elif self.state == ThinkParserState.IN_THINK:
            self._finalize_timing()
            events.append(("end_think", ""))
            self.state = ThinkParserState.THINK_FINISHED

        return events

    def _finalize_timing(self):
        self.end_time = time.time()
        if self.start_time is not None:
            self.duration = max(0.1, round(self.end_time - self.start_time, 2))
        else:
            self.duration = 0.0

        raw_thought = self.thought_text.strip()
        self.token_count = max(1, len(raw_thought) // 4) if raw_thought else 0

    def get_thought_data(self) -> Optional[Dict[str, Any]]:
        """
        Возвращает метаданные мыслей для сохранения в session.json
        или None, если блок мыслей отсутствовал.
        """
        clean = self.thought_text.strip()
        if not clean:
            return None
        return {
            "content": clean,
            "duration": self.duration,
            "token_count": self.token_count
        }
