"""
NeonRoute Automated Release Builder & Quality Gate Pipeline.
Stages:
  Stage A: AST Audit of 3rd Party Dependencies vs requirements.txt
  Stage B: Test Quality Gate (pytest)
  Stage C: Sterile Sanitized Package Assembly & Zip Bundling
"""

import argparse
import ast
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
from typing import Dict, List, Set, Tuple

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
        s_item = src / item
        d_item = dst / item
        if s_item.is_dir():
            _copy_clean_tree(s_item, d_item)
        elif s_item.is_file():
            shutil.copy2(s_item, d_item)


def main():
    parser = argparse.ArgumentParser(description="NeonRoute Automated Release Builder & Quality Gate")
    parser.add_argument("--skip-dep-check", action="store_true", help="Skip AST dependency check vs requirements.txt")
    parser.add_argument("--skip-tests", action="store_true", help="Skip pytest quality gate")
    parser.add_argument("--fast", action="store_true", help="Run fast test suite subset for quick build verification")
    parser.add_argument("--with-tests", action="store_true", help="Include tests/ folder in the release package (default: excluded)")
    parser.add_argument("--bundle-wheels", action="store_true", help="Download offline wheels package into release/wheels/")
    parser.add_argument("--no-embedded-python", action="store_true", help="Exclude bundled portable Python runtime from release package")
    args = parser.parse_args()

    version = config.APP_VERSION
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
            sys.exit(1)
        print("  [OK] AST Dependency Audit PASSED: all required runtime imports match requirements.txt.")
    else:
        print("\n[Stage A] AST Dependency Audit SKIPPED (--skip-dep-check).")

    # Stage B: Test Quality Gate
    if not args.skip_tests:
        test_passed = run_test_gate(REPO_ROOT, fast=args.fast)
        if not test_passed:
            print("\n  [ERROR] Test Quality Gate FAILED. Release build aborted.\n")
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


if __name__ == "__main__":
    main()
