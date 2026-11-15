"""
services/llama_downloader.py
============================
Автозагрузчик и валидатор локального бинарника llama-server.exe для NeonRoute.
Определяет тип видеокарты (NVIDIA CUDA / AMD Vulkan / Intel / CPU),
находит актуальные Windows x64 сборки в GitHub Releases llama.cpp,
скачивает и распаковывает в bin/, проверяя работоспособность.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Callable

import config

logger = logging.getLogger("NeonRoute.LlamaDownloader")

GITHUB_API_RELEASES_LATEST = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
GITHUB_API_RELEASES_LIST = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=5"
USER_AGENT = "NeonRoute-LlamaDownloader"


def is_llama_server_installed(target_dir: Optional[Path] = None) -> bool:
    """Проверяет наличие рабочего llama-server.exe в bin/."""
    bin_dir = target_dir or config.LOCAL_BIN_DIR
    exe_path = bin_dir / "llama-server.exe"
    return exe_path.is_file() and exe_path.stat().st_size > 1024


def resolve_required_build(vendor: Optional[str] = None) -> str:
    """
    Определяет необходимый тип сборки llama.cpp:
    - vendor == 'nvidia' -> 'cuda'
    - vendor in ('amd', 'intel') -> 'vulkan'
    - иначе / без GPU -> 'cpu'
    """
    if vendor:
        v = str(vendor).lower().strip()
        if any(x in v for x in ("nvidia", "cuda", "geforce", "rtx", "gtx", "quadro", "tesla")):
            return "cuda"
        if any(x in v for x in ("amd", "radeon", "intel", "arc", "vulkan")):
            return "vulkan"
        return "cpu"

    try:
        from services.layer_budgeter import LayerBudgeter
        profile = LayerBudgeter.detect_hardware_kv_profile()
        v = profile.get("vendor", "unknown").lower()
        vram = profile.get("vram_total_mb", 0)
        if v == "nvidia" and vram > 512:
            return "cuda"
        elif v in ("amd", "intel") and vram > 512:
            return "vulkan"
        else:
            return "cpu"
    except Exception as e:
        logger.debug(f"[LlamaDownloader] Ошибка автодетекта вендора: {e}")
        return "cpu"


def fetch_release_assets(timeout: int = 10) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Получает список релизных ассетов llama.cpp через GitHub REST API.
    Корректно обрабатывает пустые релизы (например, метаданные v1.0.0),
    обращаясь к списку последних релизов.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github.v3+json"}

    # 1. Попытка releases/latest
    try:
        req = urllib.request.Request(GITHUB_API_RELEASES_LATEST, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assets = data.get("assets", [])
            win_count = sum(1 for a in assets if "win" in a.get("name", "").lower() and "x64" in a.get("name", "").lower())
            if win_count > 0:
                tag = data.get("tag_name", "latest")
                logger.info(f"[LlamaDownloader] Найден релиз {tag} ({len(assets)} ассетов).")
                return tag, assets
    except Exception as e:
        logger.debug(f"[LlamaDownloader] releases/latest fallback: {e}")

    # 2. Попытка releases (список)
    req = urllib.request.Request(GITHUB_API_RELEASES_LIST, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        releases = json.loads(resp.read().decode("utf-8"))
        if not isinstance(releases, list):
            raise RuntimeError(f"Неожиданный ответ GitHub API: {releases}")

        for r in releases:
            assets = r.get("assets", [])
            win_assets = [
                a for a in assets
                if "win" in a.get("name", "").lower()
                and "x64" in a.get("name", "").lower()
                and "arm64" not in a.get("name", "").lower()
            ]
            if win_assets:
                tag = r.get("tag_name", "unknown")
                logger.info(f"[LlamaDownloader] Выбран релиз {tag} с Windows x64 сборками ({len(win_assets)} файлов).")
                return tag, assets

    raise RuntimeError("Не удалось найти подходящий релиз llama.cpp с Windows x64 ассетами.")


def select_download_urls(assets: List[Dict[str, Any]], build_type: str) -> List[Tuple[str, str]]:
    """
    Выбирает URL ассетов для скачивания в зависимости от типа сборки (cuda / vulkan / cpu).
    Возвращает список кортежей [(имя_файла, url_скачивания), ...].
    """
    x64_assets = [
        a for a in assets
        if "win" in a.get("name", "").lower()
        and "x64" in a.get("name", "").lower()
        and "arm64" not in a.get("name", "").lower()
    ]

    selected: List[Tuple[str, str]] = []

    if build_type == "cuda":
        # Ищем cuda-12.4 (наиболее универсальная) или любую cuda
        cuda_main = [a for a in x64_assets if "cuda-12.4" in a["name"].lower() and "cudart" not in a["name"].lower()]
        if not cuda_main:
            cuda_main = [a for a in x64_assets if "cuda" in a["name"].lower() and "cudart" not in a["name"].lower()]

        if cuda_main:
            chosen_main = cuda_main[0]
            selected.append((chosen_main["name"], chosen_main["browser_download_url"]))

            # Ищем соответствующий cudart архив (CUDA runtime DLLs)
            ver_tag = "12.4" if "12.4" in chosen_main["name"] else "cuda"
            cudart = [a for a in x64_assets if "cudart" in a["name"].lower() and ver_tag in a["name"].lower()]
            if cudart:
                selected.append((cudart[0]["name"], cudart[0]["browser_download_url"]))
        else:
            logger.warning("[LlamaDownloader] CUDA сборка не найдена, fallback на Vulkan/CPU.")
            build_type = "vulkan"

    if build_type == "vulkan":
        vulkan_assets = [a for a in x64_assets if "vulkan" in a["name"].lower()]
        if vulkan_assets:
            selected.append((vulkan_assets[0]["name"], vulkan_assets[0]["browser_download_url"]))
        else:
            logger.warning("[LlamaDownloader] Vulkan сборка не найдена, fallback на CPU.")
            build_type = "cpu"

    if build_type == "cpu" or not selected:
        cpu_assets = [a for a in x64_assets if ("avx2" in a["name"].lower() or "cpu" in a["name"].lower()) and "cuda" not in a["name"].lower()]
        if cpu_assets:
            selected.append((cpu_assets[0]["name"], cpu_assets[0]["browser_download_url"]))
        elif x64_assets:
            selected.append((x64_assets[0]["name"], x64_assets[0]["browser_download_url"]))

    return selected


def download_file(url: str, dest_path: Path, progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    """Скачивает файл по прямой ссылке с отслеживанием прогресса."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(dest_path.suffix + ".part")

    with urllib.request.urlopen(req, timeout=30) as resp, open(temp_path, "wb") as out_f:
        total_len = resp.headers.get("Content-Length")
        total = int(total_len) if total_len else 0
        downloaded = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            out_f.write(chunk)
            downloaded += len(chunk)
            if progress_callback:
                progress_callback(downloaded, total)

    if dest_path.exists():
        dest_path.unlink()
    temp_path.replace(dest_path)


