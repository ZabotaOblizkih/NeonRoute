"""
LM Studio Client for NeonRoute
Communicates with local OpenAI-compatible endpoint (http://127.0.0.1:1234/v1).
Auto-detects active model ID from GET /v1/models, constructs valid JSON payload,
emits streaming and final signals, supports cancellation, Keep-Alive connection pooling,
and Structured Outputs (response_format).
"""

import re
import json
import logging
import threading
import time
import requests
from requests.adapters import HTTPAdapter
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger("NeonRoute.LMStudio")

_LMS_SOCKET_LOCK = threading.RLock()


class ModelNotFoundError(RuntimeError):
    """Выбрасывается, когда запрашиваемая модель отсутствует в локальном хранилище LM Studio."""
    def __init__(self, model_name: str):
        self.model_name = model_name
        super().__init__(f"Модель '{model_name}' не найдена в хранилище LM Studio.")



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
        self._reload_lock = threading.Lock()
        self._initialized = True

    def _init_session(self):
        self._persistent_session = requests.Session()
        adapter = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=1)
        self._persistent_session.mount("http://", adapter)
        self._persistent_session.mount("https://", adapter)

    def is_model_loaded(self, model_name: Optional[str] = None) -> bool:
        """Проверяет готовность целевой модели в VRAM по состоянию жизненного цикла клиента."""
        if getattr(self, "_is_model_unloaded", False):
            return False

        target = model_name or getattr(self, "_last_loaded_model_id", None)
        last_id = getattr(self, "_last_loaded_model_id", None)
        if not target or not last_id:
            return False

        t_clean = target.lower().strip()
        l_clean = last_id.lower().strip()
        return t_clean == l_clean

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

        if preferred_model and not preferred_model.startswith("default"):
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
        logger.info("Unloading LM Studio models to free VRAM...")
        try:
            clean_root = self.base_url.strip().rstrip("/")
            if clean_root.endswith("/v1"):
                clean_root = clean_root[:-3].rstrip("/")
            for ep in (f"{clean_root}/api/v0/model/unload", f"{clean_root}/api/v0/models/unload"):
                try:
                    r = requests.post(ep, json={}, timeout=0.8)
                    if r.status_code in (200, 204):
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
            logger.info(f"CLI unload completed (code {res_unl.returncode}): {res_unl.stdout.strip() or res_unl.stderr.strip()} {_get_vram_telemetry_str()}")
        except Exception as e:
            logger.warning(f"Failed to unload LMS models via CLI: {e}")

        self._is_model_unloaded = True
        self._current_ctx = 0
        self._current_loaded_ctx = 0
        self._current_loaded_gpu = ""
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
            if not target_model or target_model.startswith("default"):
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
            # Перезагрузка не требуется, если модель активна, а текущей ёмкости контекста достаточно
            if (
                not getattr(self, "_is_model_unloaded", False)
                and self.is_model_loaded(model_name)
                and current_ctx >= target_ctx
                and current_gpu == target_gpu
            ):
                self._last_loaded_model_id = model_name
                return True

            # Монопольная зачистка: выгружает старую модель перед стартом новой
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

                # После вызова CLI lms load:
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
        **kwargs
    ) -> str:
        """
        Sends chat completion request to LM Studio with streaming and tool interception.
        Supports Structured Outputs (response_format) for strict JSON validation.
        """
        is_eng = kwargs.pop("is_engineer", False)
        with _LMS_SOCKET_LOCK:
            target_m = model or getattr(self, "_last_loaded_model_id", None)
            if not target_m or target_m.startswith("default"):
                target_m = self.get_active_model_id(None)

            current_ctx = getattr(self, "_current_loaded_ctx", 8192)
            current_gpu = getattr(self, "_current_loaded_gpu", "1.0")

            from utils.config_manager import ConfigManager
            from config import HARDWARE_MAX_CONTEXT_LENGTH, MASTER_MAX_RESPONSE_TOKENS, MODE_PROFILES
            app_cfg = ConfigManager.load_config()
            m_key = "engineer" if is_eng else ("roleplay" if is_roleplay else "assistant")
            def_ctx = MODE_PROFILES.get(m_key, {}).get("context_length", 14336)

            if context_length:
                target_ctx = int(context_length)
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

        est_tokens = sum(SessionManager._estimate_message_tokens(m) for m in messages if isinstance(m, dict))

        context_limit = max(target_ctx, getattr(self, "_current_loaded_ctx", target_ctx))
        requested = min(int(max_tokens or 1536), MASTER_MAX_RESPONSE_TOKENS)
        available_tokens = context_limit - est_tokens - 64
        safe_max_tokens = max(128, min(requested, max(128, available_tokens)))

        payload: Dict[str, Any] = {
            "model": active_model_id,
            "messages": messages,
            "temperature": float(temperature),
            "stream": stream
        }
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

        logger.info(
            f"[LMS-PARAMS] model='{active_model_id}', temp={payload.get('temperature')}, "
            f"top_p={payload.get('top_p')}, min_p={payload.get('min_p')}, "
            f"rep_pen={payload.get('repetition_penalty')}, "
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

                err_text = f"Ошибка LM Studio [HTTP {resp.status_code}]: {resp.text}"
                logger.error(err_text)
                self.error_occurred.emit(err_text)
                raise RuntimeError(err_text)

            content = ""
            if stream:
                accumulated = []
                first_token_time = None
                tokens_count = 0

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
                                choices = chunk.get("choices", [])
                                if not choices:
                                    continue
                                delta = choices[0].get("delta", {})
                                content_piece = delta.get("content", "")
                                if not content_piece:
                                    continue

                                if content_piece:
                                    if first_token_time is None:
                                        first_token_time = time.time()
                                        ttft = first_token_time - t_start
                                        logger.info(f"[LMS-TELEMETRY] Time to First Token (TTFT): {ttft:.2f} сек {_get_vram_telemetry_str()}")
                                    tokens_count += 1

                                accumulated.append(content_piece)

                                if emit_signal:
                                    self.token_streamed.emit(content_piece)

                            except Exception:
                                continue
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

            finish_reason = "unknown"
            prompt_tokens = 0
            if not content:
                try:
                    raw_text = resp.content.decode("utf-8", errors="replace")
                    data = json.loads(raw_text)
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "") or ""
                    usage = data.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    finish_reason = choices[0].get("finish_reason", "unknown") if choices else "unknown"
                    if completion_tokens > 0:
                        tokens_count = completion_tokens
                except Exception:
                    pass


            content = str(content)
            t_total = time.time() - t_start
            ttft_val = (first_token_time - t_start) if first_token_time else 0.0
            gen_duration = max(0.05, t_total - ttft_val)
            tok_per_sec = (tokens_count / gen_duration) if tokens_count > 0 else (len(content) / 3.5 / gen_duration)
            
            logger.info(
                f"[LMS-TELEMETRY] Готово: {len(content)} симв, ~{tokens_count} токенов за {t_total:.2f} с "
                f"(TTFT: {ttft_val:.2f} с, Генерация: {gen_duration:.2f} с | Скорость: {tok_per_sec:.1f} tok/s) "
                f"finish_reason={finish_reason}, prompt_tokens={prompt_tokens} "
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
            err_msg = (
                f"Не удалось подключиться к LM Studio на {self.base_url}.\n"
                f"Убедитесь, что сервер запущен в LM Studio на порту 1234."
            )
            logger.error(err_msg)
            self.error_occurred.emit(err_msg)
            raise ConnectionError(err_msg)
        except Exception as e:
            if not self._is_aborted:
                self.error_occurred.emit(str(e))
            raise
        finally:
            self._current_session = None
            self._active_response = None
