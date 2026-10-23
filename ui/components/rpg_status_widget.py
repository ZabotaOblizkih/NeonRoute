"""
Backward compatibility proxy for RPG elements.
Re-exports all widgets and parsers from ui.components.rpg_elements.
"""

from ui.components.rpg_elements import (
    RPGBundle,
    RPGContentParser,
    RPGStatusParser,
    RPGStatusWidget,
    RPGThoughtWidget,
    RPGChoicesWidget,
    extract_rpg_status
)

__all__ = [
    "RPGBundle",
    "RPGContentParser",
    "RPGStatusParser",
    "RPGStatusWidget",
    "RPGThoughtWidget",
    "RPGChoicesWidget",
    "extract_rpg_status",
]
