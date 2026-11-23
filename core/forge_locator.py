from pathlib import Path
from typing import Optional, Any, Dict, List, Tuple
import configparser
import logging
import os
import subprocess
import sys
import re
import string

logger = logging.getLogger("NeonRoute.ForgeLocator")

# Приоритет маркеров: специфичный для сборки forge_launcher.bat идет первым
FORGE_LAUNCHER_CANDIDATES = [
    "forge_launcher.bat",
    "webui-user.bat",
    "run.bat",
    "launch.py",
]

_module_dir = Path(__file__).resolve().parent
REPO_ROOT = _module_dir.parent if _module_dir.name in ("core", "services", "utils") else _module_dir
try:
    import config
    SERVER_CONFIG_INI_PATH = getattr(config, "SERVER_CONFIG_INI_PATH", REPO_ROOT / "server_config.ini")
except Exception:
    SERVER_CONFIG_INI_PATH = REPO_ROOT / "server_config.ini"


def is_valid_forge_dir(path: Path | str | None) -> bool:
    """Проверяет наличие маркеров SD WebUI Forge в директории."""
    if not path:
        return False
    try:
        p = Path(path)
        if not p.is_dir():
            return False
        return any((p / launcher).is_file() for launcher in FORGE_LAUNCHER_CANDIDATES)
    except Exception:
        return False

def parse_forge_launcher_args(launcher_path: Optional[Path]) -> Dict[str, Path]:
    """
    Парсит bat/cmd/sh файл лаунчера Forge на наличие аргументов командной строки:
    --models-dir, --ckpt-dir, --lora-dir, --embeddings-dir.
    Корректно извлекает пути с пробелами и кавычками.
    Возвращает словарь с нормализованными (resolve) путями для найденных параметров.
    """
    custom_paths: Dict[str, Path] = {}
    if not launcher_path:
        return custom_paths

    try:
        launcher = Path(launcher_path).resolve()
        if not launcher.is_file():
            return custom_paths

        # Читаем батник с безопасными фоллбеками по кодировкам
        content = ""
        for enc in ("utf-8", "cp1251", "latin-1"):
            try:
                content = launcher.read_text(encoding=enc)
                break
            except Exception:
                continue

        # Ищем строки передачи аргументов или COMMANDLINE_ARGS
        # Поддерживаем флаги: --models-dir, --ckpt-dir, --lora-dir, --embeddings-dir
        target_flags = {
            "--models-dir": "models_dir",
            "--ckpt-dir": "ckpt_dir",
            "--lora-dir": "lora_dir",
            "--embeddings-dir": "embeddings_dir",
        }

        # Регулярка для захвата: --flag <значение> (в кавычках или без, через пробел или =)
        pattern = re.compile(
            r'(--(?:models|ckpt|lora|embeddings)-dir)(?:[\s=]+)(?:"([^"]+)"|([^\s]+))',
            re.IGNORECASE
        )
        for match in pattern.finditer(content):
            flag = match.group(1).lower()
            val = match.group(2) or match.group(3)
            if val and flag in target_flags:
                key = target_flags[flag]
                path_obj = Path(val.strip().strip('"').strip("'"))
                # Если путь относительный, считаем его относительно рабочей папки лаунчера
                if not path_obj.is_absolute():
                    path_obj = launcher.parent / path_obj
                custom_paths[key] = path_obj.resolve()

    except Exception:
        pass

    return custom_paths


def resolve_forge_models_directory(forge_root: Optional[Path], launcher_path: Optional[Path] = None) -> Path:
    """
    Определяет реальную директорию чекпоинтов Stable Diffusion с учетом аргументов лаунчера и симлинков.
    Приоритет:
    1. --ckpt-dir из лаунчера.
    2. --models-dir / "Stable-diffusion" из лаунчера.
    3. forge_root / "models" / "Stable-diffusion".
    """
    custom = parse_forge_launcher_args(launcher_path)
    if "ckpt_dir" in custom and custom["ckpt_dir"].exists():
        return custom["ckpt_dir"]

    if "models_dir" in custom and custom["models_dir"].exists():
        sd_sub = custom["models_dir"] / "Stable-diffusion"
        return sd_sub.resolve()

    if "ckpt_dir" in custom:
        return custom["ckpt_dir"]

    if "models_dir" in custom:
        return (custom["models_dir"] / "Stable-diffusion").resolve()

    if forge_root:
        return (Path(forge_root).resolve() / "models" / "Stable-diffusion")

    return (REPO_ROOT.parent / "neyroGPD" / "models" / "Stable-diffusion").resolve()



