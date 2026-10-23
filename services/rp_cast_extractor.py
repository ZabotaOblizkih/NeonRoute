"""
RPCastExtractor: Async Scene Fact, State, Mutation & Visual DNA Extractor for NeonRoute Roleplay Mode.
Extracts NPC updates, environment changes, mutations, dynamic tone, relationship shifts, and visual prompt.
"""

import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from config import HISTORY_DIR
from services.rp_constants import SCAN_KEYWORDS

if TYPE_CHECKING:
    from services.rp_cast_manager import RPCastManager

from services.task_queue import PRIORITY_LOW, InferenceTask, get_task_queue

logger = logging.getLogger("NeonRoute.RPCastExtractor")


def build_cast_extraction_spec(
    has_companion: bool = True,
    has_stats: bool = False,
    user_name: str = "Игрок",
    companion_name: str = "Спутница"
) -> Tuple[str, Dict[str, Any]]:
    """
    Динамически формирует системный промпт и строгую JSON-схему на основе Feature Flags карточки
    и явных субъектов сцены (Игрок <-> Спутница).
    """
    u_name = user_name.strip() if user_name and user_name.strip() else "Игрок"
    c_name = companion_name.strip() if companion_name and companion_name.strip() else "Спутница"

    protagonist_rule = (
        f"PROTAGONIST IDENTITY:\n"
        f"- The player/protagonist is strictly named '{u_name}'.\n"
        f"- In 'story_milestone' and 'active_quest', ALWAYS refer to the protagonist as '{u_name}'.\n"
        f"- NEVER invent, assume, or hallucinate names like 'Daniel' or any other name for the protagonist."
    )

    # 1. Базовые правила (Core) — присутствуют во всех ролевых карточках
    rules = [
        "1. npc: стороннее лицо (третий персонаж сцены). Запрещено создавать NPC из игрока или спутницы. Если в ПОСЛЕДНЕМ ходе появился/упомянут сторонний персонаж: has_update=true, укажи name (на русском), new_fact (на русском), visual_dna, presence ('physical'|'memory'), vital_status ('alive'|'critical'|'dead'). Иначе has_update=false.",
        "2. new_location: краткое название новой зоны на русском языке, ЕСЛИ отряд явно переместился (например: 'Поверхность у гробницы', 'Пустыня'), иначе null.",
        "2a. new_location_dna: English Danbooru tags for the new physical geometry (architecture, lighting, materials), or null if location has not changed.",
        "2b. scene_ambience: физическое состояние освещения, теней и воздуха в ТЕКУЩЕЙ точке сцены на русском языке (до 7 слов: источник света, полумрак/яркость, воздух, например: 'затхлый полумрак, свет факела, сырой камень' или 'палящий багровый зенит, сухой воздух'). Если обстановка не менялась — СТРОГО null.",
        "2c. is_enclosed: логический флаг (true/false/null): true — если персонажи внутри здания, пещеры, склепа, подземелья или каюты (изолированы от неба); false — если на открытом воздухе; null — если локация не менялась.",
        "3. active_quest: новая сюжетная цель сцены на русском языке (до 8 слов) или null.",
        "4. added_item / removed_item: точное название предмета на русском языке прямо из текста (например: 'факел', 'древний амулет'), иначе null. Запрещено переводить на английский!",
        "5. player_mutation: новые стойкие травмы/ранения игрока на русском языке (до 5 слов) или null.",
        "6. new_rule / resolved_rule: новая опасность/закон мира или снятая угроза на русском языке (до 10 слов), иначе null.",
        "7. visual_prompt: STRICTLY English Danbooru/SD tags for the key scene frame (up to 25 tags), including duo/2girls if multiple characters are present, or null.",
        "7a. aspect_ratio: frame framing ('wide' for landscapes/rooms/action, 'portrait' for standing characters/outfits/embrace, 'square' for face close-ups/busts/items, or 'none').",
        "8. scene_type: кинематографический архетип сцены (СТРОГО одно из: 'intimate' — уединение, спальня, купальня, романтика, нагота; 'action' — бой, магия, прямая угроза; 'dialogue' — разговор, быт, таверна, костер; 'exploration' — путешествие, руины, дорога).",
        "8a. suggested_model: id подходящего стиля арт-роутера или null.",
        "9. event_nudge: фоновая деталь для следующего хода СТРОГО на русском языке (до 10 слов) или null.",
        "10. story_milestone: ключевое сюжетное событие хода для хроники СТРОГО на русском языке (1 предложение) или null."
    ]

    schema_props: Dict[str, Any] = {
        "npc": {
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"]},
                "aliases": {"type": ["array", "null"], "items": {"type": "string"}},
                "new_fact": {"type": ["string", "null"]},
                "visual_dna": {"type": ["string", "null"]},
                "presence": {"type": "string", "enum": ["physical", "memory", "none"]},
                "vital_status": {"type": "string", "enum": ["alive", "critical", "dead", "none"]},
                "has_update": {"type": "boolean"}
            },
            "required": ["name", "aliases", "new_fact", "visual_dna", "presence", "vital_status", "has_update"],
            "additionalProperties": False
        },
        "new_location": {"type": ["string", "null"]},
        "new_location_dna": {"type": ["string", "null"]},
        "scene_ambience": {"type": ["string", "null"]},
        "is_enclosed": {"type": ["boolean", "null"]},
        "active_quest": {"type": ["string", "null"]},
        "added_item": {"type": ["string", "null"]},
        "removed_item": {"type": ["string", "null"]},
        "player_mutation": {"type": ["string", "null"]},
        "new_rule": {"type": ["string", "null"]},
        "resolved_rule": {"type": ["string", "null"]},
        "visual_prompt": {"type": ["string", "null"]},
        "aspect_ratio": {
            "type": "string",
            "enum": ["wide", "portrait", "square", "none"]
        },
        "scene_type": {
            "type": "string",
            "enum": ["dialogue", "action", "intimate", "exploration"]
        },
        "suggested_model": {"type": ["string", "null"]},
        "event_nudge": {"type": ["string", "null"]},
        "story_milestone": {"type": ["string", "null"]}
    }

    # 2. Модуль спутницы (Companion Dynamics) — только для одушевленных персонажей
    if has_companion:
        rules.extend([
            f"11. dynamic_tone: текущее эмоциональное состояние общения ИСКЛЮЧИТЕЛЬНО между {u_name} и {c_name} прямо сейчас на русском языке (до 4 слов, например: 'игривое смущение', 'боевой кураж', 'напряженное молчание') или null.",
            f"12. relationship_shift: фундаментальный сдвиг в статусе союза СТРОГО между {u_name} и {c_name} (на русском языке, 1 краткое емкое предложение до 15 слов: кем они теперь приходятся друг другу и ключевой якорь) ТОЛЬКО если произошел необратимый перелом в их взаимных отношениях (признание, первый поцелуй, первая ночь, клятва, предательство). "
            f"КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО фиксировать как сдвиг: действия третьих лиц, сторонних NPC или защиту одного NPC другим (например, если сторонний союзник закрыл {c_name} или другого спутника — это фиксируется в npc.new_fact, а для relationship_shift возвращается null). Если между {u_name} и {c_name} фундаментального перелома не было — СТРОГО null.",
            f"13. companion_outfit: смена наряда персонажа {c_name} в этом ходе на русском языке или null.",
            f"14. companion_mutation: новые стойкие травмы/ранения персонажа {c_name} на русском языке (до 5 слов) или null.",
            f"14a. companion_updates: список точечных обновлений наряда/ранений спутниц отряда (массив объектов name, outfit (новое описание одежды на английском или null), mutation (новые раны/шрамы через запятую или null)) или null."
        ])
        schema_props.update({
            "dynamic_tone": {"type": ["string", "null"]},
            "relationship_shift": {"type": ["string", "null"]},
            "companion_outfit": {"type": ["string", "null"]},
            "companion_mutation": {"type": ["string", "null"]},
            "companion_updates": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "outfit": {"type": ["string", "null"]},
                        "mutation": {"type": ["string", "null"]}
                    },
                    "required": ["name", "outfit", "mutation"],
                    "additionalProperties": False
                }
            }
        })

    # 3. Модуль ЛитРПГ/Исекай (Game System) — строго при наличии числовых шкал/статов
    if has_stats:
        rules.append(
            "15. stat_updates: числовые изменения характеристик или шкал (фиксируй ИСКЛЮЧИТЕЛЬНО если в тексте хода ЯВНО присутствуют игровые системные логи или изменение чисел). Если явных числовых изменений в тексте не было — СТРОГО null."
        )
        schema_props["stat_updates"] = {
            "type": ["object", "null"],
            "additionalProperties": {"type": "string"}
        }

    system_prompt = (
        "Ты — летописец ролевой игры. Зафиксируй ИСКЛЮЧИТЕЛЬНО НОВЫЕ изменения из ПОСЛЕДНЕГО хода сцены "
        "(если событие произошло раньше или не менялось — СТРОГО null).\n"
        f"{protagonist_rule}\n\n"
        "ЯЗЫКОВОЙ СТАНДАРТ: Все поля заполняй СТРОГО на чистом русском литературном языке. "
        "Исключение: поле visual_prompt заполняется СТРОГО английскими Danbooru/SD-тегами для генератора изображений, а suggested_model — идентификатором модели.\n\n"
        + "\n".join(rules) +
        "\n\nОтвечай строго валидным JSON в соответствии с переданной JSON-схемой."
    )

    schema_payload = {
        "type": "json_schema",
        "json_schema": {
            "name": "extracted_rp_scene_context",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": schema_props,
                "required": list(schema_props.keys())
            }
        }
    }

    return system_prompt, schema_payload


