"""
Backwards-compatibility re-export of LoraManager from services.art_director.lora_manager.
"""

from services.art_director.lora_manager import (
    LoraManager,
    extract_lora_metadata,
    _ensure_lora_registry_path
)

__all__ = ["LoraManager", "extract_lora_metadata"]
