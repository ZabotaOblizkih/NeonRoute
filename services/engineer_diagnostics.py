"""
services/engineer_diagnostics.py
Expert log and error diagnostics system for sandbox execution.
"""

import re
from pathlib import Path
from typing import List, Optional

from services.sandbox_manager import SandboxManager
from services.engineer_prompts import resolve_pip_package


def extract_error_fingerprint(output: str) -> str:
    """Извлекает нормализованный цифровой отпечаток ошибки для детекции зацикливания."""
    if not output or not str(output).strip():
        return ""
    text = str(output).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""

    # Проверка на падение тестов unittest / pytest
    if "FAIL:" in text or "FAILED" in text:
        fail_cases = re.findall(r'FAIL:\s+([^\s]+)', text)
        last_line = lines[-1][:120]
        if fail_cases:
            return f"TEST_FAIL::{','.join(fail_cases)}::{last_line}"
        return f"TEST_FAIL::{last_line}"

    # Стандартный Python Traceback: ищем последний File "...", line X и финальное исключение
    file_line = ""
    for line in reversed(lines):
        m = re.search(r'File\s+["\']([^"\']+)["\'],\s+line\s+(\d+)', line)
        if m:
            file_line = f"{Path(m.group(1)).name}:{m.group(2)}"
            break

    last_line = lines[-1][:150]
    return f"{file_line}::{last_line}" if file_line else last_line


