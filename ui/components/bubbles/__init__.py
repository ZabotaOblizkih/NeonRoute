"""
Bubbles package facade: re-exporting avatar rendering, modal preview, and workspace cards.
"""

from .avatar_renderer import (
    get_cached_art_pixmap,
    render_rounded_art_pixmap,
    render_circular_avatar,
    _IMAGE_CACHE_LOCK,
    _IMAGE_PREVIEW_CACHE,
)
from .image_preview_dialog import ImagePreviewDialog
from .workspace_cards import (
    WorkspaceFileCardWidget,
    ConsoleOutputSpoilerWidget,
    GenerationInfoAccordionWidget,
)

__all__ = [
    "get_cached_art_pixmap",
    "render_rounded_art_pixmap",
    "render_circular_avatar",
    "_IMAGE_CACHE_LOCK",
    "_IMAGE_PREVIEW_CACHE",
    "ImagePreviewDialog",
    "WorkspaceFileCardWidget",
    "ConsoleOutputSpoilerWidget",
    "GenerationInfoAccordionWidget",
]
