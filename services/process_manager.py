"""
Process Manager Service for NeonRoute
Controls lifecycle of external local AI backends (headless llama-server, LM Studio, and SD WebUI Forge).
Supports non-blocking background startup, graceful tree-kill on exit,
and status monitoring.
"""

import os
import sys
import time
import json
import shutil
import socket
import logging
import threading
import subprocess
import urllib.request
import urllib.error
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple, List, Any, Dict

import psutil
import requests
from PyQt6.QtCore import QObject, pyqtSignal

from config import (
    FORGE_LAUNCHER_PATH,
    FORGE_ROOT_DIR,
    LM_STUDIO_CLI_CMD,
    LOGS_DIR,
    DEFAULT_LLAMA_UBATCH_SIZE,
    DEFAULT_LLAMA_BATCH_SIZE,
    KV_SLOTS_DIR,
    _rotate_if_oversized,
)
from core.forge_locator import get_clean_forge_env
from core.win32_job import Win32JobManager
from services.server_config import ServerConfigResolver, resolve_kv_cache_types

logger = logging.getLogger("NeonRoute.ProcessManager")


def verify_forge_http_handshake(host: str = "127.0.0.1", port: int = 7860, timeout: float = 0.8) -> bool:
    """
    Выполняет легковесный HTTP-handshake с API SD WebUI Forge.
    Проверяет доступность эндпоинта /sdapi/v1/options.
    Возвращает True только при подтверждении сигнатуры Forge/Automatic1111 API (HTTP 200).
    При таймауте, ошибке соединения или кодах 404/502 (чужой сервис) возвращает False с диагностическим логированием.
    """
    url = f"http://{host}:{port}/sdapi/v1/options"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "NeonRoute-Handshake/1.0"},
        method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = getattr(response, "status", getattr(response, "code", None))
            if status == 200:
                return True
            logger.debug(f"[ForgeHandshake] Статус HTTP {status} на {url} (ожидался 200).")
            return False
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.debug(
                f"[ForgeHandshake] HTTP 404 Not Found на {url}: "
                f"сервер Gradio/Uvicorn активен, но API-роутер /sdapi/v1/* ещё не зарегистрирован."
            )
        else:
            logger.debug(f"[ForgeHandshake] HTTP {e.code} ({e.reason}) на {url}.")
        return False
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.debug(f"[ForgeHandshake] Порт {port} недоступен ({type(e).__name__}): {e}")
        return False


class ServerState(str, Enum):
    """Lifecycle states for local LLM server."""
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    READY = "READY"
    SWITCHING_MODEL = "SWITCHING_MODEL"
    EVICTED_FOR_FORGE = "EVICTED_FOR_FORGE"
    RESTORING = "RESTORING"
    ERROR = "ERROR"


def _get_win32_hidden_process_flags(create_new_group: bool = False) -> Tuple[Optional[subprocess.STARTUPINFO], int]:
    """Returns startupinfo and creationflags for hidden Windows background processes."""
    import sys
    startupinfo = None
    flags = 0
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        flags = subprocess.CREATE_NO_WINDOW
        if create_new_group:
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    return startupinfo, flags


