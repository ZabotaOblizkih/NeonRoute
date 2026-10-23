import logging
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

from config import FORGE_ROOT_DIR

logger = logging.getLogger("NeonRoute.AssetValidator")

# Базовые директории Forge
FORGE_BASE_DIR = FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui"
MODELS_SD_DIR = FORGE_BASE_DIR / "models" / "Stable-diffusion"
MODELS_ADETAILER_DIR = FORGE_BASE_DIR / "models" / "adetailer"
MODELS_ESRGAN_DIR = FORGE_BASE_DIR / "models" / "ESRGAN"

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
    """Проводит быструю проверку целостности локальных нейросетевых ассетов."""

    @staticmethod
    def get_adetailer_model_path(model_name: str) -> Optional[Path]:
        """Проверяет физическое наличие файла модели ADetailer."""
        target = MODELS_ADETAILER_DIR / model_name
        if target.exists() and target.is_file():
            return target
        # Проверка альтернативной директории webui/models/adetailer/ (на случай кастомных путей)
        alt_target = FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "models" / "adetailer" / model_name
        if alt_target.exists() and alt_target.is_file():
            return alt_target
        return None

    @staticmethod
    def is_adetailer_available(model_name: str) -> bool:
        return AssetValidator.get_adetailer_model_path(model_name) is not None

    @classmethod
    def validate_all_assets(cls) -> Dict[str, Any]:
        """
        Проводит полный предстартовый аудит ассетов.
        Возвращает детальный отчет по чекпоинтам, ADetailer и апскейлерам.
        """
        report = {
            "forge_installed": FORGE_BASE_DIR.exists(),
            "checkpoints_found": {},
            "checkpoints_missing": [],
            "adetailer_found": {},
            "adetailer_missing": [],
            "all_critical_present": True
        }

        # 1. Проверка директории чекпоинтов
        existing_sd_files = []
        if MODELS_SD_DIR.exists():
            existing_sd_files = [f.name.lower() for f in MODELS_SD_DIR.iterdir() if f.is_file() and f.suffix.lower() in (".safetensors", ".ckpt")]

        for title, match_keys in REQUIRED_CHECKPOINTS:
            found = any(all(k in fn for k in match_keys) for fn in existing_sd_files)
            report["checkpoints_found"][title] = found
            if not found:
                report["checkpoints_missing"].append(title)
                report["all_critical_present"] = False

        # 2. Проверка моделей ADetailer
        for ad_model in REQUIRED_ADETAILER_MODELS:
            found = cls.is_adetailer_available(ad_model)
            report["adetailer_found"][ad_model] = found
            if not found:
                report["adetailer_missing"].append(ad_model)

        return report

    @classmethod
    def log_validation_summary(cls):
        """Выводит чистый аудит в лог NeonRoute."""
        report = cls.validate_all_assets()
        if not report["forge_installed"]:
            logger.warning(f"[AssetValidator] Директория Forge не обнаружена: {FORGE_BASE_DIR}")
            return

        ckpt_total = len(REQUIRED_CHECKPOINTS)
        ckpt_ok = ckpt_total - len(report["checkpoints_missing"])
        ad_total = len(REQUIRED_ADETAILER_MODELS)
        ad_ok = ad_total - len(report["adetailer_missing"])

        logger.info(f"[AssetValidator] Pre-flight аудит ассетов: Чекпоинты SD [{ckpt_ok}/{ckpt_total}], ADetailer [{ad_ok}/{ad_total}]")

        if report["checkpoints_missing"]:
            logger.warning(f"[AssetValidator] Отсутствуют чекпоинты: {', '.join(report['checkpoints_missing'])}")
        if report["adetailer_missing"]:
            logger.warning(
                f"[AssetValidator] Внимание: отсутствуют веса ADetailer ({', '.join(report['adetailer_missing'])}). "
                f"Флаг --ad-no-huggingface запрещает автоскачивание. При их вызове будет применена мягкая деградация."
            )