def _log_passport(forge_root: Any, source_name: str) -> None:
    """Выводит структурированный диагностический паспорт обнаруженной установки Forge."""
    try:
        resolved = Path(forge_root).resolve()
        launcher_path = resolved / "forge_launcher.bat"
        for cand in FORGE_LAUNCHER_CANDIDATES:
            try:
                cand_p = resolved / cand
                if cand_p.is_file():
                    launcher_path = cand_p
                    break
            except Exception:
                pass

        launcher_exists = False
        try:
            launcher_exists = bool(launcher_path.exists())
        except Exception:
            pass
        launcher_status = "EXISTS" if launcher_exists else "NOT_FOUND"

        models_dir = resolved / "models"
        models_exists = False
        try:
            models_exists = bool(models_dir.exists())
        except Exception:
            pass
        models_status = "EXISTS" if models_exists else "NOT_FOUND"

        passport = (
            f"\n ├── Статус: ОБНАРУЖЕН (источник: {source_name})\n"
            f" ├── Корень Forge: {resolved}\n"
            f" ├── Лаунчер: {launcher_path} [{launcher_status}]\n"
            f" └── Каталог моделей: {models_dir} [{models_status}]"
        )
        logger.info(f"[NeonRoute.ForgeLocator] {passport}")
    except Exception as e:
        logger.debug(f"[NeonRoute.ForgeLocator] Ошибка формирования паспорта: {e}")


def find_forge_root(
    configured_path: Optional[str | Path] = None,
    config_file: Optional[str | Path] = None
) -> Optional[Path]:
    """
    Каскадный поиск рабочей директории Forge:
    1. Проверка пути из переданного аргумента/конфига (если валиден — мгновенный возврат).
    2. Проверка переменной окружения FORGE_ROOT_DIR.
    3. Значение forge_root_dir из секции [PATHS] в server_config.ini.
    4. Относительные каталоги рядом с репозиторием (../neyroGPD, ../sd-webui-forge, ./forge).
    5. Типовые локации на всех доступных дисках Windows (C:/, D:/ и т.д.):
       - <диск>:/neyroGPD
       - <диск>:/sd-webui-forge
       - <диск>:/AI/Forge
    """
    # 1. Сконфигурированный путь
    if configured_path and is_valid_forge_dir(configured_path):
        resolved = Path(configured_path).resolve()
        logger.info(f"[NeonRoute.ForgeLocator] Путь получен из прямого аргумента: {resolved}")
        _log_passport(resolved, "direct_argument")
        return resolved

    # 2. Переменная окружения FORGE_ROOT_DIR
    env_root = os.getenv("FORGE_ROOT_DIR")
    if env_root and is_valid_forge_dir(env_root):
        resolved = Path(env_root).resolve()
        logger.info(f"[NeonRoute.ForgeLocator] Путь получен из переменной FORGE_ROOT_DIR: {resolved}")
        _log_passport(resolved, "FORGE_ROOT_DIR")
        return resolved

    # 3. Значение forge_root_dir из секции [PATHS] в server_config.ini
    ini_path = Path(config_file) if config_file else SERVER_CONFIG_INI_PATH
    if ini_path.is_file():
        try:
            parser = configparser.ConfigParser()
            parser.read(str(ini_path), encoding="utf-8")
            if parser.has_section("PATHS"):
                ini_forge = (
                    parser.get("PATHS", "forge_root_dir", fallback=None)
                    or parser.get("PATHS", "forge_path", fallback=None)
                )
                if ini_forge:
                    ini_forge = ini_forge.strip().strip('"').strip("'")
                    if ini_forge and is_valid_forge_dir(ini_forge):
                        resolved = Path(ini_forge).resolve()
                        logger.info(f"[NeonRoute.ForgeLocator] Путь получен из server_config.ini ([PATHS] -> forge_root_dir): {resolved}")
                        _log_passport(resolved, "server_config.ini ([PATHS] -> forge_root_dir)")
                        return resolved
        except Exception:
            pass

    # 4. Относительные пути от корня репозитория
    repo_root = REPO_ROOT
    rel_candidates = [
        repo_root.parent / "neyroGPD",
        repo_root.parent / "sd-webui-forge",
        repo_root.parent / "Forge",
        repo_root / "forge",
    ]
    for c in rel_candidates:
        if is_valid_forge_dir(c):
            resolved = c.resolve()
            logger.info(f"[NeonRoute.ForgeLocator] Обнаружен относительный каталог установки: {resolved}")
            _log_passport(resolved, "relative_directory")
            return resolved

    # 5. Проверка корней смонтированных дисков (без рекурсии)
    available_drives = [
        Path(f"{d}:/") for d in string.ascii_uppercase
        if Path(f"{d}:/").exists()
    ]
    common_names = ["neyroGPD", "sd-webui-forge", "Forge", "AI/Forge", "webui_forge"]
    for drive in available_drives:
        for name in common_names:
            c = drive / name
            if is_valid_forge_dir(c):
                resolved = c.resolve()
                drive_str = str(drive)
                logger.info(f"[NeonRoute.ForgeLocator] Обнаружена установка при сканировании диска {drive_str}: {resolved}")
                _log_passport(resolved, f"drive_scan ({drive_str})")
                return resolved

    return None


