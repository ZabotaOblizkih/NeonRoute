"""
services/engineer_diagnostics.py
Expert log and error diagnostics system for sandbox execution.
"""

import re
from pathlib import Path
from typing import List, Optional

from services.sandbox_manager import SandboxManager
from services.engineer_prompts import resolve_pip_package


def clean_traceback(raw_tb: str, max_frames: int = 4) -> str:
    """
    Очищает стектрейс Python от инфраструктурного шума песочницы.
    Отсекает вызовы runpy.py, sandbox_runner.py, sandbox_guard.py, <frozen ...> и site-packages,
    оставляя только пользовательские фреймы скрипта песочницы и финальную строку ошибки.
    """
    if not raw_tb or not str(raw_tb).strip():
        return ""

    lines = str(raw_tb).splitlines()
    filtered_blocks: list = []
    current_block: list = []
    last_error_line = ""

    # Игнорируемые инфраструктурные модули
    ignored_patterns = (
        "sandbox_runner.py",
        "sandbox_guard.py",
        "runpy.py",
        "<frozen",
        "site-packages",
        "importlib"
    )

    is_in_traceback = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Traceback (most recent call last):"):
            is_in_traceback = True
            continue

        if is_in_traceback:
            if line.startswith("  File "):
                if current_block:
                    filtered_blocks.append(current_block)
                    current_block = []
                # Проверяем, не относится ли файл к системному раннеру
                if not any(ign in line for ign in ignored_patterns):
                    current_block.append(line)
            elif current_block:
                current_block.append(line)
            elif not line.startswith(" ") and stripped:
                # Финальная строка исключения (например: NameError: ...)
                last_error_line = stripped
        else:
            if stripped:
                last_error_line = stripped

    if current_block:
        filtered_blocks.append(current_block)

    if not is_in_traceback:
        return raw_tb.strip()

    # Если все фреймы оказались системными — возвращаем хотя бы текст ошибки
    if not filtered_blocks:
        return last_error_line or raw_tb.strip()

    # Берем последние max_frames пользовательских вызовов
    result_lines = ["Traceback (most recent call last, sandbox filtered):"]
    for block in filtered_blocks[-max_frames:]:
        result_lines.extend(block)

    if last_error_line:
        result_lines.append(last_error_line)

    return "\n".join(result_lines)


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
        fail_cases = re.findall(r'(?:FAIL:|FAILED)\s+([^\s\r\n\[]+)', text)
        clean_fails = [f for f in fail_cases if not f.startswith("[")]
        err_type = ""
        m_err = re.search(r'(?:E\s+|FAILED\s+.*?-\s+)([A-Za-z0-9_.]+(?:Error|Exception)?)', text)
        if m_err:
            err_type = m_err.group(1)
        norm_last = re.sub(r'\s+in\s+\d+\.\d+s', '', lines[-1][:120])
        fails_str = ','.join(clean_fails)
        if clean_fails:
            return f"TEST_FAIL::{fails_str}::{err_type or norm_last}"
        return f"TEST_FAIL::{err_type or norm_last}"

    # Стандартный Python Traceback: ищем последний File "...", line X и финальное исключение
    file_line = ""
    file_name = ""
    for line in reversed(lines):
        m = re.search(r'File\s+["\']([^"\']+)["\'],\s+line\s+(\d+)', line)
        if m:
            fname = Path(m.group(1)).name
            if fname not in ("sandbox_runner.py", "sandbox_guard.py", "runpy.py"):
                file_name = fname
                file_line = f"{fname}:{m.group(2)}"
                break

    last_line = lines[-1][:150]
    if ("ModuleNotFoundError" in last_line or "ImportError" in last_line) and file_name:
        return f"{file_name}::{last_line}"
    return f"{file_line}::{last_line}" if file_line else last_line


