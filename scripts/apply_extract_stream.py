import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 1. Создаем services/engineer_stream.py
stream_file = PROJECT_ROOT / "services" / "engineer_stream.py"
stream_code = """\"\"\"
engineer_stream.py — Потоковый парсинг токенов LLM и извлечение чистого кода для NeonRoute.
\"\"\"
import re
from typing import Callable, Optional

def extract_clean_code(raw_text: str) -> str:
    \"\"\"Извлекает чистый Python-код из ответа модели, удаляя обрамляющие Markdown-теги.\"\"\"
    if not raw_text:
        return ""
    text = raw_text.strip()
    
    # Регулярка для извлечения содержимого первого валидного python-блока
    m = re.search(r"```(?:python|py)?\s*\\n([\\s\\S]*?)```", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    
    # Если закрывающий блок отсутствует (стрим оборван или модель забыла закрыть)
    m_open = re.search(r"```(?:python|py)?\s*\\n([\\s\\S]*)", text, re.IGNORECASE)
    if m_open:
        return m_open.group(1).strip()
        
    return text

class CodeStreamExtractor:
    \"\"\"Потоковый конечный автомат для фильтрации и трансляции кода в UI на лету.\"\"\"
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
                if "\\n" in after:
                    _, code_start = after.split("\\n", 1)
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
"""

stream_file.write_text(stream_code.strip() + "\n", encoding="utf-8")
print(f"[OK] Создан модуль: {stream_file.relative_to(PROJECT_ROOT)}")

# 2. Обновляем services/subagent_engineer.py, заменяя inline-объявления на импорт
sub_file = PROJECT_ROOT / "services" / "subagent_engineer.py"
sub_content = sub_file.read_text(encoding="utf-8")

# Проверяем, что CodeStreamExtractor и extract_clean_code импортируются
if "from services.engineer_stream import" not in sub_content:
    # Удаляем старые строки 45-96
    lines = sub_content.splitlines()
    # Ищем начало CodeStreamExtractor и конец extract_clean_code
    start_idx = None
    end_idx = None
    for i, l in enumerate(lines):
        if l.startswith("class CodeStreamExtractor:"):
            start_idx = i
        if start_idx is not None and l.startswith("class SubagentEngineer:"):
            end_idx = i
            break
            
    if start_idx is not None and end_idx is not None:
        new_lines = (
            lines[:start_idx]
            + ["from services.engineer_stream import CodeStreamExtractor, extract_clean_code", ""]
            + lines[end_idx:]
        )
        sub_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        print(f"[OK] services/subagent_engineer.py обновлен (вынесены CodeStreamExtractor и extract_clean_code, -{end_idx - start_idx} строк).")
    else:
        print("[WARN] Не удалось точно определить границы классов в subagent_engineer.py!")
        sys.exit(1)

# 3. Запускаем верификацию смок-тестов
print("\n>>> Запуск смок-тестов...")
res = subprocess.run([sys.executable, "-m", "pytest", "tests/smoke/test_smoke_subagent_engineer.py", "-v"], capture_output=True, text=True)
print(res.stdout)
if res.returncode != 0:
    print(res.stderr)
    print("[FAIL] Смок-тесты провалены!")
    sys.exit(res.returncode)

print("[PASS] Все смок-тесты пройдены на 100%!")
