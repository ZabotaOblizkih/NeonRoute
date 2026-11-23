import logging
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

import config
from core.forge_locator import (
    find_forge_root,
    parse_forge_launcher_args,
    resolve_forge_models_directory,
    FORGE_LAUNCHER_CANDIDATES,
)

logger = logging.getLogger("NeonRoute.AssetValidator")

# Базовые директории по умолчанию (для обратной совместимости с тестами и внешними модулями)
FORGE_ROOT_DIR: Path = getattr(config, "FORGE_ROOT_DIR", Path("."))
FORGE_BASE_DIR: Path = FORGE_ROOT_DIR / "webui"
MODELS_SD_DIR: Path = getattr(config, "FORGE_SD_MODELS_DIR", FORGE_BASE_DIR / "models" / "Stable-diffusion")
MODELS_ADETAILER_DIR: Path = FORGE_BASE_DIR / "models" / "adetailer"
MODELS_ESRGAN_DIR: Path = FORGE_BASE_DIR / "models" / "ESRGAN"

REQUIRED_CHECKPOINTS = [
    ("Realistic Vision v6.0", ["realisticvision", "v60"]),
    ("CyberRealistic Classic", ["cyberrealistic", "classic"]),
    ("MeinaHentai v5", ["meinahentai", "meina"]),
    ("DreamShaper 8", ["dreamshaper"])
]

REQUIRED_ADETAILER_MODELS = [
    "face_yolov8n.pt",
    "hand_yolov8n.pt"
]