def update_server_config_forge_root(
    new_path: Path | str,
    config_file: Optional[Path | str] = None
) -> bool:
    """
    Безопасно записывает forge_root_dir в секцию [PATHS] файла server_config.ini
    и обновляет активные пути в config.py в runtime.
    """
    try:
        resolved_path = Path(new_path).resolve()
        ini_path = Path(config_file) if config_file else SERVER_CONFIG_INI_PATH
        ini_path.parent.mkdir(parents=True, exist_ok=True)

        parser = configparser.ConfigParser()
        if ini_path.is_file():
            parser.read(str(ini_path), encoding="utf-8")

        if not parser.has_section("PATHS"):
            parser.add_section("PATHS")

        parser.set("PATHS", "forge_root_dir", str(resolved_path))

        with open(str(ini_path), "w", encoding="utf-8") as f:
            parser.write(f)

        # Синхронизация активных путей в config.py в runtime
        try:
            import config
            config.FORGE_ROOT_DIR = resolved_path
            _candidate_names = ["forge_launcher.bat", "webui-user.bat", "run.bat"]
            _found_launcher = next(
                (resolved_path / name for name in _candidate_names if (resolved_path / name).exists()),
                None
            )
            config.FORGE_LAUNCHER_PATH = (
                _found_launcher if _found_launcher is not None else (resolved_path / "forge_launcher.bat")
            )
            config.FORGE_SD_MODELS_DIR = resolve_forge_models_directory(config.FORGE_ROOT_DIR, config.FORGE_LAUNCHER_PATH)

            _custom_args = parse_forge_launcher_args(config.FORGE_LAUNCHER_PATH)
            lora_dirs = [
                resolved_path / "webui_forge_cu121_torch231" / "webui" / "models" / "Lora",
                resolved_path / "webui" / "models" / "Lora",
                resolved_path / "models" / "Lora"
            ]
            if "lora_dir" in _custom_args and _custom_args["lora_dir"] not in lora_dirs:
                lora_dirs.insert(0, _custom_args["lora_dir"])
            config.FORGE_LORA_DIRS = lora_dirs
            config.DEFAULT_FORGE_LORA_DIR = next(
                (p for p in config.FORGE_LORA_DIRS if p.exists()),
                config.FORGE_LORA_DIRS[0]
            )
        except Exception:
            pass

        return True
    except Exception:
        return False


