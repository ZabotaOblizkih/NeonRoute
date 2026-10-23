"""
Process Manager Service for NeonRoute
Controls lifecycle of external local AI backends (LM Studio and SD WebUI Forge).
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
from pathlib import Path
from typing import Optional, Tuple, List, Any

import psutil
import requests
from PyQt6.QtCore import QObject, pyqtSignal

from config import FORGE_LAUNCHER_PATH, LM_STUDIO_CLI_CMD

logger = logging.getLogger("NeonRoute.ProcessManager")


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


class ProcessManager(QObject):
    """
    Orchestrates execution of external local AI servers (LM Studio, SD WebUI Forge).
    Ensures non-blocking process invocation and clean VRAM deallocation on exit.
    """
    lms_status_changed = pyqtSignal(bool, str)     # is_running, message
    forge_status_changed = pyqtSignal(bool, str)   # is_running, message
    action_completed = pyqtSignal(str, bool, str)  # action_name, success, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._forge_process: Optional[subprocess.Popen] = None
        self._forge_log_file: Optional[Any] = None
        self._lms_exe: Optional[str] = self._locate_lms()
        self._lms_safe_profile_applied: bool = False
        self._is_loading_lms: bool = False
        self.lm_client: Optional[Any] = None

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
    # LM Studio Management
    # =========================================================================

    def is_port_open(self, host: str = "127.0.0.1", port: int = 1234, timeout: float = 1.0) -> bool:
        """Quick check if a network port is accepting connections."""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def is_lm_studio_running(self) -> bool:
        """Checks if LM Studio API server is responding on port 1234."""
        return self.is_port_open("127.0.0.1", 1234, timeout=0.8)

    def start_lm_studio_async(self):
        """Launches 'lms server start' in a separate thread to avoid UI freeze."""
        threading.Thread(target=self._run_lms_start, daemon=True).start()

    def _run_lms_start(self):
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
            # lms server start launches background daemon and exits
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
        """Stops LM Studio server via 'lms server stop' in background thread."""
        threading.Thread(target=self._run_lms_stop, daemon=True).start()

    def _run_lms_stop(self):
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
        Discovers all available LM Studio models via:
        1. 'lms ls --json' CLI
        2. Text table parsing of 'lms ls' CLI (fallback)
        3. Scanning ~/.lmstudio/models and ~/.cache/lm-studio/models directories for .gguf files
        Returns a deduplicated list of model identifiers.
        """
        lms_bin = self._lms_exe or "lms"
        models: List[str] = []
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
                        # Skip vision projection adapters
                        if gguf.name.lower().startswith("mmproj"):
                            continue
                        rel = gguf.relative_to(root)
                        # Example: bartowski/L3-8B-Stheno-v3.2-GGUF/L3-8B-Stheno-v3.2-Q4_K_S.gguf
                        key = rel.parent.name if rel.parent.name else gguf.stem
                        if key and key not in models:
                            models.append(key)
                except Exception as de:
                    logger.debug(f"Error scanning {root}: {de}")

        return models

    def get_loaded_lms_models(self) -> List[str]:
        """
        Queries LM Studio CLI (lms ps --json) for models currently loaded in memory.
        Returns list of model keys, identifiers, and paths.
        """
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
        Checks if the target model (or key/path substring) is already loaded and ready in LM Studio memory.
        Uses active instances via self.get_loaded_lms_models() (lms ps) to avoid false positives from GET /v1/models catalog.
        """
        if not model_key:
            return False

        loaded = self.get_loaded_lms_models()
        if not loaded:
            return False

        target = model_key.strip().lower()
        # Clean target from common suffixes/paths for flexible matching
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
        """Loads a model into memory via lms CLI in background, with graceful CPU fallback on OOM."""
        model_name = model_key
        if getattr(self, "_is_loading_lms", False):
            logger.info("[ProcessManager] Загрузка модели уже выполняется, дублирующий вызов пропущен.")
            return

        # Если модель уже в памяти LM Studio — не дергаем повторную загрузку
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

            # Единая точка входа: если подключен lm_client, используем его безопасный загрузчик
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
            # forge_launcher.bat уже содержит полный набор аргументов в COMMANDLINE_ARGS.
            # Передача аргументов через Python отключена для предотвращения задвоения флагов запуска.

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
                        # Запись в файл на диске
                        try:
                            file_handle.write(raw_line)
                            file_handle.flush()
                        except Exception:
                            pass

                        line = raw_line.strip()
                        if not line:
                            continue
                        # Фильтрация спама частых прогресс-баров tqdm
                        if "%|" in line and ("it/s" in line or "s/it" in line):
                            continue
                        if line.startswith("tiled upscale:"):
                            continue

                        # Трансляция значимых событий в консоль логов NeonRoute
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
            startup_timeout = 150  # Запас времени для холодного старта Torch/CUDA
            start_time = time.time()
            server_ready = False

            while time.time() - start_time < startup_timeout:
                # Проверка: не упал ли процесс в процессе инициализации
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

                # Проверка доступности сетевого интерфейса 7860
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
        stopped = False

        # 1. Kill tracked process tree if known
        if self._forge_process is not None and self._forge_process.pid:
            pid = self._forge_process.pid
            try:
                # Use taskkill /F /T /PID on Windows for clean recursive termination
                s_info, s_flags = _get_win32_hidden_process_flags()
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=5,
                    startupinfo=s_info,
                    creationflags=s_flags
                )
                stopped = True
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
        """Выгрузка всех моделей LM Studio для освобождения VRAM при закрытии приложения."""
        logger.info("ProcessManager: выгрузка всех моделей LM Studio...")
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

        # 2. Выгружаем модели LM Studio для освобождения VRAM
        self.unload_all_lms_models()
