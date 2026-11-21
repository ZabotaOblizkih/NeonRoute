from pathlib import Path
from typing import Optional, Any, Dict
import configparser
import logging
import os
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

    # 2. Очищаем PATH от записей активного venv и собственного Python NeonRoute
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