def get_clean_forge_env(base_env: Optional[dict] = None) -> dict:
    """
    Формирует изолированную копию переменных окружения для запуска Forge.
    - Удаляет переменные окружения Python NeonRoute: VIRTUAL_ENV, PYTHONPATH, PYTHONHOME.
    - Удаляет прокси-переменные (HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, FTP_PROXY, AG_LS_PROXY
      и их варианты в нижнем регистре), препятствующие внутренним loopback-запросам Gradio/Uvicorn.
    - Задает NO_PROXY / no_proxy ("localhost,127.0.0.1,0.0.0.0") и PYTHONUNBUFFERED="1".
    - Очищает активное виртуальное окружение и директорию интерпретатора NeonRoute
      (включая runtime\\python, sys.executable parent и Scripts) из PATH, предотвращая
      подтягивание DLL и site-packages NeonRoute во встроенный Python Forge.
    - Сохраняет системные переменные Windows (SystemRoot, ComSpec, CUDA, Temp).
    """
    env = dict(base_env if base_env is not None else os.environ)

    # 1. Удаляем переменные изоляции Python
    venv_dir = env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)

    # 2. Очищаем прокси-переменные, мешающие внутренним loopback-запросам Gradio/Uvicorn
    proxy_vars = [
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY", "AG_LS_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "ftp_proxy", "ag_ls_proxy",
    ]
    for p_var in proxy_vars:
        env.pop(p_var, None)

    # Гарантируем прямой доступ к localhost и небуферизованный вывод логов
    env["NO_PROXY"] = "localhost,127.0.0.1,0.0.0.0"
    env["no_proxy"] = "localhost,127.0.0.1,0.0.0.0"
    env["PYTHONUNBUFFERED"] = "1"

    # Защита зафиксированных версий зависимостей Forge через PIP_CONSTRAINT
    try:
        import config
        root_cand = getattr(config, "FORGE_ROOT_DIR", None)
        if root_cand:
            root_p = Path(root_cand).resolve()
            for cand in [
                root_p / "neonroute_constraints.txt",
                root_p / "webui" / "neonroute_constraints.txt",
                root_p / "webui_forge_cu121_torch231" / "webui" / "neonroute_constraints.txt",
            ]:
                if cand.is_file():
                    env["PIP_CONSTRAINT"] = str(cand.resolve())
                    break
    except Exception:
        pass

    # 3. Очищаем PATH от записей активного venv и собственного Python NeonRoute
    raw_path = env.get("PATH", "")
    if raw_path:
        parts = raw_path.split(os.pathsep)
        cleaned_parts = []

        # Собираем префиксы каталогов Python NeonRoute для исключения
        blocked_prefixes = []

        if venv_dir:
            try:
                blocked_prefixes.append(str(Path(venv_dir).resolve()).lower())
            except Exception:
                pass

        # Директория текущего интерпретатора NeonRoute (например, runtime\\python)
        try:
            exe_parent = str(Path(sys.executable).parent.resolve()).lower()
            blocked_prefixes.append(exe_parent)
        except Exception:
            pass

        # sys.prefix / sys.base_prefix
        for prefix in (getattr(sys, "prefix", None), getattr(sys, "base_prefix", None)):
            if prefix:
                try:
                    p_str = str(Path(prefix).resolve()).lower()
                    if p_str not in blocked_prefixes:
                        blocked_prefixes.append(p_str)
                except Exception:
                    pass

        for p in parts:
            if not p.strip():
                continue
            try:
                p_norm = str(Path(p).resolve()).lower()
                # Удаляем путь если он совпадает с заблокированным или находится внутри него
                is_blocked = False
                for b_prefix in blocked_prefixes:
                    if p_norm == b_prefix or p_norm.startswith(b_prefix + os.sep) or p_norm.startswith(b_prefix + "/"):
                        is_blocked = True
                        break
                if is_blocked:
                    continue

                # Страховка по вхождению .venv, runtime/python и bin/scripts
                if ".venv" in p_norm and ("scripts" in p_norm or "bin" in p_norm):
                    continue
                if ("runtime" + os.sep + "python") in p_norm or "runtime/python" in p_norm:
                    continue
            except Exception:
                pass
            cleaned_parts.append(p)

        env["PATH"] = os.pathsep.join(cleaned_parts)

    return env