CAST_EXTRACTOR_SYSTEM_PROMPT = build_cast_extraction_spec(
    has_companion=True,
    has_stats=False,
    user_name="Игрок",
    companion_name="Спутница"
)[0]


class RPCastExtractor:
    _lock = threading.Lock()
    _active_sessions = set()
    _last_turn_signature = ""

    @staticmethod
    def extract_cast_async(
        messages_history: List[Dict[str, Any]],
        session_id: str,
        lm_client: Optional[Any] = None,
        context: Optional[Any] = None,
        user_name: Optional[str] = None,
        companion_name: Optional[str] = None,
        is_environment: bool = False
    ) -> None:
        if not session_id or not messages_history or len(messages_history) < 2:
            return

        if context is not None:
            if getattr(context, "_is_initializing", False) or getattr(context, "_is_switching_session", False):
                return
            if getattr(context, "current_mode", "roleplay") != "roleplay":
                return

        p_name = user_name or (getattr(context, "user_name", "Игрок") if context else "Игрок")
        c_name = companion_name or ("Окружение" if is_environment else "Спутница")
        if context and getattr(context, "character_card", None):
            c_name = getattr(context.character_card, "name", c_name) or c_name
        elif context and getattr(context, "active_character", None):
            c_name = getattr(context.active_character, "name", c_name) or c_name

        last_msg = messages_history[-1]
        if last_msg.get("is_user") or last_msg.get("role") == "user":
            return

        recent_texts = []
        for m in messages_history[-2:]:
            c = str(m.get("text") or m.get("content") or "")
            if c and not c.startswith("["):
                recent_texts.append(c)

        combined_text = " ".join(recent_texts).lower()
        has_keywords = any(kw in combined_text for kw in SCAN_KEYWORDS)

        from services.rp_cast_manager import RPCastManager
        cast_data = RPCastManager.load_cast(session_id, user_name=p_name, companion_name=c_name)
        has_known_npc = any(
            any(alias in combined_text for alias in ent.get("aliases", []))
            for ent in cast_data.get("entities", {}).values()
        )

        if not has_keywords and not has_known_npc:
            return

        turn_sig = f"{session_id}:{len(messages_history)}:{combined_text[:60]}"
        with RPCastExtractor._lock:
            if turn_sig == RPCastExtractor._last_turn_signature:
                return
            RPCastExtractor._last_turn_signature = turn_sig

        tq = get_task_queue()
        task_args = (messages_history[-4:], session_id, lm_client, context, p_name, c_name, is_environment)
        if tq:
            task = InferenceTask(
                name="🎭 Комплексный анализ сцены",
                task_type="memory",
                func=RPCastExtractor._run_extraction,
                args=task_args,
                priority=PRIORITY_LOW,
                metadata={"silent": True, "session_id": session_id}
            )
            if hasattr(context, "_active_tasks"):
                context._active_tasks[task.id] = task
            tq.enqueue(task)
        else:
            threading.Thread(
                target=RPCastExtractor._run_extraction,
                args=task_args,
                daemon=True,
                name="RPCastExtractorThread"
            ).start()

    @staticmethod
    def _run_extraction(
        recent_history: List[Dict[str, Any]],
        session_id: str,
        lm_client: Optional[Any] = None,
        context: Optional[Any] = None,
        user_name: Optional[str] = None,
        companion_name: Optional[str] = None,
        is_environment: bool = False
    ) -> Optional[Dict[str, Any]]:
        ext_key = f"{session_id}_{is_environment}"
        with RPCastExtractor._lock:
            if ext_key in RPCastExtractor._active_sessions:
                return None
            RPCastExtractor._active_sessions.add(ext_key)

        try:
            client = lm_client
            if client is None:
                from config import DEFAULT_LM_STUDIO_URL
                from services.lm_studio import LMStudioClient
                client = LMStudioClient(DEFAULT_LM_STUDIO_URL)

            p_name = user_name or (getattr(context, "user_name", "Игрок") if context else "Игрок")
            c_name = companion_name or ("Окружение" if is_environment else "Спутница")
            if context and getattr(context, "character_card", None):
                c_name = getattr(context.character_card, "name", c_name) or c_name
            elif context and getattr(context, "active_character", None):
                c_name = getattr(context.active_character, "name", c_name) or c_name

            from services.rp_cast_manager import RPCastManager
            cast_data = RPCastManager.load_cast(session_id, user_name=p_name, companion_name=c_name)
            active_focus_name = cast_data.get("focus_companion") or c_name
            meta_context_lines = []
            if cast_data.get("current_location"):
                meta_context_lines.append(f"Локация: {cast_data['current_location']}")

            p_muts = cast_data.get("player_state", {}).get("mutations", [])
            if p_muts:
                meta_context_lines.append(f"Стойкие следы игрока: {', '.join(p_muts)}")

            if not is_environment:
                rel_ctx = cast_data.get("relationship_context") or {}
                rel_status = rel_ctx.get("current_bond") or rel_ctx.get("foundation") or ""
                if rel_status:
                    meta_context_lines.append(f"Отношения со спутницей ({active_focus_name}): {rel_status}")

                c_muts = cast_data.get("companion_state", {}).get("mutations", [])
                if c_muts:
                    meta_context_lines.append(f"Стойкие следы спутницы ({active_focus_name}): {', '.join(c_muts)}")

                c_outfit = cast_data.get("companion_state", {}).get("current_outfit", "")
                if c_outfit:
                    meta_context_lines.append(f"Текущий наряд спутницы ({active_focus_name}): {c_outfit}")

            if len(recent_history) >= 2:
                context_msgs = recent_history[:-2]
                last_turn_msgs = recent_history[-2:]
            else:
                context_msgs = []
                last_turn_msgs = recent_history

            dialogue_lines = []
            if context_msgs:
                dialogue_lines.append("Контекст прошлых реплик (для справки):")
                for m in context_msgs:
                    txt = str(m.get("text") or m.get("content") or "").strip()
                    if txt and not txt.startswith("["):
                        role = "ИГРОК" if (m.get("is_user") or m.get("role") == "user") else ("СПУТНИЦА" if not is_environment else "СРЕДА")
                        dialogue_lines.append(f"{role}: {txt}")
                dialogue_lines.append("")

            dialogue_lines.append("ПОСЛЕДНИЙ ХОД (анализируй только эти новые действия):")
            for m in last_turn_msgs:
                txt = str(m.get("text") or m.get("content") or "").strip()
                if txt and not txt.startswith("["):
                    role = "ИГРОК" if (m.get("is_user") or m.get("role") == "user") else ("СПУТНИЦА" if not is_environment else "СРЕДА")
                    dialogue_lines.append(f"{role}: {txt}")

            user_prompt_parts = []
            if meta_context_lines:
                user_prompt_parts.append("ТЕКУЩИЙ СТАТУС ДО ЭТОГО ХОДА:\n" + "\n".join(meta_context_lines) + "\n")
            user_prompt_parts.append("\n".join(dialogue_lines))

            from services.model_router import ModelRouter
            router = ModelRouter()
            active_models = [m for m in getattr(router, "profiles", []) if m.get("active", True)]
            styles_block = "\n".join(f"- {m.get('id')}: {m.get('routing_description', '')}" for m in active_models)

            # Определение Feature Flags активной карточки
            card = None
            if context and getattr(context, "character_card", None):
                card = context.character_card
            elif context and getattr(context, "active_character", None):
                card = context.active_character

            has_companion = not is_environment
            if card and getattr(card, "is_environment", False):
                has_companion = False

            # SSOT: статус ЛитРПГ определяется строго метаданными самой карточки
            # Исключает вечное воскрешение статов из старых загрязненных файлов сессии
            has_stats = bool(card and getattr(card, "is_system", False))

            # Генерация точного системного промпта и строгой схемы под активную карточку и имена актеров
            dyn_system_prompt, schema_payload = build_cast_extraction_spec(
                has_companion=has_companion,
                has_stats=has_stats,
                user_name=p_name,
                companion_name=active_focus_name
            )

            if not has_companion:
                actor_guard = (
                    f"\nГлавные роли:\n"
                    f"- Игрок: {p_name}\n"
                    f"- Среда/Мир: {active_focus_name}\n"
                    f"Запрещено создавать NPC из игрока ({p_name}).\n"
                )
            else:
                actor_guard = (
                    f"\nГлавные герои (не являются NPC):\n"
                    f"- Игрок: {p_name}\n"
                    f"- Спутница: {active_focus_name}\n"
                    f"Запрещено создавать NPC из {p_name} или {active_focus_name}.\n"
                )

            system_content = f"{dyn_system_prompt}\n{actor_guard}"
            if styles_block:
                system_content = f"{system_content}\nДОСТУПНЫЕ СТИЛИ (для suggested_model):\n{styles_block}\n"

            req_messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": "\n".join(user_prompt_parts)}
            ]

            active_loaded_ctx = getattr(client, "_current_loaded_ctx", 24576) or 24576
            active_loaded_model = getattr(client, "_last_loaded_model_id", None)

            raw_resp = client.chat_completion(
                messages=req_messages,
                model=active_loaded_model,
                temperature=0.0,
                max_tokens=700,
                context_length=active_loaded_ctx,
                is_roleplay=True,
                stream=False,
                emit_signal=False,
                response_format=schema_payload
            )

            from services.regex_filter import TextRegexFilter
            clean_resp = TextRegexFilter.process_output(str(raw_resp or "")).strip()
            if "```" in clean_resp:
                clean_resp = re.sub(r'^```(?:json)?\s*|\s*```$', '', clean_resp, flags=re.IGNORECASE).strip()

            data = None
            try:
                data = json.loads(clean_resp)
            except json.JSONDecodeError:
                depth = 0
                start_idx = -1
                found_objects = []
                for idx, char in enumerate(clean_resp):
                    if char == '{':
                        if depth == 0:
                            start_idx = idx
                        depth += 1
                    elif char == '}':
                        depth -= 1
                        if depth == 0 and start_idx != -1:
                            try:
                                candidate = json.loads(clean_resp[start_idx:idx+1])
                                if isinstance(candidate, dict):
                                    found_objects.append(candidate)
                            except json.JSONDecodeError:
                                pass
                            start_idx = -1
                if found_objects:
                    data = found_objects[-1]

            if data and isinstance(data, dict):
                if not (HISTORY_DIR / f"chat_{session_id}.json").exists():
                    logger.info(f"[RPCast] Чат chat_{session_id}.json удален. Прекращение записи каста.")
                    return None

                logger.info(f"[RPCast] Сырой JSON анализа сцены: {json.dumps(data, ensure_ascii=False)}")
                result_data = data
                RPCastManager.apply_scene_updates(session_id, result_data, user_name=p_name, companion_name=active_focus_name)

                cast_data = RPCastManager.load_cast(session_id, user_name=p_name, companion_name=active_focus_name)
                scene_type = str(result_data.get("scene_type") or "dialogue").lower().strip()
                if scene_type not in ("intimate", "action", "dialogue", "exploration"):
                    scene_type = "dialogue"

                cast_data["scene_type"] = scene_type
                RPCastManager.save_cast(session_id, cast_data)

                logger.info(
                    f"[RPCastExtractor] 🎬 Кино-архетип сцены: '{scene_type.upper()}' | "
                    f"Локация: '{cast_data.get('current_location', 'Не указана')}' | "
                    f"Одежда: {cast_data.get('companion_state', {}).get('current_outfit', 'Канон')}"
                )

                v_prompt = data.get("visual_prompt")
                suggested_model_raw = data.get("suggested_model")
                raw_ar = data.get("aspect_ratio")
                parsed_ar = raw_ar.strip().lower() if (raw_ar and isinstance(raw_ar, str) and raw_ar.strip().lower() not in ("none", "null")) else None
                if v_prompt and v_prompt.strip() and v_prompt.strip().lower() != "null":
                    clean_v = v_prompt.strip()

                    # Валидация suggested_model
                    suggested_model = None
                    if suggested_model_raw and isinstance(suggested_model_raw, str) and suggested_model_raw.strip().lower() != "null":
                        cand_m = suggested_model_raw.strip()
                        active_ids = {m.get("id") for m in active_models} if active_models else set()
                        if cand_m in active_ids:
                            suggested_model = cand_m

                    # Актуальные мутации после применения изменений сцены
                    updated_cast = cast_data
                    cur_p_muts = updated_cast.get("player_state", {}).get("mutations", [])
                    cur_c_muts = updated_cast.get("companion_state", {}).get("mutations", [])
                    comp_dna = updated_cast.get("companion_state", {}).get("visual_dna", "")
                    comp_outfit = updated_cast.get("companion_state", {}).get("current_outfit", "")

                    visual_intent = {
                        "session_id": session_id,
                        "message_id": recent_history[-1].get("id") if recent_history else None,
                        "prompt": clean_v,
                        "suggested_model": suggested_model,
                        "aspect_ratio": parsed_ar,
                        "scene_type": scene_type,
                        "mutations": list(cur_p_muts if is_environment else (cur_p_muts + cur_c_muts)),
                        "companion_dna": "" if is_environment else comp_dna,
                        "companion_outfit": "" if is_environment else comp_outfit,
                        "status": "ready"
                    }

                    # Безопасная передача результата в главный UI-поток через очередь событий Qt
                    if context is not None:
                        try:
                            from PyQt6.QtCore import QTimer
                            if hasattr(context, "_on_scene_visual_intent_ready"):
                                QTimer.singleShot(0, context, lambda sid=session_id, vi=visual_intent: context._on_scene_visual_intent_ready(sid, vi))
                            elif hasattr(context, "_on_scene_visual_prompt_extracted"):
                                QTimer.singleShot(0, context, lambda vp=clean_v, sid=session_id: context._on_scene_visual_prompt_extracted(vp, sid))
                        except Exception as e_ui:
                            logger.debug(f"[RPCast] Колбэк UI пропущен: {e_ui}")

                    return {
                        "session_id": session_id,
                        "visual_prompt": clean_v,
                        "visual_intent": visual_intent,
                        "updates": data
                    }

                return {"session_id": session_id, "visual_prompt": None, "updates": data}
            else:
                logger.warning(f"[RPCast] Не найден валидный JSON в ответе: '{clean_resp[:180]}'")

        except Exception as e:
            logger.debug(f"[RPCastExtractor] Фоновая ошибка: {e}")
        finally:
            with RPCastExtractor._lock:
                RPCastExtractor._active_sessions.discard(ext_key)
        return None
