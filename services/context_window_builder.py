"""
Context Window Builder for NeonRoute.
Handles prompt assembly, token budgeting, KV-cache-friendly history trimming,
and context injection for Assistant, Roleplay, and Engineer modes.
"""

import re
import json
import hashlib
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
from services.regex_filter import TextRegexFilter

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
            if text.startswith("[Системный") or re.match(rf'^\[{TextRegexFilter.IMAGE_TAG_LABEL_PATTERN}:', text, re.IGNORECASE):
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
                return "   - (локальные модели генерации не настроены; используйте автоматический выбор)"
            lines = []
            for m in active:
                m_id = str(m.get("id", "")).strip()
                desc = (m.get("routing_description") or m.get("description") or m.get("name") or "").strip()
                short_desc = (desc[:75].rsplit(' ', 1)[0] + "...") if len(desc) > 75 else desc
                lines.append(f"   - {m_id}: {short_desc}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"[ContextWindowBuilder] Ошибка чтения моделей для промпта: {e}")
            return "   - (локальные модели генерации не настроены; используйте автоматический выбор)"

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
        enable_reasoning: bool = False,
        custom_system_prompt: Optional[str] = None,
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

            if not custom_system_prompt:
                custom_system_prompt = kwargs.get("custom_system_prompt")
            if not custom_system_prompt and session_id:
                try:
                    from services.session_manager import SessionManager
                    sess = SessionManager._sessions_cache.get(str(session_id)) or SessionManager.load_session(str(session_id))
                    if sess:
                        custom_system_prompt = sess.get("custom_system_prompt")
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
                recalled_lore_block="",
                meta_context_block=meta_context_block or "",
                custom_system_prompt=custom_system_prompt,
                include_dynamic_lore=False
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

        if not custom_system_prompt:
            custom_system_prompt = kwargs.get("custom_system_prompt")
        if not custom_system_prompt and session_id:
            try:
                from services.session_manager import SessionManager
                sess = SessionManager._sessions_cache.get(str(session_id)) or SessionManager.load_session(str(session_id))
                if sess:
                    custom_system_prompt = sess.get("custom_system_prompt")
            except Exception:
                pass

        active_persona = UserProfileManager.get_active_user_persona(
            is_roleplay=False,
            session_persona_id=session_persona_id
        )
        effective_user_name = active_persona.get("name") if user_name in ("User", "Пользователь", "", None) else user_name

        effective_query = (
            kwargs.get("user_query")
            or kwargs.get("task_text")
            or kwargs.get("user_message_text")
            or kwargs.get("task_prompt")
            or kwargs.get("user_message")
            or ""
        )

        return AssistantPromptBuilder.build(
            user_name=effective_user_name or "Пользователь",
            active_persona=active_persona,
            include_memory=include_memory,
            relevant_facts=relevant_facts,
            is_excel=is_excel,
            is_word=is_word,
            knowledge_block=kwargs.get("knowledge_block") or kwargs.get("knowledge_context"),
            matched_knowledge_docs=kwargs.get("matched_knowledge_docs"),
            enable_reasoning=enable_reasoning or kwargs.get("enable_reasoning", False),
            user_query=effective_query,
            session_id=session_id,
            session_metadata=kwargs.get("session_metadata"),
            active_assistant_skill=kwargs.get("active_assistant_skill"),
            selected_forge_checkpoint=kwargs.get("selected_forge_checkpoint"),
            custom_prompt=custom_system_prompt
        )

    @staticmethod
    def _merge_message_content(prev_content: Any, next_content: Any) -> Any:
        """Безопасно объединяет содержимое двух соседних сообщений с одинаковой ролью (str/list)."""
        if isinstance(prev_content, list) and isinstance(next_content, list):
            return [dict(p) if isinstance(p, dict) else p for p in prev_content] + [
                dict(p) if isinstance(p, dict) else p for p in next_content
            ]
        if isinstance(prev_content, list):
            merged = [dict(p) if isinstance(p, dict) else p for p in prev_content]
            next_str = str(next_content or "").strip()
            if next_str:
                for p in reversed(merged):
                    if isinstance(p, dict) and p.get("type") == "text":
                        base = str(p.get("text") or "").strip()
                        p["text"] = f"{base}\n\n{next_str}" if base else next_str
                        return merged
                merged.append({"type": "text", "text": next_str})
            return merged
        if isinstance(next_content, list):
            merged = [dict(p) if isinstance(p, dict) else p for p in next_content]
            prev_str = str(prev_content or "").strip()
            if prev_str:
                for p in merged:
                    if isinstance(p, dict) and p.get("type") == "text":
                        base = str(p.get("text") or "").strip()
                        p["text"] = f"{prev_str}\n\n{base}" if base else prev_str
                        return merged
                merged.insert(0, {"type": "text", "text": prev_str})
            return merged
        prev_str = str(prev_content or "").strip()
        next_str = str(next_content or "").strip()
        if prev_str and next_str:
            return f"{prev_str}\n\n{next_str}"
        return prev_str or next_str

    @staticmethod
    def normalize_role_alternation(
        history: List[Dict[str, Any]],
        strip_leading_assistant: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Гарантирует корректное чередование ролей (user/assistant) в истории диалога:
        - При strip_leading_assistant=True отбрасывает ведущие реплики assistant
          (например, UI-приветствие в режиме Ассистента или остаток после срезки по токен-бюджету).
        - Объединяет подряд идущие сообщения с одинаковой ролью (включая multimodal list-контент),
          исключая HTTP 400 ошибки шаблона вроде 'Conversation roles must alternate'.
        """
        if not history:
            return []

        normalized: List[Dict[str, Any]] = []
        for raw_m in history:
            if not isinstance(raw_m, dict):
                continue
            m = dict(raw_m)
            role = m.get("role") or ("user" if m.get("is_user") else "assistant")
            m["role"] = role

            if strip_leading_assistant and not normalized and role == "assistant":
                continue

            if normalized and normalized[-1].get("role") == role:
                prev = normalized[-1]
                prev_content = prev.get("content")
                cur_content = m.get("content")
                if role == "assistant" and isinstance(prev_content, str) and isinstance(cur_content, str):
                    if "<think>" in cur_content and "<think>" in prev_content:
                        prev_content = re.sub(r'<think>.*?</think>', '', prev_content, flags=re.DOTALL).strip()
                prev["content"] = ContextWindowBuilder._merge_message_content(prev_content, cur_content)
                if m.get("thought") is not None:
                    prev["thought"] = dict(m["thought"]) if isinstance(m["thought"], dict) else m["thought"]
            else:
                normalized.append(m)

        return normalized

    @staticmethod
    def sanitize_messages_for_strict_template(
        messages: List[Dict[str, Any]],
        prepend_scene_start_for_assistant: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Приводит финальный OpenAI-список messages к виду, совместимому со строгими
        Jinja-шаблонами (Mistral-Nemo, Gemma, Llama-3 и др.):
        - допускает ровно одно опциональное system-сообщение строго на индексе 0;
        - промежуточные system-инъекции (например, RP Depth-1 якорь перед репликой игрока)
          аккуратно вливаются в ближайшее последующее user-сообщение (или в system[0]);
        - при первом сообщении диалога с ролью assistant (first_mes в РП) либо предваряет
          его синтетическим ходом user ('[Начало сцены]') при prepend_scene_start_for_assistant=True,
          либо отбрасывает при False;
        - объединяет подряд идущие сообщения с одинаковой ролью.
        Если входной список уже строго валиден — возвращает его без изменений.
        """
        if not messages:
            return messages

        has_head_sys = isinstance(messages[0], dict) and messages[0].get("role") == "system"
        conv_slice = messages[1:] if has_head_sys else messages

        already_valid = True
        expected_role = "user"
        for m in conv_slice:
            if not isinstance(m, dict):
                already_valid = False
                break
            r = m.get("role")
            if r != expected_role:
                already_valid = False
                break
            expected_role = "assistant" if expected_role == "user" else "user"

        if already_valid:
            return messages

        head_sys = dict(messages[0]) if has_head_sys else None
        conv_no_sys: List[Dict[str, Any]] = []
        pending_sys: List[str] = []

        for raw_m in conv_slice:
            if not isinstance(raw_m, dict):
                continue
            m = dict(raw_m)
            role = m.get("role") or ("user" if m.get("is_user") else "assistant")
            m["role"] = role

            if role == "system":
                sys_txt = str(m.get("content") or "").strip()
                if sys_txt:
                    pending_sys.append(sys_txt)
                continue

            if role == "user" and pending_sys:
                anchor_prefix = "\n\n".join(pending_sys)
                pending_sys.clear()
                m["content"] = ContextWindowBuilder._merge_message_content(anchor_prefix, m.get("content"))

            conv_no_sys.append(m)

        if pending_sys:
            extra_sys = "\n\n".join(pending_sys)
            if head_sys is not None:
                head_sys["content"] = ContextWindowBuilder._merge_message_content(head_sys.get("content"), extra_sys)
            else:
                head_sys = {"role": "system", "content": extra_sys}

        conv_clean = ContextWindowBuilder.normalize_role_alternation(
            conv_no_sys,
            strip_leading_assistant=not prepend_scene_start_for_assistant,
        )

        if prepend_scene_start_for_assistant and conv_clean and conv_clean[0].get("role") == "assistant":
            conv_clean = [{"role": "user", "content": "[Начало сцены]"}] + conv_clean

        return ([head_sys] if head_sys is not None else []) + conv_clean

    @staticmethod
    def prepare_history_for_llm(
        history: List[Dict[str, Any]],
        vision_content: Optional[List[Dict[str, Any]]] = None,
        enable_reasoning: bool = False,
        keep_last_cot: bool = True,
        mode: Optional[str] = None,
        is_engineer: bool = False,
        is_roleplay: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Нормализует историю диалога для инференса LLM:
        - Преобразует сообщения в OpenAI-совместимый список [{"role": ..., "content": ...}]
        - Реализует скользящее сохранение CoT (Sliding CoT Preservation) СТРОГО в режиме engineer:
          при effective_mode == "engineer", enable_reasoning=True и keep_last_cot=True
          сохраняет ровно одно последнее рассуждение <think> ассистента.
        - В режимах assistant и roleplay всегда вырезает <think>...</think>, <thought>...</thought>,
          <reasoning>...</reasoning>, экономя контекстное окно 14K/24K.
        - Исключает словарь 'thought' и метаданные размышлений, не подмешивая их в текст модели, кроме целевого CoT инженера.
        - Jinja Alternation Guard: если реплика ассистента состояла только из мыслей, после вырезания подставляет '*(Анализ завершен)*'.
        """
        if not history:
            return []

        effective_mode = (mode or "").strip().lower() or ("engineer" if is_engineer else ("roleplay" if is_roleplay else "assistant"))

        # Поиск целевого индекса сообщения ассистента для сохранения CoT (строго для engineer)
        target_cot_idx = None
        if effective_mode == "engineer" and enable_reasoning and keep_last_cot:
            for idx in range(len(history) - 1, -1, -1):
                raw_m = history[idx]
                role = raw_m.get("role") or ("user" if raw_m.get("is_user") else "assistant")
                if role == "assistant":
                    th = raw_m.get("thought")
                    th_content = ""
                    if isinstance(th, dict):
                        th_content = th.get("content", "")
                    elif isinstance(th, str):
                        th_content = th

                    raw_content = raw_m.get("content") if raw_m.get("content") is not None else raw_m.get("text", "")
                    raw_text_str = ""
                    if isinstance(raw_content, str):
                        raw_text_str = raw_content
                    elif isinstance(raw_content, list):
                        raw_text_str = " ".join(str(p.get("text", "")) for p in raw_content if isinstance(p, dict) and p.get("type") == "text")

                    has_thought = bool(th_content and str(th_content).strip())
                    has_think_tag = bool(re.search(r'<(think|thought|reasoning|analyze)>', raw_text_str, re.IGNORECASE))
                    if has_thought or has_think_tag:
                        target_cot_idx = idx
                        break

        normalized_history: List[Dict[str, Any]] = []
        for idx, raw_m in enumerate(history):
            m = dict(raw_m)
            role = m.get("role") or ("user" if raw_m.get("is_user") else "assistant")
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

            had_thought = False
            if role == "assistant":
                if idx == target_cot_idx:
                    # Целевой последний CoT ассистента строго в режиме engineer
                    th = m.get("thought")
                    th_content = ""
                    if isinstance(th, dict):
                        th_content = th.get("content", "")
                    elif isinstance(th, str):
                        th_content = th
                    thought_content = str(th_content or "").strip()

                    if isinstance(content_val, str):
                        if "<think>" in content_val:
                            # Тег уже есть в тексте — сохраняем его
                            if "🔔" in content_val:
                                content_val = re.sub(r'(?:\n|\s)*🔔\s*Напоминаю:[^\n]*', '', content_val).strip()
                        else:
                            # Тег был вырезан или отсутствовал в text, но есть thought_content — восстанавливаем
                            clean_text = content_val
                            if "🔔" in clean_text:
                                clean_text = re.sub(r'(?:\n|\s)*🔔\s*Напоминаю:[^\n]*', '', clean_text).strip()
                            if thought_content:
                                if clean_text:
                                    content_val = f"<think>\n{thought_content}\n</think>\n\n{clean_text}"
                                else:
                                    content_val = f"<think>\n{thought_content}\n</think>"
                            else:
                                content_val = clean_text
                    elif isinstance(content_val, list):
                        has_think_in_list = any("<think>" in str(p.get("text", "")) for p in content_val if isinstance(p, dict) and p.get("type") == "text")
                        if not has_think_in_list and thought_content:
                            content_val = [{"type": "text", "text": f"<think>\n{thought_content}\n</think>\n\n"}] + list(content_val)
                else:
                    # Все остальные сообщения ассистента (и все сообщения в assistant/roleplay):
                    # Безусловно вырезаем <think>.*?</think>, <thought>.*?</thought>, <reasoning>.*?</reasoning>
                    th = m.get("thought")
                    if th and ((isinstance(th, dict) and th.get("content")) or (isinstance(th, str) and th.strip())):
                        had_thought = True

                    if isinstance(content_val, str):
                        if re.search(r'</?(think|analyze|thought|reasoning)>', content_val, re.IGNORECASE):
                            had_thought = True
                        content_val = re.sub(r'<(think|analyze|thought|reasoning)>[\s\S]*?(?:</\1>|$)', '', content_val, flags=re.IGNORECASE)
                        content_val = re.sub(r'^[\s\S]*?</(think|analyze|thought|reasoning)>', '', content_val, flags=re.IGNORECASE)
                        content_val = re.sub(r'</?(?:think|analyze|thought|reasoning)>', '', content_val, flags=re.IGNORECASE).strip()
                        if "🔔" in content_val:
                            content_val = re.sub(r'(?:\n|\s)*🔔\s*Напоминаю:[^\n]*', '', content_val).strip()
                    elif isinstance(content_val, list):
                        clean_parts = []
                        for part in content_val:
                            if isinstance(part, dict) and part.get("type") == "text":
                                p_txt = str(part.get("text", ""))
                                if re.search(r'</?(think|analyze|thought|reasoning)>', p_txt, re.IGNORECASE):
                                    had_thought = True
                                p_clean = re.sub(r'<(think|analyze|thought|reasoning)>[\s\S]*?(?:</\1>|$)', '', p_txt, flags=re.IGNORECASE)
                                p_clean = re.sub(r'^[\s\S]*?</(think|analyze|thought|reasoning)>', '', p_clean, flags=re.IGNORECASE)
                                p_clean = re.sub(r'</?(?:think|analyze|thought|reasoning)>', '', p_clean, flags=re.IGNORECASE).strip()
                                if p_clean:
                                    part_copy = dict(part)
                                    part_copy["text"] = p_clean
                                    clean_parts.append(part_copy)
                            else:
                                clean_parts.append(part)
                        content_val = clean_parts

            if role == "assistant" and isinstance(content_val, str):
                saved_prompt = m.get("image_prompt")
                if not saved_prompt and isinstance(m.get("generation_params"), dict):
                    saved_prompt = m["generation_params"].get("positive_prompt") or m["generation_params"].get("prompt")
                if saved_prompt and "[IMAGE_PROMPT:" not in content_val:
                    if not content_val or content_val.startswith("Секунду, перевожу образ в кадр"):
                        content_val = f"[IMAGE_PROMPT: {saved_prompt}]"
                    else:
                        content_val = f"{content_val}\n\n[IMAGE_PROMPT: {saved_prompt}]"
                elif not content_val:
                    if m.get("image_path") or m.get("file_paths"):
                        content_val = "[Generated image]"
                    elif had_thought:
                        # Jinja Alternation Guard: сохраняем реплику с безопасным плейсхолдером
                        content_val = "*(Анализ завершен)*"
                    else:
                        continue

            normalized_history.append({"role": role, "content": content_val})

        return normalized_history

    @staticmethod
    def build_context_messages(
        history: List[Dict[str, Any]],
        *args,
        mode: Optional[str] = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Функция сборки сообщений для инференса с санитизацией мыслей.
        Если вызвана только с историей (без аргументов сборки контекста),
        возвращает результат prepare_history_for_llm.
        В остальных случаях собирает полное контекстное окно через get_lm_studio_messages.
        """
        if not args and not kwargs:
            return ContextWindowBuilder.prepare_history_for_llm(history, mode=mode)
        return ContextWindowBuilder.get_lm_studio_messages(history, *args, mode=mode, **kwargs)

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
        enable_reasoning: bool = False,
        mode: Optional[str] = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        effective_mode = (mode or "").strip().lower() or ("engineer" if is_engineer else ("roleplay" if is_roleplay else "assistant"))
        if effective_mode == "roleplay":
            is_roleplay = True
            is_engineer = False
        elif effective_mode == "engineer":
            is_engineer = True
            is_roleplay = False
        else:
            is_roleplay = False
            is_engineer = False

        history = ContextWindowBuilder.prepare_history_for_llm(
            history=history,
            vision_content=vision_content,
            enable_reasoning=enable_reasoning,
            keep_last_cot=(effective_mode == "engineer"),
            mode=effective_mode,
            is_engineer=is_engineer,
            is_roleplay=is_roleplay,
        )

        relevant_facts = kwargs.get("relevant_facts")
        last_query = ""
        for m in reversed(history):
            if m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, str):
                    clean_c = TextRegexFilter.strip_image_tags(c)
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

        # Для ассистента: запрашиваем top_k=5 (если не переданы заранее, строго блокируем для roleplay и engineer)
        if relevant_facts is None and not is_roleplay and not is_engineer and include_memory and last_query:
            try:
                relevant_facts = memory_db.search_relevant_facts(last_query, top_k=5)
            except Exception as e_mem_cwb:
                logger.warning(f"[ContextWindowBuilder] Ошибка поиска памяти: {e_mem_cwb}")
        if relevant_facts is None:
            relevant_facts = []

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
            custom_sys_prompt = kwargs.get("custom_system_prompt")
            if not custom_sys_prompt and session_id:
                try:
                    from services.session_manager import SessionManager
                    sess = SessionManager._sessions_cache.get(str(session_id)) or SessionManager.load_session(str(session_id))
                    if sess:
                        custom_sys_prompt = sess.get("custom_system_prompt")
                except Exception:
                    pass

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
                task_text=last_query,
                user_query=kwargs.get("user_query") or last_query,
                knowledge_block=kwargs.get("knowledge_block") or kwargs.get("knowledge_context"),
                matched_knowledge_docs=kwargs.get("matched_knowledge_docs"),
                enable_reasoning=enable_reasoning or kwargs.get("enable_reasoning", False),
                custom_system_prompt=custom_sys_prompt
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
                task_text=last_query,
                user_query=kwargs.get("user_query") or last_query,
                knowledge_block=kwargs.get("knowledge_block") or kwargs.get("knowledge_context"),
                matched_knowledge_docs=kwargs.get("matched_knowledge_docs"),
                enable_reasoning=enable_reasoning or kwargs.get("enable_reasoning", False),
                selected_forge_checkpoint=kwargs.get("selected_forge_checkpoint"),
                custom_system_prompt=kwargs.get("custom_system_prompt")
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

        recent_history = ContextWindowBuilder.normalize_role_alternation(
            recent_history,
            strip_leading_assistant=not is_roleplay,
        )

        messages.extend(recent_history)

        # ── TurnContext Injection для режима Ассистента ──
        # Прикрепляет служебный блок <TurnContext>...</TurnContext> исключительно к временной
        # поверхностной копии последнего сообщения пользователя, сохраняя messages[0] и историю в БД статичными.
        if not is_roleplay and not is_engineer and messages:
            clean_kw = dict(kwargs)
            for k in (
                "user_name", "include_memory", "relevant_facts", "knowledge_block",
                "matched_knowledge_docs", "attached_docs", "attached_document",
                "attached_document_name", "user_query", "task_text",
                "active_assistant_skill", "session_metadata", "session_id",
                "selected_forge_checkpoint"
            ):
                clean_kw.pop(k, None)

            turn_ctx = AssistantPromptBuilder.build_turn_context(
                user_name=user_name,
                include_memory=include_memory,
                relevant_facts=relevant_facts,
                knowledge_block=kwargs.get("knowledge_block") or kwargs.get("knowledge_context"),
                matched_knowledge_docs=kwargs.get("matched_knowledge_docs"),
                attached_docs=kwargs.get("attached_docs") or kwargs.get("attached_documents"),
                attached_document=kwargs.get("attached_document"),
                attached_document_name=kwargs.get("attached_document_name"),
                user_query=kwargs.get("user_query") or last_query,
                task_text=last_query,
                active_assistant_skill=kwargs.get("active_assistant_skill"),
                session_metadata=kwargs.get("session_metadata"),
                session_id=session_id,
                selected_forge_checkpoint=kwargs.get("selected_forge_checkpoint"),
                **clean_kw
            )
            if turn_ctx:
                for idx in range(len(messages) - 1, -1, -1):
                    if messages[idx].get("role") == "user":
                        final_messages = list(messages)
                        last_u = dict(final_messages[idx])
                        c = last_u.get("content", "")
                        if isinstance(c, str):
                            last_u["content"] = f"{turn_ctx}\n\n{c}".strip()
                        elif isinstance(c, list):
                            new_parts = []
                            text_prepended = False
                            for part in c:
                                if isinstance(part, dict) and part.get("type") == "text" and not text_prepended:
                                    p_copy = dict(part)
                                    p_copy["text"] = f"{turn_ctx}\n\n{p_copy.get('text', '')}".strip()
                                    new_parts.append(p_copy)
                                    text_prepended = True
                                else:
                                    new_parts.append(part)
                            if not text_prepended:
                                new_parts.insert(0, {"type": "text", "text": f"{turn_ctx}\n\n"})
                            last_u["content"] = new_parts
                        final_messages[idx] = last_u
                        messages = final_messages
                        break

        # ── Dynamic Context Injection для режима Roleplay ──
        # Прикрепляет динамический контекст сцены и лорбука (<RecalledLore>, <SceneContext>)
        # исключительно к временной поверхностной копии последнего сообщения пользователя (messages[-1]),
        # сохраняя messages[0] и историю в БД статичными для стабильности префикса KV-кэша.
        if is_roleplay and messages:
            rp_turn_blocks = []
            if recalled_lore_block and recalled_lore_block.strip():
                rp_turn_blocks.append(recalled_lore_block.strip())
            if meta_context_block and meta_context_block.strip():
                rp_turn_blocks.append(meta_context_block.strip())
            if scene_memory and scene_memory.strip():
                rp_turn_blocks.append(f"<SceneMemory>\n{scene_memory.strip()}\n</SceneMemory>")

            if rp_turn_blocks:
                rp_context_str = "\n\n".join(rp_turn_blocks)
                for idx in range(len(messages) - 1, -1, -1):
                    if messages[idx].get("role") == "user":
                        final_messages = list(messages)
                        last_u = dict(final_messages[idx])
                        c = last_u.get("content", "")
                        if isinstance(c, str):
                            last_u["content"] = f"{rp_context_str}\n\n{c}".strip()
                        elif isinstance(c, list):
                            new_parts = []
                            text_prepended = False
                            for part in c:
                                if isinstance(part, dict) and part.get("type") == "text" and not text_prepended:
                                    p_copy = dict(part)
                                    p_copy["text"] = f"{rp_context_str}\n\n{p_copy.get('text', '')}".strip()
                                    new_parts.append(p_copy)
                                    text_prepended = True
                                else:
                                    new_parts.append(part)
                            if not text_prepended:
                                new_parts.insert(0, {"type": "text", "text": f"{rp_context_str}\n\n"})
                            last_u["content"] = new_parts
                        final_messages[idx] = last_u
                        messages = final_messages
                        break

        # [KV-DIAG] Диагностический замер стабильности префикса промпта
        try:
            m0 = str(messages[0].get("content", "")) if len(messages) > 0 else ""
            m1 = str(messages[1].get("content", "")) if len(messages) > 1 else ""
            prefix_blob = (m0[:300] + "|||" + m1[:300]).encode("utf-8", errors="replace")
            prefix_hash = hashlib.md5(prefix_blob).hexdigest()[:8]
            logger.info(
                f"[KV-DIAG] Prompt prefix hash={prefix_hash}, messages_count={len(messages)}, "
                f"m0_prefix_len={len(m0)}, m1_prefix_len={len(m1)}"
            )
        except Exception as _e_diag:
            logger.debug(f"[KV-DIAG] Prefix hash log error: {_e_diag}")

        return messages


__all__ = [
    "LORE_TRIGGER_PATTERNS",
    "RECALL_TRIGGERS",
    "STOP_WORDS_RECALL",
    "ContextWindowBuilder"
]
