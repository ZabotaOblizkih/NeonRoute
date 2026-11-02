"""
Dialogs package for NeonRoute
"""

from ui.dialogs.clean_cache_dialog import CleanCacheDialog
from ui.dialogs.background_dialog import BackgroundSettingsDialog
from ui.dialogs.about_dialog import AboutDialog
from ui.dialogs.help_dialog import HelpDialog
from ui.dialogs.civitai_catalog_dialog import CivitaiCatalogDialog
from ui.models_manager_dialog import ModelsManagerDialog
from ui.dialogs.engineer_settings_dialog import EngineerSettingsDialog
from ui.dialogs.new_chat_dialog import NewChatDialog
from ui.dialogs.knowledge_manager_dialog import KnowledgeManagerDialog

__all__ = [
    "CleanCacheDialog",
    "BackgroundSettingsDialog",
    "AboutDialog",
    "HelpDialog",
    "CivitaiCatalogDialog",
    "ModelsManagerDialog",
    "EngineerSettingsDialog",
    "NewChatDialog",
    "KnowledgeManagerDialog",
]
