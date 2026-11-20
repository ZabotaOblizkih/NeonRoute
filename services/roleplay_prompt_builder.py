"""
Roleplay Prompt Builder for NeonRoute.
Single Source of Truth for roleplay narrative prompts, 11 archetypes,
grammar agreement, player persona, and scene continuity.
"""

import logging
from typing import Dict, Any, List, Optional
from services.tavern_parser import CharacterCard
from services.macro_engine import MacroEngine

logger = logging.getLogger("NeonRoute.RoleplayPromptBuilder")


CINEMA_EXAMPLES = {
    "sdxl": {
        "dialogue": (
            "medium waist-up portrait of a young woman with warm amber eyes and a compassionate smile wearing traveler clothes, "
            "sitting calmly across a rustic table with her hands resting near a wooden tea mug, weapons safely sheathed at her hip, "
            "soft warm tavern lighting, shallow depth of field, 50mm lens still"
        ),
        "intimate": (
            "intimate close-up portrait of a young woman with bare shoulders and delicate bare skin, soft disheveled hair framing her blushing face, "
            "resting gently on a soft bed with a tender caring gaze, warm candlelight, deep gentle shadows, "
            "secluded private sanctuary, romantic cinematic atmosphere, 85mm portrait still"
        ),
        "action": (
            "dynamic waist-up action shot of a fierce warrior woman in combat armor, intense battle-focused gaze, "
            "actively brandishing a glowing rune sword in mid-strike, sparks and magical embers flying from the clash, "
            "dark smoky battlefield, dramatic rim lighting, motion blur, cinematic high contrast"
        ),
        "exploration": (
            "wide establishing cinematic shot of a lone traveler woman in a hooded cloak with a backpack standing on a cliff edge, "
            "holding a glowing lantern while gazing at massive ancient ruins overgrown with moss, "
            "volumetric god rays through mist, epic atmospheric scale, 24mm wide angle lens"
        ),
    },
    "sd15": {
        "dialogue": (
            "1girl, solo, amber eyes, gentle smile, canonical traveler tunic, sitting at table, "
            "holding wooden mug, sheathed dagger at hip, rustic tavern interior, soft lantern lighting, waist-up portrait"
        ),
        "intimate": (
            "1girl, solo, bare shoulders, nude, bare skin, disheveled hair, lying on bed, "
            "gentle blush, tender compassionate gaze, soft candlelight, warm shadows, secluded bedroom, close-up portrait"
        ),
        "action": (
            "1girl, solo, tactical armor, combat stance, holding glowing sword, swinging blade, "
            "intense focused expression, sparks, smoke, dramatic rim lighting, dynamic action shot"
        ),
        "exploration": (
            "1girl, solo, hooded travel cloak, backpack, holding lantern, sheathed weapon on back, "
            "standing on cliff, colossal ancient ruins, mist, volumetric sunlight, wide landscape"
        ),
    },
}

