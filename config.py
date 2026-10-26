"""
NeonRoute - Central Configuration & Environment Settings
Optimized for 8 GB VRAM (RTX 5050 / MSI Cyborg 15)
"""

import os
from pathlib import Path

# Base Paths (Dynamically resolved relative to project root)
BASE_DIR = Path(__file__).resolve().parent
EXPORTS_DIR = BASE_DIR / "exports"
OUTPUTS_DIR = BASE_DIR / "outputs"
AUDIO_DIR = BASE_DIR / "audio"
LOGS_DIR = BASE_DIR / "logs"
CHARACTERS_DIR = BASE_DIR / "characters"
HISTORY_DIR = BASE_DIR / "history"
BACKGROUNDS_DIR = BASE_DIR / "backgrounds"
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
WORKSPACE_DIR = BASE_DIR / "workspace"
ASSETS_DIR = BASE_DIR / "assets"
KNOWLEDGE_BASE_DIR = BASE_DIR / "knowledge_base"
KNOWLEDGE_REGISTRY_PATH = KNOWLEDGE_BASE_DIR / "registry.json"
MEMORY_DB_PATH = DATA_DIR / "memory.db"
CONFIG_PATH = BASE_DIR / "config.json"
SERVER_CONFIG_INI_PATH = BASE_DIR / "server_config.ini"
LOCAL_BIN_DIR = BASE_DIR / "bin"
LOCAL_MODELS_DIR = BASE_DIR / "models"
LLAMA_SERVER_BAT_PATH = BASE_DIR / "run_llama_server.bat"
ART_DIRECTOR_MODELS_PATH = DATA_DIR / "art_director_models.json"
LORA_REGISTRY_PATH = DATA_DIR / "lora_registry.json"
# ENTITIES_JSON_PATH = DATA_DIR / "entities.json"  # Deprecated and removed

