"""
User Profile and Persona Management for NeonRoute
Handles loading and persisting user profiles, active persona resolution,
and cached Russian greetings for character cards.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from config import CHARACTERS_DIR, DATA_DIR
from utils.file_utils import atomic_write_json

logger = logging.getLogger("NeonRoute.UserProfileManager")

USER_PROFILES_FILE = DATA_DIR / "user_profiles.json"

DEFAULT_USER_PROFILES: Dict[str, Any] = {
    "assistant_user": {
        "name": "Пользователь",
        "avatar_path": None,
        "bio": ""
    },
    "rp_settings": {
        "active_persona_id": "persona_default",
        "fallback_mode": "use_assistant",
        "personas": {
            "persona_default": {
                "name": "Игрок",
                "gender": "male",
                "avatar_path": None,
                "bio": "",
                "personality": "",
                "backstory": "",
                "inventory": "",
                "visual_dna": "",
                "outfit": ""
            }
        }
    }
}


class UserProfileManager:
    """Manages user profiles, personas, and greeting translations."""

    _greetings_cache: Dict[str, str] = {}

    @staticmethod
    def load_user_profiles() -> Dict[str, Any]:
        if USER_PROFILES_FILE.exists():
            try:
                with open(USER_PROFILES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if "assistant_user" not in data:
                        data["assistant_user"] = dict(DEFAULT_USER_PROFILES["assistant_user"])
                    if "rp_settings" not in data:
                        data["rp_settings"] = dict(DEFAULT_USER_PROFILES["rp_settings"])
                    return data
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"[UserProfileManager] Ошибка чтения {USER_PROFILES_FILE}: {e}")
            except Exception:
                pass
        try:
            return json.loads(json.dumps(DEFAULT_USER_PROFILES))
        except (json.JSONDecodeError, ValueError):
            return dict(DEFAULT_USER_PROFILES)

    @staticmethod
    def save_user_profiles(profiles: Dict[str, Any]) -> bool:
        """Атомарное сохранение профилей пользователя."""
        if not profiles or not isinstance(profiles, dict):
            return False
        return atomic_write_json(USER_PROFILES_FILE, profiles)

    @staticmethod
    def get_active_user_persona(is_roleplay: bool = False, session_persona_id: Optional[str] = None) -> Dict[str, Any]:
        profiles = UserProfileManager.load_user_profiles()
        from utils.config_manager import ConfigManager
        app_cfg = ConfigManager.load_config()

        # ДОМЕН 1: Рабочий ассистент (Элария)
        if not is_roleplay:
            asst_user = dict(profiles.get("assistant_user", {"name": "Пользователь", "avatar_path": None, "bio": ""}))
            cfg_name = str(app_cfg.get("user_name", "")).strip()
            # Если имя не дефолтное в конфиге или профиле — берем его
            if asst_user.get("name") in ("Пользователь", "User", "", None) and cfg_name:
                asst_user["name"] = cfg_name
            return asst_user

        # ДОМЕН 2: Ролевая игра (Игрок / Персонажи)
        rp = profiles.get("rp_settings") or {}
        personas = rp.get("personas") or {}
        if session_persona_id and session_persona_id in personas:
            return personas[session_persona_id]

        active_id = rp.get("active_persona_id")
        if active_id and active_id in personas:
            return personas[active_id]
        if personas:
            return next(iter(personas.values()))
        return {"name": "Игрок", "avatar_path": None, "bio": ""}

    @staticmethod
    def get_cached_greeting_ru(char_id: Optional[str], char_name: Optional[str] = None) -> Optional[str]:
        if not char_id and not char_name:
            return None

        # 1. Check in-memory session cache strictly
        if char_id and char_id in UserProfileManager._greetings_cache:
            return UserProfileManager._greetings_cache[char_id]
        if char_name and char_name in UserProfileManager._greetings_cache:
            return UserProfileManager._greetings_cache[char_name]

        CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
        c_id_lower = str(char_id or "").lower().strip()
        c_name_lower = str(char_name or "").lower().strip()

        # 2. Strict lookup on disk: only files whose identity matches char_id or char_name
        for p in CHARACTERS_DIR.glob("*.json"):
            if p.suffix == ".tmp":
                continue

            p_stem_lower = p.stem.lower()
            matches_filename = (c_id_lower and c_id_lower in p_stem_lower) or (c_name_lower and c_name_lower == p_stem_lower)

            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)

                target = data.get("data", data) if ("spec" in data or "data" in data) else data
                disk_id = str(target.get("id") or data.get("id") or "").lower().strip()
                disk_name = str(target.get("name") or data.get("name") or "").lower().strip()

                matches_meta = (c_id_lower and c_id_lower == disk_id) or (c_name_lower and c_name_lower == disk_name)

                # STRICT GUARD: Read greeting strictly if filename or internal metadata matches the requested card
                if matches_filename or matches_meta:
                    val = target.get("first_mes_ru") or data.get("first_mes_ru")
                    if val and str(val).strip():
                        res = str(val).strip()
                        if char_id:
                            UserProfileManager._greetings_cache[char_id] = res
                        if char_name:
                            UserProfileManager._greetings_cache[char_name] = res
                        return res
            except Exception:
                continue

        return None

    @staticmethod
    def save_character_greeting_ru(char_id: str, ru_text: str, char_data: Optional[Dict[str, Any]] = None) -> bool:
        if not ru_text or (not char_id and not char_data):
            return False

        effective_id = char_id or (char_data.get("id") if char_data else "character")
        target_path = CHARACTERS_DIR / f"{effective_id}.json"
        CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)

        card_obj = {}
        if target_path.exists():
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    card_obj = json.load(f)
            except Exception:
                card_obj = {}

        card_obj["first_mes_ru"] = ru_text
        if "data" in card_obj and isinstance(card_obj["data"], dict):
            card_obj["data"]["first_mes_ru"] = ru_text

        return atomic_write_json(target_path, card_obj)


__all__ = ["USER_PROFILES_FILE", "DEFAULT_USER_PROFILES", "UserProfileManager"]
