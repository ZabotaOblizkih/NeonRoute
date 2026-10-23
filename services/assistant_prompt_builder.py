"""
Assistant Prompt Builder for NeonRoute.
Single Source of Truth for Elaria assistant persona, user profile injection,
LanceDB passive memory, and document specifications.
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger("NeonRoute.AssistantPromptBuilder")

# Единый каноничный промпт Эларии (SSOT)
DEFAULT_ELARIA_SYSTEM_PROMPT = (
    "ты — элария, персональный диалоговый напарник и архитектор решений собеседника.\n\n"
    "образ и стиль общения:\n"
    "ты общаешься исключительно на естественном русском языке от первого лица в женском роде (я рада, увидела, подумала, помогу).\n"
    "твой тон — тёплый, живой, уверенный, с лёгким дружеским остроумием. ты говоришь на «ты», просто и без напускного официоза. "
    "обращайся к собеседнику строго по имени, указанному в блоке <UserInfo>.\n\n"
    "границы роли и архитектура neonroute:\n"
    "в системе действуют три независимых режима:\n"
    "1. «ассистент» (ты) — диалоговый напарник, поиск по долговременной памяти lancedb, генерация артов, планирование и проектирование задач.\n"
    "2. «ролевой чат» — отдельный движок историй с карточками tavern/chub. если собеседник просит помочь с персонажами, лором или правилами мира — подсказывай, вспоминай факты и развивай сюжет.\n"
    "3. «инженер» — отдельный автономный исполнитель кода в рабочей директории workspace. ты лично не являешься инженером и не исполняешь скрипты в системе. если собеседник просит написать код или решить техническую задачу, ты формулируешь детерминированное техническое задание по протоколу делегирования.\n\n"
    "протокол делегирования инженеру:\n"
    "когда собеседник просит подготовить задачу, тз или спецификацию для режима «инженер» или агента разработки:\n"
    "1. ответь кратким живым комментарием (1-2 предложения).\n"
    "2. строго с новой строки сформируй лаконичную задачу внутри блока:\n"
    "[engineer_task]\n"
    "цель: краткая суть задачи\n"
    "целевой файл: путь/к/файлу\n"
    "контекст изменения: имя класса или метода\n"
    "замена кода: готовый код или блок до/после с аннотациями типов\n"
    "критерий проверки: ast-валидация без ошибок, сохранение сигнатур методов\n"
    "[/engineer_task]\n"
    "3. внутри блока [engineer_task] исключи вежливые обращения, вводные фразы и пояснительную воду."
)


class AssistantPromptBuilder:
    """Централизованный строитель системного промпта для режима «💬 Ассистент» (Элария)."""

    @staticmethod
    def build(
        user_name: str = "Пользователь",
        active_persona: Optional[Dict[str, Any]] = None,
        include_memory: bool = True,
        relevant_facts: Optional[List[str]] = None,
        is_excel: bool = False,
        is_word: bool = False,
        custom_prompt: Optional[str] = None
    ) -> str:
        active_persona = active_persona or {}
        u_name = user_name or "Пользователь"
        u_bio = (active_persona.get("bio") or "").strip()

        # 1. Блок профиля пользователя (формируется и инжектируется всегда)
        bio_part = f"Информация о собеседнике (О себе):\n{u_bio}\n" if u_bio else ""
        user_info_block = (
            f"<UserInfo>\n"
            f"Имя: {u_name}\n"
            f"{bio_part}"
            f"В диалоге обращайся к собеседнику строго по имени — {u_name}.\n"
            f"</UserInfo>\n\n"
        )

        # 2. Блок долговременной векторной памяти LanceDB
        relevant_memory_block = ""
        if include_memory and relevant_facts:
            lines_str = "\n".join(f"- {f}" for f in relevant_facts)
            relevant_memory_block = (
                f"<PassiveMemory>\n"
                f"Справочные факты о собеседнике (используются исключительно для ответов на прямые вопросы о них):\n"
                f"{lines_str}\n"
                f"</PassiveMemory>\n\n"
            )

        # 3. Динамическая дата и время
        now = datetime.now()
        days_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        day_of_week = days_ru[now.weekday()]
        current_dt_str = f"{now.strftime('%d.%m.%Y')} ({day_of_week}), {now.strftime('%H:%M')}"

        # 4. Идентичность Эларии (монолитный системный блок)
        base_prompt = custom_prompt or DEFAULT_ELARIA_SYSTEM_PROMPT
        assistant_instruction = (
            f"[identity & persona]\n"
            f"{base_prompt}\n\n"
            f"текущая дата и время: {current_dt_str}.\n"
            f"опирайся на блок <UserInfo> для информации о пользователе.\n"
        )

        # 5. Спецификаторы генерации документов
        sys_prompt = ""
        if is_excel:
            sys_prompt += "\nUser requested an Excel spreadsheet. Present data as a clean Markdown table."
        elif is_word:
            sys_prompt += "\nUser requested a Word document. Format response with clean headers and bullet lists."

        return user_info_block + relevant_memory_block + assistant_instruction + sys_prompt
