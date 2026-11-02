"""
engineer_stream.py — Потоковый парсинг токенов LLM и извлечение чистого кода для NeonRoute.
"""
import re
from typing import Callable, Optional

def extract_clean_code(raw_text: str) -> str:
    """Извлекает чистый Python-код из ответа модели, удаляя обрамляющие Markdown-теги."""
    if not raw_text:
        return ""
    text = raw_text.strip()
    
    # Регулярка для извлечения содержимого первого валидного python-блока
    m = re.search(r"```(?:python|py)?\s*\n([\s\S]*?)```", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    
    # Если закрывающий блок отсутствует (стрим оборван или модель забыла закрыть)
    m_open = re.search(r"```(?:python|py)?\s*\n([\s\S]*)", text, re.IGNORECASE)
    if m_open:
        return m_open.group(1).strip()
        
    return text

class CodeStreamExtractor:
    """Потоковый конечный автомат для фильтрации и трансляции кода в UI на лету."""
    def __init__(self, on_code_chunk_callback: Optional[Callable[[str], None]] = None):
        self.callback = on_code_chunk_callback
        self.buffer = ""
        self.in_code_block = False
        self.collected_code = []

    def feed(self, token: str) -> None:
        if not token:
            return
        self.buffer += token

        # Отслеживаем вход в блок кода
        if not self.in_code_block:
            if "```" in self.buffer:
                # Нашли маркер открытия
                parts = self.buffer.split("```", 1)
                after = parts[1]
                # Пропускаем имя языка до перевода строки
                if "\n" in after:
                    _, code_start = after.split("\n", 1)
                    self.in_code_block = True
                    self.buffer = ""
                    if code_start:
                        self._emit(code_start)
        else:
            # Мы внутри блока кода — отслеживаем закрывающий маркер
            if "```" in self.buffer:
                code_part, _ = self.buffer.split("```", 1)
                if code_part:
                    self._emit(code_part)
                self.in_code_block = False
                self.buffer = ""
            else:
                # Если буфер безопасен (не содержит частичного ````), сбрасываем в колбэк
                safe_len = len(self.buffer) - 3 if len(self.buffer) > 3 else 0
                if safe_len > 0:
                    emit_part = self.buffer[:safe_len]
                    self.buffer = self.buffer[safe_len:]
                    self._emit(emit_part)

    def _emit(self, text: str) -> None:
        self.collected_code.append(text)
        if self.callback and callable(self.callback):
            try:
                self.callback(text)
            except Exception:
                pass

    def finalize(self) -> str:
        if self.in_code_block and self.buffer:
            self._emit(self.buffer)
            self.buffer = ""
        return "".join(self.collected_code).strip()
