"""
services/embedding_service.py
=============================
Safe extraction, local detection, exact Civitai TextualInversion search,
and atomic downloading (.pt / .safetensors) for prompt embeddings.
"""

import hashlib
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from config import APP_VERSION
from services.asset_validator import AssetValidator
from services.model_catalog_service import (
    check_arch_compatibility,
    detect_architecture_from_name,
    safe_str,
)

logger = logging.getLogger("NeonRoute.EmbeddingService")

CIVITAI_API_BASE = "https://civitai.com/api/v1"
ALLOWED_EMBEDDING_EXTENSIONS = (".safetensors", ".pt")
LOCAL_EMBEDDING_EXTENSIONS = (".pt", ".safetensors", ".bin")

_HEADERS = {
    "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) NeonRoute/{APP_VERSION}",
    "Accept": "application/json",
}

_GENERIC_STOP_WORDS = {
    "bad", "easy", "fast", "low", "high", "worst", "best", "quality", "blurry",
    "3d", "cgi", "render", "sketch", "cartoon", "drawing", "anime", "photo",
    "photorealistic", "realistic", "semi-realistic", "portrait", "headshot",
    "closeup", "close-up", "cropped", "watermark", "text", "signature",
    "username", "logo", "artist", "patreon", "nsfw", "sfw", "monochrome",
    "greyscale", "grayscale", "ugly", "duplicate", "morbid", "mutilated",
    "mutation", "deformed", "dehydrated", "disfigured", "cloned", "fused",
    "interlocked", "doll", "clothes", "dress", "armor", "skirt", "gloves",
    "boots", "fabric", "tongue", "child", "childish", "toddler", "zombie",
    "thighs", "hands", "fingers", "limbs", "arms", "legs", "skin", "eyes",
    "face", "neck", "feet", "noise", "noisy", "jpeg", "artifacts", "negative",
    "fullbody", "illustration", "painting", "2d", "3d_model", "blender",
}


def normalize_embedding_key(name: str) -> str:
    """
    Normalizes an embedding name for exact comparison while preserving version markers
    (e.g. 'bad-hands-5' -> 'badhands5', 'badhandv4' -> 'badhandv4', 'FastNegativeV2' -> 'fastnegativev2').
    """
    if not name:
        return ""
    stem = Path(str(name).strip()).name
    stem = re.sub(r"\.(safetensors|pt|bin)$", "", stem, flags=re.IGNORECASE)
    return re.sub(r"[\s_\-]+", "", stem).lower()


def _clean_prompt_token(raw_token: str) -> str:
    token = raw_token.strip()
    if not token:
        return ""
    # Strip outer () or [] brackets iteratively
    while len(token) >= 2 and (
        (token[0] == "(" and token[-1] == ")")
        or (token[0] == "[" and token[-1] == "]")
    ):
        token = token[1:-1].strip()
    # Strip trailing weight like :1.2 or :0.8
    token = re.sub(r":\s*-?\d+(?:\.\d+)?\s*$", "", token).strip()
    # Strip any remaining brackets
    token = token.strip("()[]\"' ")
    return token


def _is_likely_embedding_token(token: str) -> bool:
    if not token or len(token) < 3:
        return False
    low = token.lower()
    if low in _GENERIC_STOP_WORDS:
        return False
    # Ignore multi-word ordinary descriptive tags (contain spaces)
    if " " in token:
        return False
    # Must contain alphanumeric characters
    if not re.search(r"[a-zA-Z0-9]", token):
        return False
    # Strong embedding indicators: hyphen/underscore, digits, or CamelCase/negative suffix
    has_sep = any(c in token for c in ("_", "-"))
    has_digit = any(c.isdigit() for c in token)
    has_camel = bool(re.search(r"[a-z][A-Z]", token))
    has_neg_marker = any(m in low for m in ("negative", "badhand", "baddream", "embedding", "ti_"))
    return has_sep or has_digit or has_camel or has_neg_marker


