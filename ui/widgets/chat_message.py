"""
Chat Message Widget Interface for NeonRoute
Re-exports ChatMessageWidget and components from ui.components.chat_bubble.
"""

from ui.components.chat_bubble import (
    ChatMessageWidget,
    WorkspaceFileCardWidget,
    ConsoleOutputSpoilerWidget
)
from ui.widgets.engineer_task_card import EngineerTaskCardWidget

__all__ = [
    "ChatMessageWidget",
    "WorkspaceFileCardWidget",
    "ConsoleOutputSpoilerWidget",
    "EngineerTaskCardWidget"
]
