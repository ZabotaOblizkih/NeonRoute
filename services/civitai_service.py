"""
Civitai Model Metadata Service for NeonRoute.
Provides on-demand lookup of model generation parameters via SHA256 hash
with robust name-based fallback and image metadata extraction.
"""

import os
import re
import html
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

import requests

from config import FORGE_ROOT_DIR, APP_VERSION

logger = logging.getLogger("NeonRoute.CivitaiService")

CIVITAI_API_URL = "https://civitai.com/api/v1/model-versions/by-hash/{hash}"
FORGE_SD_MODELS_DIR = FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui" / "models" / "Stable-diffusion"


def clean_civitai_description(raw_html: Optional[str]) -> str:
    """
    Нормализует HTML-описание модели с Civitai в чистый форматированный текст:
    - Конвертирует блочные элементы (<p>, <br>, <li>, <tr>, <pre>, заголовки) в читаемые переносы строк и маркеры списка;
    - Полностью удаляет остальные HTML-теги;
    - Декодирует HTML-сущности (html.unescape);
    - Вычищает невидимые/мусорные символы (\\u3164 Hangul Filler, zero-width spaces \\u200b, \\u200c, \\u200d, \\ufeff, \\u2060);
    - Нормализует пробелы и сжимает избыточные пустые строки.
    """
    if not raw_html:
        return ""

    text = str(raw_html)

    # 1. Заменяем блочные разделители на переносы строк
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</?(?:p|div|h[1-6]|tr|blockquote|pre)[^>]*>', '\n', text)
    text = re.sub(r'(?i)<li[^>]*>', '\n• ', text)
    text = re.sub(r'(?i)</li>', '', text)

    # 2. Удаляем все оставшиеся теги (<a>, <strong>, <span>, <code>, <ul>, <ol>, <table> и т.д.)
    text = re.sub(r'<[^>]+>', '', text)

    # 3. Декодируем HTML-сущности (&quot;, &#39;, &amp;, &nbsp;, &gt;, &lt; и т.д.)
    text = html.unescape(text)

    # 4. Удаляем мусорные невидимые символы и Hangul filler
    text = text.replace('\u00a0', ' ')
    text = re.sub(r'[\u3164\u200b\u200c\u200d\ufeff\u2060]', '', text)

    # 5. Нормализация строк и пробелов
    lines = []
    for line in text.splitlines():
        cleaned_line = re.sub(r'[ \t]+', ' ', line).strip()
        lines.append(cleaned_line)

    result = '\n'.join(lines)
    # Сжимаем 3+ переноса строк до максимум двух (пустая строка между абзацами)
    return re.sub(r'\n{3,}', '\n\n', result).strip()


def get_forge_search_dirs() -> List[Path]:
    """Динамически возвращает список существующих каталогов чекпоинтов Forge."""
    import config
    mod_root = globals().get("FORGE_ROOT_DIR")
    cfg_root = getattr(config, "FORGE_ROOT_DIR", None)
    curr_forge_root = mod_root if (mod_root is not None and mod_root != cfg_root) else (cfg_root or mod_root)

    candidate_dirs: List[Path] = []
    if (mod_root is None or mod_root == cfg_root) and hasattr(config, "get_forge_models_dir"):
        try:
            candidate_dirs.append(config.get_forge_models_dir())
        except Exception:
            pass
    if curr_forge_root:
        p_root = Path(curr_forge_root).resolve()
        candidate_dirs.extend([
            p_root / "models" / "Stable-diffusion",
            p_root / "webui" / "models" / "Stable-diffusion",
            p_root / "webui_forge_cu121_torch231" / "webui" / "models" / "Stable-diffusion"
        ])
    if FORGE_SD_MODELS_DIR not in candidate_dirs:
        candidate_dirs.append(FORGE_SD_MODELS_DIR)

    seen = set()
    result = []
    for d in candidate_dirs:
        try:
            r = Path(d).resolve()
            if r not in seen:
                seen.add(r)
                result.append(r)
        except Exception:
            pass
    return result

CIVITAI_HEADERS = {
    "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) NeonRoute/{APP_VERSION}",
    "Accept": "application/json"
}

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


