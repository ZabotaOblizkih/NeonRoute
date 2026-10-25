"""
Universal Document & Chat Parser for NeonRoute.
Extracts structured text, code, logs, and document metadata on CPU (0 MB VRAM).
"""

import re
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union, List, Dict, Any, Tuple

logger = logging.getLogger("NeonRoute.DocumentParser")


@dataclass
class ParsedDocument:
    """Результат парсинга документа или текстового файла."""
    filename: str
    extension: str
    size_kb: float
    line_count: int
    page_count: Optional[int]
    content: str
    is_truncated: bool
    summary_header: str


class DocumentParser:
    """Легковесный парсер документов, кода и логов на CPU без VRAM."""

    CODE_EXTENSIONS = {
        ".py", ".js", ".ts", ".html", ".htm", ".css", ".cpp", ".c", ".h",
        ".cs", ".rs", ".go", ".java", ".sql", ".sh", ".bat", ".ps1"
    }

    CONFIG_EXTENSIONS = {
        ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".xml", ".fb2"
    }

    TABLE_EXTENSIONS = {
        ".csv", ".tsv", ".xlsx", ".xls"
    }

    DOCUMENT_EXTENSIONS = {
        ".txt", ".log", ".md", ".pdf", ".docx", ".doc", ".pptx"
    }

    SUPPORTED_EXTENSIONS = (
        CODE_EXTENSIONS | CONFIG_EXTENSIONS | TABLE_EXTENSIONS | DOCUMENT_EXTENSIONS
    )

    TEXT_EXTENSIONS = CODE_EXTENSIONS | CONFIG_EXTENSIONS | {".txt", ".log", ".md", ".csv", ".tsv"}
    OFFICE_EXTENSIONS = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".pdf"}

    @classmethod
    def is_supported(cls, path: Union[str, Path]) -> bool:
        """Проверяет, поддерживается ли расширение файла."""
        if not path:
            return False
        p = Path(path)
        return p.suffix.lower() in cls.SUPPORTED_EXTENSIONS

    @classmethod
    def parse_file(
        cls,
        file_path: Union[str, Path],
        max_chars: Optional[int] = 14000
    ) -> Optional[ParsedDocument]:
        """
        Разбирает файл любого поддерживаемого формата (текст, код, логи, PDF, офис)
        строго на CPU без использования VRAM.

        :param file_path: Путь к файлу (str или Path)
        :param max_chars: Максимальное количество символов (~3000-3500 токенов)
        :return: ParsedDocument или None (если файл не существует или недоступен)
        """
        if file_path is None:
            return None

        try:
            path = Path(file_path).resolve()
            if not path.exists() or not path.is_file():
                return None

            try:
                stat = path.stat()
                size_kb = round(stat.st_size / 1024.0, 2)
            except Exception:
                size_kb = 0.0

            filename = path.name
            ext = path.suffix.lower()

            raw_content = ""
            page_count: Optional[int] = None

            if ext == ".pdf":
                raw_content, page_count = cls._extract_pdf(path)
            elif ext == ".docx":
                raw_content = cls._parse_docx(path)
            elif ext == ".doc":
                raw_content = cls._parse_doc_binary(path)
            elif ext in (".xlsx", ".xls"):
                raw_content = cls._parse_excel(path)
            elif ext in (".csv", ".tsv"):
                raw_content = cls._parse_csv_or_tsv(path)
            elif ext in (".fb2", ".xml"):
                raw_content = cls._parse_fb2_or_xml(path)
            elif ext == ".pptx":
                raw_content = cls._parse_pptx(path)
            elif ext in (".html", ".htm"):
                raw_content = cls._parse_html(path)
            elif ext == ".json":
                text = cls._read_text_safely(path)
                raw_content = cls._parse_json_or_chat(text)
            else:
                # Текстовые файлы, код и конфиги (.txt, .md, .py, .yaml, .toml, .ini, .cpp, .rs, .env, .sql, .log и др.)
                raw_content = cls._read_text_safely(path)

            raw_content = raw_content.strip() if raw_content else ""
            line_count = len(raw_content.splitlines()) if raw_content else 0

            total = len(raw_content)
            if max_chars is not None and max_chars > 0 and total > max_chars:
                is_truncated = True
                content = raw_content[:max_chars] + (
                    f"\n\n[... Документ сокращен: показаны первые {max_chars} символов из {total}. "
                    f"Задай уточняющий вопрос по конкретной части документа ...]"
                )
            else:
                is_truncated = False
                content = raw_content

            summary_header = cls._format_summary_header(filename, page_count, line_count, size_kb)

            return ParsedDocument(
                filename=filename,
                extension=ext,
                size_kb=size_kb,
                line_count=line_count,
                page_count=page_count,
                content=content,
                is_truncated=is_truncated,
                summary_header=summary_header
            )
        except Exception as e:
            logger.warning(f"Ошибка при обработке файла {file_path}: {e}")
            return None

    @classmethod
    def extract_text(cls, path: Union[str, Path], max_chars: int = 30000) -> str:
        """Извлекает очищенный текст из поддерживаемого файла (обратная совместимость)."""
        parsed = cls.parse_file(path, max_chars=max_chars)
        if not parsed:
            return ""
        return parsed.content

    @classmethod
    def _format_summary_header(
        cls,
        filename: str,
        page_count: Optional[int],
        line_count: int,
        size_kb: float
    ) -> str:
        """Формирует однострочную HUD-сводку документа."""
        if size_kb >= 10:
            size_str = f"{size_kb:.0f}" if abs(size_kb - round(size_kb)) < 0.05 else f"{size_kb:.1f}"
        else:
            size_str = f"{size_kb:.1f}" if size_kb > 0 else "0"

        ext = Path(filename).suffix.lower()
        icon = "📊" if ext in (".csv", ".tsv", ".xlsx", ".xls") else "📄"

        if page_count is not None:
            return f"{icon} {filename} | {page_count} стр. | {size_str} КБ"
        else:
            return f"{icon} {filename} | {line_count} строк | {size_str} КБ"

    @classmethod
    def _read_text_safely(cls, path: Path) -> str:
        """
        Безопасное чтение текстового файла с автоопределением кодировки.
        Последовательно пробует: utf-8, cp1251, utf-16, latin-1 с fallback на errors='replace'.
        """
        try:
            raw_bytes = path.read_bytes()
        except Exception as e:
            logger.debug(f"Не удалось прочитать байты файла {path}: {e}")
            return ""

        # Проверка маркеров BOM для UTF-16
        if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
            try:
                return raw_bytes.decode("utf-16").replace("\r\n", "\n").replace("\r", "\n")
            except UnicodeError:
                pass

        # Последовательный перебор кодировок: utf-8, cp1251, utf-16, latin-1
        for enc in ("utf-8-sig", "utf-8", "cp1251", "utf-16", "latin-1"):
            try:
                text = raw_bytes.decode(enc)
                # Защита: ложное срабатывание cp1251 на utf-16 без BOM дает чередование \x00
                if enc == "cp1251" and text.count("\x00") > 3:
                    continue
                return text.replace("\r\n", "\n").replace("\r", "\n")
            except (UnicodeDecodeError, UnicodeError):
                continue
            except Exception:
                continue

        return raw_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

    @classmethod
    def _parse_plain_text(cls, path: Path) -> str:
        """Совместимый метод чтения plain-text."""
        return cls._read_text_safely(path)

    @classmethod
    def _clean_pdf_text(cls, text: str) -> str:
        """Очистка артефактов верстки PDF: склейка разорванных переносов и разрядки."""
        if not text:
            return ""
        # 1. Склейка переносов слов через дефис на новую строку: "эксп-\nлуатации" -> "эксплуатации"
        text = re.sub(r'([А-Яа-яA-Za-z]+)-\s*\n\s*([А-Яа-яA-Za-z]+)', r'\1\2', text)
        # 2. Склейка дефисных переносов внутри строки: "предназна- ченные" -> "предназначенные"
        text = re.sub(r'([А-Яа-яA-Za-z]{3,})-\s+([а-яa-z]{3,})', r'\1\2', text)
        # 3. Де-керинг разрядки заголовков (3+ одиночных заглавных букв через пробел): "Т Е Х Н И К А" -> "ТЕХНИКА"
        text = re.sub(
            r'\b([А-ЯЁA-Z])(?:\s+([А-ЯЁA-Z])){2,}\b',
            lambda m: re.sub(r'\s+', '', m.group(0)),
            text
        )
        return text

    @classmethod
    def _extract_pdf(cls, path: Path) -> Tuple[str, Optional[int]]:
        """
        Постраничное извлечение текста из PDF без нейросетей на CPU.
        Поддерживает pypdf, pymupdf (fitz), pdfplumber.
        Возвращает (content, page_count).
        """
        last_error = ""

        # 1. Попытка через pypdf
        try:
            import pypdf
            with open(path, "rb") as f_pdf:
                reader = pypdf.PdfReader(f_pdf)
                page_count = len(reader.pages)
                pages = []
                for idx, p in enumerate(reader.pages, 1):
                    try:
                        txt = p.extract_text() or ""
                        txt = txt.strip()
                        if txt:
                            pages.append(f"--- Страница {idx} ---\n{txt}")
                    except Exception as pe:
                        logger.debug(f"Ошибка извлечения страницы {idx} PDF через pypdf: {pe}")

                content = "\n\n".join(pages)
                content = cls._clean_pdf_text(content)
                if not content.strip():
                    content = "[PDF документ не содержит распознаваемого текстового слоя (возможно, сканированное изображение)]"
                return content, page_count
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Ошибка при парсинге PDF через pypdf ({path.name}): {e}")
            last_error = str(e)

        # 2. Попытка через pymupdf (fitz)
        try:
            import fitz
            doc = fitz.open(str(path))
            page_count = len(doc)
            pages = []
            for idx, page in enumerate(doc, 1):
                try:
                    txt = page.get_text() or ""
                    txt = txt.strip()
                    if txt:
                        pages.append(f"--- Страница {idx} ---\n{txt}")
                except Exception as pe:
                    logger.debug(f"Ошибка fitz на странице {idx}: {pe}")
            doc.close()

            content = "\n\n".join(pages)
            content = cls._clean_pdf_text(content)
            if not content.strip():
                content = "[PDF документ не содержит распознаваемого текстового слоя (возможно, сканированное изображение)]"
            return content, page_count
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Ошибка при парсинге PDF через fitz ({path.name}): {e}")
            last_error = str(e)

        # 3. Попытка через pdfplumber
        try:
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                page_count = len(pdf.pages)
                pages = []
                for idx, page in enumerate(pdf.pages, 1):
                    try:
                        txt = page.extract_text() or ""
                        txt = txt.strip()
                        if txt:
                            pages.append(f"--- Страница {idx} ---\n{txt}")
                    except Exception as pe:
                        logger.debug(f"Ошибка pdfplumber на странице {idx}: {pe}")
                content = "\n\n".join(pages)
                content = cls._clean_pdf_text(content)
                if not content.strip():
                    content = "[PDF документ не содержит распознаваемого текстового слоя (возможно, сканированное изображение)]"
                return content, page_count
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Ошибка при парсинге PDF через pdfplumber ({path.name}): {e}")
            last_error = str(e)

        if last_error:
            return f"[Ошибка чтения PDF: {last_error}]", None

        return (
            "[Ошибка: Библиотека для чтения PDF не установлена в окружении (требуется pypdf, pymupdf или pdfplumber)]",
            None
        )

    @classmethod
    def _parse_pdf(cls, path: Path) -> str:
        """Совместимый метод парсинга PDF."""
        content, _ = cls._extract_pdf(path)
        return content

    @classmethod
    def _parse_docx(cls, path: Path) -> str:
        """Извлечение текста и таблиц из .docx файлов."""
        try:
            import docx
            doc = docx.Document(path)
            lines = []
            for p in doc.paragraphs:
                if p.text.strip():
                    lines.append(p.text.strip())
            for t in doc.tables:
                for row in t.rows:
                    row_txt = [c.text.strip().replace("\n", " ") for c in row.cells if c.text.strip()]
                    if row_txt:
                        lines.append(" | ".join(row_txt))
            return "\n".join(lines)
        except ImportError:
            return "[Для чтения DOCX требуется python-docx. Установите: pip install python-docx]"
        except Exception as e:
            logger.debug(f"python-docx parsing failed: {e}")
            return cls._read_text_safely(path)

    @classmethod
    def _parse_doc_binary(cls, path: Path) -> str:
        """Извлекает кириллицу и латиницу из старого OLE2 Word .doc файла."""
        try:
            raw_bytes = path.read_bytes()
            decoded_lines = []
            for enc in ("utf-16le", "cp1251", "utf-8"):
                try:
                    s = raw_bytes.decode(enc, errors="ignore")
                    words = re.findall(r"[а-яА-ЯёЁa-zA-Z0-9.,!?:;()\s-]{6,}", s)
                    clean_words = [w.strip() for w in words if len(w.strip()) > 5 and not w.strip().startswith("\x00")]
                    if len(" ".join(clean_words)) > len(" ".join(decoded_lines)):
                        decoded_lines = clean_words
                except Exception:
                    continue
            return "\n".join(decoded_lines[:350]) if decoded_lines else ""
        except Exception:
            return ""

    @classmethod
    def _parse_excel(cls, path: Path) -> str:
        """Преобразует страницы Excel в Markdown-таблицы."""
        wb = None
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True)
            tables = []
            for sheet_name in wb.sheetnames[:3]:  # Первые 3 листа
                sheet = wb[sheet_name]
                rows_data = []
                for row in sheet.iter_rows(values_only=True):
                    row_vals = [str(c).strip() if c is not None else "" for c in row]
                    if any(row_vals):
                        rows_data.append(" | ".join(row_vals[:12]))
                    if len(rows_data) >= 80:  # Ограничение до 80 строк на лист
                        rows_data.append("... [строки листа усечены]")
                        break
                if rows_data:
                    tables.append(f"### Лист: {sheet_name}\n" + "\n".join(rows_data))
            return "\n\n".join(tables)
        except ImportError:
            return "[Для чтения Excel (.xlsx/.xls) требуется openpyxl. Установите: pip install openpyxl]"
        except Exception as e:
            logger.debug(f"openpyxl parsing failed: {e}")
            return ""
        finally:
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    pass

    @classmethod
    def _parse_csv_or_tsv(cls, path: Path) -> str:
        """
        Чтение CSV и TSV файлов через встроенный модуль csv с конвертацией
        первых строк в компактную Markdown-таблицу.
        """
        import csv
        import io

        raw_text = cls._read_text_safely(path)
        if not raw_text.strip():
            return ""

        ext = path.suffix.lower()
        delimiter = "\t" if ext == ".tsv" else ","

        sample_lines = raw_text.splitlines()[:15]
        sample_str = "\n".join(sample_lines)

        if ext != ".tsv":
            try:
                sniffer = csv.Sniffer()
                dialect = sniffer.sniff(sample_str, delimiters=",;\t|")
                delimiter = dialect.delimiter
            except Exception:
                delimiter = "," if sample_str.count(",") >= sample_str.count(";") else ";"

        try:
            reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter)
            rows = []
            for row in reader:
                if any(c.strip() for c in row):
                    rows.append([c.strip().replace("\n", " ") for c in row])
                if len(rows) >= 100:
                    break

            if not rows:
                return raw_text[:3000]

            header = rows[0]
            col_count = len(header)
            md_lines = [" | ".join(header), " | ".join(["---"] * col_count)]

            for r in rows[1:]:
                if len(r) < col_count:
                    r = r + [""] * (col_count - len(r))
                elif len(r) > col_count:
                    r = r[:col_count]
                md_lines.append(" | ".join(r))

            total_lines = len(raw_text.splitlines())
            if total_lines > len(rows):
                md_lines.append(f"\n... [показано {len(rows)} строк из {total_lines}]")

            return "\n".join(md_lines)
        except Exception as e:
            logger.debug(f"CSV parsing error: {e}")
            return "\n".join(sample_lines[:80])

    _parse_csv = _parse_csv_or_tsv

    @classmethod
    def _parse_fb2_or_xml(cls, path: Path) -> str:
        """
        Парсинг FB2-книг и XML документов через xml.etree.ElementTree (без внешних зависимостей).
        Извлекает чистый текст, заголовки и метаданные без XML-тегов и бинарных данных.
        """
        import xml.etree.ElementTree as ET

        try:
            raw_bytes = path.read_bytes()
            root = ET.fromstring(raw_bytes)

            lines: List[str] = []
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                tag_lower = tag.lower()

                # Пропускаем бинарные данные картинок и скрипты
                if tag_lower in ("binary", "script", "style"):
                    continue

                text_val = (elem.text or "").strip()
                if text_val:
                    if tag_lower in ("book-title", "title"):
                        lines.append(f"### {text_val}")
                    elif tag_lower in ("p", "paragraph"):
                        lines.append(text_val)
                    elif tag_lower in ("subtitle",):
                        lines.append(f"**{text_val}**")
                    elif tag_lower in ("first-name", "last-name"):
                        lines.append(f"Автор: {text_val}")
                    elif tag_lower not in ("fictionbook", "body", "section", "description", "title-info", "author"):
                        lines.append(text_val)

            content = "\n\n".join(lines).strip()
            if content:
                return content

            all_text = [t.strip() for t in root.itertext() if t.strip()]
            return "\n".join(all_text)
        except Exception as e:
            logger.debug(f"XML/FB2 ET parse failed ({path.name}): {e}")
            raw_text = cls._read_text_safely(path)
            no_binary = re.sub(r"<binary[\s\S]*?</binary>", "", raw_text, flags=re.IGNORECASE)
            no_tags = re.sub(r"<[^>]+>", "\n", no_binary)
            cleaned_lines = [l.strip() for l in no_tags.splitlines() if l.strip()]
            return "\n".join(cleaned_lines)

    @classmethod
    def _parse_pptx(cls, path: Path) -> str:
        """Извлекает заголовки и текст со слайдов презентации PowerPoint."""
        try:
            import zipfile
            import xml.etree.ElementTree as ET
            slides_text = []
            with zipfile.ZipFile(path, "r") as z:
                slide_files = [f for f in z.namelist() if f.startswith("ppt/slides/slide") and f.endswith(".xml")]
                slide_files.sort(key=lambda x: int(re.search(r"\d+", x).group()) if re.search(r"\d+", x) else 0)

                for idx, s_file in enumerate(slide_files, 1):
                    xml_content = z.read(s_file)
                    tree = ET.fromstring(xml_content)
                    texts = [elem.text for elem in tree.iter() if elem.text and elem.text.strip()]
                    if texts:
                        slides_text.append(f"### Слайд {idx}:\n" + "\n".join(texts))
            return "\n\n".join(slides_text)
        except Exception as e:
            logger.debug(f"pptx parsing failed: {e}")
            return ""

    @classmethod
    def _parse_html(cls, path: Path) -> str:
        """Очищает HTML-код от тегов и скриптов, оставляя чистый текст."""
        raw = cls._read_text_safely(path)
        no_scripts = re.sub(r"<(script|style).*?</\1>", "", raw, flags=re.DOTALL | re.IGNORECASE)
        no_tags = re.sub(r"<[^>]+>", "\n", no_scripts)
        lines = [line.strip() for line in no_tags.splitlines() if line.strip()]
        return "\n".join(lines)

    @classmethod
    def _parse_json_or_chat(cls, target: Union[str, Path]) -> str:
        """Распознает дампы экспортов ChatGPT, Claude или Tavern/Chub и вычищает служебные поля."""
        if isinstance(target, Path):
            raw = cls._read_text_safely(target)
        else:
            raw = target

        try:
            data = json.loads(raw)
            # 1. Дамп ChatGPT (структура с mapping)
            if isinstance(data, list) and data and isinstance(data[0], dict) and "mapping" in data[0]:
                dialogues = []
                for conv in data[:2]:
                    title = conv.get("title", "Диалог")
                    dialogues.append(f"=== Диалог: {title} ===")
                    mapping = conv.get("mapping", {})
                    for node in mapping.values():
                        msg = node.get("message")
                        if msg and isinstance(msg, dict):
                            author = msg.get("author", {}).get("role", "")
                            parts = msg.get("content", {}).get("parts", [])
                            txt = " ".join(str(p) for p in parts if isinstance(p, str)).strip()
                            if txt:
                                role_lbl = "Пользователь" if author == "user" else "ИИ"
                                dialogues.append(f"[{role_lbl}]: {txt}")
                return "\n".join(dialogues)

            # 2. Обычный список сообщений [{"role": "user", "content": "..."}]
            if isinstance(data, list) and all(isinstance(x, dict) and "role" in x for x in data):
                res = []
                for item in data:
                    res.append(f"[{item.get('role')}]: {item.get('content', '')}")
                return "\n".join(res)

            # 3. Карточка персонажа Tavern / Chub
            if isinstance(data, dict) and ("spec" in data or "first_mes" in data or "data" in data):
                inner = data.get("data", data)
                name = inner.get("name", "Персонаж")
                desc = inner.get("description", "")
                first_mes = inner.get("first_mes", "")
                return f"Карточка персонажа: {name}\nОписание: {desc}\nПриветствие: {first_mes}"

            return raw
        except Exception:
            return raw