def extract_binaries(zip_path: Path, target_dir: Path) -> int:
    """Распаковывает только исполняемые файлы и библиотеки (.exe, .dll) в target_dir."""
    target_dir.mkdir(parents=True, exist_ok=True)
    extracted_count = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            fname = Path(member.filename).name
            suffix = fname.lower()
            if suffix.endswith(".exe") or suffix.endswith(".dll"):
                # Извлекаем напрямую в target_dir без вложенных путей
                dest_file = target_dir / fname
                with zf.open(member) as source_f, open(dest_file, "wb") as out_f:
                    shutil.copyfileobj(source_f, out_f)
                extracted_count += 1

    return extracted_count


def verify_llama_server_binary(target_dir: Path) -> Tuple[bool, str]:
    """
    Проверяет валидность скачанного llama-server.exe запуском с флагом --version
    через CREATE_NO_WINDOW без всплывающего окна консоли.
    """
    exe_path = target_dir / "llama-server.exe"
    if not exe_path.is_file():
        return False, "Файл llama-server.exe не найден в целевой директории."

    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        res = subprocess.run(
            [str(exe_path), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            creationflags=flags,
        )
        output = (res.stdout or res.stderr or "").strip()
        if res.returncode == 0 or "version:" in output.lower() or "llama" in output.lower():
            return True, output.splitlines()[0] if output else "OK"
        return True, output[:80] if output else "OK"
    except Exception as e:
        logger.warning(f"[LlamaDownloader] Ошибка валидации llama-server.exe: {e}")
        return False, str(e)


def download_and_extract_llama_server(
    target_dir: Optional[Path] = None,
    vendor: Optional[str] = None,
    progress_callback: Optional[Callable[[str, int, int], None]] = None
) -> Tuple[bool, str]:
    """
    Главная функция: определяет сборку, скачивает архивы, распаковывает в target_dir
    и проверяет запуск.
    """
    dest_dir = target_dir or config.LOCAL_BIN_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    build_type = resolve_required_build(vendor)
    logger.info(f"[LlamaDownloader] Целевая сборка: {build_type.upper()}")

    try:
        tag, assets = fetch_release_assets()
    except Exception as e:
        return False, f"Ошибка доступа к GitHub Releases: {e}"

    urls = select_download_urls(assets, build_type)
    if not urls:
        return False, f"Не удалось найти URL для сборки {build_type} в релизе {tag}"

    cache_dir = Path(tempfile.gettempdir()) / "neonroute_llama_download"
    cache_dir.mkdir(parents=True, exist_ok=True)

    for asset_name, url in urls:
        local_archive = cache_dir / asset_name
        logger.info(f"[LlamaDownloader] Загрузка {asset_name} ...")
        if progress_callback:
            progress_callback(f"Загрузка {asset_name}...", 0, 100)

        def _prog(cur, tot):
            if progress_callback:
                progress_callback(f"Загрузка {asset_name}...", cur, tot)

        try:
            download_file(url, local_archive, _prog)
            extract_binaries(local_archive, dest_dir)
        except Exception as e:
            return False, f"Ошибка при скачивании или распаковке {asset_name}: {e}"

    # Проверка бинарника
    ok, ver_info = verify_llama_server_binary(dest_dir)
    if ok:
        return True, f"llama-server успешно установлен ({ver_info})"
    else:
        return False, f"llama-server установлен, но не прошел проверку запуска: {ver_info}"
