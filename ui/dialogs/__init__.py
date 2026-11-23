"""Пакет диалоговых окон NeonRoute с поддержкой ленивого импорта (PEP 562)."""
from typing import TYPE_CHECKING, Any
import importlib

# Карта экспорта: ИмяКласса -> (имя_подмодуля, имя_в_модуле)
_LAZY_EXPORTS = {
    "CivitaiCatalogDialog": (".civitai_catalog_dialog", "CivitaiCatalogDialog"),
    "CharactersGalleryDialog": (".characters_gallery_dialog", "CharactersGalleryDialog"),
    "ModelsManagerDialog": ("ui.models_manager_dialog", "ModelsManagerDialog"),
    "CleanCacheDialog": (".clean_cache_dialog", "CleanCacheDialog"),
    "UserSettingsDialog": (".user_settings_dialog", "UserSettingsDialog"),
    "ModelSamplingDialog": (".model_sampling_dialog", "ModelSamplingDialog"),
    "BackgroundSettingsDialog": (".background_dialog", "BackgroundSettingsDialog"),
    "ChatMediaGalleryDialog": (".media_gallery_dialog", "ChatMediaGalleryDialog"),
    "ShutdownProgressDialog": (".shutdown_dialog", "ShutdownProgressDialog"),
    "AboutDialog": (".about_dialog", "AboutDialog"),
    "HelpDialog": (".help_dialog", "HelpDialog"),
    "EngineerSettingsDialog": (".engineer_settings_dialog", "EngineerSettingsDialog"),
    "ModeModelSettingsDialog": (".engineer_settings_dialog", "ModeModelSettingsDialog"),
    "GGUFCatalogDialog": (".gguf_catalog_dialog", "GGUFCatalogDialog"),
    "NewChatDialog": (".new_chat_dialog", "NewChatDialog"),
    "KnowledgeManagerDialog": (".knowledge_manager_dialog", "KnowledgeManagerDialog"),
    "AnchorViewerDialog": (".anchor_viewer_dialog", "AnchorViewerDialog"),
    "PromptViewerDialog": (".prompt_viewer_dialog", "PromptViewerDialog"),
    "AvatarLightboxDialog": (".avatar_lightbox_dialog", "AvatarLightboxDialog"),
    "AvatarCropDialog": (".avatar_crop_dialog", "AvatarCropDialog"),
    "DependencyDialog": (".dependency_dialog", "DependencyDialog"),
    "LLMEngineSettingsDialog": (".llm_engine_settings_dialog", "LLMEngineSettingsDialog"),
    "ForgeSettingsDialog": (".forge_settings_dialog", "ForgeSettingsDialog"),
    "ForgeOnboardingDialog": (".forge_onboarding_dialog", "ForgeOnboardingDialog"),
    "LocalLoraDialog": (".local_lora_dialog", "LocalLoraDialog"),
    "CivitaiLoraDialog": (".civitai_lora_dialog", "CivitaiLoraDialog"),
    "ChubCatalogDialog": (".chub_catalog_dialog", "ChubCatalogDialog"),
    "CardVisualSetupDialog": (".card_visual_setup_dialog", "CardVisualSetupDialog"),
    "CampaignInfoDialog": (".campaign_info_dialog", "CampaignInfoDialog"),
    "SettingsDialog": (".settings_dialog", "SettingsDialog"),
    "MobileServerDialog": (".mobile_server_dialog", "MobileServerDialog"),
}

if TYPE_CHECKING:
    from .civitai_catalog_dialog import CivitaiCatalogDialog
    from .characters_gallery_dialog import CharactersGalleryDialog
    from ui.models_manager_dialog import ModelsManagerDialog
    from .clean_cache_dialog import CleanCacheDialog
    from .user_settings_dialog import UserSettingsDialog
    from .model_sampling_dialog import ModelSamplingDialog
    from .background_dialog import BackgroundSettingsDialog
    from .media_gallery_dialog import ChatMediaGalleryDialog
    from .shutdown_dialog import ShutdownProgressDialog
    from .about_dialog import AboutDialog
    from .help_dialog import HelpDialog
    from .engineer_settings_dialog import EngineerSettingsDialog, ModeModelSettingsDialog
    from .gguf_catalog_dialog import GGUFCatalogDialog
    from .new_chat_dialog import NewChatDialog
    from .knowledge_manager_dialog import KnowledgeManagerDialog
    from .anchor_viewer_dialog import AnchorViewerDialog
    from .prompt_viewer_dialog import PromptViewerDialog
    from .avatar_lightbox_dialog import AvatarLightboxDialog
    from .avatar_crop_dialog import AvatarCropDialog
    from .dependency_dialog import DependencyDialog
    from .llm_engine_settings_dialog import LLMEngineSettingsDialog
    from .forge_settings_dialog import ForgeSettingsDialog
    from .local_lora_dialog import LocalLoraDialog
    from .civitai_lora_dialog import CivitaiLoraDialog
    from .chub_catalog_dialog import ChubCatalogDialog
    from .card_visual_setup_dialog import CardVisualSetupDialog
    from .campaign_info_dialog import CampaignInfoDialog
    from .settings_dialog import SettingsDialog


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        mod_name, attr_name = _LAZY_EXPORTS[name]
        module = importlib.import_module(mod_name, package=__package__)
        cls = getattr(module, attr_name)
        # Кэшируем класс в глобальном пространстве имен пакета
        globals()[name] = cls
        return cls
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = list(_LAZY_EXPORTS.keys())
