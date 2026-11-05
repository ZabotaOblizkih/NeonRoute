"""
Unit tests for core.win32_job (Windows Job Object process lifetime manager).
"""

import sys
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from core.win32_job import Win32JobManager


def test_win32_job_manager_singleton():
    """Проверяет паттерн Singleton для Win32JobManager.get_instance()."""
    mgr1 = Win32JobManager.get_instance()
    mgr2 = Win32JobManager.get_instance()
    assert mgr1 is mgr2
    assert isinstance(mgr1, Win32JobManager)


def test_win32_job_non_windows_fallback():
    """Мок sys.platform = 'linux': инициализация и вызовы assign_process возвращают False без падений."""
    with patch("sys.platform", "linux"):
        orig_instance = Win32JobManager._instance
        try:
            Win32JobManager._instance = None
            linux_mgr = Win32JobManager()
            assert linux_mgr._job_handle is None

            mock_proc = MagicMock()
            mock_proc._handle = 12345
            result = linux_mgr.assign_process(mock_proc)
            assert result is False

            # Также проверяем передачу сырого дескриптора
            assert linux_mgr.assign_process(12345) is False
        finally:
            Win32JobManager._instance = orig_instance


def test_win32_job_assign_popen_mock():
    """Проверяет корректное извлечение _handle из объекта subprocess.Popen и передачу в AssignProcessToJobObject."""
    orig_instance = Win32JobManager._instance
    try:
        Win32JobManager._instance = None
        mgr = Win32JobManager()
        # Имитируем активный job handle
        mgr._job_handle = 9999

        mock_kernel32 = MagicMock()
        mock_kernel32.AssignProcessToJobObject.return_value = 1

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc._handle = 54321

        with patch("ctypes.windll.kernel32", mock_kernel32), patch("sys.platform", "win32"):
            ok = mgr.assign_process(mock_proc)
            assert ok is True
            mock_kernel32.AssignProcessToJobObject.assert_called_once_with(9999, 54321)

        # Проверяем передачу дескриптора напрямую (без объекта Popen)
        mock_kernel32.reset_mock()
        with patch("ctypes.windll.kernel32", mock_kernel32), patch("sys.platform", "win32"):
            ok_raw = mgr.assign_process(7777)
            assert ok_raw is True
            mock_kernel32.AssignProcessToJobObject.assert_called_once_with(9999, 7777)
    finally:
        Win32JobManager._instance = orig_instance


def test_win32_job_handles_api_failure():
    """Проверка устойчивости при возврате None или 0 из вызовов kernel32 (без необработанных исключений)."""
    orig_instance = Win32JobManager._instance
    try:
        # Сценарий 1: CreateJobObjectW возвращает 0 / None
        mock_k32_1 = MagicMock()
        mock_k32_1.CreateJobObjectW.return_value = 0
        with patch("ctypes.windll.kernel32", mock_k32_1), patch("sys.platform", "win32"):
            Win32JobManager._instance = None
            mgr1 = Win32JobManager()
            assert mgr1._job_handle is None
            assert mgr1.assign_process(1234) is False

        # Сценарий 2: SetInformationJobObject возвращает 0 (ошибка установки флагов)
        mock_k32_2 = MagicMock()
        mock_k32_2.CreateJobObjectW.return_value = 1111
        mock_k32_2.SetInformationJobObject.return_value = 0
        with patch("ctypes.windll.kernel32", mock_k32_2), patch("sys.platform", "win32"):
            Win32JobManager._instance = None
            mgr2 = Win32JobManager()
            assert mgr2._job_handle is None
            mock_k32_2.CloseHandle.assert_called_once_with(1111)
            assert mgr2.assign_process(1234) is False

        # Сценарий 3: AssignProcessToJobObject возвращает 0 (сбой привязки)
        mock_k32_3 = MagicMock()
        mock_k32_3.CreateJobObjectW.return_value = 2222
        mock_k32_3.SetInformationJobObject.return_value = 1
        mock_k32_3.AssignProcessToJobObject.return_value = 0
        with patch("ctypes.windll.kernel32", mock_k32_3), patch("sys.platform", "win32"):
            Win32JobManager._instance = None
            mgr3 = Win32JobManager()
            assert mgr3._job_handle == 2222
            assert mgr3.assign_process(1234) is False

        # Сценарий 4: Вызов Win32 API возбуждает исключение
        mock_k32_4 = MagicMock()
        mock_k32_4.CreateJobObjectW.side_effect = OSError("Win32 fatal error")
        with patch("ctypes.windll.kernel32", mock_k32_4), patch("sys.platform", "win32"):
            Win32JobManager._instance = None
            mgr4 = Win32JobManager()
            assert mgr4._job_handle is None
            assert mgr4.assign_process(1234) is False
    finally:
        Win32JobManager._instance = orig_instance
