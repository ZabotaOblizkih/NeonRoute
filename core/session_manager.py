"""
Core Session Manager Interface for NeonRoute
Re-exports SessionManager from services.session_manager for architectural compliance.
"""

from services.session_manager import SessionManager, MINI_ANTIGRAVITY_SYSTEM_PROMPT, get_engineer_system_prompt

__all__ = ["SessionManager", "MINI_ANTIGRAVITY_SYSTEM_PROMPT", "get_engineer_system_prompt"]