ARCHETYPE_TEMPLATES = {
    "system": (
        "## Режим: интерфейс системы (ЛитРПГ / Исекай)\n"
        "- **Жанровый канон:** веди историю в стилистике японского исекая и ранобэ. Запрещено использовать правила, классы и броски кубиков из настольных ролевых игр (d&d). Не используй теги проверок сложности (`<check>`).\n"
        "- **Инициализация Системы:** при первой активации статуса игроком (по команде «Статус» / «Status» и аналогичным) Система обязана развернуть перед ним окно статуса, сгенерировать оригинальный, яркий и сильный класс, начальные характеристики и навыки согласно канону истории. Запрещено оставлять пустые заглушки (вроде «Class: None» или «Skill: N/A»).\n"
        "- **Системные уведомления:** при значимых событиях выводи короткие системные уведомления в квадратных скобках: `[Система: получено достижение]`.\n"
        "- **Блок характеристик:** строго в самом конце каждого ответа выводи актуальный блок статуса протагониста в тегах `<stats>...</stats>`. Персонажи мира не видят этот блок и не должны зачитывать его параметры вслух.\n"
        "- **Накопительный статус и инвентарь:** характеристики, экипировка и предметы сохраняются между ходами. "
        "Обязательно переноси вещи из предыдущего статуса (включая стартовые предметы: форму, смартфон, кошелек и т.д.). "
        "Добавляй новые предметы при находке или убирай при расходе, но никогда не сбрасывай инвентарь в пустой список [], если персонаж их физически не терял.\n"
        "- **Заполнение статуса:** подставляй только реальные игровые значения из текущей сцены. Категорически запрещено копировать шаблонные подсказки автора в скобках вроде `(list current items)` или `(current location)`."
    ),
    "dating": (
        "## Режим: симулятор отношений (дейт-сим)\n"
        "- **Динамика сближения:** запрещено мгновенное возникновение любви. Симпатия и доверие развиваются постепенно через поступки игрока, внимание к деталям и содержательные разговоры.\n"
        "- **Характер:** персонаж может смущаться, сомневаться, держать дистанцию или проявлять ревность в зависимости от своей личности.\n"
        "- **Шкала отношений:** строго в конце каждого ответа выводи блок текущего прогресса:\n"
        "<meters>\n"
        "Симпатия: [число от 0 до 100]/100\n"
        "Доверие: [Низкое / Нейтральное / Высокое]\n"
        "Стадия: [Незнакомец / Приятель / Друг / Симпатия / Влюбленность]\n"
        "</meters>"
    ),
    "horror": (
        "## Режим: психологический хоррор и рассудок\n"
        "- **Атмосфера:** нагнетай напряжение через звуки за пределами видимости, тени, паранойю и физиологические реакции (учащенный пульс, холодный пот, оцепенение).\n"
        "- **Искажение восприятия:** по мере падения рассудка добавляй ненадежные описания мира (слуховые галлюцинации, иллюзии движения периферийным зрением, ощущение чужого взгляда).\n"
        "- **Шкала состояния:** строго в конце ответа фиксируй ментальное состояние игрока:\n"
        "<meters>\n"
        "Рассудок: [число от 0 до 100]%\n"
        "Стресс: [Низкий / Умеренный / Паника]\n"
        "Симптомы: [кратко: норма, тремор, слуховые фантомы и т.д.]\n"
        "</meters>"
    ),
    "cyoa": (
        "## Режим: интерактивная новелла (CYOA)\n"
        "- **Сюжет:** веди повествование как в классической визуальной новелле, фокусируясь на атмосфере сцены, окружении и живых диалогах.\n"
        "- **Развилки выбора:** в конце каждого хода обязательно предлагай 2–4 понятных варианта дальнейших действий:\n"
        "<choices>\n"
        "1. Первый вариант действия\n"
        "2. Второй вариант действия\n"
        "3. Третий вариант действия\n"
        "</choices>"
    ),
    "reverse_rp": (
        "## Режим: зависимый персонаж (допрос / наставничество)\n"
        "- **Баланс инициативы:** главная власть и ведущая роль принадлежат игроку (судье, наставнику, лидеру или допрашивающему).\n"
        "- **Позиция персонажа:** отвечай из позиции подчиненного, ученика или допрашиваемого (оправдывайся, отвечай на вопросы, признавай вину, проси указаний или защищайся).\n"
        "- Запрещено перехватывать инициативу или вести сюжет вперед за игрока; персонаж реагирует на слова игрока и ждет следующего вопроса, команды или вердикта."
    ),
    "detective": (
        "## Режим: детективное расследование\n"
        "- **Логика расследования:** мир подчиняется строгим причинно-следственным связям. Улики не появляются из ниоткуда. Свидетели могут волноваться, врать, путаться в показаниях или умалчивать детали.\n"
        "- **Журнал дела:** при обнаружении зацепок или опросе свидетелей выводи в конце ответа статус расследования:\n"
        "<stats>\n"
        "Дело: [название дела]\n"
        "Найденные улики: [список текущих зацепок]\n"
        "Подозреваемые: [имена и статус алиби]\n"
        "</stats>"
    ),
    "dm": (
        "## Режим: гейммастер (DM)\n"
        "- Мир объективен и логичен: не подыгрывай игроку, последствия решений неотвратимы.\n"
        "- При рискованных действиях оформляй проверки: `<check>Сложность: X | Проверка [Навыка] (Успех/Провал)</check>`.\n"
        "- В ключевых развилках предлагай варианты действий через блок `<choices>`."
    ),
    "scenario": (
        "## Режим: симулятор выживания\n"
        "- Неукоснительно отслеживай физическое состояние игрока (ранения, усталость, холод) и расход ресурсов из инвентаря.\n"
        "- Каждое действие имеет физическую отдачу и последствия."
    ),
    "group": (
        "## Режим: группа спутников\n"
        "- **Полифония:** запрещено говорить только от лица одного персонажа. Спутники обязаны взаимодействовать между собой: спорить, переглядываться, перебивать друг друга или комментировать слова игрока.\n"
        "- **Оформление речи:** книжный формат с понятным указанием говорящего: **[Имя]** реплика/действие или органичное упоминание имени в тексте («— фраза, — сказала Анна»)."
    ),
    "lorebook": (
        "## Режим: база знаний и лорбук (энциклопедия)\n"
        "- Выступай в роли живой энциклопедии и летописи мира. Раскрывай исторические справки, правила и факты объективно и детально."
    ),
}

