"""
Stable Diffusion WebUI Forge Client for NeonRoute
Communicates with Forge REST API at http://127.0.0.1:7860
Supports pre-flight server availability checks, checkpoint switching,
txt2img generation, and hardware-safe cancellation.
"""

import os
import re
import time
import json
import copy
import socket
import base64
import logging
import threading
import requests
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from PyQt6.QtCore import QObject, pyqtSignal
from config import OUTPUTS_DIR, DEFAULT_SD_SETTINGS, FORGE_ENABLE_VAE_TILING, FORGE_ROOT_DIR
from core.forge_locator import get_clean_forge_env

logger = logging.getLogger("NeonRoute.ForgeClient")

SDXL_MARKERS = ("xl", "sdxl", "pony", "dmd", "insta", "mop")


def is_sdxl_checkpoint(model_name: str) -> bool:
    """
    Определяет, относится ли чекпоинт к архитектуре SDXL.
    Проверяет маркеры в имени файла: 'xl', 'sdxl', 'pony', 'dmd', 'insta', 'mop' (без учета регистра)
    либо сверяет размер/метаданные, если модель присутствует в реестре.
    """
    if not model_name:
        return False
    name_str = str(model_name).strip()
    if not name_str:
        return False
    name_lower = name_str.lower()

    if any(m in name_lower for m in SDXL_MARKERS):
        return True

    try:
        from services.art_director_config import load_art_director_models
        for m in load_art_director_models():
            ckpt = str(m.get("checkpoint", "")).lower()
            m_id = str(m.get("id", "")).lower()
            m_name = str(m.get("name", "")).lower()
            if name_lower in (ckpt, m_id, m_name) or (ckpt and ckpt in name_lower):
                arch = str(m.get("architecture", "")).lower()
                base_model = str(m.get("base_model", "")).lower()
                if "sdxl" in arch or "sdxl" in base_model or "pony" in arch:
                    return True
                if any(m_marker in ckpt for m_marker in SDXL_MARKERS):
                    return True
                size_gb = m.get("size_gb") or m.get("size")
                if size_gb and isinstance(size_gb, (int, float)) and size_gb > 5.0:
                    return True
    except Exception:
        pass

    return False

STAGE_DETECTORS = [
    ("hand_yolov8", "✋ Улучшение рук"),
    ("face_mesh_eyes", "👁️ Детализация взгляда"),
    ("face", "👤 Улучшение лица"),
    ("person_yolov8", "✨ Детализация силуэта"),
    ("deepfashion", "👗 Прорисовка костюма"),
    ("codeformer", "🪄 Нейрореставрация"),
    ("gfpgan", "🪄 Нейрореставрация"),
]

# Фильтр экстремальных динамических перекрытий, где детектор YOLO ошибается в геометрии лица
INTIMATE_DYNAMIC_TRIGGERS = [
    "oral", "blowjob", "blow job", "deepthroat", "kneeling", "on knees",
    "close-up interaction", "closeup interaction", "минет", "на коленях"
]


def has_intimate_or_dynamic_triggers(text: str) -> bool:
    """
    Detects intimate or dynamic triggers in prompt where face_yolov8n ADetailer
    fails due to extreme angles, occlusions, or tilted views.
    """
    if not text:
        return False
    lower = text.lower()
    for trig in INTIMATE_DYNAMIC_TRIGGERS:
        if " " in trig or "-" in trig:
            if trig in lower or trig.replace("-", " ") in lower or trig.replace(" ", "-") in lower:
                return True
        else:
            if re.search(rf"\b{re.escape(trig)}\b", lower):
                return True
    return False


class ForgeOfflineError(ConnectionError):
    """Raised when Forge server is not reachable or still booting."""
    pass


