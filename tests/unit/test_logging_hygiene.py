"""
tests/unit/test_logging_hygiene.py
==================================
Тесты гигиены логирования, обработки сбоев pythonw.exe, threading.excepthook и faulthandler.
"""

import io
import sys
import threading
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import main


def test_write_to_crash_log(tmp_path, monkeypatch):
    """Проверка корректной записи в crash.log."""
    fake_logs = tmp_path / "logs"
    fake_logs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "LOGS_DIR", fake_logs)

    main._write_to_crash_log("TEST CRASH HEADER", "Traceback line 1\nTraceback line 2")

    crash_file = fake_logs / "crash.log"
    assert crash_file.exists()
    content = crash_file.read_text(encoding="utf-8")
    assert "TEST CRASH HEADER" in content
    assert "Traceback line 1" in content
    assert "Traceback line 2" in content


def test_threading_exception_handler(tmp_path, monkeypatch, caplog):
    """Проверка перехвата исключений в фоновых потоках через threading.excepthook."""
    fake_logs = tmp_path / "logs"
    fake_logs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "LOGS_DIR", fake_logs)

    dummy_thread = threading.Thread(name="WorkerThread-42")
    try:
        raise ValueError("Simulated background thread error")
    except ValueError as val_err:
        exc_type = type(val_err)
        exc_value = val_err
        exc_tb = val_err.__traceback__

    hook_args = threading.ExceptHookArgs((exc_type, exc_value, exc_tb, dummy_thread))
    with caplog.at_level(logging.ERROR):
        main.threading_exception_handler(hook_args)

    # Логгер должен зафиксировать ошибку с именем потока
    assert any("WorkerThread-42" in r.message for r in caplog.records)
    assert any("Simulated background thread error" in r.message for r in caplog.records)

    # Файл crash.log должен содержать запись
    crash_file = fake_logs / "crash.log"
    assert crash_file.exists()
    content = crash_file.read_text(encoding="utf-8")
    assert "WorkerThread-42" in content
    assert "Simulated background thread error" in content


def test_pythonw_stream_redirection_when_none(tmp_path, monkeypatch):
    """
    Проверка, что если sys.stdout и sys.stderr равны None (как в pythonw.exe),
    setup_environment_and_logging() перенаправляет их в валидные файлы логов.
    """
    fake_logs = tmp_path / "logs"
    fake_logs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(main, "LOGS_DIR", fake_logs)

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        sys.stdout = None
        sys.stderr = None

        main.setup_environment_and_logging()

        # Потоки больше не None
        assert sys.stdout is not None
        assert sys.stderr is not None

        # Методы write/flush работают без исключений
        sys.stdout.write("test stdout line\n")
        sys.stdout.flush()
        sys.stderr.write("test stderr line\n")
        sys.stderr.flush()

        stdout_file = fake_logs / "stdout.log"
        stderr_file = fake_logs / "stderr.log"
        assert stdout_file.exists()
        assert stderr_file.exists()
        assert "test stdout line" in stdout_file.read_text(encoding="utf-8")
        assert "test stderr line" in stderr_file.read_text(encoding="utf-8")
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
