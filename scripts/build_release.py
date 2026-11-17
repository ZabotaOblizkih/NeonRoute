"""
NeonRoute Automated Release Builder & Quality Gate Pipeline.
Stages:
  Stage A: AST Audit of 3rd Party Dependencies vs requirements.txt
  Stage B: Test Quality Gate (pytest)
  Stage C: Sterile Sanitized Package Assembly & Zip Bundling
"""

import argparse
import ast
from datetime import datetime
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Reconfigure stdout/stdin to UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

# Project root resolution
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config

# Import -> PyPI package mapping (normalized to lowercase with hyphens)
IMPORT_TO_PYPI: Dict[str, str] = {
    "pil": "pillow",
    "pillow": "pillow",
    "pyqt6": "pyqt6",
    "qfluentwidgets": "pyqt6-fluent-widgets",
    "qframelesswindow": "pyqt6-frameless-window",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "sse_starlette": "sse-starlette",
    "pydantic": "pydantic",
    "win32con": "pywin32",
    "win32api": "pywin32",
    "win32gui": "pywin32",
    "pywintypes": "pywin32",
    "send2trash": "send2trash",
    "json_repair": "json-repair",
    "fastembed": "fastembed",
    "lancedb": "lancedb",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "psutil": "psutil",
    "pynvml": "pynvml",
    "pypdf": "pypdf",
    "docx": "python-docx",
    "requests": "requests",
    "dateparser": "dateparser",
    "pytest": "pytest",
    "fitz": "pymupdf",
    "pdfplumber": "pdfplumber",
    "cv2": "opencv-python",
    "yaml": "pyyaml",
}

# Known optional dependencies or test stubs
OPTIONAL_MODULES: Set[str] = {
    "torch",
    "faster_whisper",
    "whisper",
    "fitz",
    "pdfplumber",
    "transformers",
    "accelerate",
    "conftest",
    "sip",
}

INTERNAL_PROJECT_MODULES: Set[str] = {
    "config",
    "core",
    "services",
    "ui",
    "mobile_server",
    "utils",
    "main",
    "run_mobile_server",
}


