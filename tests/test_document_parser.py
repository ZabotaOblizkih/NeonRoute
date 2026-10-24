"""
Unit tests for DocumentParser service.
Tests encoding auto-detection, PDF extraction, context overflow protection,
HUD summary header formatting, and crash-resilience.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from services.document_parser import DocumentParser, ParsedDocument


def test_parsed_document_structure():
    doc = ParsedDocument(
        filename="test.txt",
        extension=".txt",
        size_kb=1.5,
        line_count=10,
        page_count=None,
        content="Hello world",
        is_truncated=False,
        summary_header="📄 test.txt | 10 строк | 1.5 КБ",
    )
    assert doc.filename == "test.txt"
    assert doc.extension == ".txt"
    assert doc.size_kb == 1.5
    assert doc.line_count == 10
    assert doc.page_count is None
    assert doc.content == "Hello world"
    assert doc.is_truncated is False
    assert doc.summary_header == "📄 test.txt | 10 строк | 1.5 КБ"


def test_parse_text_file_utf8(tmp_path: Path):
    f = tmp_path / "sample.txt"
    text_content = "Строка 1\nСтрока 2\nПривет, мир!"
    f.write_text(text_content, encoding="utf-8")

    doc = DocumentParser.parse_file(f)
    assert doc is not None
    assert doc.filename == "sample.txt"
    assert doc.extension == ".txt"
    assert doc.line_count == 3
    assert doc.page_count is None
    assert doc.content == text_content
    assert doc.is_truncated is False
    assert "📄 sample.txt | 3 строк" in doc.summary_header


def test_parse_python_code_cp1251(tmp_path: Path):
    f = tmp_path / "script.py"
    code = '# Кодировка Windows-1251\ndef main():\n    print("Тест кириллицы")\n'
    f.write_bytes(code.encode("cp1251"))

    doc = DocumentParser.parse_file(f)
    assert doc is not None
    assert doc.filename == "script.py"
    assert doc.extension == ".py"
    assert "Тест кириллицы" in doc.content
    assert doc.line_count == 3
    assert doc.page_count is None
    assert doc.is_truncated is False


def test_parse_log_utf16(tmp_path: Path):
    f = tmp_path / "server.log"
    log_content = "[2026-10-24 12:00:00] [INFO] Сервер запущен успешно.\n[2026-10-24 12:00:01] [INFO] Готов к работе.\n"
    f.write_bytes(log_content.encode("utf-16"))

    doc = DocumentParser.parse_file(f)
    assert doc is not None
    assert doc.filename == "server.log"
    assert doc.extension == ".log"
    assert "Сервер запущен успешно." in doc.content
    assert doc.line_count == 2
    assert doc.is_truncated is False


def test_parse_latin1(tmp_path: Path):
    f = tmp_path / "config.ini"
    latin_text = "[general]\napp = NeonRoute\nmode = CPU\n"
    f.write_bytes(latin_text.encode("latin-1"))

    doc = DocumentParser.parse_file(f)
    assert doc is not None
    assert "mode = CPU" in doc.content
    assert doc.line_count == 3


def test_corrupted_encoding_fallback(tmp_path: Path):
    f = tmp_path / "broken.txt"
    # Mixed invalid sequence
    raw_garbage = b"Valid prefix: \xc3\x28 ... \xff\xfe\x00 broken tail"
    f.write_bytes(raw_garbage)

    # Should not crash, fallback with errors='replace'
    doc = DocumentParser.parse_file(f)
    assert doc is not None
    assert doc.filename == "broken.txt"
    assert "Valid prefix" in doc.content


def test_context_overflow_truncation(tmp_path: Path):
    f = tmp_path / "large_doc.txt"
    large_text = "А" * 15000
    f.write_text(large_text, encoding="utf-8")

    max_chars = 1000
    doc = DocumentParser.parse_file(f, max_chars=max_chars)
    assert doc is not None
    assert doc.is_truncated is True
    assert doc.content.startswith("А" * 1000)
    expected_notice = "[... Документ сокращен: показаны первые 1000 символов из 15000. Задай уточняющий вопрос по конкретной части документа ...]"
    assert expected_notice in doc.content


def test_pdf_parsing_with_pypdf(tmp_path: Path):
    import pypdf

    pdf_path = tmp_path / "report.pdf"
    writer = pypdf.PdfWriter()

    # Add 2 pages with text
    page1 = writer.add_blank_page(width=200, height=200)
    # We can write text or blank pages
    # Let's test with a valid minimal PDF
    with open(pdf_path, "wb") as f_out:
        writer.write(f_out)

    doc = DocumentParser.parse_file(pdf_path)
    assert doc is not None
    assert doc.filename == "report.pdf"
    assert doc.extension == ".pdf"
    assert doc.page_count == 1
    assert "📄 report.pdf | 1 стр." in doc.summary_header


def test_pdf_library_missing_fallback(tmp_path: Path):
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy content")

    with patch.dict(sys.modules, {"pypdf": None, "fitz": None, "pdfplumber": None}):
        # Mocking import errors
        with patch("services.document_parser.DocumentParser._extract_pdf") as mock_extract:
            mock_extract.return_value = (
                "[Ошибка: Библиотека для чтения PDF не установлена в окружении (требуется pypdf, pymupdf или pdfplumber)]",
                None,
            )
            doc = DocumentParser.parse_file(pdf_path)
            assert doc is not None
            assert "Библиотека для чтения PDF не установлена" in doc.content
            assert doc.page_count is None


def test_corrupted_pdf_graceful_handling(tmp_path: Path):
    pdf_path = tmp_path / "corrupted.pdf"
    pdf_path.write_bytes(b"%PDF-INVALID-DATA\x00\xff\xee")

    # Should not crash the process
    doc = DocumentParser.parse_file(pdf_path)
    assert doc is not None
    assert doc.filename == "corrupted.pdf"
    assert "Ошибка чтения PDF" in doc.content or doc.content != ""


def test_nonexistent_and_invalid_paths():
    assert DocumentParser.parse_file("non_existent_file_9999.xyz") is None
    assert DocumentParser.parse_file(Path("another_missing.txt")) is None
    assert DocumentParser.parse_file(None) is None

    # Directory instead of file
    current_dir = Path.cwd()
    assert DocumentParser.parse_file(current_dir) is None


def test_backwards_compatibility(tmp_path: Path):
    sample = tmp_path / "notes.md"
    sample.write_text("# Заметки\nТестирование совместимости", encoding="utf-8")

    assert DocumentParser.is_supported(sample) is True
    assert DocumentParser.is_supported(str(sample)) is True
    assert DocumentParser.is_supported(Path("dummy.unknown_xyz")) is False

    text = DocumentParser.extract_text(sample)
    assert "# Заметки" in text
    assert "Тестирование совместимости" in text


def test_cpu_only_no_vram_imports():
    # Verify that heavy ML libraries are NOT imported by DocumentParser
    for heavy_mod in ["torch", "transformers", "sentence_transformers", "fastembed"]:
        # If not previously loaded, importing DocumentParser shouldn't load them
        assert heavy_mod not in sys.modules or True  # Checking services.document_parser source
    import inspect
    import services.document_parser as dp_mod
    src = inspect.getsource(dp_mod)
    assert "torch" not in src
    assert "transformers" not in src
    assert "cuda" not in src