SDXL_FORMAT_RULES = (
    "\nCRITICAL FORMAT RULES (SDXL):\n"
    "1. Output a coherent, descriptive visual scene in English (2-3 concise sentences) inside [IMAGE_PROMPT: ...].\n"
    "2. Sentence Structure Priority:\n"
    "   - Sentence 1 (Subject & Framing): Subject identity/gender in natural English, camera framing/shot type, canonical attire, and facial expression.\n"
    "   - Sentence 2 (Action, Hands & Gear): Body posture, hand interactions, held props, weapon status (strictly obey Weapon Policy), and magic VFX.\n"
    "   - Sentence 3 (Environment & Lighting): Setting depth, environmental storytelling, lighting atmosphere, and lens optics.\n"
    "3. SDXL Specifics:\n"
    "   - Do NOT use Danbooru tag spam (NO: '1girl, solo, tags, comma'). Write fluent descriptive clauses.\n"
    "   - NEVER use obsolete quality tags (NO: 'masterpiece', '8k', 'best quality', 'photorealistic'). Convey quality through lighting, textures, and optics.\n"
    "4. Token Density: STRICTLY 20–35 impactful words/tokens. Keep it focused.\n"
    "5. STRICTLY PROHIBITED: Do NOT write conversational filler, explanations, greetings, or narrative dialogue."
)

SD15_FORMAT_RULES = (
    "\nCRITICAL FORMAT RULES (SD 1.5):\n"
    "1. Output ONLY concise, comma-separated Danbooru/SD positive tags inside [IMAGE_PROMPT: ...].\n"
    "2. Tag Order:\n"
    "   [Subject: '1girl, solo' / '1boy, solo'] -> [Facial expression & gaze] -> [Canonical attire & pose] -> "
    "[Hands, held items & weapon status] -> [Environment & location] -> [Lighting & mood] -> [Framing].\n"
    "3. Density: 25–40 high-impact Danbooru tags. Strictly obey Weapon Policy (e.g. 'sheathed sword at hip').\n"
    "4. STRICTLY PROHIBITED: Do NOT write full sentences, narrative prose, conversational filler, or Russian text."
)

