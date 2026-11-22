"""
LM Studio Client for NeonRoute
Communicates with local OpenAI-compatible endpoint (http://127.0.0.1:1234/v1).
Auto-detects active model ID from GET /v1/models, constructs valid JSON payload,
emits streaming and final signals, supports cancellation, Keep-Alive connection pooling,
and Structured Outputs (response_format).
"""

import re
import json
import queue
import logging
import threading
import time
import requests
from requests.adapters import HTTPAdapter
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any, Iterator

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger("NeonRoute.LMStudio")

_LMS_SOCKET_LOCK = threading.RLock()


def _is_default_model_placeholder(model_name: Optional[str]) -> bool:
    if not model_name:
        return True
    s = str(model_name).strip().lower()
    if s.endswith((".gguf", ".bin", ".safetensors")) or "/" in s or "\\" in s:
        return False
    return s.startswith("default") or s == "auto"


class ModelNotFoundError(RuntimeError):
    """Выбрасывается, когда запрашиваемая модель отсутствует в локальном хранилище LM Studio."""
    def __init__(self, model_name: str):
        self.model_name = model_name
        super().__init__(f"Модель '{model_name}' не найдена в хранилище LM Studio.")


class LlamaServerStartupError(RuntimeError):
    """Выбрасывается при аппаратном сбое, OOM или ошибке инициализации процесса llama-server."""
    pass



def _get_vram_telemetry_str() -> str:
    """Возвращает строку с текущим состоянием VRAM через NVML или пустую строку при ошибке."""
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        free_mb = int(mem.free / 1024 / 1024)
        used_mb = int(mem.used / 1024 / 1024)
        return f"[VRAM: Free={free_mb}MB, Used={used_mb}MB]"
    except Exception:
        return "[VRAM: N/A]"


def _is_rtx_gpu_available() -> bool:
    """Проверяет наличие видеокарты NVIDIA RTX (Compute Capability >= 7.5)."""
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(h)
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="ignore")
        return "rtx" in str(name).lower()
    except Exception:
        return False


