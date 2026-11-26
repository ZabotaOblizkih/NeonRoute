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
from typing import Dict, Any, Optional, List, Callable

import requests

from config import APP_VERSION
from services.model_catalog_service import (
    DEFAULT_PIPELINE_PARAMS,
    check_arch_compatibility,
    detect_architecture_from_name,
    normalize_model_search_candidates,
    parse_civitai_meta,
    safe_str_list,
)

logger = logging.getLogger("NeonRoute.CivitaiService")

CIVITAI_API_URL = "https://civitai.com/api/v1/model-versions/by-hash/{hash}"


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

    curr_forge_root = getattr(config, "FORGE_ROOT_DIR", None)
    candidate_dirs: List[Path] = []
    if hasattr(config, "get_forge_models_dir"):
        try:
            candidate_dirs.append(config.get_forge_models_dir())
        except Exception:
            pass
    if curr_forge_root:
        p_root = Path(curr_forge_root).resolve()
        candidate_dirs.extend([
            p_root / "models" / "Stable-diffusion",
            p_root / "webui" / "models" / "Stable-diffusion",
        ])

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


def resolve_checkpoint_file(checkpoint_name: str) -> Optional[Path]:
    """Разрешает путь к файлу чекпоинта в штатных каталогах Forge."""
    if not checkpoint_name:
        return None
    checkpoint_file = Path(checkpoint_name)
    if checkpoint_file.is_absolute() and checkpoint_file.exists():
        return checkpoint_file

    search_dirs = get_forge_search_dirs()
    for s_dir in search_dirs:
        candidate = s_dir / checkpoint_name
        if candidate.exists():
            return candidate
    for s_dir in search_dirs:
        if s_dir.exists():
            matches = list(s_dir.rglob(Path(checkpoint_name).name))
            if matches:
                return matches[0]
    return checkpoint_file if checkpoint_file.exists() else None


CIVITAI_HEADERS = {
    "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) NeonRoute/{APP_VERSION}",
    "Accept": "application/json"
}


