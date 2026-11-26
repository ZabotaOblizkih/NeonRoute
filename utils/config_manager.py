"""
Configuration Manager for NeonRoute
Single Source of Truth for user preferences, LLM context/sampling presets,
and visualizer settings persisted to C:\\NeonRoute\\config.json
"""

import json
import logging
import threading
from pathlib import Path
from typing import Dict, Any, Optional

import config

logger = logging.getLogger("NeonRoute.ConfigManager")

_INITIAL_CONFIG_PATH = getattr(config, "CONFIG_PATH", getattr(config, "BASE_DIR", Path(".")) / "config.json")
CONFIG_PATH = _INITIAL_CONFIG_PATH


def get_config_path() -> Path:
    """
    Динамически разрешает актуальный путь к config.json.
    Учитывает:
    1. Переопределение на уровне класса ConfigManager.CONFIG_PATH
    2. Переопределение на уровне модуля utils.config_manager.CONFIG_PATH
    3. Динамический config.CONFIG_PATH или config.BASE_DIR / 'config.json'
    """
    cm_path = getattr(ConfigManager, "CONFIG_PATH", None)
    if cm_path is not None:
        return Path(cm_path)

    mod_path = globals().get("CONFIG_PATH")
    if mod_path is not None and mod_path != _INITIAL_CONFIG_PATH:
        return Path(mod_path)

    try:
        import config as _cfg
        if hasattr(_cfg, "CONFIG_PATH") and _cfg.CONFIG_PATH:
            return Path(_cfg.CONFIG_PATH)
        if hasattr(_cfg, "BASE_DIR") and _cfg.BASE_DIR:
            return Path(_cfg.BASE_DIR) / "config.json"
    except Exception:
        pass

    return Path(mod_path) if mod_path is not None else Path("config.json")


DEFAULT_SETTINGS: Dict[str, Any] = {
    # 1. UI & Interface Settings
    "theme": "dark",
    "chat_font_size": 13,
    "auto_translate": False,
    "check_dependencies_on_startup": True,
    "active_model": "",
    "engineer_model": "",
    "engineer_model_name": "",
    "rp_model_name": "",
    "assistant_model_name": "",
    "compute_device_mode": "Auto",
    "background_path": "",
    "background_dim_opacity": 140,
    "assistant_custom_prompt_template": "",

    # 2. LM Studio Context & Architecture (Честные контексты по режимам)
    "context_length": 24576,
    "context_length_roleplay": 24576,
    "context_length_assistant": 14336,
    "context_length_engineer": 28672,

    # 3. Default Sampling Presets (Ограничены мастер-предохранителем 4K)
    "default_sampling_roleplay": {
        "temperature": 1.10,
        "min_p": 0.05,
        "top_p": 0.95,
        "repeat_penalty": 1.02,
        "dry_multiplier": 0.80,
        "xtc_probability": 0.30,
        "xtc_threshold": 0.10,
        "max_tokens": 4096,
        "is_dm_mode": False
    },
    "default_sampling_assistant": {
        "temperature": 0.70,
        "min_p": 0.05,
        "top_p": 0.95,
        "repeat_penalty": 1.05,
        "dry_multiplier": 0.80,
        "xtc_probability": 0.20,
        "xtc_threshold": 0.10,
        "max_tokens": 1024
    },
    "default_sampling_engineer": {
        "temperature": 0.70,
        "min_p": 0.00,
        "top_p": 1.00,
        "repeat_penalty": 1.00,
        "dry_multiplier": 0.00,
        "xtc_probability": 0.00,
        "xtc_threshold": 0.10,
        "max_tokens": 4096
    },

    # 4. SD WebUI Forge / Art Director Defaults
    "auto_generate_art": False,
    "forge_sdxl_fp8_enabled": True,
    "forge_ad_no_huggingface": True,
    "forge_onboarding_shown": False,
    "forge_patch_ignored_paths": [],
    "visualizer_settings": {
        "auto_generate_art": False,
        "steps": 28,
        "cfg_scale": 6.0,
        "sampler_name": "DPM++ 2M",
        "denoising_strength": 0.35,
        "default_style": "realistic"
    },

    # 5. Translation Engine Settings
    "translation_settings": {
        "temperature": 0.20,
        "max_tokens_limit": 4096
    },

    # 6. LLM Engine & Hardware Autotune Settings
    "hardware_autotune_done": False,
    "kv_cache_type": "q8_0",             # Допустимые: "q8_0", "q4_0", "f16"
    "flash_attention_enabled": True,
    "thinking_mode": "auto"              # Допустимые: "auto", "on", "off"
}


DEFAULT_CONFIG = DEFAULT_SETTINGS


def _deep_merge_dicts(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merges incoming dictionary into base defaults without dropping keys."""
    result = dict(base)
    for k, v in incoming.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge_dicts(result[k], v)
        else:
            result[k] = v
    return result


class ConfigManager:
    CONFIG_PATH: Optional[Path] = None
    _lock = threading.RLock()

    @classmethod
    def get_config_path(cls) -> Path:
        """Возвращает динамически разрешённый путь к config.json."""
        if cls.CONFIG_PATH is not None:
            return Path(cls.CONFIG_PATH)
        return get_config_path()

    @classmethod
    def load_config(cls) -> Dict[str, Any]:
        """Loads configuration from config.json with recursive fallback to default settings."""
        with cls._lock:
            config = dict(DEFAULT_SETTINGS)
            target_path = cls.get_config_path()
            if target_path.exists():
                try:
                    with open(target_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        config = _deep_merge_dicts(config, data)
                except Exception as e:
                    logger.warning(f"Could not load {target_path.name}: {e}")
            return config

    @classmethod
    def save_config(cls, config_data: Dict[str, Any]):
        """Saves configuration dict to config.json safely."""
        with cls._lock:
            from utils.file_utils import atomic_write_json
            target_path = cls.get_config_path()
            atomic_write_json(target_path, config_data)

    @classmethod
    def update_setting(cls, key: str, value: Any) -> Dict[str, Any]:
        """Обновляет единичный параметр в config.json и сохраняет файл."""
        with cls._lock:
            cfg = cls.load_config()
            cfg[key] = value
            cls.save_config(cfg)
            return cfg