def diagnose_runtime_error(raw_error: str) -> str:
    """
    Compiler Diagnostics / Agent Feedback Loop.
    Анализирует сырой текст ошибки рантайма и обогащает его целевыми
    инженерными подсказками и примерами синтаксиса при обнаружении
    известных антипаттернов API.
    """
    if not raw_error:
        return raw_error

    hints: List[str] = []

    # 1. Недопустимое строковое выравнивание в python-docx
    if "WD_PARAGRAPH_ALIGNMENT" in raw_error or "is not a valid WD_PARAGRAPH_ALIGNMENT" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: В библиотеке python-docx свойство paragraph.alignment НЕ принимает строки ('center', 'left', 'right').\n"
            "Выравнивание задаётся строго через специальное перечисление:\n"
            "from docx.enum.text import WD_ALIGN_PARAGRAPH\n"
            "p.alignment = WD_ALIGN_PARAGRAPH.CENTER  # или LEFT, RIGHT, JUSTIFY"
        )

    # 2. Несуществующие стили в python-docx
    if 'KeyError: "no style with name' in raw_error or "KeyError: 'no style with name" in raw_error or "UserWarning: style lookup by style_id" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: В python-docx нельзя указывать вымышленные стили в doc.add_paragraph(style=...).\n"
            "Используй стандартный стиль 'Normal' (или без параметра style), а подписи и акценты оформляй через runs:\n"
            "p = doc.add_paragraph()\n"
            "run = p.add_run(\"Текст\")\n"
            "run.bold = True"
        )

    # 3. Случайное использование f-строк с русским текстом в фигурных скобках
    if "SyntaxError: f-string: unmatched '{'" in raw_error or "SyntaxError: invalid syntax" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: Проверь f-строки. В Python фигурные скобки f\"{...}\" вычисляют выражения. Не оборачивай русский текст внутри f-строк в фигурные скобки."
        )

    if "EOFError" in raw_error or "EOF when reading a line" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: Ошибка ввода (EOFError).\n"
            "Песочница работает в автономном неинтерактивном режиме (stdin закрыт). Запрещено вызывать input().\n"
            "Замени интерактивный ввод на автоматическое выполнение: передавай аргументы или используй тестовые данные."
        )

    if "FileNotFoundError" in raw_error:
        m_fn = re.search(r"No such file or directory: ['\"]([^'\"]+)['\"]", raw_error)
        missing_f = m_fn.group(1) if m_fn else "файла"
        hints.append(
            f"💡 [Диагностика NeonRoute]: Скрипт попытался прочитать отсутствующий файл '{missing_f}'.\n"
            f"Перед чтением входного файла обязательно создай его с тестовыми данными:\n"
            f"from pathlib import Path\n"
            f"p = Path('{missing_f}')\n"
            f"if not p.exists():\n"
            f"    p.write_text('Пример данных...', encoding='utf-8')"
        )

    m_mod = re.search(r"No module named ['\"]([^'\"]+)['\"]", raw_error)
    if m_mod:
        missing_mod = m_mod.group(1).split('.')[0]
        ws_dir = SandboxManager.get_workspace_dir()
        if (ws_dir / f"{missing_mod}.py").is_file():
            hints.append(
                f"💡 [Диагностика NeonRoute]: Модуль '{missing_mod}' — это локальный файл проекта ({missing_mod}.py).\n"
                f"Убедись, что файл импортируется корректно без опечаток: import {missing_mod}."
            )
        else:
            pip_pkg = resolve_pip_package(missing_mod)
            if pip_pkg:
                hints.append(
                    f"💡 [Диагностика NeonRoute]: Модуль '{missing_mod}' не установлен в окружении.\n"
                    f"Установка через pip во время теста невозможна. "
                    f"Если задачу можно решить средствами стандартной библиотеки — перепиши код без '{missing_mod}'. "
                    f"Если библиотека строго необходима — сохрани корректный код для пакета '{pip_pkg}'."
                )
            else:
                hints.append(
                    f"💡 [Диагностика NeonRoute]: Отсутствует локальный модуль '{missing_mod}'. Создай файл '{missing_mod}.py' с требуемыми классами/функциями через <<TOOL:write_file filename=\"{missing_mod}.py\">>."
                )

    if "'_Cell' object has no attribute 'font'" in raw_error or "'_Cell' object has no attribute 'fill'" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: В библиотеке python-docx у ячеек таблицы (_Cell) нет свойств .font и .fill.\n"
            "Текст форматируется строго через runs абзаца внутри ячейки:\n"
            "p = cell.paragraphs[0]\n"
            "p.text = 'Значение'\n"
            "if p.runs:\n"
            "    p.runs[0].font.bold = True\n"
            "    p.runs[0].font.size = Pt(11)"
        )

    if "No module named 'python_docx'" in raw_error or "No module named \"python_docx\"" in raw_error:
        hints.append("💡 В Python библиотека называется docx, а не python_docx. Используй: from docx import Document или import docx.")

    if "value must be <type 'int'>" in raw_error or "validate_int" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: В python-docx поля и размеры принимают строго целые числа (int) или объекты docx.shared.\n"
            "Не используй float (например, 2.54 * 36). Используй стандартные поля документа либо: from docx.shared import Inches, Pt, Cm."
        )

    if "default_section" in raw_error or "has no attribute 'default_section'" in raw_error:
        hints.append("💡 В python-docx нет default_section и не нужно менять поля. Создавай документ просто через doc = Document(), наполняй текстом и сохраняй doc.save().")

    if "_tkinter.TclError" in raw_error or "bad screen distance" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: Ошибка координат Tkinter Canvas.\n"
            "Методы создания фигур (create_rectangle, create_oval, create_line) принимают строго 4 координаты: (x, y, x + w, y + h).\n"
            "Запрещено передавать 2 координаты или кортеж *pos из двух чисел."
        )

    if "'int' object has no attribute" in raw_error and any(attr in raw_error for attr in ("'x'", "'y'", "'center'", "'colliderect'")):
        hints.append(
            "💡 [Диагностика NeonRoute]: Ошибка Tkinter Canvas — методы create_oval/create_rectangle возвращают числовой ID (int), а не объект.\n"
            "У этого числа нет атрибутов .x и .y. Управляй координатами в классе логики как отдельными числами (self.ball_x, self.ball_y)\n"
            "и обновляй положение на холсте через canvas.coords(self.ball, x1, y1, x2, y2) или canvas.move(self.ball, dx, dy)."
        )

    if "не найдены валидные маркеры SEARCH/REPLACE" in raw_error.lower() or "искомый блок кода не найден" in raw_error.lower():
        hints.append(
            "💡 [Диагностика NeonRoute]: Ошибка в блоке SEARCH для patch_file.\n"
            "Блок SEARCH должен СТРОГО и досимвольно совпадать с фрагментом в текущей версии файла (включая пробелы и отступы).\n"
            "Если не уверен в точном контексте строк — используй <<TOOL:write_file>> для полной перезаписи файла."
        )
    if "файл не найден" in raw_error.lower() or "file not found" in raw_error.lower():
        hints.append(
            "💡 [Диагностика NeonRoute]: Ошибка patch_file — целевой файл не найден на диске.\n"
            "Инструмент patch_file работает ТОЛЬКО для уже существующих на диске файлов.\n"
            "Создавай файлы с нуля через <<TOOL:write_file filename=\"...\">> с полной реализацией."
        )
    if "найдено более одного совпадения" in raw_error.lower():
        hints.append(
            "💡 [Диагностика NeonRoute]: Неоднозначный блок SEARCH.\n"
            "Добавь в блок SEARCH 2-3 соседние строки сверху или снизу, чтобы фрагмент стал уникальным в файле."
        )

    if "no such column" in raw_error.lower() or ("operationalerror" in raw_error.lower() and "column" in raw_error.lower()):
        hints.append(
            "💡 [Диагностика NeonRoute]: Ошибка схемы SQLite (no such column).\n"
            "Команда `CREATE TABLE IF NOT EXISTS` НЕ добавляет новые колонки в уже существующий файл .db на диске.\n"
            "Если схема данных изменилась:\n"
            "- либо пересоздай таблицу при обновлении структуры: `DROP TABLE IF EXISTS ...` перед `CREATE TABLE`,\n"
            "- либо удали устаревший файл .db при инициализации приложения, чтобы база создалась заново с актуальными полями."
        )

    if "'Cell' object has no attribute 'formula'" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: В openpyxl у ячеек нет свойства .formula.\n"
            "Формулы записываются строго в свойство .value строкой с '=':\n"
            "ws['D12'].value = '=SUM(D4:D11)'\n"
            "Замени ВСЕ вхождения .formula на .value."
        )

    if "Style objects are immutable" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: В openpyxl стили ячеек неизменяемы (cell.font.bold = True запрещено).\n"
            "Присваивай стиль новым объектом целиком:\n"
            "cell.font = Font(bold=True)\n"
            "from openpyxl.styles import Font, PatternFill, Alignment, Border, Side"
        )

    if "expected <class 'openpyxl.styles" in raw_error or "CellStyle.alignment should be" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: Свойства fill, alignment, border принимают объекты, а не строки.\n"
            "cell.alignment = Alignment(horizontal='center')\n"
            "cell.fill = PatternFill(start_color='1F497D', end_color='1F497D', fill_type='solid')"
        )

    if "'Worksheet' object has no attribute 'write'" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: В openpyxl нет метода write(). Используй:\n"
            "ws.cell(row=r, column=c).value = значение или ws['A1'].value = значение."
        )

    if not hints:
        return raw_error

    recommendations = "\n\n".join(hints)
    return f"{raw_error}\n\n=== РЕКОМЕНДАЦИИ ПО ИСПРАВЛЕНИЮ ===\n{recommendations}"


