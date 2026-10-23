import inspect
import os
import time
from pathlib import Path
import pytest

from services.file_generator import FileGenerator, _wait_for_file_ready
from services.code_engine import SelfHealingEngine, PatchResult
from services.sandbox_manager import SandboxManager


def test_signatures_preserved():
    # Verify signatures remain unchanged
    excel_sig = inspect.signature(FileGenerator.create_excel)
    assert list(excel_sig.parameters.keys()) == ["title", "data_text", "sheet_name"]

    word_sig = inspect.signature(FileGenerator.create_word)
    assert list(word_sig.parameters.keys()) == ["title", "content_markdown"]

    patch_sig = inspect.signature(SelfHealingEngine.patch_file_transactional)
    assert list(patch_sig.parameters.keys()) == ["filename", "search_block", "replace_block", "diff_text"]


def test_wait_for_file_ready_success(tmp_path: Path):
    dummy_file = tmp_path / "valid.zip"
    dummy_file.write_bytes(b"PK\x03\x04testpayload")

    assert _wait_for_file_ready(dummy_file, timeout=1.0) is True
    assert FileGenerator._wait_for_file_ready(dummy_file, timeout=1.0) is True


def test_wait_for_file_ready_timeout_on_missing(tmp_path: Path):
    missing_file = tmp_path / "missing.docx"
    t0 = time.time()
    res = _wait_for_file_ready(missing_file, timeout=0.2)
    elapsed = time.time() - t0

    assert res is False
    assert 0.15 <= elapsed <= 0.6


def test_wait_for_file_ready_timeout_on_non_zip(tmp_path: Path):
    non_zip = tmp_path / "plain.txt"
    non_zip.write_text("Hello world", encoding="utf-8")

    t0 = time.time()
    res = _wait_for_file_ready(non_zip, timeout=0.2)
    elapsed = time.time() - t0

    # Fallback return target.exists() and target.stat().st_size > 0
    assert res is True
    assert 0.15 <= elapsed <= 0.6


def test_create_excel_and_word_generation():
    # Test create_excel
    excel_data = "| Имя | Значение |\n|---|---|\n| Тест 1 | 100 |\n| Тест 2 | 200 |"
    excel_path = FileGenerator.create_excel("UnitTestExcel", excel_data)
    assert isinstance(excel_path, Path)
    assert excel_path.exists()
    assert excel_path.suffix.lower() == ".xlsx"
    with open(excel_path, "rb") as f:
        header = f.read(4)
        assert header.startswith(b"PK")

    # Test create_word
    word_text = "# Заголовок 1\n## Подзаголовок\n- Пункт 1\n- Пункт 2\nОбычный текст"
    word_path = FileGenerator.create_word("UnitTestWord", word_text)
    assert isinstance(word_path, Path)
    assert word_path.exists()
    assert word_path.suffix.lower() == ".docx"
    with open(word_path, "rb") as f:
        header = f.read(4)
        assert header.startswith(b"PK")

    # Cleanup generated exports
    try:
        excel_path.unlink()
    except Exception:
        pass
    try:
        word_path.unlink()
    except Exception:
        pass


def test_patch_file_transactional_atomic_and_fsync():
    ws_dir = SandboxManager.get_workspace_dir()
    test_file = ws_dir / "test_atomic_script.py"
    initial_code = 'def greet():\n    print("Hello Old")\n'
    test_file.write_text(initial_code, encoding="utf-8")

    try:
        res = SelfHealingEngine.patch_file_transactional(
            filename="test_atomic_script.py",
            search_block='    print("Hello Old")',
            replace_block='    print("Hello Atomic World")'
        )
        assert isinstance(res, PatchResult)
        assert res.success is True
        assert 'print("Hello Atomic World")' in res.new_content

        # Verify disk content is updated
        disk_content = test_file.read_text(encoding="utf-8")
        assert 'print("Hello Atomic World")' in disk_content

        # Verify temp and bak files are cleaned up
        tmp_file = test_file.with_suffix(test_file.suffix + ".tmp")
        bak_file = test_file.with_suffix(test_file.suffix + ".bak")
        assert not tmp_file.exists()
        assert not bak_file.exists()
    finally:
        if test_file.exists():
            test_file.unlink()


def test_patch_file_transactional_syntax_error_rollback():
    ws_dir = SandboxManager.get_workspace_dir()
    test_file = ws_dir / "test_rollback_script.py"
    initial_code = 'def valid_func():\n    return 42\n'
    test_file.write_text(initial_code, encoding="utf-8")

    try:
        # Cause syntax error to trigger rollback
        res = SelfHealingEngine.patch_file_transactional(
            filename="test_rollback_script.py",
            search_block='    return 42',
            replace_block='    return invalid syntax (('
        )
        assert isinstance(res, PatchResult)
        assert res.success is False
        assert "Ошибка синтаксиса" in res.error

        # Target file must have been rolled back to initial code
        disk_content = test_file.read_text(encoding="utf-8")
        assert disk_content == initial_code

        # Temp and bak files must be cleaned up
        tmp_file = test_file.with_suffix(test_file.suffix + ".tmp")
        bak_file = test_file.with_suffix(test_file.suffix + ".bak")
        assert not tmp_file.exists()
        assert not bak_file.exists()
    finally:
        if test_file.exists():
            test_file.unlink()
