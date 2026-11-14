"""
Markdown Table to Qt Rich Text Parser for NeonRoute.
Transforms GFM (GitHub Flavored Markdown) tables into single-line HTML tables
optimized for Qt 6 Rich Text / QTextDocument layout engine with cyberpunk styling.
"""

import re
from typing import List

_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?$")
_TABLE_HINT_RE = re.compile(
    r"(?:<table\b|(?:\n|^)\s*\|[^\n]+\|\s*\r?\n\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?)",
    re.IGNORECASE
)


def contains_markdown_table(text: str) -> bool:
    """
    Быстрая проверка, содержит ли текст уже сгенерированный тег <table
    или Markdown-таблицу (связка строки с пайпами и разделителя |:---|).
    Используется для оптимизации пересчета геометрии бабла (Active Tier 4).
    """
    if not text:
        return False
    if "<table" in text.lower():
        return True
    if "|" not in text:
        return False
    return bool(_TABLE_HINT_RE.search(text))


def _is_table_sep(line: str) -> bool:
    s = line.strip()
    if not s or "|" not in s:
        return False
    return bool(_SEP_RE.match(s))


def _is_table_row(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    # Защита от экранированных пайпов \|
    clean = s.replace(r"\|", "").replace("&vert;", "")
    if clean.startswith("|") and clean.endswith("|") and clean.count("|") >= 2:
        return True
    # Поддержка незавершенной последней строки во время активного стриминга токенов
    if clean.startswith("|") and clean.count("|") >= 2:
        return True
    return False


def _split_row_cells(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    token = "\x00ESCAPED_PIPE\x00"
    s = s.replace(r"\|", token).replace("&vert;", token)
    raw = s.split("|")
    return [c.replace(token, "|").strip() for c in raw]


def _parse_alignments(sep_line: str) -> List[str]:
    cells = _split_row_cells(sep_line)
    aligns = []
    for c in cells:
        c = c.strip()
        left = c.startswith(":")
        right = c.endswith(":")
        if left and right:
            aligns.append("center")
        elif right:
            aligns.append("right")
        elif left:
            aligns.append("left")
        else:
            aligns.append("left")
    return aligns


def _calculate_column_percentages(header_cells: List[str], table_rows: List[List[str]], num_cols: int) -> List[int]:
    """
    Вычисляет пропорциональную ширину колонок в процентах на основе максимальной длины
    содержимого ячеек в каждой колонке с гарантированным минимумом во избежание сжатия.
    Сумма всех долей строго равна 100%.
    """
    if num_cols <= 0:
        return []
    if num_cols == 1:
        return [100]

    col_lens = [0] * num_cols
    clean_re = re.compile(r"<[^>]+>")

    for col_idx in range(num_cols):
        h_text = clean_re.sub("", header_cells[col_idx]) if col_idx < len(header_cells) else ""
        max_l = max(len(h_text.strip()), 1)
        for row in table_rows:
            if col_idx < len(row):
                r_text = clean_re.sub("", row[col_idx])
                max_l = max(max_l, len(r_text.strip()))
        col_lens[col_idx] = max_l

    # Базовая минимальная доля на колонку (не менее 15% при 2-4 колонках), чтобы избежать схлопывания
    min_pct = max(12, int(70 / num_cols))
    total_len = sum(col_lens)
    if total_len <= 0:
        base = 100 // num_cols
        res = [base] * num_cols
        res[-1] += 100 - sum(res)
        return res

    raw_pcts = [max(min_pct, int((cl / total_len) * 100)) for cl in col_lens]
    total_raw = sum(raw_pcts)

    # Нормализуем строго к 100%
    norm_pcts = [int((p / total_raw) * 100) for p in raw_pcts]
    diff = 100 - sum(norm_pcts)
    norm_pcts[-1] += diff
    return norm_pcts


def parse_markdown_tables_to_qt_html(text: str) -> str:
    """
    Преобразует Markdown-таблицы в валидный Qt Rich Text HTML.
    
    Особенности:
    1. Изоляция блоков <pre>...</pre> для предотвращения ложного разбора примеров кода.
    2. Извлечение выравнивания колонок (left, center, right).
    3. Защита экранированных пайпов (\\|).
    4. Однострочная сборка HTML без символов '\\n', исключающая инъекцию <br> внутри тегов таблицы.
    5. Полная совместимость с C++ движком QTextDocument (PyQt6).
    """
    if not text or "|" not in text:
        return text

    # 1. Защита блоков <pre>...</pre> (флаг re.DOTALL)
    pre_blocks: List[str] = []

    def _mask_pre(match: re.Match) -> str:
        idx = len(pre_blocks)
        pre_blocks.append(match.group(0))
        return f"\x00PRE_BLOCK_{idx}\x00"

    masked_text = re.sub(r"<pre\b[^>]*>.*?</pre>", _mask_pre, text, flags=re.DOTALL | re.IGNORECASE)

    # 2. Построчный разбор O(N)
    lines = masked_text.split("\n")
    output: List[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        # Проверяем пару: строка заголовка + строка разделителя
        if _is_table_row(line) and not _is_table_sep(line) and (i + 1 < n) and _is_table_sep(lines[i + 1]):
            header_line = line
            sep_line = lines[i + 1]
            alignments = _parse_alignments(sep_line)
            header_cells = _split_row_cells(header_line)
            num_cols = max(len(alignments), len(header_cells), 1)

            if len(alignments) < num_cols:
                alignments.extend(["left"] * (num_cols - len(alignments)))
            if len(header_cells) < num_cols:
                header_cells.extend([""] * (num_cols - len(header_cells)))
            elif len(header_cells) > num_cols:
                header_cells = header_cells[:num_cols]

            table_rows: List[List[str]] = []
            i += 2
            while i < n and _is_table_row(lines[i]) and not _is_table_sep(lines[i]):
                row_cells = _split_row_cells(lines[i])
                if len(row_cells) < num_cols:
                    row_cells.extend([""] * (num_cols - len(row_cells)))
                elif len(row_cells) > num_cols:
                    row_cells = row_cells[:num_cols]
                table_rows.append(row_cells)
                i += 1

            # Вычисление пропорциональной ширины колонок в процентах
            col_pcts = _calculate_column_percentages(header_cells, table_rows, num_cols)

            # Генерация компактного HTML строго в одну строку без \n
            parts: List[str] = [
                '<table border="1" cellspacing="0" cellpadding="5" width="100%" '
                'style="border-collapse:collapse; border:1px solid #1E293B; '
                'margin-top:4px; margin-bottom:4px; background-color:#0A0E17;">',
                '<thead><tr style="background-color:#0F172A;">'
            ]
            for col_idx, cell in enumerate(header_cells):
                align = alignments[col_idx]
                pct = col_pcts[col_idx]
                parts.append(
                    f'<th width="{pct}%" align="{align}" style="border:1px solid #1E293B; '
                    f'background-color:#0F172A; color:#00F0FF; font-weight:600; '
                    f'padding:5px 8px;">{cell}</th>'
                )
            parts.append('</tr></thead><tbody>')

            for r_idx, row in enumerate(table_rows):
                row_bg = "#0A0E17" if r_idx % 2 == 0 else "#0D131F"
                parts.append(f'<tr style="background-color:{row_bg};">')
                for col_idx in range(num_cols):
                    cell = row[col_idx]
                    align = alignments[col_idx]
                    parts.append(
                        f'<td align="{align}" style="border:1px solid #1E293B; '
                        f'background-color:{row_bg}; color:#E2E8F0; '
                        f'padding:4px 8px;">{cell}</td>'
                    )
                parts.append('</tr>')

            parts.append('</tbody></table>')
            output.append(''.join(parts))
        else:
            output.append(line)
            i += 1

    result = "\n".join(output)

    # 3. Восстановление блоков <pre>
    for idx, orig_pre in enumerate(pre_blocks):
        result = result.replace(f"\x00PRE_BLOCK_{idx}\x00", orig_pre)

    return result
