"""
services/markdown_chunker.py

Структурный нарезчик Markdown-документов для RAG.
Обеспечивает целостность кодовых блоков (```), таблиц и сохраняет
иерархический контекст секций (Breadcrumbs) для плотных эмбеддингов.
"""

import re
from typing import Any, Dict, List, Tuple, Union


class MarkdownChunker:
    """Интеллектуальная нарезка Markdown на смысловые фрагменты."""

    _HEADER_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
    _CODE_BLOCK_DELIM = "```"

    @classmethod
    def is_table_row(cls, line: str) -> bool:
        """Проверяет, является ли строка строкой markdown таблицы."""
        s = line.strip()
        return bool(s.startswith("|") and s.endswith("|") and s.count("|") >= 2)

    @classmethod
    def is_table_separator(cls, line: str) -> bool:
        """Проверяет, является ли строка строкой-разделителем markdown таблицы (|---|---|)."""
        s = line.strip()
        return bool(re.match(r"^\|(?:\s*:?-+:?\s*\|)+$", s))

    @classmethod
    def chunk_table(
        cls,
        table_lines: List[str],
        max_chunk: int = 900,
        overlap_rows: int = 1
    ) -> List[str]:
        """
        Нарезка Markdown таблицы на чанки с гарантированным сохранением шапки (headers + separator)
        в каждом чанке и сохранением целостности строк (строго по \n, без разрезания строк таблицы).
        """
        if not table_lines:
            return []

        full_table = "\n".join(table_lines)
        if len(full_table) <= max_chunk:
            return [full_table]

        header = table_lines[0]
        separator = table_lines[1] if len(table_lines) > 1 and cls.is_table_separator(table_lines[1]) else ""
        header_block = f"{header}\n{separator}\n" if separator else f"{header}\n"

        data_rows = table_lines[2:] if separator else table_lines[1:]
        if not data_rows:
            return [full_table]

        chunks: List[str] = []
        current_rows: List[str] = []
        header_len = len(header_block)

        for row in data_rows:
            current_len = header_len + sum(len(r) + 1 for r in current_rows)
            if current_rows and (current_len + len(row) + 1 > max_chunk):
                chunk_content = header_block + "\n".join(current_rows)
                chunks.append(chunk_content.strip())
                overlap_count = min(overlap_rows, len(current_rows))
                current_rows = current_rows[-overlap_count:] if overlap_count > 0 else []
            current_rows.append(row)

        if current_rows:
            chunk_content = header_block + "\n".join(current_rows)
            if chunks and len(current_rows) == 1 and len(chunks[-1]) + len(current_rows[0]) + 1 <= max_chunk + 100:
                chunks[-1] = chunks[-1] + "\n" + current_rows[0]
            else:
                chunks.append(chunk_content.strip())

        return chunks

    @classmethod
    def chunk_code_block(
        cls,
        code_block: Union[str, List[str]],
        min_chunk: int = 500,
        max_chunk: int = 1400
    ) -> List[str]:
        """
        Безопасное разделение крупного кодового блока на валидные Markdown-фрагменты.
        1. Строго по границам строк (\\n).
        2. Каждый фрагмент сохраняет открывающий маркер ``` (включая язык) и закрывающий ```.
        3. Размер каждого фрагмента стремится к диапазону min_chunk..max_chunk.
        """
        if not code_block:
            return []

        if isinstance(code_block, str):
            code_lines = code_block.splitlines()
        else:
            code_lines = list(code_block)

        if not code_lines:
            return []

        full_code = "\n".join(code_lines)
        if len(full_code) <= max_chunk:
            return [full_code]

        # Определяем открывающий маркер (с языком программирования, если указан)
        first_line = code_lines[0].strip()
        if first_line.startswith(cls._CODE_BLOCK_DELIM):
            open_fence = code_lines[0]
            inner_lines = code_lines[1:]
        else:
            open_fence = cls._CODE_BLOCK_DELIM
            inner_lines = code_lines[:]

        # Отсекаем закрывающий маркер из внутренних строк
        if inner_lines and inner_lines[-1].strip() == cls._CODE_BLOCK_DELIM:
            inner_lines = inner_lines[:-1]

        close_fence = cls._CODE_BLOCK_DELIM
        fence_len = len(open_fence) + len(close_fence) + 2  # 2 переноса \n

        chunks: List[str] = []
        buf: List[str] = []
        buf_len = fence_len

        for line in inner_lines:
            line_len = len(line) + 1  # длина строки плюс перенос \n
            # Если добавление очередной строки превышает max_chunk и буфер уже не пуст
            if buf and (buf_len + line_len > max_chunk):
                chunk_str = f"{open_fence}\n" + "\n".join(buf) + f"\n{close_fence}"
                chunks.append(chunk_str.strip())
                buf = [line]
                buf_len = fence_len + line_len
            else:
                buf.append(line)
                buf_len += line_len

        if buf:
            chunk_str = f"{open_fence}\n" + "\n".join(buf) + f"\n{close_fence}"
            # Если остаточный фрагмент можно объединить с предыдущим без превышения max_chunk
            if chunks and (len(chunks[-1]) + (buf_len - fence_len) <= max_chunk):
                prev_lines = chunks[-1].splitlines()
                if (
                    len(prev_lines) >= 2
                    and prev_lines[0].strip().startswith(cls._CODE_BLOCK_DELIM)
                    and prev_lines[-1].strip() == cls._CODE_BLOCK_DELIM
                ):
                    merged_inner = prev_lines[1:-1] + buf
                    chunks[-1] = (f"{open_fence}\n" + "\n".join(merged_inner) + f"\n{close_fence}").strip()
                else:
                    chunks.append(chunk_str.strip())
            else:
                chunks.append(chunk_str.strip())

        return chunks

    @classmethod
    def _split_oversized_text(
        cls,
        text: str,
        min_chunk: int = 500,
        max_chunk: int = 1400,
        overlap: int = 150
    ) -> List[str]:
        """Разбивает крупный текстовый блок на перекрывающиеся части по границам предложений/слов."""
        if len(text) <= max_chunk:
            return [text]

        chunks: List[str] = []
        current = text.strip()

        while len(current) > max_chunk:
            split_pos = current.rfind(". ", min_chunk, max_chunk)
            if split_pos == -1:
                split_pos = current.rfind("\n", min_chunk, max_chunk)
            if split_pos == -1:
                split_pos = current.rfind(" ", min_chunk, max_chunk)
            if split_pos == -1:
                split_pos = max_chunk
            else:
                split_pos += 1

            chunk_slice = current[:split_pos].strip()
            chunks.append(chunk_slice)

            overlap_slice = chunk_slice[-overlap:] if len(chunk_slice) > overlap else chunk_slice
            sp_idx = overlap_slice.find(" ")
            if sp_idx != -1 and sp_idx < len(overlap_slice) - 10:
                overlap_slice = overlap_slice[sp_idx + 1:]
            current = (overlap_slice + " " + current[split_pos:]).strip()

        if current:
            if chunks and len(current) < 200 and len(chunks[-1]) + len(current) + 2 <= max_chunk:
                chunks[-1] = (chunks[-1] + "\n\n" + current).strip()
            else:
                chunks.append(current)

        return chunks

    @classmethod
    def _extract_atomic_blocks(cls, text: str) -> List[Tuple[str, Any, str]]:
        """Разбивает документ на список кортежей (тип_блока, содержимое, текущий_заголовок)."""
        lines = text.splitlines()
        blocks: List[Tuple[str, Any, str]] = []
        current_text: List[str] = []
        current_header = ""
        in_code = False
        code_lines: List[str] = []
        idx = 0

        while idx < len(lines):
            line = lines[idx]
            stripped = line.strip()

            # Обнаружение открытия/закрытия блока кода
            if stripped.startswith(cls._CODE_BLOCK_DELIM):
                if in_code:
                    code_lines.append(line)
                    blocks.append(("code", "\n".join(code_lines), current_header))
                    code_lines = []
                    in_code = False
                    idx += 1
                    continue
                else:
                    if current_text:
                        blocks.append(("text", "\n".join(current_text), current_header))
                        current_text = []
                    in_code = True
                    code_lines.append(line)
                    idx += 1
                    continue

            if in_code:
                code_lines.append(line)
                idx += 1
                continue

            # Обнаружение Markdown-таблицы
            if cls.is_table_row(line) and idx + 1 < len(lines) and cls.is_table_separator(lines[idx + 1]):
                if current_text:
                    blocks.append(("text", "\n".join(current_text), current_header))
                    current_text = []
                table_lines = [line, lines[idx + 1]]
                idx += 2
                while idx < len(lines) and cls.is_table_row(lines[idx]):
                    table_lines.append(lines[idx])
                    idx += 1
                blocks.append(("table", table_lines, current_header))
                continue

            # Обнаружение Markdown-заголовка
            h_match = cls._HEADER_RE.match(line)
            if h_match:
                if current_text:
                    blocks.append(("text", "\n".join(current_text), current_header))
                    current_text = []
                current_header = h_match.group(2).strip()
                current_text.append(line)
                idx += 1
                continue

            current_text.append(line)
            idx += 1

        if code_lines:
            blocks.append(("code", "\n".join(code_lines), current_header))
        if current_text:
            blocks.append(("text", "\n".join(current_text), current_header))

        return blocks

    @classmethod
    def chunk_document(
        cls,
        text: str,
        filename: str = "document",
        min_chunk: int = 500,
        max_chunk: int = 1400,
        overlap: int = 150
    ) -> List[Dict[str, Any]]:
        """
        Нарезает Markdown-текст на структурные чанки.
        1. Защищает кодовые блоки от разделения посередине, разделяя гигантские блоки на валидные фрагменты.
        2. Формирует границы по заголовкам секций.
        3. Сохраняет целостность таблиц и строк.
        4. Обеспечивает плавное контекстное перекрытие (overlap) между чанками.
        """
        if not text or not text.strip():
            return []

        raw_text = text.strip()
        if len(raw_text) <= max_chunk:
            return [{
                "chunk_id": f"{filename}#c0",
                "filename": filename,
                "chunk_index": 0,
                "text": raw_text,
                "char_count": len(raw_text),
                "section": filename
            }]

        atomic_blocks = cls._extract_atomic_blocks(raw_text)

        raw_chunks: List[Tuple[str, str]] = []  # (chunk_text, section)
        current_chunk = ""
        current_section = filename

        for b_type, b_content, s_title in atomic_blocks:
            if s_title:
                current_section = s_title

            if b_type == "table":
                if current_chunk:
                    raw_chunks.append((current_chunk.strip(), current_section))
                    current_chunk = ""
                t_chunks = cls.chunk_table(b_content, max_chunk=max_chunk, overlap_rows=1)
                for tc in t_chunks:
                    raw_chunks.append((tc.strip(), current_section))

            elif b_type == "code":
                # Сбрасываем накопленный текст перед кодом
                if current_chunk:
                    raw_chunks.append((current_chunk.strip(), current_section))
                    current_chunk = ""

                # Нарезаем крупный блок кода на валидные Markdown-фрагменты с маркерами ```
                c_chunks = cls.chunk_code_block(b_content, min_chunk=min_chunk, max_chunk=max_chunk)
                for cc in c_chunks:
                    raw_chunks.append((cc.strip(), current_section))

            else:
                # Обычный текст / параграфы
                paragraphs = b_content.split("\n\n")
                for p in paragraphs:
                    p_strip = p.strip()
                    if not p_strip:
                        continue

                    if not current_chunk:
                        current_chunk = p_strip
                    elif len(current_chunk) + 2 + len(p_strip) <= max_chunk:
                        current_chunk += "\n\n" + p_strip
                    else:
                        if len(current_chunk) >= min_chunk:
                            raw_chunks.append((current_chunk.strip(), current_section))
                            overlap_slice = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
                            sp_idx = overlap_slice.find(" ")
                            if sp_idx != -1 and sp_idx < len(overlap_slice) - 10:
                                overlap_slice = overlap_slice[sp_idx + 1:]
                            current_chunk = (overlap_slice + "\n\n" + p_strip).strip()
                        else:
                            current_chunk += "\n\n" + p_strip

                    while len(current_chunk) > max_chunk:
                        split_pos = current_chunk.rfind(". ", min_chunk, max_chunk)
                        if split_pos == -1:
                            split_pos = current_chunk.rfind("\n", min_chunk, max_chunk)
                        if split_pos == -1:
                            split_pos = current_chunk.rfind(" ", min_chunk, max_chunk)
                        if split_pos == -1:
                            split_pos = max_chunk
                        else:
                            split_pos += 1

                        chunk_slice = current_chunk[:split_pos].strip()
                        raw_chunks.append((chunk_slice, current_section))

                        overlap_slice = chunk_slice[-overlap:] if len(chunk_slice) > overlap else chunk_slice
                        sp_idx = overlap_slice.find(" ")
                        if sp_idx != -1 and sp_idx < len(overlap_slice) - 10:
                            overlap_slice = overlap_slice[sp_idx + 1:]
                        current_chunk = (overlap_slice + " " + current_chunk[split_pos:]).strip()

                if current_chunk and len(current_chunk) >= min_chunk:
                    pass  # Держим в current_chunk для слияния или финала

        if current_chunk:
            if raw_chunks and len(current_chunk) < 300 and len(raw_chunks[-1][0]) + len(current_chunk) + 2 <= max_chunk:
                last_txt, last_sec = raw_chunks[-1]
                # Не склеиваем обычный текст с закрытым блоком кода или строкой таблицы
                if not last_txt.endswith(cls._CODE_BLOCK_DELIM) and not cls.is_table_row(last_txt.splitlines()[-1]):
                    raw_chunks[-1] = ((last_txt + "\n\n" + current_chunk).strip(), last_sec)
                else:
                    raw_chunks.append((current_chunk.strip(), current_section))
            else:
                raw_chunks.append((current_chunk.strip(), current_section))

        result: List[Dict[str, Any]] = []
        for i, (c_text, c_sec) in enumerate(raw_chunks):
            result.append({
                "chunk_id": f"{filename}#c{i}",
                "filename": filename,
                "chunk_index": i,
                "text": c_text,
                "char_count": len(c_text),
                "section": c_sec
            })

        return result