class AssetValidator:
    """Проводит предстартовую проверку целостности локальных нейросетевых ассетов Forge."""

    @classmethod
    def resolve_forge_root(cls) -> Optional[Path]:
        """
        Каскадное динамическое разрешение корня установки Forge:
        1. Пропатченный или явно установленный модуль-уровень FORGE_ROOT_DIR.
        2. config.FORGE_ROOT_DIR.
        3. find_forge_root() из core.forge_locator.
        4. Родительский каталог лаунчера (если launcher найден).
        Всегда нормализует путь через .resolve() для раскрытия симлинков/Junctions.
        """
        mod_root = globals().get("FORGE_ROOT_DIR")
        cfg_root = getattr(config, "FORGE_ROOT_DIR", None)

        # 1. Если FORGE_ROOT_DIR был явно переопределен в модуле (например, в тестах)
        if mod_root is not None and mod_root != cfg_root:
            p = Path(mod_root).resolve()
            return p

        # 2. Если задан mod_root и он физически существует
        if mod_root is not None:
            try:
                p = Path(mod_root).resolve()
                if p.is_dir():
                    return p
            except Exception:
                pass

        # 3. Проверяем config.FORGE_ROOT_DIR
        if cfg_root is not None:
            try:
                p = Path(cfg_root).resolve()
                if p.is_dir():
                    return p
            except Exception:
                pass

        # 4. Каскадный поиск через find_forge_root
        try:
            found = find_forge_root()
            if found and Path(found).resolve().is_dir():
                return Path(found).resolve()
        except Exception:
            pass

        # 5. Проверяем родительский каталог лаунчера
        cfg_launcher = getattr(config, "FORGE_LAUNCHER_PATH", None)
        if cfg_launcher:
            try:
                lp = Path(cfg_launcher).resolve()
                if lp.is_file():
                    parent = lp.parent
                    if (parent / "webui.py").is_file():
                        return parent.parent.resolve() if (parent.parent / "models").is_dir() else parent
                    return parent
            except Exception:
                pass

        if mod_root is not None:
            return Path(mod_root).resolve()

        return None

    @classmethod
    def resolve_forge_launcher(cls, forge_root: Optional[Path] = None) -> Optional[Path]:
        """Разрешает путь к исполняемому лаунчеру Forge."""
        root = forge_root or cls.resolve_forge_root()
        if root and root.is_dir():
            for cand in FORGE_LAUNCHER_CANDIDATES:
                try:
                    p = (root / cand).resolve()
                    if p.is_file():
                        return p
                except Exception:
                    pass
            for cand in FORGE_LAUNCHER_CANDIDATES:
                try:
                    p = (root / "webui" / cand).resolve()
                    if p.is_file():
                        return p
                except Exception:
                    pass

        cfg_launcher = getattr(config, "FORGE_LAUNCHER_PATH", None)
        if cfg_launcher:
            try:
                p = Path(cfg_launcher).resolve()
                if p.is_file():
                    return p
            except Exception:
                pass

        return None

    @classmethod
    def resolve_webui_dir(cls, forge_root: Optional[Path] = None) -> Optional[Path]:
        """
        Каскадный поиск каталога webui:
        1. Пропатченный FORGE_BASE_DIR (если существует).
        2. <forge_root>/webui (стандартная чистая структура).
        3. <forge_root>/webui_forge_cu121_torch231/webui (сборка cu121).
        4. <forge_root> (если webui.py или modules/ находятся прямо в корне).
        5. Шаблон <forge_root>/webui*/webui.
        """
        patched_base = globals().get("FORGE_BASE_DIR")
        if patched_base is not None:
            try:
                pb = Path(patched_base).resolve()
                if pb.is_dir():
                    return pb
            except Exception:
                pass

        root = forge_root or cls.resolve_forge_root()
        if not root or not root.is_dir():
            return None

        candidates = [
            root / "webui",
            root / "webui_forge_cu121_torch231" / "webui",
            root,
        ]
        for c in candidates:
            try:
                cp = c.resolve()
                if cp.is_dir() and ((cp / "webui.py").is_file() or (cp / "modules").is_dir()):
                    return cp
            except Exception:
                continue

        # Поиск по глобу webui*/webui
        try:
            for sub in root.glob("webui*/webui"):
                if sub.is_dir():
                    return sub.resolve()
        except Exception:
            pass

        # Если папка webui существует без маркеров
        if (root / "webui").is_dir():
            return (root / "webui").resolve()

        return root.resolve()

    @classmethod
    def resolve_sd_models_dir(
        cls,
        forge_root: Optional[Path] = None,
        webui_dir: Optional[Path] = None,
        launcher_path: Optional[Path] = None
    ) -> Path:
        """
        Разрешает директорию чекпоинтов Stable Diffusion:
        1. Пропатченный MODELS_SD_DIR (если задан и существует).
        2. Параметры лаунчера (--ckpt-dir, --models-dir).
        3. <webui_dir>/models/Stable-diffusion.
        4. <forge_root>/models/Stable-diffusion.
        5. <forge_root>/webui_forge_cu121_torch231/webui/models/Stable-diffusion.
        6. config.FORGE_SD_MODELS_DIR.
        7. Фоллбек: (webui_dir или forge_root)/models/Stable-diffusion.
        Всегда возвращает нормализованный Path (.resolve()).
        """
        patched_sd = globals().get("MODELS_SD_DIR")
        if patched_sd is not None:
            try:
                psd = Path(patched_sd).resolve()
                if psd.is_dir():
                    return psd
            except Exception:
                pass

        launcher = launcher_path or cls.resolve_forge_launcher(forge_root)
        custom_args = parse_forge_launcher_args(launcher) if launcher else {}

        if "ckpt_dir" in custom_args:
            try:
                cd = custom_args["ckpt_dir"].resolve()
                if cd.is_dir():
                    return cd
            except Exception:
                pass

        if "models_dir" in custom_args:
            try:
                md_sd = (custom_args["models_dir"] / "Stable-diffusion").resolve()
                if md_sd.is_dir():
                    return md_sd
            except Exception:
                pass

        root = forge_root or cls.resolve_forge_root()
        w_dir = webui_dir or cls.resolve_webui_dir(root)

        candidates = []
        if w_dir:
            candidates.append(w_dir / "models" / "Stable-diffusion")
        if root:
            candidates.append(root / "models" / "Stable-diffusion")
            candidates.append(root / "webui_forge_cu121_torch231" / "webui" / "models" / "Stable-diffusion")

        for c in candidates:
            try:
                cp = c.resolve()
                if cp.is_dir():
                    return cp
            except Exception:
                continue

        cfg_sd = getattr(config, "FORGE_SD_MODELS_DIR", None)
        if cfg_sd:
            try:
                csd = Path(cfg_sd).resolve()
                if csd.is_dir():
                    return csd
            except Exception:
                pass

        if "ckpt_dir" in custom_args:
            return custom_args["ckpt_dir"].resolve()
        if "models_dir" in custom_args:
            return (custom_args["models_dir"] / "Stable-diffusion").resolve()

        if candidates:
            return candidates[0].resolve()

        fallback = (w_dir or root or Path(".")).resolve() / "models" / "Stable-diffusion"
        return fallback.resolve()

    @classmethod
    def resolve_adetailer_dirs(
        cls,
        forge_root: Optional[Path] = None,
        webui_dir: Optional[Path] = None,
        launcher_path: Optional[Path] = None
    ) -> List[Path]:
        """
        Возвращает список возможных каталогов размещения моделей ADetailer (в порядке приоритета).
        Нормализует пути через .resolve() для раскрытия Junctions.
        """
        candidates: List[Path] = []
        seen = set()

        def _add(path_candidate: Optional[Path]):
            if not path_candidate:
                return
            try:
                res = Path(path_candidate).resolve()
                if str(res) not in seen:
                    seen.add(str(res))
                    candidates.append(res)
            except Exception:
                pass

        # 1. Пропатченный MODELS_ADETAILER_DIR
        _add(globals().get("MODELS_ADETAILER_DIR"))

        # 2. Аргументы лаунчера
        launcher = launcher_path or cls.resolve_forge_launcher(forge_root)
        custom_args = parse_forge_launcher_args(launcher) if launcher else {}
        if "models_dir" in custom_args:
            _add(custom_args["models_dir"] / "adetailer")

        root = forge_root or cls.resolve_forge_root()
        w_dir = webui_dir or cls.resolve_webui_dir(root)

        # 3. webui / models / adetailer
        if w_dir:
            _add(w_dir / "models" / "adetailer")
            _add(w_dir / "extensions" / "sd-webui-adetailer" / "models")

        # 4. forge_root / models / adetailer
        if root:
            _add(root / "models" / "adetailer")
            _add(root / "webui_forge_cu121_torch231" / "webui" / "models" / "adetailer")
            _add(root / "webui_forge_cu121_torch231" / "models" / "adetailer")

        return candidates

    @classmethod
    def resolve_esrgan_dir(
        cls,
        forge_root: Optional[Path] = None,
        webui_dir: Optional[Path] = None,
        launcher_path: Optional[Path] = None
    ) -> Path:
        """Разрешает директорию апскейлеров ESRGAN."""
        patched_esr = globals().get("MODELS_ESRGAN_DIR")
        if patched_esr is not None:
            try:
                pesr = Path(patched_esr).resolve()
                if pesr.is_dir():
                    return pesr
            except Exception:
                pass

        launcher = launcher_path or cls.resolve_forge_launcher(forge_root)
        custom_args = parse_forge_launcher_args(launcher) if launcher else {}
        if "models_dir" in custom_args:
            try:
                md_esr = (custom_args["models_dir"] / "ESRGAN").resolve()
                if md_esr.is_dir():
                    return md_esr
            except Exception:
                pass

        root = forge_root or cls.resolve_forge_root()
        w_dir = webui_dir or cls.resolve_webui_dir(root)

        candidates = []
        if w_dir:
            candidates.append(w_dir / "models" / "ESRGAN")
        if root:
            candidates.append(root / "models" / "ESRGAN")
            candidates.append(root / "webui_forge_cu121_torch231" / "webui" / "models" / "ESRGAN")

        for c in candidates:
            try:
                cp = c.resolve()
                if cp.is_dir():
                    return cp
            except Exception:
                continue

        if "models_dir" in custom_args:
            return (custom_args["models_dir"] / "ESRGAN").resolve()
        if candidates:
            return candidates[0].resolve()

        return (w_dir or root or Path(".")).resolve() / "models" / "ESRGAN"

    @classmethod
    def resolve_paths(cls) -> Dict[str, Any]:
        """
        Каскадное динамическое разрешение всех ключевых путей Forge.
        Возвращает словарь с нормализованными (.resolve()) путями.
        """
        root = cls.resolve_forge_root()
        launcher = cls.resolve_forge_launcher(root)
        w_dir = cls.resolve_webui_dir(root)
        sd_dir = cls.resolve_sd_models_dir(root, w_dir, launcher)
        ad_dirs = cls.resolve_adetailer_dirs(root, w_dir, launcher)
        esr_dir = cls.resolve_esrgan_dir(root, w_dir, launcher)

        return {
            "forge_root": root,
            "forge_launcher": launcher,
            "webui_dir": w_dir,
            "sd_models_dir": sd_dir,
            "adetailer_dirs": ad_dirs,
            "esrgan_dir": esr_dir,
        }

    @classmethod
    def get_adetailer_model_path(cls, model_name: str) -> Optional[Path]:
        """
        Проверяет физическое наличие файла модели ADetailer.
        Каскадно опрашивает все разрешенные директории ADetailer с раскрытием Junctions/симлинков.
        """
        for ad_dir in cls.resolve_adetailer_dirs():
            try:
                candidate = (ad_dir / model_name).resolve()
                if candidate.is_file():
                    return candidate
            except Exception:
                continue
        return None

    @classmethod
    def find_adetailer_model(cls, model_name: str) -> Optional[Path]:
        """Публичный интерфейс поиска модели ADetailer (алиас к get_adetailer_model_path)."""
        return cls.get_adetailer_model_path(model_name)

    @classmethod
    def is_adetailer_available(cls, model_name: str) -> bool:
        """Проверяет доступность модели ADetailer."""
        return cls.get_adetailer_model_path(model_name) is not None

    @classmethod
    def validate_all_assets(cls) -> Dict[str, Any]:
        """
        Проводит полный предстартовый аудит ассетов.
        Возвращает детальный отчет по чекпоинтам, ADetailer и апскейлерам.
        """
        paths = cls.resolve_paths()
        forge_root = paths.get("forge_root")
        webui_dir = paths.get("webui_dir")
        sd_models_dir = paths.get("sd_models_dir")

        # Forge считается установленным, если доступна либо директория webui, либо корень Forge
        forge_installed = bool(
            (webui_dir and webui_dir.is_dir()) or
            (forge_root and forge_root.is_dir())
        )

        report: Dict[str, Any] = {
            "forge_installed": forge_installed,
            "resolved_paths": paths,
            "checkpoints_count": 0,
            "checkpoints_files": [],
            "checkpoints_found": {},
            "checkpoints_missing": [],
            "adetailer_found": {},
            "adetailer_missing": [],
            "all_critical_present": True
        }

        # 1. Динамическая проверка чекпоинтов SD
        valid_checkpoint_files = []
        if sd_models_dir and sd_models_dir.exists():
            try:
                for f in sd_models_dir.iterdir():
                    try:
                        if f.is_file() and f.suffix.lower() in (".safetensors", ".ckpt"):
                            try:
                                if f.stat().st_size > 0:
                                    valid_checkpoint_files.append(f)
                            except (OSError, PermissionError):
                                pass
                    except (OSError, PermissionError):
                        pass
            except (FileNotFoundError, PermissionError, OSError) as e:
                logger.debug(f"[AssetValidator] Ошибка чтения директории чекпоинтов {sd_models_dir}: {e}")

        report["checkpoints_count"] = len(valid_checkpoint_files)
        report["checkpoints_files"] = [f.name for f in valid_checkpoint_files]

        # Обратная совместимость для checkpoints_found / checkpoints_missing
        existing_sd_filenames = [f.name.lower() for f in valid_checkpoint_files]
        for title, match_keys in REQUIRED_CHECKPOINTS:
            found = any(all(k in fn for k in match_keys) for fn in existing_sd_filenames)
            report["checkpoints_found"][title] = found

        if not forge_installed or report["checkpoints_count"] == 0:
            report["checkpoints_missing"].append("No valid SD checkpoints found (.safetensors / .ckpt)")
            report["all_critical_present"] = False

        # 2. Проверка моделей ADetailer
        for ad_model in REQUIRED_ADETAILER_MODELS:
            found = cls.is_adetailer_available(ad_model)
            report["adetailer_found"][ad_model] = found
            if not found:
                report["adetailer_missing"].append(ad_model)

        # Если отсутствуют модели ADetailer или Forge не установлен, all_critical_present = False
        if not forge_installed or report["adetailer_missing"]:
            report["all_critical_present"] = False

        return report

    @classmethod
    def log_validation_summary(cls):
        """Выводит чистый аудит в лог NeonRoute."""
        report = cls.validate_all_assets()
        paths = report.get("resolved_paths", {})
        if not report["forge_installed"]:
            display_path = paths.get("webui_dir") or paths.get("forge_root") or FORGE_BASE_DIR
            logger.warning(f"[AssetValidator] Директория Forge не обнаружена: {display_path}")
            return

        ckpt_count = report.get("checkpoints_count", 0)
        ad_total = len(REQUIRED_ADETAILER_MODELS)
        ad_ok = ad_total - len(report["adetailer_missing"])

        if ckpt_count >= 1:
            logger.info(f"[AssetValidator] Pre-flight аудит ассетов: найдено {ckpt_count} чекпоинтов SD, ADetailer [{ad_ok}/{ad_total}]")
        else:
            logger.warning(
                f"[AssetValidator] Pre-flight аудит ассетов: не найдено ни одного чекпоинта SD (0), ADetailer [{ad_ok}/{ad_total}]"
            )
            logger.warning("[AssetValidator] В директории моделей SD отсутствуют валидные чекпоинты (.safetensors / .ckpt)")

        if report["adetailer_missing"]:
            try:
                from utils.config_manager import ConfigManager
                ad_no_hf = bool(ConfigManager.load_config().get("forge_ad_no_huggingface", False))
            except Exception:
                ad_no_hf = False

            if ad_no_hf:
                logger.warning(
                    f"[AssetValidator] Внимание: отсутствуют веса ADetailer ({', '.join(report['adetailer_missing'])}). "
                    f"Флаг --ad-no-huggingface запрещает автоскачивание. При их вызове будет применена мягкая деградация."
                )
            else:
                logger.info(
                    f"[AssetValidator] Внимание: отсутствуют веса ADetailer ({', '.join(report['adetailer_missing'])}). "
                    f"Флаг --ad-no-huggingface выключен (снят): Forge попытается автоматически загрузить их с Hugging Face при рендере."
                )