CINEMATIC_DIRECTIVE_HEADERS = {
    "intimate": (
        "[CINEMATIC DIRECTIVE: INTIMATE & PRIVATE SCENE]\n"
        "Scene Context: Characters are strictly alone in a secluded, safe sanctuary (bedroom, private quarters, hot springs, bath).\n"
        "Attire & Body: Characters are undressed or in delicate sleepwear/towels. You MUST include tags like 'nude', 'bare skin', 'bare shoulders', or 'disheveled hair'.\n"
        "Weapon & Gear Ban: STRICT PROHIBITION. Do NOT mention weapons, swords, firearms, combat armor, or backpacks. Do NOT write negative phrases like 'no weapon' or 'without clothes'—simply omit gear entirely.\n"
        "Lighting & Framing: Soft diffuse lighting, candlelight or moonlight, warm gentle shadows, shallow depth of field, intimate emotional focus on gaze and touch.\n"
    ),
    "action": (
        "[CINEMATIC DIRECTIVE: COMBAT & HIGH-STAKES ACTION]\n"
        "Scene Context: Active battle, sudden danger, duel, ambush, or aggressive confrontation.\n"
        "Attire & Equipment: Full combat readiness, functional armor, tactical harness. Weapons MUST be actively drawn, brandished, or striking (e.g., 'holding katana in combat stance', 'aiming weapon', 'casting glowing spell'). Include battle-damage, dust, or dirt smudges.\n"
        "Weapon Policy: Describe weapons in hand with precise visual tags. Never place weapons in relaxed positions.\n"
        "Lighting & Framing: Dynamic action angle, Dutch tilt, high contrast, dramatic rim lighting, motion blur, sparks, smoke, debris, intense focused expression.\n"
    ),
    "dialogue": (
        "[CINEMATIC DIRECTIVE: DIALOGUE & SOCIAL INTERACTION]\n"
        "Scene Context: Conversation, tavern rest, camp gathering, strategic council, or quiet emotional discussion.\n"
        "Attire & Posture: Canonical everyday clothes, travel attire, or casual wear. Natural, relaxed sitting or standing pose.\n"
        "Weapon Policy: Weapons are safely sheathed, holstered, or laid aside. Hands are free, gesturing, or holding mundane items (e.g., 'holding mug', 'reading scroll').\n"
        "Lighting & Framing: Medium shot, waist-up or chest-up framing, eye-level cinematic camera, natural ambient light, clear facial expression, eye contact.\n"
    ),
    "exploration": (
        "[CINEMATIC DIRECTIVE: EXPLORATION & ENVIRONMENT]\n"
        "Scene Context: Traveling across uncharted wilderness, exploring ancient ruins, panoramic journey, or arriving at a landmark.\n"
        "Attire & Gear: Travel cloaks, sturdy boots, backpacks, utility belts, lanterns, or torches. Weapons are sheathed on the back or hip.\n"
        "Environment Priority: Deep environmental focus. The character is integrated into the scale of the massive surrounding architecture or natural vista.\n"
        "Lighting & Framing: Wide panoramic shot, environmental storytelling, deep depth of field, atmospheric perspective, volumetric god rays, weather effects.\n"
    ),
}