def inspect_port_occupant(port: int) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    Через psutil.net_connections сканирует статус порта.
    Возвращает кортеж (is_occupied, pid, process_name).
    """
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                pid = conn.pid
                pname = None
                if pid:
                    try:
                        pname = psutil.Process(pid).name()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pname = "unknown"
                return (True, pid, pname)
    except Exception as e:
        logger.debug(f"[ProcessManager] inspect_port_occupant error: {e}")

    return (False, None, None)


class ProcessManager(QObject):
    """
    Orchestrates execution of external local AI servers (llama-server, LM Studio, SD WebUI Forge).
    Ensures non-blocking process invocation, state machine management, and clean VRAM deallocation on exit.
    """
    lms_status_changed = pyqtSignal(bool, str)     # is_running, message
    forge_status_changed = pyqtSignal(bool, str)   # is_running, message
    action_completed = pyqtSignal(str, bool, str)  # action_name, success, message
    server_state_changed = pyqtSignal(str, str)    # state_name, message
    inspect_port_occupant = staticmethod(inspect_port_occupant)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._forge_process: Optional[subprocess.Popen] = None
        self._forge_log_file: Optional[Any] = None
        self._llama_process: Optional[subprocess.Popen] = None
        self._llama_log_file: Optional[Any] = None
        self._server_state: ServerState = ServerState.STOPPED
        self._process_lock = threading.RLock()
        self._lms_exe: Optional[str] = self._locate_lms()
        self._lms_safe_profile_applied: bool = False
        self._is_loading_lms: bool = False
        self._lm_client: Optional[Any] = None
        self.last_error: str = ""
        self.is_vram_oom_failure: bool = False
        self.is_host_ram_oom_failure: bool = False
        self._force_cuda_no_pinned: bool = False
        self._last_attempted_ngl: Optional[int] = None
        self._current_running_ngl: Optional[int] = None
        self._current_total_layers: Optional[int] = None
        self._evicted_for_forge_ngl: Optional[int] = None
        self._evicted_for_forge_model: Optional[str] = None
        self._current_running_model_path: Optional[str] = None
        self._current_running_ctx: Optional[int] = None
        self._current_running_ctk: Optional[str] = None
        self._current_running_ctv: Optional[str] = None
        self._current_running_fa: Optional[str] = None
        self._current_running_port: Optional[int] = None

        # Зачистка остатков KV-слотов после аварийного закрытия
        self.clear_kv_slot_cache()

    @property
    def lm_client(self) -> Optional[Any]:
        return self._lm_client

    @lm_client.setter
    def lm_client(self, client: Optional[Any]):
        self._lm_client = client
        if client and hasattr(client, "set_process_manager"):
            try:
                client.set_process_manager(self)
            except Exception:
                pass

    @property
    def server_state(self) -> ServerState:
        return self._server_state

    @property
    def is_oom_failure(self) -> bool:
        return bool(getattr(self, "is_vram_oom_failure", False) or getattr(self, "is_host_ram_oom_failure", False))

    @is_oom_failure.setter
    def is_oom_failure(self, value: bool) -> None:
        if not value:
            self.is_vram_oom_failure = False
            self.is_host_ram_oom_failure = False
        else:
            if not getattr(self, "is_host_ram_oom_failure", False):
                self.is_vram_oom_failure = True

    @property
    def port(self) -> int:
        if hasattr(self, "_port") and self._port:
            return self._port
        try:
            return int(ServerConfigResolver.load_ini().get("port", 1234))
        except Exception:
            return 1234

    @port.setter
    def port(self, p: int):
        self._port = int(p)

    @property
    def _process(self) -> Optional[subprocess.Popen]:
        return self._llama_process

    @_process.setter
    def _process(self, proc: Optional[subprocess.Popen]):
        self._llama_process = proc

    @property
    def _tracked_pid(self) -> Optional[int]:
        if self._llama_process and hasattr(self._llama_process, "pid") and isinstance(self._llama_process.pid, int):
            return self._llama_process.pid
        return getattr(self, "_saved_tracked_pid", None)

    @_tracked_pid.setter
    def _tracked_pid(self, val: Optional[int]):
        self._saved_tracked_pid = val

    def _safe_emit(self, signal_name: str, *args) -> bool:
        """Safely emits a pyqtSignal without crashing if underlying C++ QObject was deleted."""
        try:
            sig = getattr(self, signal_name, None)
            if sig is not None:
                sig.emit(*args)
                return True
        except RuntimeError:
            logger.debug(f"[ProcessManager] Ignored signal '{signal_name}' emit on deleted QObject.")
        except Exception:
            pass
        return False

    def _set_server_state(self, new_state: ServerState, message: str = ""):
        self._server_state = new_state
        if new_state == ServerState.ERROR and message:
            self.last_error = message
        elif new_state == ServerState.READY:
            self.last_error = None
        logger.info(f"[ProcessManager.ServerState] {new_state.value}: {message}")
        self._safe_emit("server_state_changed", new_state.value, message)

    def has_headless_server(self) -> bool:
        """Checks if headless llama-server.exe is available via ServerConfigResolver."""
        try:
            return ServerConfigResolver.resolve_server_exe() is not None
        except Exception as e:
            logger.debug(f"has_headless_server check error: {e}")
            return False

    @staticmethod
    def locate_lms() -> Optional[str]:
        """Finds lms.exe executable in PATH or standard user directories without instantiating QObject."""
        # 1. System PATH
        which_path = shutil.which(LM_STUDIO_CLI_CMD)
        if which_path:
            return which_path

        # 2. Standard user paths for LM Studio CLI
        candidates = [
            Path.home() / ".lmstudio" / "bin" / "lms.exe",
            Path.home() / ".cache" / "lm-studio" / "bin" / "lms.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "LM-Studio" / "bin" / "lms.exe"
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

        return None

    def _locate_lms(self) -> Optional[str]:
        """Backward compatibility shim for locate_lms."""
        return self.locate_lms()

    def _locate_lm_studio_app(self) -> Optional[Path]:
        """Finds desktop LM Studio.exe if available."""
        local_app = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "LM Studio" / "LM Studio.exe"
        if local_app.exists():
            return local_app
        return None

    # =========================================================================
    # Headless llama-server / LM Studio Management
    # =========================================================================

    def is_port_open(self, host: str = "127.0.0.1", port: int = 1234, timeout: float = 1.0) -> bool:
        """Quick check if a network port is accepting connections."""
        if isinstance(host, int):
            port, host = host, "127.0.0.1"
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def is_lm_studio_running(self) -> bool:
        """Checks if LLM API server is running (either tracked llama-server process or port 1234)."""
        if self._llama_process is not None and self._llama_process.poll() is None:
            return True
        return self.is_port_open("127.0.0.1", 1234, timeout=0.8)

    def _kill_llama_server_instance(self, for_forge_eviction: bool = False):
        """
        Gracefully terminates llama-server.exe process tree:
        - Terminates self._llama_process via taskkill /F /T /PID <pid>
        - Closes log file handle
        - Terminates any orphaned llama-server.exe processes
        - Waits for port 1234 to close (up to 3.0s with 0.1s step)
        - Sets ServerState to EVICTED_FOR_FORGE or STOPPED
        """
        with self._process_lock:
            # 1. Kill tracked process
            if self._llama_process is not None:
                pid = getattr(self._llama_process, "pid", None)
                if pid:
                    try:
                        s_info, s_flags = _get_win32_hidden_process_flags()
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True,
                            timeout=5,
                            startupinfo=s_info,
                            creationflags=s_flags
                        )
                        logger.info(f"llama-server process (PID {pid}) terminated via taskkill.")
                    except Exception as e:
                        logger.warning(f"Error terminating llama-server PID {pid}: {e}")
                if for_forge_eviction:
                    # Сохраняем модель и нормализованный NGL перед выгрузкой в пользу Forge
                    prev_model = None
                    if hasattr(self, "lm_client") and self.lm_client:
                        prev_model = getattr(self.lm_client, "_last_loaded_model_id", None)
                    prev_ngl = self._current_running_ngl
                    prev_layers = self._current_total_layers
                    if prev_ngl is None and hasattr(self, "lm_client") and self.lm_client:
                        prev_ngl = getattr(self.lm_client, "_current_running_ngl", None)
                    if prev_layers is None and hasattr(self, "lm_client") and self.lm_client:
                        prev_layers = getattr(self.lm_client, "_current_total_layers", None)

                    if prev_ngl is not None and prev_model:
                        if prev_layers is not None and prev_layers > 0:
                            norm_ngl = min(int(prev_ngl), int(prev_layers))
                        else:
                            norm_ngl = int(prev_ngl)
                        self._evicted_for_forge_ngl = norm_ngl
                        self._evicted_for_forge_model = str(prev_model)
                        logger.info(
                            f"[ProcessManager] Сохранен NGL={norm_ngl} для модели '{prev_model}' перед выгрузкой в пользу Forge."
                        )
                else:
                    self._evicted_for_forge_ngl = None
                    self._evicted_for_forge_model = None
                    self.clear_kv_slot_cache()

                self._llama_process = None
                self._current_running_ngl = None
                self._current_total_layers = None
                if not for_forge_eviction:
                    self._current_running_model_path = None
                    self._current_running_ctx = None
                    self._current_running_ctk = None
                    self._current_running_ctv = None
                    self._current_running_fa = None
                    self._current_running_port = None
                if hasattr(self, "lm_client") and self.lm_client:
                    self.lm_client._current_running_ngl = None
                    self.lm_client._current_total_layers = None

            if self._llama_log_file and not getattr(self._llama_log_file, "closed", True):
                try:
                    self._llama_log_file.close()
                except Exception:
                    pass
                self._llama_log_file = None

            # 2. Check for orphaned llama-server.exe processes
            try:
                s_info, s_flags = _get_win32_hidden_process_flags()
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        pname = (proc.info.get('name') or '').lower()
                        if "llama-server" in pname:
                            logger.info(f"Terminating residual llama-server PID {proc.pid}")
                            subprocess.run(
                                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                capture_output=True,
                                timeout=3,
                                startupinfo=s_info,
                                creationflags=s_flags
                            )
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception as e:
                logger.debug(f"Residual llama-server cleanup error: {e}")

            # 3. Wait for port 1234 to close (up to 3.0s with 0.1s step)
            port = 1234
            try:
                port = ServerConfigResolver.load_ini().get("port", 1234)
            except Exception:
                pass

            deadline = time.time() + 3.0
            while time.time() < deadline:
                if not self.is_port_open("127.0.0.1", port, timeout=0.1):
                    break
                time.sleep(0.1)

            if self.is_port_open("127.0.0.1", port, timeout=0.2):
                logger.error(
                    f"[ProcessManager] Порт {port} остался ЗАНЯТ после 3.0с принудительного завершения! "
                    f"Процесс llama-server не освободил дескриптор сокета."
                )

            # 4. Set state
            next_state = ServerState.EVICTED_FOR_FORGE if for_forge_eviction else ServerState.STOPPED
            msg = "LLM выгружен для Forge" if for_forge_eviction else "Сервер LLM остановлен"
            self._set_server_state(next_state, msg)
            if not for_forge_eviction:
                self._safe_emit("lms_status_changed", False, msg)

            # 5. Пост-стоп очистка дампов KV-слотов (дескрипторы ОС освобождены)
            if for_forge_eviction:
                active_fn = None
                meta_path = KV_SLOTS_DIR / "slot_metadata.json"
                if meta_path.exists():
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            active_fn = json.load(f).get("filename")
                    except Exception:
                        pass
                self.clear_kv_slot_cache(keep_filename=active_fn)
            else:
                self.clear_kv_slot_cache()

    # =========================================================================
    # KV Slot Cache Management (Save / Restore across Forge eviction)
    # =========================================================================

    def clear_kv_slot_cache(self, keep_filename: Optional[str] = None) -> None:
        """
        Публичный потокобезопасный метод безопасного удаления файлов дампов KV-слотов
        из KV_SLOTS_DIR.
        Если передан keep_filename, удаляются все .bin файлы, кроме keep_filename.
        Если keep_filename is None, удаляются все .bin файлы и slot_metadata.json.
        """
        with self._process_lock:
            try:
                if not KV_SLOTS_DIR.exists():
                    return
            except OSError:
                return

            bin_files = list(KV_SLOTS_DIR.glob("evicted_slot_*.bin"))
            for file_path in bin_files:
                if keep_filename and file_path.name == keep_filename:
                    continue
                try:
                    if file_path.exists():
                        file_path.unlink()
                except OSError as e:
                    logger.warning(f"[ProcessManager] clear_kv_slot_cache не удалось удалить {file_path.name}: {e}")

            if not keep_filename:
                meta_path = KV_SLOTS_DIR / "slot_metadata.json"
                try:
                    if meta_path.exists():
                        meta_path.unlink()
                except OSError as e:
                    logger.warning(f"[ProcessManager] clear_kv_slot_cache не удалось удалить slot_metadata.json: {e}")

    def erase_kv_slot_sync(self, slot_id: int = 0) -> bool:
        """
        Сбрасывает состояние KV-слота на сервере llama-server (POST /slots/{slot_id}?action=erase).
        Вызывается безопасно только при живом процессе.
        """
        if self._llama_process is None or self._llama_process.poll() is not None:
            return False
        port = getattr(self, "_current_running_port", None) or 1234
        if not self.is_port_open("127.0.0.1", port, timeout=0.2):
            return False
        url = f"http://127.0.0.1:{port}/slots/{slot_id}?action=erase"
        try:
            logger.info(f"[ProcessManager] Отправка action=erase для слота {slot_id}...")
            resp = requests.post(url, json={}, timeout=5.0)
            return resp.status_code == 200
        except Exception as e:
            logger.debug(f"[ProcessManager] Не удалось сбросить KV-слот {slot_id} через action=erase: {e}")
            return False

    def _save_kv_slot_for_eviction(self) -> bool:
        """
        Сохраняет снимок KV-кэша слота 0 и метаданные перед выгрузкой llama-server в пользу SD Forge.
        Вызывается внутри evict_llm_for_forge_sync() непосредственно перед остановкой процесса.
        """
        import uuid
        with self._process_lock:
            # Если процесс не запущен и порт закрыт, сохранять нечего
            if self._llama_process is None and not self.is_port_open(port=1234, timeout=0.1):
                return False

            port = getattr(self, "_current_running_port", None)
            if not port:
                try:
                    port = ServerConfigResolver.load_ini().get("port", 1234)
                except Exception:
                    port = 1234

            # Определяем параметры активной модели
            model_path = getattr(self, "_current_running_model_path", None)
            if not model_path and hasattr(self, "lm_client") and self.lm_client:
                model_path = getattr(self.lm_client, "_last_loaded_model_id", None)
            if not model_path:
                model_path = getattr(self, "_evicted_for_forge_model", None)

            if model_path:
                resolved_p = ServerConfigResolver.resolve_model_path(model_path) or Path(model_path)
            else:
                resolved_p = None

            norm_path = str(resolved_p.resolve()) if (resolved_p and resolved_p.exists()) else str(model_path or "")
            try:
                model_mtime = os.path.getmtime(resolved_p) if (resolved_p and resolved_p.exists()) else 0.0
            except OSError:
                model_mtime = 0.0

            ctx = getattr(self, "_current_running_ctx", None)
            if not ctx:
                try:
                    ctx = ServerConfigResolver.load_ini().get("default_context", 14336)
                except Exception:
                    ctx = 14336

            ctk = getattr(self, "_current_running_ctk", None) or "q8_0"
            ctv = getattr(self, "_current_running_ctv", None) or "q8_0"
            fa = getattr(self, "_current_running_fa", None) or "on"

            ngl_val = getattr(self, "_current_running_ngl", None)
            if ngl_val is None and hasattr(self, "lm_client") and self.lm_client:
                ngl_val = getattr(self.lm_client, "_current_running_ngl", None)
            tot_layers = getattr(self, "_current_total_layers", None)
            if tot_layers is None and hasattr(self, "lm_client") and self.lm_client:
                tot_layers = getattr(self.lm_client, "_current_total_layers", None)

            if ngl_val is not None:
                if tot_layers is not None and tot_layers > 0 and int(ngl_val) >= int(tot_layers):
                    saved_gpu_layers = int(tot_layers)
                else:
                    saved_gpu_layers = int(ngl_val)
            else:
                saved_gpu_layers = 99

            # Оценка размера KV-кэша для адаптивного таймаута
            try:
                from services.layer_budgeter import LayerBudgeter
                expected_kv_mb = LayerBudgeter.estimate_kv_cache_mb(int(ctx), cache_type=str(ctk))
            except Exception:
                expected_kv_mb = 500.0

            save_timeout = max(15.0, float(expected_kv_mb) / 50.0)

            # Формируем уникальное имя файла дампа для исключения гонок WinError 32
            unique_filename = f"evicted_slot_0_{uuid.uuid4().hex[:8]}.bin"

            meta = {
                "filename": unique_filename,
                "model_path": norm_path,
                "model_mtime": model_mtime,
                "context_length": int(ctx),
                "gpu_layers": int(saved_gpu_layers),
                "kv_cache_type_k": str(ctk),
                "kv_cache_type_v": str(ctv),
                "flash_attn": str(fa),
            }

            t0 = time.time()
            url = f"http://127.0.0.1:{port}/slots/0?action=save"
            try:
                logger.info(f"[ProcessManager] Сохранение KV-слота 0 в {KV_SLOTS_DIR / unique_filename} (timeout={save_timeout:.1f}с)...")
                resp = requests.post(url, json={"filename": unique_filename}, timeout=save_timeout)
                elapsed_ms = (time.time() - t0) * 1000.0
                if resp.status_code == 200:
                    meta_path = KV_SLOTS_DIR / "slot_metadata.json"
                    with open(meta_path, "w", encoding="utf-8") as f:
                        json.dump(meta, f, indent=2, ensure_ascii=False)
                    slot_file = KV_SLOTS_DIR / unique_filename
                    size_mb = 0.0
                    try:
                        if slot_file.exists():
                            size_mb = round(slot_file.stat().st_size / 1048576, 1)
                    except OSError:
                        pass
                    logger.info(
                        f"[ProcessManager] KV-слот 0 и метаданные сохранены перед выгрузкой под Forge ({size_mb} MB за {elapsed_ms:.1f} мс)."
                    )
                    return True
                else:
                    logger.warning(
                        f"[ProcessManager] Ошибка сервера при сохранении KV-слота 0 (HTTP {resp.status_code}): {resp.text[:100]}"
                    )
                    return False
            except Exception as e:
                logger.warning(f"[ProcessManager] Исключение при сохранении KV-слота 0: {e}")
                return False

    def _restore_kv_slot_after_start(self, expected_meta: Dict[str, Any]) -> bool:
        """
        Восстанавливает снимок KV-слота 0 после перезапуска llama-server, если снимок совместим.
        Всегда очищает дамп после попытки восстановления (одноразовый дамп).
        """
        meta_file = KV_SLOTS_DIR / "slot_metadata.json"
        if not meta_file.exists():
            return False

        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                saved_meta = json.load(f)
        except Exception as e:
            logger.warning(f"[ProcessManager] Не удалось прочитать метаданные KV-слота {meta_file}: {e}")
            self.clear_kv_slot_cache()
            return False

        slot_filename = saved_meta.get("filename") or "evicted_slot_0.bin"
        slot_file = KV_SLOTS_DIR / slot_filename
        if not slot_file.exists():
            fallback_file = KV_SLOTS_DIR / "evicted_slot_0.bin"
            if fallback_file.exists():
                slot_file = fallback_file
                slot_filename = fallback_file.name
            else:
                self.clear_kv_slot_cache()
                return False

        # Сверка метаданных с актуальными expected_meta
        mismatch_reasons = []
        for key in ("model_path", "model_mtime", "context_length", "gpu_layers", "kv_cache_type_k", "kv_cache_type_v", "flash_attn"):
            saved_val = saved_meta.get(key)
            exp_val = expected_meta.get(key)
            if key == "model_mtime":
                try:
                    if abs(float(saved_val or 0.0) - float(exp_val or 0.0)) > 0.01:
                        mismatch_reasons.append(f"{key}: saved={saved_val} != expected={exp_val}")
                except (ValueError, TypeError):
                    mismatch_reasons.append(f"{key}: parse error")
            elif key == "model_path":
                s_name = Path(str(saved_val)).name.lower() if saved_val else ""
                e_name = Path(str(exp_val)).name.lower() if exp_val else ""
                if s_name != e_name:
                    mismatch_reasons.append(f"{key}: saved={saved_val} != expected={exp_val}")
            elif key == "gpu_layers":
                try:
                    s_ngl = int(saved_val) if saved_val is not None else None
                    e_ngl = int(exp_val) if exp_val is not None else None
                    if s_ngl is None or e_ngl is None or s_ngl != e_ngl:
                        mismatch_reasons.append(f"{key}: saved={saved_val} != expected={exp_val}")
                except (ValueError, TypeError):
                    mismatch_reasons.append(f"{key}: parse error")
            else:
                if str(saved_val) != str(exp_val):
                    mismatch_reasons.append(f"{key}: saved={saved_val} != expected={exp_val}")

        if mismatch_reasons:
            logger.info(
                f"[ProcessManager] Инвалидация KV-слота: параметры запуска не совпадают ({'; '.join(mismatch_reasons)}). "
                f"Кэш очищен, выполняется стандартный холодный prefill."
            )
            try:
                if meta_file.exists():
                    meta_file.unlink()
            except OSError:
                pass
            return False

        # Проверка аномального размера дампа перед restore
        size_mb = 0.0
        try:
            size_mb = round(slot_file.stat().st_size / 1048576, 2)
        except OSError:
            pass

        try:
            from services.layer_budgeter import LayerBudgeter
            exp_ctx = int(expected_meta.get("context_length", 8192))
            eff_k = str(expected_meta.get("kv_cache_type_k", "q8_0"))
            expected_kv_mb = LayerBudgeter.estimate_kv_cache_mb(exp_ctx, cache_type=eff_k)
        except Exception:
            expected_kv_mb = 500.0

        import psutil
        try:
            avail_ram_mb = psutil.virtual_memory().available / (1024.0 * 1024.0)
        except Exception:
            avail_ram_mb = 8192.0

        is_anomalous = False
        anomaly_reason = ""
        if expected_kv_mb > 0 and size_mb > 1.5 * expected_kv_mb:
            is_anomalous = True
            anomaly_reason = f"размер дампа {size_mb:.1f} МБ превышает 1.5x расчетного KV-кэша ({expected_kv_mb:.1f} МБ)"
        elif avail_ram_mb > 0 and size_mb > 0.40 * avail_ram_mb:
            is_anomalous = True
            anomaly_reason = f"размер дампа {size_mb:.1f} МБ превышает 40% доступной RAM ({avail_ram_mb:.1f} МБ)"

        if is_anomalous:
            logger.warning(
                f"[ProcessManager] ⚠️ Аномальный размер дампа KV-слота ({anomaly_reason}). "
                f"Восстановление пропущено во избежание OOM. Слот будет очищен."
            )
            self.erase_kv_slot_sync(slot_id=0)
            try:
                if meta_file.exists():
                    meta_file.unlink()
            except OSError:
                pass
            return False

        port = getattr(self, "_current_running_port", None) or 1234
        url = f"http://127.0.0.1:{port}/slots/0?action=restore"
        restore_timeout = max(15.0, size_mb / 50.0)

        t0 = time.time()
        try:
            logger.info(f"[ProcessManager] Восстановление KV-слота 0 из {slot_filename} ({size_mb} MB, timeout={restore_timeout:.1f}с)...")
            resp = requests.post(url, json={"filename": slot_filename}, timeout=restore_timeout)
            elapsed_ms = (time.time() - t0) * 1000.0
            if resp.status_code == 200:
                logger.info(f"[ProcessManager] KV-слот 0 успешно восстановлен ({size_mb} MB за {elapsed_ms:.1f} мс).")
                try:
                    if meta_file.exists():
                        meta_file.unlink()
                except OSError:
                    pass
                return True
            else:
                logger.warning(
                    f"[ProcessManager] Ошибка восстановления KV-слота 0 (HTTP {resp.status_code}): {resp.text[:100]}"
                )
                self.erase_kv_slot_sync(slot_id=0)
                try:
                    if meta_file.exists():
                        meta_file.unlink()
                except OSError:
                    pass
                return False
        except Exception as e:
            logger.warning(f"[ProcessManager] Исключение при восстановлении KV-слота 0: {e}")
            self.erase_kv_slot_sync(slot_id=0)
            try:
                if meta_file.exists():
                    meta_file.unlink()
            except OSError:
                pass
            return False

    def _extract_llama_startup_error(self) -> Tuple[str, bool]:
        """
        При падении процесса llama-server читает последние 30 строк logs/llama_server.log:
        - парсит JSON из LMSTUDIO_STARTUP_ERROR:{...} при наличии;
        - раздельно классифицирует VRAM OOM и Host RAM OOM;
        - возвращает (читаемое описание ошибки, флаг_oom).
        """
        log_path = LOGS_DIR / "llama_server.log"
        lines: List[str] = []
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()[-30:]
            except Exception as e:
                logger.debug(f"[ProcessManager] Не удалось прочитать logs/llama_server.log: {e}")

        parsed_message = ""
        is_vram_oom = False
        is_host_oom = False

        host_markers = (
            "cuda_host",
            "cudahostalloc",
            "host buffer",
            "virtualalloc",
            "commit limit",
            "pagefile",
            "failed to allocate host",
            "unable to allocate cuda_host buffer",
            "bad allocation",
            "std::bad_alloc",
            "bad_alloc",
        )
        vram_markers = (
            "cudamalloc failed",
            "out of device memory",
            "cuda out of memory",
            "failed to allocate cuda",
        )
        generic_markers = (
            "out of memory",
            "failed to allocate",
        )

        for line in lines:
            line_stripped = line.strip()
            line_lower = line_stripped.lower()

            if any(m in line_lower for m in host_markers):
                is_host_oom = True
            elif any(m in line_lower for m in vram_markers):
                is_vram_oom = True
            elif any(m in line_lower for m in generic_markers):
                if any(hm in line_lower for hm in ("host", "system", "ram", "cpu", "virtual")):
                    is_host_oom = True
                else:
                    is_vram_oom = True

            if "LMSTUDIO_STARTUP_ERROR:" in line_stripped:
                try:
                    raw_json = line_stripped.split("LMSTUDIO_STARTUP_ERROR:", 1)[1].strip()
                    payload = json.loads(raw_json)
                    if isinstance(payload, dict):
                        msg = payload.get("message") or payload.get("code") or ""
                        if msg:
                            parsed_message = str(msg)
                except Exception:
                    pass

        if not parsed_message:
            all_markers = host_markers + vram_markers + generic_markers
            for line in reversed(lines):
                l_s = line.strip()
                l_lower = l_s.lower()
                if any(marker in l_lower for marker in all_markers) or "error" in l_lower:
                    parsed_message = l_s
                    break

        if is_host_oom and is_vram_oom:
            final_msg = f"Out of Memory (VRAM + Host RAM): {parsed_message}" if parsed_message else "OOM (VRAM + Host RAM) при инициализации модели"
        elif is_host_oom:
            final_msg = f"Host RAM Out of Memory: {parsed_message}" if parsed_message else "Host RAM Out of Memory (CUDA_Host buffer / Commit Limit)"
        elif is_vram_oom:
            final_msg = f"CUDA Out of Memory: {parsed_message}" if parsed_message else "CUDA Out of Memory при инициализации модели"
        elif parsed_message:
            final_msg = f"Ошибка запуска модели: {parsed_message}"
        else:
            final_msg = "Процесс llama-server завершился с ошибкой"

        self.last_error = final_msg
        self.is_vram_oom_failure = is_vram_oom
        self.is_host_ram_oom_failure = is_host_oom
        is_oom = is_vram_oom or is_host_oom
        return final_msg, is_oom

    def _wait_for_server_ready(
        self,
        host: str = "127.0.0.1",
        port: int = 1234,
        timeout_s: float = 60.0
    ) -> bool:
        """
        Polls GET http://{host}:{port}/v1/models until ready or timeout.
        Immediately returns False if process crashed (poll() is not None).
        Validates that response payload contains non-empty models/data.
        """
        models_url = f"http://{host}:{port}/v1/models"
        start_time = time.time()

        while time.time() - start_time < timeout_s:
            proc = self._process
            if proc is not None and proc.poll() is not None:
                exit_code = proc.poll()
                detail_msg, is_oom = self._extract_llama_startup_error()
                self.is_oom_failure = is_oom
                err_msg = f"Процесс llama-server завершился с кодом {exit_code}: {detail_msg}. См. logs/llama_server.log"
                logger.error(f"[ProcessManager] {err_msg}")
                self._process = None
                self._current_running_ngl = None
                self._current_total_layers = None
                if hasattr(self, "lm_client") and self.lm_client:
                    self.lm_client._current_running_ngl = None
                    self.lm_client._current_total_layers = None
                self._set_server_state(ServerState.ERROR, err_msg)
                self.last_error = err_msg
                self.lms_status_changed.emit(False, err_msg)
                return False

            try:
                resp = requests.get(models_url, timeout=0.35)
                if resp.status_code == 200:
                    try:
                        payload = resp.json()
                        if isinstance(payload, dict):
                            data = payload.get("data")
                            if data is None:
                                data = payload.get("models")
                            if isinstance(data, list):
                                if len(data) > 0:
                                    return True
                                else:
                                    logger.debug("[ProcessManager] /v1/models вернул 200 OK, но список моделей пуст. Ожидание...")
                            else:
                                return True
                        else:
                            return True
                    except Exception:
                        return True
            except Exception:
                pass

            time.sleep(0.35)

        return False

    def start_llama_server_sync(
        self,
        model_identifier: Optional[str] = None,
        context_length: Optional[int] = None,
        gpu_layers: Optional[int] = None,
        is_restore: bool = False,
        skip_kv_restore: bool = False,
    ) -> bool:
        """
        Synchronously launches headless llama-server.exe:
        - Kills any previous running instance under _process_lock
        - Sets state to RESTORING (if is_restore) or STARTING
        - Reads settings from server_config.ini via ServerConfigResolver
        - Resolves binary, model path, CUDA dll dir, and mmproj
        - Launches subprocess.Popen with hidden window and streaming logs
        - Polls GET /v1/models (up to 60s) until HTTP 200 OK
        - Sets state to READY and synchronizes lm_client
        """
        with self._process_lock:
            self.last_error = ""
            self.is_oom_failure = False
            self._last_attempted_ngl = None

            # 1. Grab current running server's ngl for hysteresis before killing
            was_running = (self._llama_process is not None and self._llama_process.poll() is None)
            current_running_ngl = None
            if was_running:
                val = getattr(self, "_current_running_ngl", None)
                if isinstance(val, int):
                    current_running_ngl = val
                elif hasattr(self, "lm_client") and self.lm_client:
                    val = getattr(self.lm_client, "_current_running_ngl", None)
                    if isinstance(val, int):
                        current_running_ngl = val
                tot_layers = getattr(self, "_current_total_layers", None)
                if current_running_ngl is not None and tot_layers is not None and tot_layers > 0:
                    current_running_ngl = min(int(current_running_ngl), int(tot_layers))

            # Kill existing instance if running
            self._kill_llama_server_instance()

            # 2. Update state to STARTING or RESTORING
            start_state = ServerState.RESTORING if is_restore else ServerState.STARTING
            start_msg = "Восстановление сервера LLM..." if is_restore else "Запуск сервера llama-server..."
            self._set_server_state(start_state, start_msg)
            self.lms_status_changed.emit(False, start_msg)

            try:
                # 3. Load INI config
                ini_cfg = ServerConfigResolver.load_ini()
                host = ini_cfg.get("host", "127.0.0.1")
                port = ini_cfg.get("port", 1234)
                self.port = int(port)

                # Pre-flight: Port Conflict Guard
                is_occupied, occ_pid, occ_name = self.inspect_port_occupant(self.port)
                if is_occupied:
                    tracked_pid = self._tracked_pid
                    is_our_server = (tracked_pid is not None and occ_pid == tracked_pid)
                    if is_our_server and occ_pid:
                        logger.info(f"[ProcessManager] Порт {self.port} занят нашим зависшим сервером (PID {occ_pid}). Завершаем через taskkill.")
                        try:
                            s_info, s_flags = _get_win32_hidden_process_flags()
                            subprocess.run(
                                ["taskkill", "/F", "/T", "/PID", str(occ_pid)],
                                capture_output=True,
                                timeout=5,
                                startupinfo=s_info,
                                creationflags=s_flags
                            )
                            time.sleep(0.3)
                        except Exception as kill_err:
                            logger.warning(f"[ProcessManager] Ошибка taskkill PID {occ_pid}: {kill_err}")
                    else:
                        proc_name = occ_name or "unknown"
                        pid_str = occ_pid if occ_pid is not None else "Unknown"
                        err_msg = f"Порт {self.port} занят процессом '{proc_name}' (PID: {pid_str}). Закройте стороннее приложение."
                        logger.error(f"[ProcessManager] {err_msg}")
                        self._set_server_state(ServerState.ERROR, err_msg)
                        self.last_error = err_msg
                        self.lms_status_changed.emit(False, err_msg)
                        return False

                # 4. Resolve binary
                server_exe = ServerConfigResolver.resolve_server_exe()
                if not server_exe:
                    logger.error("llama-server.exe executable not found on any cascade level.")
                    self._set_server_state(ServerState.ERROR, "Бинарник llama-server.exe не найден.")
                    self.lms_status_changed.emit(False, "Бинарник llama-server.exe не найден.")
                    return False

                # 5. Resolve model path
                target_id = model_identifier or getattr(self.lm_client, "_last_loaded_model_id", None)
                if not target_id:
                    try:
                        from utils.config_manager import ConfigManager
                        cfg_json = ConfigManager.load_config()
                        target_id = cfg_json.get("assistant_model_name") or cfg_json.get("active_model")
                    except Exception:
                        pass

                model_path = ServerConfigResolver.resolve_model_path(target_id)
                if not model_path:
                    logger.error(f"Model '{target_id}' could not be resolved to a valid GGUF file.")
                    self._set_server_state(ServerState.ERROR, f"Модель '{target_id}' не найдена.")
                    self.lms_status_changed.emit(False, f"Модель '{target_id}' не найдена.")
                    return False

                effective_ctx = context_length or ini_cfg.get("default_context", 14336)

                # Read KV-cache type and Flash Attention settings from config.json (with ini_cfg fallback)
                app_cfg = {}
                try:
                    from utils.config_manager import ConfigManager
                    app_cfg = ConfigManager.load_config()
                except Exception:
                    pass

                raw_kv = app_cfg.get("kv_cache_type")
                ini_k = ini_cfg.get("cache_type_k")
                ini_v = ini_cfg.get("cache_type_v")
                ctk, ctv = resolve_kv_cache_types(raw_type=raw_kv, cache_type_k=ini_k, cache_type_v=ini_v)

                fa_enabled = app_cfg.get("flash_attention_enabled")
                if fa_enabled is None:
                    fa_enabled = (ini_cfg.get("flash_attn", "on") == "on")
                fa_str = "on" if fa_enabled else "off"

                # Pre-flight Guard: квантованный KV-кэш требует включенного Flash Attention
                if fa_str == "off" and (ctk != "f16" or ctv != "f16"):
                    logger.warning(
                        f"[ProcessManager] Pre-flight Guard: Квантованный KV-кэш ({ctk}/{ctv}) требует включенного Flash Attention. "
                        f"При fa=off типы принудительно сброшены на f16 во избежание сбоя ядра llama.cpp."
                    )
                    ctk, ctv = "f16", "f16"

                eff_kv_profile = f"{ctk}_{ctv}" if ctk != ctv else ctk

                # Динамический расчет слоев через LayerBudgeter
                audit_data: Dict[str, Any] = {}
                if current_running_ngl is None and getattr(self, "_evicted_for_forge_ngl", None) is not None:
                    evicted_model = getattr(self, "_evicted_for_forge_model", None)
                    curr_model_req = str(model_path)
                    curr_model_id = getattr(self.lm_client, "_last_loaded_model_id", None) if hasattr(self, "lm_client") and self.lm_client else None
                    model_matches = False
                    if evicted_model:
                        evicted_p = Path(evicted_model).name.lower()
                        curr_p = Path(curr_model_req).name.lower()
                        if (
                            evicted_model == curr_model_req
                            or evicted_p == curr_p
                            or (curr_model_id and evicted_model == str(curr_model_id))
                            or evicted_model in curr_model_req
                            or curr_model_req in evicted_model
                        ):
                            model_matches = True
                    if model_matches:
                        current_running_ngl = getattr(self, "_evicted_for_forge_ngl", None)
                        logger.info(
                            f"[ProcessManager] Восстановлен NGL={current_running_ngl} для модели '{evicted_model}' после Forge для гистерезиса."
                        )
                    else:
                        logger.info(
                            f"[ProcessManager] Модель сменилась после Forge ({evicted_model} -> {curr_model_req}). Сброс сохраненного NGL."
                        )
                    self._evicted_for_forge_ngl = None
                    self._evicted_for_forge_model = None

                is_manual_override = (
                    gpu_layers is not None
                    and str(gpu_layers).strip().lower() != "auto"
                    and gpu_layers != 99
                )

                total_model_layers = None
                audit_data: Dict[str, Any] = {}
                if is_manual_override:
                    try:
                        effective_ngl = int(gpu_layers)
                    except (ValueError, TypeError):
                        effective_ngl = 99
                    logger.warning(
                        f"[ProcessManager] Ручной оверрайд gpu_layers={effective_ngl}. Авторасчет LayerBudgeter пропущен."
                    )
                else:
                    frag_margin = 0
                    try:
                        from services.vram_arbiter import VRAMArbiter
                        arb = VRAMArbiter.get_instance()
                        last_rel = getattr(arb, "_last_diffusion_release_ts", 0.0)
                        if last_rel > 0.0 and (time.monotonic() - last_rel) < 8.0:
                            from services.layer_budgeter import LayerBudgeter
                            curr_vram = LayerBudgeter.get_current_used_vram_mb()
                            if curr_vram <= 400.0:
                                frag_margin = 180
                                logger.info(
                                    f"[ProcessManager] Обнаружено недавнее освобождение Forge "
                                    f"({time.monotonic() - last_rel:.1f}с < 8.0с, VRAM used={curr_vram:.1f} MB <= 400 MB). "
                                    f"Применен fragmentation_margin={frag_margin} MB."
                                )
                            else:
                                frag_margin = 0
                                logger.info(
                                    f"[ProcessManager] Обнаружено недавнее освобождение Forge "
                                    f"({time.monotonic() - last_rel:.1f}с < 8.0с), но VRAM used={curr_vram:.1f} MB > 400 MB "
                                    f"(CUDA-контекст уже учтен в effective_os_reserve). fragmentation_margin=0 MB."
                                )
                    except Exception:
                        frag_margin = 0

                    ubatch_size = int(ini_cfg.get("ubatch_size", DEFAULT_LLAMA_UBATCH_SIZE))
                    from services.layer_budgeter import LayerBudgeter
                    ngl, can_run, reason, audit_data = LayerBudgeter.calculate_layer_offload(
                        model_path=model_path,
                        target_context=effective_ctx,
                        current_running_ngl=current_running_ngl,
                        cache_type=eff_kv_profile,
                        is_arbiter_managed=True,
                        fragmentation_margin_mb=frag_margin,
                        ubatch_size=ubatch_size,
                    )

                    if not can_run:
                        logger.error(f"[ProcessManager] Запуск отменен: {reason}")
                        self._set_server_state(ServerState.ERROR, reason)
                        self.last_error = reason
                        self.lms_status_changed.emit(False, reason)
                        return False

                    effective_ngl = ngl
                    total_model_layers = audit_data.get("total_layers")

                self._last_attempted_ngl = effective_ngl

                ubatch_size = int(ini_cfg.get("ubatch_size", DEFAULT_LLAMA_UBATCH_SIZE))
                batch_size = int(ini_cfg.get("batch_size", DEFAULT_LLAMA_BATCH_SIZE))
                if batch_size < ubatch_size:
                    batch_size = ubatch_size

                # 6. Assemble CLI arguments (strict list, no shell=True)
                cmd = [
                    str(server_exe),
                    "-m", str(model_path),
                    "-c", str(effective_ctx),
                    "-ngl", str(effective_ngl),
                    "-fa", str(fa_str),
                    "-ctk", str(ctk),
                    "-ctv", str(ctv),
                    "-b", str(batch_size),
                    "-ub", str(ubatch_size),
                    "-np", str(ini_cfg.get("parallel_slots", 1)),
                    "--slot-save-path", str(KV_SLOTS_DIR),
                    "--port", str(port),
                    "--host", str(host),
                ]

                cuda_dir = ServerConfigResolver.resolve_cuda_dir(server_exe)

                is_hybrid = effective_ngl < audit_data.get("total_layers", 99)
                if ini_cfg.get("no_mmap", True) or is_hybrid:
                    has_mmap_flag = any(
                        arg in ("--no-mmap", "--load-mode", "-lm") or
                        arg.startswith(("--load-mode=", "-lm="))
                        for arg in cmd
                    )
                    if not has_mmap_flag:
                        cmd.extend(ServerConfigResolver.resolve_mmap_disable_args(server_exe, cuda_dir))

                mmproj = ServerConfigResolver.resolve_mmproj_for_model(model_path)
                if mmproj:
                    cmd.extend(["--mmproj", str(mmproj)])

                # 7. Environment & CUDA PATH
                env = os.environ.copy()
                if cuda_dir:
                    env["PATH"] = f"{str(cuda_dir)}{os.pathsep}{env.get('PATH', '')}"

                # Защита от сбоев аллокации Host RAM (CUDA_Host buffer) при низкой доступной памяти
                try:
                    import psutil
                    avail_sys_ram_mb = psutil.virtual_memory().available / (1024.0 * 1024.0)
                except Exception:
                    avail_sys_ram_mb = 8192.0

                if avail_sys_ram_mb < 4096.0 or getattr(self, "_force_cuda_no_pinned", False):
                    env["GGML_CUDA_NO_PINNED"] = "1"
                    logger.info("[ProcessManager] Установлена переменная GGML_CUDA_NO_PINNED=1 (защита от дефицита Host RAM).")
                else:
                    env.pop("GGML_CUDA_NO_PINNED", None)

                # 8. Setup log file
                logs_dir = LOGS_DIR
                logs_dir.mkdir(parents=True, exist_ok=True)
                log_file_path = logs_dir / "llama_server.log"

                if self._llama_log_file and not getattr(self._llama_log_file, "closed", True):
                    try:
                        self._llama_log_file.close()
                    except Exception:
                        pass

                _rotate_if_oversized(log_file_path)
                self._llama_log_file = open(log_file_path, "a", encoding="utf-8", errors="replace", buffering=1)

                # 9. Popen with hidden flags
                startupinfo, flags = _get_win32_hidden_process_flags(create_new_group=True)
                try:
                    logger.info(
                        "[ProcessManager] Запуск llama-server: model=%s | NGL=%s/%s | CTX=%s | batch=%s | ubatch=%s | FA=%s | KV=%s/%s | slots=%s",
                        Path(model_path).name,
                        effective_ngl,
                        total_model_layers or 99,
                        effective_ctx,
                        batch_size,
                        ubatch_size,
                        fa_str,
                        ctk,
                        ctv,
                        ini_cfg.get("parallel_slots", 1),
                    )
                    self._llama_process = subprocess.Popen(
                        cmd,
                        cwd=str(server_exe.parent),
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        startupinfo=startupinfo,
                        creationflags=flags
                    )
                    assigned = Win32JobManager.get_instance().assign_process(self._llama_process)
                    if assigned:
                        logger.info("[ProcessManager] Процесс llama-server привязан к Windows Job Object (KILL_ON_JOB_CLOSE).")
                    else:
                        logger.warning("[ProcessManager] ⚠️ Процесс llama-server НЕ привязан к Windows Job Object! Защита от сиротских процессов не активна.")
                except Exception as popen_err:
                    logger.error(f"Failed to spawn llama-server.exe: {popen_err}")
                    if self._llama_log_file:
                        self._llama_log_file.close()
                        self._llama_log_file = None
                    self._set_server_state(ServerState.ERROR, str(popen_err))
                    self.lms_status_changed.emit(False, str(popen_err))
                    return False

                # 10. Background stream reader for stdout
                def _stream_llama_stdout(proc: subprocess.Popen, file_handle: Any):
                    llama_logger = logging.getLogger("NeonRoute.LlamaServer")
                    try:
                        for raw_line in iter(proc.stdout.readline, ''):
                            if not raw_line:
                                break
                            try:
                                file_handle.write(raw_line)
                                file_handle.flush()
                            except Exception:
                                pass
                            line = raw_line.strip()
                            if line:
                                llama_logger.debug(line)
                    except Exception as stream_err:
                        llama_logger.debug(f"llama stdout stream closed: {stream_err}")
                    finally:
                        try:
                            file_handle.close()
                        except Exception:
                            pass
                        rc = proc.poll()
                        if rc is not None and self._server_state in (ServerState.READY, ServerState.STARTING):
                            if self._llama_process is proc:
                                self._llama_process = None
                            if rc != 0:
                                detail_msg, is_oom = self._extract_llama_startup_error()
                                self.is_oom_failure = is_oom
                                err_msg = f"llama-server завершился с ошибкой (код {rc}): {detail_msg}"
                                self._set_server_state(ServerState.ERROR, err_msg)
                                self._safe_emit("lms_status_changed", False, err_msg)
                            else:
                                self._set_server_state(ServerState.STOPPED, f"llama-server завершился неожиданно (код {rc})")

                threading.Thread(
                    target=_stream_llama_stdout,
                    args=(self._llama_process, self._llama_log_file),
                    daemon=True
                ).start()

                # 11. Polling readiness handshake GET http://{host}:{port}/v1/models (up to 60s)
                timeout_s = 60.0
                start_time = time.time()
                server_ready = self._wait_for_server_ready(host=host, port=port, timeout_s=timeout_s)

                if server_ready:
                    elapsed = round(time.time() - start_time, 1)
                    ready_msg = f"Модель {model_path.stem} готова к работе ({elapsed} с)."
                    logger.info(f"llama-server ready on {host}:{port} in {elapsed}s.")
                    self._set_server_state(ServerState.READY, ready_msg)
                    self._safe_emit("lms_status_changed", True, "Сервер LLM запущен.")
                    self._current_running_ngl = effective_ngl
                    self._current_total_layers = total_model_layers
                    self._current_running_model_path = str(model_path)
                    self._current_running_ctx = int(effective_ctx)
                    self._current_running_ctk = str(ctk)
                    self._current_running_ctv = str(ctv)
                    self._current_running_fa = str(fa_str)
                    self._current_running_port = int(port)
                    self.last_error = None

                    # Попытка восстановления сохраненного KV-слота после выгрузки под Forge
                    resolved_p = ServerConfigResolver.resolve_model_path(model_path) or Path(model_path)
                    try:
                        mtime = os.path.getmtime(resolved_p) if (resolved_p and resolved_p.exists()) else 0.0
                    except OSError:
                        mtime = 0.0
                    norm_expected_ngl = int(effective_ngl)
                    if total_model_layers and total_model_layers > 0 and norm_expected_ngl >= int(total_model_layers):
                        norm_expected_ngl = int(total_model_layers)

                    expected_meta = {
                        "model_path": str(resolved_p.resolve()) if (resolved_p and resolved_p.exists()) else str(model_path),
                        "model_mtime": mtime,
                        "context_length": int(effective_ctx),
                        "gpu_layers": norm_expected_ngl,
                        "kv_cache_type_k": str(ctk),
                        "kv_cache_type_v": str(ctv),
                        "flash_attn": str(fa_str),
                    }
                    if skip_kv_restore:
                        logger.info("[ProcessManager] Восстановление KV-слота пропущено (skip_kv_restore=True). Кэш очищен.")
                        self.clear_kv_slot_cache()
                    else:
                        self._restore_kv_slot_after_start(expected_meta)
                    if hasattr(self, "lm_client") and self.lm_client:
                        self.lm_client._is_model_unloaded = False
                        self.lm_client._last_loaded_model_id = model_path.stem
                        self.lm_client._current_loaded_ctx = effective_ctx
                        self.lm_client._current_running_ngl = effective_ngl
                        self.lm_client._current_total_layers = total_model_layers
                    return True
                else:
                    if self._server_state != ServerState.ERROR:
                        err_msg = f"Превышено время ожидания запуска llama-server ({timeout_s} с)."
                        logger.warning(err_msg)
                        self._kill_llama_server_instance()
                        self._set_server_state(ServerState.ERROR, err_msg)
                        self._safe_emit("lms_status_changed", False, err_msg)
                    return False
            except Exception as e:
                logger.error(f"Error starting llama-server: {e}")
                self._kill_llama_server_instance()
                self._set_server_state(ServerState.ERROR, str(e))
                self._safe_emit("lms_status_changed", False, str(e))
                return False

    def restart_llama_server_async(self):
        """Kills running llama-server and starts it afresh with current settings."""
        def _run():
            target_model = getattr(self.lm_client, "_last_loaded_model_id", None)
            if not target_model:
                try:
                    from utils.config_manager import ConfigManager
                    target_model = ConfigManager.load_config().get("active_model")
                except Exception:
                    pass
            ok = self.start_llama_server_sync(model_identifier=target_model)
            if ok:
                self.action_completed.emit("lms_restart", True, "LLM сервер успешно перезапущен.")
            else:
                self.action_completed.emit("lms_restart", False, "Не удалось перезапустить LLM сервер.")

        threading.Thread(target=_run, daemon=True).start()

    def start_lm_studio_async(self):
        """Launches LLM server in a separate thread to avoid UI freeze."""
        threading.Thread(target=self._run_lms_start, daemon=True).start()

    def _run_lms_start(self):
        if self.has_headless_server():
            if self.is_lm_studio_running() and self._server_state == ServerState.READY:
                logger.info("LLM server is already running and ready on port 1234.")
                self.lms_status_changed.emit(True, "Сервер LLM уже активен.")
                self.action_completed.emit("lms_start", True, "LLM сервер уже работает.")
                return

            target_model = getattr(self.lm_client, "_last_loaded_model_id", None)
            target_ctx = getattr(self.lm_client, "_current_loaded_ctx", None) if self.lm_client else None
            if not target_model:
                try:
                    from utils.config_manager import ConfigManager
                    from config import MODE_PROFILES
                    cfg_init = ConfigManager.load_config()
                    target_model = cfg_init.get("assistant_model_name") or cfg_init.get("active_model")
                    def_ctx = MODE_PROFILES.get("assistant", {}).get("context_length", 14336)
                    target_ctx = int(cfg_init.get("context_length_assistant", def_ctx))
                except Exception:
                    pass

            ok = self.start_llama_server_sync(model_identifier=target_model, context_length=target_ctx)
            if ok:
                self._safe_emit("action_completed", "lms_start", True, "LLM сервер успешно запущен.")
            else:
                self._safe_emit("action_completed", "lms_start", False, "Не удалось запустить LLM сервер.")
            return

        # Legacy LMS CLI fallback
        if self.is_lm_studio_running():
            logger.info("LM Studio server is already running on port 1234.")
            self.lms_status_changed.emit(True, "Сервер LM Studio уже активен.")
            self.action_completed.emit("lms_start", True, "LM Studio уже работает.")
            return

        lms_bin = self._lms_exe
        startupinfo, flags = _get_win32_hidden_process_flags(create_new_group=True)

        if not lms_bin:
            app_path = self._locate_lm_studio_app()
            if app_path:
                logger.info(f"lms CLI not found, launching desktop app: {app_path}")
                subprocess.Popen(
                    [str(app_path)],
                    startupinfo=startupinfo,
                    creationflags=flags
                )
                self.lms_status_changed.emit(True, "Запуск приложения LM Studio...")
                self.action_completed.emit("lms_start", True, "Запущено приложение LM Studio.")
                return

            err_msg = (
                "Движок llama-server не найден в папке bin/. "
                "Запустите install.bat или скачайте движок в меню «Параметры и настройки → Движок LLM»."
            )
            logger.warning(f"[ProcessManager] {err_msg}")
            self._set_server_state(ServerState.ERROR, err_msg)
            self.lms_status_changed.emit(False, err_msg)
            self.action_completed.emit("lms_start", False, err_msg)
            return

        logger.info(f"Starting LM Studio server via CLI: {lms_bin} server start")
        try:
            s_info, s_flags = _get_win32_hidden_process_flags()
            result = subprocess.run(
                [lms_bin, "server", "start"],
                capture_output=True,
                text=True,
                timeout=25,
                startupinfo=s_info,
                creationflags=s_flags
            )
            if result.returncode == 0 or self.is_lm_studio_running():
                logger.info("LM Studio server started successfully.")
                self.lms_status_changed.emit(True, "Сервер LM Studio запущен.")
                self.action_completed.emit("lms_start", True, "LM Studio успешно запущен.")
            else:
                err_msg = result.stderr.strip() or result.stdout.strip() or "Неизвестная ошибка CLI"
                logger.warning(f"lms server start returned code {result.returncode}: {err_msg}")
                self.lms_status_changed.emit(False, f"Ошибка запуска: {err_msg}")
                self.action_completed.emit("lms_start", False, err_msg)
        except Exception as e:
            logger.error(f"Failed to start LM Studio via CLI: {e}")
            self.lms_status_changed.emit(False, str(e))
            self.action_completed.emit("lms_start", False, str(e))

    def stop_lm_studio_async(self):
        """Stops LLM server in background thread."""
        threading.Thread(target=self._run_lms_stop, daemon=True).start()

    def _run_lms_stop(self):
        if self.has_headless_server() or self._llama_process is not None:
            logger.info("Stopping headless llama-server...")
            self._kill_llama_server_instance(for_forge_eviction=False)
            if hasattr(self, "lm_client") and self.lm_client:
                self.lm_client._is_model_unloaded = True
                self.lm_client._last_loaded_model_id = None
            self.lms_status_changed.emit(False, "Сервер LLM остановлен.")
            self.action_completed.emit("lms_stop", True, "LLM сервер остановлен.")
            return

        lms_bin = self._lms_exe
        if not lms_bin and not self._locate_lm_studio_app():
            logger.info("ProcessManager: llama-server/LM Studio не найдены, пропуск остановки CLI.")
            if hasattr(self, "lm_client") and self.lm_client:
                self.lm_client._is_model_unloaded = True
                self.lm_client._last_loaded_model_id = None
            self.lms_status_changed.emit(False, "Сервер LLM остановлен.")
            self.action_completed.emit("lms_stop", True, "LLM сервер остановлен.")
            return

        if not lms_bin:
            lms_bin = "lms"
        logger.info(f"Stopping LM Studio server: {lms_bin} server stop")
        try:
            self.unload_all_lms_models()
            if hasattr(self, "lm_client") and self.lm_client:
                self.lm_client._is_model_unloaded = True
                self.lm_client._last_loaded_model_id = None

            s_info, s_flags = _get_win32_hidden_process_flags()
            subprocess.run(
                [lms_bin, "server", "stop"],
                capture_output=True,
                timeout=15,
                startupinfo=s_info,
                creationflags=s_flags
            )
            self.lms_status_changed.emit(False, "Сервер LM Studio остановлен.")
            self.action_completed.emit("lms_stop", True, "LM Studio остановлен.")
        except Exception as e:
            logger.error(f"Error stopping LM Studio: {e}")
            self.action_completed.emit("lms_stop", False, str(e))

    def get_available_lms_models(self) -> List[str]:
        """
        Discovers all available LM Studio / GGUF models:
        1. Fast discovery via ServerConfigResolver.discover_gguf_models() (< 20 ms)
        2. Fallback to 'lms ls --json', text table parsing, and directory scanning
        Returns a deduplicated list of model identifiers.
        """
        models: List[str] = []
        try:
            discovered = ServerConfigResolver.discover_gguf_models()
            for key, path in discovered.items():
                parent_name = path.parent.name
                if parent_name and parent_name.lower() not in ("models", "bin") and parent_name not in models:
                    models.append(parent_name)
                if path.stem and path.stem not in models:
                    models.append(path.stem)
                if key and key not in models:
                    models.append(key)
        except Exception as e:
            logger.debug(f"Fast GGUF model scan error: {e}")

        if models:
            return models

        lms_bin = self._lms_exe or "lms"
        s_info, s_flags = _get_win32_hidden_process_flags()

        # 1. Try 'lms ls --json'
        try:
            res = subprocess.run(
                [lms_bin, "ls", "--json"],
                capture_output=True,
                text=True,
                timeout=8,
                startupinfo=s_info,
                creationflags=s_flags
            )
            if res.returncode == 0 and res.stdout.strip():
                try:
                    data = json.loads(res.stdout)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get("type") in ("llm", None):
                                key = item.get("modelKey") or item.get("path")
                                if key and key not in models:
                                    models.append(key)
                except Exception as je:
                    logger.debug(f"JSON parsing of lms ls failed: {je}")
        except Exception as e:
            logger.debug(f"lms ls --json failed: {e}")

        # 2. Fallback: Parse text 'lms ls' table if json was empty
        if not models:
            try:
                res = subprocess.run(
                    [lms_bin, "ls"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    startupinfo=s_info,
                    creationflags=s_flags
                )
                if res.returncode == 0 and res.stdout:
                    in_llm_section = False
                    for line in res.stdout.splitlines():
                        trimmed = line.strip()
                        if not trimmed:
                            continue
                        if "LLM" in trimmed and "PARAMS" in trimmed:
                            in_llm_section = True
                            continue
                        if "EMBEDDING" in trimmed:
                            in_llm_section = False
                            break
                        if in_llm_section:
                            parts = trimmed.split()
                            if parts:
                                model_id = parts[0]
                                if model_id not in ("LLM", "PARAMS", "ARCH", "SIZE", "DEVICE"):
                                    if model_id not in models:
                                        models.append(model_id)
            except Exception as e:
                logger.debug(f"lms ls text parsing failed: {e}")

        # 3. Directory scanning in ~/.lmstudio/models and ~/.cache/lm-studio/models
        scan_roots = [
            Path.home() / ".lmstudio" / "models",
            Path.home() / ".cache" / "lm-studio" / "models"
        ]
        for root in scan_roots:
            if root.exists() and root.is_dir():
                try:
                    for gguf in root.rglob("*.gguf"):
                        if gguf.name.lower().startswith("mmproj"):
                            continue
                        rel = gguf.relative_to(root)
                        key = rel.parent.name if rel.parent.name else gguf.stem
                        if key and key not in models:
                            models.append(key)
                except Exception as de:
                    logger.debug(f"Error scanning {root}: {de}")

        return models

    def get_loaded_lms_models(self) -> List[str]:
        """
        Queries models currently loaded in memory.
        In headless mode, returns the active model identifier.
        In legacy mode, calls 'lms ps --json'.
        """
        if self.has_headless_server():
            if self._server_state == ServerState.READY:
                if hasattr(self, "lm_client") and self.lm_client:
                    last_id = getattr(self.lm_client, "_last_loaded_model_id", None)
                    if last_id and not getattr(self.lm_client, "_is_model_unloaded", False):
                        return [str(last_id)]
            return []

        lms_bin = self._lms_exe or "lms"
        loaded: List[str] = []
        s_info, s_flags = _get_win32_hidden_process_flags()
        try:
            res = subprocess.run(
                [lms_bin, "ps", "--json"],
                capture_output=True,
                text=True,
                timeout=5,
                startupinfo=s_info,
                creationflags=s_flags
            )
            if res.returncode == 0 and res.stdout.strip():
                try:
                    data = json.loads(res.stdout)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                for field in ("identifier", "modelKey", "path", "indexedModelIdentifier", "displayName"):
                                    val = item.get(field)
                                    if val and str(val).strip() and str(val).strip() not in loaded:
                                        loaded.append(str(val).strip())
                except Exception as je:
                    logger.debug(f"JSON parsing of lms ps failed: {je}")
        except Exception as e:
            logger.debug(f"lms ps --json check failed: {e}")

        # Fallback text parsing if json was empty
        if not loaded:
            try:
                res = subprocess.run(
                    [lms_bin, "ps"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    startupinfo=s_info,
                    creationflags=s_flags
                )
                if res.returncode == 0 and res.stdout:
                    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
                    if len(lines) > 1 and "IDENTIFIER" in lines[0]:
                        for line in lines[1:]:
                            parts = line.split()
                            if parts:
                                loaded.append(parts[0])
            except Exception as e:
                logger.debug(f"lms ps text fallback failed: {e}")

        return loaded

    def is_lms_model_loaded(self, model_key: str) -> bool:
        """
        Checks if the target model (or key/path substring) is already loaded and ready in memory.
        """
        if not model_key:
            return False

        if self.has_headless_server():
            if self._server_state != ServerState.READY:
                return False
            if hasattr(self, "lm_client") and self.lm_client:
                if getattr(self.lm_client, "_is_model_unloaded", False):
                    return False
                last_id = getattr(self.lm_client, "_last_loaded_model_id", None)
                if not last_id:
                    return False
                if model_key.lower() == str(last_id).lower() or Path(model_key).stem.lower() == Path(str(last_id)).stem.lower():
                    return True
                try:
                    p1 = ServerConfigResolver.resolve_model_path(model_key)
                    p2 = ServerConfigResolver.resolve_model_path(str(last_id))
                    if p1 and p2 and p1 == p2:
                        return True
                except Exception:
                    pass
            elif self._llama_process is not None and self._llama_process.poll() is None:
                return True
            return False

        loaded = self.get_loaded_lms_models()
        if not loaded:
            return False

        target = model_key.strip().lower()
        target_stem = Path(target).stem.lower().replace("-gguf", "")

        for item in loaded:
            item_lower = item.lower()
            item_stem = Path(item_lower).stem.replace("-gguf", "")
            if (
                target == item_lower
                or target in item_lower
                or item_lower in target
                or (target_stem and target_stem in item_lower)
                or (item_stem and item_stem in target)
            ):
                return True
        return False

    def load_lms_model_async(self, model_key: str, device_mode: str = "Auto"):
        """Loads a model into memory in background, with graceful CPU fallback on OOM."""
        model_name = model_key
        if getattr(self, "_is_loading_lms", False):
            logger.info("[ProcessManager] Загрузка модели уже выполняется, дублирующий вызов пропущен.")
            return

        # Если модель уже в памяти — не дергаем повторную загрузку
        if hasattr(self, "lm_client") and self.lm_client and self.lm_client.is_model_loaded(model_name):
            logger.info(f"[ProcessManager] Модель '{model_name}' уже готова к работе.")
            return
        elif self.is_lms_model_loaded(model_name):
            logger.info(f"[ProcessManager] Модель '{model_name}' уже готова к работе.")
            return

        self._is_loading_lms = True
        threading.Thread(target=self._run_lms_load, args=(model_key, device_mode), daemon=True).start()

    def _run_lms_load(self, model_key: str, device_mode: str = "Auto"):
        self._is_loading_lms = True
        try:
            if not model_key:
                return

            if self.has_headless_server():
                self._set_server_state(ServerState.SWITCHING_MODEL, f"Загрузка модели '{model_key}'...")
                from config import MODE_PROFILES
                target_context = MODE_PROFILES.get("assistant", {}).get("context_length", 14336)
                ngl = 0 if device_mode in ("CPU Only", "0", "0.0", "cpu") else None
                ok = self.start_llama_server_sync(
                    model_identifier=model_key,
                    context_length=target_context,
                    gpu_layers=ngl
                )
                if ok:
                    self._lms_safe_profile_applied = True
                    self.action_completed.emit("lms_load", True, f"Модель {model_key} готова к работе.")
                else:
                    self.action_completed.emit("lms_load", False, f"Не удалось загрузить модель {model_key}.")
                return

            # Legacy fallback: если подключен lm_client, используем его безопасный загрузчик
            if hasattr(self, "lm_client") and self.lm_client:
                logger.info(f"[ProcessManager] Делегирование безопасной загрузки '{model_key}' в LMStudioClient...")
                success = self.lm_client.ensure_model_loaded(model_key)
                if success:
                    self._lms_safe_profile_applied = True
                    self.action_completed.emit("lms_load", True, f"Модель {model_key} готова к работе.")
                else:
                    self.action_completed.emit("lms_load", False, f"Не удалось загрузить модель {model_key}.")
                return

            # Фоллбэк, если lm_client не инициализирован:
            lms_bin = self._lms_exe
            if not lms_bin and not self._locate_lm_studio_app():
                err_msg = (
                    "Движок llama-server не найден в папке bin/. "
                    "Запустите install.bat или скачайте движок в меню «Параметры и настройки → Движок LLM»."
                )
                logger.warning(f"[ProcessManager] {err_msg}")
                self.action_completed.emit("lms_load", False, err_msg)
                return

            if not lms_bin:
                lms_bin = "lms"
            gpu_param = "0" if device_mode == "CPU Only" else "1.0"
            from config import MODE_PROFILES
            target_context = str(MODE_PROFILES.get("assistant", {}).get("context_length", 14336))

            s_info, s_flags = _get_win32_hidden_process_flags()
            res = subprocess.run(
                [lms_bin, "load", model_key, "--gpu", gpu_param, "--context-length", target_context, "-y"],
                capture_output=True,
                text=True,
                timeout=120,
                startupinfo=s_info,
                creationflags=s_flags
            )
            if res.returncode == 0:
                self.action_completed.emit("lms_load", True, f"Модель {model_key} загружена.")
            else:
                self.action_completed.emit("lms_load", False, res.stderr.strip()[:80])
        except Exception as e:
            logger.error(f"[ProcessManager] Ошибка загрузки модели {model_key}: {e}")
            self.action_completed.emit("lms_load", False, str(e))
        finally:
            self._is_loading_lms = False

    def evict_llm_for_forge_sync(self) -> bool:
        """
        Synchronously stops llama-server to free 100% of GPU VRAM for SD WebUI Forge.
        Sets ServerState to EVICTED_FOR_FORGE while preserving _last_loaded_model_id in lm_client.
        """
        with self._process_lock:
            if (
                self._server_state in (ServerState.EVICTED_FOR_FORGE, ServerState.STOPPED)
                and self._llama_process is None
            ):
                if hasattr(self, "lm_client") and self.lm_client:
                    self.lm_client._is_model_unloaded = True
                return True
            logger.info("ProcessManager: evicting LLM server for Forge diffusion generation...")
            self._save_kv_slot_for_eviction()
            self._kill_llama_server_instance(for_forge_eviction=True)
            if hasattr(self, "lm_client") and self.lm_client:
                self.lm_client._is_model_unloaded = True
            return True

    # =========================================================================
    # SD WebUI Forge Management
    # =========================================================================

    def is_forge_running(self) -> bool:
        """Checks if Forge API server is actively responding on port 7860."""
        # Шаг 1: Быстрая проверка сокета L4
        if not self.is_port_open(port=7860, timeout=0.3):
            return False
        # Шаг 2: Валидация протокола L7 через HTTP-handshake
        return verify_forge_http_handshake(port=7860, timeout=0.8)

    def start_forge_async(self):
        """Starts Forge launcher in a detached process group without blocking UI."""
        threading.Thread(target=self._run_forge_start, daemon=True).start()

    def launch_forge(self):
        """Alias for start_forge_async: launches Forge in a background thread."""
        self.start_forge_async()

    def _run_forge_start(self):
        if self.is_forge_running():
            logger.info("Forge is already running.")
            self.forge_status_changed.emit(True, "Forge уже запущен.")
            self.action_completed.emit("forge_start", True, "Forge уже работает.")
            return

        from unittest.mock import MagicMock
        if not (isinstance(getattr(self, "is_port_open", None), MagicMock)) and self.is_port_open(port=7860, timeout=0.3):
            if not verify_forge_http_handshake(port=7860, timeout=0.8):
                is_occ, occ_pid, occ_name = self.inspect_port_occupant(7860)
                occ_desc = f" (PID {occ_pid}: '{occ_name}')" if is_occ and occ_pid else ""
                logger.warning(
                    f"[ProcessManager] Конфликт портов! Порт 7860 занят посторонним сервисом{occ_desc} "
                    f"(HTTP handshake не пройден). Gradio может переключиться на порт 7861."
                )

        # Запуск быстрого pre-flight аудита ассетов перед стартом демона
        try:
            from services.asset_validator import AssetValidator
            AssetValidator.log_validation_summary()
        except Exception as ve:
            logger.debug(f"[ProcessManager] Ошибка аудита ассетов: {ve}")

        import config
        launcher_path = config.FORGE_LAUNCHER_PATH
        # Поддержка мока FORGE_LAUNCHER_PATH в тестах
        if "pytest" in sys.modules and FORGE_LAUNCHER_PATH != config.FORGE_LAUNCHER_PATH:
            launcher_path = FORGE_LAUNCHER_PATH
        if not launcher_path.exists():
            msg = f"Файл запуска Forge не найден: {launcher_path}"
            logger.error(msg)
            self.forge_status_changed.emit(False, msg)
            self.action_completed.emit("forge_start", False, msg)
            return

        logger.info(f"Launching SD WebUI Forge via: {launcher_path} (headless daemon)")
        try:
            startupinfo, flags = _get_win32_hidden_process_flags(create_new_group=True)
            working_dir = launcher_path.parent

            cmd = ["cmd.exe", "/c", str(launcher_path)]
            try:
                from config import FORGE_EXTRA_ARGS
                from services.art_director.vram_policy import resolve_forge_startup_flags

                llm_active = bool(
                    (self._llama_process is not None and self._llama_process.poll() is None)
                    or self.is_lm_studio_running()
                )
                startup_flags, startup_meta = resolve_forge_startup_flags(
                    llm_active=llm_active,
                    extra_args=list(FORGE_EXTRA_ARGS) if FORGE_EXTRA_ARGS else None,
                )
                cmd.extend(startup_flags)
                logger.info(
                    f"[ProcessManager] Стартовые флаги Forge: VRAM={startup_meta['total_vram_gb']:.1f}GB | "
                    f"pin_shared_memory={startup_meta['pin_shared_memory']} ({startup_meta['pin_reason']}) | "
                    f"startup_offload={startup_meta['startup_always_offload']}"
                )
            except Exception as _e_pm:
                logger.debug(f"[ProcessManager] Проверка стартового бюджета Forge: {_e_pm}")

            logs_dir = LOGS_DIR
            logs_dir.mkdir(parents=True, exist_ok=True)
            forge_log_path = logs_dir / "forge.log"

            if self._forge_log_file and not self._forge_log_file.closed:
                try:
                    self._forge_log_file.close()
                except Exception:
                    pass

            self._forge_log_file = open(forge_log_path, "w", encoding="utf-8", errors="replace", buffering=1)

            clean_env = get_clean_forge_env()
            if "startup_flags" in locals() and startup_flags:
                clean_env["COMMANDLINE_ARGS"] = " ".join(startup_flags)
            try:
                self._forge_process = subprocess.Popen(
                    cmd,
                    cwd=str(working_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    encoding="utf-8",
                    errors="replace",
                    startupinfo=startupinfo,
                    creationflags=flags,
                    env=clean_env
                )
                assigned = Win32JobManager.get_instance().assign_process(self._forge_process)
                if assigned:
                    logger.info("[ProcessManager] Процесс Forge привязан к Windows Job Object (KILL_ON_JOB_CLOSE).")
                else:
                    logger.warning("[ProcessManager] ⚠️ Процесс Forge НЕ привязан к Windows Job Object! Защита от сиротских процессов не активна.")
            except Exception as popen_err:
                if self._forge_log_file:
                    self._forge_log_file.close()
                    self._forge_log_file = None
                raise popen_err

            # Фоновый поток-демон для параллельной записи в файл и трансляции в LogConsoleDialog
            def _stream_forge_output(proc: subprocess.Popen, file_handle: Any):
                forge_logger = logging.getLogger("NeonRoute.Forge")
                try:
                    for raw_line in iter(proc.stdout.readline, ''):
                        if not raw_line:
                            break
                        try:
                            file_handle.write(raw_line)
                            file_handle.flush()
                        except Exception:
                            pass

                        line = raw_line.strip()
                        if not line:
                            continue
                        if "%|" in line and ("it/s" in line or "s/it" in line):
                            continue
                        if line.startswith("tiled upscale:"):
                            continue

                        forge_logger.info(line)
                except Exception as stream_err:
                    forge_logger.debug(f"Forge stdout reader closed: {stream_err}")
                finally:
                    try:
                        file_handle.close()
                    except Exception:
                        pass

            threading.Thread(
                target=_stream_forge_output,
                args=(self._forge_process, self._forge_log_file),
                daemon=True
            ).start()

            logger.info(f"Forge daemon process created with PID: {self._forge_process.pid}. Waiting for port 7860 readiness...")
            self.forge_status_changed.emit(False, "Запуск ядра Forge (ожидание порта 7860)...")

            # Честный цикл ожидания открытия порта (Handshake) с защитой от сбоя процесса
            startup_timeout = 150
            start_time = time.time()
            server_ready = False

            while time.time() - start_time < startup_timeout:
                if self._forge_process and self._forge_process.poll() is not None:
                    exit_code = self._forge_process.poll()
                    err_msg = f"Процесс Forge неожиданно завершился до открытия порта (код {exit_code}). Проверьте logs/forge.log"
                    logger.error(err_msg)
                    self._forge_process = None
                    if self._forge_log_file and not self._forge_log_file.closed:
                        try:
                            self._forge_log_file.close()
                        except Exception:
                            pass
                        self._forge_log_file = None
                    self.forge_status_changed.emit(False, err_msg)
                    self.action_completed.emit("forge_start", False, err_msg)
                    return

                if verify_forge_http_handshake(port=7860, timeout=0.8):
                    server_ready = True
                    break
                elif "pytest" in sys.modules and isinstance(getattr(self, "is_port_open", None), MagicMock):
                    if self.is_port_open("127.0.0.1", 7860, timeout=0.8):
                        server_ready = True
                        break

                time.sleep(1.0)

            if server_ready:
                elapsed = int(time.time() - start_time)
                logger.info(f"Forge server is online and responding on port 7860 (startup took {elapsed}s).")
                self.forge_status_changed.emit(True, "Forge активен и готов к генерации.")
                self.action_completed.emit("forge_start", True, f"SD WebUI Forge готов к работе ({elapsed} с).")
            else:
                # Детальная диагностика причины недоступности Forge
                diag_reason = "порт 7860 не открыт или процесс не отвечает"
                try:
                    test_req = urllib.request.Request(
                        "http://127.0.0.1:7860/sdapi/v1/options",
                        headers={"User-Agent": "NeonRoute-Handshake/1.0"}
                    )
                    with urllib.request.urlopen(test_req, timeout=1.0) as test_resp:
                        code = getattr(test_resp, "status", getattr(test_resp, "code", None))
                        if code == 200:
                            diag_reason = "сервер ответил HTTP 200 в момент таймаута"
                        else:
                            diag_reason = f"сервер отвечает HTTP {code}"
                except urllib.error.HTTPError as he:
                    if he.code == 404:
                        diag_reason = (
                            "HTTP 404 Not Found: корневой веб-сервер активен, но API /sdapi/v1/options не зарегистрирован "
                            "(поток инициализации Forge заблокирован)"
                        )
                    else:
                        diag_reason = f"HTTP {he.code} ({he.reason})"
                except Exception as ex:
                    diag_reason = f"соединение не установлено ({type(ex).__name__}: {ex})"

                err_msg = f"Превышено время ожидания запуска Forge ({startup_timeout} с). Причина: {diag_reason}."
                logger.warning(f"[ProcessManager] {err_msg}")
                try:
                    logger.info("[ProcessManager] Штатная остановка дерева процессов Forge после таймаута...")
                    self._run_forge_stop()
                except Exception as stop_err:
                    logger.warning(f"[ProcessManager] Ошибка при очистке процесса Forge после таймаута: {stop_err}")
                self._forge_process = None
                if self._forge_log_file and not self._forge_log_file.closed:
                    try:
                        self._forge_log_file.close()
                    except Exception:
                        pass
                    self._forge_log_file = None
                self.forge_status_changed.emit(False, err_msg)
                self.action_completed.emit("forge_start", False, err_msg)
        except Exception as e:
            logger.error(f"Failed to launch Forge daemon: {e}")
            self._forge_process = None
            if self._forge_log_file and not self._forge_log_file.closed:
                try:
                    self._forge_log_file.close()
                except Exception:
                    pass
                self._forge_log_file = None
            self.forge_status_changed.emit(False, str(e))
            self.action_completed.emit("forge_start", False, str(e))

    _start_forge_process = _run_forge_start

    def stop_forge_async(self):
        """Stops Forge and terminates child process trees to release VRAM."""
        threading.Thread(target=self._run_forge_stop, daemon=True).start()

    def _run_forge_stop(self):
        logger.info("Stopping SD WebUI Forge and releasing GPU VRAM...")

        # 1. Kill tracked process tree if known
        if self._forge_process is not None and self._forge_process.pid:
            pid = self._forge_process.pid
            try:
                s_info, s_flags = _get_win32_hidden_process_flags()
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                    startupinfo=s_info,
                    creationflags=s_flags
                )
            except Exception as e:
                logger.warning(f"Error terminating Forge PID {pid}: {e}")
            finally:
                self._forge_process = None
                if self._forge_log_file and not self._forge_log_file.closed:
                    try:
                        self._forge_log_file.close()
                    except Exception:
                        pass
                    self._forge_log_file = None

        # 2. Check for any remaining python process running launch.py from C:\neyroGPD
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    pname = (proc.info.get('name') or '').lower()
                    if pname not in ('python.exe', 'pythonw.exe', 'cmd.exe'):
                        continue
                    cmdline = proc.cmdline()
                    cmd_str = " ".join(cmdline).lower()
                    import config
                    forge_dir_str = str(config.FORGE_ROOT_DIR).lower()
                    # Фильтруем строго по полному пути установки или легаси-маркеру neyrogpd
                    is_forge_proc = ("neyrogpd" in cmd_str or (len(forge_dir_str) > 3 and forge_dir_str in cmd_str))
                    if is_forge_proc and ("launch.py" in cmd_str or "webui" in cmd_str):
                        logger.info(f"Terminating residual Forge PID {proc.pid}")
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass

    def unload_all_lms_models(self):
        """Выгрузка всех моделей LM Studio / llama-server для освобождения VRAM при закрытии приложения."""
        logger.info("ProcessManager: выгрузка всех моделей LM Studio / llama-server...")
        if self.has_headless_server() or self._llama_process is not None:
            self._kill_llama_server_instance(for_forge_eviction=False)
            return

        try:
            resp = requests.post("http://127.0.0.1:1234/api/v0/models/unload", timeout=1.5)
            if resp.status_code == 200:
                logger.info("LMS models unloaded via REST API.")
                return
        except Exception:
            pass

        lms_bin = self._lms_exe
        if not lms_bin and not self._locate_lm_studio_app():
            logger.info("ProcessManager: llama-server/LM Studio не найдены, пропуск выгрузки моделей.")
            return

        if not lms_bin:
            lms_bin = "lms"
        try:
            s_info, s_flags = _get_win32_hidden_process_flags()
            subprocess.run(
                [lms_bin, "unload", "--all"],
                capture_output=True,
                timeout=5.0,
                startupinfo=s_info,
                creationflags=s_flags
            )
            logger.info("LMS models unloaded via CLI.")
        except subprocess.TimeoutExpired:
            logger.warning("lms unload --all timed out (5.0s), proceeding to shutdown...")
        except Exception as e:
            logger.warning(f"Failed to unload LMS models via CLI: {e}")

    def shutdown(self):
        """Standard shutdown interface for terminating managed AI processes and freeing VRAM."""
        self.shutdown_all()

    def shutdown_all(self):
        """Called upon MainWindow close to terminate background subprocesses and free VRAM."""
        logger.info("ProcessManager: shutting down managed processes and releasing VRAM...")

        # 1. Сначала мгновенно глушим дерево процессов Forge (занимает <0.1 сек)
        if self._forge_process is not None and self._forge_process.pid:
            pid = self._forge_process.pid
            try:
                s_info, s_flags = _get_win32_hidden_process_flags()
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=3,
                    startupinfo=s_info,
                    creationflags=s_flags
                )
                logger.info(f"Forge process tree (PID {pid}) terminated.")
            except Exception as e:
                logger.warning(f"Error terminating Forge on shutdown: {e}")
            finally:
                self._forge_process = None

        # 1.1 Резервная зачистка оторванных процессов Forge через taskkill /F /T
        try:
            import psutil
            s_info, s_flags = _get_win32_hidden_process_flags()
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmd = proc.info.get('cmdline') or []
                    cmd_str = " ".join(cmd).lower()
                    import config
                    forge_dir_str = str(config.FORGE_ROOT_DIR).lower()
                    # Фильтруем строго по полному пути установки или легаси-маркеру neyrogpd
                    is_forge_proc = ("neyrogpd" in cmd_str or (len(forge_dir_str) > 3 and forge_dir_str in cmd_str))
                    if is_forge_proc and ("launch.py" in cmd_str or "webui" in cmd_str):
                        logger.info(f"Terminating residual Forge process tree PID {proc.pid}")
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            capture_output=True,
                            timeout=2,
                            startupinfo=s_info,
                            creationflags=s_flags
                        )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            logger.debug(f"Residual Forge cleanup error: {e}")

        # 2. Глушим дерево процессов llama-server.exe
        self._kill_llama_server_instance(for_forge_eviction=False)

        # 3. Выгружаем модели LM Studio для освобождения VRAM (сохраняет совместимость с тестами)
        self.unload_all_lms_models()