class ForgeClient(QObject):
    generation_progress = pyqtSignal(str, int)  # stage_name, percent
    _instance: Optional['ForgeClient'] = None

    @classmethod
    def get_instance(cls) -> 'ForgeClient':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, base_url: str = "http://127.0.0.1:7860", parent=None, system_monitor=None):
        super().__init__(parent)
        ForgeClient._instance = self
        self.base_url = base_url.rstrip("/")
        self.system_monitor = system_monitor
        self._unload_lock = threading.Lock()
        self._last_vram_wait_ts: float = 0.0
        self._current_session: Optional[requests.Session] = None
        self._is_aborted = False
        self._checkpoint_unloaded = False
        self._has_loaded_model_in_vram = False
        self._last_vram_policy_key: Optional[Tuple[bool, int]] = None
        self._last_vram_mode: Optional[str] = None
        self._last_always_offload: Optional[bool] = None
        self._last_requested_checkpoint: Optional[str] = None
        self._last_requested_dtype: Optional[str] = None
        self._never_oom_vae_active: bool = False
        self._cached_scripts: Optional[Dict[str, List[str]]] = None
        self._checkpoint_cache_size: int = 0
        self._cached_upscalers: Optional[List[str]] = None
        self._cached_embeddings: Optional[List[str]] = None
        self._free_vram_supported: bool = True
        self._free_vram_warned: bool = False
        self.last_generation_info: Dict[str, Any] = {}

    def get_installed_embeddings(self) -> List[str]:
        """Опрашивает доступные Textual Inversion эмбеддинги с локальным файловым сканированием."""
        if self._cached_embeddings is not None:
            return self._cached_embeddings
        found = []
        try:
            resp = requests.get(f"{self.base_url}/sdapi/v1/embeddings", timeout=1.0)
            if resp.status_code == 200:
                loaded = resp.json().get("loaded", {})
                found.extend([k for k in loaded.keys()])
        except Exception:
            pass

        # Надежный поиск файлов на диске, если эндпоинт Forge возвращает ошибку 500
        local_emb_dirs = [
            FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui" / "embeddings",
            FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui" / "models" / "embeddings"
        ]
        for d in local_emb_dirs:
            if d.exists():
                try:
                    for f in d.iterdir():
                        if f.is_file() and f.suffix.lower() in (".pt", ".bin", ".safetensors"):
                            stem = f.stem
                            if stem not in found:
                                found.append(stem)
                except Exception:
                    pass

        self._cached_embeddings = found
        if found:
            logger.info(f"[ForgeClient] Активные эмбеддинги: {self._cached_embeddings}")
        return self._cached_embeddings

    def get_upscalers(self) -> List[str]:
        """
        Retrieves list of available upscalers from Forge (/sdapi/v1/upscalers).
        Fast check with 2.0s timeout, cached in self._cached_upscalers.
        """
        if self._cached_upscalers is not None:
            return self._cached_upscalers
        try:
            resp = requests.get(f"{self.base_url}/sdapi/v1/upscalers", timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                self._cached_upscalers = [
                    u["name"] for u in data if isinstance(u, dict) and "name" in u
                ]
                logger.info(f"Forge detected upscalers: {self._cached_upscalers}")
                return self._cached_upscalers
        except Exception as e:
            logger.debug(f"Could not fetch upscalers from Forge: {e}")
        return []

    def has_script(self, script_title: str, tab: str = "txt2img") -> bool:
        """Проверяет наличие alwayson-скрипта в Forge (/sdapi/v1/scripts) с кэшированием."""
        if not script_title:
            return False
        try:
            if self._cached_scripts is None:
                resp = requests.get(f"{self.base_url}/sdapi/v1/scripts", timeout=1.5)
                if getattr(resp, "status_code", None) == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        self._cached_scripts = {
                            str(k): [str(s).strip().lower() for s in v]
                            for k, v in data.items()
                            if isinstance(v, list)
                        }
            if isinstance(self._cached_scripts, dict):
                return script_title.strip().lower() in self._cached_scripts.get(tab, [])
        except Exception:
            pass
        return False

    def resolve_safe_hires_settings(
        self,
        requested_upscaler: Optional[str] = None,
        requested_denoising: Optional[float] = None
    ) -> tuple[str, float]:
        """
        Dynamically selects a safe, high-quality upscaler with intelligent denoising pairing:
        - If 'R-ESRGAN 4x+' (or 'R-ESRGAN 4x+ Anime6B' for anime) is available in Forge,
          prioritize it while preserving the author's custom denoising strength (e.g. 0.35 for realistic models).
        - If only built-in 'Latent' is available (fallback): if denoise < 0.48, automatically
          adjust to 0.50 to eliminate latent space blur.
        """
        available = self.get_upscalers()
        available_map = {u.strip().lower(): u for u in available}

        target = (requested_upscaler or DEFAULT_SD_SETTINGS.get("hr_upscaler", "R-ESRGAN 4x+")).strip()
        default_denoise = DEFAULT_SD_SETTINGS.get("denoising_strength", 0.35)
        denoise = requested_denoising if requested_denoising is not None else default_denoise

        selected_upscaler = None

        # Priority 1: Check if requested upscaler or ESRGAN is available
        if target.lower() in available_map:
            selected_upscaler = available_map[target.lower()]
        elif "anime" in target.lower() and "r-esrgan 4x+ anime6b" in available_map:
            selected_upscaler = available_map["r-esrgan 4x+ anime6b"]
        elif "r-esrgan 4x+" in available_map:
            selected_upscaler = available_map["r-esrgan 4x+"]
        elif "r-esrgan 4x+ anime6b" in available_map:
            selected_upscaler = available_map["r-esrgan 4x+ anime6b"]

        if selected_upscaler and selected_upscaler.lower() != "latent":
            return selected_upscaler, denoise

        # Fallback to native Latent (built into SD core, 0 network downloads)
        if denoise < 0.48:
            denoise = 0.50
        return "Latent", denoise

    def is_server_ready(self, timeout: float = 1.0) -> bool:
        """
        Fast lightweight pre-flight check to verify if Forge port is open
        and responding to API requests before sending heavy payloads.
        """
        try:
            with socket.create_connection(("127.0.0.1", 7860), timeout=timeout):
                pass
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

        try:
            resp = requests.get(f"{self.base_url}/sdapi/v1/sd-models", timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def check_health(self) -> Tuple[bool, str]:
        """Checks if SD Forge server is reachable."""
        try:
            resp = requests.get(f"{self.base_url}/sdapi/v1/sd-models", timeout=1.5)
            if resp.status_code == 200:
                return True, "SD Forge Online"
            return False, f"HTTP {resp.status_code}"
        except requests.exceptions.ConnectionError:
            return False, "SD Forge Offline"
        except Exception as e:
            return False, f"Offline ({str(e)})"

    @staticmethod
    def parse_progress_payload(
        data: Dict[str, Any],
        is_hr_enabled: bool = False,
        steps_hr: int = 0,
        last_step: int = 0,
        base_pass_finished: bool = False,
    ) -> Tuple[Dict[str, Any], bool]:
        """Нормализует JSON-ответ /sdapi/v1/progress и определяет стадию генерации."""
        state = data.get("state") or {}
        raw_textinfo = str(state.get("textinfo") or "")
        textinfo = raw_textinfo.lower()
        step = int(state.get("sampling_step") or 0)
        total_steps = int(state.get("sampling_steps") or 0)
        progress_float = float(data.get("progress") or 0.0)
        eta_relative = float(data.get("eta_relative") or 0.0)

        stage_name = None
        for keyword, title in STAGE_DETECTORS:
            if keyword in textinfo:
                stage_name = title
                break

        if not stage_name:
            if step < last_step and last_step > 0:
                base_pass_finished = True
            if "hires" in textinfo or base_pass_finished or (is_hr_enabled and step > 0 and total_steps == steps_hr):
                stage_name = "🔍 Повышение четкости"
            elif total_steps > 0 or step > 0 or progress_float > 0.0:
                stage_name = "🎨 Создание общего плана"

        if total_steps > 0:
            pct = int((step / total_steps) * 100)
        else:
            pct = int(progress_float * 100)
        pct = max(0, min(100, pct))

        parsed = {
            "progress": progress_float,
            "step": step,
            "total_steps": total_steps,
            "eta_relative": eta_relative,
            "textinfo": raw_textinfo,
            "stage_name": stage_name,
            "percent": pct,
        }
        return parsed, base_pass_finished

    def get_progress(self, timeout: float = 1.0) -> Dict[str, Any]:
        """Возвращает нормализованный снимок прогресса генерации из /sdapi/v1/progress."""
        try:
            resp = requests.get(
                f"{self.base_url}/sdapi/v1/progress",
                params={"skip_current_image": "true"},
                timeout=timeout,
            )
            if resp.status_code == 200:
                parsed, _ = self.parse_progress_payload(resp.json())
                return parsed
        except Exception:
            pass
        return {
            "progress": 0.0,
            "step": 0,
            "total_steps": 0,
            "eta_relative": 0.0,
            "textinfo": "",
            "stage_name": None,
            "percent": 0,
        }

    def get_models(self) -> List[Dict[str, str]]:
        """
        Retrieves list of checkpoints available in Forge.
        Returns list of dicts with 'title' and 'model_name'.
        """
        try:
            resp = requests.get(f"{self.base_url}/sdapi/v1/sd-models", timeout=2.5)
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception as e:
            logger.debug(f"Failed to fetch models from Forge: {e}")
            return []

    def get_installed_checkpoints(self) -> List[str]:
        """
        Retrieves list of checkpoint names/files installed in Forge.
        Queries GET http://127.0.0.1:7860/sdapi/v1/sd-models only if Forge is running.
        Otherwise immediately falls back to fast local disk scan (<1 ms).
        """
        checkpoints: List[str] = []
        if hasattr(self, "is_running") and self.is_running():
            try:
                resp = requests.get(f"{self.base_url}/sdapi/v1/sd-models", timeout=1.0)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data:
                        if isinstance(item, dict):
                            fn = item.get("filename")
                            name = ""
                            if fn:
                                name = Path(fn).name
                            if not name:
                                name = item.get("model_name") or item.get("title", "")
                            name = str(name).strip()
                            if name and name not in checkpoints:
                                checkpoints.append(name)
            except Exception as e:
                logger.debug(f"Failed to fetch installed checkpoints from Forge API: {e}")

        # Local directory fallback scan (discovers files instantly if Forge is offline or booting)
        local_models_dir = FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui" / "models" / "Stable-diffusion"
        if local_models_dir.exists():
            try:
                for f in local_models_dir.iterdir():
                    if f.is_file() and f.suffix.lower() in (".safetensors", ".ckpt"):
                        if f.name not in checkpoints:
                            checkpoints.append(f.name)
            except Exception as e:
                logger.debug(f"Failed to scan local Forge models dir: {e}")

        return checkpoints

    def refresh_checkpoints(self) -> bool:
        """
        Triggers POST /sdapi/v1/refresh-checkpoints to re-scan Forge models folder.
        """
        try:
            resp = requests.post(f"{self.base_url}/sdapi/v1/refresh-checkpoints", timeout=10.0)
            return resp.status_code == 200
        except Exception as e:
            logger.debug(f"Failed to refresh Forge checkpoints via API: {e}")
            return False

    @staticmethod
    def launch_forge_process_silently(launcher_path: Optional[Path] = None):
        """
        Safely launches Forge background process on Windows strictly without
        command prompt or PowerShell windows popping up (SW_HIDE + CREATE_NO_WINDOW).
        """
        import subprocess
        import sys
        from config import FORGE_LAUNCHER_PATH

        target = launcher_path or FORGE_LAUNCHER_PATH
        if not target.exists():
            logger.error(f"Forge launcher not found: {target}")
            return None

        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

        cmd = ["cmd.exe", "/c", str(target)]
        try:
            from config import FORGE_EXTRA_ARGS
            from services.art_director.vram_policy import resolve_forge_startup_flags

            startup_flags, startup_meta = resolve_forge_startup_flags(
                llm_active=False,
                extra_args=list(FORGE_EXTRA_ARGS) if FORGE_EXTRA_ARGS else None,
                launcher_path=target,
            )
            cmd.extend(startup_flags)
            ad_no_hf_val = startup_meta.get("ad_no_huggingface", False)
            logger.info(
                f"[ForgeClient] Стартовые флаги Forge: VRAM={startup_meta['total_vram_gb']:.1f}GB | "
                f"ad_no_huggingface={ad_no_hf_val} | "
                f"pin_shared_memory={startup_meta['pin_shared_memory']} ({startup_meta['pin_reason']})"
            )
            if not ad_no_hf_val:
                logger.info("[ForgeClient] 🌐 Флаг --ad-no-huggingface выключен (снят): автоскачивание моделей ADetailer с Hugging Face разрешено.")
        except Exception:
            pass

        return subprocess.Popen(
            cmd,
            cwd=str(target.parent),
            startupinfo=startupinfo,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=get_clean_forge_env()
        )

    def get_current_model(self) -> Optional[str]:
        """Queries currently loaded checkpoint model in Forge."""
        try:
            resp = requests.get(f"{self.base_url}/sdapi/v1/options", timeout=2.5)
            if resp.status_code == 200:
                return resp.json().get("sd_model_checkpoint")
        except Exception as e:
            logger.debug(f"Failed to query active Forge checkpoint: {e}")
        return None

    def _wait_for_vram_clear(self):
        """Ожидание физического освобождения VRAM драйвером WDDM после выгрузки LLM."""
        if hasattr(self, "_last_vram_wait_ts") and (time.time() - self._last_vram_wait_ts < 2.0):
            return

        logger.info("ForgeClient: ожидание освобождения адресного пространства VRAM драйвером...")
        monitor = getattr(self, "system_monitor", None)
        if not monitor and hasattr(self, "parent") and self.parent():
            monitor = getattr(self.parent(), "system_monitor", None)

        if monitor and hasattr(monitor, "get_free_vram_mb"):
            total_vram_mb = 8192
            try:
                if hasattr(monitor, "get_total_vram_mb"):
                    cand = monitor.get_total_vram_mb()
                    if isinstance(cand, (int, float)) and cand > 0:
                        total_vram_mb = int(cand)
                elif isinstance(getattr(monitor, "_last_emitted_stats", None), dict):
                    cand = monitor._last_emitted_stats.get("vram_total_mb")
                    if isinstance(cand, (int, float)) and cand > 0:
                        total_vram_mb = int(cand)
                else:
                    import pynvml
                    pynvml.nvmlInit()
                    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                    info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    total_vram_mb = int(info.total / (1024 * 1024))
            except Exception:
                pass
            target_free_mb = min(3800, max(512, int(total_vram_mb * 0.65)))

            start_t = time.time()
            # Динамическое ожидание (до 4.0 секунд с шагом 150 мс), пока free_mb >= target_free_mb
            free_mb = 0
            while time.time() - start_t < 4.0:
                free_mb = monitor.get_free_vram_mb()
                if free_mb >= target_free_mb:
                    logger.info(f"ForgeClient: VRAM свободна ({free_mb} MB >= {target_free_mb} MB), продолжение.")
                    self._last_vram_wait_ts = time.time()
                    return
                time.sleep(0.15)
            logger.info(f"ForgeClient: завершено динамическое ожидание VRAM (достигнут лимит 4.0 сек, доступно {free_mb} MB, порог {target_free_mb} MB).")
        else:
            time.sleep(1.2)

        self._last_vram_wait_ts = time.time()

    def set_model(self, checkpoint_title: str) -> bool:
        """Switches active checkpoint model in Forge, avoiding redundant reloads."""
        if not checkpoint_title:
            return False
        try:
            self._checkpoint_unloaded = False

            # Ищем точный title модели из списка /sdapi/v1/sd-models
            target_title = checkpoint_title
            models = self.get_models()
            search_str = checkpoint_title.lower().replace(".safetensors", "").replace(".ckpt", "")
            for m in models:
                m_title = m.get("title", "")
                m_name = m.get("model_name", "")
                m_fn = Path(m.get("filename", "")).name.lower()
                comb = f"{m_title.lower()} {m_name.lower()} {m_fn}"
                if search_str == "realistic" and "cyber" in comb:
                    continue
                if (search_str in m_title.lower() or 
                    search_str in m_name.lower() or 
                    search_str in m_fn):
                    target_title = m_title
                    break

            current = self.get_current_model()
            if current and (current == target_title or target_title in current):
                self._last_resolved_title = current
                self._last_requested_checkpoint = current
                logger.info(f"Forge checkpoint '{target_title}' is already active.")
                return True

            self._wait_for_vram_clear()

            # В пропатченном Forge 2.0 POST /options обновляет shared.opts и запускает
            # refresh_model_loading_parameters(). Сама модель загрузится в VRAM на следующем txt2img.
            from utils.config_manager import ConfigManager
            config = ConfigManager.load_config()
            sdxl_fp8_enabled = config.get("forge_sdxl_fp8_enabled", True)

            target_dtype = "fp8e4" if (is_sdxl_checkpoint(target_title) and sdxl_fp8_enabled) else "Auto"
            opt_payload: Dict[str, Any] = {
                "sd_model_checkpoint": target_title,
                "forge_unet_storage_dtype": target_dtype
            }

            # На системах <= 16.5 ГБ RAM предотвращаем кэширование старых моделей в системной памяти
            try:
                import psutil
                if (psutil.virtual_memory().total / (1024.0 ** 3)) <= 16.5:
                    opt_payload["sd_checkpoint_cache"] = 0
                    opt_payload["sd_checkpoints_limit"] = 1
                    opt_payload["sd_checkpoints_keep_in_cpu"] = False
                    self._system_options_ensured = True
            except Exception:
                pass

            fp8_note = "ВКЛ (fp8e4, экономия ~2.5ГБ VRAM)" if target_dtype == "fp8e4" else "ВЫКЛ (Auto/fp16)"
            logger.info(f"[ForgeClient] Чекпоинт '{target_title}' запрошен с forge_unet_storage_dtype='{target_dtype}' [Квантомания: {fp8_note}]")

            ok = self.set_options(opt_payload, timeout=30.0)
            if not ok:
                logger.error(f"Failed to set model {target_title} via /sdapi/v1/options")
                return False

            self._last_resolved_title = target_title
            self._last_requested_checkpoint = target_title
            self._last_requested_dtype = target_dtype
            logger.info(f"Forge checkpoint successfully queued: {target_title}")
            self._checkpoint_unloaded = False
            return True
        except Exception as e:
            logger.error(f"Failed to set model in Forge: {e}")
            return False

    set_active_checkpoint = set_model

    def set_options(self, options: Dict[str, Any], timeout: float = 10.0) -> bool:
        """Безопасная отправка настроек через POST /sdapi/v1/options."""
        try:
            resp = requests.post(
                f"{self.base_url}/sdapi/v1/options",
                json=options,
                timeout=timeout
            )
            if resp.status_code == 200:
                logger.info(f"[ForgeClient] Опции успешно применены: {list(options.keys())}")
                return True
            logger.warning(f"[ForgeClient] Не удалось применить опции {list(options.keys())}: HTTP {resp.status_code} - {resp.text[:100]}")
            return False
        except Exception as e:
            logger.debug(f"[ForgeClient] Ошибка вызова set_options: {e}")
            return False

    def ensure_system_options(self, force: bool = False) -> bool:
        """Гарантирует безопасные системные настройки Forge (кэш чекпоинтов в RAM) для систем с <= 16.5 ГБ RAM."""
        if not force and getattr(self, "_system_options_ensured", False):
            return True
        try:
            import psutil
            total_ram_gb = psutil.virtual_memory().total / (1024.0 ** 3)
            if total_ram_gb <= 16.5:
                ok = self.set_options({
                    "sd_checkpoint_cache": 0,
                    "sd_checkpoints_limit": 1,
                    "sd_checkpoints_keep_in_cpu": False,
                })
                if ok:
                    self._system_options_ensured = True
                return ok
        except Exception as e:
            logger.debug(f"[ForgeClient] ensure_system_options error: {e}")
        return True

    def unload_checkpoint(self) -> bool:
        """Unloads checkpoint from VRAM to RAM (~3.2GB) and purges PyTorch CUDA cache."""
        with self._unload_lock:
            if getattr(self, "_checkpoint_unloaded", False):
                return True
            try:
                resp = requests.post(f"{self.base_url}/sdapi/v1/unload-checkpoint", timeout=18.0)
                if resp.status_code == 200:
                    self.free_vram(force=True, unload_models=True)
                    self._checkpoint_unloaded = True
                    self._has_loaded_model_in_vram = False
                    used_vram_mb: Any = "?"
                    avail_ram_mb: Any = "?"
                    try:
                        from services.layer_budgeter import LayerBudgeter
                        import psutil
                        used_vram_mb = round(LayerBudgeter.get_current_used_vram_mb(), 1)
                        avail_ram_mb = round(psutil.virtual_memory().available / 1048576, 1)
                    except Exception:
                        pass
                    logger.info(f"Successfully unloaded Forge checkpoint (VRAM used: {used_vram_mb} MB | RAM free: {avail_ram_mb} MB).")
                    return True
                else:
                    logger.warning(f"Forge unload-checkpoint status {resp.status_code}: {resp.text[:100]}")
                    self.free_vram(force=True, unload_models=True)
                    self._checkpoint_unloaded = False
                    return False
            except requests.exceptions.ReadTimeout as e:
                logger.warning(
                    f"[ForgeClient] Таймаут (18.0с) ожидания /sdapi/v1/unload-checkpoint. "
                    f"Сервер Forge переносит веса в RAM. Пауза 1.0с перед аварийной очисткой VRAM: {e}"
                )
                time.sleep(1.0)
                self.free_vram(force=True, unload_models=True)
                self._checkpoint_unloaded = False
                return False
            except Exception as e:
                logger.debug(f"Failed to unload Forge checkpoint: {e}")
                self.free_vram(force=True, unload_models=True)
                self._checkpoint_unloaded = False
                return False

    def free_vram(self, always_offload: Optional[bool] = None, inference_memory_mb: Optional[int] = None, unload_models: bool = True, force: bool = False) -> bool:
        """Принудительно очищает PyTorch CUDA кэш и синхронизирует режим выгрузки с vram_policy."""
        if getattr(self, "_free_vram_supported", True) is False:
            return False

        if not force and unload_models and getattr(self, "_checkpoint_unloaded", False) and always_offload is None and inference_memory_mb is None:
            return True
        try:
            payload = {"unload_models": unload_models}
            if always_offload is not None:
                payload["always_offload"] = bool(always_offload)
            if inference_memory_mb is not None:
                payload["inference_memory_mb"] = int(inference_memory_mb)
            resp = requests.post(f"{self.base_url}/sdapi/v1/free-vram", json=payload, timeout=10.0)
            if resp.status_code == 404:
                self._free_vram_supported = False
                if not getattr(self, "_free_vram_warned", False):
                    self._free_vram_warned = True
                    logger.warning(
                        "[ForgeClient] Эндпоинт /sdapi/v1/free-vram недоступен (HTTP 404). "
                        "Обнаружен оригинальный Forge без патча NeonRoute. Выгрузка VRAM деградирует до базового режима."
                    )
                return False

            ok = resp.status_code == 200
            if ok and unload_models:
                self._has_loaded_model_in_vram = False
            return ok
        except Exception as e:
            logger.debug(f"Failed to call free-vram: {e}")
            return False

    def reload_checkpoint(self, checkpoint_name: Optional[str] = None) -> bool:
        """Перезагрузка чекпоинта с гарантированным ожиданием очистки VRAM после LLM."""
        self._wait_for_vram_clear()

        try:
            logger.info("Reloading Forge checkpoint into VRAM...")
            resp = requests.post(f"{self.base_url}/sdapi/v1/reload-checkpoint", timeout=30.0)
            if resp.status_code == 200:
                self._checkpoint_unloaded = False
                self._has_loaded_model_in_vram = True
                logger.info("Successfully reloaded Forge checkpoint.")
                if checkpoint_name:
                    return self.set_model(checkpoint_name)
                return True
            logger.warning(f"Forge reload-checkpoint returned status {resp.status_code}: {resp.text[:100]}")
            return False
        except Exception as e:
            logger.error(f"Failed to reload Forge checkpoint: {e}")
            return False

    def interrupt(self):
        """Immediately interrupts Forge generation on the server side."""
        self._is_aborted = True
        try:
            # Увеличен таймаут с 2.0 до 5.0с для безопасной обработки сигнала прерывания сервером Forge
            requests.post(f"{self.base_url}/sdapi/v1/interrupt", timeout=5.0)
            logger.info("Sent interrupt signal to Forge.")
        except Exception as e:
            logger.warning(f"Could not send interrupt to Forge: {e}")

        if self._current_session:
            try:
                self._current_session.close()
            except Exception:
                pass

    def _detect_adetailer_needed(self, prompt: str, target_model_name: str = "") -> bool:
        """
        Автоопределение необходимости ADetailer на основе промпта и модели:
        - Отключает для SDXL архитектуры (аппаратный ADetailer Guard на 8GB VRAM)
        - Отключает для динамических/интимных поз
        - Отключает для чистого 2D-аниме (MeinaHentai)
        - Отключает при отсутствии маркеров человека/персонажа (пейзажи, техника, интерьеры)
        """
        if is_sdxl_checkpoint(target_model_name):
            logger.info(f"[ForgeClient] ADetailer Guard: SDXL model '{target_model_name}' active. Optimal profile: inpaint 768px, conf 0.32/0.28.")
        if has_intimate_or_dynamic_triggers(prompt):
            logger.info("ADetailer automatically disabled for dynamic/intimate scene.")
            return False

        _is_anime = any(k in target_model_name for k in ("meina", "anime", "meinahentai"))
        if _is_anime:
            logger.info("ADetailer automatically disabled for clean 2D anime/MeinaHentai mode.")
            return False

        human_markers = [
            "1girl", "1boy", "girl", "boy", "man", "woman", "women", "female", "male",
            "person", "people", "portrait", "face", "character", "warrior", "watchmaker",
            "detective", "soldier", "knight", "queen", "king", "princess", "prince",
            "empress", "emperor", "lady", "lord", "elf", "maiden", "fairy", "sorceress",
            "witch", "hero", "inventor", "mechanic", "engineer", "noble", "goddess",
            "девушк", "парен", "мужчин", "женщин", "человек", "портрет", "лицо",
            "королев", "корол", "принцесс", "принц", "владычиц", "госпож",
            "часовщик", "эльф", "дева", "колдунь", "маг", "изобретател"
        ]
        has_human = any(re.search(rf"\b{re.escape(m)}", prompt.lower()) for m in human_markers)
        if not has_human:
            logger.info("ADetailer automatically disabled: no character/human markers detected in prompt.")
            return False

        return True

    def text_to_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        steps: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        cfg_scale: Optional[float] = None,
        sampler_name: Optional[str] = None,
        scheduler: Optional[str] = None,
        checkpoint: Optional[str] = None,
        seed: int = -1,
        clip_skip: int = 1,
        enable_hr: Optional[bool] = None,
        hr_scale: Optional[float] = None,
        hr_upscaler: Optional[str] = None,
        denoising_strength: float = 0.35,
        hr_second_pass_steps: int = 16,
        enable_adetailer: Optional[bool] = None,
        enable_hand_adetailer: bool = True,
        ad_denoising_strength: Optional[float] = None,
        ad_prompt: Optional[str] = None,
        ad_negative_prompt: Optional[str] = None,
        ad_mask_blur: Optional[int] = None,
        output_dir: Optional[Path | str] = None,
        session_id: Optional[str] = None,
        **kwargs
    ) -> Path:
        """
        Generates an image via Forge txt2img and saves it to C:/NeonRoute/outputs.
        Performs pre-flight check to verify server readiness before sending request.
        Uses WebUI reference parameters: DPM++ 2M Karras, 28 steps, CFG 6.8, base sanitary negative,
        calibrated Hires.fix (R-ESRGAN 4x+ / Latent, scale 1.30, denoising 0.35, 16 second-pass steps, timeout 180s),
        and optional ADetailer face recovery (face_yolov8n.pt, denoising 0.35, confidence 0.3)
        with automatic multi-tier fallback (strips ADetailer first, then falls back to enable_hr=False on memory errors).
        """
        self._is_aborted = False

        # Fast pre-flight check
        if not self.is_server_ready(timeout=1.0):
            err_msg = (
                "⚠️ SD WebUI Forge не запущен или еще загружается. "
                "Запустите Forge через кнопку в нижней панели и дождитесь зеленого индикатора."
            )
            logger.warning(err_msg)
            raise ForgeOfflineError(err_msg)

        # Switch checkpoint if specified or reload previously unloaded checkpoint
        model_synced_via_set = False
        if checkpoint:
            model_synced_via_set = bool(self.set_model(checkpoint))
        elif getattr(self, "_checkpoint_unloaded", False):
            self.reload_checkpoint()
        else:
            self._wait_for_vram_clear()
        self._checkpoint_unloaded = False

        if not model_synced_via_set:
            self.ensure_system_options()

        # Detect if model is MoP DMD SDXL
        target_model_name = (checkpoint or "").lower()
        if not target_model_name:
            cached_curr = getattr(self, "_last_resolved_title", None)
            if cached_curr:
                target_model_name = cached_curr.lower()
            elif self.is_server_ready(timeout=0.5):
                curr = self.get_current_model()
                if curr:
                    target_model_name = curr.lower()

        is_mop = any(k in target_model_name for k in ["mop", "pervert", "dmd"])

        # Санирование строк промптов: очистка от переносов строк, табуляций и лишних пробелов с сохранением тегов LoRA
        clean_prompt = " ".join((prompt or "").replace('\r', ' ').replace('\n', ' ').split())
        clean_negative = " ".join((negative_prompt or "").replace('\r', ' ').replace('\n', ' ').split())

        # Гарантируем кратность 64 и минимальный размер не менее 64x64
        target_w = int(width) if width else 512
        target_h = int(height) if height else 512
        clean_width = max(64, (target_w // 64) * 64)
        clean_height = max(64, (target_h // 64) * 64)

        # Resolve Hires.fix parameters (Dynamic Safe Check)
        is_hr_enabled = DEFAULT_SD_SETTINGS.get("enable_hr", True) if enable_hr is None else enable_hr
        scale_hr = DEFAULT_SD_SETTINGS.get("hr_scale", 1.30) if hr_scale is None else hr_scale
        steps_hr = DEFAULT_SD_SETTINGS.get("hr_second_pass_steps", 16) if hr_second_pass_steps is None else hr_second_pass_steps

        safe_upscaler, safe_denoise = self.resolve_safe_hires_settings(
            requested_upscaler=hr_upscaler or DEFAULT_SD_SETTINGS.get("hr_upscaler", "R-ESRGAN 4x+"),
            requested_denoising=denoising_strength if denoising_strength is not None else DEFAULT_SD_SETTINGS.get("denoising_strength", 0.35)
        )


        # Строгая валидация числовых типов параметров сэмплинга
        clean_seed = int(seed) if seed is not None else -1
        clean_steps = max(1, int(steps)) if steps is not None else 20
        clean_cfg = float(cfg_scale) if cfg_scale is not None else 7.0

        # Auto clip_skip=2 for merged/URPM realistic models trained with CLIP skip 2
        CLIP_SKIP2_MARKERS = ["urpm", "uberrealistic", "merge", "boudoir", "nsfw", "porn"]
        is_clip2_model = any(k in target_model_name for k in CLIP_SKIP2_MARKERS)
        if is_clip2_model and clip_skip == 1:
            clip_skip = 2
            logger.info(f"Auto clip_skip=2 applied for merged/URPM model: {target_model_name}")

        # 1, 2, 4. Full payload with WebUI reference parameters and safe Latent Hires.fix
        payload = {
            "prompt": clean_prompt,
            "negative_prompt": clean_negative,
            "steps": clean_steps,
            "width": clean_width,
            "height": clean_height,
            "cfg_scale": clean_cfg,
            "sampler_name": sampler_name or DEFAULT_SD_SETTINGS.get("sampler_name", "DPM++ 2M"),
            "scheduler": scheduler or DEFAULT_SD_SETTINGS.get("scheduler", "Karras"),
            "seed": clean_seed,
            "subseed_strength": 0,
            "restore_faces": False,
            "clip_skip": clip_skip,
            "send_images": True,
            "save_images": False,
            "enable_hr": is_hr_enabled,
            "hr_scale": scale_hr,
            "hr_upscaler": safe_upscaler,
            "denoising_strength": safe_denoise,
            "hr_second_pass_steps": steps_hr
        }

        override_dict = {
            "CLIP_stop_at_last_layers": clip_skip,
            "sd_vae": "Automatic"
        }

        # Защита VRAM: VAE Tiling исключает скачки памяти в Shared RAM при апскейле
        if FORGE_ENABLE_VAE_TILING:
            override_dict["forge_vae_tiling"] = True
            override_dict["sd_vae_tile"] = True
            payload["forge_vae_tiling"] = True
            payload["sd_vae_tile"] = True

        # ЖЕЛЕЗНЫЙ ХАРДЛОК ЧЕКПОИНТА: передаем модель прямо в override_settings только если set_model её ещё не синхронизировал
        target_model_title = target_model_name
        if checkpoint:
            # Используем уже разрешенный в set_model title без повторного сетевого опроса get_models()
            cached_title = getattr(self, "_last_resolved_title", None)
            if cached_title and checkpoint.lower().replace(".safetensors", "").replace(".ckpt", "") in cached_title.lower():
                target_model_title = cached_title
                models = []
            else:
                target_model_title = checkpoint
                models = self.get_models()
            search_str = checkpoint.lower().replace(".safetensors", "").replace(".ckpt", "")
            for m in models:
                m_title = m.get("title", "")
                m_name = m.get("model_name", "")
                m_fn = Path(m.get("filename", "")).name.lower()
                comb = f"{m_title.lower()} {m_name.lower()} {m_fn}"
                if search_str == "realistic" and "cyber" in comb:
                    continue
                if (search_str in m_title.lower() or 
                    search_str in m_name.lower() or 
                    search_str in m_fn):
                    target_model_title = m_title
                    break

        from utils.config_manager import ConfigManager
        config = ConfigManager.load_config()
        sdxl_fp8_enabled = config.get("forge_sdxl_fp8_enabled", True)
        active_target = target_model_title if checkpoint else target_model_name
        target_dtype = "fp8e4" if (is_sdxl_checkpoint(active_target) and sdxl_fp8_enabled) else "Auto"

        already_synced_via_set = bool(
            checkpoint
            and model_synced_via_set
            and getattr(self, "_last_requested_checkpoint", None) == target_model_title
            and getattr(self, "_last_requested_dtype", None) == target_dtype
        )
        if not already_synced_via_set:
            if checkpoint:
                override_dict["sd_model_checkpoint"] = target_model_title
            override_dict["forge_unet_storage_dtype"] = target_dtype
            fp8_note = "ВКЛ (fp8e4, экономия ~2.5ГБ VRAM)" if target_dtype == "fp8e4" else "ВЫКЛ (Auto/fp16)"
            logger.info(f"[ForgeClient] Чекпоинт '{active_target}' запрошен с forge_unet_storage_dtype='{target_dtype}' [Квантомания: {fp8_note}]")
            self._last_requested_checkpoint = target_model_title if checkpoint else getattr(self, "_last_requested_checkpoint", None)
            self._last_requested_dtype = target_dtype

        if override_dict:
            payload["override_settings"] = override_dict
            # Отключаем откат настроек, чтобы Forge не тратил VRAM на обратную загрузку старой модели
            payload["override_settings_restore_afterwards"] = False

        # 3. Сквозная передача alwayson_scripts (Zero-Logic ADetailer)
        width = clean_width
        height = clean_height
        steps = clean_steps
        cfg_scale = clean_cfg
        enable_hr = is_hr_enabled

        if "alwayson_scripts" in kwargs and kwargs["alwayson_scripts"]:
            payload["alwayson_scripts"] = copy.deepcopy(kwargs["alwayson_scripts"])

        has_ad = bool("alwayson_scripts" in payload and "ADetailer" in payload["alwayson_scripts"])

        ad_args = (
            payload["alwayson_scripts"]["ADetailer"].get("args", [])
            if has_ad and "alwayson_scripts" in payload and "ADetailer" in payload["alwayson_scripts"]
            else []
        )
        slot_dicts = [s for s in ad_args[2:] if isinstance(s, dict)]
        has_hands_ad = bool(kwargs.get("enable_hand_adetailer", False)) or len(slot_dicts) >= 2
        ad_inpaint_sz = max(
            [int(s.get("ad_inpaint_width", 512) or 512) for s in slot_dicts],
            default=512,
        )
        lora_count = len(re.findall(r"<lora:[^>]+>", clean_prompt, flags=re.IGNORECASE))

        try:
            from services.art_director.vram_policy import (
                resolve_forge_offload_policy,
                resolve_checkpoint_file_size_mb,
                format_vram_planner_log,
            )
            monitor = getattr(self, "system_monitor", None)
            if not monitor and hasattr(self, "parent") and self.parent():
                monitor = getattr(self.parent(), "system_monitor", None)

            forge_holds_vram = False
            try:
                from services.vram_arbiter import VRAMArbiter, GPUOwner
                arb = VRAMArbiter.get_instance()
                forge_holds_vram = bool(
                    arb._current_owner == GPUOwner.DIFFUSION
                    and not getattr(self, "_checkpoint_unloaded", False)
                    and getattr(self, "_has_loaded_model_in_vram", False)
                )
            except Exception:
                forge_holds_vram = bool(
                    not getattr(self, "_checkpoint_unloaded", False)
                    and getattr(self, "_has_loaded_model_in_vram", False)
                )

            ckpt_size_mb = resolve_checkpoint_file_size_mb(active_target)
            offload_pol = resolve_forge_offload_policy(
                width=clean_width,
                height=clean_height,
                architecture="sdxl" if is_sdxl_checkpoint(active_target) else "sd15",
                is_fp8=(target_dtype == "fp8e4"),
                enable_adetailer=has_ad,
                active_lora_count=lora_count,
                system_monitor=monitor,
                enable_hr=bool(is_hr_enabled),
                hr_scale=float(scale_hr or 1.0),
                hr_upscaler=str(safe_upscaler or "Latent"),
                enable_hand_adetailer=has_hands_ad,
                ad_inpaint_size=ad_inpaint_sz,
                vae_tiling=bool(FORGE_ENABLE_VAE_TILING),
                checkpoint_size_mb=ckpt_size_mb,
                forge_holds_vram=forge_holds_vram,
            )
            logger.info(format_vram_planner_log(offload_pol))

            want_offload = bool(offload_pol["always_offload"])
            target_inf_mb = int(offload_pol["inference_memory_mb"])
            target_mode = str(offload_pol.get("mode", "DEFAULT"))
            host_ram_starved = bool(offload_pol.get("host_ram_starved", False))
            policy_key = (want_offload, target_inf_mb)
            prev_key = getattr(self, "_last_vram_policy_key", None)
            prev_mode = getattr(self, "_last_vram_mode", None)

            # При первом входе в STAGE_OFFLOAD (включая prev_mode is None), при понижении режима
            # либо при дефиците системной RAM принудительно очищаем старые хвосты в VRAM (unload_models=True)
            entering_stage_offload = (
                target_mode == "STAGE_OFFLOAD"
                and (prev_mode != "STAGE_OFFLOAD" or host_ram_starved)
            )

            if prev_key != policy_key or entering_stage_offload:
                self._last_vram_policy_key = policy_key
                self._last_vram_mode = target_mode
                self._last_always_offload = want_offload
                self.free_vram(
                    always_offload=want_offload,
                    inference_memory_mb=target_inf_mb,
                    unload_models=entering_stage_offload,
                )

            # Host RAM Starvation Guard: принудительный даунгрейд в fp8e4, отключение Hires и вторых слотов ADetailer
            if offload_pol.get("force_fp8_storage") and is_sdxl_checkpoint(active_target):
                logger.warning(
                    f"[Host-RAM-Guard] Критический дефицит системной RAM + малый VRAM для '{active_target}': "
                    "принудительный даунгрейд forge_unet_storage_dtype='fp8e4'."
                )
                target_dtype = "fp8e4"
                if checkpoint:
                    override_dict["sd_model_checkpoint"] = target_model_title
                override_dict["forge_unet_storage_dtype"] = "fp8e4"
                payload["override_settings"] = override_dict
                payload["override_settings_restore_afterwards"] = False
                self._last_requested_dtype = "fp8e4"

            if offload_pol.get("force_disable_hr") and payload.get("enable_hr"):
                payload["enable_hr"] = False
                is_hr_enabled = False
                enable_hr = False

            if offload_pol.get("disable_secondary_adetailer") and has_ad and len(ad_args) > 3:
                payload["alwayson_scripts"]["ADetailer"]["args"] = list(ad_args[:3])
                ad_args = payload["alwayson_scripts"]["ADetailer"]["args"]

            want_vae_tiling = bool(FORGE_ENABLE_VAE_TILING or offload_pol.get("enable_vae_tiling", False))
            if want_vae_tiling:
                override_dict["forge_vae_tiling"] = True
                override_dict["sd_vae_tile"] = True
                payload["forge_vae_tiling"] = True
                payload["sd_vae_tile"] = True
                payload["override_settings"] = override_dict
            if want_vae_tiling or getattr(self, "_never_oom_vae_active", False):
                if self.has_script("Never OOM Integrated", tab="txt2img"):
                    scripts_map = payload.setdefault("alwayson_scripts", {})
                    if "Never OOM Integrated" not in scripts_map:
                        scripts_map["Never OOM Integrated"] = {"args": [False, want_vae_tiling]}
                        self._never_oom_vae_active = want_vae_tiling
        except Exception as _pol_err:
            offload_pol = {"mode": "DEFAULT", "always_offload": False}
            logger.debug(f"[ForgeClient] Ошибка применения vram_policy: {_pol_err}")

        if has_ad and ad_args:
            # ad_args: [ad_enable, ad_skip_img2img, face_slot, hand_slot, ...]
            slots_summary = []
            for idx, slot in enumerate(ad_args[2:], start=1):
                if isinstance(slot, dict):
                    m = slot.get("ad_model", "none")
                    if m == "None":
                        slots_summary.append(f"Slot{idx}(SKIPPED)")
                    else:
                        d = slot.get("ad_denoising_strength", 0.0)
                        sz = slot.get("ad_inpaint_width", 0)
                        st = slot.get("ad_steps", "auto")
                        slots_summary.append(f"Slot{idx}({m}: d={d}, steps={st}, {sz}px)")
            if slots_summary:
                logger.info(f"[ForgeClient.ADetailer] Активные слоты в payload: {' | '.join(slots_summary)}")

        logger.info(
            f"[SD Forge] Старт txt2img: {clean_width}x{clean_height}, шаги={clean_steps}, CFG={clean_cfg}, "
            f"Hires.fix={enable_hr}, ADetailer={has_ad}, VRAMMode={offload_pol.get('mode')}"
        )

        stop_poller = threading.Event()
        watchdog_triggered = threading.Event()

        def _poll_progress_loop():
            last_stage = ""
            last_pct = -1
            base_pass_finished = False
            last_step = 0
            last_activity_time = time.time()
            # Расширен таймаут с 40.0 до 90.0с для безопасного завершения Hires.fix и ADetailer
            stagnation_timeout = 90.0

            while not stop_poller.wait(0.35):
                if self._is_aborted:
                    break
                try:
                    resp = requests.get(
                        f"{self.base_url}/sdapi/v1/progress",
                        params={"skip_current_image": "true"},
                        timeout=2.0
                    )
                    if resp.status_code != 200:
                        if time.time() - last_activity_time > stagnation_timeout:
                            logger.error(f"[Forge Watchdog] Ядро Forge не отвечает более {stagnation_timeout}с. Принудительный сброс сокета.")
                            watchdog_triggered.set()
                            self.interrupt()
                            break
                        continue

                    data = resp.json()
                    parsed, base_pass_finished = self.parse_progress_payload(
                        data,
                        is_hr_enabled=is_hr_enabled,
                        steps_hr=steps_hr,
                        last_step=last_step,
                        base_pass_finished=base_pass_finished,
                    )
                    textinfo = parsed["textinfo"].lower()
                    step = parsed["step"]
                    total_steps = parsed["total_steps"]
                    progress_float = parsed["progress"]

                    # Сбрасываем таймер застоя при смене шага, прогрессе или смене внутреннего состояния
                    if step != last_step or progress_float > 0 or textinfo:
                        last_activity_time = time.time()

                    # Сторожевой контроль зависания шагов (deadlock в CUDA)
                    if total_steps > 0 and (time.time() - last_activity_time > stagnation_timeout):
                        logger.error(f"[Forge Watchdog] Генерация застряла на шаге {step}/{total_steps} (таймаут {stagnation_timeout}с).")
                        watchdog_triggered.set()
                        self.interrupt()
                        break

                    stage_name = parsed["stage_name"] or "🎨 Создание общего плана"
                    last_step = step
                    pct = parsed["percent"]

                    if stage_name != last_stage or abs(pct - last_pct) >= 2:
                        last_stage = stage_name
                        last_pct = pct
                        self.generation_progress.emit(stage_name, pct)

                except Exception:
                    if time.time() - last_activity_time > stagnation_timeout:
                        logger.error(f"[Forge Watchdog] Потеря связи с сервером Forge во время генерации ({stagnation_timeout}с).")
                        watchdog_triggered.set()
                        self.interrupt()
                        break

        poller_thread = threading.Thread(target=_poll_progress_loop, daemon=True)
        poller_thread.start()

        self._current_session = requests.Session()
        self._checkpoint_unloaded = False
        try:
            current_payload = copy.deepcopy(payload)

            def _send_request(p: dict) -> Optional[requests.Response]:
                self._checkpoint_unloaded = False
                t0 = time.time()
                resp = self._current_session.post(
                    f"{self.base_url}/sdapi/v1/txt2img",
                    json=copy.deepcopy(p),
                    timeout=120
                )
                elapsed = time.time() - t0
                if resp.status_code == 200:
                    self._has_loaded_model_in_vram = True
                    logger.info(f"[SD Forge] Генерация успешно завершена за {elapsed:.2f} с (HTTP 200)")
                else:
                    logger.warning(f"[SD Forge] Ответ API HTTP {resp.status_code} за {elapsed:.2f} с: {resp.text[:120]}")
                return resp

            resp = None
            last_err = None

            # Attempt 1: Full request (with ADetailer and Hires.fix if requested)
            try:
                resp = _send_request(current_payload)
            except requests.exceptions.ConnectionError:
                raise
            except Exception as req_err:
                last_err = req_err
                logger.warning(f"Forge txt2img initial attempt failed: {req_err}")

            if watchdog_triggered.is_set():
                raise TimeoutError("Ядро Forge зависло или превысило лимит ожидания шага (Watchdog reset).")

            if self._is_aborted:
                raise InterruptedError("Генерация изображения была отменена пользователем.")

            # Fallback 1: If failed and ADetailer is present, retry without ADetailer while keeping Hires.fix enabled
            has_ad_in_payload = "ADetailer" in (current_payload.get("alwayson_scripts") or {})
            if (resp is None or resp.status_code != 200) and has_ad_in_payload:
                status_info = f"HTTP {resp.status_code}" if resp is not None else str(last_err)
                logger.warning(
                    f"Forge txt2img with ADetailer failed ({status_info}). "
                    "Retrying generation without ADetailer while keeping Hires.fix enabled..."
                )
                scripts_dict = current_payload.get("alwayson_scripts")
                if isinstance(scripts_dict, dict):
                    scripts_dict.pop("ADetailer", None)
                    if not scripts_dict:
                        current_payload.pop("alwayson_scripts", None)
                try:
                    resp = _send_request(current_payload)
                    last_err = None
                except requests.exceptions.ConnectionError:
                    raise
                except Exception as ad_err:
                    last_err = ad_err
                    logger.warning(f"Forge retry without ADetailer failed: {ad_err}")
                    resp = None

            if watchdog_triggered.is_set():
                raise TimeoutError("Ядро Forge зависло или превысило лимит ожидания шага (Watchdog reset).")

            if self._is_aborted:
                raise InterruptedError("Генерация изображения была отменена пользователем.")

            # Fallback 2: Only as extreme emergency fallback if retry with upscale failed due to memory error
            if (resp is None or resp.status_code != 200) and current_payload.get("enable_hr"):
                err_text = resp.text if resp is not None else str(last_err)
                is_oom = any(kw in err_text.lower() for kw in [
                    "out of memory", "outofmemory", "cuda out of memory", "cuda error", "memory", "allocation on device"
                ])
                status_info = f"HTTP {resp.status_code}" if resp is not None else str(last_err)
                logger.warning(
                    f"Forge txt2img with Hires.fix failed ({status_info}, memory_error={is_oom}). "
                    "Emergency last-resort fallback: retrying generation with enable_hr=False..."
                )
                if is_oom:
                    self.free_vram(always_offload=True, inference_memory_mb=1024, unload_models=True)
                current_payload["enable_hr"] = False
                current_payload.pop("alwayson_scripts", None)
                try:
                    resp = _send_request(current_payload)
                    last_err = None
                except requests.exceptions.ConnectionError:
                    raise
                except Exception as hr_err:
                    logger.error(f"Fallback txt2img without Hires.fix failed: {hr_err}")
                    raise

            if watchdog_triggered.is_set():
                raise TimeoutError("Ядро Forge зависло или превысило лимит ожидания шага (Watchdog reset).")

            if self._is_aborted:
                raise InterruptedError("Генерация изображения была отменена пользователем.")

            if resp is None:
                if last_err:
                    raise last_err
                raise RuntimeError("Не удалось получить ответ от Forge API.")

            if resp.status_code != 200:
                raise RuntimeError(f"Forge API error {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            images = data.get("images", [])

            # Fallback if empty images with Hires
            if not images and current_payload.get("enable_hr"):
                logger.warning("Forge returned empty images list with Hires.fix. Retrying without Hires.fix...")
                current_payload["enable_hr"] = False
                current_payload.pop("alwayson_scripts", None)
                resp = _send_request(current_payload)
                if resp.status_code == 200:
                    data = resp.json()
                    images = data.get("images", [])

            if not images:
                raise ValueError("Forge вернул пустой список изображений.")

            # Store last generation metadata (seed, parameters, info)
            info_raw = data.get("info", "")
            if isinstance(info_raw, str):
                try:
                    self.last_generation_info = json.loads(info_raw)
                except Exception:
                    self.last_generation_info = {}
            elif isinstance(info_raw, dict):
                self.last_generation_info = info_raw
            else:
                self.last_generation_info = {}

            if "seed" not in self.last_generation_info or self.last_generation_info.get("seed") in (None, -1):
                if clean_seed != -1:
                    self.last_generation_info["seed"] = clean_seed

            actual_seed = self.last_generation_info.get("seed", clean_seed)
            logger.info(f"[SD Forge] Фактический Seed инференса: {actual_seed}")

            # Direct extraction of original uncompressed PNG from images[0]
            def _decode_png(raw_b64_str: str) -> bytes:
                if "," in raw_b64_str:
                    raw_b64_str = raw_b64_str.split(",", 1)[1]
                b = base64.b64decode(raw_b64_str)
                if len(b) < 100:
                    raise ValueError("Получены поврежденные или неполные байты изображения от Forge.")
                return b

            def _measure_dims(b: bytes, fallback_w: int, fallback_h: int) -> Tuple[int, int]:
                if len(b) >= 24 and b.startswith(b"\x89PNG\r\n\x1a\n"):
                    try:
                        import struct
                        return struct.unpack(">II", b[16:24])
                    except Exception:
                        pass
                try:
                    import io
                    from PIL import Image
                    with Image.open(io.BytesIO(b)) as img:
                        return img.size
                except Exception:
                    if is_hr_enabled and scale_hr:
                        return (int(clean_width * scale_hr), int(clean_height * scale_hr))
                    return (fallback_w, fallback_h)

            image_bytes = _decode_png(images[0])
            actual_w, actual_h = _measure_dims(image_bytes, clean_width, clean_height)

            # Детектор пустого/чёрного кадра (возможное NaN-переполнение в VAE) ДО сохранения на диск
            if len(image_bytes) < 10_000 and actual_w >= 512 and actual_h >= 512:
                logger.warning(
                    f"[ForgeClient] Обнаружен пустой/чёрный кадр (возможное NaN-переполнение в VAE): "
                    f"размер файла всего {len(image_bytes)} байт при разрешении {actual_w}x{actual_h}. "
                    "Выполняется автоматический восстановительный ретрай (seed=-1, enable_hr=False)..."
                )
                try:
                    self.free_vram(always_offload=False, inference_memory_mb=768)
                except Exception:
                    pass

                retry_payload = copy.deepcopy(current_payload)
                retry_payload["seed"] = -1
                if retry_payload.get("enable_hr"):
                    retry_payload["enable_hr"] = False
                elif "denoising_strength" in retry_payload:
                    retry_payload["denoising_strength"] = 0.32

                resp_retry = _send_request(retry_payload)
                if resp_retry is not None and resp_retry.status_code == 200:
                    retry_data = resp_retry.json()
                    retry_images = retry_data.get("images", [])
                    if retry_images:
                        retry_bytes = _decode_png(retry_images[0])
                        retry_w, retry_h = _measure_dims(retry_bytes, clean_width, clean_height)
                        if len(retry_bytes) < 10_000 and retry_w >= 512 and retry_h >= 512:
                            raise RuntimeError(
                                f"Forge вернул пустой/чёрный кадр (NaN в VAE) даже после восстановительного ретрая "
                                f"({len(retry_bytes)} байт, {retry_w}x{retry_h})."
                            )
                        # Восстановительный ретрай успешен
                        image_bytes = retry_bytes
                        actual_w, actual_h = retry_w, retry_h
                        data = retry_data
                        info_raw = data.get("info", "")
                        logger.info(
                            f"[ForgeClient] Восстановительный ретрай успешно восстановил кадр "
                            f"({len(image_bytes)} байт, {actual_w}x{actual_h})."
                        )
                    else:
                        raise RuntimeError("Восстановительный ретрай Forge вернул пустой список изображений.")
                else:
                    status_c = resp_retry.status_code if resp_retry is not None else "нет ответа"
                    raise RuntimeError(f"Сбой восстановительного ретрая Forge: {status_c}")

            # Save 100% original full-size image to session or global outputs directory
            if output_dir:
                save_dir = Path(output_dir)
            elif session_id:
                save_dir = OUTPUTS_DIR / "sessions" / str(session_id) / "images"
            else:
                save_dir = OUTPUTS_DIR

            save_dir.mkdir(parents=True, exist_ok=True)
            filename = f"gen_{int(time.time())}_{os.urandom(3).hex()}.png"
            file_path = save_dir / filename
            with open(file_path, "wb") as f:
                f.write(image_bytes)

            logger.info(f"Successfully saved original full-resolution PNG to {file_path} ({len(image_bytes)} bytes, {actual_w}x{actual_h})")
            return file_path

        except requests.exceptions.ConnectionError:
            err_msg = (
                "⚠️ SD WebUI Forge не запущен или еще загружается. "
                "Запустите Forge через кнопку в нижней панели и дождитесь зеленого индикатора."
            )
            logger.warning(err_msg)
            raise ForgeOfflineError(err_msg)
        finally:
            stop_poller.set()
            if poller_thread.is_alive():
                poller_thread.join(timeout=1.5)
            if self._current_session:
                self._current_session.close()
                self._current_session = None

            # Гарантированная выгрузка чекпоинта в системную RAM и очистка CUDA-кэша под LLM
            try:
                unloaded = self.unload_checkpoint()
                if unloaded:
                    logger.info("[ForgeClient] VRAM успешно зачищена: чекпоинт сброшен в RAM, память свободна под LLM.")
                else:
                    logger.warning("[ForgeClient] Выгрузка чекпоинта Forge завершилась с ошибкой, выполнена аварийная очистка кэша.")
            except Exception as _e_clean:
                logger.debug(f"[ForgeClient] Не удалось выполнить автозачистку VRAM: {_e_clean}")

    # Alias txt2img for text_to_image
    txt2img = text_to_image