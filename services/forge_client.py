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

    def __init__(self, base_url: str = "http://127.0.0.1:7860", parent=None, system_monitor=None):
        super().__init__(parent)
        self.base_url = base_url.rstrip("/")
        self.system_monitor = system_monitor
        self._last_vram_wait_ts: float = 0.0
        self._current_session: Optional[requests.Session] = None
        self._is_aborted = False
        self._checkpoint_unloaded = False
        self._checkpoint_cache_size: int = 0
        self._cached_upscalers: Optional[List[str]] = None
        self._cached_embeddings: Optional[List[str]] = None
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
        Queries GET http://127.0.0.1:7860/sdapi/v1/sd-models (timeout=3.0s).
        Extracts model names (model_name, title, or filename), and supplements
        with local file discovery in C:\\neyroGPD\\...\\models\\Stable-diffusion.
        """
        checkpoints: List[str] = []
        try:
            resp = requests.get(f"{self.base_url}/sdapi/v1/sd-models", timeout=3.0)
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

        # Local directory fallback scan (discovers files even if Forge is offline or still booting)
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
            if FORGE_EXTRA_ARGS:
                # Дедупликация аргументов: предотвращаем дублирование параметров, если bat-файл уже содержит флаги
                seen_args = set()
                clean_extra = []
                for a in FORGE_EXTRA_ARGS:
                    if a not in seen_args:
                        seen_args.add(a)
                        clean_extra.append(a)
                cmd.extend(clean_extra)
        except Exception:
            pass

        return subprocess.Popen(
            cmd,
            cwd=str(target.parent),
            startupinfo=startupinfo,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
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
            start_t = time.time()
            # Динамическое ожидание (до 4.0 секунд с шагом 150 мс), пока system_monitor.get_free_vram_mb() >= 3800
            free_mb = 0
            while time.time() - start_t < 4.0:
                free_mb = monitor.get_free_vram_mb()
                if free_mb >= 3800:
                    logger.info(f"ForgeClient: VRAM свободна ({free_mb} MB >= 3800 MB), продолжение.")
                    self._last_vram_wait_ts = time.time()
                    return
                time.sleep(0.15)
            logger.info(f"ForgeClient: завершено динамическое ожидание VRAM (достигнут лимит 4.0 сек, доступно {free_mb} MB).")
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
                logger.info(f"Forge checkpoint '{target_title}' is already active.")
                return True

            self._wait_for_vram_clear()

            # В пропатченном Forge 2.0 POST /options обновляет shared.opts и запускает
            # refresh_model_loading_parameters(). Сама модель загрузится в VRAM на следующем txt2img.
            from utils.config_manager import ConfigManager
            config = ConfigManager.load_config()
            sdxl_fp8_enabled = config.get("forge_sdxl_fp8_enabled", True)

            target_dtype = "fp8e4" if (is_sdxl_checkpoint(target_title) and sdxl_fp8_enabled) else "Auto"
            opt_payload = {
                "sd_model_checkpoint": target_title,
                "forge_unet_storage_dtype": target_dtype
            }
            logger.info(f"[ForgeClient] Чекпоинт '{target_title}' запрошен с forge_unet_storage_dtype='{target_dtype}'")

            resp_opt = requests.post(
                f"{self.base_url}/sdapi/v1/options",
                json=opt_payload,
                timeout=30.0
            )
            if resp_opt.status_code != 200:
                logger.error(f"Failed to set model {target_title}: HTTP {resp_opt.status_code} - {resp_opt.text}")
                return False

            logger.info(f"Forge checkpoint successfully queued: {target_title}")
            self._checkpoint_unloaded = False
            return True
        except Exception as e:
            logger.error(f"Failed to set model in Forge: {e}")
            return False

    set_active_checkpoint = set_model

    def unload_checkpoint(self) -> bool:
        """Unloads checkpoint from VRAM to RAM (~3.2GB) and purges PyTorch CUDA cache."""
        if getattr(self, "_checkpoint_unloaded", False):
            return True
        try:
            resp = requests.post(f"{self.base_url}/sdapi/v1/unload-checkpoint", timeout=5.0)
            if resp.status_code == 200:
                logger.info("Successfully unloaded Forge checkpoint to free VRAM (~3.2GB).")
            else:
                logger.warning(f"Forge unload-checkpoint status {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            logger.debug(f"Failed to unload Forge checkpoint: {e}")

        self.free_vram()
        self._checkpoint_unloaded = True
        return True

    def free_vram(self, always_offload: Optional[bool] = None, inference_memory_mb: Optional[int] = None, unload_models: bool = True) -> bool:
        """Принудительно очищает PyTorch CUDA кэш и синхронизирует режим выгрузки с vram_policy."""
        if unload_models and getattr(self, "_checkpoint_unloaded", False) and always_offload is None and inference_memory_mb is None:
            return True
        try:
            payload = {"unload_models": unload_models}
            if always_offload is not None:
                payload["always_offload"] = bool(always_offload)
            if inference_memory_mb is not None:
                payload["inference_memory_mb"] = int(inference_memory_mb)
            resp = requests.post(f"{self.base_url}/sdapi/v1/free-vram", json=payload, timeout=5.0)
            return resp.status_code == 200
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

        self._checkpoint_unloaded = False
        # Switch checkpoint if specified
        if checkpoint:
            self.set_model(checkpoint)
        elif getattr(self, "_checkpoint_unloaded", False):
            self.reload_checkpoint()
        else:
            self._wait_for_vram_clear()

        # Detect if model is MoP DMD SDXL
        target_model_name = (checkpoint or "").lower()
        if not target_model_name and self.is_server_ready(timeout=0.5):
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

        # ЖЕЛЕЗНЫЙ ХАРДЛОК ЧЕКПОИНТА: передаем модель прямо в override_settings запроса
        if checkpoint:
            # Ищем точный title модели из списка Forge
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
            override_dict["sd_model_checkpoint"] = target_model_title

        from utils.config_manager import ConfigManager
        config = ConfigManager.load_config()
        sdxl_fp8_enabled = config.get("forge_sdxl_fp8_enabled", True)
        active_target = target_model_title if checkpoint else target_model_name
        target_dtype = "fp8e4" if (is_sdxl_checkpoint(active_target) and sdxl_fp8_enabled) else "Auto"
        override_dict["forge_unet_storage_dtype"] = target_dtype
        logger.info(f"[ForgeClient] Чекпоинт '{active_target}' запрошен с forge_unet_storage_dtype='{target_dtype}'")

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

        try:
            from services.art_director.vram_policy import resolve_forge_offload_policy
            monitor = getattr(self, "system_monitor", None)
            if not monitor and hasattr(self, "parent") and self.parent():
                monitor = getattr(self.parent(), "system_monitor", None)
            offload_pol = resolve_forge_offload_policy(
                width=clean_width,
                height=clean_height,
                architecture="sdxl" if is_sdxl_checkpoint(active_target) else "sd15",
                is_fp8=(target_dtype == "fp8e4"),
                enable_adetailer=has_ad,
                system_monitor=monitor,
            )
            self.free_vram(
                always_offload=offload_pol["always_offload"],
                inference_memory_mb=offload_pol["inference_memory_mb"],
                unload_models=False,
            )
        except Exception as _pol_err:
            offload_pol = {"mode": "DEFAULT", "always_offload": False}
            logger.debug(f"[ForgeClient] Ошибка применения vram_policy: {_pol_err}")

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
                    state = data.get("state", {})
                    textinfo = str(state.get("textinfo") or "").lower()
                    step = state.get("sampling_step", 0)
                    total_steps = state.get("sampling_steps", 0)
                    progress_float = data.get("progress", 0.0)

                    # Сбрасываем таймер застоя при смене шага, прогрессе или смене внутреннего состояния
                    if step != last_step or progress_float > 0 or textinfo:
                        last_activity_time = time.time()

                    # Сторожевой контроль зависания шагов (deadlock в CUDA)
                    if total_steps > 0 and (time.time() - last_activity_time > stagnation_timeout):
                        logger.error(f"[Forge Watchdog] Генерация застряла на шаге {step}/{total_steps} (таймаут {stagnation_timeout}с).")
                        watchdog_triggered.set()
                        self.interrupt()
                        break

                    stage_name = None

                    # 1. Проверка специализированных улучшаторов ADetailer
                    for keyword, title in STAGE_DETECTORS:
                        if keyword in textinfo:
                            stage_name = title
                            break

                    # 2. Если улучшаторы не активны — базовые проходы генерации
                    if not stage_name:
                        if step < last_step and last_step > 0:
                            base_pass_finished = True

                        if base_pass_finished or (is_hr_enabled and step > 0 and total_steps == steps_hr):
                            stage_name = "🔍 Повышение четкости"
                        else:
                            stage_name = "🎨 Создание общего плана"

                    last_step = step

                    if total_steps > 0:
                        pct = int((step / total_steps) * 100)
                    else:
                        pct = int(progress_float * 100)

                    pct = max(0, min(100, pct))

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
                    json=p,
                    timeout=120
                )
                elapsed = time.time() - t0
                if resp.status_code == 200:
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
            has_ad = "alwayson_scripts" in current_payload and bool(current_payload.get("alwayson_scripts"))
            if (resp is None or resp.status_code != 200) and has_ad:
                status_info = f"HTTP {resp.status_code}" if resp is not None else str(last_err)
                logger.warning(
                    f"Forge txt2img with ADetailer failed ({status_info}). "
                    "Retrying generation without ADetailer while keeping Hires.fix enabled..."
                )
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
            raw_b64 = images[0]
            if "," in raw_b64:
                raw_b64 = raw_b64.split(",", 1)[1]

            image_bytes = base64.b64decode(raw_b64)
            if len(image_bytes) < 100:
                raise ValueError("Получены поврежденные или неполные байты изображения от Forge.")

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

            actual_w, actual_h = clean_width, clean_height
            try:
                import io
                from PIL import Image
                with Image.open(io.BytesIO(image_bytes)) as img:
                    actual_w, actual_h = img.size
            except Exception:
                if is_hr_enabled and scale_hr:
                    actual_w = int(clean_width * scale_hr)
                    actual_h = int(clean_height * scale_hr)

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
            poller_thread.join(timeout=0.8)
            if self._current_session:
                self._current_session.close()
                self._current_session = None

            # Гарантированная выгрузка чекпоинта в системную RAM и очистка CUDA-кэша под LLM
            try:
                self.unload_checkpoint()
                logger.info("[ForgeClient] VRAM успешно зачищена: чекпоинт сброшен в RAM, память свободна под LLM.")
            except Exception as _e_clean:
                logger.debug(f"[ForgeClient] Не удалось выполнить автозачистку VRAM: {_e_clean}")

    # Alias txt2img for text_to_image
    txt2img = text_to_image