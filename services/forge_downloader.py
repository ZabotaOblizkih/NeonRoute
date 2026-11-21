"""
services/forge_downloader.py
============================
Автоматический загрузчик и установщик SD WebUI Forge, стартового чекпоинта DreamShaper 8
и моделей ADetailer для NeonRoute.
Обеспечивает фоновое скачивание (с поддержкой докачки .part), каскадную распаковку .7z,
генерацию безопасного лаунчера forge_launcher.bat и регистрацию модели в ArtDirector.
"""

import os
import sys
import time
import json
import shutil
import logging
import threading
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, Callable, List, Union

import urllib.request
import urllib.error

from PyQt6.QtCore import QThread, pyqtSignal

import config
from core.forge_locator import (
    is_valid_forge_dir,
    update_server_config_forge_root,
    resolve_forge_models_directory,
    FORGE_LAUNCHER_CANDIDATES,
)
from services.llama_downloader import download_file

logger = logging.getLogger("NeonRoute.ForgeDownloader")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

FORGE_DEFAULT_GITHUB_API = "https://api.github.com/repos/lllyasviel/stable-diffusion-webui-forge/releases/latest"
FORGE_FALLBACK_URL = (
    "https://github.com/lllyasviel/stable-diffusion-webui-forge/releases/download/latest/webui_forge_cu121_torch231.7z"
)

DREAMSHAPER_URL = (
    "https://huggingface.co/Lykon/DreamShaper/resolve/main/DreamShaper_8_pruned.safetensors"
)
DREAMSHAPER_FILENAME = "dreamshaper_8.safetensors"

ADETAILER_MODELS: Tuple[Tuple[str, str], ...] = (
    (
        "face_yolov8m.pt",
        "https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8m.pt",
    ),
    (
        "hand_yolov9c.pt",
        "https://huggingface.co/Bingsu/adetailer/resolve/main/hand_yolov9c.pt",
    ),
)


@dataclass
class ForgeInstallSpec:
    """Аппаратные требования и URL-ресурсы для установки SD WebUI Forge."""
    supported: bool
    is_blackwell: bool
    gpu_summary: str
    forge_url: str
    checkpoint_url: str
    checkpoint_filename: str
    adetailer_models: Tuple[Tuple[str, str], ...]