def on_rm_error(func, path, exc_info):
    """Handles read-only file removal on Windows."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def get_stdlib_modules() -> Set[str]:
    """Returns a set of standard library module names for the current Python."""
    if hasattr(sys, "stdlib_module_names"):
        return set(sys.stdlib_module_names)
    import sysconfig
    std_path = sysconfig.get_path("stdlib")
    names = set(sys.builtin_module_names)
    if std_path and os.path.isdir(std_path):
        for f in os.listdir(std_path):
            names.add(os.path.splitext(f)[0].lower())
    return names


def parse_requirements_txt(req_path: Path) -> Set[str]:
    """Parses requirements.txt into a set of normalized PyPI package names."""
    packages = set()
    if not req_path.is_file():
        return packages
    with open(req_path, "r", encoding="utf-8") as f:
        for line in f:
            clean = line.strip()
            if not clean or clean.startswith("#"):
                continue
            # Strip comments and version specifiers
            clean = re.split(r"[#;]", clean)[0].strip()
            pkg = re.split(r"[<>=!~@ ]", clean)[0].strip()
            if pkg:
                packages.add(pkg.lower().replace("_", "-"))
    return packages


def audit_ast_dependencies(repo_root: Path) -> Tuple[bool, List[str], List[str]]:
    """
    Stage A: Scans all runtime python files via AST to extract top-level imports
    and verifies that every required 3rd-party library is listed in requirements.txt.
    """
    stdlib = get_stdlib_modules()
    req_file = repo_root / "requirements.txt"
    listed_pypi = parse_requirements_txt(req_file)

    target_dirs = ["core", "services", "ui", "mobile_server", "utils"]
    target_files = ["main.py", "run_mobile_server.py", "config.py"]

    detected_imports: Dict[str, List[Tuple[str, int]]] = {}

    for tf in target_files:
        fp = repo_root / tf
        if fp.is_file():
            _collect_imports_from_file(fp, repo_root, detected_imports)

    for td in target_dirs:
        dp = repo_root / td
        if dp.is_dir():
            for root, _, files in os.walk(dp):
                if "__pycache__" in root:
                    continue
                for f in files:
                    if f.endswith(".py"):
                        fp = Path(root) / f
                        _collect_imports_from_file(fp, repo_root, detected_imports)

    missing_errors: List[str] = []
    optional_info: List[str] = []

    for mod, occurrences in sorted(detected_imports.items()):
        mod_lower = mod.lower()
        if mod in stdlib or mod_lower in stdlib or mod in INTERNAL_PROJECT_MODULES:
            continue

        if mod_lower in OPTIONAL_MODULES:
            sample_rel, sample_line = occurrences[0]
            optional_info.append(f"{mod} (optional/fallback in {sample_rel}:{sample_line})")
            continue

        pypi_pkg = IMPORT_TO_PYPI.get(mod_lower, mod_lower).replace("_", "-")
        if pypi_pkg not in listed_pypi:
            sample_rel, sample_line = occurrences[0]
            missing_errors.append(
                f"Missing dependency: module '{mod}' imported at {sample_rel}:{sample_line} "
                f"requires PyPI package '{pypi_pkg}' in requirements.txt"
            )

    success = len(missing_errors) == 0
    return success, missing_errors, optional_info


def _collect_imports_from_file(file_path: Path, repo_root: Path, results: Dict[str, List[Tuple[str, int]]]):
    rel_path = file_path.relative_to(repo_root)
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            tree = ast.parse(f.read(), filename=str(file_path))
    except Exception:
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                results.setdefault(top, []).append((str(rel_path), node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                top = node.module.split(".")[0]
                results.setdefault(top, []).append((str(rel_path), node.lineno))


def run_test_gate(repo_root: Path, fast: bool = False) -> bool:
    """
    Stage B: Executes pytest test suite.
    """
    cmd = [sys.executable, "-m", "pytest", "-q"]
    if fast:
        cmd.extend([
            "tests/smoke",
            "tests/test_main_window_baseline.py",
            "tests/unit/test_path_normalization.py",
            "tests/unit/test_default_avatars.py"
        ])

    print(f"\n[Stage B] Running Test Quality Gate: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(repo_root))
    return result.returncode == 0


PORTABLE_PYTHON_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    "20250106/cpython-3.10.16+20250106-x86_64-pc-windows-msvc-install_only.tar.gz"
)
PORTABLE_PYTHON_ARCHIVE_NAME = "cpython-3.10.16+20250106-x86_64-pc-windows-msvc-install_only.tar.gz"


def ensure_embedded_python(repo_root: Path, target_runtime_dir: Path) -> bool:
    """
    Downloads and caches portable CPython 3.10 x64 (Astral python-build-standalone)
    into .cache/portable_python/ and unpacks it into release_dir/runtime/python/.
    """
    cache_dir = repo_root / ".cache" / "portable_python"
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive_path = cache_dir / PORTABLE_PYTHON_ARCHIVE_NAME

    if not archive_path.is_file():
        print(f"Downloading portable Python runtime from:\n  {PORTABLE_PYTHON_URL}")
        try:
            req = urllib.request.Request(
                PORTABLE_PYTHON_URL,
                headers={"User-Agent": "NeonRoute-ReleaseBuilder"}
            )
            with urllib.request.urlopen(req) as resp, open(archive_path, "wb") as out_f:
                total_len = resp.headers.get("Content-Length")
                total = int(total_len) if total_len else 0
                downloaded = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = (downloaded / total) * 100
                        print(
                            f"\r  Progress: {downloaded / (1024*1024):.1f}/{total / (1024*1024):.1f} MB ({pct:.1f}%)",
                            end="",
                            flush=True
                        )
            print("\n  [OK] Download complete.")
        except Exception as e:
            print(f"\n  [ERROR] Failed to download portable Python: {e}")
            if archive_path.exists():
                archive_path.unlink()
            return False
    else:
        print(f"Using cached portable Python archive:\n  {archive_path.resolve()}")

    target_python_dir = target_runtime_dir / "python"
    target_python_dir.mkdir(parents=True, exist_ok=True)
    print(f"Unpacking portable Python into:\n  {target_python_dir.resolve()} ...")

    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.startswith("python/"):
                    member.name = member.name[len("python/"):]
                    if member.name:
                        tar.extract(member, target_python_dir)
        if not (target_python_dir / "python.exe").is_file():
            print(f"  [ERROR] Unpacked runtime is missing python.exe in {target_python_dir}")
            return False
        print("  [OK] Portable Python runtime unpacked successfully.")
        return True
    except Exception as e:
        print(f"  [ERROR] Failed to unpack portable Python: {e}")
        return False


def assemble_release(
    repo_root: Path,
    version: str,
    with_tests: bool = False,
    bundle_wheels: bool = False,
    no_embedded_python: bool = False
) -> Tuple[Path, Path]:
    """
    Stage C: Assembles sterile release directory and packs into zip archive.
    """
    dist_dir = repo_root / "dist"
    release_dir = dist_dir / f"NeonRoute_v{version}"
    zip_path = dist_dir / f"NeonRoute_v{version}.zip"

    dist_dir.mkdir(parents=True, exist_ok=True)

    if release_dir.exists():
        shutil.rmtree(release_dir, onerror=on_rm_error)
    if zip_path.exists():
        zip_path.unlink()

    release_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[Stage C] Assembling sterile package: {release_dir.name}")

    # 1. Root files
    root_files = [
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
        "hotfix.md",
        "pytest.ini"
    ]
    for rf in root_files:
        src = repo_root / rf
        if src.is_file():
            shutil.copy2(src, release_dir / rf)

    # 2. Source directories
    source_dirs = ["core", "services", "ui", "mobile_server", "utils", "docs", "scripts", "audio", "lorebooks", "exports", "assets"]
    for sd in source_dirs:
        src = repo_root / sd
        dst = release_dir / sd
        if src.is_dir():
            _copy_clean_tree(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=True)
            (dst / ".gitkeep").touch()

    # 3. Clean runtime folders (empty with .gitkeep)
    for rd in ["history", "outputs", "logs", "workspace", "backgrounds", "knowledge_base", "bin", "models"]:
        target_dir = release_dir / rd
        target_dir.mkdir(parents=True, exist_ok=True)
        # Ensure sterile empty directory with only .gitkeep (no *.exe, *.dll, *.gguf, *.bin, *.safetensors)
        for existing in target_dir.iterdir():
            if existing.name != ".gitkeep":
                if existing.is_dir():
                    shutil.rmtree(existing, onerror=on_rm_error)
                else:
                    existing.unlink(missing_ok=True)
        (target_dir / ".gitkeep").touch()

    # 4. Data directory (user personas, lora registry & icons; zero personal data/local checkpoints)
    # art_director_models.json intentionally excluded so local developer models do not leak into release.
    # Clean defaults are auto-generated on first launch by services.art_director_config.
    data_dst = release_dir / "data"
    data_dst.mkdir(parents=True, exist_ok=True)
    for df in ["user_personas.json", "lora_registry.json"]:
        src_df = repo_root / "data" / df
        if src_df.is_file():
            shutil.copy2(src_df, data_dst / df)

    if (repo_root / "data" / "skill_icons").is_dir():
        _copy_clean_tree(repo_root / "data" / "skill_icons", data_dst / "skill_icons")

    (data_dst / "cache").mkdir(parents=True, exist_ok=True)
    (data_dst / "vector_memory").mkdir(parents=True, exist_ok=True)

    # 5. Characters directory (only canonical Serafina, zero user-generated images)
    char_dst = release_dir / "characters"
    char_dst.mkdir(parents=True, exist_ok=True)
    for cf in ["Серафина.json", "Серафина_avatar.png"]:
        src_cf = repo_root / "characters" / cf
        if src_cf.is_file():
            shutil.copy2(src_cf, char_dst / cf)
    (char_dst / "session_avatars").mkdir(parents=True, exist_ok=True)

    # 6. Avatars directory (canonical modes only)
    avatars_dst = release_dir / "avatars"
    avatars_dst.mkdir(parents=True, exist_ok=True)
    for av in ["assistant.jpg", "engenier.jpg", "RP_chat.jpg"]:
        src_av = repo_root / "avatars" / av
        if src_av.is_file():
            shutil.copy2(src_av, avatars_dst / av)

    # 7. Optional Tests
    if with_tests:
        tests_src = repo_root / "tests"
        if tests_src.is_dir():
            _copy_clean_tree(tests_src, release_dir / "tests")

    # 8. Optional Offline Wheels
    if bundle_wheels:
        wheels_dir = release_dir / "wheels"
        wheels_dir.mkdir(parents=True, exist_ok=True)
        print(f"Downloading wheels for offline installation into {wheels_dir}...")
        subprocess.run([
            sys.executable, "-m", "pip", "download",
            "-r", str(repo_root / "requirements.txt"),
            "-d", str(wheels_dir)
        ], check=True)

    # 9. Autonomous Embedded Python Runtime
    if not no_embedded_python:
        print("\nEmbedding autonomous portable Python runtime...")
        ok = ensure_embedded_python(repo_root, release_dir / "runtime")
        if not ok:
            raise RuntimeError("Failed to bundle autonomous portable Python runtime.")
    else:
        print("\n[INFO] Bundling of embedded Python runtime skipped (--no-embedded-python).")

    # 10. Pack to ZIP archive
    print(f"\nCompressing into ZIP archive: {zip_path.name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(release_dir):
            for file in files:
                abs_f = Path(root) / file
                rel_f = abs_f.relative_to(dist_dir)
                zf.write(abs_f, arcname=str(rel_f))

    return release_dir, zip_path


def _copy_clean_tree(src: Path, dst: Path):
    """Copies directory recursively, omitting cache files and temp directories."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in os.listdir(src):
        if item in ("__pycache__", ".pytest_cache", ".ruff_cache", ".git"):
            continue
        if any(item.endswith(ext) for ext in (".pyc", ".pyo", ".log", ".bak", ".tmp")):
            continue
        if item.startswith("hotfix_"):
            continue
        s_item = src / item
        d_item = dst / item
        if s_item.is_dir():
            _copy_clean_tree(s_item, d_item)
        elif s_item.is_file():
            shutil.copy2(s_item, d_item)


