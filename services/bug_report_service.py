"""
services/bug_report_service.py
==============================
Изолированный сервис диагностики, маскирования чувствительных данных,
сборки отчёта об ошибке и отправки его на Cloudflare Worker (GitHub Issues proxy).

Не зависит от Qt-виджетов и может тестироваться напрямую в pytest.
Сетевой запрос выполняется исключительно при явном вызове send_bug_report().
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("NeonRoute.BugReport")

DEFAULT_BUG_REPORT_ENDPOINT = "https://neonroute-bug-reporter.neonroute-logs-2026.workers.dev"
MAX_TITLE_CHARS = 200
DEFAULT_MAX_BODY_CHARS = 12000
TRUNCATION_MARKER = "\n\n... [Отчёт усечён до лимита символов]"

# Предкомпилированные регулярные выражения для санитизации чувствительных данных
_WIN_USER_PATH_RE = re.compile(
    r"(?i)([a-z]:[\\/]+users[\\/]+)([^\\/\s\"'<>|]+)"
)
_URL_CREDS_RE = re.compile(
    r"(?i)(https?://)([^:@/\s]+):([^@/\s]+)@"
)
_TOKEN_PREFIX_RE = re.compile(
    r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{10,}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{10,}\b"
    r"|\bhf_[A-Za-z0-9]{10,}\b"
    r"|\bsk-[A-Za-z0-9_\-]{12,}\b"
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|api_key|apikey|key|secret|password|auth|access_token)=)([^&\s\"']+)"
)
_BEARER_RE = re.compile(
    r"(?i)\b(authorization\s*:\s*bearer\s+)([^\s\"']+)"
)
_KV_SECRET_RE = re.compile(
    r"(?i)\b(github_token|hf_token|civitai_api_key|api_key|secret_key|password)\b(\s*[=:]\s*[\"']?)([^\s\"',;\}]+)"
)


@dataclass
class BugReportPayload:
    """Подготовленный и очищенный от секретов отчёт об ошибке."""

    title: str
    body: str
    app_version: str = "N/A"
    category: str = "manual"
    environment: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BugReportResult:
    """Нормализованный результат отправки отчёта на Cloudflare Worker."""

    ok: bool
    status_code: Optional[int]
    message: str
    issue_url: Optional[str] = None
    issue_number: Optional[int] = None


def get_bug_report_endpoint() -> str:
    """Возвращает URL Cloudflare Worker из окружения, config.json или значения по умолчанию."""
    env_url = os.environ.get("NEONROUTE_BUG_REPORT_URL", "").strip()
    if env_url:
        return env_url
    try:
        from utils.config_manager import ConfigManager

        cfg = ConfigManager.load_config()
        cfg_url = str(cfg.get("bug_report_endpoint_url", "") or "").strip()
        if cfg_url:
            return cfg_url
    except Exception:
        pass
    return DEFAULT_BUG_REPORT_ENDPOINT


def _replace_path_variants(text: str, raw_path: str, placeholder: str) -> str:
    """Заменяет прямые и обратные слеши пути на плейсхолдер без учёта регистра."""
    clean = (raw_path or "").strip().rstrip("\\/")
    if not clean or len(clean) < 3:
        return text

    variants = {
        clean,
        clean.replace("\\", "/"),
        clean.replace("/", "\\"),
    }
    for variant in sorted(variants, key=len, reverse=True):
        pattern = re.compile(re.escape(variant), re.IGNORECASE)
        text = pattern.sub(placeholder, text)
    return text


def sanitize_text(text: str) -> str:
    """
    Маскирует пути пользователя Windows, переменные окружения (BASE_DIR, FORGE_ROOT_DIR,
    USERPROFILE, USERNAME), учётные данные в URL и известные форматы токенов/API-ключей.
    """
    if text is None:
        return ""
    result = str(text)
    if not result:
        return ""

    # 1. Замена известных корневых директорий приложения и Forge
    try:
        import config as _cfg

        base_dir = getattr(_cfg, "BASE_DIR", None)
        if base_dir:
            result = _replace_path_variants(result, str(base_dir), "<APP_DIR>")
        forge_dir = getattr(_cfg, "FORGE_ROOT_DIR", None)
        if forge_dir:
            result = _replace_path_variants(result, str(forge_dir), "<FORGE_DIR>")
    except Exception:
        pass

    # 2. Замена USERPROFILE / HOME из окружения
    for env_key in ("USERPROFILE", "HOME"):
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            result = _replace_path_variants(result, env_val, "<USER_HOME>")

    # 3. Замена любых Windows-путей вида C:\Users\<name> или C:/Users/<name>
    result = _WIN_USER_PATH_RE.sub(r"\1<USER>", result)

    # 4. Маскирование учётных данных и токенов
    result = _URL_CREDS_RE.sub(r"\1<USER>:<REDACTED>@", result)
    result = _TOKEN_PREFIX_RE.sub("<REDACTED_TOKEN>", result)
    result = _QUERY_SECRET_RE.sub(r"\1<REDACTED>", result)
    result = _BEARER_RE.sub(r"\1<REDACTED>", result)
    result = _KV_SECRET_RE.sub(r"\1\2<REDACTED>", result)

    # 5. Замена имени пользователя из окружения (при длине >= 3 во избежание ложных срабатываний)
    username = os.environ.get("USERNAME", "").strip()
    if len(username) >= 3:
        user_re = re.compile(
            r"(?i)(?<![a-z0-9_])" + re.escape(username) + r"(?![a-z0-9_])"
        )
        result = user_re.sub("<USER>", result)

    return result


def _normalize_short_hash(raw: Any) -> Optional[str]:
    """Нормализует хэш сборки/коммита до 7–12 символов без пробелов и санитизирует его."""
    if raw is None:
        return None
    parts = str(raw).strip().split()
    if not parts:
        return None
    cleaned = sanitize_text(parts[0]).strip()
    if not cleaned:
        return None
    return cleaned[:12]


def _resolve_build_hash() -> str:
    """
    Определяет короткий хэш сборки/коммита из config, release_manifest.json или .git.
    При любых сбоях возвращает 'N/A'.
    """
    try:
        import config as _cfg

        # 1. Опциональные атрибуты в config
        for attr in ("BUILD_HASH", "APP_BUILD_HASH", "GIT_COMMIT"):
            short = _normalize_short_hash(getattr(_cfg, attr, None))
            if short:
                return short

        base_dir_raw = getattr(_cfg, "BASE_DIR", None)
        if not base_dir_raw:
            return "N/A"
        base_dir = Path(base_dir_raw)

        # 2. release_manifest.json в BASE_DIR
        manifest_path = base_dir / "release_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(manifest, dict):
                    for key in ("build_hash", "git_commit", "commit"):
                        short = _normalize_short_hash(manifest.get(key))
                        if short:
                            return short
            except Exception:
                pass

        # 3. .git/HEAD -> refs/... или packed-refs (без subprocess)
        git_dir = base_dir / ".git"
        head_path = git_dir / "HEAD"
        if head_path.is_file():
            head_text = head_path.read_text(encoding="utf-8", errors="replace").strip()
            if head_text.lower().startswith("ref:"):
                ref_rel = head_text[4:].strip().lstrip("/")
                ref_file = git_dir / ref_rel
                if ref_file.is_file():
                    short = _normalize_short_hash(
                        ref_file.read_text(encoding="utf-8", errors="replace")
                    )
                    if short:
                        return short

                packed_refs = git_dir / "packed-refs"
                if packed_refs.is_file():
                    for line in packed_refs.read_text(encoding="utf-8", errors="replace").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or line.startswith("^"):
                            continue
                        tokens = line.split()
                        if len(tokens) >= 2 and tokens[1] == ref_rel:
                            short = _normalize_short_hash(tokens[0])
                            if short:
                                return short
            else:
                short = _normalize_short_hash(head_text)
                if short:
                    return short
    except Exception:
        pass

    return "N/A"


def collect_environment_summary() -> Dict[str, Any]:
    """
    Собирает краткий срез окружения (версия приложения, build_hash, ОС, Python, GPU/VRAM, RAM,
    статус зависимостей). Каждый блок изолирован try/except с безопасным fallback 'N/A'.
    """
    summary: Dict[str, Any] = {
        "app_version": "N/A",
        "build_hash": _resolve_build_hash(),
        "os": "N/A",
        "python": "N/A",
        "gpu": "N/A",
        "vram_total_mb": "N/A",
        "ram_total_gb": "N/A",
        "ram_available_gb": "N/A",
        "missing_dependencies": "N/A",
        "packages": {},
    }

    # 1. Версия приложения
    try:
        import config as _cfg

        ver = getattr(_cfg, "APP_VERSION", None)
        if ver:
            summary["app_version"] = str(ver)
    except Exception:
        pass

    # 2. ОС и платформа
    try:
        summary["os"] = f"{platform.system()} {platform.release()} ({platform.machine()})"
    except Exception:
        pass

    # 3. Версия Python
    try:
        summary["python"] = platform.python_version()
    except Exception:
        pass

    # 4. GPU и VRAM (через pynvml или системный реестр без запуска QThread)
    try:
        gpu_name = None
        vram_mb = None
        try:
            import pynvml

            pynvml.nvmlInit()
            try:
                if pynvml.nvmlDeviceGetCount() > 0:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                    raw_name = pynvml.nvmlDeviceGetName(handle)
                    if isinstance(raw_name, bytes):
                        raw_name = raw_name.decode("utf-8", errors="replace")
                    gpu_name = str(raw_name)
                    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    vram_mb = int(mem.total // (1024 * 1024))
            finally:
                try:
                    pynvml.nvmlShutdown()
                except Exception:
                    pass
        except Exception:
            pass

        if not gpu_name:
            from utils.system_monitor import _detect_windows_gpu_via_registry

            reg_info = _detect_windows_gpu_via_registry()
            if reg_info.get("gpu_name") and reg_info.get("gpu_name") != "Unknown GPU":
                gpu_name = str(reg_info["gpu_name"])
                if reg_info.get("vram_total_mb"):
                    vram_mb = int(reg_info["vram_total_mb"])

        if gpu_name:
            summary["gpu"] = sanitize_text(gpu_name)
        if vram_mb is not None:
            summary["vram_total_mb"] = vram_mb
    except Exception:
        pass

    # 5. Системная RAM
    try:
        import psutil

        vm = psutil.virtual_memory()
        summary["ram_total_gb"] = round(vm.total / (1024 ** 3), 2)
        summary["ram_available_gb"] = round(vm.available / (1024 ** 3), 2)
    except Exception:
        pass

    # 6. Недостающие опциональные зависимости
    try:
        from services.environment_doctor import EnvironmentDoctor

        missing = EnvironmentDoctor.check_missing(verbose=False)
        if isinstance(missing, list):
            summary["missing_dependencies"] = [
                str(item.get("key", item.get("name", "unknown")))
                for item in missing
                if isinstance(item, dict)
            ]
    except Exception:
        pass

    # 7. Версии ключевых библиотек
    try:
        from importlib.metadata import version as _pkg_version

        pkgs: Dict[str, str] = {}
        for pkg_name in ("PyQt6", "PyQt6-Fluent-Widgets", "requests", "psutil", "pynvml"):
            try:
                pkgs[pkg_name] = _pkg_version(pkg_name)
            except Exception:
                pkgs[pkg_name] = "N/A"
        summary["packages"] = pkgs
    except Exception:
        pass

    return summary


def _read_tail_lines(path: Path, max_lines: int) -> List[str]:
    """Безопасно читает последние max_lines строк из текстового файла."""
    if max_lines <= 0 or not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        return lines[-max_lines:]
    except Exception:
        return []


def _collect_recent_logs(log_tail_lines: int) -> str:
    """Собирает хвост свежих логов из буфера сессии и/или файлов logs/."""
    if log_tail_lines <= 0:
        return ""

    chunks: List[str] = []

    # 1. Буфер текущей сессии (только если ui.log_console уже загружен или доступен)
    try:
        log_console_mod = sys.modules.get("ui.log_console")
        if log_console_mod is not None and hasattr(log_console_mod, "_CURRENT_SESSION_LOGS"):
            session_buf = list(log_console_mod._CURRENT_SESSION_LOGS)
            if session_buf:
                tail = [msg for msg, _lvl in session_buf[-log_tail_lines:]]
                chunks.append("### Session Log Tail\n" + "\n".join(tail))
    except Exception:
        pass

    # 2. Файлы логов на диске (если буфер пуст или для полноты картины)
    try:
        import config as _cfg

        logs_dir = Path(getattr(_cfg, "LOGS_DIR", Path("logs")))
        if not chunks:
            neon_lines = _read_tail_lines(logs_dir / "neonroute.log", log_tail_lines)
            if neon_lines:
                chunks.append("### neonroute.log (tail)\n" + "\n".join(neon_lines))

        crash_lines = _read_tail_lines(logs_dir / "crash.log", min(log_tail_lines, 30))
        if crash_lines:
            chunks.append("### crash.log (tail)\n" + "\n".join(crash_lines))
    except Exception:
        pass

    return "\n\n".join(chunks)


def build_bug_report_payload(
    error_summary: str = "",
    traceback_text: str = "",
    log_tail_lines: int = 50,
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
    source: str = "manual",
) -> BugReportPayload:
    """
    Формирует очищенный и ограниченный по длине BugReportPayload для предпросмотра и отправки.
    """
    env_summary = collect_environment_summary()
    app_version = str(env_summary.get("app_version", "N/A"))

    clean_summary = sanitize_text((error_summary or "").strip())
    if clean_summary:
        first_line = clean_summary.splitlines()[0].strip()
        raw_title = f"[Bug] {first_line}"
    else:
        raw_title = f"[Bug Report] NeonRoute {app_version} ({source})"

    title = raw_title[:MAX_TITLE_CHARS].strip()

    sections: List[str] = [
        "## Описание проблемы\n" + (clean_summary or "_Опишите, что произошло и как воспроизвести ошибку._"),
        f"## Источник\n`{sanitize_text(source)}`",
        (
            "## Окружение\n"
            f"- **App Version**: `{env_summary.get('app_version', 'N/A')}`\n"
            f"- **Build Hash**: `{env_summary.get('build_hash', 'N/A')}`\n"
            f"- **OS**: `{env_summary.get('os', 'N/A')}`\n"
            f"- **Python**: `{env_summary.get('python', 'N/A')}`\n"
            f"- **GPU**: `{env_summary.get('gpu', 'N/A')}` (VRAM: `{env_summary.get('vram_total_mb', 'N/A')}` MB)\n"
            f"- **RAM**: `{env_summary.get('ram_available_gb', 'N/A')}` / `{env_summary.get('ram_total_gb', 'N/A')}` GB\n"
            f"- **Missing Deps**: `{env_summary.get('missing_dependencies', 'N/A')}`\n"
            f"- **Packages**: `{env_summary.get('packages', {})}`"
        ),
    ]

    clean_tb = sanitize_text((traceback_text or "").strip())
    if clean_tb:
        sections.append(f"## Traceback\n```python\n{clean_tb}\n```")

    raw_logs = _collect_recent_logs(log_tail_lines)
    clean_logs = sanitize_text(raw_logs.strip())
    if clean_logs:
        sections.append(f"## Логи\n```text\n{clean_logs}\n```")

    body = "\n\n".join(sections)

    if max_body_chars > 0 and len(body) > max_body_chars:
        keep_len = max(0, max_body_chars - len(TRUNCATION_MARKER))
        body = body[:keep_len].rstrip() + TRUNCATION_MARKER

    return BugReportPayload(
        title=title,
        body=body,
        app_version=app_version,
        category=source or "manual",
        environment=env_summary,
    )


def send_bug_report(
    endpoint_url: str,
    title: str,
    body: str,
    timeout: float = 15.0,
    app_version: str = "",
    category: str = "manual",
) -> BugReportResult:
    """
    Выполняет явную отправку отчёта об ошибке на Cloudflare Worker через POST JSON.
    """
    url = (endpoint_url or "").strip()
    if not url or not (url.startswith("https://") or url.startswith("http://")):
        return BugReportResult(
            ok=False,
            status_code=None,
            message="URL сервиса отправки отчётов не настроен (укажите NEONROUTE_BUG_REPORT_URL).",
        )

    clean_title = sanitize_text((title or "").strip())[:MAX_TITLE_CHARS]
    clean_body = sanitize_text((body or "").strip())
    if not clean_title or not clean_body:
        return BugReportResult(
            ok=False,
            status_code=None,
            message="Заголовок и текст отчёта не могут быть пустыми.",
        )

    payload: Dict[str, Any] = {
        "title": clean_title,
        "body": clean_body,
        "category": (category or "manual").strip() or "manual",
    }
    if app_version:
        payload["app_version"] = str(app_version).strip()

    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "NeonRoute-BugReporter",
            },
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return BugReportResult(
            ok=False,
            status_code=None,
            message="Превышено время ожидания ответа от сервера отчётов.",
        )
    except requests.exceptions.ConnectionError:
        return BugReportResult(
            ok=False,
            status_code=None,
            message="Не удалось подключиться к серверу отправки отчётов.",
        )
    except requests.exceptions.RequestException as exc:
        return BugReportResult(
            ok=False,
            status_code=None,
            message=f"Сетевая ошибка при отправке отчёта: {sanitize_text(str(exc))}",
        )

    data: Optional[Dict[str, Any]] = None
    try:
        parsed = resp.json()
        if isinstance(parsed, dict):
            data = parsed
    except ValueError:
        data = None

    if 200 <= resp.status_code < 300:
        issue_url = data.get("issue_url") if data else None
        issue_number = data.get("issue_number") if data else None
        msg = "Отчёт успешно отправлен."
        if issue_number is not None:
            msg = f"Отчёт успешно отправлен (Issue #{issue_number})."
        return BugReportResult(
            ok=True,
            status_code=resp.status_code,
            message=msg,
            issue_url=str(issue_url) if issue_url else None,
            issue_number=int(issue_number) if isinstance(issue_number, int) else None,
        )

    err_detail = ""
    if data and data.get("error"):
        err_detail = str(data["error"])
    elif resp.text:
        err_detail = resp.text[:200]
    err_detail = sanitize_text(err_detail).strip()

    return BugReportResult(
        ok=False,
        status_code=resp.status_code,
        message=f"Сервер вернул ошибку HTTP {resp.status_code}" + (f": {err_detail}" if err_detail else "."),
    )