FORGE_REQUIRED_PACKAGES: Tuple[str, ...] = ("clip", "tqdm", "ftfy", "regex", "torch", "torchvision")


def find_forge_python(
    forge_root: Optional[Path | str] = None,
    launcher_path: Optional[Path | str] = None,
) -> Optional[Path]:
    """
    Динамически находит исполняемый файл Python целевого окружения Forge (без хардкода путей).
    Каскад поиска:
    1. Парсинг строки 'set PYTHON=' из файла лаунчера launcher_path.
    2. Относительные каталоги рядом с launcher_path (system/python/python.exe, python/python.exe, venv/Scripts/python.exe).
    3. Относительные каталоги в forge_root и подкаталогах 1-го уровня (webui_forge_*, system/python/python.exe).
    4. Раскрытие NTFS Junctions и симлинков через Path.resolve().
    """
    # 1. Проверка через явно переданный launcher_path
    resolved_launcher: Optional[Path] = None
    if launcher_path:
        try:
            resolved_launcher = Path(launcher_path).resolve()
        except Exception:
            resolved_launcher = None

    if resolved_launcher and resolved_launcher.is_file():
        launcher_dir = resolved_launcher.parent
        # Попытка парсинга переменной PYTHON из bat-файла лаунчера
        try:
            content = ""
            for enc in ("utf-8", "cp1251", "latin-1"):
                try:
                    content = resolved_launcher.read_text(encoding=enc)
                    break
                except Exception:
                    continue
            for line in content.splitlines():
                s = line.strip()
                if s.lower().startswith("set python="):
                    val = s.split("=", 1)[1].strip().strip('"').strip("'")
                    if val and not val.startswith("%"):
                        # Заменяем базовые bat-переменные
                        val_exp = val.replace("%~dp0", str(launcher_dir) + os.sep)
                        val_exp = val_exp.replace("%ROOT%", str(launcher_dir))
                        val_exp = val_exp.replace("%BASE_DIR%", str(launcher_dir) + os.sep)
                        val_exp = val_exp.replace("%SYSTEM%", str(launcher_dir / "system"))
                        val_exp = val_exp.replace("%FORGE%", str(launcher_dir))
                        try:
                            cand = Path(val_exp).resolve()
                            if cand.is_file():
                                return cand
                        except Exception:
                            pass
        except Exception:
            pass

        # Стандартные относительные папки лаунчера
        for cand in [
            launcher_dir / "system" / "python" / "python.exe",
            launcher_dir / "python" / "python.exe",
            launcher_dir / "venv" / "Scripts" / "python.exe",
        ]:
            try:
                if cand.is_file():
                    return cand.resolve()
            except Exception:
                pass

        # Подпапки 1-го уровня лаунчера (например, webui_forge_cu121_torch231/system/python/python.exe)
        try:
            for child in launcher_dir.iterdir():
                if child.is_dir():
                    for sub in [
                        child / "system" / "python" / "python.exe",
                        child / "python" / "python.exe",
                        child / "venv" / "Scripts" / "python.exe",
                    ]:
                        if sub.is_file():
                            return sub.resolve()
        except Exception:
            pass

    # 2. Проверка через forge_root
    resolved_root: Optional[Path] = None
    if forge_root:
        try:
            resolved_root = Path(forge_root).resolve()
        except Exception:
            resolved_root = None
    elif not launcher_path:
        try:
            import config
            rc = getattr(config, "FORGE_ROOT_DIR", None)
            if rc:
                resolved_root = Path(rc).resolve()
        except Exception:
            pass
        if not resolved_root or not resolved_root.is_dir():
            try:
                import config
                lp_cand = getattr(config, "FORGE_LAUNCHER_PATH", None)
                if lp_cand:
                    lp_resolved = Path(lp_cand).resolve()
                    if lp_resolved.is_file():
                        found_from_lp = find_forge_python(launcher_path=lp_resolved)
                        if found_from_lp:
                            return found_from_lp
            except Exception:
                pass

    if resolved_root and resolved_root.is_dir():
        for cand in [
            resolved_root / "system" / "python" / "python.exe",
            resolved_root / "python" / "python.exe",
            resolved_root / "venv" / "Scripts" / "python.exe",
        ]:
            try:
                if cand.is_file():
                    return cand.resolve()
            except Exception:
                pass

        try:
            for child in resolved_root.iterdir():
                if child.is_dir():
                    for sub in [
                        child / "system" / "python" / "python.exe",
                        child / "python" / "python.exe",
                        child / "venv" / "Scripts" / "python.exe",
                    ]:
                        if sub.is_file():
                            return sub.resolve()
        except Exception:
            pass

    return None


