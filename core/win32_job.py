"""
core/win32_job.py - Управление Windows Job Objects для гарантированного завершения дочерних процессов.

При аварийном завершении родительского процесса NeonRoute операционная система Windows
автоматически завершает все дочерние процессы, привязанные к Job Object с флагом
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (0x2000).
"""

import sys
import ctypes
import logging
from typing import Optional, Any

logger = logging.getLogger("NeonRoute.Win32Job")

# Константы Win32 Job Object
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryLimit", ctypes.c_size_t),
        ("PeakJobMemoryLimit", ctypes.c_size_t),
    ]


class Win32JobManager:
    """Управляет глобальным объектом задания Windows для гарантированного убийства потомков."""

    _instance: Optional["Win32JobManager"] = None
    _job_handle = None

    @classmethod
    def get_instance(cls) -> "Win32JobManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._init_job_object()

    def _init_job_object(self):
        if sys.platform != "win32":
            return
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                err = kernel32.GetLastError()
                err_str = f" (GetLastError={err})" if err else ""
                logger.warning(f"[Win32Job] Не удалось создать Job Object via CreateJobObjectW{err_str}.")
                self._job_handle = None
                return

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

            success = kernel32.SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not success:
                err = kernel32.GetLastError()
                err_str = f" (GetLastError={err})" if err else ""
                logger.warning(f"[Win32Job] Не удалось настроить SetInformationJobObject{err_str}.")
                kernel32.CloseHandle(handle)
                self._job_handle = None
                return

            self._job_handle = handle
            logger.info("[Win32Job] Windows Job Object успешно инициализирован (KILL_ON_JOB_CLOSE).")
        except Exception as e:
            logger.warning(f"[Win32Job] Исключение при инициализации Job Object: {e}")
            self._job_handle = None

    def assign_process(self, process_handle: Any) -> bool:
        """Привязывает процесс (дескриптор или объект subprocess.Popen) к объекту задания."""
        if sys.platform != "win32":
            return False
        if not self._job_handle:
            logger.warning("[Win32Job] Невозможно привязать процесс: Job Object не инициализирован.")
            return False
        try:
            kernel32 = ctypes.windll.kernel32
            # Если передан subprocess.Popen, берем его _handle
            raw_handle = getattr(process_handle, "_handle", process_handle)
            pid = getattr(process_handle, "pid", "unknown")
            if raw_handle:
                try:
                    handle_val = int(raw_handle)
                except (ValueError, TypeError):
                    handle_val = raw_handle
                res = kernel32.AssignProcessToJobObject(self._job_handle, handle_val)
                if not res:
                    err = kernel32.GetLastError()
                    err_str = f" (GetLastError={err})" if err else ""
                    logger.warning(f"[Win32Job] Не удалось привязать процесс PID={pid} к Job Object{err_str}.")
                    return False
                return True
        except Exception as e:
            logger.warning(f"[Win32Job] Исключение при привязке процесса к Job Object: {e}")
        return False