def resolve_forge_hardware_spec() -> ForgeInstallSpec:
    """
    Опрашивает LayerBudgeter.detect_hardware_kv_profile() и формирует спецификацию
    установки Forge: совместимость GPU NVIDIA (VRAM >= 3.8 ГБ), флаг Blackwell
    (CC >= 10.0 или RTX 50xx) и ссылки на актуальные ассеты.
    """
    vendor = "unknown"
    vram_mb = 0
    compute_cap: Any = None
    gpu_name = "NVIDIA GeForce"

    try:
        from services.layer_budgeter import LayerBudgeter
        profile = LayerBudgeter.detect_hardware_kv_profile()
        vendor = str(profile.get("vendor", "")).lower()
        vram_mb = int(profile.get("vram_total_mb", 0))
        compute_cap = profile.get("compute_cap")
        gpu_name = str(profile.get("gpu_name", "NVIDIA GeForce"))
    except Exception as e:
        logger.debug(f"[ForgeDownloader] Ошибка детекта GPU через LayerBudgeter: {e}")

    # Проверка поддержки: строго NVIDIA и VRAM >= 3800 МБ
    supported = (vendor == "nvidia" and vram_mb >= 3800)

    # Проверка архитектуры Blackwell (RTX 50xx / compute capability >= 10.0)
    is_blackwell = False
    try:
        if compute_cap is not None and compute_cap != "N/A":
            cc_float = float(compute_cap)
            if cc_float >= 10.0:
                is_blackwell = True
    except (ValueError, TypeError):
        pass

    if "RTX 50" in gpu_name or "Blackwell" in gpu_name:
        is_blackwell = True

    gpu_summary = f"{gpu_name} ({vram_mb} МБ VRAM)" if vram_mb > 0 else gpu_name

    # Резолв URL Forge через GitHub Releases API (с таймаутом 5с и fallback)
    forge_url = FORGE_FALLBACK_URL
    try:
        req = urllib.request.Request(
            FORGE_DEFAULT_GITHUB_API,
            headers={"User-Agent": "NeonRoute-Forge-Downloader/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assets = data.get("assets", [])
            for asset in assets:
                aname = asset.get("name", "").lower()
                if aname.endswith(".7z") and ("webui_forge" in aname or "cu121" in aname):
                    found_url = asset.get("browser_download_url")
                    if found_url:
                        forge_url = found_url
                        break
    except Exception as e:
        logger.debug(f"[ForgeDownloader] GitHub API fallback к {FORGE_FALLBACK_URL}: {e}")

    return ForgeInstallSpec(
        supported=supported,
        is_blackwell=is_blackwell,
        gpu_summary=gpu_summary,
        forge_url=forge_url,
        checkpoint_url=DREAMSHAPER_URL,
        checkpoint_filename=DREAMSHAPER_FILENAME,
        adetailer_models=ADETAILER_MODELS,
    )


def resolve_extracted_forge_root(target_dir: Path) -> Optional[Path]:
    """
    Находит корневую рабочую папку Forge в target_dir или среди вложенных подпапок.
    Проверяет маркеры is_valid_forge_dir() или наличие launch.py / webui/launch.py.
    """
    target = Path(target_dir).resolve()
    if not target.exists():
        return None

    def _is_forge_root(p: Path) -> bool:
        if not p.is_dir():
            return False
        if is_valid_forge_dir(p):
            return True
        if (p / "webui" / "launch.py").is_file():
            return True
        if (p / "launch.py").is_file():
            return True
        return False

    # 1. Проверяем сам target_dir
    if _is_forge_root(target):
        return target

    # 2. Проверяем подпапки 1-го уровня (например, webui_forge_cu121_torch231)
    try:
        for child in target.iterdir():
            if child.is_dir() and _is_forge_root(child):
                return child
    except Exception:
        pass

    # 3. Проверяем подпапки 2-го уровня
    try:
        for child in target.iterdir():
            if child.is_dir():
                for grandchild in child.iterdir():
                    if grandchild.is_dir() and _is_forge_root(grandchild):
                        return grandchild
    except Exception:
        pass

    return None


def extract_7z_archive(archive_path: Path, target_dir: Path) -> Tuple[bool, str]:
    """
    Каскадная распаковка архива .7z:
    Шаг 1: Системный tar.exe (Windows 10/11 bsdtar с libarchive).
    Шаг 2: Поиск 7z.exe / 7za.exe (PATH, Program Files).
    Шаг 3: Библиотека py7zr.
    При успехе проверяет появление файлов Forge. При неудаче возвращает (False, "MANUAL_EXTRACT_REQUIRED").
    """
    archive_path = Path(archive_path).resolve()
    target_dir, _ = validate_or_fallback_forge_dir(target_dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"Ошибка создания папки распаковки {target_dir}: {e}"

    if not archive_path.is_file():
        return False, f"Файл архива не найден: {archive_path}"

    # Шаг 1: Системный tar.exe (bsdtar)
    tar_candidate = shutil.which("tar")
    if not tar_candidate:
        sys_root = os.environ.get("SystemRoot", r"C:\Windows")
        cand_p = Path(sys_root) / "System32" / "tar.exe"
        if cand_p.is_file():
            tar_candidate = str(cand_p)

    if tar_candidate:
        try:
            logger.info(f"[ForgeDownloader] Попытка распаковки через tar.exe: {archive_path}")
            cmd = [str(tar_candidate), "-xf", str(archive_path), "-C", str(target_dir)]
            res = subprocess.run(
                cmd,
                creationflags=CREATE_NO_WINDOW,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if res.returncode == 0 and resolve_extracted_forge_root(target_dir) is not None:
                logger.info("[ForgeDownloader] Успешно распаковано системным tar.exe.")
                return True, "Успешно распаковано через системный tar.exe."
            else:
                logger.debug(f"[ForgeDownloader] tar.exe код {res.returncode}, stderr: {res.stderr[:200]}")
        except Exception as e:
            logger.debug(f"[ForgeDownloader] Ошибка tar.exe: {e}")

    # Шаг 2: 7z.exe / 7za.exe
    seven_z_candidates = [
        shutil.which("7z"),
        shutil.which("7za"),
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    seven_z_bin = None
    for cand in seven_z_candidates:
        if cand and Path(cand).is_file():
            seven_z_bin = str(cand)
            break

    if seven_z_bin:
        try:
            logger.info(f"[ForgeDownloader] Попытка распаковки через 7z: {seven_z_bin}")
            cmd = [seven_z_bin, "x", str(archive_path), f"-o{str(target_dir)}", "-y"]
            res = subprocess.run(
                cmd,
                creationflags=CREATE_NO_WINDOW,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if res.returncode == 0 and resolve_extracted_forge_root(target_dir) is not None:
                logger.info("[ForgeDownloader] Успешно распаковано через 7z.exe.")
                return True, "Успешно распаковано через 7-Zip."
            else:
                logger.debug(f"[ForgeDownloader] 7z код {res.returncode}, stderr: {res.stderr[:200]}")
        except Exception as e:
            logger.debug(f"[ForgeDownloader] Ошибка 7z: {e}")

    # Шаг 3: py7zr fallback
    try:
        import py7zr
        logger.info(f"[ForgeDownloader] Попытка распаковки через py7zr: {archive_path}")
        with py7zr.SevenZipFile(str(archive_path), mode="r") as z:
            z.extractall(path=str(target_dir))

        if resolve_extracted_forge_root(target_dir) is not None:
            logger.info("[ForgeDownloader] Успешно распаковано через py7zr.")
            return True, "Успешно распаковано через py7zr."
    except Exception as e:
        logger.debug(f"[ForgeDownloader] Ошибка py7zr: {e}")

    return False, "MANUAL_EXTRACT_REQUIRED"


def create_forge_launcher_bat(forge_root: Path) -> Path:
    """
    Создаёт в forge_root / "forge_launcher.bat" надёжный командный лаунчер,
    который пробрасывает все флаги %* в launch.py и корректно инициализирует PATH,
    минуя затирание COMMANDLINE_ARGS= в официальном webui-user.bat.
    """
    forge_root = Path(forge_root).resolve()
    launcher_file = forge_root / "forge_launcher.bat"

    has_sub_webui = (forge_root / "webui" / "launch.py").is_file()
    has_local_launch = (forge_root / "launch.py").is_file()

    # Определение расположения портативного Python (в корне или уровнем выше)
    if (forge_root / "system" / "python").is_dir():
        py_prefix = "%BASE_DIR%system"
    elif (forge_root.parent / "system" / "python").is_dir():
        py_prefix = "%BASE_DIR%..\\system"
    else:
        py_prefix = None

    lines = [
        "@echo off",
        "set BASE_DIR=%~dp0",
    ]
    if py_prefix:
        lines.append(
            f"set PATH={py_prefix}\\python;{py_prefix}\\python\\Scripts;{py_prefix}\\git\\bin;%PATH%"
        )

    if has_sub_webui:
        lines.append('cd /d "%BASE_DIR%webui"')
    elif has_local_launch:
        lines.append('cd /d "%BASE_DIR%"')
    else:
        lines.append('cd /d "%BASE_DIR%webui"')

    lines.append("python.exe launch.py %*")
    lines.append("")

    content = "\r\n".join(lines)
    launcher_file.write_text(content, encoding="utf-8")
    logger.info(f"[ForgeDownloader] Создан безопасный лаунчер: {launcher_file}")
    return launcher_file


def get_default_forge_install_dir(preferred_name: str = "sd-webui-forge") -> Path:
    """
    Возвращает безопасный путь установки на реально существующем системном диске.
    Приоритет: C:/ (если диск существует) -> диск BASE_DIR -> первый найденный диск системы.
    """
    clean_name = preferred_name.strip("/\\") or "sd-webui-forge"
    if Path("C:/").exists():
        return Path(f"C:/{clean_name}").resolve()
    base_dir = getattr(config, "BASE_DIR", None)
    if base_dir and hasattr(base_dir, "drive") and base_dir.drive:
        b_drive = base_dir.drive
        if Path(b_drive + "/").exists():
            return Path(f"{b_drive}/{clean_name}").resolve()
    import string
    for letter in string.ascii_uppercase:
        d_path = Path(f"{letter}:/")
        if d_path.exists():
            return (d_path / clean_name).resolve()
    return Path(f"./{clean_name}").resolve()


def validate_or_fallback_forge_dir(target_dir: Union[str, Path, None]) -> Tuple[Path, bool]:
    """
    Проверяет валидность и существование физического диска для target_dir.
    Если диск отсутствует в системе, автоматически подбирает доступный существующий диск.
    Возвращает: (валидный Path, был_ли_фоллбэк: bool).
    """
    try:
        t_path = Path(target_dir).resolve() if target_dir else get_default_forge_install_dir()
    except Exception:
        return get_default_forge_install_dir(), True

    drive = t_path.drive
    if drive and not Path(drive + "/").exists():
        fallback = get_default_forge_install_dir(t_path.name or "sd-webui-forge")
        return fallback, True
    return t_path, False


def _check_disk_space(target_path: Path, required_bytes: int, part_path: Optional[Path] = None) -> Tuple[bool, int, int]:
    """Проверяет доступное место на диске с учётом уже скачанных байт .part."""
    try:
        check_dir = Path(target_path).resolve()
        while not check_dir.exists() and check_dir.parent != check_dir:
            check_dir = check_dir.parent
        usage = shutil.disk_usage(str(check_dir))
        free = usage.free
        if part_path and part_path.exists():
            free += part_path.stat().st_size
        return (free >= required_bytes, free, required_bytes)
    except Exception as e:
        logger.debug(f"[ForgeDownloader] check_disk_space error: {e}")
        return (True, 0, required_bytes)


def install_forge_pipeline(
    target_dir: Path,
    download_dreamshaper: bool = True,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Tuple[bool, str, str]:
    """
    Полный автоматический конвейер установки Forge:
    1. Проверка существования диска и создание целевой директории.
    2. Проверка свободного места на диске (>= 12 ГБ или 14.5 ГБ с DreamShaper).
    3. Скачивание архива Forge (stage="forge").
    4. Распаковка архива и создание forge_launcher.bat (stage="extract").
    5. Загрузка детекторов ADetailer (stage="adetailer").
    6. Скачивание DreamShaper 8 и регистрация в art_director_models.json (stage="checkpoint").
    """
    target_dir, fell_back = validate_or_fallback_forge_dir(target_dir)
    if fell_back:
        logger.warning(
            f"[ForgeDownloader] Целевой диск для Forge не найден в системе. "
            f"Автоматическое переключение на доступный диск: {target_dir}"
        )

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"[ForgeDownloader] Не удалось создать директорию {target_dir}: {e}")
        return False, "", f"Не удалось создать директорию установки '{target_dir}': {e}"

    spec = resolve_forge_hardware_spec()

    # Расчёт необходимого места: 12 ГБ без DreamShaper, 14.5 ГБ с DreamShaper
    required_bytes = int((14.5 if download_dreamshaper else 12.0) * 1024 * 1024 * 1024)
    archive_path = target_dir / "webui_forge_cu121_torch231.7z"
    part_path = archive_path.with_suffix(archive_path.suffix + ".part")

    ok_space, free_bytes, req_bytes = _check_disk_space(target_dir, required_bytes, part_path)
    if not ok_space:
        free_gb = free_bytes / (1024 ** 3)
        req_gb = req_bytes / (1024 ** 3)
        return (
            False,
            "",
            f"Недостаточно свободного места на диске ({free_gb:.1f} ГБ доступно, требуется минимум {req_gb:.1f} ГБ).",
        )

    # ---------------------------------------------------------
    # Этап 1: Скачивание архива Forge
    # ---------------------------------------------------------
    if not archive_path.is_file():
        if progress_cb:
            progress_cb("forge", 0, 100)

        def _forge_prog(cur: int, tot: int):
            if progress_cb:
                progress_cb("forge", cur, tot)

        try:
            logger.info(f"[ForgeDownloader] Скачивание Forge из {spec.forge_url} в {archive_path} ...")
            download_file(
                spec.forge_url,
                archive_path,
                progress_callback=_forge_prog,
                timeout=60,
                max_retries=3,
                cancel_event=cancel_event,
            )
        except InterruptedError:
            return False, "", "Загрузка отменена пользователем."
        except Exception as e:
            return False, "", f"Ошибка скачивания архива Forge: {e}"

    if cancel_event and cancel_event.is_set():
        return False, "", "Загрузка отменена пользователем."

    # ---------------------------------------------------------
    # Этап 2: Распаковка архива и создание лаунчера
    # ---------------------------------------------------------
    resolved_root = resolve_extracted_forge_root(target_dir)
    if not resolved_root:
        if progress_cb:
            progress_cb("extract", 0, 100)

        ok_extract, extract_msg = extract_7z_archive(archive_path, target_dir)
        if not ok_extract:
            return (
                False,
                "",
                f"Не удалось автоматически распаковать архив Forge ({extract_msg}). "
                f"Пожалуйста, распакуйте {archive_path.name} вручную архиватором 7-Zip в папку {target_dir}.",
            )

        resolved_root = resolve_extracted_forge_root(target_dir)
        if not resolved_root:
            return False, "", "Не удалось обнаружить корневую папку Forge после распаковки."

    # Создание лаунчера и привязка к server_config.ini
    create_forge_launcher_bat(resolved_root)
    update_server_config_forge_root(resolved_root)

    # Удаление скачанного архива после успешной распаковки для экономии места
    if archive_path.is_file():
        try:
            archive_path.unlink(missing_ok=True)
            logger.info("[ForgeDownloader] Архив Forge удален после успешной распаковки.")
        except Exception as e:
            logger.debug(f"[ForgeDownloader] Не удалось удалить архив: {e}")

    # Best-effort применение патчей совместимости NeonRoute
    try:
        from services.forge_patcher import ForgePatcher
        ok_patch, patch_msg = ForgePatcher.apply_patch(resolved_root)
        logger.info(f"[ForgeDownloader] Best-effort ForgePatcher: ok={ok_patch}, {patch_msg}")
    except Exception as e:
        logger.warning(f"[ForgeDownloader] Предупреждение ForgePatcher: {e}")

    if cancel_event and cancel_event.is_set():
        return False, str(resolved_root), "Установка отменена пользователем."

    # ---------------------------------------------------------
    # Этап 3: Загрузка детекторов ADetailer
    # ---------------------------------------------------------
    if (resolved_root / "webui" / "models").is_dir():
        ad_dir = resolved_root / "webui" / "models" / "adetailer"
    else:
        ad_dir = resolved_root / "models" / "adetailer"
    ad_dir.mkdir(parents=True, exist_ok=True)

    if progress_cb:
        progress_cb("adetailer", 0, 100)

    for ad_filename, ad_url in spec.adetailer_models:
        if cancel_event and cancel_event.is_set():
            return False, str(resolved_root), "Установка отменена пользователем."

        ad_dest = ad_dir / ad_filename
        if not ad_dest.is_file() or ad_dest.stat().st_size < 1000:
            def _ad_prog(cur: int, tot: int):
                if progress_cb:
                    progress_cb("adetailer", cur, tot)

            try:
                logger.info(f"[ForgeDownloader] Загрузка ADetailer {ad_filename} ...")
                download_file(
                    ad_url,
                    ad_dest,
                    progress_callback=_ad_prog,
                    timeout=45,
                    max_retries=2,
                    cancel_event=cancel_event,
                )
            except InterruptedError:
                return False, str(resolved_root), "Установка отменена пользователем."
            except Exception as e:
                logger.warning(f"[ForgeDownloader] Предупреждение загрузки детектора {ad_filename}: {e}")

    if cancel_event and cancel_event.is_set():
        return False, str(resolved_root), "Установка отменена пользователем."

    # ---------------------------------------------------------
    # Этап 4: Скачивание DreamShaper 8 и регистрация профиля
    # ---------------------------------------------------------
    if download_dreamshaper:
        if (resolved_root / "webui" / "models" / "Stable-diffusion").is_dir():
            models_dir = resolved_root / "webui" / "models" / "Stable-diffusion"
        elif (resolved_root / "models" / "Stable-diffusion").is_dir():
            models_dir = resolved_root / "models" / "Stable-diffusion"
        else:
            models_dir = resolve_forge_models_directory(resolved_root)
        models_dir.mkdir(parents=True, exist_ok=True)

        ckpt_dest = models_dir / spec.checkpoint_filename
        has_existing_ckpts = any(
            f.is_file() and f.suffix.lower() in (".safetensors", ".ckpt")
            for f in models_dir.iterdir()
        )

        if not ckpt_dest.is_file() and not has_existing_ckpts:
            if progress_cb:
                progress_cb("checkpoint", 0, 100)

            def _ckpt_prog(cur: int, tot: int):
                if progress_cb:
                    progress_cb("checkpoint", cur, tot)

            try:
                logger.info(f"[ForgeDownloader] Скачивание DreamShaper 8 в {ckpt_dest} ...")
                download_file(
                    spec.checkpoint_url,
                    ckpt_dest,
                    progress_callback=_ckpt_prog,
                    timeout=60,
                    max_retries=3,
                    cancel_event=cancel_event,
                )
            except InterruptedError:
                return False, str(resolved_root), "Установка отменена пользователем."
            except Exception as e:
                return (
                    False,
                    str(resolved_root),
                    f"Forge установлен, но произошла ошибка при скачивании DreamShaper 8: {e}",
                )

        # Регистрация / активация DreamShaper 8 в data/art_director_models.json
        try:
            from services.art_director_config import load_art_director_models, save_art_director_models
            art_models = load_art_director_models()
            found = False
            for m in art_models:
                m_ckpt = Path(m.get("checkpoint", "")).name.lower()
                if m_ckpt == spec.checkpoint_filename.lower() or m.get("id") == "fantasy_art":
                    m["is_default"] = True
                    m["active"] = True
                    m["checkpoint"] = spec.checkpoint_filename
                    found = True
                else:
                    m["is_default"] = False

            if not found:
                art_models.append({
                    "id": "dreamshaper_8",
                    "name": "DreamShaper 8",
                    "checkpoint": spec.checkpoint_filename,
                    "routing_description": "Художественные арты, цифровая живопись, магия, сказочные расы, фэнтези и концепт-арт.",
                    "is_default": True,
                    "active": True,
                    "triggers": ["art", "fantasy", "digital painting", "illustration", "concept art", "рисунок", "картина", "живопись", "фэнтези", "арт", "эльф", "магия"],
                    "description": "Художественные арты, цифровая живопись, магия, фэнтези."
                })
            save_art_director_models(art_models)

            # Перезагрузка профилей роутера (если синглтон активен)
            try:
                from services.model_router import ModelRouter
                if hasattr(ModelRouter, "_instance") and ModelRouter._instance:
                    ModelRouter._instance.load_profiles()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"[ForgeDownloader] Ошибка обновления art_director_models.json: {e}")

    return True, str(resolved_root), "SD WebUI Forge успешно установлен и настроен."


# =====================================================================
# QThread Worker и Singleton для GUI
# =====================================================================

class ForgeDownloadWorker(QThread):
    """
    Фоновый рабочий поток скачивания и распаковки SD WebUI Forge.
    Сигналы:
    - progress_signal(stage: str, downloaded_bytes: int, total_bytes: int, speed_mb_s: float)
    - finished_signal(success: bool, resolved_forge_root: str, message: str)
    """
    progress_signal = pyqtSignal(str, int, int, float)
    finished_signal = pyqtSignal(bool, str, str)

    def __init__(self, target_dir: Path, download_dreamshaper: bool = True):
        super().__init__(parent=None)
        self.target_dir = Path(target_dir).resolve()
        self.download_dreamshaper = download_dreamshaper
        self._cancel_event = threading.Event()

        # Параметры троттлинга (0.12 с) и вычисления скорости
        self._last_emit_time = 0.0
        self._last_bytes = 0
        self._current_stage = ""
        self.last_downloaded = 0
        self.last_total = 0
        self.last_speed_mb = 0.0

        self.finished.connect(self._on_worker_finished)

    def cancel(self):
        """Безопасная отмена загрузки без удаления .part файла (для докачки)."""
        logger.info("[ForgeDownloadWorker] Получен сигнал отмены загрузки Forge.")
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _on_worker_finished(self):
        global _ACTIVE_FORGE_WORKER
        if _ACTIVE_FORGE_WORKER is self:
            _ACTIVE_FORGE_WORKER = None
        self.deleteLater()

    def _progress_callback(self, stage: str, downloaded: int, total: int):
        now = time.monotonic()
        first_in_stage = (stage != self._current_stage)
        if first_in_stage:
            self._current_stage = stage
            self._last_bytes = 0
            self._last_emit_time = 0.0

        dt = now - self._last_emit_time
        if dt >= 0.12 or (total > 0 and downloaded >= total) or downloaded == 0 or first_in_stage:
            delta_bytes = max(0, downloaded - self._last_bytes)
            speed_mb = (delta_bytes / (1024 * 1024)) / dt if dt > 0.001 else 0.0

            self._last_emit_time = now
            self._last_bytes = downloaded
            self.last_downloaded = downloaded
            self.last_total = total
            self.last_speed_mb = speed_mb

            self.progress_signal.emit(stage, downloaded, total, speed_mb)

    def run(self):
        global _ACTIVE_FORGE_WORKER
        _ACTIVE_FORGE_WORKER = self
        logger.info(f"[ForgeDownloadWorker] Старт установки Forge в {self.target_dir}")

        try:
            ok, root_path, msg = install_forge_pipeline(
                target_dir=self.target_dir,
                download_dreamshaper=self.download_dreamshaper,
                progress_cb=self._progress_callback,
                cancel_event=self._cancel_event,
            )
            self.finished_signal.emit(ok, root_path, msg)
        except Exception as e:
            logger.error(f"[ForgeDownloadWorker] Критическое исключение установки: {e}", exc_info=True)
            self.finished_signal.emit(False, "", f"Сбой процесса установки: {e}")


_ACTIVE_FORGE_WORKER: Optional[ForgeDownloadWorker] = None


def start_forge_download_worker(worker: ForgeDownloadWorker) -> None:
    """
    Регистрирует воркер в глобальном реестре ДО запуска QThread.

    Если сильная ссылка на QThread появляется только внутри run(), Python
    успевает уничтожить объект сразу после worker.start(), пока новый поток
    ещё не получил GIL. Это приводит к фатальному падению процесса:
    "QThread: Destroyed while thread is still running" (0xC0000409).
    """
    global _ACTIVE_FORGE_WORKER
    _ACTIVE_FORGE_WORKER = worker
    try:
        worker.start()
    except Exception:
        if _ACTIVE_FORGE_WORKER is worker:
            _ACTIVE_FORGE_WORKER = None
        raise


def get_active_forge_download_worker() -> Optional[ForgeDownloadWorker]:
    """Возвращает текущий активный экземпляр ForgeDownloadWorker, если он выполняется."""
    global _ACTIVE_FORGE_WORKER
    if _ACTIVE_FORGE_WORKER is not None and _ACTIVE_FORGE_WORKER.isRunning():
        return _ACTIVE_FORGE_WORKER
    return None
