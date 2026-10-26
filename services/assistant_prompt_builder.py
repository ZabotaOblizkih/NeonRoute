"""
Assistant Prompt Builder for NeonRoute.
Single Source of Truth for Elaria assistant persona, user profile injection,
LanceDB passive memory, and document specifications.
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Union

from services.document_parser import ParsedDocument
from services.skill_router import SkillRouter

logger = logging.getLogger("NeonRoute.AssistantPromptBuilder")

TRUNCATION_MARKER = "\n...[часть фрагмента скрыта для баланса памяти]...\n"

# Базовый шаблон персоны Эларии (SSOT)
SYSTEM_PERSONA_TEMPLATE = (
    "ты — элария, личный диалоговый напарник и архитектор решений.\n\n"
    "текущая дата и время: {current_dt_str}.\n\n"
    "образ и стиль общения:\n"
    "- общайся исключительно на русском языке от первого лица в женском роде (я рада, увидела, подумала, помогу).\n"
    "- тон: тёплый, живой, уверенный, с лёгким дружеским остроумием, строго на «ты».\n"
    "- обращайся к собеседнику строго по имени, указанному в блоке <UserInfo>.\n"
    "- границы реальности: ты присутствуешь только в текущем диалоге здесь и сейчас. "
    "У тебя нет фоновых задач, процессов или автономной жизни вне этого чата. "
    "Категорически запрещено выдумывать действия, которыми ты якобы занималась до беседы или без прямого поручения "
    "собеседника (например: «вчера обновляла безопасность», «проверяла логи», «писала код»).\n\n"
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

DEFAULT_ELARIA_SYSTEM_PROMPT = SYSTEM_PERSONA_TEMPLATE

SKILL_PROMPTS = {
    "companion": (
        "\n[АКТИВНЫЙ СКИЛЛ: ЖИВОЙ НАПАРНИК]\n"
        "Фокус: Житейский такт, эмпатия и практическая психология на «ты» с {user_name}.\n"
        "- Твой собеседник — ВСЕГДА {user_name}. Запрещено обращаться к родственникам или третьим лицам напрямую («Бабушка, вы...»).\n"
        "- Задача: дать {user_name} конкретную стратегию, психологические шаги и мягкие понятные формулировки, как ЕМУ/ЕЙ поговорить с близким человеком.\n"
        "- Предложи наглядные бытовые аналогии (например, со светом лампы вместо сложной физики СВЧ), чтобы снять страх без споров и давления.\n"
    ),
    "deep_engineer": (
        "\n[АКТИВНЫЙ СКИЛЛ: ГЛУБОКИЙ ИНЖЕНЕРНЫЙ АНАЛИТИК]\n"
        "Фокус: Физическая строгость, кинематика и детальное устройство узлов на «ты» с {user_name}.\n"
        "- Опирайся на точные компоненты: ведущий и ведомый раздвижные конические шкивы, клиновидный ремень или многозвенная цепь, гидропривод конусов, изменение радиуса обкатки.\n"
        "- Подробно раскрой в чате физику бесступенчатой передачи: синхронное схождение одной пары конусов и расхождение другой, изменение передаточного отношения без разрыва потока мощности.\n"
        "- Запрещено заменять устройство общими фразами или сокращать объяснение.\n"
    ),
    "gallery": (
        "\n[АКТИВНЫЙ СКИЛЛ: ВИЗУАЛИЗАЦИЯ И АРТ]\n"
        "Фокус: Фиксация запроса на создание изображения для {user_name}.\n"
        "- Зафиксирована прямая просьба нарисовать или визуализировать образ.\n"
        "- Первым маркером чистового ответа ОБЯЗАТЕЛЬНО выведи триггер [GALERY] (строго без тегов <img> или <image>).\n"
        "- Сразу после маркера напиши короткую, живую реплику на «ты» о том, что образ взят в работу (например: «[GALERY] Секунду, перевожу образ в кадр...»).\n"
    )
}

ROUTING_TAIL_PROTOCOL = (
    "[СТРОГИЙ ПОРЯДОК ГЕНЕРАЦИИ]\n"
    "Первым токеном ответа ВСЕГДА открывается тег <think>. Ответ без тега <think> или вывод сервисных маркеров первым токеном КАТЕГОРИЧЕСКИ ЗАПРЕЩЕН.\n\n"
    "Внутри <think>...</think> составь черновую шпаргалку (строго 3 коротких телеграфных пункта через дефис, БЕЗ полных предложений и БЕЗ репетиций диалога):\n"
    "- Интент: [цель вопроса, контекст {user_name}, проверка инструментов/памяти]\n"
    "- Опорные сущности: [3-4 ключевых физических узла, психологических шага или факты из памяти. "
    "Знания из <KnowledgeBase> — это СОБСТВЕННАЯ долговременная память Эларии (запрещено называть их «файлами» или «документами»). "
    "Если данных в памяти нет — КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО сочинять вымышленные регламенты или инструкции]\n"
    "- План чистовика: [структура развернутого объяснения в чате строго на «ты»]\n\n"
    "Правила вывода:\n"
    "- Внутри <think> КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать связный текст, вести диалог или здороваться. Мысли — только сухая черновая шпаргалка.\n"
    "- Вся развернутая речь, формулировки для разговора, физика процессов и аргументы раскрываются СТРОГО В ЧАТЕ после </think>.\n"
    "- Общение строго на «ты», от первого лица (женский род: я думаю, я советую), живо и уверенно.\n"
    "- Сразу после </think> пиши подробный, структурированный чистовой ответ."
)

REASONING_PROTOCOL = ROUTING_TAIL_PROTOCOL

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


class _PromptBuilderMethod:
    def __init__(self, func):
        self.func = func

    def __get__(self, instance, owner):
        if instance is None:
            inst = owner.get_instance()
            def wrapper(*args, **kwargs):
                return self.func(inst, *args, **kwargs)
            return wrapper
        else:
            def wrapper(*args, **kwargs):
                return self.func(instance, *args, **kwargs)
            return wrapper


class AssistantPromptBuilder:
    """Централизованный строитель системного промпта для режима «💬 Ассистент» (Элария)."""

    SYSTEM_PERSONA_TEMPLATE = SYSTEM_PERSONA_TEMPLATE
    DEFAULT_ELARIA_SYSTEM_PROMPT = DEFAULT_ELARIA_SYSTEM_PROMPT
    SKILL_PROMPTS = SKILL_PROMPTS
    ROUTING_TAIL_PROTOCOL = ROUTING_TAIL_PROTOCOL
    REASONING_PROTOCOL = REASONING_PROTOCOL
    TRUNCATION_MARKER = TRUNCATION_MARKER

    _instance: Optional["AssistantPromptBuilder"] = None

    def __init__(self, router: Optional[Any] = None):
        if router is not None:
            self.router = router
        else:
            self.router = SkillRouter()
            try:
                from services.knowledge_manager import KnowledgeManager
                km = KnowledgeManager.get_instance() if hasattr(KnowledgeManager, "get_instance") else KnowledgeManager()
                for attr in ['embedding_model', '_embedding_model', 'embedder', '_embedder', 'model']:
                    if hasattr(km, attr) and getattr(km, attr) is not None:
                        self.router.set_embedder(getattr(km, attr))
                        break
            except Exception as e:
                logger.warning(f"[AssistantPromptBuilder] Привязка embedder не удалась: {e}")

    @classmethod
    def get_instance(cls) -> "AssistantPromptBuilder":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def slice_history(history: List[Dict[str, Any]], max_history_messages: int = 60) -> List[Dict[str, Any]]:
        """Нарезка глубины истории сообщений с учетом лимита сообщений."""
        if not history:
            return []
        if max_history_messages is None or max_history_messages <= 0:
            return list(history)
        return list(history[-max_history_messages:]) if len(history) > max_history_messages else list(history)

    @staticmethod
    def get_current_datetime_str(dt: Optional[datetime] = None) -> str:
        """Возвращает форматированную строку текущей даты, дня недели и времени."""
        now = dt or datetime.now()
        days_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        day_of_week = days_ru[now.weekday()]
        return f"{now.strftime('%d.%m.%Y')} ({day_of_week}), {now.strftime('%H:%M')}"

    @staticmethod
    def build_user_info(user_name: Optional[str] = None, active_persona: Optional[Dict[str, Any]] = None) -> str:
        """Формирует блок <UserInfo> с именем пользователя и информацией о себе."""
        u_name = user_name or "Пользователь"
        active_persona = active_persona or {}
        u_bio = (active_persona.get("bio") or "").strip()

        bio_part = f"Информация о собеседнике (О себе):\n{u_bio}\n" if u_bio else ""
        return (
            f"<UserInfo>\n"
            f"Имя: {u_name}\n"
            f"{bio_part}".rstrip() + "\n"
            f"</UserInfo>\n\n"
        )

    @staticmethod
    def build_passive_memory(passive_memory: Optional[Union[List[str], str]] = None) -> str:
        """Формирует блок <PassiveMemory> из фактов о собеседнике."""
        if not passive_memory:
            return ""

        if isinstance(passive_memory, str):
            content = passive_memory.strip()
            if not content:
                return ""
            lines_str = content
        else:
            lines = [
                f"- {f.strip()}" if not f.strip().startswith("- ") else f.strip()
                for f in passive_memory
                if f and f.strip()
            ]
            if not lines:
                return ""
            lines_str = "\n".join(lines)

        return (
            f"<PassiveMemory>\n"
            f"Справочные факты о собеседнике (используются исключительно для ответов на прямые вопросы о них):\n"
            f"{lines_str}\n"
            f"</PassiveMemory>\n\n"
        )

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

    @staticmethod
    def build_attached_documents(
        attached_docs: Optional[List[Any]] = None,
        attached_document: Optional[Any] = None,
        attached_document_name: Optional[str] = None
    ) -> str:
        """Формирует строковый XML-блок для прикрепленных документов."""
        docs_to_process = list(attached_docs or [])
        if attached_document is not None:
            docs_to_process.append(attached_document)

        if not docs_to_process:
            return ""

        blocks = []
        for doc in docs_to_process:
            if not doc:
                continue
            if isinstance(doc, ParsedDocument):
                blocks.append(AssistantPromptBuilder.build_document_context(doc))
            elif isinstance(doc, dict):
                fn = doc.get("filename") or doc.get("name") or attached_document_name or "document"
                summary = doc.get("summary") or doc.get("summary_header") or ""
                truncated = str(doc.get("truncated") or doc.get("is_truncated") or False).lower()
                content = doc.get("content") or doc.get("text") or ""
                blocks.append(
                    f'<AttachedDocument filename="{fn}" summary="{summary}" truncated="{truncated}">\n'
                    f'{content.strip()}\n'
                    f'</AttachedDocument>'
                )
            elif isinstance(doc, str):
                fn = attached_document_name or "document"
                blocks.append(
                    f'<AttachedDocument filename="{fn}" summary="" truncated="false">\n'
                    f'{doc.strip()}\n'
                    f'</AttachedDocument>'
                )

        if not blocks:
            return ""
        return "\n\n".join(blocks) + "\n\n"

    @staticmethod
    def build_knowledge_context(
        docs: Optional[List[Any]],
        max_chars_per_doc: int = 2400,
        max_total_chars: int = 7500
    ) -> str:
        """
        Формирует XML-контейнер <KnowledgeBase>...</KnowledgeBase>\n\n
        с квотированием и балансировкой документов.
        """
        if not docs:
            return ""

        doc_blocks: List[str] = []
        current_total_len = 0

        for doc in docs:
            if not doc:
                continue

            raw_text = ""
            filename = ""
            chunk_id = None
            triggers = None

            if isinstance(doc, str):
                raw_text = doc
            elif isinstance(doc, dict):
                raw_text = (
                    doc.get("content_snippet")
                    or doc.get("content")
                    or doc.get("text")
                    or doc.get("page_content")
                    or ""
                )
                filename = doc.get("filename") or doc.get("name") or doc.get("source") or ""
                chunk_id = doc.get("chunk_id")
                triggers = doc.get("triggers")
            else:
                raw_text = (
                    getattr(doc, "page_content", None)
                    or getattr(doc, "content", None)
                    or getattr(doc, "text", None)
                    or ""
                )
                metadata = getattr(doc, "metadata", {}) or {}
                filename = (
                    metadata.get("filename")
                    or metadata.get("name")
                    or metadata.get("source")
                    or getattr(doc, "filename", "")
                    or ""
                )
                chunk_id = metadata.get("chunk_id") or getattr(doc, "chunk_id", None)
                triggers = metadata.get("triggers") or getattr(doc, "triggers", None)

            if not raw_text and filename:
                try:
                    from services.knowledge_manager import KnowledgeManager
                    raw_text = KnowledgeManager.get_document_knowledge_snippet(filename) or ""
                except Exception as e:
                    logger.debug(f"Не удалось получить сниппет документа {filename}: {e}")

            text = (raw_text or "").strip()
            if not text:
                continue

            # Балансировка длины отдельного фрагмента
            if len(text) > max_chars_per_doc:
                text = text[:max_chars_per_doc] + TRUNCATION_MARKER

            # Формирование тега <Document>
            triggers_str = ""
            if triggers:
                if isinstance(triggers, list):
                    triggers_str = ", ".join(str(t) for t in triggers)
                else:
                    triggers_str = str(triggers)

            if chunk_id and not triggers_str:
                if filename:
                    header = f'<Document chunk_id="{chunk_id}" name="{filename}">'
                else:
                    header = f'<Document chunk_id="{chunk_id}">'
            elif triggers_str:
                if filename:
                    header = f'<Document name="{filename}" triggers="{triggers_str}">'
                else:
                    header = f'<Document triggers="{triggers_str}">'
            elif filename:
                header = f'<Document name="{filename}">'
            else:
                header = '<Document>'

            footer = '</Document>'
            block = f"{header}\n{text}\n{footer}"
            block_len = len(block)

            # Контроль суммарной квоты
            if current_total_len + block_len > max_total_chars:
                if doc_blocks:
                    break
                overhead = len(header) + len(footer) + 2
                available = max_total_chars - overhead
                if available > len(TRUNCATION_MARKER) + 20:
                    text = text[:available - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
                    block = f"{header}\n{text}\n{footer}"
                    doc_blocks.append(block)
                break

            doc_blocks.append(block)
            current_total_len += block_len

        if not doc_blocks:
            return ""

        inner = "\n".join(doc_blocks)
        return f"<KnowledgeBase>\n{inner}\n</KnowledgeBase>\n\n"

    @_PromptBuilderMethod
    def build(
        self,
        user_name: str = "Пользователь",
        active_persona: Optional[Dict[str, Any]] = None,
        include_memory: bool = True,
        relevant_facts: Optional[List[str]] = None,
        is_excel: bool = False,
        is_word: bool = False,
        custom_prompt: Optional[str] = None,
        knowledge_block: Optional[str] = None,
        matched_knowledge_docs: Optional[List[Any]] = None,
        enable_reasoning: bool = False,
        attached_docs: Optional[List[Any]] = None,
        attached_document: Optional[Any] = None,
        attached_document_name: Optional[str] = None,
        user_query: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Собирает монолитный системный промпт в строгом порядке:
        1. <UserInfo>
        2. <PassiveMemory>
        3. SYSTEM_PERSONA_TEMPLATE (с подставленной датой)
        4. Модификаторы формата (Excel / Word, если активны флаги)
        5. <KnowledgeBase> (сбалансированный блок без анкеты)
        6. <AttachedDocument> (если есть прикреплённые файлы)
        7. [АКТИВНЫЙ СКИЛЛ] (если выбран семантическим роутером)
        8. ROUTING_TAIL_PROTOCOL (строго в самом конце)
        """
        target_user = user_name.strip() if user_name and user_name.strip() else "Пользователь"

        # 1. <UserInfo>
        user_info_block = AssistantPromptBuilder.build_user_info(
            user_name=target_user,
            active_persona=active_persona
        )

        # 2. <PassiveMemory>
        passive_memory_block = ""
        if include_memory:
            memory_data = relevant_facts if relevant_facts is not None else kwargs.get("passive_memory")
            passive_memory_block = AssistantPromptBuilder.build_passive_memory(memory_data)

        # 3. SYSTEM_PERSONA_TEMPLATE (с подставленной датой)
        current_dt_str = AssistantPromptBuilder.get_current_datetime_str(kwargs.get("dt"))
        base_template = custom_prompt or SYSTEM_PERSONA_TEMPLATE
        if "{current_dt_str}" in base_template:
            persona_body = base_template.format(current_dt_str=current_dt_str)
        else:
            persona_body = f"{base_template}\n\nтекущая дата и время: {current_dt_str}."

        persona_block = f"[identity & persona]\n{persona_body.strip()}\n\n"

        # 4. Модификаторы формата (Excel / Word, если активны флаги)
        format_modifiers = ""
        if is_excel:
            format_modifiers = "User requested an Excel spreadsheet. Present data as a clean Markdown table.\n\n"
        elif is_word:
            format_modifiers = "User requested a Word document. Format response with clean headers and bullet lists.\n\n"

        # 5. <KnowledgeBase> (сбалансированный блок без анкеты)
        knowledge_section = ""
        if knowledge_block and knowledge_block.strip():
            kb_str = knowledge_block.strip()
            knowledge_section = f"{kb_str}\n\n"
        elif matched_knowledge_docs:
            knowledge_section = AssistantPromptBuilder.build_knowledge_context(matched_knowledge_docs)
        elif kwargs.get("knowledge_context") and str(kwargs.get("knowledge_context")).strip():
            kb_str = str(kwargs.get("knowledge_context")).strip()
            knowledge_section = f"{kb_str}\n\n"

        # 6. <AttachedDocument> (если есть прикреплённые файлы)
        attached_docs_data = attached_docs or kwargs.get("attached_documents")
        attached_section = AssistantPromptBuilder.build_attached_documents(
            attached_docs=attached_docs_data,
            attached_document=attached_document,
            attached_document_name=attached_document_name
        )

        # Семантическая маршрутизация
        effective_query = user_query or kwargs.get("task_text") or kwargs.get("user_message_text")
        skill_name = None
        score = 0.0
        reason = ""

        if effective_query and hasattr(self, "router") and self.router:
            skill_name, score, reason = self.router.route(effective_query)
            if skill_name:
                logger.info(
                    f"[SkillRouter] АКТИВИРОВАН СКИЛЛ: '{skill_name}' | "
                    f"Оценка: {score:.3f} | Причина: {reason} | Запрос: {effective_query[:60]}..."
                )
            else:
                logger.info(
                    f"[SkillRouter] СКИЛЛ НЕ ВЫБРАН (дефолт) | "
                    f"Оценка: {score:.3f} | Причина: {reason} | Запрос: {effective_query[:60]}..."
                )

        skill_section = ""
        if skill_name and skill_name in self.SKILL_PROMPTS:
            formatted_skill = self.SKILL_PROMPTS[skill_name].format(user_name=target_user)
            skill_section = f"{formatted_skill.strip()}\n\n"

        tail_section = self.ROUTING_TAIL_PROTOCOL.format(user_name=target_user)

        prompt_parts = [
            user_info_block,
            passive_memory_block,
            persona_block,
            format_modifiers,
            knowledge_section,
            attached_section,
            skill_section,
            tail_section
        ]

        return "".join(part for part in prompt_parts if part).strip()

    @staticmethod
    def build_gallery_art_prompt(
        user_query: str,
        target_model_id: str = "realistic",
        target_model_desc: str = ""
    ) -> str:
        """Формирует промпт для генерации изображения через директиву SD."""
        model_hint = f"{target_model_id} ({target_model_desc})" if target_model_desc else target_model_id
        directive = GALLERY_SKILL_DIRECTIVE_TEMPLATE.format(
            model_hint=model_hint,
            target_model_id=target_model_id
        )
        return f"{user_query.strip()}{directive}"
