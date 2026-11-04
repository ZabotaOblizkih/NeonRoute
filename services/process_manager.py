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
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple, List, Any

import psutil
import requests
from PyQt6.QtCore import QObject, pyqtSignal

from config import FORGE_LAUNCHER_PATH, LM_STUDIO_CLI_CMD
from services.server_config import ServerConfigResolver

logger = logging.getLogger("NeonRoute.ProcessManager")


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
        self.last_error: Optional[str] = None
        self._current_running_ngl: Optional[int] = None

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

    def _set_server_state(self, new_state: ServerState, message: str = ""):
        self._server_state = new_state
        if new_state == ServerState.ERROR and message:
            self.last_error = message
        elif new_state == ServerState.READY:
            self.last_error = None
        logger.info(f"[ProcessManager.ServerState] {new_state.value}: {message}")
        try:
            self.server_state_changed.emit(new_state.value, message)
        except Exception:
            pass

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
                self._llama_process = None
                self._current_running_ngl = None

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

            # 4. Set state
            next_state = ServerState.EVICTED_FOR_FORGE if for_forge_eviction else ServerState.STOPPED
            msg = "LLM выгружен для Forge" if for_forge_eviction else "Сервер LLM остановлен"
            self._set_server_state(next_state, msg)
            if not for_forge_eviction:
                self.lms_status_changed.emit(False, msg)

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
                err_msg = f"Процесс llama-server завершился с кодом {exit_code}. См. logs/llama_server.log"
                logger.error(f"[ProcessManager] {err_msg}")
                self._process = None
                self._current_running_ngl = None
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
        is_restore: bool = False
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
            # 1. Grab current running server's ngl for hysteresis before killing
            current_running_ngl = None
            if self._llama_process is not None and self._llama_process.poll() is None:
                val = getattr(self, "_current_running_ngl", None)
                if isinstance(val, int):
                    current_running_ngl = val
            if current_running_ngl is None and hasattr(self, "lm_client") and self.lm_client:
                val = getattr(self.lm_client, "_current_running_ngl", None)
                if isinstance(val, int):
                    current_running_ngl = val

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

                kv_cache_type = app_cfg.get("kv_cache_type") or ini_cfg.get("cache_type_k") or "q8_0"
                fa_enabled = app_cfg.get("flash_attention_enabled")
                if fa_enabled is None:
                    fa_enabled = (ini_cfg.get("flash_attn", "on") == "on")
                fa_str = "on" if fa_enabled else "off"

                # Динамический расчет слоев через LayerBudgeter
                audit_data: Dict[str, Any] = {}
                is_manual_override = (
                    gpu_layers is not None
                    and str(gpu_layers).strip().lower() != "auto"
                    and gpu_layers != 99
                )

                if is_manual_override:
                    try:
                        effective_ngl = int(gpu_layers)
                    except (ValueError, TypeError):
                        effective_ngl = 99
                    logger.warning(
                        f"[ProcessManager] Ручной оверрайд gpu_layers={effective_ngl}. Авторасчет LayerBudgeter пропущен."
                    )
                else:
                    from services.layer_budgeter import LayerBudgeter
                    ngl, can_run, reason, audit_data = LayerBudgeter.calculate_layer_offload(
                        model_path=model_path,
                        target_context=effective_ctx,
                        current_running_ngl=current_running_ngl,
                        cache_type=kv_cache_type
                    )

                    if not can_run:
                        logger.error(f"[ProcessManager] Запуск отменен: {reason}")
                        self._set_server_state(ServerState.ERROR, reason)
                        self.last_error = reason
                        self.lms_status_changed.emit(False, reason)
                        return False

                    effective_ngl = ngl

                # 6. Assemble CLI arguments (strict list, no shell=True)
                cmd = [
                    str(server_exe),
                    "-m", str(model_path),
                    "-c", str(effective_ctx),
                    "-ngl", str(effective_ngl),
                    "-fa", str(fa_str),
                    "-ctk", str(kv_cache_type),
                    "-ctv", str(kv_cache_type),
                    "-np", str(ini_cfg.get("parallel_slots", 1)),
                    "--port", str(port),
                    "--host", str(host),
                ]

                is_hybrid = effective_ngl < audit_data.get("total_layers", 99)
                if (ini_cfg.get("no_mmap", True) or is_hybrid) and "--no-mmap" not in cmd:
                    cmd.append("--no-mmap")

                mmproj = ServerConfigResolver.resolve_mmproj_for_model(model_path)
                if mmproj:
                    cmd.extend(["--mmproj", str(mmproj)])

                # 7. Environment & CUDA PATH
                env = os.environ.copy()
                cuda_dir = ServerConfigResolver.resolve_cuda_dir(server_exe)
                if cuda_dir:
                    env["PATH"] = f"{str(cuda_dir)};{env.get('PATH', '')}"

                # 8. Setup log file
                logs_dir = Path("logs")
                logs_dir.mkdir(parents=True, exist_ok=True)
                log_file_path = logs_dir / "llama_server.log"

                if self._llama_log_file and not getattr(self._llama_log_file, "closed", True):
                    try:
                        self._llama_log_file.close()
                    except Exception:
                        pass

                self._llama_log_file = open(log_file_path, "a", encoding="utf-8", errors="replace", buffering=1)

                # 9. Popen with hidden flags
                startupinfo, flags = _get_win32_hidden_process_flags(create_new_group=True)
                try:
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
                    self.lms_status_changed.emit(True, "Сервер LLM запущен.")
                    self._current_running_ngl = effective_ngl
                    self.last_error = None
                    if hasattr(self, "lm_client") and self.lm_client:
                        self.lm_client._is_model_unloaded = False
                        self.lm_client._last_loaded_model_id = model_path.stem
                        self.lm_client._current_loaded_ctx = effective_ctx
                        self.lm_client._current_running_ngl = effective_ngl
                    return True
                else:
                    if self._server_state != ServerState.ERROR:
                        err_msg = f"Превышено время ожидания запуска llama-server ({timeout_s} с)."
                        logger.warning(err_msg)
                        self._kill_llama_server_instance()
                        self._set_server_state(ServerState.ERROR, err_msg)
                        self.lms_status_changed.emit(False, err_msg)
                    return False
            except Exception as e:
                logger.error(f"Error starting llama-server: {e}")
                self._kill_llama_server_instance()
                self._set_server_state(ServerState.ERROR, str(e))
                self.lms_status_changed.emit(False, str(e))
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
                self.action_completed.emit("lms_start", True, "LLM сервер успешно запущен.")
            else:
                self.action_completed.emit("lms_start", False, "Не удалось запустить LLM сервер.")
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
            lms_bin = "lms"

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

        lms_bin = self._lms_exe or "lms"
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
            lms_bin = self._lms_exe or "lms"
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
        logger.info("ProcessManager: evicting LLM server for Forge diffusion generation...")
        with self._process_lock:
            self._kill_llama_server_instance(for_forge_eviction=True)
            if hasattr(self, "lm_client") and self.lm_client:
                self.lm_client._is_model_unloaded = True
            return True

    # =========================================================================
    # SD WebUI Forge Management
    # =========================================================================

    def is_forge_running(self) -> bool:
        """Checks if Forge API server is actively responding on port 7860."""
        return self.is_port_open("127.0.0.1", 7860, timeout=0.8)

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

        # Запуск быстрого pre-flight аудита ассетов перед стартом демона
        try:
            from services.asset_validator import AssetValidator
            AssetValidator.log_validation_summary()
        except Exception as ve:
            logger.debug(f"[ProcessManager] Ошибка аудита ассетов: {ve}")

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
                from services.art_director.vram_policy import resolve_forge_offload_policy
                pol = resolve_forge_offload_policy(architecture="sd15", is_fp8=True, enable_adetailer=True)
                if pol.get("always_offload", False):
                    # Если уже на старте система видит, что памяти не хватает даже для базового цикла без выгрузки
                    cmd.extend([
                        "--api", "--port", "7860", "--listen", "--cuda-malloc",
                        "--always-offload-from-vram", "--opt-sdp-attention",
                        "--ad-no-huggingface", "--no-download-sd-model",
                        "--skip-torch-cuda-test", "--no-hashing", "--disable-nan-check"
                    ])
                    logger.info("[ProcessManager] Инициализация памяти: недостаточно VRAM для полноценного удержания -> запуск Forge в режиме STAGE_OFFLOAD (--always-offload-from-vram).")
            except Exception as _e_pm:
                logger.debug(f"[ProcessManager] Проверка стартового бюджета Forge: {_e_pm}")

            logs_dir = Path("logs")
            logs_dir.mkdir(parents=True, exist_ok=True)
            forge_log_path = logs_dir / "forge.log"

            if self._forge_log_file and not self._forge_log_file.closed:
                try:
                    self._forge_log_file.close()
                except Exception:
                    pass

            self._forge_log_file = open(forge_log_path, "w", encoding="utf-8", errors="replace", buffering=1)

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
                    creationflags=flags
                )
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
                err_msg = f"Превышено время ожидания запуска Forge ({startup_timeout} с). Порт 7860 не отвечает."
                logger.warning(err_msg)
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
                    if "neyrogpd" in cmd_str and ("launch.py" in cmd_str or "webui" in cmd_str):
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

        lms_bin = self._lms_exe or "lms"
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
                    if "neyrogpd" in cmd_str and ("launch.py" in cmd_str or "webui" in cmd_str):
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
