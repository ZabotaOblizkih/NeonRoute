"""
RPSceneUpdater: Scene mutation logic and name heuristics for NeonRoute Roleplay Mode.
Handles location transitions, inventory mutations, world state hazards, outfit/mutation
tracking, NPC registration, and vector memory integration.
"""

import re
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Set

logger = logging.getLogger("NeonRoute.RPSceneUpdater")

COMMON_NOUN_NAMES = frozenset({"свет", "света", "вера", "роза", "любовь", "надежда", "лилия", "май", "мир"})

def _get_name_inflections(name: str) -> set[str]:
    clean = name.strip()
    if not clean:
        return set()
    low = clean.lower()
    forms = {low}
    if low.endswith("а") and len(low) > 2:
        stem = low[:-1]
        forms.update({stem + "ы", stem + "и", stem + "е", stem + "у", stem + "ой", stem + "ою"})
    elif low.endswith("я") and len(low) > 2:
        stem = low[:-1]
        forms.update({stem + "и", stem + "е", stem + "ю", stem + "ей", stem + "ею"})
    elif low.endswith(("й", "ь")) and len(low) > 2:
        stem = low[:-1]
        forms.update({stem + "я", stem + "ю", stem + "ем", stem + "е"})
    elif len(low) > 2 and low[-1] in "бвгджзклмнпрстфхцчшщ":
        stem = low
        forms.update({stem + "а", stem + "у", stem + "ом", stem + "е"})
    return forms

BASE_RESERVED_NPC_NAMES = frozenset({
    "персонаж", "игрок", "user", "assistant", "system", "narrator",
    "рассказчик", "спутник", "спутница", "напарник", "напарница"
})

def get_forbidden_npc_names(user_name: str = "", companion_name: str = "", companion_names: Optional[List[str]] = None) -> set:
    """Возвращает изолированный набор зарезервированных имён без мутации глобального состояния."""
    names = set(BASE_RESERVED_NPC_NAMES)
    for n in (user_name, companion_name):
        if n and n.strip():
            names.add(n.lower().strip())
    if companion_names:
        for cn in companion_names:
            if cn and cn.strip():
                names.add(cn.lower().strip())
    return names

FORBIDDEN_NPC_NAMES = BASE_RESERVED_NPC_NAMES