def _extract_meta_into_params(
    ver_data: dict,
    model_tags: list,
    model_title: str,
    checkpoint_name: str
) -> Dict[str, Any]:
    """Извлекает параметры автора из объекта версии Civitai и ее превью."""
    result = dict(DEFAULT_PIPELINE_PARAMS)

    images = ver_data.get("images", [])
    meta = {}
    for img in images:
        if "meta" in img and isinstance(img["meta"], dict):
            m = img["meta"]
            if any(k in m for k in ("steps", "cfgScale", "sampler", "Sampler")):
                meta = m
                break
            elif not meta:
                meta = m

    if meta:
        sampler_name = meta.get("sampler") or meta.get("Sampler")
        if sampler_name:
            result["sampler"] = str(sampler_name).strip()
        if meta.get("steps") is not None:
            try:
                result["steps"] = int(meta["steps"])
            except (ValueError, TypeError):
                pass
        if meta.get("cfgScale") is not None:
            try:
                result["cfg_scale"] = float(meta["cfgScale"])
            except (ValueError, TypeError):
                pass
        cs = meta.get("Clip skip") or meta.get("clipSkip")
        if cs is not None:
            try:
                result["clip_skip"] = int(cs)
            except (ValueError, TypeError):
                pass
        if meta.get("negativePrompt"):
            result["negative_prompt"] = str(meta["negativePrompt"]).strip(' "')
        if meta.get("Hires upscaler"):
            result["hires_upscaler"] = str(meta["Hires upscaler"]).strip(' "')
        ds = meta.get("Denoising strength") or meta.get("denoise")
        if ds is not None:
            try:
                result["hires_denoise"] = float(ds)
            except (ValueError, TypeError):
                pass

    base_model = str(ver_data.get("baseModel", "")).lower()
    name_lower = f"{model_title} {ver_data.get('name', '')} {checkpoint_name}".lower()
    is_anime = any(w in name_lower or w in model_tags for w in ["anime", "hentai", "manga", "2d", "waifu"])

    if is_anime:
        result["hires_upscaler"] = "R-ESRGAN 4x+ Anime6B"
        if "clip_skip" not in meta:
            result["clip_skip"] = 2
        if not meta.get("negativePrompt"):
            result["negative_prompt"] = "worst quality, low quality, bad anatomy, deformed, badhandv4"

    ver_name = ver_data.get("name", "v1.0")
    result["status"] = "success"
    result["message"] = f"Успешно подтянуты параметры Civitai: «{model_title}» (версия {ver_name})"

    LIGHT_SPEED_MARKERS = ("light", "lightning", "turbo", "lcm", "dmd", "hyper")
    steps_val = result.get("steps")
    is_fast_model = any(w in name_lower for w in LIGHT_SPEED_MARKERS) or (
        isinstance(steps_val, (int, float)) and steps_val <= 10
    )
    if is_fast_model:
        result["adetailer_face_denoise"] = 0.30
        result["adetailer_hands_denoise"] = 0.36

    return result


def _is_name_relevant(civitai_name: str, file_query: str) -> bool:
    """Строгий фильтр ложных срабатываний Civitai Meilisearch."""
    m_clean = re.sub(r"[^a-zA-Z0-9а-яА-Я]", "", civitai_name).lower()
    q_clean = re.sub(r"[^a-zA-Z0-9а-яА-Я]", "", file_query).lower()

    # 1. Прямое совпадение уникальных имен длиной от 5 символов (counterfeitxl в counterfeitxlv25)
    if len(m_clean) >= 5 and (m_clean in q_clean or q_clean in m_clean):
        return True

    # 2. Исключаем технический мусор и приставки
    stop_words = {
        "model", "safetensors", "checkpoint", "ckpt", "pruned", "fp16", "fp8",
        "v1", "v2", "sdxl", "sd15", "non", "fake", "test", "temp", "beta", "alpha"
    }
    q_tokens = [t.lower() for t in re.split(r"[\s_.\-]+", file_query) if len(t) >= 3 and t.lower() not in stop_words]
    m_tokens = [t.lower() for t in re.split(r"[\s_.\-]+", civitai_name) if len(t) >= 3]

    for qt in q_tokens:
        for mt in m_tokens:
            if qt == mt:
                return True
            if len(qt) >= 5 and len(mt) >= 5 and (qt in mt or mt in qt):
                return True

    return False