FORBIDDEN_WIN_CHARS: Set[str] = set('<>:"/\\|?*')


def validate_version_string(ver: str) -> Tuple[bool, str]:
    r"""
    Validates version string. Allows arbitrary string format (e.g. 0.9.9-beta,
    0.9.8.123.2321ABSJDLKAD, 1.0_rc2) without SemVer constraints.
    Rejects empty/whitespace, Windows illegal filename characters (<>:"/\|?*)
    and control characters.
    """
    if not ver or not ver.strip():
        return False, "Версия не может быть пустой"
    ver_clean = ver.strip()
    for ch in ver_clean:
        if ch in FORBIDDEN_WIN_CHARS:
            return False, f"Символ '{ch}' запрещен в именах файлов Windows (< > : \" / \\ | ? *)"
        if ord(ch) < 32 or ord(ch) == 127:
            return False, "Управляющие символы запрещены в номере версии"
    return True, ""


RELEASE_COMMIT_MARKER = "[release-head]"

NOISE_PREFIXES = (
    "fixup!",
    "squash!",
    "wip",
    "temp",
    "bump",
    "release v",
    "ci:",
    "ci ",
    "ci(",
    "style:",
    "style ",
    "style(",
    "test:",
    "test ",
    "test(",
    "chore(release)",
)


