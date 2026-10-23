"""
Universal Document & Chat Parser for NeonRoute
Safely extracts human-readable text from office documents, spreadsheets,
presentations, PDFs, and exported AI chats (ChatGPT, Claude, HTML).
"""

import re
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger("NeonRoute.DocumentParser")


class DocumentParser:
    SUPPORTED_EXTENSIONS = {
        # Текстовые файлы и скрипты
        ".txt", ".py", ".md", ".json", ".log", ".ini", ".cfg", ".yaml", ".yml", ".csv",
        # Офисные документы и таблицы
        ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".pdf",
        # Веб и сохраненные страницы
        ".html", ".htm"
    }

    @classmethod
    def is_supported(cls, path: Path) -> bool:
        return path.suffix.lower() in cls.SUPPORTED_EXTENSIONS

    @classmethod
    def extract_text(cls, path: Path, max_chars: int = 30000) -> str:
        """Извлекает очищенный текст из поддерживаемого файла."""
        if not path.exists():
            return ""

        ext = path.suffix.lower()
        extracted = ""

        try:
            if ext == ".docx":
                extracted = cls._parse_docx(path)
            elif ext == ".doc":
                extracted = cls._parse_doc_binary(path)
            elif ext in (".xlsx", ".xls"):
                extracted = cls._parse_excel(path)
            elif ext == ".csv":
                extracted = cls._parse_csv(path)
            elif ext == ".pptx":
                extracted = cls._parse_pptx(path)
            elif ext == ".pdf":
                extracted = cls._parse_pdf(path)
            elif ext in (".html", ".htm"):
                extracted = cls._parse_html(path)
            elif ext == ".json":
                extracted = cls._parse_json_or_chat(path)
            else:
                extracted = cls._parse_plain_text(path)
        except Exception as e:
            logger.warning(f"Ошибка при парсинге {path.name}: {e}")
            extracted = cls._parse_plain_text(path)

        if not extracted or not extracted.strip():
            return ""

        cleaned = extracted.strip()
        if len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars] + f"\n... [Документ усечен до {max_chars} символов для безопасности контекста] ..."

        return cleaned

    @classmethod
    def _parse_docx(cls, path: Path) -> str:
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
        except Exception as e:
            logger.debug(f"python-docx parsing failed: {e}")
            return cls._parse_plain_text(path)

    @classmethod
    def _parse_doc_binary(cls, path: Path) -> str:
        """Извлекает кириллицу и латиницу из старого OLE2 Word .doc файла."""
        try:
            raw_bytes = path.read_bytes()
            # Пробуем декодировать читаемые строки через utf-16le (стандарт Word OLE) и cp1251
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
    def _parse_csv(cls, path: Path) -> str:
        lines = cls._parse_plain_text(path).splitlines()
        return "\n".join(lines[:120])

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
    def _parse_pdf(cls, path: Path) -> str:
        try:
            import pypdf
            with open(path, "rb") as f_pdf:
                reader = pypdf.PdfReader(f_pdf)
                pages = []
                for idx, p in enumerate(reader.pages[:20], 1):
                    txt = p.extract_text()
                    if txt and txt.strip():
                        pages.append(f"--- Страница {idx} ---\n{txt.strip()}")
                return "\n\n".join(pages)
        except Exception as e:
            logger.debug(f"pdf parsing failed: {e}")
            return ""

    @classmethod
    def _parse_html(cls, path: Path) -> str:
        """Очищает HTML-код от тегов и скриптов, оставляя чистый текст переписки."""
        raw = cls._parse_plain_text(path)
        no_scripts = re.sub(r"<(script|style).*?</\1>", "", raw, flags=re.DOTALL | re.IGNORECASE)
        no_tags = re.sub(r"<[^>]+>", "\n", no_scripts)
        lines = [line.strip() for line in no_tags.splitlines() if line.strip()]
        return "\n".join(lines)

    @classmethod
    def _parse_json_or_chat(cls, path: Path) -> str:
        """Распознает дампы экспортов ChatGPT, Claude или Tavern/Chub и вычищает служебные поля."""
        raw = cls._parse_plain_text(path)
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

            return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception:
            return raw

    @classmethod
    def _parse_plain_text(cls, path: Path) -> str:
        for enc in ("utf-8", "cp1251", "latin-1"):
            try:
                return path.read_text(encoding=enc)
            except Exception:
                continue
        return ""