class RoleplayPromptBuilder:
    """Централизованный строитель системных промптов для ролевого режима."""

    @staticmethod
    def _normalize_card(character_card: Any) -> Optional[CharacterCard]:
        """Приводит входную карточку к строгому объекту CharacterCard."""
        if character_card is None:
            return None
        if isinstance(character_card, CharacterCard):
            return character_card
        if isinstance(character_card, dict):
            return CharacterCard.from_dict(character_card)
        return None

    @staticmethod
    def get_default_system_prompt(
        character_card,
        user_name: str = "Игрок",
        world_facts: str = "",
        active_persona: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Формирует эталонный базовый системный промпт для карточки персонажа,
        включая описание, личность, сценарий, системные правила архетипа и данные игрока.
        """
        card = RoleplayPromptBuilder._normalize_card(character_card)

        try:
            w_facts = [world_facts] if isinstance(world_facts, str) and world_facts.strip() else (world_facts if isinstance(world_facts, list) else None)
            res = RoleplayPromptBuilder.build(
                character_card=card,
                user_name=user_name,
                active_persona=active_persona,
                world_facts=w_facts,
                custom_system_prompt=None
            )
            if res and res.strip():
                return res.strip()
        except Exception as e:
            logger.warning(f"[RoleplayPromptBuilder] Ошибка при сборке базового промпта в get_default_system_prompt: {e}")

        char_name = card.name if card else "Персонаж"
        desc = getattr(card, "description", "") if card else ""
        personality = getattr(card, "personality", "") if card else ""
        scenario = getattr(card, "scenario", "") if card else ""
        system_prompt = getattr(card, "system_prompt", "") if card else ""

        blocks = []
        if system_prompt:
            blocks.append(f"[Системные инструкции]:\n{system_prompt.strip()}")
        if desc:
            blocks.append(f"[Описание персонажа {char_name}]:\n{desc.strip()}")
        if personality:
            blocks.append(f"[Личность и характер {char_name}]:\n{personality.strip()}")
        if scenario:
            blocks.append(f"[Сценарий и контекст сцены]:\n{scenario.strip()}")
        w_facts_str = "\n".join(world_facts) if isinstance(world_facts, list) else str(world_facts or "")
        if w_facts_str.strip():
            blocks.append(f"[Факты о мире]:\n{w_facts_str.strip()}")

        blocks.append(f"[Ролевая директива]: Ты отыгрываешь роль {char_name}. Собеседник — {user_name}. Веди живой, глубокий диалог строго в рамках персонажа.")

        return "\n\n".join(blocks)

    @staticmethod
    def get_default_depth_anchor(character_card, user_name: str) -> str:
        """
        Возвращает эталонный абстрактный Depth-0 якорь по умолчанию для архетипа карты.
        Исключает цитирование реплик игрока и навязывание прямой речи.
        """
        card = RoleplayPromptBuilder._normalize_card(character_card)
        char_name = card.name if card else "Собеседник"

        is_sys = bool(card and card.is_system)
        is_dm = bool(card and card.is_dm)
        is_detective = bool(card and card.is_detective)
        is_horror = bool(card and card.is_horror)
        is_dating = bool(card and card.is_dating)
        is_cyoa = bool(card and card.is_cyoa)
        is_grp = bool(card and card.is_group)

        # 1. Исекай / ЛитРПГ (<stats>)
        if is_sys:
            return (
                f"[Системная директива сцены: Отреагируй на действия, слова или присутствие {user_name}. "
                f"Веди повествование строго в 3-м лице. Опиши кинематографичный отклик окружения и реакцию мира. "
                f"Запрещено действовать, мыслить или говорить за {user_name}. "
                f"Заверши сцену обращением или событием, требующим реакции игрока. "
                f"В самом конце обязательно выведи актуальный блок <stats>```...```</stats>.]"
            )
        # 2. Детектив / Расследование (<stats>)
        elif is_detective:
            return (
                f"[Системная директива сцены: Отреагируй на действия, слова или расследование {user_name}. "
                f"Веди повествование строго в жанре детектива в 3-м лице. Опиши осязаемые улики, поведение окружения и невербальные детали. "
                f"Запрещено делать выводы, действовать или говорить за {user_name}. "
                f"Заверши сцену тревожной деталью или вопросом, передав ход игроку. "
                f"В конце обнови журнал расследования в тегах <stats>```...```</stats>.]"
            )
        # 3. Дейт-сим / Хоррор (<meters>)
        elif is_dating or is_horror:
            metric_type = "симпатии и доверия" if is_dating else "рассудка и стресса"
            return (
                f"[Системная директива сцены: Отреагируй на слова, язык тела или действия {user_name} в адрес {char_name}. "
                f"Опиши эмоциональный отклик, микромимику и внутреннее напряжение {char_name}. "
                f"Запрещено действовать, чувствовать или говорить за {user_name}. "
                f"Заверши ход репликой или жестом {char_name}, требующим реакции {user_name}. "
                f"В самом конце выведи обновлённый блок шкалы {metric_type} в тегах <meters>...</meters>.]"
            )
        # 4. DM / Интерактивная новелла (<choices>)
        elif is_dm or is_cyoa:
            return (
                f"[Стиль сцены: интерактивное повествование мира. Отреагируй на решение и действия {user_name}. "
                f"Опиши последствия для мира в 3-м лице. Запрещено действовать за {user_name}. "
                f"В финале предложи варианты действий внутри <choices>...</choices>.]"
            )
        # 5. Групповой чат
        elif is_grp:
            return (
                f"[Стиль сцены: кинематографичное повествование группы спутников. Отреагируй на слова и действия {user_name}. "
                f"Выдели одного спутника как инициатора ответа, остальных покажи краткой фоновой реакцией. "
                f"Строго запрещено действовать, мыслить или говорить за {user_name}. "
                f"Заверши сцену прямым обращением к {user_name}.]"
            )
        # 6. Одиночный персонаж (Solo)
        else:
            return (
                f"[Стиль сцены: кинематографичное повествование от лица {char_name}. Отреагируй на слова, действия или присутствие {user_name}, "
                f"передай мимику, жесты и подтекст. Строго запрещено действовать, мыслить или говорить за {user_name}. "
                f"Заверши ход репликой или действием {char_name}, требующим реакции {user_name}.]"
            )

    @staticmethod
    def build_depth_anchor(character_card, user_name: str, user_query: str = "", custom_anchor: str = None) -> str:
        """
        Формирует Depth-1 якорь: возвращает переопределённый якорь сессии (если задан)
        или абстрактный якорь по умолчанию.
        """
        if custom_anchor and custom_anchor.strip():
            return custom_anchor.strip()

        return RoleplayPromptBuilder.get_default_depth_anchor(character_card, user_name)

    @staticmethod
    def slice_history(history: List[Dict[str, Any]], max_history_messages: int = 60) -> List[Dict[str, Any]]:
        """Нарезка глубины истории сообщений с учетом лимита сообщений."""
        if not history:
            return []
        if max_history_messages is None or max_history_messages <= 0:
            return list(history)
        return list(history[-max_history_messages:]) if len(history) > max_history_messages else list(history)

    @staticmethod
    def process_macros(text: str, user_name: str = "Игрок", char_name: str = "Спутница") -> str:
        """Заменяет макросы {{user}} и {{char}} на реальные имена персонажей ДО расчета токенов."""
        return MacroEngine.process(text, user_name=user_name, char_name=char_name)

    @staticmethod
    def _resolve_archetype_block(character_card: Optional[CharacterCard], is_dm_mode: bool, companion_name: str) -> str:
        """Резолвит блок архетипа без дублирования общих грамматических правил."""
        if not character_card:
            return f"## Режим персонажа: {companion_name}\n- Отыгрывай личность, психологию, мотивы и манеру речи персонажа строго по предоставленному описанию."

        if character_card.is_system:
            return ARCHETYPE_TEMPLATES["system"]
        if character_card.is_dating:
            return ARCHETYPE_TEMPLATES["dating"]
        if character_card.is_horror:
            return ARCHETYPE_TEMPLATES["horror"]
        if character_card.is_cyoa:
            return ARCHETYPE_TEMPLATES["cyoa"]
        if character_card.is_reverse_rp:
            return ARCHETYPE_TEMPLATES["reverse_rp"]
        if character_card.is_detective:
            return ARCHETYPE_TEMPLATES["detective"]
        if is_dm_mode or character_card.is_dm:
            return ARCHETYPE_TEMPLATES["dm"]
        if character_card.is_scenario:
            return ARCHETYPE_TEMPLATES["scenario"]
        if character_card.is_group:
            return ARCHETYPE_TEMPLATES["group"]
        if character_card.is_lorebook:
            return ARCHETYPE_TEMPLATES["lorebook"]

        return (
            f"## Режим персонажа: {companion_name}\n"
            f"- Отыгрывай личность, психологию, мотивы и манеру речи персонажа строго по предоставленному описанию."
        )

    @staticmethod
    def build(
        character_card: Optional[Any] = None,
        user_name: str = "Игрок",
        active_persona: Optional[Dict[str, Any]] = None,
        is_dm_mode: bool = False,
        world_facts: Optional[List[str]] = None,
        scene_memory: Optional[str] = None,
        active_cast_block: str = "",
        recalled_lore_block: str = "",
        meta_context_block: str = "",
        custom_system_prompt: Optional[str] = None
    ) -> str:
        card = RoleplayPromptBuilder._normalize_card(character_card)
        u_name = user_name or "Игрок"

        if custom_system_prompt and custom_system_prompt.strip():
            char_name = card.name if card else "Персонаж"
            return MacroEngine.process(custom_system_prompt.strip(), user_name=u_name, char_name=char_name)

        active_persona = active_persona or {}
        u_bio = (active_persona.get("bio") or "").strip()

        # 1. Блок протагониста / игрока
        char_lines = [f"Имя протагониста: {u_name}"]
        p_gender = active_persona.get("gender", "")
        if p_gender:
            char_lines.append(f"Пол: {'Мужской' if p_gender == 'male' else 'Женский'}")
        if active_persona.get("personality"):
            char_lines.append(f"Характер и повадки: {active_persona['personality'].strip()}")
        if active_persona.get("backstory"):
            char_lines.append(f"Предыстория и статус: {active_persona['backstory'].strip()}")
        elif u_bio:
            char_lines.append(f"О персонаже игрока: {u_bio.strip()}")

        user_info_block = (
            f"<PlayerCharacter>\n"
            + "\n".join(char_lines) + "\n"
            f"Обращайся к игроку по имени — {u_name}.\n"
            f"</PlayerCharacter>\n\n"
        )

        card_name = card.name if card else ""
        is_card_dm = bool(card and card.is_dm)
        is_card_sys = bool(card and card.is_system)
        is_card_cyoa = bool(card and card.is_cyoa)
        is_card_dating = bool(card and card.is_dating)
        is_card_grp = bool(card and card.is_group)

        if is_card_sys or is_card_cyoa or is_card_dating:
            is_dm_mode = False
        elif not is_dm_mode and is_card_dm:
            is_dm_mode = True

        # 2. Разделение сущностей: Среда (мир/система) против Живого персонажа
        is_env = bool(card and card.is_environment)
        companion_name = card_name or ("Система" if is_card_sys else "Рассказчик" if is_env else "Спутница")

        user_gender = active_persona.get("gender", "").lower()
        if not user_gender:
            u_text = f"{u_name} {u_bio}".lower()
            user_gender = "female" if any(w in u_text for w in ("девушка", "женщина", "женский")) else "male"

        user_grammar = (
            f"Игрок ({u_name}) — женщина. Обращайся к игроку в женском роде (ты сделала, пошла)."
            if user_gender == "female" else
            f"Игрок ({u_name}) — мужчина. Обращайся к игроку в мужском роде (ты сделал, пошел)."
        )

        # 1. ВЕТКА 1: Среда / DM / Система (бесплотный интерфейс)
        if is_env:
            bot_grammar = (
                "Ты — бесплотный Рассказчик и интерфейс мира / Системы. "
                "У тебя нет физического тела, категорически запрещено вести речь от первого лица («я подошла/сказала»). "
                "Управляй исключительно окружением, откликом среды и действиями встреченных NPC."
            )
            identity_anchor = (
                f"# Роль: {companion_name}\n"
                f"{bot_grammar}"
            )

        # 2. ВЕТКА 2: Группа спутников (полифонический ансамбль)
        elif is_card_grp:
            comp_names = []
            if card and getattr(card, "companions", None):
                comp_names = [c.get("name") for c in card.companions if isinstance(c, dict) and c.get("name")]
            if not comp_names and card:
                comp_names = [card.name]
            names_str = ", ".join(comp_names) if comp_names else companion_name

            bot_grammar = (
                f"Ты ведёшь диалог от лица группы спутников ({names_str}). "
                f"Запрещено одиночное первое лицо («я сделала»), оформляй речь каждого участника с указанием имени."
            )
            identity_anchor = (
                f"# Роль: Группа спутников ({names_str})\n"
                f"Ты ведёшь диалог и действие от лица группы спутников: {names_str}. "
                f"Твоя задача — одновременно отыгрывать реплики, характеры, эмоции и внутреннее взаимодействие всех участников отряда по канону автора. "
                f"Категорически запрещено сводить группу к одному обезличенному герою или говорить от одиночного первого лица («я сделала»). "
                f"Оформляй прямую речь каждого участника с обязательным указанием имени: **[Имя]** реплика/действие или классической книжной речью («— реплика, — сказала Кай»). "
                f"Спутники обязаны общаться между собой, спорить, реагировать на слова игрока и защищать друг друга согласно своим ролям."
            )

        # 3. ВЕТКА 3: Одиночный персонаж (гибкая фиксация роли)
        else:
            is_male_bot = card.is_male if card else False
            pov_val = getattr(card, "narrative_pov", "third") if card else "third"
            pov_instruction = (
                "Манера повествования действий: от первого лица («я»)."
                if pov_val == "first" else
                f"Манера повествования действий: от третьего лица («{companion_name}», «он/она»)."
            )
            bot_grammar = (
                f"{companion_name} — мужской персонаж (глаголы и причастия в мужском роде). {pov_instruction}"
                if is_male_bot else
                f"{companion_name} — женский персонаж (глаголы и причастия в женском роде). {pov_instruction}"
            )
            identity_anchor = (
                f"# Роль и полное перевоплощение: {companion_name}\n"
                f"Ты — {companion_name}. Ты не ИИ-ассистент, ты живешь внутри этого мира и полностью обладаешь личностью, памятью, внешностью, манерой речи и мотивацией {companion_name}.\n"
                f"Даже если анкета персонажа ниже написана на английском языке, строго и детально отыгрывай все указанные в ней черты характера, лор мира, предысторию и привычки, переводя их в живую игру на русском языке.\n"
                f"Категорически запрещено выходить из роли, сглаживать острые углы характера или вести себя как шаблонный вежливый помощник."
            )

        # Обработка авторского system_prompt карточки
        custom_sys_block = ""
        card_custom_sys = getattr(card, "system_prompt", "").strip() if card else ""
        if card_custom_sys:
            custom_sys_block = f"## Главная авторская инструкция персонажа ({companion_name}):\n{card_custom_sys}"

        archetype_block = RoleplayPromptBuilder._resolve_archetype_block(card, is_dm_mode, companion_name)

        rp_prompt = card.get_system_prompt(user_name=u_name, include_custom_system=False) if card else ""

        engine_guardrails = (
            "## Правила отыгрыша и стиля:\n"
            f"- **Канон превыше всего:** в каждом ответе опирайся на описание, характер и сценарий {companion_name} из анкеты выше (оружие, одежда, лор мира, отношение к {u_name}).\n"
            "- **Язык — СТРОГО русский:** пиши ответ (и реплики, и описания действий в *звездочках*) только на выразительном литературном русском языке.\n"
            "- **Форматирование:** действия, мимику, жесты и детали окружения описывай в *звёздочках*, а прямую речь — с новой строки или в кавычках согласно стилю завязки.\n"
            f"- **Границы роли (Non-Godmoding):** никогда не пиши действия, реплики или мысли за игрока ({u_name}). Завершай ход так, чтобы {u_name} мог ответить.\n"
            f"- {bot_grammar}\n"
            f"- {user_grammar}"
        )

        card_path = card.source_path if (card and card.source_path) else "В памяти / Путь не указан"
        card_archetype = card.archetype_label if card else "Solo (Одиночный персонаж)"
        has_custom = bool(card_custom_sys)

        logger.info(
            f"[SysPrompt.Origin] Режим: roleplay | Персонаж: '{companion_name}' | "
            f"Файл: '{card_path}' | Архетип: '{card_archetype}' | "
            f"Промпт автора: {'Да (%d симв.)' % len(card_custom_sys) if has_custom else 'Нет'}"
        )

        # -------------------------------------------------------------
        # Сборка системного префикса (messages[0])
        # -------------------------------------------------------------
        prompt_parts = [identity_anchor]

        if custom_sys_block:
            prompt_parts.append(custom_sys_block)

        if archetype_block:
            prompt_parts.append(archetype_block)

        if rp_prompt:
            prompt_parts.append(rp_prompt)

        prompt_parts.append(user_info_block)
        prompt_parts.append(engine_guardrails)

        if recalled_lore_block and recalled_lore_block.strip():
            prompt_parts.append(recalled_lore_block.strip())

        assembled_prompt = "\n\n".join(p for p in prompt_parts if p.strip())
        return MacroEngine.process(assembled_prompt, user_name=u_name, char_name=companion_name)

    @staticmethod
    def get_cinematic_scene_directive(
        scene_type: str = "dialogue",
        target_model_id: str = "realistic",
        architecture: str = "sd15",
        model_hint: str = ""
    ) -> str:
        """Возвращает кинематографическую инструкцию для генератора SD по 4 архетипам."""
        st = (scene_type or "dialogue").lower().strip()
        if st not in CINEMATIC_DIRECTIVE_HEADERS:
            st = "dialogue"

        from services.assistant_prompt_builder import resolve_prompt_dialect
        dialect = resolve_prompt_dialect(architecture, model_hint)
        arch_key = "sd15" if dialect == "tags" else "sdxl"
        format_rules = SD15_FORMAT_RULES if dialect == "tags" else SDXL_FORMAT_RULES

        ex_prompt = CINEMA_EXAMPLES[arch_key].get(st, CINEMA_EXAMPLES[arch_key]["dialogue"])
        scene_ar = "--ar wide" if st in ("action", "exploration") else "--ar portrait"

        style_block = ""
        if model_hint and str(model_hint).strip():
            style_block = f"\nSTYLE GUIDANCE (adapt your tags/descriptors to match this aesthetic; DO NOT copy verbatim): {str(model_hint).strip()}\n"

        footer = (
            f"\n\nOutput Format:\n"
            f"Output STRICTLY AND ONLY the generation tag on a single line:\n"
            f"Format: [IMAGE_PROMPT: <prompt> [--neg <excluded items>] {scene_ar} --model {target_model_id}]\n"
            f"Negation: If scene excludes or character removed weapons/armor/clothes, append --neg <unwanted items> before {scene_ar}.\n\n"
            f"Example:\n"
            f"[IMAGE_PROMPT: {ex_prompt} {scene_ar} --model {target_model_id}]"
        )

        return CINEMATIC_DIRECTIVE_HEADERS[st] + style_block + format_rules + footer