def is_noise_commit(subject: str) -> bool:
    s = subject.strip().lower()
    for prefix in NOISE_PREFIXES:
        if s.startswith(prefix):
            return True
    return False


def collect_git_commits(
    repo_root: Path,
    max_commits: int = 50
) -> Tuple[List[str], List[str], List[str]]:
    """
    Collects commits from HEAD down until RELEASE_COMMIT_MARKER is encountered in subject or body.
    Filters noise (fixup!, squash!, wip, temp, bump, release v, ci:, style:, test:, chore(release)
    and duplicate subjects).
    Returns (relevant_formatted, ignored_formatted, all_formatted).
    Never crashes if git is unavailable or not a git repo.
    """
    if not shutil.which("git"):
        return [], [], []

    try:
        res = subprocess.run(
            ["git", "log", "--no-merges", f"-n{max_commits}", "--format=%H%x1f%s%x1f%b%x1e"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5
        )
        if res.returncode != 0 or not res.stdout:
            return [], [], []
        raw_log = res.stdout
    except Exception:
        return [], [], []

    relevant: List[str] = []
    ignored: List[str] = []
    all_commits: List[str] = []
    seen_subjects: Set[str] = set()

    records = raw_log.split("\x1e")
    for rec in records:
        rec_clean = rec.strip("\r\n")
        if not rec_clean:
            continue
        fields = rec_clean.split("\x1f")
        h = fields[0].strip() if len(fields) > 0 else ""
        s = fields[1].strip() if len(fields) > 1 else ""
        b = fields[2].strip() if len(fields) > 2 else ""

        # Stop immediately when marker is encountered in subject or body
        if RELEASE_COMMIT_MARKER in s or RELEASE_COMMIT_MARKER in b:
            break

        if not s:
            continue

        short_h = h[:7] if h else ""
        formatted = f"* {s} ({short_h})" if short_h else f"* {s}"
        all_commits.append(formatted)

        norm_s = s.lower()
        if is_noise_commit(s) or norm_s in seen_subjects:
            ignored.append(formatted)
        else:
            seen_subjects.add(norm_s)
            relevant.append(formatted)

    return relevant, ignored, all_commits


def get_git_recent_commits(repo_root: Path, max_commits: int = 20) -> List[str]:
    """Backward-compatible helper returning relevant commits."""
    relevant, _, all_commits = collect_git_commits(repo_root, max_commits=max_commits)
    return relevant if relevant else all_commits


def read_app_version(config_path: Path) -> str:
    """Reads APP_VERSION from config.py safely."""
    if not config_path.is_file():
        try:
            import config
            return getattr(config, "APP_VERSION", "0.0.0")
        except Exception:
            return "0.0.0"

    content = config_path.read_text(encoding="utf-8")
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("APP_VERSION") and "=" in stripped:
            val_part = stripped.split("=", 1)[1].strip()
            return val_part.strip("\"'")

    try:
        import config
        return getattr(config, "APP_VERSION", "0.0.0")
    except Exception:
        return "0.0.0"


def update_app_version_in_config(config_path: Path, new_version: str) -> None:
    """Updates APP_VERSION in config.py safely without regex traps."""
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("APP_VERSION") and "=" in stripped:
            prefix = line.split("=")[0]
            lines[i] = f'{prefix}= "{new_version}"\n'
            found = True
            break
    if not found:
        raise ValueError(f"APP_VERSION definition not found in {config_path}")

    config_path.write_text("".join(lines), encoding="utf-8")
    try:
        import config
        config.APP_VERSION = new_version
    except Exception:
        pass


def update_hotfix_md(
    hotfix_path: Path,
    new_version: str,
    changes_text: str,
    date_str: Optional[str] = None
) -> None:
    """
    Inserts or replaces the entry for ## v<new_version> (YYYY-MM-DD) in hotfix.md.
    Preserves all existing history. Creates file if missing.
    """
    today_str = date_str or datetime.now().strftime("%Y-%m-%d")
    clean_changes = changes_text.strip()
    new_entry = f"## v{new_version} ({today_str})\n\n{clean_changes}\n\n"

    if not hotfix_path.is_file():
        title = "# История изменений и релизов NeonRoute\n\n"
        hotfix_path.write_text(title + new_entry, encoding="utf-8")
        return

    content = hotfix_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    target_header = f"## v{new_version}"

    # Search for an existing section with this exact version
    start_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == target_header or stripped.startswith(f"{target_header} ") or stripped.startswith(f"{target_header}("):
            start_idx = i
            break

    if start_idx is not None:
        # Section exists: replace until the next ## v section or EOF
        end_idx = len(lines)
        for j in range(start_idx + 1, len(lines)):
            if lines[j].strip().startswith("## v") or lines[j].strip().startswith("##  v"):
                end_idx = j
                break
        new_lines = lines[:start_idx] + [new_entry] + lines[end_idx:]
    else:
        # Section does not exist: insert right after top title (# ...)
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.strip().startswith("# "):
                insert_idx = i + 1
                while insert_idx < len(lines) and not lines[insert_idx].strip():
                    insert_idx += 1
                break
        new_lines = lines[:insert_idx] + [new_entry] + lines[insert_idx:]

    hotfix_path.write_text("".join(new_lines).rstrip() + "\n", encoding="utf-8")


def rollback_versioning(rollback_state: dict) -> None:
    """Rolls back config.py and hotfix.md to their states before interactive versioning."""
    print("\n  [ROLLBACK] Откат изменений версии и hotfix.md из-за сбоя проверок...")
    config_path = rollback_state.get("config_path")
    config_backup = rollback_state.get("config_backup")
    if config_path and config_backup is not None:
        Path(config_path).write_text(config_backup, encoding="utf-8")
        try:
            import config
            old_version = rollback_state.get("old_version")
            if old_version:
                config.APP_VERSION = old_version
        except Exception:
            pass

    hotfix_path = rollback_state.get("hotfix_path")
    hotfix_existed = rollback_state.get("hotfix_existed")
    hotfix_backup = rollback_state.get("hotfix_backup")
    if hotfix_path:
        hp = Path(hotfix_path)
        if hotfix_existed and hotfix_backup is not None:
            hp.write_text(hotfix_backup, encoding="utf-8")
        elif not hotfix_existed and hp.exists():
            hp.unlink(missing_ok=True)
    print("  [ROLLBACK] Исходные файлы успешно восстановлены.")


def prompt_interactive_versioning(
    repo_root: Path,
    non_interactive: bool = False
) -> Tuple[str, Optional[dict]]:
    """
    Interactive versioning step before Stage A.
    Skipped if non_interactive is True, in CI environment, or if stdin is not a TTY.
    Returns (effective_version, rollback_state_or_None).
    """
    config_path = repo_root / "config.py"
    hotfix_path = repo_root / "hotfix.md"
    current_version = read_app_version(config_path)

    is_ci = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
    is_tty = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    if non_interactive or is_ci or not is_tty:
        return current_version, None

    print("\n" + "=" * 65)
    print(f"  [ИНТЕРАКТИВНОЕ УПРАВЛЕНИЕ ВЕРСИЕЙ]")
    print(f"  Текущая версия: {current_version}")
    print("=" * 65)

    relevant, ignored, all_commits = collect_git_commits(repo_root)
    if relevant:
        print("\nПоследние изменения (коммиты git):")
        for c in relevant[:15]:
            print(f"  {c}")
        if len(relevant) > 15:
            print(f"  ... и еще {len(relevant) - 15} коммитов")
    if ignored:
        print(f"  Скрыто служебных: {len(ignored)} (введите 'all' на шаге описания, если нужно увидеть все)")
    elif not relevant:
        print("\n[INFO] Новых коммитов git после предыдущего релиза не найдено.")

    while True:
        try:
            val = input(f"\nВведите новую версию (Enter = оставить {current_version}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[INFO] Ввод прерван, используется текущая версия.")
            return current_version, None

        if not val or val == current_version:
            print(f"  [OK] Версия не изменилась: {current_version}")
            return current_version, None

        ok, err_msg = validate_version_string(val)
        if ok:
            new_version = val
            break
        print(f"  [!] Ошибка: {err_msg}. Попробуйте снова.")

    while True:
        print("\nВведите описание изменений (завершите строкой END или пустым вводом; 'all' = показать все коммиты):")
        lines: List[str] = []
        show_all = False
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip() == "END":
                break
            if not line.strip() and lines:
                break
            if not line.strip() and not lines:
                break
            if not lines and line.strip().lower() == "all":
                show_all = True
                break
            lines.append(line)

        if show_all:
            print("\nВсе коммиты до последнего релиза (включая служебные):")
            for c in all_commits:
                print(f"  {c}")
            continue
        break

    changes_text = "\n".join(lines).strip()
    if not changes_text:
        fallback_commits = relevant if relevant else all_commits
        if fallback_commits:
            print("  [INFO] Ввод пуст. Используется список коммитов git.")
            changes_text = "\n".join(fallback_commits[:20])
        else:
            changes_text = "- Технические оптимизации и исправления ошибок."

    rollback_state = {
        "config_path": config_path,
        "config_backup": config_path.read_text(encoding="utf-8"),
        "old_version": current_version,
        "hotfix_path": hotfix_path,
        "hotfix_existed": hotfix_path.is_file(),
        "hotfix_backup": hotfix_path.read_text(encoding="utf-8") if hotfix_path.is_file() else None,
        "changes_text": changes_text,
    }

    update_app_version_in_config(config_path, new_version)
    print(f"  [SUCCESS] Версия в config.py обновлена: {current_version} -> {new_version}")

    update_hotfix_md(hotfix_path, new_version, changes_text)
    print(f"  [SUCCESS] Запись в {hotfix_path.name} добавлена/обновлена.")

    return new_version, rollback_state


def create_release_git_commit(
    repo_root: Path,
    new_version: str,
    changes_text: str
) -> bool:
    """
    Creates an automatic git release commit for config.py and hotfix.md.
    Contains RELEASE_COMMIT_MARKER in the commit body (never in hotfix.md).
    Uses git commit --only -- config.py hotfix.md to avoid committing unrelated files.
    Does not raise exceptions if git fails (e.g., missing user.name/user.email).
    """
    if not shutil.which("git"):
        print("  [WARNING] Git не найден в PATH, релизный коммит пропущен.")
        return False

    first_line = ""
    for line in changes_text.strip().splitlines():
        clean = line.strip().lstrip("-*• ").strip()
        if clean:
            first_line = clean
            break

    if first_line:
        commit_subject = f"v{new_version}: {first_line}"
    else:
        commit_subject = f"Release v{new_version}"

    if len(commit_subject) > 80:
        commit_subject = commit_subject[:77] + "..."

    commit_body = f"{changes_text.strip()}\n\n{RELEASE_COMMIT_MARKER}"

    try:
        subprocess.run(
            ["git", "add", "config.py", "hotfix.md"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False
        )

        res = subprocess.run(
            [
                "git",
                "commit",
                "--only",
                "--",
                "config.py",
                "hotfix.md",
                "-m",
                commit_subject,
                "-m",
                commit_body
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False
        )
        if res.returncode == 0:
            print(f"  [SUCCESS] Релизный git-коммит создан: {commit_subject}")
            return True
        else:
            err = (res.stderr or res.stdout or "").strip()
            print(f"  [WARNING] Не удалось создать релизный git-коммит: {err}")
            return False
    except Exception as e:
        print(f"  [WARNING] Ошибка при создании git-коммита: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="NeonRoute Automated Release Builder & Quality Gate")
    parser.add_argument("--skip-dep-check", action="store_true", help="Skip AST dependency check vs requirements.txt")
    parser.add_argument("--skip-tests", action="store_true", help="Skip pytest quality gate")
    parser.add_argument("--fast", action="store_true", help="Run fast test suite subset for quick build verification")
    parser.add_argument("--with-tests", action="store_true", help="Include tests/ folder in the release package (default: excluded)")
    parser.add_argument("--bundle-wheels", action="store_true", help="Download offline wheels package into release/wheels/")
    parser.add_argument("--no-embedded-python", action="store_true", help="Exclude bundled portable Python runtime from release package")
    parser.add_argument("--non-interactive", action="store_true", help="Run without interactive prompts (for CI/CD)")
    parser.add_argument("--no-git-commit", action="store_true", help="Do not create an automatic git release commit")
    args = parser.parse_args()

    # Interactive Versioning Step (Before Stage A)
    version, rollback_state = prompt_interactive_versioning(
        REPO_ROOT,
        non_interactive=args.non_interactive
    )

    print("=" * 65)
    print(f"  NeonRoute Release Pipeline — Target Version: v{version}")
    print("=" * 65)

    # Stage A: AST Dependency Audit
    if not args.skip_dep_check:
        print("\n[Stage A] Running AST 3rd-Party Dependency Audit...")
        success, errors, optionals = audit_ast_dependencies(REPO_ROOT)
        if optionals:
            print("  [INFO] Optional / fallback imports detected:")
            for opt in optionals:
                print(f"    • {opt}")

        if not success:
            print("\n  [ERROR] AST Dependency Audit FAILED:")
            for err in errors:
                print(f"    ✖ {err}")
            print("\nPlease add the missing packages to requirements.txt or use --skip-dep-check.\n")
            if rollback_state:
                rollback_versioning(rollback_state)
            sys.exit(1)
        print("  [OK] AST Dependency Audit PASSED: all required runtime imports match requirements.txt.")
    else:
        print("\n[Stage A] AST Dependency Audit SKIPPED (--skip-dep-check).")

    # Stage B: Test Quality Gate
    if not args.skip_tests:
        test_passed = run_test_gate(REPO_ROOT, fast=args.fast)
        if not test_passed:
            print("\n  [ERROR] Test Quality Gate FAILED. Release build aborted.\n")
            if rollback_state:
                rollback_versioning(rollback_state)
            sys.exit(1)
        print("  [OK] Test Quality Gate PASSED.")
    else:
        print("\n[Stage B] Test Quality Gate SKIPPED (--skip-tests).")

    # Stage C: Assembly & Sanitization
    release_dir, zip_path = assemble_release(
        REPO_ROOT,
        version=version,
        with_tests=args.with_tests,
        bundle_wheels=args.bundle_wheels,
        no_embedded_python=args.no_embedded_python
    )

    dir_size_mb = sum(f.stat().st_size for f in release_dir.rglob("*") if f.is_file()) / (1024 * 1024)
    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
    file_count = sum(1 for f in release_dir.rglob("*") if f.is_file())

    print("\n" + "=" * 65)
    print(f"  [SUCCESS] Release Assembly Completed Successfully!")
    print(f"  Directory: {release_dir.resolve()}")
    print(f"  Files:     {file_count} files ({dir_size_mb:.2f} MB)")
    print(f"  Archive:   {zip_path.resolve()} ({zip_size_mb:.2f} MB)")
    print("=" * 65)

    # Stage D: Automatic Git Release Commit
    version_changed = (rollback_state is not None and version != rollback_state.get("old_version"))
    if version_changed and not args.no_git_commit:
        is_test_or_ci = bool(os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
        if not is_test_or_ci:
            create_release_git_commit(
                repo_root=REPO_ROOT,
                new_version=version,
                changes_text=rollback_state.get("changes_text", "")
            )


if __name__ == "__main__":
    main()