def _search_civitai_by_name(checkpoint_name: str) -> Optional[Dict[str, Any]]:
    """Полнотекстовый fallback-поиск модели по имени файла."""
    base = re.sub(r"\.(safetensors|ckpt|pt)$", "", Path(checkpoint_name).name, flags=re.IGNORECASE)
    base = re.sub(r"\[[a-fA-F0-9]+\]", "", base)
    cleaned = re.sub(r"[_.\-]+", " ", base).strip()

    queries = [cleaned]
    parts = cleaned.split()
    if len(parts) > 1 and parts[0] not in queries:
        queries.append(parts[0])

    for q in queries:
        search_url = f"https://civitai.com/api/v1/models?query={requests.utils.quote(q)}&types=Checkpoint&limit=5"
        try:
            resp = requests.get(search_url, headers=CIVITAI_HEADERS, timeout=8.0)
            if resp.status_code != 200:
                continue
            data = resp.json()
            items = data.get("items", [])
            if not items:
                continue

            best_model = None
            for item in items:
                if _is_name_relevant(item.get("name", ""), checkpoint_name):
                    best_model = item
                    break

            if not best_model:
                continue

            versions = best_model.get("modelVersions", [])
            if not versions:
                continue

            target_ver = versions[0]
            for v in versions:
                v_name = v.get("name", "").lower().replace(".", "").replace(" ", "")
                if any(p.lower() in v_name for p in parts[1:] if len(p) >= 2):
                    target_ver = v
                    break

            ver_id = target_ver.get("id")
            ver_url = f"https://civitai.com/api/v1/model-versions/{ver_id}"
            ver_resp = requests.get(ver_url, headers=CIVITAI_HEADERS, timeout=8.0)
            ver_data = ver_resp.json() if ver_resp.status_code == 200 else target_ver

            model_tags = [str(t).lower() for t in (best_model.get("tags") or [])]
            return _extract_meta_into_params(ver_data, model_tags, best_model.get("name", ""), checkpoint_name)

        except Exception as e:
            logger.warning(f"[CivitaiService] Ошибка fallback-поиска по имени '{q}': {e}")
            continue

    return None


def fetch_civitai_parameters(checkpoint_name: str) -> Dict[str, Any]:
    """
    Запрашивает параметры модели на Civitai:
    1. По хэшу SHA256 (AutoV2).
    2. При несовпадении или 404 — через fallback-поиск по имени.
    """
    result = dict(DEFAULT_PIPELINE_PARAMS)
    checkpoint_file = Path(checkpoint_name)
    if not checkpoint_file.is_absolute() or not checkpoint_file.exists():
        search_dirs = get_forge_search_dirs()
        found = False
        for s_dir in search_dirs:
            candidate = s_dir / checkpoint_name
            if candidate.exists():
                checkpoint_file = candidate
                found = True
                break
        if not found:
            for s_dir in search_dirs:
                if s_dir.exists():
                    matches = list(s_dir.rglob(Path(checkpoint_name).name))
                    if matches:
                        checkpoint_file = matches[0]
                        found = True
                        break

    model_hash = None
    if checkpoint_file.exists():
        logger.info(f"[CivitaiService] Хеширование модели {checkpoint_name}...")
        model_hash = compute_model_sha256(checkpoint_file, max_mb=None)

    # 1. Запрос по хэшу (если хэш доступен)
    if model_hash:
        url = CIVITAI_API_URL.format(hash=model_hash)
        try:
            logger.info(f"[CivitaiService] Запрос по хэшу: {model_hash[:10]}...")
            resp = requests.get(url, headers=CIVITAI_HEADERS, timeout=8.0)
            if resp.status_code == 200:
                data = resp.json()
                model_tags = [str(t).lower() for t in ((data.get("model") or {}).get("tags") or []) if t]
                return _extract_meta_into_params(
                    ver_data=data,
                    model_tags=model_tags,
                    model_title=data.get("name", checkpoint_name),
                    checkpoint_name=checkpoint_name
                )
            else:
                logger.info(f"[CivitaiService] Запрос по хэшу вернул {resp.status_code}, пробуем поиск по названию...")
        except Exception as e:
            logger.warning(f"[CivitaiService] Сбой запроса по хэшу ({e}), переключаемся на поиск по имени...")

    # 2. Fallback-поиск по имени
    logger.info(f"[CivitaiService] Выполняем fallback-поиск для: '{checkpoint_name}'...")
    name_result = _search_civitai_by_name(checkpoint_name)
    if name_result:
        logger.info(f"[CivitaiService] Успешно найдены параметры по имени: {name_result.get('message')}")
        return name_result

    # 3. Ничего не найдено
    result["status"] = "not_found"
    result["message"] = f"Модель «{checkpoint_name}» не найдена в базе Civitai."
    return result