class LMStudioClient(QObject):
    """
    Client for LM Studio local REST API.
    Implemented as a thread-safe Singleton to enforce a Single Source of Truth
    for model lifecycle and VRAM allocation across all threads.
    """
    response_received = pyqtSignal(str)
    token_streamed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    _instance = None
    _singleton_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, base_url: str = "http://127.0.0.1:1234/v1", parent=None):
        if getattr(self, "_initialized", False):
            return
        super().__init__(parent)
        self.base_url = base_url.rstrip("/")
        self._init_session()
        self._current_session: Optional[requests.Session] = None
        self._active_response: Optional[requests.Response] = None
        self._is_aborted = False
        self._is_model_unloaded = False
        self._current_ctx: int = 0
        self._current_loaded_ctx: int = 8192
        self._current_loaded_gpu: str = "1.0"
        self._last_loaded_model_id: Optional[str] = None
        self.process_manager: Optional[Any] = None
        self._reload_lock = threading.Lock()
        self._initialized = True

    def set_process_manager(self, pm: Any) -> None:
        """Injects ProcessManager dependency for headless server coordination."""
        self.process_manager = pm

    def _init_session(self):
        self._persistent_session = requests.Session()
        adapter = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=1)
        self._persistent_session.mount("http://", adapter)
        self._persistent_session.mount("https://", adapter)

    def is_model_loaded(self, model_name: Optional[str] = None) -> bool:
        """Проверяет готовность целевой модели в VRAM по состоянию жизненного цикла клиента."""
        if getattr(self, "_is_model_unloaded", False):
            return False

        if self.process_manager is not None and not self.process_manager.is_lm_studio_running():
            self._last_loaded_model_id = None
            return False

        target = model_name or getattr(self, "_last_loaded_model_id", None)
        last_id = getattr(self, "_last_loaded_model_id", None)

        # Если локальный last_id пуст (новый инстанс), опрашиваем живой сервер /models
        if not last_id:
            try:
                active = self.get_models()
                if active:
                    last_id = active[0]
                    self._last_loaded_model_id = last_id
                    if not getattr(self, "_current_loaded_ctx", 0):
                        self._current_loaded_ctx = 14336
            except Exception:
                pass

        if not target or not last_id:
            return False

        t_clean = str(target).lower().strip()
        l_clean = str(last_id).lower().strip()
        if t_clean == l_clean:
            return True

        if Path(t_clean).stem.lower() == Path(l_clean).stem.lower():
            return True

        def _clean_stem(s: str) -> str:
            name = s.replace(chr(92), "/").rsplit("/", 1)[-1]
            for ext in (".gguf", "-gguf", "_gguf"):
                if name.endswith(ext):
                    name = name[:-len(ext)]
            return name

        c1 = _clean_stem(t_clean)
        c2 = _clean_stem(l_clean)
        if c1 == c2 or c1 in c2 or c2 in c1:
            return True

        return False

    def check_health(self) -> Tuple[bool, str]:
        """Быстрая неблокирующая проверка доступности сервера (Keep-Alive, таймаут 1.2 сек)."""
        try:
            resp = self._persistent_session.get(f"{self.base_url}/models", timeout=1.2)
            if resp.status_code == 200:
                return True, "LM Studio Online"
            return False, f"HTTP {resp.status_code}"
        except requests.exceptions.ConnectionError:
            return False, "LM Studio Offline (Connection Refused)"
        except Exception as e:
            return False, f"Offline ({str(e)})"

    def get_models(self) -> List[str]:
        """Возвращает список активных моделей через пул соединений."""
        try:
            resp = self._persistent_session.get(f"{self.base_url}/models", timeout=2.0)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                return [m.get("id") for m in data if m.get("id")]
            return []
        except Exception as e:
            logger.warning(f"Failed to fetch models from LM Studio: {e}")
            return []

    def get_active_model_id(self, preferred_model: Optional[str] = None) -> str:
        """Определяет ID активной модели из GET /v1/models."""
        endpoint = f"{self.base_url}/models"
        try:
            resp = self._persistent_session.get(endpoint, timeout=2.5)
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Не удалось подключиться к LM Studio на {self.base_url}. Убедитесь, что сервер включен в LM Studio."
            )
        except Exception as e:
            raise RuntimeError(f"Ошибка запроса списка моделей к {endpoint}: {e}")

        if resp.status_code != 200:
            raise RuntimeError(f"LM Studio /models вернул HTTP {resp.status_code}: {resp.text}")

        data = resp.json().get("data", [])
        if not data:
            raise RuntimeError(
                "В LM Studio не загружена ни одна модель! Загрузите модель в Local Server LM Studio."
            )

        available_ids = [m.get("id") for m in data if m.get("id")]
        if not available_ids:
            raise RuntimeError("LM Studio вернул пустой список идентификаторов моделей.")

        if preferred_model and not _is_default_model_placeholder(preferred_model):
            return preferred_model

        chosen_id = available_ids[0]
        logger.info(f"Auto-selected active LM Studio model: '{chosen_id}'")
        return chosen_id

    def abort(self):
        """Немедленно прерывает активный HTTP-запрос."""
        self._is_aborted = True
        active_resp = getattr(self, "_active_response", None)
        if active_resp:
            try:
                active_resp.close()
            except Exception:
                pass

    def unload_models_cli(self) -> bool:
        """Полная выгрузка моделей из VRAM с обновлением флага состояния."""
        logger.info("Unloading LM Studio / LLM models to free VRAM...")

        unload_ok = False
        if self.process_manager and hasattr(self.process_manager, "has_headless_server") and self.process_manager.has_headless_server():
            try:
                evict_res = self.process_manager.evict_llm_for_forge_sync()
                unload_ok = (evict_res is not False)
            except Exception as e:
                logger.warning(f"Failed to evict LLM via process_manager: {e}")
        else:
            try:
                clean_root = self.base_url.strip().rstrip("/")
                if clean_root.endswith("/v1"):
                    clean_root = clean_root[:-3].rstrip("/")
                for ep in (f"{clean_root}/api/v0/model/unload", f"{clean_root}/api/v0/models/unload"):
                    try:
                        r = requests.post(ep, json={}, timeout=0.8)
                        if r.status_code in (200, 204):
                            unload_ok = True
                            break
                    except Exception:
                        continue
            except Exception:
                pass
            try:
                import subprocess
                import sys
                startupinfo = None
                creationflags = 0
                if sys.platform == "win32":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 0
                    creationflags = subprocess.CREATE_NO_WINDOW

                res_unl = subprocess.run(
                    ["lms", "unload", "--all"],
                    capture_output=True,
                    text=True,
                    timeout=15.0,
                    startupinfo=startupinfo,
                    creationflags=creationflags
                )
                if res_unl.returncode == 0:
                    unload_ok = True
                logger.info(f"CLI unload completed (code {res_unl.returncode}): {res_unl.stdout.strip() or res_unl.stderr.strip()} {_get_vram_telemetry_str()}")
            except Exception as e:
                logger.warning(f"Failed to unload LMS models via CLI: {e}")

        if not unload_ok:
            self._is_model_unloaded = False
            return False

        self._is_model_unloaded = True
        self._current_ctx = 0
        self._current_loaded_ctx = 0
        self._current_loaded_gpu = ""
        # СТРОГО СОХРАНЯЕМ self._last_loaded_model_id для автоматического возврата LLM после генерации в Forge
        return True

    def safe_unload_models(self) -> bool:
        """Безопасная выгрузка моделей (REST + CLI fallback)."""
        return self.unload_models_cli()

    def unload_models(self) -> bool:
        """Безопасная выгрузка моделей (REST + CLI fallback)."""
        return self.unload_models_cli()

    def unload_model(self) -> bool:
        """Выгрузка текущей модели из VRAM (обратная совместимость)."""
        return self.unload_models_cli()

    def ensure_model_loaded(self, preferred_model: Optional[str] = None, context_length: Optional[int] = None, gpu_ratio: Optional[str] = None, **kwargs) -> bool:
        """
        Гарантирует загрузку строго одной модели в VRAM с заданными параметрами контекста и GPU ratio.
        """
        with self._reload_lock:
            target_model = preferred_model or getattr(self, "_last_loaded_model_id", None)
            if _is_default_model_placeholder(target_model):
                if self.process_manager and hasattr(self.process_manager, "has_headless_server") and self.process_manager.has_headless_server():
                    try:
                        from utils.config_manager import ConfigManager
                        app_cfg = ConfigManager.load_config()
                        target_model = app_cfg.get("active_model")
                    except Exception:
                        pass
                if _is_default_model_placeholder(target_model):
                    catalog = self.get_models()
                    target_model = catalog[0] if catalog else None

            if not target_model:
                logger.warning("[LMStudio] Нет доступных моделей для загрузки.")
                raise ModelNotFoundError("активная модель")

            model_name = target_model

            from utils.config_manager import ConfigManager
            from config import HARDWARE_MAX_CONTEXT_LENGTH, MASTER_MAX_RESPONSE_TOKENS, MODE_PROFILES
            app_cfg = ConfigManager.load_config()

            m_key = "engineer" if kwargs.get("is_engineer") else ("roleplay" if kwargs.get("is_roleplay") else "assistant")
            def_ctx = MODE_PROFILES.get(m_key, {}).get("context_length", 14336)

            if context_length:
                target_ctx = int(context_length)
            else:
                target_ctx = int(app_cfg.get(f"context_length_{m_key}", def_ctx))
            target_ctx = min(target_ctx, HARDWARE_MAX_CONTEXT_LENGTH)
            target_gpu = str(gpu_ratio) if gpu_ratio is not None else "1.0"

            current_ctx = getattr(self, "_current_loaded_ctx", 0)
            current_gpu = getattr(self, "_current_loaded_gpu", "1.0")

            # Защита от частых перезагрузок (Reload Loop):
            # Если модель уже выгружена (_is_model_unloaded=True), не тратим 2.4с на ConnectTimeout к закрытому порту 1234
            is_healthy = False if getattr(self, "_is_model_unloaded", False) else self.check_health()[0]
            if is_healthy and self.is_model_loaded(model_name):
                # Проверяем достаточность загруженного контекста как для headless, так и для standalone LM Studio
                raw_loaded_ctx = getattr(self, "_current_loaded_ctx", 0)
                if raw_loaded_ctx >= target_ctx or (raw_loaded_ctx == 0 and not context_length):
                    current_ctx = raw_loaded_ctx or target_ctx
                    self._is_model_unloaded = False
                    self._last_loaded_model_id = model_name
                    self._current_loaded_ctx = current_ctx
                    self._current_loaded_gpu = target_gpu
                    return True

            # Headless server путь через ProcessManager
            if self.process_manager and hasattr(self.process_manager, "has_headless_server") and self.process_manager.has_headless_server():
                logger.info(f"[LMStudio] Шлюз: запуск/перезапуск headless сервера для '{model_name}' (CTX={target_ctx}, GPU={target_gpu})...")

                # Проверяем реальное наличие файла модели на диске
                from services.server_config import ServerConfigResolver
                m_path = ServerConfigResolver.resolve_model_path(model_name)
                if not m_path or not m_path.is_file():
                    logger.warning(f"[LMStudio] GGUF файл для модели '{model_name}' не найден на диске.")
                    raise ModelNotFoundError(str(model_name))

                ngl = 0 if target_gpu in ("0", "0.0", "cpu", "CPU Only") else None
                if ngl is None:
                    try:
                        ngl = ServerConfigResolver.load_ini().get("gpu_layers", 99)
                    except Exception:
                        ngl = 99

                ok = self.process_manager.start_llama_server_sync(
                    model_identifier=model_name,
                    context_length=target_ctx,
                    gpu_layers=ngl
                )

                # Безопасный OOM step-down снаружи _process_lock
                is_vram_oom = bool(getattr(self.process_manager, "is_vram_oom_failure", False) is True)
                is_host_oom = bool(getattr(self.process_manager, "is_host_ram_oom_failure", False) is True)
                if not is_vram_oom and not is_host_oom and (getattr(self.process_manager, "is_oom_failure", False) is True):
                    is_vram_oom = True

                if not ok and is_vram_oom:
                    attempted_ngl = getattr(self.process_manager, "_last_attempted_ngl", None)
                    if attempted_ngl is None:
                        attempted_ngl = ngl
                    base_layers = attempted_ngl
                    if base_layers is None or (isinstance(base_layers, int) and base_layers >= 99):
                        try:
                            from services.layer_budgeter import LayerBudgeter
                            meta = LayerBudgeter.parse_gguf_metadata(m_path)
                            base_layers = meta.get("total_layers", 48)
                        except Exception:
                            base_layers = 48

                    retry_ngl = max(0, int(base_layers) - 4)
                    if retry_ngl < base_layers:
                        logger.warning(
                            f"[LMStudio] Обнаружен дефицит/фрагментация VRAM при инициализации compute-графа -> "
                            f"повторный запуск с NGL={retry_ngl} (было {base_layers})"
                        )
                        ok = self.process_manager.start_llama_server_sync(
                            model_identifier=model_name,
                            context_length=target_ctx,
                            gpu_layers=retry_ngl
                        )
                elif not ok and is_host_oom:
                    logger.warning(
                        f"[LMStudio] Обнаружен дефицит Host RAM / CUDA_Host buffer -> "
                        f"повторный запуск с GGML_CUDA_NO_PINNED=1 и пропуском восстановления KV-слота (без снижения NGL)"
                    )
                    setattr(self.process_manager, "_force_cuda_no_pinned", True)
                    try:
                        ok = self.process_manager.start_llama_server_sync(
                            model_identifier=model_name,
                            context_length=target_ctx,
                            gpu_layers=ngl,
                            skip_kv_restore=True
                        )
                    finally:
                        setattr(self.process_manager, "_force_cuda_no_pinned", False)

                if not ok:
                    err_msg = getattr(self.process_manager, "last_error", "") or f"Headless сервер не смог запустить модель '{model_name}'"
                    logger.warning(f"[LMStudio] {err_msg}")
                    raise LlamaServerStartupError(err_msg)

                self._is_model_unloaded = False
                self._last_loaded_model_id = model_name
                self._current_loaded_ctx = target_ctx
                self._current_loaded_gpu = target_gpu

                actual_ngl = getattr(self, "_current_running_ngl", None)
                if actual_ngl is None and self.process_manager:
                    actual_ngl = getattr(self.process_manager, "_current_running_ngl", None)
                if actual_ngl is None:
                    actual_ngl = ngl

                total_layers = getattr(self, "_current_total_layers", None)
                if total_layers is None and self.process_manager:
                    total_layers = getattr(self.process_manager, "_current_total_layers", None)
                if total_layers is None:
                    try:
                        from services.server_config import ServerConfigResolver
                        from services.layer_budgeter import LayerBudgeter
                        m_path = ServerConfigResolver.resolve_model_path(model_name)
                        if m_path:
                            meta = LayerBudgeter.parse_gguf_metadata(m_path)
                            total_layers = meta.get("total_layers")
                    except Exception:
                        total_layers = None

                self._current_running_ngl = actual_ngl
                self._current_total_layers = total_layers

                if actual_ngl == 99:
                    layers_str = f"99 (all {total_layers} layers)" if total_layers else "99 (all layers)"
                elif total_layers and isinstance(actual_ngl, int):
                    layers_str = f"{actual_ngl}/{total_layers}"
                else:
                    layers_str = str(actual_ngl)

                logger.info(f"[LMStudio] Headless модель '{model_name}' готова к работе ({target_ctx // 1024}K, GPU layers: {layers_str}).")
                return True

            # Legacy путь через subprocess lms load
            logger.info(f"[LMStudio] Подготовка VRAM перед загрузкой '{model_name}' (сброс старых слотов)...")
            self.unload_models_cli()

            # Аппаратная пауза: стабилизация VRAM после выгрузки
            try:
                import pynvml
                pynvml.nvmlInit()
                h = pynvml.nvmlDeviceGetHandleByIndex(0)
                prev_free = 0
                for _ in range(6):
                    m_info = pynvml.nvmlDeviceGetMemoryInfo(h)
                    free_mb = m_info.free / 1024 / 1024
                    if free_mb >= 3800 or (prev_free > 0 and (free_mb - prev_free) > 500):
                        break
                    prev_free = free_mb
                    time.sleep(0.15)
            except Exception:
                time.sleep(0.2)

            try:
                import subprocess
                import sys

                startupinfo = None
                creationflags = 0
                if sys.platform == "win32":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 0
                    creationflags = subprocess.CREATE_NO_WINDOW

                logger.info(f"[LMStudio] Шлюз: чистая загрузка '{model_name}' ({target_ctx // 1024}K, GPU: {target_gpu})...")

                lms_bin = "lms"
                try:
                    from services.process_manager import ProcessManager
                    resolved = ProcessManager.locate_lms()
                    if resolved:
                        lms_bin = resolved
                except Exception:
                    pass

                cmd = [
                    lms_bin, "load", model_name,
                    "--gpu", target_gpu,
                    "--context-length", str(target_ctx),
                    "-y"
                ]
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=90,
                    startupinfo=startupinfo,
                    creationflags=creationflags
                )
                out = res.stdout or ""
                err = res.stderr or ""

                if "Model not found" in out or "No model found" in out or "not found" in err.lower() or "not found" in out.lower():
                    logger.warning(f"[LMStudio] Модель '{model_name}' не найдена на диске.")
                    raise ModelNotFoundError(model_name)

                if res.returncode == 0:
                    self._is_model_unloaded = False
                    self._last_loaded_model_id = model_name
                    self._current_loaded_ctx = target_ctx
                    self._current_loaded_gpu = target_gpu
                    logger.info(f"[LMStudio] Модель '{model_name}' готова к работе ({target_ctx // 1024}K, GPU: {target_gpu}).")
                    return True
                else:
                    logger.warning(f"[LMStudio] CLI warning: {err.strip()[:160]}")
                    raise ModelNotFoundError(model_name)
            except ModelNotFoundError:
                raise
            except Exception as e:
                logger.error(f"[LMStudio] Ошибка CLI загрузки: {e}")
                raise ModelNotFoundError(model_name)

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        context_length: Optional[int] = None,
        gpu_ratio: Optional[str] = None,
        emit_signal: bool = True,
        stream: bool = True,
        stop: Optional[List[str]] = None,
        is_roleplay: Optional[bool] = None,
        enable_thinking: Optional[bool] = None,
        respect_master_cap: bool = True,
        **kwargs
    ) -> str:
        """
        Sends chat completion request to LM Studio with streaming and tool interception.
        Supports Structured Outputs (response_format) for strict JSON validation.
        """
        is_eng = kwargs.pop("is_engineer", False)
        chunk_callback = kwargs.pop("chunk_callback", None)
        if enable_thinking is None and "enable_thinking" in kwargs:
            enable_thinking = kwargs.pop("enable_thinking")
        if "respect_master_cap" in kwargs:
            respect_master_cap = kwargs.pop("respect_master_cap")
        with _LMS_SOCKET_LOCK:
            target_m = model or getattr(self, "_last_loaded_model_id", None)
            if _is_default_model_placeholder(target_m):
                target_m = self.get_active_model_id(None)

            current_ctx = getattr(self, "_current_loaded_ctx", 8192)
            current_gpu = getattr(self, "_current_loaded_gpu", "1.0")

            from utils.config_manager import ConfigManager
            from config import HARDWARE_MAX_CONTEXT_LENGTH, MASTER_MAX_RESPONSE_TOKENS, MODE_PROFILES
            app_cfg = ConfigManager.load_config()
            m_key = "engineer" if is_eng else ("roleplay" if is_roleplay else "assistant")
            def_ctx = MODE_PROFILES.get(m_key, {}).get("context_length", 14336)

            has_pm_headless = bool(self.process_manager and hasattr(self.process_manager, "has_headless_server") and self.process_manager.has_headless_server())
            if context_length:
                target_ctx = int(context_length)
            elif not has_pm_headless and current_ctx > 0:
                target_ctx = int(current_ctx)
            else:
                target_ctx = int(app_cfg.get(f"context_length_{m_key}", def_ctx))
            target_ctx = min(target_ctx, HARDWARE_MAX_CONTEXT_LENGTH)
            target_gpu = str(gpu_ratio) if gpu_ratio is not None else "1.0"

            # Модель перезагружается только при смене модели, сбросе VRAM или нехватке контекста
            needs_reload = (
                getattr(self, "_is_model_unloaded", False)
                or not self.is_model_loaded(target_m)
                or current_ctx < target_ctx
                or current_gpu != target_gpu
            )
            if needs_reload:
                self.ensure_model_loaded(target_m, context_length=target_ctx, gpu_ratio=target_gpu)

            self._is_aborted = False
        self._current_session = self._persistent_session

        active_model_id = target_m
        self._last_loaded_model_id = active_model_id
        t_start = time.time()
        first_token_time = None
        tokens_count = 0
        logger.info(f"Sending chat completion to LM Studio for '{active_model_id}' (stream={stream}) {_get_vram_telemetry_str()}...")

        from services.session_manager import SessionManager
        from services.context_window_builder import ContextWindowBuilder

        req_messages = list(messages or [])
        req_messages = ContextWindowBuilder.sanitize_messages_for_strict_template(
            req_messages,
            prepend_scene_start_for_assistant=(is_roleplay is not False),
        )
        req_messages = list(req_messages)

        est_tokens = sum(SessionManager._estimate_message_tokens(m) for m in req_messages if isinstance(m, dict))

        context_limit = max(target_ctx, getattr(self, "_current_loaded_ctx", target_ctx))
        raw_req = int(max_tokens or 1536)
        requested = min(raw_req, MASTER_MAX_RESPONSE_TOKENS) if respect_master_cap else raw_req
        available_tokens = context_limit - est_tokens - 64
        safe_max_tokens = max(128, min(requested, max(128, available_tokens)))

        payload: Dict[str, Any] = {
            "model": active_model_id,
            "messages": req_messages,
            "temperature": float(temperature),
            "stream": stream,
            "cache_prompt": bool(kwargs.get("cache_prompt", True))
        }

        # Принудительная привязка к слоту 0 для встроенного llama-server
        if "id_slot" in kwargs:
            if kwargs["id_slot"] is not None:
                payload["id_slot"] = int(kwargs["id_slot"])
        elif has_pm_headless:
            payload["id_slot"] = 0

        if stream:
            payload["stream_options"] = {"include_usage": True}

        if enable_thinking is not None:
            payload["chat_template_kwargs"] = {
                "enable_thinking": bool(enable_thinking),
                "thinking": bool(enable_thinking),
            }
            if enable_thinking is False and not kwargs.get("response_format"):
                from config import THINKING_MODEL_KEYWORDS
                _target_lower = str(active_model_id or "").lower()
                if any(k in _target_lower for k in THINKING_MODEL_KEYWORDS):
                    has_last_assistant = bool(req_messages and req_messages[-1].get("role") == "assistant")
                    if not has_last_assistant:
                        req_messages.append({"role": "assistant", "content": "<think>\n\n</think>\n\n"})
                        payload["messages"] = req_messages
        if stop:
            payload["stop"] = stop
        if safe_max_tokens > 0:
            payload["max_tokens"] = safe_max_tokens
            payload["max_completion_tokens"] = safe_max_tokens

        # Поддержка синтаксиса LM Studio / llama.cpp REST
        rep_val = kwargs.get("repetition_penalty") or kwargs.get("repeat_penalty")
        if rep_val is not None:
            payload["repetition_penalty"] = float(rep_val)
            payload["repeat_penalty"] = float(rep_val)

        if "min_p" in kwargs and kwargs["min_p"] is not None:
            payload["min_p"] = float(kwargs["min_p"])

        for extra_k in ("presence_penalty", "frequency_penalty", "top_p", "top_k", "response_format"):
            if extra_k in kwargs and kwargs[extra_k] is not None:
                payload[extra_k] = kwargs[extra_k]

        # Нативный сэмплер повторов DRY (Don't Repeat Yourself) для llama-server
        if "dry_multiplier" in kwargs and kwargs["dry_multiplier"] is not None:
            payload["dry_multiplier"] = float(kwargs["dry_multiplier"])
        if "dry_base" in kwargs and kwargs["dry_base"] is not None:
            payload["dry_base"] = float(kwargs["dry_base"])
        if "dry_allowed_length" in kwargs and kwargs["dry_allowed_length"] is not None:
            payload["dry_allowed_length"] = int(kwargs["dry_allowed_length"])
        if "dry_penalty_last_n" in kwargs and kwargs["dry_penalty_last_n"] is not None:
            payload["dry_penalty_last_n"] = int(kwargs["dry_penalty_last_n"])

        # Нативный сэмплер XTC (Exclude Top Choices) для llama-server
        if "xtc_probability" in kwargs and kwargs["xtc_probability"] is not None:
            payload["xtc_probability"] = float(kwargs["xtc_probability"])
        if "xtc_threshold" in kwargs and kwargs["xtc_threshold"] is not None:
            payload["xtc_threshold"] = float(kwargs["xtc_threshold"])

        logger.info(
            f"[LMS-PARAMS] model='{active_model_id}', temp={payload.get('temperature')}, "
            f"top_p={payload.get('top_p')}, min_p={payload.get('min_p')}, "
            f"rep_pen={payload.get('repetition_penalty')}, dry_mult={payload.get('dry_multiplier')}, "
            f"xtc_p={payload.get('xtc_probability')}, xtc_t={payload.get('xtc_threshold')}, "
            f"cache_prompt={payload.get('cache_prompt')}, "
            f"max_tokens={payload.get('max_tokens')} (context_limit={context_limit})"
        )

        headers = {
            "Content-Type": "application/json"
        }
        endpoint = f"{self.base_url}/chat/completions"

        try:

            resp = self._current_session.post(
                endpoint,
                json=payload,
                headers=headers,
                stream=stream,
                timeout=180.0
            )
            self._active_response = resp
            resp.encoding = "utf-8"

            if self._is_aborted:
                raise InterruptedError("Запрос к LM Studio был прерван пользователем.")

            if resp.status_code != 200:
                resp_text = resp.text
                resp_lower = resp_text.lower()
                has_image = any(isinstance(m.get("content"), list) for m in payload.get("messages", []))
                if has_image and (
                    resp.status_code == 400
                    or "vision" in resp_lower
                    or "image" in resp_lower
                    or "support" in resp_lower
                    or "multimodal" in resp_lower
                ):
                    err_text = (
                        "⚠️ Текущая модель не поддерживает распознавание изображений. "
                        "Выберите Vision-модель (например, Qwen2.5-VL)."
                    )
                    logger.warning(err_text)
                    self.error_occurred.emit(err_text)
                if "No models loaded" in resp_text or "not found" in resp_lower or "model not loaded" in resp_lower:
                    logger.warning(f"[LMStudio] Модель '{active_model_id}' не найдена или не загружена: {resp_text}")
                    raise ModelNotFoundError(model or active_model_id or "активная модель")

                if resp.status_code == 400 and (
                    "jinja exception" in resp_lower
                    or "conversation roles must alternate" in resp_lower
                    or "roles must alternate" in resp_lower
                    or "unable to generate parser for this template" in resp_lower
                ):
                    friendly_switch_msg = "Вы сменили модель, начните новый диалог, чтобы продолжить"
                    raw_detail = resp_text.strip().replace("\n", " ")[:300]
                    logger.warning(
                        f"[LMStudio] Ошибка шаблона чата ('{active_model_id}'): {raw_detail} | "
                        f"Сообщение пользователю: {friendly_switch_msg}"
                    )
                    raise RuntimeError(friendly_switch_msg)

                err_text = f"Ошибка LM Studio [HTTP {resp.status_code}]: {resp.text}"
                logger.error(err_text)
                self.error_occurred.emit(err_text)
                raise RuntimeError(err_text)

            content = ""
            prompt_tokens = 0
            completion_tokens = 0
            cache_n = 0
            prompt_n = 0
            finish_reason = "unknown"

            if stream:
                accumulated = []
                first_token_time = None
                tokens_count = 0
                in_reasoning_stream = False
                rc_line_buf = ""
                rc_recent_lines: List[str] = []
                rc_loop_broken = False

                try:
                    for line_bytes in resp.iter_lines(decode_unicode=False):
                        if self._is_aborted:
                            raise InterruptedError("Запрос к LM Studio был прерван пользователем.")
                        if not line_bytes:
                            continue

                        line = line_bytes.decode("utf-8", errors="replace").strip()
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                            except Exception:
                                continue

                            # Перехват SSE chunk с полем "error" от сервера
                            if isinstance(chunk, dict) and chunk.get("error"):
                                err_payload = chunk["error"]
                                if isinstance(err_payload, dict):
                                    err_msg = err_payload.get("message") or err_payload.get("code") or "Unknown SSE error"
                                else:
                                    err_msg = str(err_payload)
                                logger.error(f"[LMStudio] Сервер вернул ошибку в SSE-потоке: {err_msg}")
                                raise RuntimeError(f"LLM stream error: {err_msg}")

                            try:
                                # Извлекаем usage и timings до проверки choices (в финальном служебном чанке choices=[])
                                usage_obj = chunk.get("usage")
                                if isinstance(usage_obj, dict):
                                    if "prompt_tokens" in usage_obj:
                                        prompt_tokens = int(usage_obj.get("prompt_tokens") or 0)
                                    if "completion_tokens" in usage_obj:
                                        completion_tokens = int(usage_obj.get("completion_tokens") or 0)
                                timings_obj = chunk.get("timings")
                                if isinstance(timings_obj, dict):
                                    if "cache_n" in timings_obj:
                                        cache_n = int(timings_obj.get("cache_n") or 0)
                                    if "prompt_n" in timings_obj:
                                        prompt_n = int(timings_obj.get("prompt_n") or 0)

                                choices = chunk.get("choices", [])
                                if not choices:
                                    continue
                                delta = choices[0].get("delta", {})
                                if choices[0].get("finish_reason"):
                                    finish_reason = choices[0].get("finish_reason")
                                rc_piece = delta.get("reasoning_content") or delta.get("reasoning") or ""
                                content_piece = delta.get("content") or ""

                                if not rc_piece and not content_piece:
                                    continue

                                pieces_to_emit = []
                                if rc_piece:
                                    clean_rc = re.sub(r"</?think>", "", rc_piece, flags=re.IGNORECASE)
                                    if not in_reasoning_stream:
                                        in_reasoning_stream = True
                                        pieces_to_emit.append("<think>")
                                    if clean_rc:
                                        pieces_to_emit.append(clean_rc)
                                elif content_piece:
                                    if in_reasoning_stream:
                                        in_reasoning_stream = False
                                        pieces_to_emit.append("</think>")
                                    pieces_to_emit.append(content_piece)

                                for piece in pieces_to_emit:
                                    if first_token_time is None:
                                        first_token_time = time.time()
                                        ttft = first_token_time - t_start
                                        logger.info(f"[LMS-TELEMETRY] Time to First Token (TTFT): {ttft:.2f} сек {_get_vram_telemetry_str()}")
                                    tokens_count += 1
                                    accumulated.append(piece)

                                    if chunk_callback and callable(chunk_callback):
                                        try:
                                            chunk_callback(piece)
                                        except Exception as _cb_err:
                                            logger.debug(f"Error in chunk_callback: {_cb_err}")

                                    if emit_signal:
                                        self.token_streamed.emit(piece)

                                if rc_piece:
                                    rc_line_buf += rc_piece
                                    while "\n" in rc_line_buf:
                                        full_line, rc_line_buf = rc_line_buf.split("\n", 1)
                                        norm_line = full_line.strip()
                                        if norm_line and (len(norm_line) >= 8 or "<|channel>" in norm_line):
                                            rc_recent_lines.append(norm_line)
                                            if len(rc_recent_lines) > 16:
                                                rc_recent_lines.pop(0)
                                            if len(rc_recent_lines) >= 12 and len(set(rc_recent_lines[-12:])) == 1:
                                                logger.warning("[LMStudio] Reasoning Anti-Loop: прервано зацикливание в reasoning_content.")
                                                if in_reasoning_stream:
                                                    in_reasoning_stream = False
                                                    accumulated.append("</think>")
                                                    if chunk_callback and callable(chunk_callback):
                                                        try:
                                                            chunk_callback("</think>")
                                                        except Exception:
                                                            pass
                                                    if emit_signal:
                                                        self.token_streamed.emit("</think>")
                                                try:
                                                    resp.close()
                                                except Exception:
                                                    pass
                                                rc_loop_broken = True
                                                break
                                    if rc_loop_broken:
                                        break

                            except Exception:
                                continue
                        if rc_loop_broken:
                            break
                    if in_reasoning_stream:
                        in_reasoning_stream = False
                        accumulated.append("</think>")
                        if chunk_callback and callable(chunk_callback):
                            try:
                                chunk_callback("</think>")
                            except Exception:
                                pass
                        if emit_signal:
                            self.token_streamed.emit("</think>")

                    if self._is_aborted:
                        raise InterruptedError("Запрос к LM Studio был прерван пользователем.")
                except Exception as stream_err:
                    if self._is_aborted:
                        raise InterruptedError("Запрос к LM Studio был прерван пользователем.")
                    raise stream_err
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass

                content = "".join(accumulated)
                if not content and not self._is_aborted:
                    raise RuntimeError("LLM stream closed with 0 tokens (server disconnected/crashed)")

            if not content:
                try:
                    raw_text = resp.content.decode("utf-8", errors="replace")
                    data = json.loads(raw_text)
                    choices = data.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        content = msg.get("content", "") or ""
                        reasoning = msg.get("reasoning_content", "") or msg.get("reasoning", "") or ""
                        if reasoning and "<think>" not in content:
                            content = f"<think>\n{reasoning}\n</think>\n{content}"
                        if choices[0].get("finish_reason"):
                            finish_reason = choices[0].get("finish_reason")
                    usage = data.get("usage", {})
                    if "prompt_tokens" in usage:
                        prompt_tokens = int(usage.get("prompt_tokens") or 0)
                    if "completion_tokens" in usage:
                        completion_tokens = int(usage.get("completion_tokens") or 0)
                    timings = data.get("timings", {})
                    if "cache_n" in timings:
                        cache_n = int(timings.get("cache_n") or 0)
                    if "prompt_n" in timings:
                        prompt_n = int(timings.get("prompt_n") or 0)
                    if completion_tokens > 0:
                        tokens_count = completion_tokens
                except Exception:
                    pass

            if tokens_count == 0 and completion_tokens > 0:
                tokens_count = completion_tokens
            if prompt_n == 0 and prompt_tokens > 0 and cache_n == 0:
                prompt_n = prompt_tokens

            content = str(content)
            t_total = time.time() - t_start
            ttft_val = (first_token_time - t_start) if first_token_time else 0.0
            gen_duration = max(0.05, t_total - ttft_val)
            tok_per_sec = (tokens_count / gen_duration) if tokens_count > 0 else (len(content) / 3.5 / gen_duration)
            
            logger.info(
                f"[LMS-TELEMETRY] Готово: {len(content)} симв, ~{tokens_count} токенов за {t_total:.2f} с "
                f"(TTFT: {ttft_val:.2f} с, Генерация: {gen_duration:.2f} с | Скорость: {tok_per_sec:.1f} tok/s) "
                f"finish_reason={finish_reason}, prompt_tokens={prompt_tokens} "
                f"(KV hit: {cache_n} tok, eval: {prompt_n} tok) "
                f"{_get_vram_telemetry_str()}"
            )

            if emit_signal:
                self.response_received.emit(content)
            return content

        except ModelNotFoundError:
            raise
        except requests.exceptions.HTTPError as http_err:
            resp_text = getattr(http_err.response, "text", "")
            if "No models loaded" in resp_text or "not found" in resp_text.lower():
                raise ModelNotFoundError(model or "активная модель")
            raise RuntimeError(f"Ошибка LM Studio [HTTP {http_err.response.status_code}]: {resp_text}")
        except requests.exceptions.ConnectionError:
            self._is_model_unloaded = True
            self._last_loaded_model_id = None

            is_headless = (
                self.process_manager is not None
                and hasattr(self.process_manager, "has_headless_server")
                and self.process_manager.has_headless_server()
            )

            is_evicted = False
            if self.process_manager is not None:
                state = getattr(self.process_manager, "_server_state", None)
                state_val = getattr(state, "value", state)
                if state_val in ("EVICTED_FOR_FORGE",):
                    is_evicted = True

            if is_headless and not is_evicted:
                proc = getattr(self.process_manager, "_llama_process", None)
                if proc is not None and proc.poll() is not None:
                    rc = proc.poll()
                    detail_msg = ""
                    if hasattr(self.process_manager, "_extract_llama_startup_error"):
                        try:
                            detail_msg, _ = self.process_manager._extract_llama_startup_error()
                        except Exception:
                            pass
                    last_err = getattr(self.process_manager, "last_error", "") or ""
                    err_desc = detail_msg or last_err or f"код завершения {rc}"
                    err_msg = f"Локальный процесс llama-server аварийно завершился (код {rc}): {err_desc}"
                    logger.error(f"[LMStudio] {err_msg}")
                    self.error_occurred.emit(err_msg)
                    raise ConnectionError(err_msg)

            if self.process_manager is not None:
                try:
                    self.process_manager.is_lm_studio_running()
                except Exception:
                    pass
            err_msg = (
                f"Не удалось подключиться к LM Studio / серверу LLM на {self.base_url}.\n"
                f"Убедитесь, что сервер запущен в LM Studio на порту 1234."
            )
            logger.error(err_msg)
            self.error_occurred.emit(err_msg)
            raise ConnectionError(err_msg)
        except Exception as e:
            err_str = str(e).lower()
            if isinstance(e, RuntimeError) and any(m in err_str for m in ("0 tokens", "bad allocation", "out of memory", "cudamalloc failed")):
                logger.warning(f"[LMStudio] Обнаружен срыв потока/OOM ('{e}'). Сброс состояния модели для перезапуска.")
                self._is_model_unloaded = True
                self._last_loaded_model_id = None
                if self.process_manager is not None:
                    try:
                        if any(m in err_str for m in ("bad allocation", "out of memory", "cudamalloc failed")):
                            self.process_manager.is_oom_failure = True
                            self.process_manager.last_error = str(e)
                        self.process_manager.is_lm_studio_running()
                    except Exception:
                        pass
            if not self._is_aborted:
                self.error_occurred.emit(str(e))
            raise
        finally:
            self._current_session = None
            self._active_response = None

    def stream_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        **kwargs
    ) -> Iterator[str]:
        """
        Streams completion token chunks by invoking chat_completion with chunk_callback
        in a background thread and yielding chunks synchronously.
        """
        q: queue.Queue = queue.Queue()
        sentinel = object()

        def _worker():
            try:
                call_kwargs = dict(kwargs)
                call_kwargs.pop("stream", None)
                call_kwargs.pop("emit_signal", None)
                call_kwargs.pop("chunk_callback", None)
                self.chat_completion(
                    messages=messages,
                    chunk_callback=lambda tok: q.put(tok),
                    stream=True,
                    emit_signal=False,
                    **call_kwargs
                )
            except Exception as exc:
                q.put(exc)
            finally:
                q.put(sentinel)

        threading.Thread(target=_worker, daemon=True).start()

        while True:
            item = q.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield item
