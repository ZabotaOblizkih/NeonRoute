"""
Civitai Model Metadata Service for NeonRoute.
Provides on-demand lookup of model generation parameters via SHA256 hash with offline fallback.
"""

import os
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, Optional

import requests

from config import FORGE_ROOT_DIR

logger = logging.getLogger("NeonRoute.CivitaiService")

CIVITAI_API_URL = "https://civitai.com/api/v1/model-versions/by-hash/{hash}"
FORGE_SD_MODELS_DIR = FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui" / "models" / "Stable-diffusion"

# Базовые безопасные параметры по умолчанию (Safe Defaults)
DEFAULT_PIPELINE_PARAMS: Dict[str, Any] = {
    "sampler": "DPM++ 2M Karras",
    "steps": 25,
    "cfg_scale": 6.0,
    "clip_skip": 1,
    "hires_upscaler": "4x-UltraSharp",
    "hires_denoise": 0.32,
    "hires_scale": 1.25,
    "negative_prompt": "worst quality, low quality, bad anatomy, bad proportions, deformed, badhandv4"
}


def compute_model_sha256(file_path: Path, max_mb: Optional[int] = None) -> Optional[str]:
    """
    Быстрое хеширование первых max_mb мегабайт файла для мгновенной сверки,
    либо полный расчет AutoV2 SHA256 для точного совпадения с базой Civitai.
    """
    if not file_path.exists():
        return None
    try:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            if max_mb and max_mb > 0:
                # Читаем блоками по 1MB
                for _ in range(max_mb):
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    sha256.update(chunk)
            else:
                while True:
                    chunk = f.read(1024 * 1024 * 4)
                    if not chunk:
                        break
                    sha256.update(chunk)
        return sha256.hexdigest().upper()
    except Exception as e:
        logger.error(f"[CivitaiService] Ошибка хеширования файла {file_path.name}: {e}")
        return None