def check_forge_environment_ready(
    python_exe: Optional[Path | str] = None,
    forge_root: Optional[Path | str] = None,
    launcher_path: Optional[Path | str] = None,
    required_packages: Tuple[str, ...] = FORGE_REQUIRED_PACKAGES,
    timeout: float = 3.0,
) -> Tuple[bool, str, List[str]]:
    """
    Проверяет готовность Python-окружения Forge к автономному запуску (--skip-install).
    Проверяет наличие ключевых библиотек (clip, tqdm, ftfy, regex, torch, torchvision) без сетевых запросов.
    Возвращает: (is_ready, reason, missing_packages).
    """
    py_path: Optional[Path] = None
    if python_exe:
        try:
            py_path = Path(python_exe).resolve()
        except Exception:
            py_path = None
    else:
        py_path = find_forge_python(forge_root=forge_root, launcher_path=launcher_path)

    pkgs_list = list(required_packages)

    if not py_path or not py_path.is_file():
        return False, "Интерпретатор Python Forge не найден", pkgs_list

    # 1. Попытка быстрой проверки через подпроцесс Python (find_spec без импорта тяжелых библиотек)
    code = (
        "import importlib.util, sys; "
        f"pkgs = {pkgs_list!r}; "
        "missing = [p for p in pkgs if importlib.util.find_spec(p) is None]; "
        "print(','.join(missing)); "
        "sys.exit(1 if missing else 0)"
    )
    clean_env = get_clean_forge_env()
    try:
        proc = subprocess.run(
            [str(py_path), "-c", code],
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if proc.returncode == 0:
            return True, "Окружение Forge полностью укомплектовано", []
        missing = [p.strip() for p in proc.stdout.strip().split(",") if p.strip()]
        if not missing and proc.stderr:
            missing = pkgs_list
        return False, f"Отсутствуют пакеты в окружении: {missing}", missing
    except subprocess.TimeoutExpired:
        return False, f"Таймаут проверки окружения ({timeout} с)", []
    except Exception as e:
        # 2. Фолбэк на файловую инспекцию site-packages (для синтетических тестов / mock)
        try:
            py_dir = py_path.parent
            site_pkgs_candidates = [
                py_dir / "lib" / "site-packages",
                py_dir / "Lib" / "site-packages",
                py_dir.parent / "lib" / "site-packages",
                py_dir.parent / "Lib" / "site-packages",
            ]
            site_pkgs = next((d for d in site_pkgs_candidates if d.is_dir()), None)
            if site_pkgs:
                missing = []
                for p in pkgs_list:
                    p_norm = p.replace("-", "_")
                    has_dir = (site_pkgs / p).is_dir() or (site_pkgs / p_norm).is_dir()
                    has_dist = any(
                        d.name.lower().startswith((f"{p}-", f"{p_norm}-"))
                        for d in site_pkgs.iterdir()
                        if d.is_dir()
                    )
                    if not (has_dir or has_dist):
                        missing.append(p)
                if not missing:
                    return True, "Окружение Forge укомплектовано (по файловой проверке site-packages)", []
                return False, f"Отсутствуют пакеты в site-packages: {missing}", missing
        except Exception:
            pass

        return False, f"Ошибка проверки интерпретатора: {e}", pkgs_list