def extract_crash_slice(
    source_code: str,
    raw_error: str,
    filename: Optional[str] = None,
    window: int = 12
) -> str:
    """
    Извлекает пронумерованный контекстный срез строк вокруг места сбоя из Traceback.
    Маркирует упавшую строку маркером '> ' и комментарием '# <-- ОШИБКА'.
    """
    if not source_code or not raw_error:
        return ""

    lines = source_code.splitlines()
    if not lines:
        return ""

    target_lineno = None
    clean_name = Path(filename).name if filename else ""

    if clean_name:
        pat = rf'File\s+["\'][^"\']*{re.escape(clean_name)}["\'],\s+line\s+(\d+)'
        matches = list(re.finditer(pat, raw_error, re.IGNORECASE))
        if matches:
            target_lineno = int(matches[-1].group(1))

    if target_lineno is None:
        matches = list(re.finditer(r'File\s+["\']([^"\']+\.pyw?)["\'],\s+line\s+(\d+)', raw_error, re.IGNORECASE))
        if matches:
            target_lineno = int(matches[-1].group(2))

    if target_lineno is None:
        gen_matches = list(re.finditer(r'line\s+(\d+)', raw_error, re.IGNORECASE))
        if gen_matches:
            target_lineno = int(gen_matches[-1].group(1))

    if not target_lineno or target_lineno < 1:
        return ""

    start_idx = max(0, target_lineno - 1 - window)
    end_idx = min(len(lines), target_lineno + window)

    slice_lines = []
    for idx in range(start_idx, end_idx):
        line_num = idx + 1
        marker = " > " if line_num == target_lineno else "   "
        suffix = "  # <-- ОШИБКА" if line_num == target_lineno else ""
        slice_lines.append(f"{line_num:4d}{marker}{lines[idx]}{suffix}")

    return "\n".join(slice_lines)