def fetch_civitai_parameters(checkpoint_name: str) -> Dict[str, Any]:
    """
    Выполняет строго единичный запрос к Civitai по хешу файла с таймаутом 4.0 сек.
    Возвращает словарь с параметрами. При любых сетевых сбоях возвращает Safe Defaults.
    """
    result = dict(DEFAULT_PIPELINE_PARAMS)
    checkpoint_file = Path(checkpoint_name)
    if not checkpoint_file.is_absolute() or not checkpoint_file.exists():
        checkpoint_file = FORGE_SD_MODELS_DIR / checkpoint_name

    # 1. Проверка существования файла
    if not checkpoint_file.exists():
        # Поиск в подпапках
        matches = list(FORGE_SD_MODELS_DIR.rglob(Path(checkpoint_name).name))
        if matches:
            checkpoint_file = matches[0]
        else:
            logger.warning(f"[CivitaiService] Файл модели '{checkpoint_name}' не найден на диске. Применены настройки по умолчанию.")
            result["status"] = "file_not_found"
            result["message"] = "Файл модели не найден на диске. Выставлены параметры по умолчанию."
            return result

    # 2. Вычисление хеша (полный AutoV2 SHA256 для точного распознавания на Civitai)
    logger.info(f"[CivitaiService] Хеширование модели {checkpoint_name}...")
    model_hash = compute_model_sha256(checkpoint_file, max_mb=None)
    if not model_hash:
        result["status"] = "hash_failed"
        result["message"] = "Не удалось вычислить хеш. Выставлены параметры по умолчанию."
        return result

    # 3. Сетевой запрос с жестким таймаутом 4.0 секунды
    url = CIVITAI_API_URL.format(hash=model_hash)
    try:
        logger.info(f"[CivitaiService] Запрос метаданных Civitai для {checkpoint_name} (хэш: {model_hash[:10]}...)...")
        resp = requests.get(url, timeout=4.0)

        if resp.status_code == 200:
            data = resp.json()
            # Извлекаем параметры автора модели (приоритет элементу, где есть steps или cfgScale)
            images = data.get("images", [])
            meta = {}
            for img in images:
                if "meta" in img and isinstance(img["meta"], dict):
                    m = img["meta"]
                    if "steps" in m or "cfgScale" in m:
                        meta = m
                        break
                    elif not meta:
                        meta = m

            if meta:
                if "sampler" in meta and meta["sampler"]:
                    result["sampler"] = meta["sampler"]
                if "steps" in meta and meta["steps"] is not None:
                    try:
                        result["steps"] = int(meta["steps"])
                    except (ValueError, TypeError):
                        pass
                if "cfgScale" in meta and meta["cfgScale"] is not None:
                    try:
                        result["cfg_scale"] = float(meta["cfgScale"])
                    except (ValueError, TypeError):
                        pass
                if "clipSkip" in meta and meta["clipSkip"] is not None:
                    try:
                        result["clip_skip"] = int(meta["clipSkip"])
                    except (ValueError, TypeError):
                        pass
                if "negativePrompt" in meta and meta["negativePrompt"]:
                    result["negative_prompt"] = str(meta["negativePrompt"]).strip(' "')
                if "Hires upscaler" in meta and meta["Hires upscaler"]:
                    result["hires_upscaler"] = str(meta["Hires upscaler"]).strip(' "')
                if "Denoising strength" in meta and meta["Denoising strength"] is not None:
                    try:
                        result["hires_denoise"] = float(meta["Denoising strength"])
                    except (ValueError, TypeError):
                        pass

            # Проверка типа модели (если Anime -> меняем дефолты)
            model_tags = [str(t).lower() for t in ((data.get("model") or {}).get("tags") or []) if t]
            name_lower = data.get("name", "").lower() + " " + checkpoint_name.lower()
            is_anime = any(w in name_lower or w in model_tags for w in ["anime", "hentai", "manga", "2d"])
            if is_anime:
                result["hires_upscaler"] = "R-ESRGAN 4x+ Anime6B"
                result["clip_skip"] = 2
                if not meta.get("negativePrompt"):
                    result["negative_prompt"] = "worst quality, low quality, bad anatomy, easynegative"

            result["status"] = "success"
            result["message"] = f"Успешно подтянуты параметры Civitai (версия: {data.get('name', 'OK')})"
            logger.info(f"[CivitaiService] Успешно получены параметры для '{checkpoint_name}': steps={result['steps']}, cfg={result['cfg_scale']}, sampler={result['sampler']}")
            return result

        elif resp.status_code == 404:
            logger.warning(f"[CivitaiService] Модель '{checkpoint_name}' не найдена в базе Civitai (HTTP 404). Применены параметры по умолчанию.")
            result["status"] = "not_found"
            result["message"] = "Модель не найдена на Civitai. Применены параметры по умолчанию."
            return result
        else:
            logger.warning(f"[CivitaiService] Неожиданный ответ Civitai: HTTP {resp.status_code}. Применены параметры по умолчанию.")
            result["status"] = "http_error"
            result["message"] = f"Civitai вернул код {resp.status_code}. Выставлены параметры по умолчанию."
            return result

    except requests.exceptions.Timeout:
        logger.error(f"[CivitaiService] Ошибка подключения: превышен таймаут (4.0с) при запросе к Civitai для '{checkpoint_name}'. Сеть недоступна или заблокирована.")
        result["status"] = "timeout"
        result["message"] = "Таймаут соединения с Civitai (нет интернета). Выставлены параметры по умолчанию."
        return result
    except requests.exceptions.ConnectionError as e:
        logger.error(f"[CivitaiService] Ошибка подключения к Civitai (сеть недоступна): {e}")
        result["status"] = "connection_error"
        result["message"] = "Ошибка подключения к сети. Выставлены параметры по умолчанию."
        return result
    except Exception as e:
        logger.error(f"[CivitaiService] Непредвиденная ошибка при запросе к Civitai: {e}")
        result["status"] = "error"
        result["message"] = f"Сбой запроса: {e}. Выставлены параметры по умолчанию."
        return result
