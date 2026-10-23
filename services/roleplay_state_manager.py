"""
Deprecated: services.roleplay_state_manager is deprecated and unified into
services.rp_cast_manager.RPCastManager.
This module is a compatibility stub redirecting to RPCastManager.
"""

import re
from typing import Dict, Any, List, Optional
from services.rp_cast_manager import RPCastManager


class RoleplayStateManager:
    cleanup_session = staticmethod(RPCastManager.cleanup_session)
    load_cast = staticmethod(RPCastManager.load_cast)
    save_cast = staticmethod(RPCastManager.save_cast)

    @staticmethod
    def process_model_turn(session_id: Optional[str], response_text: str) -> str:
        """
        Strips legacy director/scene tags so they never appear in chat bubbles.
        Ensures: No hidden [СЦЕНА: ...] or - СЦЕНА: ... tags ever appear.
        """
        if not response_text:
            return response_text
        cleaned = re.sub(
            r'\[(?:СЦЕНА|SCENE|СТАТУС|STATUS|ШРАМ|SCAR|КВЕСТ|QUEST|ВЕХА|MILESTONE)[:\s]+[^\]\n]+\]',
            '',
            response_text,
            flags=re.IGNORECASE
        )
        cleaned = re.sub(
            r'(?:^|\n)\s*[-*•]?\s*(?:СЦЕНА|SCENE)[:\s]+[^\n]+',
            '',
            cleaned,
            flags=re.IGNORECASE
        )
        return cleaned.strip()
