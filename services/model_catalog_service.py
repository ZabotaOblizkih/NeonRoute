"""
Model Presets Catalog & Cascade Resolver for NeonRoute.
Provides:
- Safe type coercion and unified Civitai/Catalog metadata parsing
- Name normalization (search candidates vs collision-free canonical_key)
- Versioned catalog loading & validation (GitHub raw -> local data/model_presets_catalog.json)
- Cascade resolver: Catalog (GitHub -> Local) -> Civitai API -> Explicit Fallback
"""

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

import config
from services.art_director.geometry import is_xl_model

logger = logging.getLogger("NeonRoute.ModelCatalogService")

CATALOG_SCHEMA_VERSION = 1

DEFAULT_PIPELINE_PARAMS: Dict[str, Any] = {
    "sampler": "DPM++ 2M",
    "scheduler": "Karras",
    "steps": 25,
    "cfg_scale": 6.0,
    "clip_skip": 1,
    "enable_hr": False,
    "hr_scale": 1.25,
    "hires_upscaler": "4x-UltraSharp",
    "hires_denoise": 0.32,
    "negative_prompt": "worst quality, low quality, bad anatomy, bad proportions, deformed, badhandv4",
}

_TECH_SUFFIXES = (
    "_fp8_e4m3fn", "-fp8-e4m3fn", "_fp8e4m3fn", "-fp8e4m3fn",
    "_fp8e4", "-fp8e4", "_fp16e4", "-fp16e4",
    "_e4m3fn", "-e4m3fn", "_e5m2", "-e5m2",
    "_fp16", "-fp16", "_fp8", "-fp8", "_bf16", "-bf16",
    "_pruned", "-pruned", "_emaonly", "-emaonly", "_noema", "-noema",
    "_ema", "-ema", "_inpainting", "-inpainting",
    "_hypervae", "-hypervae", "_vae", "-vae",
)

_STOP_TOKENS = {
    "model", "safetensors", "checkpoint", "ckpt", "pruned", "fp16", "fp8",
    "fp8e4", "fp16e4", "e4m3fn", "bf16", "ema", "emaonly", "noema", "vae",
    "inpainting", "v1", "v2", "sdxl", "sd15", "non", "fake", "test", "temp",
    "beta", "alpha", "final",
}


# ---------------------------------------------------------------------------
# Safe type coercion helpers
# ---------------------------------------------------------------------------

def safe_int(
    val: Any,
    default: Optional[int] = None,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
) -> Optional[int]:
    """Safely converts value to int with optional clamping; returns default on None/error."""
    if val is None or isinstance(val, bool):
        return default
    try:
        num = int(float(str(val).strip()))
    except (ValueError, TypeError):
        return default
    if min_val is not None and num < min_val:
        num = min_val
    if max_val is not None and num > max_val:
        num = max_val
    return num