def extract_embedding_candidates(
    negative_prompt: Optional[str],
    catalog_embeddings: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Extracts embedding candidates from catalog metadata (priority) and negative_prompt tokens.
    Deduplicates by normalized key.
    """
    candidates: List[Dict[str, Any]] = []
    seen = set()

    for entry in (catalog_embeddings or []):
        if not isinstance(entry, dict):
            continue
        name = safe_str(entry.get("name") or entry.get("filename"))
        if not name:
            continue
        key = normalize_embedding_key(name)
        if key and key not in seen:
            seen.add(key)
            candidates.append({
                "name": name,
                "key": key,
                "architecture": safe_str(entry.get("architecture")) or None,
                "civitai_model_id": entry.get("civitai_model_id"),
                "civitai_version_id": entry.get("civitai_version_id"),
                "filename": safe_str(entry.get("filename")) or None,
                "source": "catalog",
            })

    if negative_prompt:
        raw_parts = re.split(r"[,\n]+", str(negative_prompt))
        for raw in raw_parts:
            token = _clean_prompt_token(raw)
            if not _is_likely_embedding_token(token):
                continue
            key = normalize_embedding_key(token)
            if key and key not in seen:
                seen.add(key)
                candidates.append({
                    "name": token,
                    "key": key,
                    "architecture": None,
                    "civitai_model_id": None,
                    "civitai_version_id": None,
                    "filename": None,
                    "source": "negative_prompt",
                })

    return candidates


def find_local_embedding(
    name: str,
    search_dirs: Optional[List[Path]] = None,
    installed_names: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Checks if an embedding is already installed locally (by Forge API names or disk files).
    """
    target_key = normalize_embedding_key(name)
    if not target_key:
        return None

    for inst in (installed_names or []):
        if normalize_embedding_key(inst) == target_key:
            logger.info(f"[EmbeddingService] Local match via Forge API/cache: '{name}' (key='{target_key}')")
            return {"name": name, "path": None, "status": "installed_local"}

    if search_dirs is not None:
        dirs = list(search_dirs)
    else:
        dirs = list(AssetValidator.resolve_embeddings_dirs())
        try:
            target_dir = AssetValidator.get_target_embeddings_dir().resolve()
            if target_dir not in dirs:
                dirs.insert(0, target_dir)
        except Exception:
            pass

    logger.info(
        f"[EmbeddingService] Checking local disk for '{name}' (key='{target_key}'), "
        f"dirs={[str(d) for d in dirs]}"
    )
    for d in dirs:
        try:
            resolved_d = Path(d).resolve()
            if not resolved_d.is_dir():
                logger.debug(f"[EmbeddingService] Dir does not exist: '{resolved_d}'")
                continue
            found_files = 0
            for item in resolved_d.rglob("*"):
                if item.is_file() and item.suffix.lower() in LOCAL_EMBEDDING_EXTENSIONS:
                    found_files += 1
                    item_key = normalize_embedding_key(item.stem)
                    if item_key == target_key:
                        logger.info(f"[EmbeddingService] Local disk HIT: '{name}' -> '{item.resolve()}'")
                        return {"name": name, "path": str(item.resolve()), "status": "installed_local"}
            logger.info(f"[EmbeddingService] Dir scanned '{resolved_d}': {found_files} embedding files, no match for '{target_key}'")
        except Exception as e:
            logger.warning(f"[EmbeddingService] Error scanning dir '{d}': {e}")
    return None


def _pick_embedding_file(files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Selects a safe .safetensors or .pt file from Civitai version files (preferring .safetensors)."""
    valid: List[Dict[str, Any]] = []
    for f in (files or []):
        if not isinstance(f, dict):
            continue
        fname = safe_str(f.get("name"))
        dl_url = safe_str(f.get("downloadUrl"))
        if not fname or not dl_url:
            continue
        if Path(fname).suffix.lower() in ALLOWED_EMBEDDING_EXTENSIONS:
            valid.append(f)
    if not valid:
        return None
    for f in valid:
        if Path(f["name"]).suffix.lower() == ".safetensors":
            return f
    return valid[0]


def _build_civitai_candidate(
    model_name: str,
    ver: Dict[str, Any],
    file_obj: Dict[str, Any],
    source: str = "civitai",
) -> Dict[str, Any]:
    hashes = file_obj.get("hashes") or {}
    sha256 = safe_str(hashes.get("SHA256") or hashes.get("sha256")) or None
    return {
        "name": model_name,
        "version_name": safe_str(ver.get("name"), "v1.0"),
        "architecture": detect_architecture_from_name(ver.get("baseModel", "")),
        "base_model": safe_str(ver.get("baseModel")),
        "filename": safe_str(file_obj.get("name")),
        "download_url": safe_str(file_obj.get("downloadUrl")),
        "sha256": sha256,
        "size_kb": float(file_obj.get("sizeKB") or 0.0),
        "civitai_version_id": ver.get("id"),
        "source": source,
    }


def search_civitai_embedding(
    name: str,
    architecture: Optional[str] = None,
    catalog_meta: Optional[Dict[str, Any]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    Searches Civitai strictly for TextualInversion with exact canonical key matching
    and architecture compatibility filtering.
    Returns status: 'found' | 'ambiguous' | 'not_found' | 'cancelled' | 'error'.
    """
    if cancel_check and cancel_check():
        return {"status": "cancelled", "name": name, "candidates": []}

    target_key = normalize_embedding_key(name)
    cat_meta = catalog_meta or {}
    target_arch = safe_str(cat_meta.get("architecture")) or architecture
    expected_fname_key = normalize_embedding_key(safe_str(cat_meta.get("filename")))

    # 1. Direct lookup if catalog specifies civitai_version_id
    ver_id = cat_meta.get("civitai_version_id")
    if ver_id:
        try:
            url = f"{CIVITAI_API_BASE}/model-versions/{ver_id}"
            logger.info(f"[EmbeddingService] Direct version lookup for '{name}' id={ver_id}")
            resp = requests.get(url, headers=_HEADERS, timeout=10.0)
            if resp.status_code == 200:
                ver_data = resp.json()
                model_obj = ver_data.get("model") or {}
                m_type = safe_str(model_obj.get("type"))
                if not m_type or m_type.lower() == "textualinversion":
                    ver_arch = detect_architecture_from_name(ver_data.get("baseModel", ""))
                    if check_arch_compatibility(target_arch, ver_arch):
                        f_obj = _pick_embedding_file(ver_data.get("files") or [])
                        if f_obj:
                            cand = _build_civitai_candidate(
                                safe_str(model_obj.get("name")) or name,
                                ver_data,
                                f_obj,
                                source="catalog_civitai",
                            )
                            return {"status": "found", "name": name, "candidate": cand, "candidates": [cand]}
        except Exception as e:
            logger.warning(f"[EmbeddingService] Direct version lookup failed for '{name}': {e}")

    # 2. Strict TextualInversion search on Civitai API
    query_str = re.sub(r"[_]+", " ", Path(name).stem).strip()
    try:
        url = f"{CIVITAI_API_BASE}/models"
        params = {"query": query_str, "types": "TextualInversion", "limit": 10}
        logger.info(f"[EmbeddingService] Searching Civitai TextualInversion query='{query_str}' arch={target_arch or 'any'}")
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=10.0)
        if resp.status_code != 200:
            return {"status": "error", "name": name, "message": f"HTTP {resp.status_code}", "candidates": []}
        items = (resp.json() or {}).get("items") or []
    except Exception as e:
        logger.warning(f"[EmbeddingService] Search failed for '{name}': {e}")
        return {"status": "error", "name": name, "message": str(e), "candidates": []}

    exact_matches: List[Dict[str, Any]] = []
    for item in items:
        if cancel_check and cancel_check():
            return {"status": "cancelled", "name": name, "candidates": []}
        if not isinstance(item, dict):
            continue
        item_type = safe_str(item.get("type")).lower()
        if item_type and item_type != "textualinversion":
            continue

        model_title = safe_str(item.get("name"))
        model_key = normalize_embedding_key(model_title)

        for ver in (item.get("modelVersions") or []):
            ver_arch = detect_architecture_from_name(ver.get("baseModel", ""))
            if not check_arch_compatibility(target_arch, ver_arch):
                continue

            ver_key = normalize_embedding_key(safe_str(ver.get("name")))
            f_obj = _pick_embedding_file(ver.get("files") or [])
            if not f_obj:
                continue
            file_key = normalize_embedding_key(safe_str(f_obj.get("name")))

            is_exact = (
                model_key == target_key
                or ver_key == target_key
                or file_key == target_key
                or (expected_fname_key and file_key == expected_fname_key)
            )
            if is_exact:
                exact_matches.append(_build_civitai_candidate(model_title, ver, f_obj, source="civitai"))
                break  # one best version per model item

    if not exact_matches:
        logger.info(f"[EmbeddingService] No exact TextualInversion match for '{name}' (arch={target_arch})")
        return {"status": "not_found", "name": name, "candidates": []}
    if len(exact_matches) == 1:
        logger.info(f"[EmbeddingService] Exact match for '{name}': {exact_matches[0]['filename']}")
        return {"status": "found", "name": name, "candidate": exact_matches[0], "candidates": exact_matches}

    logger.info(f"[EmbeddingService] Ambiguous matches ({len(exact_matches)}) for '{name}'")
    return {"status": "ambiguous", "name": name, "candidates": exact_matches}


def resolve_missing_embeddings(
    negative_prompt: Optional[str],
    architecture: Optional[str] = None,
    catalog_embeddings: Optional[List[Dict[str, Any]]] = None,
    installed_names: Optional[List[str]] = None,
    search_dirs: Optional[List[Path]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    Extracts embedding candidates, verifies local presence first, and searches Civitai
    only for missing embeddings.
    """
    candidates = extract_embedding_candidates(negative_prompt, catalog_embeddings=catalog_embeddings)
    installed: List[Dict[str, Any]] = []
    ready_to_download: List[Dict[str, Any]] = []
    ambiguous: List[Dict[str, Any]] = []
    not_found: List[str] = []

    logger.info(f"[EmbeddingService] Checking {len(candidates)} embedding candidates (arch={architecture or 'any'})")
    for cand in candidates:
        if cancel_check and cancel_check():
            break
        local_hit = find_local_embedding(cand["name"], search_dirs=search_dirs, installed_names=installed_names)
        if local_hit:
            logger.info(f"[EmbeddingService] Local hit: '{cand['name']}' -> {local_hit.get('path') or 'Forge API'}")
            installed.append(local_hit)
            continue

        res = search_civitai_embedding(
            cand["name"],
            architecture=cand.get("architecture") or architecture,
            catalog_meta=cand if cand.get("source") == "catalog" else None,
            cancel_check=cancel_check,
        )
        st = res.get("status")
        if st == "found":
            ready_to_download.append(res["candidate"])
        elif st == "ambiguous":
            ambiguous.append({"name": cand["name"], "candidates": res["candidates"]})
        elif st == "cancelled":
            break
        else:
            not_found.append(cand["name"])

    return {
        "installed": installed,
        "ready_to_download": ready_to_download,
        "ambiguous": ambiguous,
        "not_found": not_found,
    }


def download_embedding_file(
    download_url: str,
    dest_path: Path,
    expected_sha256: Optional[str] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> Path:
    """
    Safely downloads an embedding (.safetensors or .pt) to a .part file,
    validates host whitelist (*.civitai.com), size, and optional SHA-256,
    and atomically moves it to dest_path.
    """
    dest = Path(dest_path)
    if dest.suffix.lower() not in ALLOWED_EMBEDDING_EXTENSIONS:
        raise ValueError(
            f"Недопустимое расширение эмбеддинга '{dest.suffix}'. Разрешены только .safetensors и .pt."
        )

    parsed_url = urllib.parse.urlparse(download_url)
    host = (parsed_url.hostname or "").lower()
    if not (host == "civitai.com" or host.endswith(".civitai.com")):
        raise ValueError(f"SSRF защита: домен '{host}' не входит в белый список (*.civitai.com).")

    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_part = dest.with_name(f"{dest.name}.part")
    expected_hash = expected_sha256.strip().lower() if expected_sha256 else None

    try:
        with requests.get(download_url, headers=_HEADERS, stream=True, timeout=20.0, allow_redirects=True) as resp:
            if cancel_check and cancel_check():
                raise InterruptedError("Загрузка отменена.")
            resp.raise_for_status()

            final_host = (urllib.parse.urlparse(resp.url).hostname or "").lower()
            if not (final_host == "civitai.com" or final_host.endswith(".civitai.com")):
                raise ValueError(f"SSRF защита: итоговый хост '{final_host}' не входит в белый список (*.civitai.com).")

            total_size = int(resp.headers.get("content-length") or 0)
            downloaded = 0
            hasher = hashlib.sha256()

            with open(temp_part, "wb") as f:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if cancel_check and cancel_check():
                        raise InterruptedError("Загрузка отменена.")
                    if chunk:
                        f.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            progress_callback(int(downloaded * 100 / total_size))

        if downloaded == 0 or (total_size > 0 and downloaded != total_size):
            raise ValueError(f"Неполная загрузка файла {dest.name}: получено {downloaded} из {total_size} байт.")

        if expected_hash:
            actual_hash = hasher.hexdigest().lower()
            if actual_hash != expected_hash:
                raise ValueError(f"SHA-256 не совпадает для {dest.name}: ожидалось {expected_hash}, получено {actual_hash}.")

        temp_part.replace(dest)
        logger.info(f"[EmbeddingService] Downloaded embedding -> {dest}")
        return dest
    except Exception:
        temp_part.unlink(missing_ok=True)
        raise