def compute_model_sha256(
    file_path: Path,
    max_mb: Optional[int] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Optional[str]:
    """
    Быстрое хеширование первых max_mb мегабайт файла для мгновенной сверки,
    либо полный расчет AutoV2 SHA256 для точного совпадения с базой Civitai.
    Поддерживает прерывание через cancel_check().
    """
    if not file_path.exists():
        return None
    try:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            if max_mb and max_mb > 0:
                for _ in range(max_mb):
                    if cancel_check and cancel_check():
                        return None
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    sha256.update(chunk)
            else:
                while True:
                    if cancel_check and cancel_check():
                        return None
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
    images = ver_data.get("images") or []
    meta = {}
    for img in images:
        if isinstance(img, dict) and isinstance(img.get("meta"), dict):
            m = img["meta"]
            if any(k in m for k in ("steps", "cfgScale", "sampler", "Sampler")):
                meta = m
                break
            elif not meta:
                meta = m

    trained_words = safe_str_list(ver_data.get("trainedWords"))
    base_model = str(ver_data.get("baseModel", "") or "")
    parsed = parse_civitai_meta(
        meta=meta,
        model_tags=model_tags,
        model_title=model_title,
        checkpoint_name=checkpoint_name,
        base_model=base_model,
        trained_words=trained_words,
    )

    ver_name = ver_data.get("name", "v1.0")
    parsed["source"] = "civitai"
    if parsed.get("has_real_meta"):
        parsed["status"] = "success"
        parsed["message"] = f"Успешно подтянуты параметры Civitai: «{model_title}» (версия {ver_name})"
    else:
        parsed["status"] = "empty_meta"
        parsed["message"] = (
            f"Модель «{model_title}» (версия {ver_name}) найдена на Civitai, "
            "но у превью-изображений отсутствуют метаданные генерации."
        )
    return parsed


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


def _search_civitai_by_name(
    checkpoint_name: str,
    cancel_check: Optional[Callable[[], bool]] = None,
    autofill_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Полнотекстовый fallback-поиск модели по очищенным кандидатам имени с учетом архитектуры."""
    aid = autofill_id or "-"
    queries = normalize_model_search_candidates(checkpoint_name)
    if not queries:
        return None
    query_arch = detect_architecture_from_name(checkpoint_name)

    for q in queries:
        if cancel_check and cancel_check():
            return None
        search_url = f"https://civitai.com/api/v1/models?query={requests.utils.quote(q)}&types=Checkpoint&limit=5"
        try:
            logger.info(f"[AutoFill:{aid}] CIVITAI_API name_search query='{q}'")
            resp = requests.get(search_url, headers=CIVITAI_HEADERS, timeout=8.0)
            if resp.status_code != 200:
                logger.info(f"[AutoFill:{aid}] CIVITAI_API name_search http={resp.status_code} query='{q}'")
                continue
            data = resp.json()
            items = data.get("items", [])
            if not items:
                continue

            best_model = None
            for item in items:
                if not _is_name_relevant(item.get("name", ""), checkpoint_name):
                    continue
                versions = item.get("modelVersions") or []
                if query_arch and versions:
                    if not any(
                        check_arch_compatibility(query_arch, detect_architecture_from_name(v.get("baseModel", "")))
                        for v in versions
                    ):
                        continue
                best_model = item
                break

            if not best_model:
                continue

            versions = best_model.get("modelVersions", [])
            if not versions:
                continue

            parts = q.split()
            target_ver = versions[0]
            for v in versions:
                v_arch = detect_architecture_from_name(v.get("baseModel", ""))
                if not check_arch_compatibility(query_arch, v_arch):
                    continue
                v_name = v.get("name", "").lower().replace(".", "").replace(" ", "")
                if any(p.lower() in v_name for p in parts[1:] if len(p) >= 2):
                    target_ver = v
                    break

            ver_id = target_ver.get("id")
            ver_url = f"https://civitai.com/api/v1/model-versions/{ver_id}"
            ver_resp = requests.get(ver_url, headers=CIVITAI_HEADERS, timeout=8.0)
            ver_data = ver_resp.json() if ver_resp.status_code == 200 else target_ver

            model_title = best_model.get("name", "")
            model_tags = [str(t).lower() for t in (best_model.get("tags") or [])]
            parsed = _extract_meta_into_params(ver_data, model_tags, model_title, checkpoint_name)
            logger.info(
                f"[AutoFill:{aid}] CIVITAI_API matched='name' model_title='{model_title}' "
                f"status={parsed.get('status')} has_real_meta={parsed.get('has_real_meta')} "
                f"sampler='{parsed.get('sampler')}' scheduler='{parsed.get('scheduler')}' "
                f"steps={parsed.get('steps')} cfg_scale={parsed.get('cfg_scale')} "
                f"clip_skip={parsed.get('clip_skip')} hr_scale={parsed.get('hr_scale')} "
                f"has_trained_words={bool(parsed.get('trained_words'))} "
                f"has_negative_prompt={bool(parsed.get('negative_prompt'))}"
            )
            return parsed

        except Exception as e:
            logger.warning(f"[AutoFill:{aid}] CIVITAI_API name_search error query='{q}': {e}")
            continue

    return None


def fetch_civitai_api_parameters(
    checkpoint_name: str,
    model_hash: Optional[str] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    autofill_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Запрашивает параметры модели напрямую на Civitai API:
    1. По хэшу SHA256 (AutoV2).
    2. При несовпадении или 404 — через fallback-поиск по имени.
    """
    aid = autofill_id or "-"
    result = dict(DEFAULT_PIPELINE_PARAMS)
    if cancel_check and cancel_check():
        result["status"] = "cancelled"
        result["message"] = "Операция отменена."
        return result

    if model_hash is None:
        checkpoint_file = resolve_checkpoint_file(checkpoint_name)
        if checkpoint_file and checkpoint_file.exists():
            logger.info(f"[AutoFill:{aid}] Хеширование модели {checkpoint_name}...")
            model_hash = compute_model_sha256(checkpoint_file, max_mb=None, cancel_check=cancel_check)

    if cancel_check and cancel_check():
        result["status"] = "cancelled"
        result["message"] = "Операция отменена."
        return result

    # 1. Запрос по хэшу (если хэш доступен)
    if model_hash:
        url = CIVITAI_API_URL.format(hash=model_hash)
        try:
            logger.info(f"[AutoFill:{aid}] CIVITAI_API hash_lookup sha256_prefix={model_hash[:10]}")
            resp = requests.get(url, headers=CIVITAI_HEADERS, timeout=8.0)
            if resp.status_code == 200:
                data = resp.json()
                model_obj = data.get("model") or {}
                model_tags = [str(t).lower() for t in (model_obj.get("tags") or []) if t]
                model_title = model_obj.get("name") or data.get("name") or checkpoint_name
                parsed = _extract_meta_into_params(
                    ver_data=data,
                    model_tags=model_tags,
                    model_title=model_title,
                    checkpoint_name=checkpoint_name
                )
                logger.info(
                    f"[AutoFill:{aid}] CIVITAI_API matched='hash' model_title='{model_title}' "
                    f"status={parsed.get('status')} has_real_meta={parsed.get('has_real_meta')} "
                    f"sampler='{parsed.get('sampler')}' scheduler='{parsed.get('scheduler')}' "
                    f"steps={parsed.get('steps')} cfg_scale={parsed.get('cfg_scale')} "
                    f"clip_skip={parsed.get('clip_skip')} hr_scale={parsed.get('hr_scale')} "
                    f"has_trained_words={bool(parsed.get('trained_words'))} "
                    f"has_negative_prompt={bool(parsed.get('negative_prompt'))}"
                )
                return parsed
            else:
                logger.info(f"[AutoFill:{aid}] CIVITAI_API hash_lookup http={resp.status_code}, fallback к поиску по имени")
        except Exception as e:
            logger.warning(f"[AutoFill:{aid}] CIVITAI_API hash_lookup error ({e}), fallback к поиску по имени")

    # 2. Fallback-поиск по имени
    name_result = _search_civitai_by_name(checkpoint_name, cancel_check=cancel_check, autofill_id=aid)
    if name_result:
        return name_result

    # 3. Ничего не найдено
    result["status"] = "not_found"
    result["source"] = "fallback"
    result["message"] = f"Модель «{checkpoint_name}» не найдена в базе Civitai."
    logger.info(f"[AutoFill:{aid}] CIVITAI_API status=not_found checkpoint='{checkpoint_name}'")
    return result


def fetch_civitai_parameters(
    checkpoint_name: str,
    cancel_check: Optional[Callable[[], bool]] = None,
    autofill_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Каскадный резолвер параметров модели:
    1. Каталог (GitHub raw -> локальный data/model_presets_catalog.json)
    2. Civitai API (SHA256 -> имя)
    3. Явный fallback (not_found / empty_meta / catalog_error)
    """
    from services.model_catalog_service import resolve_model_presets
    return resolve_model_presets(
        checkpoint_name=checkpoint_name,
        cancel_check=cancel_check,
        autofill_id=autofill_id,
    )
