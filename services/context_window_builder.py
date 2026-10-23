"""
Context Window Builder for NeonRoute.
Handles prompt assembly, token budgeting, KV-cache-friendly history trimming,
and context injection for Assistant, Roleplay, and Engineer modes.
"""

import re
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

from config import MASTER_MAX_RESPONSE_TOKENS
from services.token_budget_manager import TokenBudgetManager
from services.tavern_parser import CharacterCard, TavernParser
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.assistant_prompt_builder import AssistantPromptBuilder
from services.memory_db import memory_db
from services.rp_cast_manager import RPCastManager
from services.user_profile_manager import UserProfileManager
from services.engineer_prompts import get_engineer_system_prompt
from services.macro_engine import MacroEngine

logger = logging.getLogger("NeonRoute.ContextWindowBuilder")


def get_mode_profile(mode: str = "assistant") -> Dict[str, Any]:
    """Получает профиль режима из services.session_manager."""
    from services.session_manager import get_mode_profile as _gmp
    return _gmp(mode)


LORE_TRIGGER_PATTERNS = [
    re.compile(r'\bпредупреждал(?:а|и)?(?:сь)?\b.{1,60}?\b(?:что|о|про)\b', re.IGNORECASE),
    re.compile(r'\bнельзя\b.{1,40}?\b(?:идти|ходить|соваться|бывать|делать|трогать|входить|приближаться|доверять)\b', re.IGNORECASE),
    re.compile(r'\bопасно\b.{1,40}?\b(?:идти|ходить|соваться|приближаться|находиться|доверять|оставаться|здесь|там)\b', re.IGNORECASE),
    re.compile(r'\bзапрещено\b.{1,40}?\b(?:идти|входить|находиться|делать|трогать)\b', re.IGNORECASE),
    re.compile(r'\b(?:тайник|клад|сокровищ\w+)\b.{1,60}?\b(?:спрятан\w*|находится|лежит|скрыт\w*)\b', re.IGNORECASE),
    re.compile(r'\b(?:спрятан\w*|находится|лежит|скрыт\w*)\b.{1,60}?\b(?:тайник|клад|сокровищ\w+)\b', re.IGNORECASE),
    re.compile(r'\bнаша\s+цель\b', re.IGNORECASE),
]

RECALL_TRIGGERS = (
    "помнишь", "не помнишь", "ты помнишь", "вспомни", "вспоминаешь",
    "в начале", "в начале нашего", "в тот раз", "тогда, когда", "когда мы",
    "как говорил", "как говорила", "что говорил", "что говорила",
    "что рассказывал", "что рассказывала", "предупреждал", "предупреждала",
    "мы же договаривались", "ты мне рассказывал", "ты мне рассказывала"
)

STOP_WORDS_RECALL = {
    "что", "как", "про", "об", "обо", "мы", "ты", "вы", "он", "она", "они",
    "мне", "тебе", "нас", "вас", "это", "этот", "эту", "этом", "когда", "где",
    "куда", "откуда", "почему", "зачем", "было", "были", "был", "была", "там",
    "тут", "здесь", "ли", "же", "то", "или", "если", "помнишь", "вспомни",
    "начале", "тот", "раз", "тогда", "говорил", "говорила", "сказал", "сказала",
    "слушай", "кстати", "скажи"
}