def apply_scene_updates_to_cast(
    session_id: str,
    updates: Dict[str, Any],
    user_name: str = "Игрок",
    companion_name: str = "Спутница"
) -> None:
    if not session_id or not updates:
        return

    from services.rp_cast_manager import RPCastManager
    cast_data = RPCastManager.load_cast(session_id, user_name=user_name, companion_name=companion_name)
    modified = False

    # 1. Локация и атмосфера среды
    env = cast_data.setdefault("environment", {
        "world_baseline": "",
        "scene_ambience": "",
        "location_dna": "",
        "is_enclosed": False
    })

    new_loc = updates.get("new_location")
    new_loc_dna = updates.get("new_location_dna")
    if new_loc and str(new_loc).strip().lower() not in ("null", "none", ""):
        loc_clean = str(new_loc).strip()
        if loc_clean.lower() != cast_data.get("current_location", "").lower():
            cast_data["current_location"] = loc_clean
            if new_loc_dna and str(new_loc_dna).strip().lower() not in ("null", "none", ""):
                env["location_dna"] = str(new_loc_dna).strip()
            else:
                from services.art_director.prompting import sanitize_and_translate_tags
                env["location_dna"] = sanitize_and_translate_tags(loc_clean)

            for ent in cast_data.get("entities", {}).values():
                if ent.get("role") != "companion" and ent.get("presence") == "physical":
                    ent["ttl"] = 2
                    ent["presence"] = "memory"
            ws = cast_data.get("world_state", [])
            if ws:
                cast_data["world_state"] = [r for r in ws if any(g in r.lower() for g in ("закон", "магия", "мир", "всегда", "прокляти"))]
            logger.info(f"[RPCast] Переход в локацию '{loc_clean}' (location_dna: '{env.get('location_dna')}').")
            modified = True

    new_amb = updates.get("scene_ambience")
    if new_amb and str(new_amb).strip().lower() not in ("null", "none", ""):
        env["scene_ambience"] = str(new_amb).strip()
        modified = True
        logger.info(f"[RPCast] Обновлена атмосфера сцены: {env['scene_ambience']}")

    enc_flag = updates.get("is_enclosed")
    if enc_flag is not None and isinstance(enc_flag, bool):
        env["is_enclosed"] = enc_flag
        modified = True

    # 2. Квест
    q = updates.get("active_quest")
    if q and q.strip() and q.strip().lower() != "null":
        cast_data["active_quest"] = q.strip()
        modified = True

    # 3. Живой консенсус отношений (Dynamic Tone & Relationship Shift)
    rel_ctx = cast_data.setdefault("relationship_context", {
        "dynamic_tone": "",
        "foundation": "",
        "current_bond": ""
    })

    # Dynamic Tone
    new_tone = updates.get("dynamic_tone") or updates.get("relationship_state")
    if new_tone and str(new_tone).strip().lower() not in ("null", "none", ""):
        clean_tone = str(new_tone).strip()
        rel_ctx["dynamic_tone"] = clean_tone
        cast_data["relationship_state"] = clean_tone  # Backwards compatibility
        modified = True

    # Relationship Shift (Bond & Foundation)
    shift = updates.get("relationship_shift") or updates.get("intimacy_milestone")
    if shift and str(shift).strip().lower() not in ("null", "none", ""):
        clean_shift = str(shift).strip()
        
        # Если фундамент еще не был задан, фиксируем изначальную базу
        if not rel_ctx.get("foundation"):
            rel_ctx["foundation"] = clean_shift
        else:
            rel_ctx["current_bond"] = clean_shift

        # Синхронизация с legacy intimacy_milestones без дубликатов и костылей
        m_list = cast_data.setdefault("intimacy_milestones", [])
        if clean_shift not in m_list:
            m_list.append(clean_shift)
            if len(m_list) > 3:
                cast_data["intimacy_milestones"] = [m_list[0], m_list[-1]]
        
        modified = True
        logger.info(f"[RPCast] Зафиксирован сдвиг отношений: {clean_shift}")

    # 5. Инвентарь (с защитой ключевых сюжетных предметов от вытеснения)
    KEY_ITEM_MARKERS = (
        "ключ", "амулет", "кольцо", "печать", "клинок", "меч", "посох",
        "книга", "свиток", "реликви", "артефакт", "документ", "письмо",
        "карта", "кинжал", "броня", "доспех", "медальон", "кристалл",
        "кулон", "диадем", "корон", "дневник", "кулон", "талисман"
    )

    inv = cast_data.setdefault("inventory", [])
    add_item = updates.get("added_item")
    if add_item and add_item.strip() and add_item.strip().lower() != "null":
        item_clean = add_item.strip()
        if not any(item_clean.lower() in i.lower() for i in inv):
            inv.append(item_clean)
            
            # Если инвентарь переполнен (> 16), вытесняем старейший расходник, защищая реликвии
            if len(inv) > 16:
                evicted_idx = None
                for idx, itm in enumerate(inv):
                    itm_low = itm.lower()
                    if not any(k in itm_low for k in KEY_ITEM_MARKERS):
                        evicted_idx = idx
                        break
                
                if evicted_idx is not None:
                    dropped = inv.pop(evicted_idx)
                    logger.info(f"[RPCast] Инвентарь переполнен: вытеснен расходник '{dropped}'")
                elif len(inv) > 24:
                    # Аварийный потолок: если весь инвентарь из ключевых предметов
                    inv.pop(0)

            modified = True
            logger.info(f"[RPCast] Инвентарь: + {item_clean}")

        rem_item = updates.get("removed_item")
        if rem_item and rem_item.strip() and rem_item.strip().lower() != "null":
            kw = rem_item.strip().lower()
            pattern = re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
            for idx, itm in enumerate(inv):
                if pattern.search(itm):
                    dropped = inv.pop(idx)
                    modified = True
                    logger.info(f"[RPCast] Инвентарь: - {dropped} (по совпадению с '{kw}')")
                    break

    # 6. Опасности (смысловое сопоставление по пересечению слов)
    ws = cast_data.setdefault("world_state", [])
    resolved = updates.get("resolved_rule")
    if resolved and resolved.strip() and resolved.strip().lower() != "null":
        clean_res = resolved.strip().lower()
        res_words = set(re.findall(r'\b[a-zA-Zа-яА-ЯёЁ]{2,}\b', clean_res))
        orig_ws_len = len(ws)
        new_ws = []
        for r in ws:
            r_clean = r.strip().lower()
            r_words = set(re.findall(r'\b[a-zA-Zа-яА-ЯёЁ]{2,}\b', r_clean))
            overlap = len(res_words & r_words)
            is_direct_match = clean_res in r_clean or r_clean in clean_res
            if is_direct_match or overlap >= 1 or (r_words and (overlap / len(r_words)) >= 0.4):
                logger.info(f"[RPCast] Опасность устранена по смыслу: '{r}' (снято через '{resolved}')")
                modified = True
            else:
                new_ws.append(r)
        if len(new_ws) < orig_ws_len:
            cast_data["world_state"] = new_ws

    new_r = updates.get("new_rule")
    if new_r and new_r.strip() and new_r.strip().lower() != "null":
        r_clean = new_r.strip()
        if not any(r_clean.lower() in r.lower() for r in ws):
            ws.append(r_clean)
            if len(ws) > 5:
                ws.pop(0)
            modified = True
            logger.info(f"[RPCast] Новая опасность: {r_clean}")

    # 7. Физические следы и шрамы игрока
    p_mut = updates.get("player_mutation")
    if p_mut and str(p_mut).strip().lower() != "null":
        tags = [t.strip() for t in str(p_mut).split(",") if t.strip()]
        if tags:
            p_state = cast_data.setdefault("player_state", {})
            muts = p_state.setdefault("mutations", [])
            for t in tags:
                if not any(t.lower() == m.lower() for m in muts):
                    muts.append(t)
            if len(muts) > 8:
                cast_data["player_state"]["mutations"] = muts[-8:]
            modified = True
            logger.info(f"[RPCast] Внешность игрока (+следы): {tags}")

    # 8. Физические следы спутницы
    c_mut = updates.get("companion_mutation")
    if c_mut and str(c_mut).strip().lower() != "null":
        tags = [t.strip() for t in str(c_mut).split(",") if t.strip()]
        if tags:
            c_state = cast_data.setdefault("companion_state", {})
            c_muts = c_state.setdefault("mutations", [])
            for t in tags:
                if not any(t.lower() == m.lower() for m in c_muts):
                    c_muts.append(t)
            if len(c_muts) > 8:
                cast_data["companion_state"]["mutations"] = c_muts[-8:]
            modified = True
            logger.info(f"[RPCast] Внешность спутницы (+следы): {tags}")

    # 9. Визуальный кадр сцены
    v_prompt = updates.get("visual_prompt")
    if v_prompt and v_prompt.strip() and v_prompt.strip().lower() != "null":
        cast_data["last_visual_prompt"] = v_prompt.strip()
        modified = True
        logger.info(f"[RPCast] Зафиксирован visual_prompt: {v_prompt.strip()[:60]}...")

    # 10. NPC
    npc_data = updates.get("npc") or {}
    raw_name = str(npc_data.get("name") or "").strip()
    has_update = bool(npc_data.get("has_update", False))

    if (has_update or raw_name) and raw_name.lower() not in ("null", "none", "", "false"):
        name_lower = raw_name.lower().strip()
        companion_party = [
            edata.get("canonical_name", "").strip()
            for edata in cast_data.get("entities", {}).values()
            if isinstance(edata, dict) and edata.get("role") == "companion" and edata.get("canonical_name")
        ]
        forbidden = get_forbidden_npc_names(user_name=user_name, companion_name=companion_name, companion_names=companion_party)

        if name_lower in forbidden or len(name_lower) < 2:
            logger.info(f"[RPCast] NPC пропущен: имя '{raw_name}' зарезервировано или слишком короткое")
        else:
            fact = str(npc_data.get("new_fact") or "").strip()
            if not fact:
                fact = "Участник событий"
            aliases = [a.lower().strip() for a in npc_data.get("aliases", []) if isinstance(a, str) and a.strip()]
            raw_presence = str(npc_data.get("presence") or "").strip().lower()
            presence = "physical"
            if raw_presence in ("physical", "memory"):
                presence = raw_presence
            
            raw_vital = str(npc_data.get("vital_status") or "").strip().lower()
            vital_status = "alive"
            if raw_vital in ("alive", "critical", "dead"):
                vital_status = raw_vital
            
            v_dna = str(npc_data.get("visual_dna") or "").strip()
            ttl_val = 3 if presence == "physical" and vital_status != "dead" else 0

            entities = cast_data.setdefault("entities", {})
            target_id = None
            for eid, edata in entities.items():
                if edata.get("canonical_name", "").lower() == name_lower or any(a == name_lower for a in edata.get("aliases", [])):
                    target_id = eid
                    break

            if not target_id:
                target_id = re.sub(r'[^a-zA-Z0-9_\-а-яА-ЯёЁ]', '_', name_lower)
                entities[target_id] = {
                    "role": "npc",
                    "canonical_name": raw_name,
                    "aliases": list(set([name_lower] + aliases)),
                    "facts": [fact],
                    "visual_dna": v_dna,
                    "presence": presence,
                    "vital_status": vital_status,
                    "ttl": ttl_val,
                    "updated_at": datetime.now().isoformat()
                }
                logger.info(f"[RPCast] Создан NPC [{raw_name}] ({presence}, {vital_status}): {fact}")
            else:
                ent = entities[target_id]
                ent["vital_status"] = vital_status
                cur_facts = ent.setdefault("facts", [])
                if not any(fact.lower() in f.lower() for f in cur_facts):
                    cur_facts.append(fact)
                    if len(cur_facts) > 6:
                        cur_facts.pop(0)
                ent["ttl"] = ttl_val
                ent["presence"] = presence
                if v_dna:
                    ent["visual_dna"] = v_dna
                ent["updated_at"] = datetime.now().isoformat()
                logger.info(f"[RPCast] Обновлен NPC [{ent.get('canonical_name')}] ({presence}, {vital_status}): {fact}")

            modified = True

    # 11. Фоновое случайное событие (Event Nudge)
    nudge = updates.get("event_nudge")
    rel_state = str(updates.get("relationship_state") or updates.get("dynamic_tone") or "").lower()
    is_intimate = bool(
        updates.get("relationship_shift")
        or updates.get("intimacy_milestone")
        or any(w in rel_state for w in ("страст", "нежн", "любов", "поцелу", "близост", "романтик", "объят"))
    )

    if is_intimate:
        cast_data["turns_since_last_event"] = 0
    elif nudge and nudge.strip() and nudge.strip().lower() != "null":
        clean_nudge = nudge.strip()
        cast_data["pending_event_nudge"] = clean_nudge
        cast_data["turns_since_last_event"] = 0
        modified = True
        logger.info(f"[RPCast] Подготовлен фоновый Event Nudge: '{clean_nudge}'")
    else:
        cast_data["turns_since_last_event"] = cast_data.get("turns_since_last_event", 0) + 1
        modified = True

    # 12. Гардероб спутницы (Adaptive Outfit)
    c_outfit = updates.get("companion_outfit")
    if c_outfit and c_outfit.strip() and c_outfit.strip().lower() != "null":
        clean_outfit = c_outfit.strip()
        c_state = cast_data.setdefault("companion_state", {})
        if c_state.get("current_outfit") != clean_outfit:
            c_state["current_outfit"] = clean_outfit
            modified = True

    # 12a. Таргетированное обновление одежды и ранений спутниц отряда (Companion Updates)
    comp_updates = updates.get("companion_updates")
    if comp_updates and isinstance(comp_updates, list):
        entities = cast_data.setdefault("entities", {})
        for c_upd in comp_updates:
            if not isinstance(c_upd, dict):
                continue
            t_name = str(c_upd.get("name") or "").lower().strip()
            if not t_name:
                continue
            for ent in entities.values():
                if not isinstance(ent, dict) or ent.get("role") != "companion":
                    continue
                c_canon = ent.get("canonical_name", "").lower().strip()
                c_aliases = [str(a).lower().strip() for a in ent.get("aliases", []) if isinstance(a, str)]
                if t_name == c_canon or t_name in c_aliases or c_canon in t_name:
                    st = ent.setdefault("companion_state", {})
                    new_outfit = c_upd.get("outfit")
                    if new_outfit and str(new_outfit).strip().lower() not in ("null", "none", ""):
                        st["current_outfit"] = str(new_outfit).strip()
                        logger.info(f"[RPCast] Обновлен наряд спутницы [{ent.get('canonical_name')}]: {st['current_outfit']}")
                    new_mut = c_upd.get("mutation")
                    if new_mut and str(new_mut).strip().lower() not in ("null", "none", ""):
                        tags = [t.strip() for t in str(new_mut).split(",") if t.strip()]
                        muts = st.setdefault("mutations", [])
                        for t in tags:
                            if not any(t.lower() == m.lower() for m in muts):
                                muts.append(t)
                        if len(muts) > 8:
                            st["mutations"] = muts[-8:]
                        logger.info(f"[RPCast] Добавлены ранения/следы спутнице [{ent.get('canonical_name')}]: {tags}")
                    focus_c = (cast_data.get("focus_companion") or companion_name or "").strip().lower()
                    if focus_c and (t_name == focus_c or c_canon == focus_c or focus_c in c_aliases or c_canon in focus_c):
                        cast_data["companion_state"] = dict(st)
                    modified = True
                    break

    # 13. Обновление шкал состояния (Stats)
    st_upd = updates.get("stat_updates")
    if st_upd and isinstance(st_upd, dict):
        cur_stats = cast_data.setdefault("stats", {})
        for stat_name, delta in st_upd.items():
            if not stat_name:
                continue
            delta_str = str(delta).strip()
            if delta_str.startswith(("+", "-")):
                try:
                    val = int(delta_str)
                    old_v = int(cur_stats.get(stat_name, 0))
                    cur_stats[stat_name] = max(0, old_v + val)
                    modified = True
                    logger.info(f"[RPCast] Шкала '{stat_name}': {old_v} -> {cur_stats[stat_name]}")
                except ValueError:
                    cur_stats[stat_name] = delta_str
                    modified = True
            else:
                cur_stats[stat_name] = delta_str
                modified = True

    # Зеркалирование обновленных срезов в досье активного focus_companion
    focus_comp = (cast_data.get("focus_companion") or companion_name or "Спутница").strip()
    entities = cast_data.setdefault("entities", {})
    for ent in entities.values():
        if not isinstance(ent, dict):
            continue
        if ent.get("role") == "companion":
            c_cname = ent.get("canonical_name", "").lower().strip()
            aliases = [a.lower().strip() for a in ent.get("aliases", []) if isinstance(a, str)]
            if c_cname == focus_comp.lower() or focus_comp.lower() in aliases:
                ent["relational_cache"] = dict(cast_data.get("relationship_context", {}))
                ent["companion_state"] = dict(cast_data.get("companion_state", {}))
                break

    if modified:
        RPCastManager.save_cast(session_id, cast_data)

    # Автоматическая запись в векторную память сессии
    from services.memory_db import memory_db

    m_stone = updates.get("story_milestone")
    if m_stone and m_stone.strip() and m_stone.strip().lower() != "null":
        memory_db.add_rp_memory(session_id, m_stone.strip())

    rel_event = updates.get("relationship_shift") or updates.get("intimacy_milestone")
    if rel_event and str(rel_event).strip().lower() not in ("null", "none", ""):
        memory_db.add_rp_memory(session_id, f"Веха отношений: {str(rel_event).strip()}")


__all__ = [
    "COMMON_NOUN_NAMES",
    "BASE_RESERVED_NPC_NAMES",
    "FORBIDDEN_NPC_NAMES",
    "get_forbidden_npc_names",
    "_get_name_inflections",
    "apply_scene_updates_to_cast",
]
