"""
Session Cast, Lore, Appearance & Vision Context Manager for NeonRoute (Roleplay Mode).
Stores NPCs, world state, inventory, objectives, persistent appearance mutations,
relationship dynamics, and visual scene prompts in history/cast_<session_id>.json.
CPU-scanned (0 ms) with async fact & visual extraction via TaskQueue (PRIORITY_LOW).
"""

import os
import re
import json
import logging
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from config import HISTORY_DIR
from services.task_queue import get_task_queue, InferenceTask, PRIORITY_LOW

logger = logging.getLogger("NeonRoute.RPCastManager")

from services.rp_scene_updater import (
    COMMON_NOUN_NAMES,
    BASE_RESERVED_NPC_NAMES,
    FORBIDDEN_NPC_NAMES,
    get_forbidden_npc_names,
    _get_name_inflections,
    apply_scene_updates_to_cast,
)


from services.rp_constants import SCAN_KEYWORDS


class RPCastManager:
    _file_lock = threading.RLock()

    @staticmethod
    def get_cast_file_path(session_id: str) -> Path:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        return HISTORY_DIR / f"cast_{session_id}.json"

    @staticmethod
    def bootstrap_party_from_card(session_id: str, card: Any) -> Optional[Dict[str, Any]]:
        """
        Мгновенно инициализирует каст сессии из карточки персонажа за 0 мс:
        - Регистрирует сопартийцев и их visual_dna.
        - Выполняет pre-scan стартового приветствия (first_mes) на наличие статов, локации, инвентаря и квестов.
        """
        if not session_id or not card:
            return None

        card_name = getattr(card, "name", "Спутница")
        cast_data = RPCastManager.load_cast(session_id, companion_name=card_name)
        modified = False

        env = cast_data.setdefault("environment", {})
        card_scenery = getattr(card, "scenery_dna", "").strip()
        if card_scenery and not env.get("location_dna"):
            env["location_dna"] = card_scenery
            modified = True

        # Pre-scan стартового приветствия карточки (first_mes / first_mes_ru)
        first_mes = getattr(card, "first_mes_ru", "") or getattr(card, "first_mes", "")
        if first_mes and isinstance(first_mes, str):
            from services.roleplay_state_manager import RoleplayStateManager
            parsed_status = RoleplayStateManager.parse_explicit_status(first_mes)
            if parsed_status.get("location"):
                cast_data["current_location"] = parsed_status["location"]
                modified = True
            elif card_scenery and not cast_data.get("current_location"):
                cast_data["current_location"] = card_scenery.split(',')[0].strip()
                modified = True

            if parsed_status.get("inventory"):
                cast_data["inventory"] = parsed_status["inventory"]
                modified = True
            if parsed_status.get("quest"):
                cast_data["active_quest"] = parsed_status["quest"]
                modified = True
            if parsed_status.get("stats"):
                cast_data.setdefault("stats", {}).update(parsed_status["stats"])
                modified = True

        # Инициализация первичного состояния мира из сценария (если пусто)
        scenario = getattr(card, "scenario", "") or getattr(card, "description", "")
        if scenario and not cast_data.get("world_state"):
            first_line = scenario.strip().splitlines()[0][:140]
            if first_line:
                cast_data.setdefault("world_state", []).append(first_line)
                modified = True

        companions = getattr(card, "companions", [])
        if hasattr(card, "companions") and isinstance(companions, list) and len(companions) > 1:
            entities = cast_data.setdefault("entities", {})
            first_comp_name = None

            for idx, comp in enumerate(companions):
                c_name = comp.get("name", "").strip() or f"Спутник {idx+1}"
                if idx == 0:
                    first_comp_name = c_name
                comp_key = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', c_name.lower().strip())
                v_dna = comp.get("visual_dna", "").strip()
                role_desc = comp.get("role", "Спутник отряда").strip()
                aliases = [a.lower().strip() for a in comp.get("aliases", []) if isinstance(a, str) and a.strip()]
                if c_name.lower().strip() not in aliases:
                    aliases.append(c_name.lower().strip())

                entities[comp_key] = {
                    "role": "companion",
                    "canonical_name": c_name,
                    "aliases": aliases,
                    "facts": [role_desc] if role_desc else ["Спутник отряда"],
                    "visual_dna": v_dna,
                    "is_minor": comp.get("is_minor", False),
                    "presence": "physical",
                    "vital_status": "alive",
                    "ttl": 999,
                    "relational_cache": {
                        "dynamic_tone": "",
                        "foundation": "",
                        "current_bond": ""
                    },
                    "companion_state": {
                        "vital_status": "alive",
                        "visual_dna": v_dna,
                        "mutations": [],
                        "current_outfit": ""
                    },
                    "updated_at": datetime.now().isoformat()
                }

            card_key = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', card_name.lower().strip())
            actual_keys = {
                re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', (c.get("name") or "").lower().strip())
                for c in companions
            }
            if card_key in entities and card_key not in actual_keys:
                entities.pop(card_key, None)

            if first_comp_name:
                cast_data["focus_companion"] = first_comp_name
                if "companion_state" in cast_data:
                    first_dna = companions[0].get("visual_dna", "").strip()
                    if first_dna:
                        cast_data["companion_state"]["visual_dna"] = first_dna

            modified = True

        card_is_minor = bool(getattr(card, "is_minor", False))
        if card_is_minor:
            entities = cast_data.setdefault("entities", {})
            for ent in entities.values():
                if isinstance(ent, dict) and ent.get("role") == "companion":
                    if not ent.get("is_minor"):
                        ent["is_minor"] = True
                        modified = True

        if modified:
            RPCastManager.save_cast(session_id, cast_data)
            return cast_data

        return cast_data

    @staticmethod
    def get_party_companions(cast_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Возвращает словарь всех записей из cast_data['entities'], у которых role == 'companion'."""
        entities = cast_data.get("entities", {}) if isinstance(cast_data, dict) else {}
        return {
            eid: ent for eid, ent in entities.items()
            if isinstance(ent, dict) and ent.get("role") == "companion"
        }

    @staticmethod
    def get_active_companions(cast_data: Optional[Dict[str, Any]] = None, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Возвращает список активных сопартийцев (сущностей с role == 'companion')."""
        if cast_data is None and session_id:
            cast_data = RPCastManager.load_cast(session_id)
        if not cast_data or not isinstance(cast_data, dict):
            return []
        party = RPCastManager.get_party_companions(cast_data)
        return list(party.values())

    @staticmethod
    def format_cast_data_block(cast_data: Optional[Dict[str, Any]]) -> str:
        """
        Форматирует структуру cast_data в компактное текстовое описание
        активных спутников, окружения, статуса и инвентаря для системного промпта.
        """
        if not cast_data or not isinstance(cast_data, dict):
            return ""

        sections = []

        # 1. Локация и обстановка
        loc = (cast_data.get("current_location") or "").strip()
        env = cast_data.get("environment") or {}
        ambience = (env.get("scene_ambience") or "").strip()
        loc_parts = []
        if loc:
            is_enc = bool(env.get("is_enclosed", False))
            enc_str = " (в укрытии/помещении)" if is_enc else " (открытый воздух)"
            loc_parts.append(f"{loc}{enc_str}")
        if ambience:
            loc_parts.append(f"Атмосфера: {ambience}")
        if loc_parts:
            sections.append(f"Локация: {', '.join(loc_parts)}")

        # 2. Текущая сюжетная цель
        quest = (cast_data.get("active_quest") or "").strip()
        if quest:
            sections.append(f"Текущая цель: {quest}")

        # 3. Активные спутники и присутствующие NPC
        entities = cast_data.get("entities", {})
        comp_lines = []
        npc_lines = []

        for eid, ent in entities.items():
            if not isinstance(ent, dict):
                continue
            name = (ent.get("canonical_name") or eid).strip()
            if not name:
                continue
            role = ent.get("role", "npc")
            vital = ent.get("vital_status", "alive")
            presence = ent.get("presence", "physical")
            ttl = ent.get("ttl", 0)

            # Пропускаем мертвых NPC и тех, чье присутствие не физическое или ttl истек (для сторонних NPC)
            if role != "companion" and (vital == "dead" or presence != "physical" or ttl <= 0):
                continue

            details = []
            if vital == "critical":
                details.append("Тяжело ранен / При смерти")
            elif vital == "dead":
                details.append("Погиб (мёртв)")

            # Состояние спутницы (наряд, ранения)
            c_state = ent.get("companion_state") or {}
            outfit = (c_state.get("current_outfit") or "").strip()
            muts = c_state.get("mutations") or []
            if outfit:
                details.append(f"Наряд: {outfit}")
            if muts:
                details.append(f"Следы/раны: {', '.join(str(m).strip() for m in muts if str(m).strip())}")

            raw_facts = ent.get("facts", [])
            if isinstance(raw_facts, str):
                raw_facts = [raw_facts]
            facts = [str(f).strip() for f in raw_facts if str(f).strip() and str(f).strip() != "Основной спутник отряда"]
            if facts:
                details.append("; ".join(facts))

            line = f"• {name}"
            if details:
                line += f" ({', '.join(details)})"

            if role == "companion":
                comp_lines.append(line)
            else:
                npc_lines.append(line)

        if comp_lines:
            sections.append("Спутники в отряде:\n" + "\n".join(comp_lines))
        if npc_lines:
            sections.append("Присутствующие персонажи (NPC):\n" + "\n".join(npc_lines))

        # 4. Состояние игрока
        p_state = cast_data.get("player_state") or {}
        p_vital = p_state.get("vital_status", "alive")
        p_muts = p_state.get("mutations", [])
        p_details = []
        if p_vital == "critical":
            p_details.append("Тяжело ранен / При смерти")
        elif p_vital == "dead":
            p_details.append("Погиб (мёртв)")
        if p_muts:
            p_details.append(f"Следы/раны: {', '.join(str(m).strip() for m in p_muts if str(m).strip())}")
        if p_details:
            sections.append(f"Состояние игрока: {', '.join(p_details)}")

        # 5. Инвентарь
        inv = cast_data.get("inventory", [])
        if inv and isinstance(inv, list):
            clean_inv = [str(item).strip() for item in inv if str(item).strip()]
            if clean_inv:
                sections.append(f"Инвентарь: {', '.join(clean_inv)}")

        # 6. Характеристики / шкалы (если есть)
        stats = cast_data.get("stats", {})
        if stats and isinstance(stats, dict):
            stat_strs = [f"{k}: {v}" for k, v in stats.items() if str(v).strip()]
            if stat_strs:
                sections.append(f"Параметры/Шкалы: {', '.join(stat_strs)}")

        return "\n".join(sections).strip()

    @classmethod
    def get_cast_prompt_block(cls, session_id: Optional[str] = None, cast_data: Optional[Dict[str, Any]] = None) -> str:
        """
        Загружает актуальный cast_data для сессии (или использует переданный) и
        возвращает форматированный блок спутников, окружения, статуса и инвентаря
        для системного промпта ролевой игры.
        """
        if cast_data is None and session_id:
            cast_data = cls.load_cast(session_id)
        if not cast_data or not isinstance(cast_data, dict):
            return ""
        return cls.format_cast_data_block(cast_data)

    @staticmethod
    def resolve_focus_from_turn(cast_data: Dict[str, Any], text: str) -> Optional[str]:
        """Определяет фокусного спутника из реплики хода по точным границам слов."""
        party = RPCastManager.get_party_companions(cast_data)
        if len(party) <= 1 or not text or not text.strip():
            return None

        text_lower = text.lower()
        matched_candidates = set()

        for eid, ent in party.items():
            canonical = ent.get("canonical_name", eid)
            aliases = set(ent.get("aliases", []))
            if canonical:
                aliases.add(canonical)
            if eid:
                aliases.add(eid)

            matched_this_companion = False
            for alias in aliases:
                clean_alias = (alias or "").strip()
                if not clean_alias or len(clean_alias) < 3:
                    continue
                low_alias = clean_alias.lower()
                is_common_noun = low_alias in COMMON_NOUN_NAMES
                forms = _get_name_inflections(clean_alias)
                for form in forms:
                    if len(form) < 3:
                        continue
                    if is_common_noun:
                        if re.search(rf"\b{re.escape(form.capitalize())}\b", text):
                            matched_candidates.add(canonical)
                            matched_this_companion = True
                            break
                    else:
                        if re.search(rf"\b{re.escape(form)}\b", text_lower):
                            matched_candidates.add(canonical)
                            matched_this_companion = True
                            break
                if matched_this_companion:
                    break

        if len(matched_candidates) == 1:
            return next(iter(matched_candidates))
        return None

    @staticmethod
    def switch_focus(cast_data: Dict[str, Any], target_companion_name: str) -> bool:
        """Детерминированно переключает активного фокусного спутника с сохранением и восстановлением срезов."""
        cur_focus = (cast_data.get("focus_companion") or "").strip()
        target_clean = (target_companion_name or "").strip()
        if not target_clean or cur_focus.lower() == target_clean.lower():
            return False

        entities = cast_data.setdefault("entities", {})
        cur_ent = None
        target_ent = None

        for eid, ent in entities.items():
            if not isinstance(ent, dict):
                continue
            c_name = ent.get("canonical_name", eid).lower().strip()
            aliases = [a.lower().strip() for a in ent.get("aliases", []) if isinstance(a, str)]

            if cur_focus and (c_name == cur_focus.lower() or cur_focus.lower() in aliases or eid.lower().strip() == cur_focus.lower()):
                cur_ent = ent

            if c_name == target_clean.lower() or target_clean.lower() in aliases or eid.lower().strip() == target_clean.lower():
                target_ent = ent

        if cur_ent is not None:
            cur_ent["relational_cache"] = dict(cast_data.get("relationship_context", {}))
            cur_ent["companion_state"] = dict(cast_data.get("companion_state", {}))

        if target_ent is None:
            return False

        new_canonical_name = target_ent.get("canonical_name", target_clean)
        cast_data["focus_companion"] = new_canonical_name

        if "relational_cache" in target_ent and isinstance(target_ent["relational_cache"], dict):
            cast_data["relationship_context"] = dict(target_ent["relational_cache"])
        else:
            cast_data["relationship_context"] = {"dynamic_tone": "", "foundation": "", "current_bond": ""}

        if "companion_state" in target_ent and isinstance(target_ent["companion_state"], dict):
            cast_data["companion_state"] = dict(target_ent["companion_state"])
        else:
            cast_data["companion_state"] = {
                "vital_status": target_ent.get("vital_status", "alive"),
                "visual_dna": target_ent.get("visual_dna", ""),
                "mutations": [],
                "current_outfit": ""
            }

        cast_data["relationship_state"] = cast_data["relationship_context"].get("dynamic_tone", "")
        return True

    @staticmethod
    def load_cast(session_id: Optional[str], user_name: str = "Игрок", companion_name: str = "Спутница") -> Dict[str, Any]:
        cur_focus_default = companion_name or "Спутница"
        comp_key_default = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', cur_focus_default.lower().strip())
        default_data = {
            "session_id": session_id or "",
            "focus_companion": cur_focus_default,
            "current_location": "",
            "active_quest": "",
            "relationship_context": {
                "dynamic_tone": "",
                "foundation": "",
                "current_bond": ""
            },
            "relationship_state": "",
            "intimacy_milestones": [],
            "inventory": [],
            "stats": {},
            "world_state": [],
            "entities": {
                comp_key_default: {
                    "role": "companion",
                    "canonical_name": cur_focus_default,
                    "aliases": [cur_focus_default.lower().strip()],
                    "facts": ["Основной спутник отряда"],
                    "visual_dna": "",
                    "is_minor": False,
                    "presence": "physical",
                    "vital_status": "alive",
                    "ttl": 999,
                    "relational_cache": {
                        "dynamic_tone": "",
                        "foundation": "",
                        "current_bond": ""
                    },
                    "companion_state": {
                        "vital_status": "alive",
                        "visual_dna": "",
                        "mutations": [],
                        "current_outfit": ""
                    },
                    "updated_at": datetime.now().isoformat()
                }
            },
            "player_state": {
                "vital_status": "alive",
                "visual_dna": "",
                "mutations": [],
                "current_outfit": ""
            },
            "companion_state": {
                "vital_status": "alive",
                "visual_dna": "",
                "mutations": [],
                "current_outfit": ""
            },
            "environment": {
                "world_baseline": "",
                "scene_ambience": "",
                "location_dna": "",
                "is_enclosed": False
            },
            "last_visual_prompt": None,
            "pending_event_nudge": None,
            "turns_since_last_event": 0
        }
        if not session_id:
            return default_data

        fpath = RPCastManager.get_cast_file_path(session_id)
        with RPCastManager._file_lock:
            if fpath.exists():
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            # Мягкая миграция существующих сессий
                            if "relationship_context" not in data or not isinstance(data.get("relationship_context"), dict):
                                old_ms = data.get("intimacy_milestones", [])
                                old_state = data.get("relationship_state", "")
                                data["relationship_context"] = {
                                    "dynamic_tone": old_state,
                                    "foundation": old_ms[0] if old_ms else "",
                                    "current_bond": old_ms[-1] if len(old_ms) > 1 else (old_ms[0] if old_ms else "")
                                }

                            # Инициализация блока окружения для старых сессий
                            env = data.setdefault("environment", {})
                            env.setdefault("world_baseline", "")
                            env.setdefault("scene_ambience", "")
                            env.setdefault("location_dna", "")
                            env.setdefault("is_enclosed", False)

                            for k, v in default_data.items():
                                if k != "entities":
                                    data.setdefault(k, v)

                            rel_c = data.setdefault("relationship_context", {})
                            for rk, rv in default_data["relationship_context"].items():
                                rel_c.setdefault(rk, rv)

                            p_st = data.setdefault("player_state", {})
                            for pk, pv in default_data["player_state"].items():
                                p_st.setdefault(pk, pv)

                            c_st = data.setdefault("companion_state", {})
                            for ck, cv in default_data["companion_state"].items():
                                c_st.setdefault(ck, cv)

                            entities = data.setdefault("entities", {})
                            for eid, edata in entities.items():
                                if isinstance(edata, dict):
                                    edata.setdefault("vital_status", "alive")
                                    edata.setdefault("role", "npc")

                            # Гарантия наличия активного фокусного спутника в entities
                            cur_focus = (data.get("focus_companion") or companion_name or "Спутница").strip()
                            data["focus_companion"] = cur_focus

                            has_companion = False
                            for eid, edata in entities.items():
                                if isinstance(edata, dict) and edata.get("role") == "companion":
                                    c_cname = edata.get("canonical_name", eid).lower().strip()
                                    aliases = [a.lower().strip() for a in edata.get("aliases", []) if isinstance(a, str)]
                                    if c_cname == cur_focus.lower() or cur_focus.lower() in aliases or eid.lower().strip() == cur_focus.lower():
                                        has_companion = True
                                        break

                            if not has_companion:
                                comp_key = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', cur_focus.lower().strip())
                                comp_st_raw = data.get("companion_state") or {}
                                entities[comp_key] = {
                                    "role": "companion",
                                    "canonical_name": cur_focus,
                                    "aliases": [cur_focus.lower().strip()],
                                    "facts": ["Основной спутник отряда"],
                                    "visual_dna": comp_st_raw.get("visual_dna", ""),
                                    "is_minor": False,
                                    "presence": "physical",
                                    "vital_status": comp_st_raw.get("vital_status", "alive"),
                                    "ttl": 999,
                                    "relational_cache": dict(data.get("relationship_context") or {}),
                                    "companion_state": dict(comp_st_raw),
                                    "updated_at": datetime.now().isoformat()
                                }

                            # Защита сопартийцев от вытеснения и очистки
                            companion_party = [
                                edata.get("canonical_name", "").strip()
                                for edata in entities.values()
                                if isinstance(edata, dict) and edata.get("role") == "companion" and edata.get("canonical_name")
                            ]
                            forbidden = get_forbidden_npc_names(
                                user_name=user_name,
                                companion_name=companion_name,
                                companion_names=companion_party
                            )

                            purged = False
                            for eid in list(entities.keys()):
                                edata = entities[eid]
                                if isinstance(edata, dict) and edata.get("role") == "companion":
                                    continue
                                c_name = edata.get("canonical_name", "").lower().strip() if isinstance(edata, dict) else ""
                                if eid.lower().strip() in forbidden or c_name in forbidden:
                                    del entities[eid]
                                    purged = True
                            if purged:
                                RPCastManager.save_cast(session_id, data)

                            return data
                except Exception as e:
                    logger.error(f"[RPCast] Ошибка чтения {fpath.name}: {e}")
        return default_data

    @staticmethod
    def cleanup_session(session_id: str):
        """Atomically erases all RP session data."""
        sid = str(session_id).strip()
        if not sid:
            return
        for prefix in ("cast_", "inventory_", "location_", "story_"):
            for ext in (".json", ".tmp"):
                p = HISTORY_DIR / f"{prefix}{sid}{ext}"
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass

    @staticmethod
    def save_cast(session_id: Optional[str], data: Dict[str, Any]) -> bool:
        if not session_id or not data:
            return False

        fpath = RPCastManager.get_cast_file_path(session_id)
        tmp_path = fpath.with_suffix(".tmp")

        with RPCastManager._file_lock:
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"[RPCast] Ошибка записи tmp {tmp_path.name}: {e}")
                return False

            bak_path = fpath.with_suffix(".bak")
            for attempt in range(5):
                try:
                    if fpath.exists():
                        fpath.replace(bak_path)
                    tmp_path.replace(fpath)
                    if bak_path.exists():
                        bak_path.unlink()
                    return True
                except PermissionError:
                    time.sleep(0.05 * (2 ** attempt))
                except Exception as e:
                    logger.error(f"[RPCast] Ошибка атомарной замены: {e}")
                    if bak_path.exists() and not fpath.exists():
                        bak_path.replace(fpath)
                    time.sleep(0.05)

            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False

    @staticmethod
    def scan_and_tick(
        session_id: Optional[str],
        user_text: str,
        default_ttl: int = 5,
        user_name: str = "Игрок",
        companion_name: str = "Спутница",
        is_environment: bool = False
    ) -> Dict[str, str]:
        res = {"active_cast": "", "recalled_lore": "", "meta_context": ""}
        if not session_id:
            return res

        cast_data = RPCastManager.load_cast(session_id, user_name=user_name, companion_name=companion_name)
        entities = cast_data.get("entities", {})
        text_lower = (user_text or "").lower()
        active_physical = []
        recalled_memories = []
        # Pre-pass: детерминированное переключение фокуса спутника (0 мс)
        if not is_environment and user_text:
            cand = RPCastManager.resolve_focus_from_turn(cast_data, user_text)
            if cand and cand.lower().strip() != (cast_data.get("focus_companion") or "").lower().strip():
                RPCastManager.switch_focus(cast_data, cand)

        for ent_id, ent in entities.items():
            if not isinstance(ent, dict):
                continue
            if ent.get("role") == "companion":
                ent["ttl"] = 999
                ent["presence"] = "physical"
                continue

            aliases = ent.get("aliases", [])
            c_name = ent.get("canonical_name", ent_id).lower().strip()
            is_mentioned = any(alias in text_lower for alias in aliases) or (bool(c_name) and c_name in text_lower)
            if is_mentioned:
                ent["ttl"] = default_ttl
            elif ent.get("ttl", 0) > 0:
                ent["ttl"] -= 1

            if ent.get("ttl", 0) > 0:
                name = ent.get("canonical_name", ent_id)
                facts = "; ".join(ent.get("facts", []))
                presence = ent.get("presence", "physical")

                vital = ent.get("vital_status", "alive")
                if vital == "dead":
                    continue  # Dead NPCs do not participate in active scene
                stat_note = " [Тяжело ранен]" if vital == "critical" else ""
                if presence == "physical":
                    active_physical.append(f"• {name}{stat_note}: {facts}")
                else:
                    recalled_memories.append(f"• {name} (в воспоминаниях): {facts}")

        if active_physical:
            res["active_cast"] = (
                "\n<ActiveCast>\n"
                "Действующие лица в текущей сцене (находятся рядом):\n"
                + "\n".join(active_physical) +
                "\nДИРЕКТИВА: Учитывай их физическое присутствие и статус.\n"
                "</ActiveCast>\n"
            )

        # Семантический поиск воспоминаний в LanceDB (CPU ONNX, 0 VRAM)
        # Порог ужесточен до 0.35 для отсева семантического шума
        recalled_events = []
        try:
            from services.memory_db import memory_db
            db_hits = memory_db.search_rp_memories(session_id, user_text, top_k=3, max_distance=0.35)
            if db_hits:
                recalled_events = [f"• [Событие]: {h}" for h in db_hits]
        except Exception as _e_mem:
            logger.debug(f"[RPCast] Recall query skipped: {_e_mem}")

        lore_sections = []
        if recalled_memories:
            lore_sections.append(
                "Упомянутые лица из прошлого (ФИЗИЧЕСКИ ОТСУТСТВУЮТ В СЦЕНЕ):\n"
                + "\n".join(recalled_memories)
            )
        if recalled_events:
            lore_sections.append(
                "Всплывшие воспоминания и пережитые события из прошлого сюжета:\n"
                + "\n".join(recalled_events)
            )

        if lore_sections:
            res["recalled_lore"] = (
                "\n<RecalledLore>\n"
                + "\n\n".join(lore_sections) +
                "\nДИРЕКТИВА: Эти факты и персонажи НЕ находятся прямо сейчас в комнате, но всплыли в памяти к слову. Органично учитывай этот общий пережитый опыт.\n"
                "</RecalledLore>\n"
            )

        meta_parts = []

        # Dynamic Lorebook Injection: Constant vs Selective (Triggered)
        active_lb_file = None
        try:
            from services.session_manager import SessionManager
            sess_info = SessionManager.load_session(session_id)
            if sess_info:
                active_lb_file = sess_info.get("active_lorebook")
        except Exception:
            pass

        if active_lb_file:
            from config import CHARACTERS_DIR
            lb_path = (CHARACTERS_DIR.parent / "lorebooks") / active_lb_file
            if lb_path.exists():
                try:
                    with open(lb_path, "r", encoding="utf-8") as f_lb:
                        lb_json = json.load(f_lb)
                    raw_entries = lb_json.get("entries", {})
                    items = list(raw_entries.values()) if isinstance(raw_entries, dict) else (raw_entries if isinstance(raw_entries, list) else [])

                    constant_rules = []
                    selective_rules = []

                    for e in items:
                        if not isinstance(e, dict) or not e.get("enabled", True):
                            continue
                        cnt = str(e.get("content") or "").strip()
                        # Защита от деструктивных инъекций debug/override
                        if not cnt or any(w in cnt for w in ("BEGIN OVERRIDE SEQUENCE", "STOP ALL CURRENT PROCESSES")):
                            continue

                        is_constant = bool(e.get("constant", False) or not (e.get("keys") or e.get("key")))
                        if is_constant:
                            constant_rules.append(cnt)
                        else:
                            raw_keys = e.get("keys") or e.get("key") or []
                            if isinstance(raw_keys, str):
                                e_keys = [k.strip().lower() for k in raw_keys.split(",") if k.strip()]
                            elif isinstance(raw_keys, list):
                                e_keys = [str(k).strip().lower() for k in raw_keys if str(k).strip()]
                            else:
                                e_keys = []
                            if any(k in text_lower for k in e_keys):
                                selective_rules.append(cnt)

                    if constant_rules:
                        meta_parts.append("Законы и константы мира:\n" + "\n".join(f"• {c[:800]}" for c in constant_rules[:5]))
                    if selective_rules:
                        meta_parts.append("Активные правила сцены:\n" + "\n".join(f"• {s[:800]}" for s in selective_rules[:4]))
                except Exception as _e_lb_scan:
                    logger.debug(f"[RPCast] Lorebook scan error: {_e_lb_scan}")

        env = cast_data.get("environment") or {}
        is_enc = bool(env.get("is_enclosed", False))
        loc_name = cast_data.get("current_location", "")
        if loc_name:
            enc_tag = " [В укрытии/подземелье]" if is_enc else " [Открытый воздух]"
            meta_parts.append(f"Локация: {loc_name}{enc_tag}")

        ambience = env.get("scene_ambience", "").strip()
        world_base = env.get("world_baseline", "").strip()
        if ambience:
            meta_parts.append(f"Освещение и атмосфера: {ambience}")
        elif world_base and not is_enc:
            meta_parts.append(f"Купол мира: {world_base}")
        if cast_data.get("active_quest"):
            meta_parts.append(f"Текущая цель: {cast_data['active_quest']}")
        rel_ctx = cast_data.get("relationship_context") or {}
        dyn_tone = rel_ctx.get("dynamic_tone") or cast_data.get("relationship_state", "")
        foundation = rel_ctx.get("foundation", "")
        bond = rel_ctx.get("current_bond", "")

        focus_name = cast_data.get("focus_companion") or companion_name or "Спутница"
        if not is_environment:
            party = RPCastManager.get_party_companions(cast_data)
            if len(party) > 1:
                other_members = [
                    ent.get("canonical_name", eid)
                    for eid, ent in party.items()
                    if ent.get("canonical_name", eid).lower().strip() != focus_name.lower().strip()
                ]
                if other_members:
                    meta_parts.append("Отряд рядом: " + ", ".join(other_members))

            rel_lines = []
            if foundation:
                rel_lines.append(f"Фундамент связи: {foundation}")
            if bond and bond != foundation:
                rel_lines.append(f"Текущий статус союза: {bond}")
            elif not foundation and bond:
                rel_lines.append(f"Текущий статус союза: {bond}")
            if dyn_tone:
                rel_lines.append(f"Эмоциональный тон момента: {dyn_tone}")

            if rel_lines:
                meta_parts.append(f"Отношения со спутником ({focus_name}):\n  " + "\n  ".join(f"• {line}" for line in rel_lines))
                combo_check = f"{foundation} {bond} {dyn_tone}".lower()
                if any(w in combo_check for w in ("любов", "возлюбл", "близост", "нежност", "страст", "поцелу", "постел")):
                    meta_parts.append("ДИРЕКТИВА: Между вами и игроком есть опыт физической/эмоциональной близости. Категорически запрещено сбрасывать общение в холодный тон.")
        elif dyn_tone:
            meta_parts.append(f"Эмоциональный тон: {dyn_tone}")

        p_state = cast_data.get("player_state") or {}
        c_state = cast_data.get("companion_state") or {}
        p_vital = p_state.get("vital_status", "alive")
        c_vital = c_state.get("vital_status", "alive")
        dead_list = []
        if p_vital == "dead":
            meta_parts.append("Статус игрока: ПОГИБ (МЁРТВ)")
            dead_list.append("Игрок")
        elif p_vital == "critical":
            meta_parts.append("Статус игрока: Тяжело ранен / При смерти")

        if not is_environment:
            if c_vital == "dead":
                meta_parts.append("Статус спутницы: ПОГИБЛА (МЕРТВА)")
                dead_list.append("Спутница")
            elif c_vital == "critical":
                meta_parts.append("Статус спутницы: Тяжело ранена / Без сознания")

        if dead_list:
            meta_parts.append(f"ДИРЕКТИВА: Погибший персонаж ({', '.join(dead_list)}) мёртв: запрещено оживлять его или описывать его действия, пока игрок прямо не воскресит его.")

        p_muts = p_state.get("mutations", [])
        if p_muts:
            meta_parts.append(f"Следы/раны игрока: {', '.join(p_muts)}")

        if not is_environment:
            c_muts = c_state.get("mutations", [])
            if c_muts:
                meta_parts.append(f"Следы/раны спутницы: {', '.join(c_muts)}")

        if cast_data.get("inventory"):
            meta_parts.append(f"Инвентарь: {', '.join(cast_data['inventory'])}")

        if cast_data.get("stats"):
            meta_parts.append("Текущие параметры и шкалы (<Stats>): " + ", ".join(f"{k}: {v}" for k, v in cast_data["stats"].items()))

        intimacy_triggers = (
            "целу", "поцелу", "губ", "обним", "обня", "прижал", "притян", 
            "ласк", "нежн", "страст", "постел", "шатер", "палатк", "раздел", 
            "тело", "признан", "люблю", "ночь", "близост"
        )
        user_is_intimate = any(trig in text_lower for trig in intimacy_triggers)

        # Детерминированный триггер уместности наряда и близости (только для живых спутниц)
        if not is_environment:
            comp_outfit = c_state.get("current_outfit", "")
            if comp_outfit:
                meta_parts.append(f"Наряд спутницы: {comp_outfit}")

            loc_lower = str(cast_data.get("current_location", "")).lower()
            rest_markers = ("шатер", "шатёр", "палатк", "спальн", "поко", "комнат", "таверн", "купальн", "лагерь", "дом")
            travel_markers = ("дорог", "тракт", "лес", "троп", "перевал", "путь", "выход", "улиц", "пустош", "город", "руин")

            is_resting_spot = any(rm in loc_lower for rm in rest_markers)
            is_traveling_spot = any(tm in loc_lower for tm in travel_markers) or any(w in text_lower for w in ("выходим", "в путь", "по коням", "пошли", "выдвигаемся", "на улиц"))

            has_heavy_gear = any(hg in comp_outfit.lower() for hg in ("armor", "plate", "chainmail", "cloak", "доспех", "броня", "кирас")) if comp_outfit else False
            is_indoor_attire = any(ia in comp_outfit.lower() for ia in ("chemise", "nightgown", "underwear", "towel", "сорочк", "бель")) if comp_outfit else False

            # 1. Пик близости -> фиксация момента и сброс одежды
            if user_is_intimate:
                if comp_outfit and not any(nd in comp_outfit.lower() for nd in ("nude", "naked", "underwear", "chemise")):
                    meta_parts.append(
                        "ДЕЙСТВИЕ СПУТНИЦЫ: Пик физической и эмоциональной близости. "
                        "Спутница по своей воле избавляется от мешающей одежды/шнуровки, отдаваясь моменту."
                    )
            # 2. Вход в укрытие/отдых -> снять доспехи/плащ
            elif is_resting_spot and has_heavy_gear:
                meta_parts.append(
                    "ДЕЙСТВИЕ СПУТНИЦЫ: Вы расположились на отдых/ночлег в укрытии. "
                    "Оставаться в тяжелых латах/дорожном плаще неестественно. "
                    "Пусть спутница снимет тяжелую броню/сапоги и останется в удобной домашней сорочке или тунике."
                )
            # 3. Выход из укрытия в путь -> снаряжение обратно
            elif is_traveling_spot and is_indoor_attire:
                meta_parts.append(
                    "ДЕЙСТВИЕ СПУТНИЦЫ: Вы выходите из укрытия в дорогу. "
                    "Спутнице нужно собраться и одеться по погоде: застегнуть походный плащ, подтянуть ремни и взять вещи перед выходом."
                )

        active_nudge = cast_data.get("pending_event_nudge")
        if active_nudge:
            meta_parts.append(
                f"Фоновое микро-событие: {active_nudge}.\n"
                f"ДИРЕКТИВА: Органично вплети этот штрих (для ГМ — в окружение мира; для спутницы — в ее реакцию или наблюдение), сохраняя общую нить диалога."
            )

        if meta_parts:
            res["meta_context"] = (
                "\n<SceneContext>\n"
                + "\n".join(meta_parts) +
                "\n</SceneContext>\n"
            )

        return res

    @staticmethod
    def get_world_state(session_id: Optional[str]) -> List[str]:
        if not session_id:
            return []
        data = RPCastManager.load_cast(session_id)
        return data.get("world_state", [])

    @staticmethod
    def get_npc_visual_dna(session_id: Optional[str], npc_name: str) -> Optional[str]:
        if not session_id or not npc_name:
            return None
        data = RPCastManager.load_cast(session_id)
        target = npc_name.lower().strip()
        for ent in data.get("entities", {}).values():
            if ent.get("canonical_name", "").lower() == target or target in ent.get("aliases", []):
                return ent.get("visual_dna")
        return None

    @staticmethod
    def apply_scene_updates(session_id: str, updates: Dict[str, Any], user_name: str = "Игрок", companion_name: str = "Спутница") -> None:
        apply_scene_updates_to_cast(session_id, updates, user_name=user_name, companion_name=companion_name)


# Re-export from services.rp_cast_extractor for 100% backward compatibility
from services.rp_cast_extractor import (
    RPCastExtractor,
    build_cast_extraction_spec,
    CAST_EXTRACTOR_SYSTEM_PROMPT,
)

__all__ = [
    "RPCastManager",
    "RPCastExtractor",
    "build_cast_extraction_spec",
    "CAST_EXTRACTOR_SYSTEM_PROMPT",
    "BASE_RESERVED_NPC_NAMES",
    "FORBIDDEN_NPC_NAMES",
    "COMMON_NOUN_NAMES",
    "SCAN_KEYWORDS",
    "get_forbidden_npc_names",
    "_get_name_inflections",
    "apply_scene_updates_to_cast",
]
