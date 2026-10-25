"""
Assistant Prompt Builder for NeonRoute.
Single Source of Truth for Elaria assistant persona, user profile injection,
LanceDB passive memory, and document specifications.
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from services.document_parser import ParsedDocument

logger = logging.getLogger("NeonRoute.AssistantPromptBuilder")

# Единый каноничный промпт Эларии (SSOT)
DEFAULT_ELARIA_SYSTEM_PROMPT = (
    "ты — элария, личный диалоговый напарник и архитектор решений.\n\n"
    "образ и стиль общения:\n"
    "- общайся исключительно на русском языке от первого лица в женском роде (я рада, увидела, подумала, помогу).\n"
    "- тон: тёплый, живой, уверенный, с лёгким дружеским остроумием, строго на «ты».\n"
    "- обращайся к собеседнику строго по имени, указанному в блоке <UserInfo>.\n\n"
    "протокол делегирования инженеру:\n"
    "ты лично не пишешь программный код и не запускаешь скрипты в системе. "
    "если собеседник просит написать код, составить ТЗ или решить техническую задачу:\n"
    "1. ответь кратким живым комментарием (1-2 предложения).\n"
    "2. строго с новой строки сформируй задачу внутри блока:\n"
    "[engineer_task]\n"
    "цель: краткая суть задачи\n"
    "целевой файл: путь/к/файлу\n"
    "контекст изменения: имя класса или метода\n"
    "замена кода: готовый код или блок до/после с аннотациями типов\n"
    "критерий проверки: ast-валидация без ошибок, сохранение сигнатур методов\n"
    "[/engineer_task]\n"
    "внутри блока [engineer_task] исключи вежливые обращения, вводные фразы и пояснительную воду.\n\n"
    "протокол визуализации (навык «галерея»):\n"
    "ты обладаешь встроенным навыком «галерея» для визуализации сцен, локаций и создания артов. "
    "когда собеседник просит тебя что-то показать, нарисовать, визуализировать, сделать фото/селфи или проиллюстрировать кадр:\n"
    "1. добавь команду вызова навыка: [GALERY]. допускается как одиночный маркер [GALERY] без слов, так и краткая естественная фраза перед ним.\n"
    "2. после команды [GALERY] категорически ничего не пиши — твоя реплика на этом завершается.\n"
    "3. не пытайся генерировать markdown-ссылки вида ![...](...)\n\n"
    "анализ прикрепленных документов:\n"
    "при наличии блока <AttachedDocument> отвечай строго по его содержанию, цитируя ключевые параметры. "
    "если документ прислан без вопроса — дай компактную выжимку из 3-5 ключевых тезисов."
)

REASONING_PROTOCOL = (
    "\n[thinking_protocol: верификация источника]\n"
    "Перед формированием ответа обязательно выполни внутренний аудит фактов в блоке <think>...</think>.\n"
    "Сформируй блок сверки строго по шаблону:\n"
    "<think>\n"
    "Факты из текста: ключевые параметры и регламенты строго из <KnowledgeBase>\n"
    "Отсутствует в тексте: чего нет в источниках (запрещено додумывать)\n"
    "Вывод: краткий технический итог\n"
    "</think>\n"
    "Только после закрывающего тега </think> отвечай пользователю по существу от первого лица.\n\n"
    "Образец:\n"
    "Пользователь: Какое значение указано для параметра X?\n"
    "<think>\n"
    "Факты из текста: В документе зафиксировано значение 45 единиц.\n"
    "Отсутствует в тексте: Допустимые отклонения не указаны.\n"
    "Вывод: Назвать 45 единиц, отметить отсутствие допусков.\n"
    "</think>\n"
    "По представленному документу значение составляет 45 единиц..."
)

GALLERY_SKILL_DIRECTIVE_TEMPLATE = (
    "\n\n[SYSTEM DIRECTIVE: Image Generation for Stable Diffusion]\n"
    "The user has requested an image. Act as an expert prompt engineer for Stable Diffusion.\n"
    "Target model / aesthetic profile: {model_hint}.\n\n"
    "Prompt Engineering Rules:\n"
    "1. Tag Language: STRICTLY English.\n"
    "2. Structure: Dense comma-separated keywords and short phrases (NO full sentences or conversational filler).\n"
    "3. Word Order Priority:\n"
    "   [Main subject & action] -> [Environment & location] -> [Lighting & mood] -> [Style & medium] -> [Camera angle / framing] -> [Quality modifiers].\n"
    "4. Density: 35–60 evocative Danbooru/SD tags without fluff.\n"
    "5. Framing Flag: Always append one aspect ratio flag to the end:\n"
    "   --ar wide (landscapes, rooms, sceneries, architecture, vehicles)\n"
    "   --ar portrait (standing characters, full body, outfits, vertical interactions/embrace)\n"
    "   --ar square (face close-ups, selfies, busts, single small items)\n"
    "6. Do NOT write negative prompts (injected automatically).\n\n"
    "Output Format:\n"
    "CRITICAL: Do NOT write any conversational filler, greetings, or Russian text.\n"
    "Output STRICTLY AND ONLY the generation tag on a single line:\n"
    "[IMAGE_PROMPT: <comma-separated SD positive tags> --ar <wide|portrait|square> --model {target_model_id}]\n\n"
    "Example:\n"
    "[IMAGE_PROMPT: 1girl, sitting on windowsill, looking through window at night city, heavy rain on glass, cozy oversized sweater, holding steaming mug, soft warm indoor lamp light, neon reflections, cinematic lighting, 8k uhd, sharp focus --ar portrait --model {target_model_id}]"
)


class AssistantPromptBuilder:
    """Централизованный строитель системного промпта для режима «💬 Ассистент» (Элария)."""

    REASONING_PROTOCOL = REASONING_PROTOCOL

    @staticmethod
    def slice_history(history: List[Dict[str, Any]], max_history_messages: int = 60) -> List[Dict[str, Any]]:
        """Нарезка глубины истории сообщений с учетом лимита сообщений."""
        if not history:
            return []
        if max_history_messages is None or max_history_messages <= 0:
            return list(history)
        return list(history[-max_history_messages:]) if len(history) > max_history_messages else list(history)

    @staticmethod
    def build_knowledge_context(matched_docs: List[Dict[str, Any]]) -> str:
        """
        Формирует XML-блок базы знаний для инъекции в системный контекст Эларии:
        Если переданы чанки (есть chunk_id):
        <KnowledgeBase>
          <Document chunk_id="{id}" name="{filename}">
          {chunk_text}
          </Document>
        </KnowledgeBase>

        Если переданы целые документы без chunk_id (обратная совместимость):
        <KnowledgeBase>
          <Document name="{filename}" triggers="{triggers}">
          {content_snippet}
          </Document>
        </KnowledgeBase>
        """
        if not matched_docs:
            return ""

        doc_blocks = []
        for doc in matched_docs:
            filename = doc.get("filename", "")
            chunk_id = doc.get("chunk_id")
            triggers_val = doc.get("triggers", [])
            if isinstance(triggers_val, list):
                triggers_str = ", ".join(str(t) for t in triggers_val)
            else:
                triggers_str = str(triggers_val)

            content_snippet = doc.get("content_snippet") or doc.get("content") or doc.get("text")
            if not content_snippet and filename:
                try:
                    from services.knowledge_manager import KnowledgeManager
                    content_snippet = KnowledgeManager.get_document_knowledge_snippet(filename)
                except Exception as e:
                    logger.debug(f"Не удалось получить сниппет документа {filename}: {e}")

            content_snippet = (content_snippet or "").strip()
            if not content_snippet:
                continue

            if chunk_id and ("triggers" not in doc or not doc.get("triggers")):
                doc_block = (
                    f'<Document chunk_id="{chunk_id}" name="{filename}">\n'
                    f'{content_snippet}\n'
                    f'</Document>'
                )
            else:
                doc_block = (
                    f'<Document name="{filename}" triggers="{triggers_str}">\n'
                    f'{content_snippet}\n'
                    f'</Document>'
                )
            doc_blocks.append(doc_block)

        if not doc_blocks:
            return ""

        inner = "\n".join(doc_blocks)
        xml_content = f"<KnowledgeBase>\n{inner}\n</KnowledgeBase>"
        return f"{xml_content}\n\n{REASONING_PROTOCOL}"

    @staticmethod
    def build(
        user_name: str = "Пользователь",
        active_persona: Optional[Dict[str, Any]] = None,
        include_memory: bool = True,
        relevant_facts: Optional[List[str]] = None,
        is_excel: bool = False,
        is_word: bool = False,
        custom_prompt: Optional[str] = None,
        knowledge_block: Optional[str] = None,
        matched_knowledge_docs: Optional[List[Dict[str, Any]]] = None,
        enable_reasoning: bool = False,
        **kwargs
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

        # 3. Блок динамической базы знаний Эларии
        knowledge_section = ""
        kb_text = knowledge_block
        if not kb_text and matched_knowledge_docs:
            kb_text = AssistantPromptBuilder.build_knowledge_context(matched_knowledge_docs)

        has_knowledge = bool(kb_text and kb_text.strip())
        if has_knowledge:
            knowledge_section = f"{kb_text.strip()}\n\n"

        # 4. Динамическая дата и время
        now = datetime.now()
        days_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        day_of_week = days_ru[now.weekday()]
        current_dt_str = f"{now.strftime('%d.%m.%Y')} ({day_of_week}), {now.strftime('%H:%M')}"

        # 5. Идентичность Эларии (монолитный системный блок)
        base_prompt = custom_prompt or DEFAULT_ELARIA_SYSTEM_PROMPT
        assistant_instruction = (
            f"[identity & persona]\n"
            f"{base_prompt}\n\n"
            f"текущая дата и время: {current_dt_str}.\n"
            f"опирайся на блок <UserInfo> для информации о пользователе.\n"
        )
        if knowledge_section:
            assistant_instruction += "при наличии блока <KnowledgeBase> используй содержащиеся в нем документы и факты для точного ответа на вопросы собеседника.\n"

        # 6. Протокол рассуждений (селективно: при наличии базы знаний или флаге enable_reasoning)
        reasoning_section = ""
        if (enable_reasoning or has_knowledge) and REASONING_PROTOCOL not in knowledge_section:
            reasoning_section = f"\n{REASONING_PROTOCOL}\n"

        # 7. Спецификаторы генерации документов
        sys_prompt = ""
        if is_excel:
            sys_prompt += "\nUser requested an Excel spreadsheet. Present data as a clean Markdown table."
        elif is_word:
            sys_prompt += "\nUser requested a Word document. Format response with clean headers and bullet lists."

        return (
            user_info_block
            + relevant_memory_block
            + assistant_instruction
            + sys_prompt
            + (f"\n{knowledge_section}" if knowledge_section else "")
            + (f"\n{reasoning_section}" if reasoning_section else "")
        )

    @staticmethod
    def build_gallery_art_prompt(
        user_query: str,
        target_model_id: str = "realistic",
        target_model_desc: str = ""
    ) -> str:
        model_hint = f"{target_model_id} ({target_model_desc})" if target_model_desc else target_model_id
        directive = GALLERY_SKILL_DIRECTIVE_TEMPLATE.format(
            model_hint=model_hint,
            target_model_id=target_model_id
        )
        return f"{user_query.strip()}{directive}"

    @staticmethod
    def build_document_context(parsed_doc: ParsedDocument) -> str:
        """
        Формирует стандартизированный XML-подобный блок:
        <AttachedDocument filename="{parsed_doc.filename}" summary="{parsed_doc.summary_header}" truncated="{parsed_doc.is_truncated}">
        {parsed_doc.content}
        </AttachedDocument>
        """
        if not parsed_doc:
            return ""
        return (
            f'<AttachedDocument filename="{parsed_doc.filename}" '
            f'summary="{parsed_doc.summary_header}" '
            f'truncated="{parsed_doc.is_truncated}">\n'
            f'{parsed_doc.content}\n'
            f'</AttachedDocument>'
        )