def diagnose_runtime_error(raw_error: str) -> str:
    """
    Compiler Diagnostics / Agent Feedback Loop.
    Анализирует сырой текст ошибки рантайма и обогащает его целевыми
    инженерными подсказками и примерами синтаксиса при обнаружении
    известных антипаттернов API.
    """
    raw_error = clean_traceback(raw_error)

    if not raw_error:
        return raw_error

    if "the following arguments are required:" in raw_error:
        m_arg = re.search(r"the following arguments are required:\s*([^\r\n]+)", raw_error)
        req_args = m_arg.group(1).strip() if m_arg else "позиционные аргументы"
        return (
            f"[СБОЙ АВТОНОМНОГО ЗАПУСКА CLI]: Скрипт требует обязательный аргумент {req_args}, но песочница запускает его без параметров.\n"
            f"Решение: в argparse добавь для аргумента '{req_args}' параметры nargs='?' и default='demo.txt', "
            f"а перед его чтением создай тестовый файл 'demo.txt', чтобы скрипт завершался с кодом 0."
        )

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
        if missing_mod.lower() == "pil":
            hints.append("💡 [Диагностика NeonRoute]: Модуль называется PIL (заглавными буквами). Используй: from PIL import Image или import PIL.")
        elif missing_mod.lower() == "cv":
            hints.append("💡 [Диагностика NeonRoute]: В Python библиотека OpenCV импортируется как cv2: import cv2.")
        else:
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
                        f"💡 [Диагностика NeonRoute]: Модуль '{missing_mod}' не найден в рабочей папке и не входит в установленные пакеты.\n"
                        f"- Либо реализуй нужные классы и функции прямо внутри основного файла и убери `import {missing_mod}`,\n"
                        f"- либо создай недостающий файл '{missing_mod}.py' через `<<TOOL:write_file filename=\"{missing_mod}.py\">>` (если это собственный модуль проекта).\n"
                        f"Если это сторонняя библиотека — НЕ создавай фейковый файл '{missing_mod}.py', а перепиши логику на стандартную библиотеку / доступные пакеты."
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

    if "module 'tkinter' has no attribute" in raw_error or 'module "tkinter" has no attribute' in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: В Tkinter нет констант клавиш вроде tk.SPACE или tk.LEFT.\n"
            "Привязывай клавиши строковыми событиями: '<space>', '<Left>', '<Right>', '<Up>', '<Down>', '<Return>'."
        )

    if "'method' object is not subscriptable" in raw_error or '"method" object is not subscriptable' in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: Конфликт имён с tk.Tk.keys().\n"
            "У объекта окна tk.Tk уже есть встроенный метод .keys(). "
            "Не называй свой словарь состояний клавиш self.keys (используй self.pressed_keys или self.key_states)."
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

    if "pygame.error" in raw_error and any(k in raw_error.lower() for k in ("couldn't open", "no such file", "unable to open", "file not found", "failed loading")):
        hints.append(
            "💡 [Диагностика NeonRoute]: Ошибка загрузки ресурса в Pygame (pygame.error).\n"
            "В автономной песочнице нет внешних файлов картинок (.png), шрифтов (.ttf) или звуков (.wav).\n"
            "Рисуй игровые объекты примитивами (pygame.draw.rect, pygame.draw.circle, pygame.draw.polygon, pygame.Surface) "
            "и используй системные шрифты: pygame.font.SysFont(None, 32)."
        )

    if "PermissionError" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: Ошибка доступа к файлу (PermissionError).\n"
            "Файл заблокирован (возможно, открыт в Excel/Word) либо путь недоступен для записи. "
            "Сохраняй файл в текущую рабочую папку под новым или безопасным именем."
        )

    if re.search(r"missing \d+ required positional|takes \d+ positional|unexpected keyword argument", raw_error):
        hints.append(
            "💡 [Диагностика NeonRoute]: Ошибка сигнатуры функции/метода (TypeError).\n"
            "Несоответствие аргументов вызова и объявления функции/метода. "
            "Сверь список параметров в def (включая self) с аргументами в точке вызова."
        )

    if "AttributeError:" in raw_error and "has no attribute" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: Ошибка обращения к атрибуту (AttributeError).\n"
            "Обращение к несуществующему полю/методу объекта. "
            "Проверь инициализацию всех атрибутов в __init__ и точное имя метода."
        )

    if "IndexError" in raw_error:
        hints.append(
            "💡 [Диагностика NeonRoute]: Ошибка индексации (IndexError).\n"
            "Выход за границы списка/коллекции. Проверяй len(...) и непустоту списка перед обращением по индексу."
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