def safe_float(
    val: Any,
    default: Optional[float] = None,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> Optional[float]:
    """Safely converts value to float with optional clamping; returns default on None/error."""
    if val is None or isinstance(val, bool):
        return default
    try:
        num = float(str(val).strip().replace(",", "."))
    except (ValueError, TypeError):
        return default
    if min_val is not None and num < min_val:
        num = min_val
    if max_val is not None and num > max_val:
        num = max_val
    return num


def safe_bool(val: Any, default: bool = False) -> bool:
    """Safely converts value to bool."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return default


def safe_str(val: Any, default: str = "") -> str:
    """Safely converts value to stripped string."""
    if val is None:
        return default
    return str(val).strip()


def safe_str_list(val: Any) -> List[str]:
    """Converts list/tuple/comma-separated string to deduplicated list of non-empty strings."""
    if val is None:
        return []
    if isinstance(val, str):
        raw_items = [p.strip() for p in val.split(",")]
    elif isinstance(val, (list, tuple, set)):
        raw_items = [str(p).strip() for p in val if p is not None]
    else:
        return []

    seen = set()
    result: List[str] = []
    for item in raw_items:
        if not item:
            continue
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def merge_unique_str_list(existing: Any, incoming: Any) -> List[str]:
    """Merges two string lists without case-insensitive duplicates, preserving order."""
    base = safe_str_list(existing)
    seen = {x.lower() for x in base}
    for item in safe_str_list(incoming):
        if item.lower() not in seen:
            seen.add(item.lower())
            base.append(item)
    return base


# ---------------------------------------------------------------------------
# Architecture & Sampler/Scheduler helpers
# ---------------------------------------------------------------------------

def detect_architecture_from_name(identifier: Any) -> Optional[str]:
    """Detects 'sdxl' or 'sd15' from checkpoint name, baseModel, or dict; None if ambiguous."""
    if not identifier:
        return None
    if isinstance(identifier, dict):
        arch = str(identifier.get("architecture") or (identifier.get("pipeline") or {}).get("architecture") or "").strip().lower()
        if arch in ("sdxl", "pony", "flux", "illustrious", "sd3"):
            return "sdxl"
        if arch in ("sd15", "sd1", "sd_15", "sd 1.5"):
            return "sd15"
    text = str(identifier).strip().lower()
    if not text:
        return None
    if any(k in text for k in ("sd 1.5", "sd1.5", "sd15", "sd 1.", "1.5")):
        if not any(k in text for k in ("sdxl", "pony", "flux", "illustrious")):
            return "sd15"
    if is_xl_model(identifier):
        return "sdxl"
    return None


def check_arch_compatibility(query_arch: Optional[str], entry_arch: Optional[str]) -> bool:
    """Checks architecture compatibility; unknown architecture is treated as wildcard."""
    if not query_arch or not entry_arch:
        return True
    q = query_arch.strip().lower()
    e = entry_arch.strip().lower()
    xl_set = {"sdxl", "pony", "flux", "illustrious", "sd3"}
    sd15_set = {"sd15", "sd1", "sd_15", "sd 1.5"}
    if q in xl_set and e in sd15_set:
        return False
    if q in sd15_set and e in xl_set:
        return False
    return True


def split_sampler_scheduler(
    raw_sampler: Any,
    explicit_scheduler: Any = None,
) -> Tuple[str, Optional[str]]:
    """
    Separates combined sampler strings like 'DPM++ 2M Karras' into ('DPM++ 2M', 'Karras').
    Preserves explicit_scheduler if provided.
    """
    sampler_str = safe_str(raw_sampler)
    sched_str = safe_str(explicit_scheduler) or None
    if not sampler_str:
        return "", sched_str

    if re.search(r"\s+karras$", sampler_str, flags=re.IGNORECASE):
        base_sampler = re.sub(r"\s+karras$", "", sampler_str, flags=re.IGNORECASE).strip()
        if not sched_str:
            sched_str = "Karras"
        return base_sampler or sampler_str, sched_str

    if re.search(r"\s+exponential$", sampler_str, flags=re.IGNORECASE):
        base_sampler = re.sub(r"\s+exponential$", "", sampler_str, flags=re.IGNORECASE).strip()
        if not sched_str:
            sched_str = "Exponential"
        return base_sampler or sampler_str, sched_str

    return sampler_str, sched_str


# ---------------------------------------------------------------------------
# Name normalization & Canonical key
# ---------------------------------------------------------------------------

def _strip_tech_suffixes(stem: str) -> str:
    s = stem.strip()
    changed = True
    while changed:
        changed = False
        low = s.lower()
        for sfx in _TECH_SUFFIXES:
            if low.endswith(sfx):
                s = s[:-len(sfx)]
                changed = True
                break
    return s.strip(" _-.")


def compute_canonical_key(
    checkpoint_name: str,
    architecture: Optional[str] = None,
) -> str:
    """
    Computes a collision-safe canonical key that preserves version and architecture.
    Strips only file extension, [hash], and packaging/quantization suffixes (fp16/fp8/pruned/ema).
    """
    if not checkpoint_name:
        return ""
    stem = Path(checkpoint_name).name
    stem = re.sub(r"\.(safetensors|ckpt|pt|bin)$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\[[a-fA-F0-9]+\]", "", stem)
    cleaned = _strip_tech_suffixes(stem)
    norm = re.sub(r"[^a-z0-9а-яё]+", "_", cleaned.lower()).strip("_")
    arch = architecture or detect_architecture_from_name(checkpoint_name)
    if arch:
        return f"{arch}::{norm}"
    return norm


def normalize_model_search_candidates(checkpoint_name: str) -> List[str]:
    """
    Produces ordered search candidates from most specific to most general:
    1. Exact stem (without extension and [hash])
    2. Stem without technical packaging/quantization tails (fp16/fp8/pruned/ema/vae)
    3. Stem without trailing version suffix (v1/v2/v1.0/etc.) if >= 4 chars
    """
    if not checkpoint_name:
        return []
    raw = Path(checkpoint_name).name
    stem = re.sub(r"\.(safetensors|ckpt|pt|bin)$", "", raw, flags=re.IGNORECASE)
    stem = re.sub(r"\[[a-fA-F0-9]+\]", "", stem).strip()

    candidates: List[str] = []
    seen = set()

    def _add(val: str):
        clean = re.sub(r"[_.\-]+", " ", val).strip()
        clean = re.sub(r"\s+", " ", clean)
        low = clean.lower()
        if clean and low not in seen:
            seen.add(low)
            candidates.append(clean)

    _add(stem)
    no_tech = _strip_tech_suffixes(stem)
    _add(no_tech)

    # Strip trailing version markers (e.g. _v51, v6.0, v10Final) as broader fallback
    no_ver = re.sub(r"(?i)[\s_.\-]+v?\d+(?:[._]\d+)*(?:final|alpha|beta|b\d+)?$", "", no_tech).strip()
    if len(re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]", "", no_ver)) >= 4:
        _add(no_ver)

    return candidates


# ---------------------------------------------------------------------------
# Unified Civitai metadata parser
# ---------------------------------------------------------------------------

def parse_civitai_meta(
    meta: Optional[Dict[str, Any]],
    model_tags: Optional[List[str]] = None,
    model_title: str = "",
    checkpoint_name: str = "",
    base_model: str = "",
    trained_words: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Unified, type-safe parser for Civitai image generation metadata.
    Never overwrites explicit Clip Skip in meta, separates sampler/scheduler,
    and reports `has_real_meta` so callers never mistake empty meta for success.
    """
    result = dict(DEFAULT_PIPELINE_PARAMS)
    result["negative_prompt"] = None
    result["routing_description"] = None
    result["llm_guidance"] = None
    result["style_triggers"] = []
    result["trained_words"] = []
    m = meta if isinstance(meta, dict) else {}
    tags_lower = [str(t).lower() for t in (model_tags or []) if t]

    has_real_meta = False

    raw_sampler = m.get("sampler") or m.get("Sampler")
    raw_scheduler = m.get("Schedule type") or m.get("scheduler") or m.get("Scheduler")
    if raw_sampler is not None and safe_str(raw_sampler):
        sampler_clean, sched_clean = split_sampler_scheduler(raw_sampler, raw_scheduler)
        if sampler_clean:
            result["sampler"] = sampler_clean
            has_real_meta = True
        if sched_clean:
            result["scheduler"] = sched_clean
    elif raw_scheduler is not None and safe_str(raw_scheduler):
        result["scheduler"] = safe_str(raw_scheduler)
        has_real_meta = True

    steps_val = safe_int(m.get("steps"), default=None, min_val=5, max_val=60)
    if steps_val is not None:
        result["steps"] = steps_val
        has_real_meta = True

    cfg_val = safe_float(m.get("cfgScale") if m.get("cfgScale") is not None else m.get("cfg_scale"), default=None, min_val=1.0, max_val=15.0)
    if cfg_val is not None:
        result["cfg_scale"] = round(cfg_val, 1)
        has_real_meta = True

    raw_clip = m.get("Clip skip") if m.get("Clip skip") is not None else m.get("clipSkip")
    clip_val = safe_int(raw_clip, default=None, min_val=1, max_val=4)
    explicit_clip_present = clip_val is not None
    if explicit_clip_present:
        result["clip_skip"] = clip_val
        has_real_meta = True

    neg_val = safe_str(m.get("negativePrompt") or m.get("negative_prompt"))
    if neg_val:
        result["negative_prompt"] = neg_val.strip(' "')
        has_real_meta = True

    up_val = safe_str(m.get("Hires upscaler") or m.get("hires_upscaler"))
    if up_val:
        result["hires_upscaler"] = up_val.strip(' "')
        result["enable_hr"] = True
        has_real_meta = True

    raw_hr_scale = m.get("Hires upscale") if m.get("Hires upscale") is not None else m.get("hr_scale")
    hr_scale_val = safe_float(raw_hr_scale, default=None, min_val=1.0, max_val=4.0)
    if hr_scale_val is not None and hr_scale_val > 1.0:
        result["hr_scale"] = round(hr_scale_val, 2)
        result["enable_hr"] = True
        has_real_meta = True

    raw_denoise = m.get("Denoising strength") if m.get("Denoising strength") is not None else m.get("denoise")
    denoise_val = safe_float(raw_denoise, default=None, min_val=0.10, max_val=0.90)
    if denoise_val is not None:
        result["hires_denoise"] = round(denoise_val, 2)
        has_real_meta = True

    arch = detect_architecture_from_name(base_model) or detect_architecture_from_name(f"{model_title} {checkpoint_name}")
    if arch:
        result["architecture"] = arch
        if arch == "sd15" and hr_scale_val is None:
            result["hr_scale"] = 1.5

    name_lower = f"{model_title} {checkpoint_name}".lower()
    is_anime = any(w in name_lower or w in tags_lower for w in ("anime", "hentai", "manga", "2d", "waifu"))
    if is_anime:
        if not up_val:
            result["hires_upscaler"] = "R-ESRGAN 4x+ Anime6B"
        if not explicit_clip_present:
            result["clip_skip"] = 2

    light_speed_markers = ("light", "lightning", "turbo", "lcm", "dmd", "hyper")
    if any(w in name_lower for w in light_speed_markers) or (isinstance(result.get("steps"), (int, float)) and result["steps"] <= 10):
        result["adetailer_face_denoise"] = 0.30
        result["adetailer_hands_denoise"] = 0.36

    result["trained_words"] = safe_str_list(trained_words)
    result["has_real_meta"] = has_real_meta
    return result


# ---------------------------------------------------------------------------
# Catalog loading, validation & matching
# ---------------------------------------------------------------------------

def validate_catalog_schema(data: Any) -> Tuple[bool, str]:
    """Validates versioned model_presets_catalog.json structure."""
    if not isinstance(data, dict):
        return False, "Корневой элемент каталога должен быть JSON-объектом."
    if "schema_version" not in data or not isinstance(data.get("schema_version"), int):
        return False, "Отсутствует или некорректен целочисленный ключ 'schema_version'."
    models = data.get("models")
    if not isinstance(models, list):
        return False, "Отсутствует или некорректен список 'models'."
    for idx, entry in enumerate(models):
        if not isinstance(entry, dict):
            return False, f"Запись models[{idx}] должна быть объектом."
        if not isinstance(entry.get("match"), dict):
            return False, f"Запись models[{idx}] не содержит секцию 'match'."
        if not isinstance(entry.get("pipeline"), dict):
            return False, f"Запись models[{idx}] не содержит секцию 'pipeline'."
    return True, ""


def load_local_catalog(catalog_path: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Loads and validates local model_presets_catalog.json.
    Distinguishes missing file (None, None) from read/parse/schema errors (None, error_message).
    """
    path = Path(catalog_path) if catalog_path else Path(getattr(config, "MODEL_PRESETS_CATALOG_PATH"))
    if not path.exists():
        return None, None
    try:
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except Exception as e:
        msg = f"Ошибка чтения/парсинга локального каталога {path.name}: {e}"
        logger.error(f"[ModelCatalogService] {msg}")
        return None, msg

    ok, err = validate_catalog_schema(data)
    if not ok:
        msg = f"Некорректная схема локального каталога {path.name}: {err}"
        logger.error(f"[ModelCatalogService] {msg}")
        return None, msg
    return data, None


def fetch_github_catalog(
    url: Optional[str] = None,
    timeout: float = 3.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Fetches and validates model_presets_catalog.json from GitHub raw URL with short timeout."""
    target_url = url or getattr(config, "MODEL_PRESETS_CATALOG_URL", "")
    if not target_url:
        return None, "URL каталога GitHub не задан."
    try:
        resp = requests.get(target_url, timeout=timeout, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code} от GitHub каталога"
        data = resp.json()
    except Exception as e:
        return None, f"Сбой загрузки каталога GitHub: {e}"

    ok, err = validate_catalog_schema(data)
    if not ok:
        return None, f"Некорректная схема каталога GitHub: {err}"
    return data, None


def match_catalog_entry(
    catalog: Dict[str, Any],
    checkpoint_name: str,
    sha256: Optional[str] = None,
    architecture: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Matches checkpoint against catalog entries with strict architecture compatibility
    and collision-safe priority:
    1. Exact SHA-256
    2. Exact canonical_key (with architecture)
    3. Exact alias / normalized candidate match
    4. Safe token match (tokens >= 5 chars only, no arch conflict)
    """
    models = catalog.get("models", [])
    if not models:
        return None

    query_arch = architecture or detect_architecture_from_name(checkpoint_name)
    query_key = compute_canonical_key(checkpoint_name, architecture=query_arch)
    query_key_no_arch = compute_canonical_key(checkpoint_name, architecture=None).split("::")[-1]
    candidates = normalize_model_search_candidates(checkpoint_name)
    candidates_clean = {re.sub(r"[^a-z0-9а-яё]", "", c.lower()) for c in candidates}
    sha_upper = sha256.strip().upper() if sha256 else None

    # 1. SHA-256 exact match
    if sha_upper:
        for entry in models:
            m_sha = safe_str((entry.get("match") or {}).get("sha256")).upper()
            if m_sha and m_sha == sha_upper:
                return entry

    # 2. Exact canonical_key match
    for entry in models:
        match_sec = entry.get("match") or {}
        pipe_sec = entry.get("pipeline") or {}
        entry_arch = safe_str(pipe_sec.get("architecture")) or None
        if not check_arch_compatibility(query_arch, entry_arch):
            continue
        c_key = safe_str(match_sec.get("canonical_key")).lower()
        if c_key and (c_key == query_key.lower() or c_key.split("::")[-1] == query_key_no_arch):
            return entry

    # 3. Exact alias match
    for entry in models:
        match_sec = entry.get("match") or {}
        pipe_sec = entry.get("pipeline") or {}
        entry_arch = safe_str(pipe_sec.get("architecture")) or None
        if not check_arch_compatibility(query_arch, entry_arch):
            continue
        aliases = safe_str_list(match_sec.get("aliases"))
        for alias in aliases:
            alias_clean = re.sub(r"[^a-z0-9а-яё]", "", alias.lower())
            if alias_clean and alias_clean in candidates_clean:
                return entry

    # 4. Safe long-token match (>= 5 chars, avoiding generic short names)
    for entry in models:
        match_sec = entry.get("match") or {}
        pipe_sec = entry.get("pipeline") or {}
        entry_arch = safe_str(pipe_sec.get("architecture")) or None
        if not check_arch_compatibility(query_arch, entry_arch):
            continue
        aliases = safe_str_list(match_sec.get("aliases"))
        c_key_base = safe_str(match_sec.get("canonical_key")).split("::")[-1]
        for ref in [c_key_base] + aliases:
            ref_clean = re.sub(r"[^a-z0-9а-яё]", "", ref.lower())
            if len(ref_clean) >= 5 and any(len(c) >= 5 and (ref_clean == c or ref_clean in c) for c in candidates_clean):
                return entry

    return None


def catalog_entry_to_params(entry: Dict[str, Any], source: str = "local_catalog") -> Dict[str, Any]:
    """Converts a catalog entry into normalized parameters dict for UI & pipeline."""
    match_sec = entry.get("match") or {}
    style_sec = entry.get("style_and_prompts") or {}
    pipe_sec = entry.get("pipeline") or {}

    raw_sampler = pipe_sec.get("sampler", DEFAULT_PIPELINE_PARAMS["sampler"])
    raw_sched = pipe_sec.get("scheduler", DEFAULT_PIPELINE_PARAMS["scheduler"])
    sampler, scheduler = split_sampler_scheduler(raw_sampler, raw_sched)

    params: Dict[str, Any] = {
        "status": "success",
        "source": source,
        "message": f"Параметры загружены из каталога ({source}): «{match_sec.get('canonical_key', '')}»",
        "architecture": safe_str(pipe_sec.get("architecture")) or None,
        "sampler": sampler or DEFAULT_PIPELINE_PARAMS["sampler"],
        "scheduler": scheduler or DEFAULT_PIPELINE_PARAMS["scheduler"],
        "steps": safe_int(pipe_sec.get("steps"), DEFAULT_PIPELINE_PARAMS["steps"], 1, 150),
        "cfg_scale": safe_float(pipe_sec.get("cfg_scale"), DEFAULT_PIPELINE_PARAMS["cfg_scale"], 1.0, 30.0),
        "clip_skip": safe_int(pipe_sec.get("clip_skip"), DEFAULT_PIPELINE_PARAMS["clip_skip"], 1, 12),
        "enable_hr": safe_bool(pipe_sec.get("enable_hr"), False),
        "hr_scale": safe_float(pipe_sec.get("hr_scale"), DEFAULT_PIPELINE_PARAMS["hr_scale"], 1.0, 4.0),
        "hires_upscaler": safe_str(pipe_sec.get("hires_upscaler"), DEFAULT_PIPELINE_PARAMS["hires_upscaler"]),
        "hires_denoise": safe_float(pipe_sec.get("hires_denoise"), DEFAULT_PIPELINE_PARAMS["hires_denoise"], 0.05, 1.0),
        "enable_adetailer": safe_bool(pipe_sec.get("enable_adetailer"), True),
        "routing_description": safe_str(style_sec.get("routing_description") or style_sec.get("description")),
        "llm_guidance": safe_str(style_sec.get("llm_guidance")),
        "style_triggers": safe_str_list(style_sec.get("style_triggers")),
        "trained_words": safe_str_list(style_sec.get("trained_words")),
        "negative_prompt": safe_str(style_sec.get("negative_prompt")) or None,
        "embeddings": list(style_sec.get("embeddings") or []),
    }

    ad_keys = (
        "adetailer_face_model", "adetailer_face_confidence", "adetailer_face_denoise",
        "adetailer_face_inpaint_size", "adetailer_inpaint_size",
        "adetailer_hands_enabled", "adetailer_hands_model", "adetailer_hands_confidence",
        "adetailer_hands_denoise", "adetailer_hands_inpaint_size", "adetailer_hands_mask_blur",
        "adetailer_hands_prompt", "adetailer_hands_negative_prompt",
    )
    for k in ad_keys:
        if k in pipe_sec and pipe_sec[k] is not None:
            params[k] = pipe_sec[k]

    return params


# ---------------------------------------------------------------------------
# Cascade resolver: GitHub Catalog -> Local Catalog -> Civitai -> Fallback
# ---------------------------------------------------------------------------

def resolve_model_presets(
    checkpoint_name: str,
    architecture: Optional[str] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    catalog_url: Optional[str] = None,
    catalog_path: Optional[Path] = None,
    allow_github: bool = True,
    autofill_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolves generation presets in strict cascade order:
    1. GitHub catalog (short timeout, optional)
    2. Local catalog (data/model_presets_catalog.json)
    3. Civitai API (SHA-256 -> name search)
    4. Explicit fallback (never masks empty_meta or catalog_error as success)
    """
    from services.civitai_service import (
        compute_model_sha256,
        fetch_civitai_api_parameters,
        resolve_checkpoint_file,
    )

    aid = autofill_id or uuid.uuid4().hex[:8]
    candidates = normalize_model_search_candidates(checkpoint_name)
    detected_arch = architecture or detect_architecture_from_name(checkpoint_name)
    logger.info(
        f"[AutoFill:{aid}] NORMALIZE checkpoint='{checkpoint_name}' "
        f"arch={detected_arch or 'unknown'} candidates={candidates}"
    )

    ckpt_file = resolve_checkpoint_file(checkpoint_name)
    model_hash = None
    if ckpt_file and ckpt_file.exists():
        logger.info(f"[AutoFill:{aid}] HASH_START file_found=True path='{ckpt_file.name}'")
        model_hash = compute_model_sha256(ckpt_file, max_mb=None, cancel_check=cancel_check)
        if cancel_check and cancel_check():
            logger.info(f"[AutoFill:{aid}] HASH_CANCELLED checkpoint='{checkpoint_name}'")
            return {
                **DEFAULT_PIPELINE_PARAMS,
                "autofill_id": aid,
                "status": "cancelled",
                "source": "cancelled",
                "message": "Операция отменена.",
            }
        if model_hash:
            logger.info(f"[AutoFill:{aid}] HASH_OK sha256_prefix={model_hash[:10]}")
        else:
            logger.warning(f"[AutoFill:{aid}] HASH_FAILED file='{ckpt_file.name}'")
    else:
        logger.info(f"[AutoFill:{aid}] HASH_SKIPPED file_found=False checkpoint='{checkpoint_name}'")

    # 1. GitHub catalog
    if allow_github:
        gh_cat, gh_err = fetch_github_catalog(url=catalog_url, timeout=3.0)
        if gh_cat is not None:
            entry = match_catalog_entry(gh_cat, checkpoint_name, sha256=model_hash, architecture=architecture)
            if entry is not None:
                res = catalog_entry_to_params(entry, source="github_catalog")
                res["autofill_id"] = aid
                logger.info(
                    f"[AutoFill:{aid}] GITHUB_CATALOG status=hit key='{(entry.get('match') or {}).get('canonical_key', '')}'"
                )
                return res
            logger.info(f"[AutoFill:{aid}] GITHUB_CATALOG status=miss checkpoint='{checkpoint_name}'")
        else:
            logger.info(f"[AutoFill:{aid}] GITHUB_CATALOG status=error reason='{gh_err}'")
    else:
        logger.info(f"[AutoFill:{aid}] GITHUB_CATALOG status=skipped")

    if cancel_check and cancel_check():
        logger.info(f"[AutoFill:{aid}] CANCELLED before local catalog")
        return {**DEFAULT_PIPELINE_PARAMS, "autofill_id": aid, "status": "cancelled", "source": "cancelled", "message": "Операция отменена."}

    # 2. Local catalog
    local_cat, local_err = load_local_catalog(catalog_path=catalog_path)
    if local_cat is not None:
        entry = match_catalog_entry(local_cat, checkpoint_name, sha256=model_hash, architecture=architecture)
        if entry is not None:
            res = catalog_entry_to_params(entry, source="local_catalog")
            res["autofill_id"] = aid
            logger.info(
                f"[AutoFill:{aid}] LOCAL_CATALOG status=hit key='{(entry.get('match') or {}).get('canonical_key', '')}'"
            )
            return res
        logger.info(f"[AutoFill:{aid}] LOCAL_CATALOG status=miss checkpoint='{checkpoint_name}'")
    elif local_err:
        logger.warning(f"[AutoFill:{aid}] LOCAL_CATALOG status=error reason='{local_err}'")
    else:
        logger.info(f"[AutoFill:{aid}] LOCAL_CATALOG status=missing_file")

    if cancel_check and cancel_check():
        logger.info(f"[AutoFill:{aid}] CANCELLED before Civitai API")
        return {**DEFAULT_PIPELINE_PARAMS, "autofill_id": aid, "status": "cancelled", "source": "cancelled", "message": "Операция отменена."}

    # 3. Civitai API
    civitai_res = fetch_civitai_api_parameters(
        checkpoint_name=checkpoint_name,
        model_hash=model_hash,
        cancel_check=cancel_check,
        autofill_id=aid,
    )
    civitai_res["autofill_id"] = aid
    if civitai_res.get("status") in ("success", "empty_meta"):
        if civitai_res.get("status") == "empty_meta":
            logger.info(
                f"[AutoFill:{aid}] FALLBACK status=empty_meta source=civitai reason='{civitai_res.get('message', '')}'"
            )
        return civitai_res

    # 4. Explicit fallback (or diagnostic catalog_error if local catalog was broken)
    if local_err:
        msg = f"{local_err}. Модель «{checkpoint_name}» также не найдена на Civitai."
        logger.warning(f"[AutoFill:{aid}] FALLBACK status=catalog_error source=fallback reason='{msg}'")
        return {
            **DEFAULT_PIPELINE_PARAMS,
            "autofill_id": aid,
            "status": "catalog_error",
            "source": "fallback",
            "message": msg,
        }

    msg = f"Модель «{checkpoint_name}» не найдена ни в каталоге, ни в базе Civitai."
    logger.info(f"[AutoFill:{aid}] FALLBACK status=not_found source=fallback reason='{msg}'")
    return {
        **DEFAULT_PIPELINE_PARAMS,
        "autofill_id": aid,
        "status": "not_found",
        "source": "fallback",
        "message": msg,
    }
