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
        self._closing_mode: Optional[str] = None
        self._strip_md_wrapper: bool = False

    def feed(self, token: str) -> None:
        if not token:
            return
        self.buffer += token

        # Отслеживаем вход в блок кода (Markdown ``` или тег <<TOOL:write_file>>)
        if not self.in_code_block:
            m_tool = re.search(r'<<TOOL:write_file\b[^>]*>>[ \t]*\r?\n?', self.buffer, re.IGNORECASE)
            tool_pos = m_tool.start() if m_tool else -1
            md_pos = self.buffer.find("```")

            if tool_pos >= 0 and (md_pos == -1 or tool_pos < md_pos):
                self.in_code_block = True
                self._closing_mode = "tool"
                self._strip_md_wrapper = True
                self.buffer = self.buffer[m_tool.end():]
            elif md_pos >= 0:
                parts = self.buffer.split("```", 1)
                after = parts[1]
                if "\n" in after:
                    _, code_start = after.split("\n", 1)
                    self.in_code_block = True
                    self._closing_mode = "md"
                    self._strip_md_wrapper = False
                    self.buffer = code_start
                else:
                    return
            else:
                return

        # Если мы внутри блока кода
        if self._closing_mode == "md":
            if "```" in self.buffer:
                code_part, _ = self.buffer.split("```", 1)
                if code_part:
                    self._emit(code_part)
                self.in_code_block = False
                self._closing_mode = None
                self.buffer = ""
            else:
                safe_len = len(self.buffer) - 3 if len(self.buffer) > 3 else 0
                if safe_len > 0:
                    emit_part = self.buffer[:safe_len]
                    self.buffer = self.buffer[safe_len:]
                    self._emit(emit_part)
        elif self._closing_mode == "tool":
            if self._strip_md_wrapper:
                m_wrap = re.match(r'^[ \t]*```(?:python|py)?[ \t]*\r?\n', self.buffer, re.IGNORECASE)
                if m_wrap:
                    self.buffer = self.buffer[m_wrap.end():]
                    self._strip_md_wrapper = False
                elif re.match(r'^[ \t]*`{1,3}[a-zA-Z]*$', self.buffer):
                    return
                elif len(self.buffer) > 0 and not self.buffer.lstrip().startswith("`"):
                    self._strip_md_wrapper = False

            m_close = re.search(r'(?:<<?/+TOOL(?::write_file)?>>?|`+/+TOOL(?::write_file)?`+|(?=<<TOOL:\w+))', self.buffer, re.IGNORECASE)
            if m_close:
                code_part = self.buffer[:m_close.start()]
                code_part = re.sub(r'[\r\n]+```[ \t]*$', '', code_part)
                code_part = re.sub(r'```[ \t]*$', '', code_part)
                if code_part:
                    self._emit(code_part)
                self.in_code_block = False
                self._closing_mode = None
                self._strip_md_wrapper = False
                self.buffer = ""
            else:
                safe_len = len(self.buffer) - 25 if len(self.buffer) > 25 else 0
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
            if self._closing_mode == "tool":
                m_close = re.search(r'(?:<<?/+TOOL(?::write_file)?>>?|`+/+TOOL(?::write_file)?`+|(?=<<TOOL:\w+))', self.buffer, re.IGNORECASE)
                if m_close:
                    code_part = self.buffer[:m_close.start()]
                else:
                    code_part = re.sub(r'<<?/?(?:TOOL(?::write_file)?)?[^>]*$', '', self.buffer, flags=re.IGNORECASE)
                code_part = re.sub(r'[\r\n]+```[ \t]*$', '', code_part)
                code_part = re.sub(r'```[ \t]*$', '', code_part)
                if code_part:
                    self._emit(code_part)
            else:
                if "```" in self.buffer:
                    code_part, _ = self.buffer.split("```", 1)
                    if code_part:
                        self._emit(code_part)
                else:
                    self._emit(self.buffer)
            self.buffer = ""
        return "".join(self.collected_code).strip()