class ContextWindowBuilder:
    """Сборщик системных промптов и контекстного окна LLM."""

    @staticmethod
    def _estimate_message_tokens(m: Dict[str, Any]) -> int:
        return TokenBudgetManager.estimate_message_tokens(m)

    @staticmethod
    def extract_world_facts_from_history(history: List[Dict[str, Any]]) -> List[str]:
        facts: List[str] = []
        for m in history:
            raw_c = m.get("content") if m.get("content") is not None else m.get("text", "")
            text = str(raw_c or "")
            if text.startswith("[Системный") or re.match(r'^\[IMAGE[\s_-]PROMPT:', text, re.IGNORECASE):
                continue
            sentences = re.split(r'[.!?\n]+', text)
            for s in sentences:
                s_clean = s.strip().strip('"\'*-–- ')
                if 15 <= len(s_clean) <= 180:
                    for pat in LORE_TRIGGER_PATTERNS:
                        if pat.search(s_clean):
                            s_lower = s_clean.lower()
                            if not any(s_lower in f.lower() or f.lower() in s_lower for f in facts):
                                facts.append(s_clean)
                            break
        return facts[-7:]

    @staticmethod
    def get_dynamic_art_models_protocol() -> str:
        """
        Формирует динамический список активных моделей из data/art_director_models.json.
        Включает только модели с active=True и непустым описанием,
        чтобы пользователь мог прозрачно управлять ими через интерфейс (Ctrl+Shift+M).
        """
        try:
            from services.model_router import ModelRouter
            router = ModelRouter()
            active = [
                m for m in router.profiles
                if m.get("active", True) and (m.get("routing_description") or m.get("description") or "").strip()
            ]
            if not active:
                return "   - cyberrealistic: фотореализм, студия, косплей\n   - realistic: повседневная жизнь"
            lines = []
            for m in active:
                m_id = str(m.get("id", "")).strip()
                desc = (m.get("routing_description") or m.get("description") or m.get("name") or "").strip()
                short_desc = (desc[:75].rsplit(' ', 1)[0] + "...") if len(desc) > 75 else desc
                lines.append(f"   - {m_id}: {short_desc}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"[ContextWindowBuilder] Ошибка чтения моделей для промпта: {e}")
            return "   - cyberrealistic: фотореализм, студия, косплей\n   - realistic: повседневная жизнь\n   - artistic: фэнтези, 3D\n   - anime: 2D аниме"

    @staticmethod
    def build_system_prompt(
        is_roleplay: bool = False,
        character_card: Optional[CharacterCard] = None,
        is_excel: bool = False,
        is_word: bool = False,
        user_name: str = "User",
        include_memory: bool = True,
        session_persona_id: Optional[str] = None,
        is_engineer: bool = False,
        relevant_facts: Optional[List[str]] = None,
        world_facts: Optional[List[str]] = None,
        scene_memory: Optional[str] = None,
        has_vision: bool = False,
        art_guidance: Optional[str] = None,
        active_cast_block: Optional[str] = None,
        recalled_lore_block: Optional[str] = None,
        meta_context_block: Optional[str] = None,
        session_id: Optional[str] = None,
        is_dm_mode: bool = False,
        **kwargs
    ) -> str:
        # Single Type normalization
        if isinstance(character_card, dict):
            character_card = CharacterCard.from_dict(character_card)

        # -------------------------------------------------------------
        # ДОМЕН 1: РОЛЕВОЙ ЧАТ (Делегирование в RoleplayPromptBuilder)
        # -------------------------------------------------------------
        if is_roleplay:
            active_persona = UserProfileManager.get_active_user_persona(
                is_roleplay=True,
                session_persona_id=session_persona_id
            )
            if user_name and user_name not in ("User", "Пользователь", "", None):
                effective_user_name = user_name
            else:
                effective_user_name = active_persona.get("name") or "Игрок"

            if not active_cast_block and session_id:
                cast_mgr = RPCastManager
                if hasattr(cast_mgr, "get_cast_prompt_block"):
                    active_cast_block = cast_mgr.get_cast_prompt_block(session_id)
                else:
                    try:
                        from services.session_manager import SessionManager
                        sess = SessionManager._sessions_cache.get(str(session_id)) or SessionManager.load_session(str(session_id))
                        if sess and "cast_data" in sess and hasattr(RPCastManager, "format_cast_data_block"):
                            active_cast_block = RPCastManager.format_cast_data_block(sess["cast_data"])
                    except Exception:
                        pass

            return RoleplayPromptBuilder.build(
                character_card=character_card,
                user_name=effective_user_name,
                active_persona=active_persona,
                is_dm_mode=is_dm_mode,
                world_facts=world_facts,
                scene_memory=scene_memory,
                active_cast_block=active_cast_block or "",
                recalled_lore_block=recalled_lore_block or "",
                meta_context_block=meta_context_block or ""
            )

        # -------------------------------------------------------------
        # ДОМЕН 2: ИНЖЕНЕР (Изолированная песочница)
        # -------------------------------------------------------------
        if is_engineer:
            logger.info("[SysPrompt.Origin] Режим: engineer | Источник: Изолированная песочница Workspace Subagent")
            task_text = kwargs.get("task_prompt", "") or kwargs.get("task_text", "") or kwargs.get("user_message", "") or ""
            return get_engineer_system_prompt(task_text=task_text, is_analysis=kwargs.get("is_analysis", False))

        # -------------------------------------------------------------
        # ДОМЕН 3: АССИСТЕНТ (Элария + LanceDB)
        # -------------------------------------------------------------
        logger.info("[SysPrompt.Origin] Режим: assistant | Источник: Базовая персона Эларии + LanceDB Memory")

        active_persona = UserProfileManager.get_active_user_persona(
            is_roleplay=False,
            session_persona_id=session_persona_id
        )
        effective_user_name = active_persona.get("name") if user_name in ("User", "Пользователь", "", None) else user_name

        return AssistantPromptBuilder.build(
            user_name=effective_user_name or "Пользователь",
            active_persona=active_persona,
            include_memory=include_memory,
            relevant_facts=relevant_facts,
            is_excel=is_excel,
            is_word=is_word
        )

    @staticmethod
    def get_lm_studio_messages(
        history: List[Dict[str, Any]],
        is_roleplay: bool = False,
        character_card: Optional[CharacterCard] = None,
        is_excel: bool = False,
        is_word: bool = False,
        vision_content: Optional[List[Dict[str, Any]]] = None,
        history_limit: int = 35,
        user_name: str = "User",
        include_memory: bool = True,
        session_persona_id: Optional[str] = None,
        is_engineer: bool = False,
        world_facts: Optional[List[str]] = None,
        art_guidance: Optional[str] = None,
        active_cast_block: Optional[str] = None,
        recalled_lore_block: Optional[str] = None,
        meta_context_block: Optional[str] = None,
        session_id: Optional[str] = None,
        is_dm_mode: bool = False,
        max_context_tokens: Optional[int] = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        normalized_history: List[Dict[str, Any]] = []
        for idx, raw_m in enumerate(history):
            m = dict(raw_m)
            role = m.get("role") or ("user" if m.get("is_user") else "assistant")
            content = m.get("content") if m.get("content") is not None else m.get("text", "")
            text = content

            if isinstance(text, list):
                if idx != len(history) - 1 or vision_content is None:
                    txt_parts = [str(p.get("text", "")) for p in text if isinstance(p, dict) and p.get("type") == "text"]
                    content_val = " ".join(tp for tp in txt_parts if tp).strip()
                else:
                    content_val = text
            else:
                content_val = str(text or "").strip()
                if role == "assistant" and "🔔" in content_val:
                    content_val = re.sub(r'(?:\n|\s)*🔔\s*Напоминаю:[^\n]*', '', content_val).strip()

            if isinstance(content_val, str) and not content_val and role == "assistant":
                continue

            normalized_history.append({"role": role, "content": content_val})

        history = normalized_history

        relevant_facts = []
        last_query = ""
        for m in reversed(history):
            if m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, str):
                    clean_c = re.sub(r'\[IMAGE[\s_-]PROMPT:[^\]]+\]', '', c, flags=re.IGNORECASE).strip()
                    if clean_c.startswith("[Системный") or clean_c.startswith("[Tool"):
                        break
                    if clean_c.lower().startswith(("нарисуй", "покажи", "сгенерируй", "!art", "!арт")):
                        last_query = ""
                        break
                    last_query = clean_c
                    break
                elif isinstance(c, list):
                    text_parts = [str(p.get("text", "")) for p in c if isinstance(p, dict) and p.get("type") == "text"]
                    c_text = " ".join(tp.strip() for tp in text_parts if tp.strip())
                    if c_text.startswith("[Системный") or c_text.startswith("[Tool"):
                        break
                    if c_text.lower().startswith(("нарисуй", "покажи", "сгенерируй", "!art", "!арт")):
                        last_query = ""
                        break
                    if c_text:
                        last_query = c_text
                        break

        # Для ассистента: запрашиваем top_k=5 (строго блокируем для roleplay и engineer)
        if not is_roleplay and not is_engineer and include_memory and last_query:
            try:
                relevant_facts = memory_db.search_relevant_facts(last_query, top_k=5)
            except Exception:
                pass

        deep_history: List[Dict[str, Any]] = []
        recent_history: List[Dict[str, Any]] = []
        scene_memory = ""
        effective_world_facts = world_facts

        # -------------------------------------------------------------
        # ДОМЕН 1: РОЛЕВОЙ РЕЖИМ (Динамический бюджет и first_mes Pin)
        # -------------------------------------------------------------
        if is_roleplay:
            active_p = UserProfileManager.get_active_user_persona(is_roleplay=True, session_persona_id=session_persona_id)
            if user_name and user_name not in ("User", "Пользователь", "", None):
                effective_user_name = user_name
            else:
                effective_user_name = active_p.get("name") or "Игрок"
            companion_name = getattr(character_card, "name", "Спутница") if character_card else "Спутница"

            if character_card:
                # Гарантия сохранения Root Anchor (first_mes карточки) в начале истории
                has_assistant_first = bool(history and history[0].get("role") == "assistant")
                if not has_assistant_first:
                    pool = character_card.get_greetings_pool() if hasattr(character_card, "get_greetings_pool") else []
                    raw_greet = pool[0]["effective"] if pool else (character_card.first_mes_ru or character_card.first_mes or "")
                    if raw_greet and raw_greet.strip():
                        processed_greet = MacroEngine.process(raw_greet.strip(), user_name=effective_user_name, char_name=companion_name)
                        history = [{"role": "assistant", "content": processed_greet}] + list(history)

            # 1. Замена макросов {{user}} и {{char}} во всех репликах истории ДО расчета токенов
            processed_history: List[Dict[str, Any]] = []
            for m in history:
                m_copy = dict(m)
                c = m_copy.get("content")
                if isinstance(c, str):
                    m_copy["content"] = MacroEngine.process(c, user_name=effective_user_name, char_name=companion_name)
                processed_history.append(m_copy)
            history = processed_history

            regex_world_facts = ContextWindowBuilder.extract_world_facts_from_history(history)
            if not effective_world_facts:
                effective_world_facts = regex_world_facts
            else:
                for rf in regex_world_facts:
                    if not any(rf.lower() in ef.lower() for ef in effective_world_facts):
                        effective_world_facts = list(effective_world_facts) + [rf]

            # Семантический поиск LanceDB централизован внутри RPCastManager.scan_and_tick (исключает дублирование)
            scene_memory = ""

            # 2. Предотвращение двойного тика: scan_and_tick строго если контроллер не передал готовые блоки
            if session_id and active_cast_block is None:
                is_env = getattr(character_card, "is_environment", False) if character_card else False

                last_raw = history[-1].get("content", "") if history else ""
                if isinstance(last_raw, list):
                    last_text = " ".join(p.get("text", "") for p in last_raw if isinstance(p, dict) and p.get("type") == "text")
                else:
                    last_text = str(last_raw or "")

                scan_blocks = RPCastManager.scan_and_tick(
                    session_id,
                    last_text,
                    user_name=effective_user_name,
                    companion_name=companion_name,
                    is_environment=is_env
                )
                active_cast_block = scan_blocks.get("active_cast", "")
                recalled_lore_block = scan_blocks.get("recalled_lore", "")
                meta_context_block = scan_blocks.get("meta_context", "")

            # 3. Защитная нормализация блоков сцены
            active_cast_block = active_cast_block or ""
            recalled_lore_block = recalled_lore_block or ""
            meta_context_block = meta_context_block or ""

            # Получение актуального контекста спутников и инвентаря для ролевого системного промпта
            cast_context = ""
            cast_mgr = RPCastManager
            session = None
            if session_id:
                try:
                    from services.session_manager import SessionManager
                    session = SessionManager._sessions_cache.get(str(session_id)) or SessionManager.load_session(str(session_id))
                except Exception:
                    session = None

            if cast_mgr and hasattr(cast_mgr, "get_cast_prompt_block") and session_id:
                cast_context = cast_mgr.get_cast_prompt_block(session_id)
            elif session and "cast_data" in session:
                if hasattr(RPCastManager, "format_cast_data_block"):
                    cast_context = RPCastManager.format_cast_data_block(session["cast_data"])
                elif isinstance(session["cast_data"], dict):
                    cast_lines = []
                    if session["cast_data"].get("inventory"):
                        cast_lines.append("Инвентарь: " + ", ".join(session["cast_data"]["inventory"]))
                    cast_context = "\n".join(cast_lines)

            if cast_context:
                if active_cast_block and active_cast_block.strip() and active_cast_block.strip() not in cast_context:
                    active_cast_block = f"{cast_context.strip()}\n\n{active_cast_block.strip()}"
                else:
                    active_cast_block = cast_context.strip()

            # 4. Якорь 1 (Системный промпт): messages[0] (личность, сценарий, правила, mes_example) — никогда не удаляется
            system_prompt = ContextWindowBuilder.build_system_prompt(
                is_roleplay=True,
                character_card=character_card,
                is_excel=False,
                is_word=False,
                user_name=effective_user_name,
                include_memory=include_memory,
                session_persona_id=session_persona_id,
                is_engineer=False,
                relevant_facts=[],
                world_facts=effective_world_facts,
                scene_memory=scene_memory,
                has_vision=bool(vision_content),
                art_guidance=art_guidance,
                active_cast_block=active_cast_block,
                recalled_lore_block=recalled_lore_block,
                meta_context_block=meta_context_block,
                session_id=session_id,
                is_dm_mode=is_dm_mode,
                task_text=last_query
            )
            safe_user_name = str(effective_user_name).strip() if effective_user_name else "пользователь"
            system_content = system_prompt.replace("{user_name}", safe_user_name)
            system_message = {"role": "system", "content": system_content}

            # 5. Определение бюджета токенов max_context_tokens (24K Budget)
            rp_profile = get_mode_profile("roleplay")
            target_ctx_len = int(rp_profile.get("context_length", 24576))
            # Резерв на генерацию ответа модели (обычно 1024–2048 токенов, до 4096)
            configured_max_resp = int(rp_profile.get("sampling", {}).get("max_tokens", 4096))
            generation_reserve = max(1024, min(configured_max_resp, MASTER_MAX_RESPONSE_TOKENS))
            SCENE_INJECTION_RESERVE = 1200
            SAFETY_MARGIN = 128

            max_allowed_context = max(4000, target_ctx_len - generation_reserve - SCENE_INJECTION_RESERVE - SAFETY_MARGIN)

            effective_max_tokens = max_context_tokens if max_context_tokens is not None else kwargs.get("max_context_tokens")
            if effective_max_tokens is None:
                effective_max_tokens = max_allowed_context
            else:
                effective_max_tokens = int(effective_max_tokens)
                # Если передан сырой общий лимит контекста, гарантируем вычет резерва на генерацию
                if effective_max_tokens >= target_ctx_len - generation_reserve:
                    effective_max_tokens = max_allowed_context

            # 6. Архитектура «Трёх якорей» и логика вытеснения из середины (Eviction Zone)
            deep_history = []

            if not history:
                recent_history = []
            elif len(history) <= 14:
                # Если общее количество сообщений в истории <= 14: вытеснение не требуется, возвращаем всю историю целиком
                first_message = history[0]
                recent_tail = list(history[1:])

                total_tokens = (
                    TokenBudgetManager.estimate_message_tokens(system_message)
                    + TokenBudgetManager.estimate_message_tokens(first_message)
                    + sum(TokenBudgetManager.estimate_message_tokens(m) for m in recent_tail)
                )

                if total_tokens > effective_max_tokens:
                    logger.warning(
                        f"[RoleplayEviction] Сообщения истории превышают лимит токенов ({total_tokens} > {effective_max_tokens}) "
                        f"при длине истории {len(history)} <= 14."
                    )

                recent_history = [first_message] + recent_tail
            else:
                # Якорь 2 (Завязка сюжета): Первое сообщение диалога first_message = history[0] (канонический first_mes) — никогда не удаляется
                first_message = history[0]
                # Зона вытеснения: средняя часть диалога
                middle_messages = list(history[1:-12])
                # Якорь 3 (Оперативный контекст): Хвост из последних 12 сообщений recent_tail = history[-12:] — всегда сохраняется для связности диалога
                recent_tail = list(history[-12:])

                total_tokens = (
                    TokenBudgetManager.estimate_message_tokens(system_message)
                    + TokenBudgetManager.estimate_message_tokens(first_message)
                    + sum(TokenBudgetManager.estimate_message_tokens(m) for m in middle_messages)
                    + sum(TokenBudgetManager.estimate_message_tokens(m) for m in recent_tail)
                )

                # В цикле while удаляем по одному самому старому сообщению из начала middle_messages,
                # пока суммарный объем токенов (system_prompt + first_message + middle_messages + recent_tail)
                # не уложится в заданный бюджет max_context_tokens
                while total_tokens > effective_max_tokens and middle_messages:
                    evicted = middle_messages.pop(0)
                    deep_history.append(evicted)
                    total_tokens -= TokenBudgetManager.estimate_message_tokens(evicted)

                if total_tokens > effective_max_tokens:
                    logger.warning(
                        f"[RoleplayEviction] Суммарный объем токенов ({total_tokens}) превышает бюджет "
                        f"max_context_tokens ({effective_max_tokens}) даже после полной очистки зоны вытеснения (middle_messages)."
                    )

                # Итоговый список собирается строго в хронологическом порядке:
                # [system_prompt, first_message] + middle_messages + recent_tail
                recent_history = [first_message] + middle_messages + recent_tail

            # 7. Фолбэк-поиск в глубокой истории (если векторная память LanceDB была пуста)
            if not scene_memory and deep_history and last_query:
                lq_lower = last_query.lower()
                if any(t in lq_lower for t in RECALL_TRIGGERS):
                    query_tokens = [
                        w for w in re.findall(r'\b[a-zA-Zа-яА-ЯёЁ]{3,}\b', lq_lower)
                        if w not in STOP_WORDS_RECALL
                    ]
                    if query_tokens:
                        best_match_text = ""
                        max_overlap = 0
                        for past_m in deep_history:
                            p_content = str(past_m.get("content", "")).strip()
                            if not p_content or p_content.startswith("["):
                                continue
                            p_lower = p_content.lower()
                            overlap = sum(1 for tok in query_tokens if tok in p_lower)
                            if overlap > max_overlap:
                                max_overlap = overlap
                                best_match_text = p_content

                        if max_overlap >= 1 and best_match_text:
                            scene_memory = (best_match_text[:250].strip() + "...") if len(best_match_text) > 250 else best_match_text
                            # Перекомпиляция системного промпта с найденным воспоминанием
                            system_prompt = ContextWindowBuilder.build_system_prompt(
                                is_roleplay=True,
                                character_card=character_card,
                                is_excel=False,
                                is_word=False,
                                user_name=effective_user_name,
                                include_memory=include_memory,
                                session_persona_id=session_persona_id,
                                is_engineer=False,
                                relevant_facts=[],
                                world_facts=effective_world_facts,
                                scene_memory=scene_memory,
                                has_vision=bool(vision_content),
                                art_guidance=art_guidance,
                                active_cast_block=active_cast_block,
                                recalled_lore_block=recalled_lore_block,
                                meta_context_block=meta_context_block,
                                session_id=session_id,
                                is_dm_mode=is_dm_mode,
                                task_text=last_query
                            )
                            system_content = system_prompt.replace("{user_name}", safe_user_name)
                            system_message = {"role": "system", "content": system_content}

            messages: List[Dict[str, Any]] = [system_message]

        # -------------------------------------------------------------
        # ДОМЕН 2 И 3: ИНЖЕНЕР И АССИСТЕНТ (Изолированная логика без изменений)
        # -------------------------------------------------------------
        else:
            active_mode_key = "engineer" if is_engineer else "assistant"
            target_history_tokens = get_mode_profile(active_mode_key).get("history_tokens", 11000)
            if len(history) <= 4:
                recent_history = [dict(m) for m in history]
            elif is_engineer:
                # Якорь 1 (Root Anchor): Первые 2 сообщения сессии (старт диалога / исходная задача) строго для engineer
                root_anchor = [dict(history[0]), dict(history[1])]
                root_tok = sum(TokenBudgetManager.estimate_message_tokens(m) for m in root_anchor)

                min_budget = 4000
                remaining_budget = max(min_budget, target_history_tokens - root_tok)
                tail_reversed = []
                tail_tok = 0

                for m in reversed(history[2:]):
                    m_tok = TokenBudgetManager.estimate_message_tokens(m)
                    if tail_tok + m_tok <= remaining_budget:
                        tail_reversed.append(dict(m))
                        tail_tok += m_tok
                    else:
                        break

                tail_reversed.reverse()
                recent_history = root_anchor + tail_reversed
            else:
                # Для assistant: чистое скользящее окно по токенному бюджету tail_reversed без root_anchor
                remaining_budget = max(2400, target_history_tokens)
                tail_reversed = []
                tail_tok = 0

                for m in reversed(history):
                    m_tok = TokenBudgetManager.estimate_message_tokens(m)
                    if tail_tok + m_tok <= remaining_budget:
                        tail_reversed.append(dict(m))
                        tail_tok += m_tok
                    else:
                        break

                tail_reversed.reverse()
                recent_history = tail_reversed

            system_prompt = ContextWindowBuilder.build_system_prompt(
                is_roleplay=False,
                character_card=character_card,
                is_excel=is_excel,
                is_word=is_word,
                user_name=user_name,
                include_memory=include_memory,
                session_persona_id=session_persona_id,
                is_engineer=is_engineer,
                relevant_facts=relevant_facts,
                world_facts=effective_world_facts,
                scene_memory=scene_memory,
                has_vision=bool(vision_content),
                art_guidance=art_guidance,
                active_cast_block=active_cast_block,
                recalled_lore_block=recalled_lore_block,
                meta_context_block=meta_context_block,
                session_id=session_id,
                is_dm_mode=is_dm_mode,
                task_text=last_query
            )
            safe_user_name = str(user_name).strip() if user_name else "пользователь"
            system_content = system_prompt.replace("{user_name}", safe_user_name)

            messages = [{"role": "system", "content": system_content}]

        if not is_roleplay and is_engineer:
            sanitized = []
            total = len(recent_history)
            for i, m in enumerate(recent_history):
                c = m.get('content')
                c_str = str(c) if isinstance(c, str) else ""
                role = m.get('role', '')
                is_last = (i == total - 1)
                if c_str.startswith('[Системный результат') or c_str.startswith('[Tool Observation'):
                    if is_last:
                        sanitized.append(m)
                    continue
                if role == 'assistant' and not is_last:
                    cleaned = re.sub(r'<<TOOL:\w+.*?>>.*?<<?/+TOOL(?::\w+)?>>?', '', c_str, flags=re.DOTALL)
                    cleaned = re.sub(r'<<TOOL:\w+.*?>>', '', cleaned).strip()
                    if cleaned:
                        sanitized.append({'role': 'assistant', 'content': cleaned})
                    continue
                sanitized.append(m)
            recent_history = sanitized

        if vision_content and recent_history:
            for i in range(len(recent_history) - 1, -1, -1):
                if recent_history[i].get("role") == "user":
                    payload_list = []
                    for part in vision_content:
                        p_copy = dict(part)
                        if p_copy.get("type") == "text" and is_roleplay:
                            t = str(p_copy.get("text", ""))
                            if "[Отвечай на русском языке]" not in t:
                                p_copy["text"] = t + "\n\n[Отвечай строго на русском языке. Подробно опиши изображение и его детали.]"
                        payload_list.append(p_copy)
                    recent_history[i] = {"role": "user", "content": payload_list}
                    break

        messages.extend(recent_history)

        # -------------------------------------------------------------
        # IN-CHAT INJECTION (ЭТАЛОН SILLYTAVERN / KV-CACHE FRIENDLY)
        # Динамические блоки сцены подклеиваются строго к последней реплике игрока.
        # messages[0] и история messages[1...N-1] остаются 100% неизменными,
        # что гарантирует сохранение KV-кэша LM Studio на всех 50+ ходах.
        # -------------------------------------------------------------
        if is_roleplay:
            scene_appendices = []
            # Динамические правила мира вынесены из static_system_prompt для 100% заморозки KV-кэша
            if effective_world_facts:
                lines_wf = "\n".join(f"• {wf}" for wf in effective_world_facts[:5])
                scene_appendices.append(f"<WorldState>\nКлючевые обстоятельства и правила мира:\n{lines_wf}\n</WorldState>")
            if recalled_lore_block and recalled_lore_block.strip():
                scene_appendices.append(recalled_lore_block.strip())
            # active_cast_block интегрирован в Anchor 1 (Системный промпт), исключая дублирование в хвосте
            if meta_context_block and meta_context_block.strip():
                scene_appendices.append(meta_context_block.strip())

            if character_card and hasattr(character_card, "get_triggered_lore_entries"):
                recent_window_text = " ".join(
                    str(m.get("content", "")) for m in recent_history[-4:] if isinstance(m.get("content"), str)
                )
                card_lore_hits = character_card.get_triggered_lore_entries(recent_window_text, user_name=user_name)
                if card_lore_hits:
                    scene_appendices.append(
                        "<TriggeredCardLore>\nАктивированные статьи лорбука карточки:\n"
                        + "\n\n".join(card_lore_hits)
                        + "\n</TriggeredCardLore>"
                    )

            # Директива глубины (post_history_instructions)
            depth_text = ""
            if character_card:
                post_hist = getattr(character_card, "post_history_instructions", "") or ""
                if not post_hist:
                    raw_data = getattr(character_card, "raw_data", {}) or {}
                    target_data = raw_data.get("data", raw_data) if isinstance(raw_data, dict) else {}
                    post_hist = target_data.get("post_history_instructions", "") if isinstance(target_data, dict) else ""
                if post_hist and post_hist.strip() and post_hist.strip() != getattr(character_card, "system_prompt", "").strip():
                    depth_text = MacroEngine.process(post_hist.strip(), user_name=user_name, char_name=character_card.name).strip()

            # Авторская заметка (depth_prompt / character_note)
            card_depth_prompt = ""
            if character_card:
                raw_dp = getattr(character_card, "depth_prompt", "") or ""
                if not raw_dp:
                    raw_data = getattr(character_card, "raw_data", {}) or {}
                    target_data = raw_data.get("data", raw_data) if isinstance(raw_data, dict) else {}
                    raw_dp = (
                        target_data.get("depth_prompt")
                        or target_data.get("character_note")
                        or (target_data.get("extensions") or {}).get("depth_prompt", {}).get("prompt", "")
                        or ""
                    ) if isinstance(target_data, dict) else ""
                if raw_dp and str(raw_dp).strip():
                    card_depth_prompt = MacroEngine.process(str(raw_dp).strip(), user_name=user_name, char_name=character_card.name).strip()

            if scene_appendices or card_depth_prompt or depth_text or character_card:
                combined_injection = []
                if scene_appendices:
                    combined_injection.append("[Обстановка сцены и действующие лица:\n" + "\n\n".join(scene_appendices) + "]")

                if card_depth_prompt:
                    combined_injection.append(f"[Ключевая заметка автора: {card_depth_prompt}]")

                # Адаптивный ролевой якорь с языковым замком для отсечения английского курсива
                is_grp = getattr(character_card, "is_group", False) if character_card else False
                is_env = getattr(character_card, "is_environment", False) if character_card else False
                char_n = getattr(character_card, "name", "") if character_card else ""

                if depth_text:
                    latin_cnt = sum(1 for c in depth_text if 'a' <= c.lower() <= 'z')
                    cyr_cnt = sum(1 for c in depth_text if 'а' <= c.lower() <= 'я')
                    if latin_cnt > cyr_cnt and latin_cnt > 15:
                        combined_injection.append(f"[Стиль повествования (строго на русском языке): {depth_text}]")
                    else:
                        combined_injection.append(f"[Стиль повествования: {depth_text}]")
                else:
                    if is_grp:
                        combined_injection.append("[Динамика сцены: диалог и взаимодействие участников (все реплики и описания строго на русском языке)]")
                    elif is_env:
                        combined_injection.append("[Отклик окружения и реакция мира (строго на русском языке)]")
                    elif char_n and char_n.lower() not in ("персонаж", "assistant", "unknown"):
                        combined_injection.append(
                            f"[Держи канонический характер, стиль речи и манеру повествования (лицо, разметку действий и диалогов), "
                            f"заданные в завязке и анкете {char_n}. Не выходи из образа и не пиши за {user_name}. "
                            f"Язык ответа — строго литературный русский.]"
                        )

                if combined_injection:
                    injection_str = "\n\n" + "\n\n".join(combined_injection)

                    for idx in range(len(messages) - 1, -1, -1):
                        if messages[idx].get("role") == "user":
                            u_content = messages[idx].get("content")
                            if isinstance(u_content, str):
                                messages[idx]["content"] = u_content + injection_str
                            elif isinstance(u_content, list):
                                for part in u_content:
                                    if isinstance(part, dict) and part.get("type") == "text":
                                        part["text"] = str(part.get("text", "")) + injection_str
                                        break
                            break

        return messages


__all__ = [
    "LORE_TRIGGER_PATTERNS",
    "RECALL_TRIGGERS",
    "STOP_WORDS_RECALL",
    "ContextWindowBuilder"
]
