"""
services/integrity_service.py
=============================
Единый источник правды (SSOT) для:
- периметра целостности и списка исключений NeonRoute;
- потокового расчёта SHA-256 и канонического aggregate_hash;
- сборки, чтения и атомарной записи release_manifest.json;
- фоновой warn-only проверки локальной целостности;
- загрузки удалённого манифеста с GitHub Releases и 3-way анализа (B, L, R);
- выборочного скачивания и безопасного применения обновлений через TemporaryDirectory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from utils.file_utils import atomic_write_json

logger = logging.getLogger("NeonRoute.Integrity")

MANIFEST_FILENAME = "release_manifest.json"
SCHEMA_VERSION = 1
GITHUB_REPO = "ZabotaOblizkih/NeonRoute"
TARGET_BRANCH = "ui-fluent-redesign"
CHUNK_SIZE = 131072  # 128 KB

# ---------------------------------------------------------------------------
# Единый периметр целостности и исключения
# ---------------------------------------------------------------------------

RELEASE_ROOT_FILES: Tuple[str, ...] = (
    "main.py",
    "run_mobile_server.py",
    "config.py",
    "server_config.ini",
    "requirements.txt",
    "run.bat",
    "run_debug.bat",
    "run_llama_server.bat",
    "install.bat",
    "README.md",
    "Agentrules.md",
    "ARCHITECTURE.md",
    "pytest.ini",
)

# Пользовательские / изменяемые файлы не входят в периметр контроля целостности
INTEGRITY_ROOT_FILES: Tuple[str, ...] = tuple(
    f for f in RELEASE_ROOT_FILES if f not in ("server_config.ini",)
)

INTEGRITY_SOURCE_DIRS: Tuple[str, ...] = (
    "core",
    "services",
    "ui",
    "mobile_server",
    "utils",
    "scripts",
    "audio",
    "assets",
)

# Дополнительные каталоги релиза, которые копируются в сборку (включая lorebooks/exports),
# но пользовательские экспортированные файлы не должны вызывать ложных срабатываний.
RELEASE_EXTRA_DIRS: Tuple[str, ...] = (
    "lorebooks",
    "exports",
)

INTEGRITY_DATA_FILES: Tuple[str, ...] = (
    "data/model_presets_catalog.json",
    "data/lora_registry.json",
)

INTEGRITY_DATA_DIRS: Tuple[str, ...] = (
    "data/skill_icons",
)

INTEGRITY_AVATAR_FILES: Tuple[str, ...] = (
    "avatars/assistant.jpg",
    "avatars/engenier.jpg",
    "avatars/RP_chat.jpg",
)

EXCLUDED_TOP_DIRS = {
    "history",
    "outputs",
    "logs",
    "workspace",
    "backgrounds",
    "knowledge_base",
    "characters",
    "exports",
    "lorebooks",
    "bin",
    "models",
    "runtime",
    "wheels",
    "dist",
    "tests",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".gemini",
}

EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".git",
}

EXCLUDED_EXACT_FILES = {
    "config.json",
    "server_config.ini",
    MANIFEST_FILENAME,
    "data/art_director_models.json",
    "data/user_personas.json",
    "data/models_cache.json",
    "docs/assets/splash.gif",
}

EXCLUDED_PREFIXES = (
    "data/memory.db",
    "data/cache/",
    "data/kv_slots/",
    "data/vector_memory/",
)

EXCLUDED_EXTENSIONS = (".pyc", ".pyo", ".log", ".tmp", ".bak")


def normalize_rel_path(rel_path: str) -> str:
    """Нормализует относительный путь в POSIX-формат без ведущих ./ и /."""
    norm = str(rel_path).replace("\\", "/").strip()
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.lstrip("/")


def is_path_in_integrity_perimeter(rel_path: str) -> bool:
    """
    Возвращает True, если путь входит в периметр контроля целостности
    и не попадает ни под одно правило исключений.
    """
    norm = normalize_rel_path(rel_path)
    if not norm:
        return False

    lower = norm.lower()
    parts = [p for p in norm.split("/") if p]
    if not parts:
        return False

    # Защита от self-hashing и небезопасных путей
    if ".." in parts:
        return False

    # Исключённые точные файлы и префиксы
    if lower in EXCLUDED_EXACT_FILES or norm in EXCLUDED_EXACT_FILES:
        return False

    for pref in EXCLUDED_PREFIXES:
        if lower.startswith(pref):
            return False

    # Исключённые директории верхнего уровня и вложенные кэши
    if parts[0].lower() in EXCLUDED_TOP_DIRS:
        return False
    if any(part in EXCLUDED_DIR_NAMES for part in parts):
        return False

    # Исключённые имена/расширения файлов
    filename = parts[-1].lower()
    if filename.startswith("hotfix_"):
        return False
    if any(filename.endswith(ext) for ext in EXCLUDED_EXTENSIONS):
        return False

    # Включаемые пути
    if len(parts) == 1:
        return norm in INTEGRITY_ROOT_FILES

    top = parts[0]
    if top in INTEGRITY_SOURCE_DIRS:
        return True
    if norm in INTEGRITY_DATA_FILES:
        return True
    if any(norm.startswith(d + "/") for d in INTEGRITY_DATA_DIRS):
        return True
    if norm in INTEGRITY_AVATAR_FILES:
        return True

    return False


def collect_integrity_files(root_dir: Path) -> List[Tuple[str, Path]]:
    """
    Сканирует root_dir и возвращает отсортированный по POSIX-пути список
    (rel_path, abs_path) для всех файлов, входящих в периметр целостности.
    """
    root = Path(root_dir).resolve()
    collected: List[Tuple[str, Path]] = []
    if not root.is_dir():
        return collected

    for dirpath, dirnames, filenames in os.walk(root):
        cur_dir = Path(dirpath)
        rel_dir = cur_dir.relative_to(root)
        rel_dir_posix = "" if str(rel_dir) == "." else normalize_rel_path(str(rel_dir))

        # Отсекаем исключённые поддиректории на лету
        pruned: List[str] = []
        for d in dirnames:
            if d in EXCLUDED_DIR_NAMES:
                continue
            cand_rel = f"{rel_dir_posix}/{d}" if rel_dir_posix else d
            top = cand_rel.split("/")[0].lower()
            if top in EXCLUDED_TOP_DIRS:
                continue
            if cand_rel.lower().startswith(("data/cache", "data/kv_slots", "data/vector_memory")):
                continue
            if d.startswith("hotfix_"):
                continue
            pruned.append(d)
        dirnames[:] = sorted(pruned)

        for fname in sorted(filenames):
            rel = f"{rel_dir_posix}/{fname}" if rel_dir_posix else fname
            if is_path_in_integrity_perimeter(rel):
                collected.append((rel, cur_dir / fname))

    collected.sort(key=lambda item: item[0])
    return collected


# ---------------------------------------------------------------------------
# Потоковое хэширование и канонический aggregate_hash
# ---------------------------------------------------------------------------

def compute_file_sha256(file_path: Path, chunk_size: int = CHUNK_SIZE) -> Optional[str]:
    """
    Вычисляет SHA-256 файла в бинарном режиме (rb) чанками.
    Возвращает hex-строку в нижнем регистре или None при отсутствии/ошибке чтения.
    """
    try:
        p = Path(file_path)
        if not p.is_file():
            return None
        hasher = hashlib.sha256()
        with open(p, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest().lower()
    except Exception:
        return None


def compute_aggregate_hash(files_map: Dict[str, Dict[str, Any]]) -> str:
    """
    Вычисляет детерминированный aggregate_hash от канонической UTF-8 строки:
    отсортированные нормализованные POSIX-пути вида f"{rel_path}:{sha256}:{size}\\n".
    Исключает release_manifest.json (защита от самохэширования).
    """
    normalized_entries: List[Tuple[str, str, int]] = []
    for raw_path, meta in files_map.items():
        norm_path = normalize_rel_path(raw_path)
        if not norm_path or norm_path == MANIFEST_FILENAME:
            continue
        sha = str(meta.get("sha256", "")).strip().lower()
        size = int(meta.get("size", 0))
        normalized_entries.append((norm_path, sha, size))

    normalized_entries.sort(key=lambda x: x[0])
    canonical = "".join(f"{path}:{sha}:{size}\n" for path, sha, size in normalized_entries)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest().lower()


# ---------------------------------------------------------------------------
# Манифест: сборка, чтение, запись
# ---------------------------------------------------------------------------

def build_release_manifest(
    root_dir: Path,
    app_version: str,
    target_branch: str = TARGET_BRANCH,
) -> Dict[str, Any]:
    """Формирует словарь release_manifest.json для собранного каталога релиза."""
    files_map: Dict[str, Dict[str, Any]] = {}
    for rel_path, abs_path in collect_integrity_files(root_dir):
        if rel_path == MANIFEST_FILENAME:
            continue
        sha = compute_file_sha256(abs_path)
        if sha is None:
            continue
        size = abs_path.stat().st_size
        files_map[rel_path] = {
            "sha256": sha,
            "size": size,
        }

    agg_hash = compute_aggregate_hash(files_map)
    return {
        "schema_version": SCHEMA_VERSION,
        "app_version": str(app_version),
        "tag": f"v{app_version}",
        "target_branch": target_branch,
        "aggregate_hash": agg_hash,
        "files": files_map,
    }


def load_local_manifest(base_dir: Path) -> Optional[Dict[str, Any]]:
    """Безопасно загружает локальный release_manifest.json, возвращает None при отсутствии/сбое."""
    manifest_path = Path(base_dir) / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
            return None
        return data
    except Exception as e:
        logger.warning(f"[Integrity] Не удалось прочитать {MANIFEST_FILENAME}: {e}")
        return None


def save_local_manifest(base_dir: Path, manifest: Dict[str, Any]) -> bool:
    """Атомарно сохраняет release_manifest.json в base_dir."""
    manifest_path = Path(base_dir) / MANIFEST_FILENAME
    return atomic_write_json(manifest_path, manifest)


# ---------------------------------------------------------------------------
# Фоновая warn-only проверка локальной целостности
# ---------------------------------------------------------------------------

@dataclass
class StartupIntegrityReport:
    manifest_present: bool
    app_version: Optional[str] = None
    expected_aggregate_hash: Optional[str] = None
    checked_count: int = 0
    mismatches: List[Tuple[str, str, str]] = field(default_factory=list)  # (rel_path, actual, expected)


_LAST_INTEGRITY_REPORT: Optional[StartupIntegrityReport] = None


def get_last_integrity_report() -> Optional[StartupIntegrityReport]:
    return _LAST_INTEGRITY_REPORT


def set_last_integrity_report(report: StartupIntegrityReport) -> None:
    global _LAST_INTEGRITY_REPORT
    _LAST_INTEGRITY_REPORT = report


def verify_local_integrity(base_dir: Path, log_warnings: bool = True) -> StartupIntegrityReport:
    """
    Выполняет warn-only проверку локальных файлов против release_manifest.json.
    Никогда не бросает исключений и не прерывает запуск приложения.
    """
    try:
        base = Path(base_dir).resolve()
        manifest = load_local_manifest(base)
        if manifest is None:
            if log_warnings:
                logger.info(
                    "[Integrity] Локальный release_manifest.json отсутствует — "
                    "базовый эталон B недоступен (warn-only пропуск)."
                )
            report = StartupIntegrityReport(manifest_present=False)
            set_last_integrity_report(report)
            return report

        files_section = manifest.get("files", {})
        mismatches: List[Tuple[str, str, str]] = []
        checked = 0

        for raw_rel, meta in sorted(files_section.items()):
            rel_path = normalize_rel_path(raw_rel)
            if not is_path_in_integrity_perimeter(rel_path):
                continue
            if not isinstance(meta, dict):
                continue
            expected_sha = str(meta.get("sha256", "")).strip().lower()
            if not expected_sha:
                continue

            checked += 1
            actual_sha = compute_file_sha256(base / rel_path)
            if actual_sha is None:
                mismatches.append((rel_path, "<missing>", expected_sha))
                if log_warnings:
                    logger.warning(
                        f"[HASH MISMATCH] {rel_path}: current=<missing>, expected={expected_sha}"
                    )
            elif actual_sha != expected_sha:
                mismatches.append((rel_path, actual_sha, expected_sha))
                if log_warnings:
                    logger.warning(
                        f"[HASH MISMATCH] {rel_path}: current={actual_sha}, expected={expected_sha}"
                    )

        report = StartupIntegrityReport(
            manifest_present=True,
            app_version=manifest.get("app_version"),
            expected_aggregate_hash=manifest.get("aggregate_hash"),
            checked_count=checked,
            mismatches=mismatches,
        )
        if log_warnings:
            if not mismatches:
                logger.info(
                    f"[Integrity] Проверка целостности пройдена: {checked} файлов соответствуют эталону."
                )
            else:
                logger.warning(
                    f"[Integrity] Обнаружены расхождения целостности: {len(mismatches)} из {checked} файлов."
                )
        set_last_integrity_report(report)
        return report
    except Exception as e:
        if log_warnings:
            logger.warning(f"[Integrity] Ошибка фоновой проверки целостности (игнорируется): {e}")
        report = StartupIntegrityReport(manifest_present=False)
        set_last_integrity_report(report)
        return report


# ---------------------------------------------------------------------------
# Загрузка удалённого манифеста и 3-way анализ (B, L, R)
# ---------------------------------------------------------------------------

class FileUpdateStatus(str, Enum):
    UPDATE_SAFE = "update_safe"          # B есть, L == B, R != B (или L отсутствует) -> отмечен по умолчанию
    CONFLICT_LOCAL = "conflict_local"    # B есть, L есть, L != B, R != B, L != R -> не отмечен по умолчанию
    NO_BASE_DIFF = "no_base_diff"        # B отсутствует, L != R -> не отмечен по умолчанию (требует выбора)
    UP_TO_DATE = "up_to_date"            # L == R


@dataclass
class ThreeWayFileEntry:
    rel_path: str
    status: FileUpdateStatus
    base_sha: Optional[str]
    local_sha: Optional[str]
    remote_sha: str
    remote_size: int
    default_checked: bool


@dataclass
class ThreeWayReport:
    remote_version: str
    remote_tag: str
    remote_aggregate_hash: str
    local_version: Optional[str]
    local_aggregate_hash: Optional[str]
    base_available: bool
    is_up_to_date: bool
    entries: List[ThreeWayFileEntry] = field(default_factory=list)


def fetch_remote_release_manifest(
    repo: str = GITHUB_REPO,
    timeout: float = 8.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
    """
    Запрашивает последний релиз с release_manifest.json на GitHub Releases.
    Возвращает (remote_manifest, release_meta, error_message).
    """
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "NeonRoute-Updater",
    }
    releases_to_check: List[Dict[str, Any]] = []

    try:
        latest_url = f"https://api.github.com/repos/{repo}/releases/latest"
        resp = requests.get(latest_url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            releases_to_check.append(resp.json())
        elif resp.status_code in (403, 429):
            return None, None, f"Превышен лимит запросов GitHub API (HTTP {resp.status_code}). Попробуйте позже."
        elif resp.status_code != 404:
            return None, None, f"Ошибка GitHub API: HTTP {resp.status_code}"
    except requests.RequestException as e:
        return None, None, f"Сетевая ошибка при проверке обновлений: {e}"

    if not releases_to_check:
        try:
            list_url = f"https://api.github.com/repos/{repo}/releases?per_page=5"
            resp = requests.get(list_url, headers=headers, timeout=timeout)
            if resp.status_code == 200 and isinstance(resp.json(), list):
                releases_to_check.extend(resp.json())
            elif resp.status_code in (403, 429):
                return None, None, f"Превышен лимит запросов GitHub API (HTTP {resp.status_code})."
            else:
                return None, None, f"Релизы не найдены (HTTP {resp.status_code})."
        except requests.RequestException as e:
            return None, None, f"Сетевая ошибка при получении списка релизов: {e}"

    chosen_release = None
    manifest_asset = None
    zip_asset = None

    for rel in releases_to_check:
        if rel.get("draft"):
            continue
        assets = rel.get("assets") or []
        m_asset = next((a for a in assets if a.get("name") == MANIFEST_FILENAME), None)
        if m_asset:
            chosen_release = rel
            manifest_asset = m_asset
            zip_asset = next(
                (a for a in assets if str(a.get("name", "")).lower().endswith(".zip")),
                None,
            )
            break

    if not chosen_release or not manifest_asset:
        return None, None, f"В последнем релизе не найден файл {MANIFEST_FILENAME}."

    try:
        dl_url = manifest_asset.get("browser_download_url")
        m_resp = requests.get(dl_url, headers={"User-Agent": "NeonRoute-Updater"}, timeout=timeout)
        if m_resp.status_code != 200:
            return None, None, f"Не удалось скачать {MANIFEST_FILENAME}: HTTP {m_resp.status_code}"
        remote_manifest = m_resp.json()
        if not isinstance(remote_manifest, dict) or not isinstance(remote_manifest.get("files"), dict):
            return None, None, f"Некорректный формат удалённого {MANIFEST_FILENAME}."
    except Exception as e:
        return None, None, f"Ошибка загрузки {MANIFEST_FILENAME}: {e}"

    release_meta = {
        "tag_name": chosen_release.get("tag_name", ""),
        "html_url": chosen_release.get("html_url", ""),
        "body": chosen_release.get("body", "") or "",
        "zip_url": zip_asset.get("browser_download_url") if zip_asset else None,
        "zip_name": zip_asset.get("name") if zip_asset else None,
        "zip_size": zip_asset.get("size", 0) if zip_asset else 0,
    }
    return remote_manifest, release_meta, None


def analyze_three_way_updates(
    base_dir: Path,
    remote_manifest: Dict[str, Any],
    local_manifest: Optional[Dict[str, Any]] = None,
    current_app_version: Optional[str] = None,
) -> ThreeWayReport:
    """
    Выполняет 3-way анализ (B, L, R) для файлов периметра целостности.
    """
    base = Path(base_dir).resolve()
    if local_manifest is None:
        local_manifest = load_local_manifest(base)

    base_available = local_manifest is not None
    local_files = (local_manifest or {}).get("files", {})
    local_agg = (local_manifest or {}).get("aggregate_hash")
    local_ver = (local_manifest or {}).get("app_version") or current_app_version

    remote_ver = str(remote_manifest.get("app_version", ""))
    remote_tag = str(remote_manifest.get("tag", f"v{remote_ver}"))
    remote_agg = str(remote_manifest.get("aggregate_hash", "")).lower()
    remote_files = remote_manifest.get("files", {})

    # Быстрая проверка по aggregate_hash и версии
    if (
        base_available
        and local_agg
        and remote_agg
        and local_agg.lower() == remote_agg
        and (not remote_ver or local_ver == remote_ver)
    ):
        return ThreeWayReport(
            remote_version=remote_ver,
            remote_tag=remote_tag,
            remote_aggregate_hash=remote_agg,
            local_version=local_ver,
            local_aggregate_hash=local_agg,
            base_available=True,
            is_up_to_date=True,
            entries=[],
        )

    entries: List[ThreeWayFileEntry] = []
    for raw_rel, r_meta in sorted(remote_files.items()):
        rel_path = normalize_rel_path(raw_rel)
        if not is_path_in_integrity_perimeter(rel_path):
            continue
        if not isinstance(r_meta, dict):
            continue

        remote_sha = str(r_meta.get("sha256", "")).strip().lower()
        remote_size = int(r_meta.get("size", 0))
        if not remote_sha:
            continue

        b_meta = local_files.get(rel_path) if base_available else None
        base_sha = str(b_meta.get("sha256", "")).strip().lower() if isinstance(b_meta, dict) else None
        if base_sha == "":
            base_sha = None

        local_sha = compute_file_sha256(base / rel_path)

        if local_sha is not None and local_sha == remote_sha:
            continue

        if base_available and base_sha is not None:
            if local_sha is None or local_sha == base_sha:
                status = FileUpdateStatus.UPDATE_SAFE
                default_checked = True
            else:
                # L != B и R != L
                status = FileUpdateStatus.CONFLICT_LOCAL
                default_checked = False
        elif base_available and base_sha is None:
            # Новый файл в релизе (отсутствовал в B)
            if local_sha is None:
                status = FileUpdateStatus.UPDATE_SAFE
                default_checked = True
            else:
                status = FileUpdateStatus.CONFLICT_LOCAL
                default_checked = False
        else:
            # Базовый эталон B недоступен
            status = FileUpdateStatus.NO_BASE_DIFF
            default_checked = False

        entries.append(
            ThreeWayFileEntry(
                rel_path=rel_path,
                status=status,
                base_sha=base_sha,
                local_sha=local_sha,
                remote_sha=remote_sha,
                remote_size=remote_size,
                default_checked=default_checked,
            )
        )

    return ThreeWayReport(
        remote_version=remote_ver,
        remote_tag=remote_tag,
        remote_aggregate_hash=remote_agg,
        local_version=local_ver,
        local_aggregate_hash=local_agg,
        base_available=base_available,
        is_up_to_date=(len(entries) == 0),
        entries=entries,
    )


# ---------------------------------------------------------------------------
# Выборочное скачивание и безопасное применение через TemporaryDirectory
# ---------------------------------------------------------------------------

@dataclass
class UpdateApplyResult:
    applied_files: List[str] = field(default_factory=list)
    locked_files: List[str] = field(default_factory=list)
    missing_in_archive: List[str] = field(default_factory=list)
    backup_files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    manifest_updated: bool = False


def _strip_release_archive_prefix(member_name: str, app_version: str = "") -> Optional[str]:
    """
    Безопасно нормализует путь элемента внутри ZIP-архива, защищает от Zip Slip
    и снимает корневой префикс NeonRoute_v{version}/ (если он присутствует).
    """
    raw = str(member_name).replace("\\", "/")
    if not raw or raw.endswith("/"):
        return None

    # Защита от абсолютных путей, дисков Windows и ..
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
        return None
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts or ".." in parts:
        return None

    expected_prefix = f"NeonRoute_v{app_version}" if app_version else ""
    if expected_prefix and parts[0] == expected_prefix:
        parts = parts[1:]
    elif parts[0].startswith("NeonRoute_v"):
        parts = parts[1:]

    if not parts:
        return None
    return "/".join(parts)


def apply_selective_update_from_zip(
    zip_path: Path,
    base_dir: Path,
    selected_files: List[str],
    remote_manifest: Dict[str, Any],
    entry_map: Optional[Dict[str, ThreeWayFileEntry]] = None,
) -> UpdateApplyResult:
    """
    Распаковывает только выбранные файлы из zip_path во временную директорию
    и безопасно применяет их в base_dir с защитой от Zip Slip, созданием .bak
    для конфликтных файлов и обработкой WinError 32 / PermissionError.
    """
    base = Path(base_dir).resolve()
    app_ver = str(remote_manifest.get("app_version", ""))
    selected_set = {normalize_rel_path(p) for p in selected_files if is_path_in_integrity_perimeter(p)}
    result = UpdateApplyResult()

    if not selected_set:
        return result

    with tempfile.TemporaryDirectory(prefix="neonroute_apply_") as tmp_dir:
        extract_root = Path(tmp_dir) / "extracted"
        extract_root.mkdir(parents=True, exist_ok=True)
        extracted_map: Dict[str, Path] = {}

        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                rel_path = _strip_release_archive_prefix(member.filename, app_version=app_ver)
                if not rel_path or rel_path not in selected_set:
                    continue

                target_tmp = (extract_root / rel_path).resolve()
                # Дополнительная проверка Zip Slip на уровне resolved path
                if not str(target_tmp).startswith(str(extract_root.resolve())):
                    continue

                target_tmp.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member, "r") as src_f, open(target_tmp, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                extracted_map[rel_path] = target_tmp

        for rel_path in sorted(selected_set):
            src_file = extracted_map.get(rel_path)
            if src_file is None or not src_file.is_file():
                result.missing_in_archive.append(rel_path)
                continue

            dest_file = (base / rel_path).resolve()
            if not str(dest_file).startswith(str(base)):
                continue
            dest_file.parent.mkdir(parents=True, exist_ok=True)

            # Резервная копия для конфликтных файлов перед перезаписью
            entry_info = (entry_map or {}).get(rel_path)
            is_conflict = entry_info is not None and entry_info.status in (
                FileUpdateStatus.CONFLICT_LOCAL,
                FileUpdateStatus.NO_BASE_DIFF,
            )
            if is_conflict and dest_file.is_file():
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                bak_path = dest_file.with_name(f"{dest_file.name}.{stamp}.bak")
                try:
                    shutil.copy2(dest_file, bak_path)
                    result.backup_files.append(normalize_rel_path(str(bak_path.relative_to(base))))
                except Exception as bak_err:
                    logger.warning(f"[Updater] Не удалось создать резервную копию для {rel_path}: {bak_err}")

            # Атомарная замена в целевой директории с обработкой WinError 32 / PermissionError
            tmp_dest = dest_file.with_name(f".{dest_file.name}.upd.tmp")
            try:
                shutil.copy2(src_file, tmp_dest)
                os.replace(tmp_dest, dest_file)
                result.applied_files.append(rel_path)
            except PermissionError as perm_err:
                logger.warning(f"[Updater] Файл занят процессом (требует перезапуска): {rel_path} ({perm_err})")
                result.locked_files.append(rel_path)
            except OSError as os_err:
                if getattr(os_err, "winerror", None) in (5, 32):
                    logger.warning(f"[Updater] WinError {os_err.winerror} для {rel_path}: {os_err}")
                    result.locked_files.append(rel_path)
                else:
                    logger.error(f"[Updater] Ошибка записи {rel_path}: {os_err}")
                    result.errors.append(f"{rel_path}: {os_err}")
            finally:
                if tmp_dest.exists():
                    try:
                        tmp_dest.unlink()
                    except OSError:
                        pass

    # Обновление локального release_manifest.json
    if result.applied_files:
        local_manifest = load_local_manifest(base) or {
            "schema_version": SCHEMA_VERSION,
            "app_version": app_ver,
            "tag": remote_manifest.get("tag", f"v{app_ver}"),
            "target_branch": remote_manifest.get("target_branch", TARGET_BRANCH),
            "files": {},
        }
        files_dict = dict(local_manifest.get("files") or {})
        remote_files = remote_manifest.get("files") or {}

        for rel_path in result.applied_files:
            if rel_path in remote_files:
                files_dict[rel_path] = dict(remote_files[rel_path])

        # Если конфликтов/пропусков не осталось, обновляем метаданные релиза полностью
        if not result.locked_files and not result.errors and not result.missing_in_archive:
            local_manifest["app_version"] = app_ver
            local_manifest["tag"] = remote_manifest.get("tag", f"v{app_ver}")
            local_manifest["target_branch"] = remote_manifest.get("target_branch", TARGET_BRANCH)

        local_manifest["files"] = files_dict
        local_manifest["aggregate_hash"] = compute_aggregate_hash(files_dict)
        result.manifest_updated = save_local_manifest(base, local_manifest)

    return result


def download_and_apply_selective_update(
    zip_url: str,
    base_dir: Path,
    selected_files: List[str],
    remote_manifest: Dict[str, Any],
    entry_map: Optional[Dict[str, ThreeWayFileEntry]] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    timeout: float = 30.0,
) -> UpdateApplyResult:
    """
    Скачивает релизный ZIP во временную директорию и применяет выбранные файлы.
    """
    with tempfile.TemporaryDirectory(prefix="neonroute_dl_") as tmp_dir:
        zip_path = Path(tmp_dir) / "release.zip"
        if progress_cb:
            progress_cb(0, 100, "Скачивание архива обновления...")

        with requests.get(
            zip_url,
            headers={"User-Agent": "NeonRoute-Updater"},
            stream=True,
            timeout=timeout,
        ) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"Ошибка загрузки архива: HTTP {resp.status_code}")
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(zip_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=131072):
                    if cancel_check and cancel_check():
                        raise InterruptedError("Обновление отменено пользователем.")
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total, "Скачивание архива обновления...")

        if progress_cb:
            progress_cb(100, 100, "Применение выбранных файлов...")

        return apply_selective_update_from_zip(
            zip_path=zip_path,
            base_dir=base_dir,
            selected_files=selected_files,
            remote_manifest=remote_manifest,
            entry_map=entry_map,
        )