# Ensure all managed directories exist
for folder in [BASE_DIR, EXPORTS_DIR, OUTPUTS_DIR, AUDIO_DIR, LOGS_DIR, CHARACTERS_DIR, HISTORY_DIR, BACKGROUNDS_DIR, DATA_DIR, CONFIG_DIR, WORKSPACE_DIR, ASSETS_DIR, LOCAL_BIN_DIR, LOCAL_MODELS_DIR, KNOWLEDGE_BASE_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# Network Endpoints for Local AI Backends
DEFAULT_LM_STUDIO_URL = "http://127.0.0.1:1234/v1"
DEFAULT_FORGE_URL = "http://127.0.0.1:7860"
DEFAULT_TTS_URL = "http://127.0.0.1:5010"

# External Backend Process Launch Paths
from enum import Enum

class MemoryStrategy(str, Enum):
    BALANCE = "balance"            # Гибридный оффлоад (50/50, чат и Forge делят VRAM)
    ART_PRIORITY = "art_priority"  # Максимум VRAM для графики, LLM выгружается

DEFAULT_MEMORY_STRATEGY = MemoryStrategy.BALANCE

# Art Generation Gateway Defaults
DEFAULT_AUTO_GENERATE_ART = False  # False = подтверждение по кнопке-чипу, True = авторендер

# External SD WebUI Forge Paths
FORGE_ROOT_DIR = Path(os.environ.get("FORGE_ROOT_DIR", r"C:\neyroGPD"))
FORGE_LORA_DIRS = [
    FORGE_ROOT_DIR / "webui_forge_cu121_torch231" / "webui" / "models" / "Lora",
    FORGE_ROOT_DIR / "webui" / "models" / "Lora",
    FORGE_ROOT_DIR / "models" / "Lora"
]
DEFAULT_FORGE_LORA_DIR = next((p for p in FORGE_LORA_DIRS if p.exists()), FORGE_LORA_DIRS[0])

_forge_env = os.environ.get("NEONROUTE_FORGE_LAUNCHER")
if _forge_env:
    FORGE_LAUNCHER_PATH = Path(_forge_env)
else:
    _local_launcher = BASE_DIR.parent / "neyroGPD" / "forge_launcher.bat"
    FORGE_LAUNCHER_PATH = _local_launcher if _local_launcher.exists() else (FORGE_ROOT_DIR / "forge_launcher.bat")
LM_STUDIO_CLI_CMD = "lms"

# Hardware optimization flags for Forge (RTX 5050 8GB VRAM)
FORGE_EXTRA_ARGS = []
FORGE_ENABLE_VAE_TILING = True

# Strict 8 GB VRAM Safety Controls
# Maximum heavy tasks executed at once: strictly 1
# Sliding dialog context window for Assistant (6 user-assistant pairs to minimize prefill latency)
LLM_CONTEXT_WINDOW_LIMIT = 12

MAX_CONCURRENT_INFERENCE = 1
VRAM_TOTAL_BUDGET_MB = 8192  # 8 GB VRAM
VRAM_SAFE_LIMIT_MB = 7000    # Alert threshold for high VRAM pressure
VRAM_MONITOR_INTERVAL_MS = 2000  # 2.0 seconds polling

# Двухконтурная модель управления контекстом (Token Budget + Emergency VRAM Guard)
EMERGENCY_VRAM_THRESHOLD_MB = 600  # Пожарный рубильник: критический порог свободной VRAM (< 600 МБ)
EMERGENCY_VRAM_MB = 600            # Алиас для обратной совместимости

# Пороги относительного коэффициента заполнения контекста (Контур 1)
CONTEXT_RATIO_TIER_1 = 0.60        # ratio < 0.60 -> Ступень 1 (60 сообщ / 1024 токена)
CONTEXT_RATIO_TIER_2 = 0.80        # 0.60 <= ratio < 0.80 -> Ступень 2 (42 сообщ / 768 токенов)
                                   # ratio >= 0.80 -> Ступень 3 (24 сообщ / 512 токенов)

# Параметры ступеней (Ступень 1 / 2 / 3)
CONTEXT_TIERS = {
    1: {"history_limit": 60, "max_tokens": 1024, "ratio_max": 0.60},
    2: {"history_limit": 42, "max_tokens": 768, "ratio_max": 0.80},
    3: {"history_limit": 24, "max_tokens": 512, "ratio_max": 1.00},
}

# Service Health Check Intervals
BACKEND_HEALTH_CHECK_INTERVAL_MS = 5000  # 5 seconds polling for backend statuses

# Default Generation Parameters (optimized for 8GB VRAM & WebUI reference quality)
DEFAULT_SD_SETTINGS = {
    "steps": 20,
    "width": 768,
    "height": 768,
    "cfg_scale": 6.8,
    "sampler_name": "DPM++ 2M",
    "scheduler": "Karras",
    "enable_hr": True,
    "hr_scale": 1.30,
    "hr_upscaler": "4x-UltraSharp",  # Эталонный апскейлер фотореализма
    "denoising_strength": 0.35,     # Эталонная резкость для ESRGAN
    "hr_second_pass_steps": 12,
    "negative_prompt": "bad anatomy, poorly drawn, deformed, extra limbs, missing limbs, bad hands, blurry"
}

# Application UI Strings & Metadata
APP_NAME = "NeonRoute"
APP_VERSION = "0.9.0-beta"
APP_TAGLINE = "Local AI Orchestrator • 8GB VRAM Optimized"

# Базовый промпт ролевого режима (по умолчанию, если не задана карточка)
ROLEPLAY_DEFAULT_PROMPT = "[Язык: выразительный художественный русский без цензуры и калек с английского. Строго держи характер персонажа: его темперамент, манеру речи и внутренние границы. Детально передавай телесные реакции, физический контакт, взгляды и дыхание. Развивай сцену органично, без спешки. Формат: действия *в звёздочках*.]"

# Аппаратный мастер-предохранитель (RTX 5050 Laptop 8GB VRAM)
HARDWARE_MAX_CONTEXT_LENGTH = 28672     # Абсолютный потолок контекста (28K)
MASTER_MAX_RESPONSE_TOKENS = 4096       # Абсолютный потолок длины ответа (4K)

# Централизованные профили режимов (Single Source of Truth)
MODE_PROFILES = {
    "assistant": {
        "context_length": 14336,        # 14K контекст
        "history_tokens": 11000,
        "history_limit": 60,
        "sampling": {
            "temperature": 0.70,
            "min_p": 0.05,
            "repeat_penalty": 1.05,
            "max_tokens": 1024,
        }
    },
    "roleplay": {
        "context_length": 24576,        # 24K глубокий контекст RP
        "history_tokens": 18000,        # ~25-30 ходов связной памяти
        "history_limit": 65,
        "sampling": {
            "temperature": 1.10,
            "min_p": 0.05,
            "top_p": 0.95,
            "repeat_penalty": 1.02,
            "max_tokens": 4096,         # Полные 4K на литературный пост
            "is_dm_mode": False
        }
    },
    "engineer": {
        "context_length": 28672,        # 28K под листинги кода и файлы
        "history_tokens": 22000,
        "history_limit": 50,
        "sampling": {
            "temperature": 0.15,
            "min_p": 0.00,
            "repeat_penalty": 1.00,
            "max_tokens": 4096,         # До 4K под генерацию скриптов
        }
    }
}

