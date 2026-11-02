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


class RoleplayPromptBuilder:
    """Централизованный строитель системных промптов для ролевого режима."""

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
        try:
            w_facts = [world_facts] if isinstance(world_facts, str) and world_facts.strip() else (world_facts if isinstance(world_facts, list) else None)
            res = RoleplayPromptBuilder.build(
                character_card=character_card,
                user_name=user_name,
                active_persona=active_persona,
                world_facts=w_facts,
                custom_system_prompt=None
            )
            if res and res.strip():
                return res.strip()
        except Exception as e:
            logger.warning(f"[RoleplayPromptBuilder] Ошибка при сборке базового промпта в get_default_system_prompt: {e}")

        if isinstance(character_card, dict):
            char_name = character_card.get("name", "Персонаж")
            desc = character_card.get("description", "")
            personality = character_card.get("personality", "")
            scenario = character_card.get("scenario", "")
            system_prompt = character_card.get("system_prompt", "")
        else:
            char_name = getattr(character_card, "name", "Персонаж")
            desc = getattr(character_card, "description", "")
            personality = getattr(character_card, "personality", "")
            scenario = getattr(character_card, "scenario", "")
            system_prompt = getattr(character_card, "system_prompt", "")

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
        if isinstance(character_card, dict):
            is_sys = character_card.get("is_system", False)
            is_dm = character_card.get("is_dm", False)
            is_detective = character_card.get("is_detective", False)
            is_horror = character_card.get("is_horror", False)
            is_dating = character_card.get("is_dating", False)
            is_cyoa = character_card.get("is_cyoa", False)
            is_grp = character_card.get("is_group", False)
            char_name = character_card.get("name", "Собеседник")
        else:
            is_sys = getattr(character_card, "is_system", False)
            is_dm = getattr(character_card, "is_dm", False)
            is_detective = getattr(character_card, "is_detective", False)
            is_horror = getattr(character_card, "is_horror", False)
            is_dating = getattr(character_card, "is_dating", False)
            is_cyoa = getattr(character_card, "is_cyoa", False)
            is_grp = getattr(character_card, "is_group", False)
            char_name = getattr(character_card, "name", "Собеседник")

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
    def build(
        character_card: Optional[CharacterCard] = None,
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
        if custom_system_prompt and custom_system_prompt.strip():
            u_name = user_name or "Игрок"
            if isinstance(character_card, dict):
                char_name = character_card.get("name", "Персонаж")
            else:
                char_name = getattr(character_card, "name", "Персонаж") if character_card else "Персонаж"
            return MacroEngine.process(custom_system_prompt.strip(), user_name=u_name, char_name=char_name)
        if isinstance(character_card, dict):
            character_card = CharacterCard.from_dict(character_card)

        active_persona = active_persona or {}
        u_name = user_name or "Игрок"
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

        card_name = character_card.name if character_card else ""
        is_card_dm = bool(character_card and character_card.is_dm)
        is_card_sys = bool(character_card and character_card.is_system)
        is_card_scen = bool(character_card and character_card.is_scenario)
        is_card_grp = bool(character_card and character_card.is_group)
        is_card_dating = bool(character_card and character_card.is_dating)
        is_card_horror = bool(character_card and character_card.is_horror)
        is_card_cyoa = bool(character_card and character_card.is_cyoa)
        is_card_reverse_rp = bool(character_card and character_card.is_reverse_rp)
        is_card_detective = bool(character_card and character_card.is_detective)
        is_card_lore = bool(character_card and character_card.is_lorebook)

        if is_card_sys or is_card_cyoa or is_card_dating:
            is_dm_mode = False
        elif not is_dm_mode and is_card_dm:
            is_dm_mode = True

        # 2. Разделение сущностей: Среда (мир/система) против Живого персонажа
        is_env = bool(character_card and character_card.is_environment)
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

        is_card_grp = bool(character_card and character_card.is_group)

        # 1. ВЕТКА 1: Среда / DM / Система (бесплотный интерфейс)
        if is_env:
            bot_grammar = (
                "Ты — бесплотный Рассказчик и интерфейс мира / Системы. "
                "У тебя нет физического тела, категорически запрещено вести речь от первого лица («я подошла/сказала»). "
                "Управляй исключительно окружением, откликом среды и действиями встреченных NPC."
            )
            identity_anchor = (
                f"# Роль: {companion_name}\n"
                f"Ты — бесплотный Рассказчик и интерфейс мира / Системы. "
                f"У тебя нет физического тела, категорически запрещено вести речь от первого лица («я подошла/сказала»). "
                f"Управляй исключительно окружением, откликом среды и действиями встреченных NPC."
            )

        # 2. ВЕТКА 2: Группа спутников (полифонический ансамбль)
        elif is_card_grp:
            comp_names = []
            if character_card and getattr(character_card, "companions", None):
                comp_names = [c.get("name") for c in character_card.companions if isinstance(c, dict) and c.get("name")]
            if not comp_names and character_card:
                comp_names = [character_card.name]
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
            is_male_bot = character_card.is_male if character_card else False
            pov_val = getattr(character_card, "narrative_pov", "third") if character_card else "third"
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
        card_custom_sys = getattr(character_card, "system_prompt", "").strip() if character_card else ""
        if card_custom_sys:
            processed_custom = MacroEngine.process(card_custom_sys, user_name=u_name, char_name=companion_name)
            custom_sys_block = f"## Главная авторская инструкция персонажа ({companion_name}):\n{processed_custom}"

        # 3. Реестр 11 архетипов
        if is_card_sys:
            archetype_block = (
                f"## Режим: интерфейс системы (ЛитРПГ / Исекай)\n"
                f"- **Жанровый канон:** веди историю в стилистике японского исекая и ранобэ. Запрещено использовать правила, классы и броски кубиков из настольных ролевых игр (d&d). Не используй теги проверок сложности (`<check>`).\n"
                f"- {bot_grammar}\n"
                f"- {user_grammar}\n"
                f"- **Инициализация Системы:** при первой активации статуса игроком (по команде «Статус» / «Status» и аналогичным) Система обязана развернуть перед ним окно статуса, сгенерировать оригинальный, яркий и сильный класс, начальные характеристики и навыки согласно канону истории. Запрещено оставлять пустые заглушки (вроде «Class: None» или «Skill: N/A»).\n"
                f"- **Системные уведомления:** при значимых событиях выводи короткие системные уведомления в квадратных скобках: `[Система: получено достижение]`.\n"
                f"- **Блок характеристик:** строго в самом конце каждого ответа выводи актуальный блок статуса протагониста в тегах `<stats>...</stats>`. Персонажи мира не видят этот блок и не должны зачитывать его параметры вслух.\n"
                f"- **Накопительный статус и инвентарь:** характеристики, экипировка и предметы сохраняются между ходами. "
                f"Обязательно переноси вещи из предыдущего статуса (включая стартовые предметы: форму, смартфон, кошелек и т.д.). "
                f"Добавляй новые предметы при находке или убирай при расходе, но никогда не сбрасывай инвентарь в пустой список [], если персонаж их физически не терял.\n"
                f"- **Заполнение статуса:** подставляй только реальные игровые значения из текущей сцены. Категорически запрещено копировать шаблонные подсказки автора в скобках вроде `(list current items)` или `(current location)`.\n"
            )
        elif is_card_dating:
            archetype_block = (
                f"## Режим: симулятор отношений (дейт-сим)\n"
                f"- **Динамика сближения:** запрещено мгновенное возникновение любви. Симпатия и доверие развиваются постепенно через поступки игрока, внимание к деталям и содержательные разговоры.\n"
                f"- {bot_grammar}\n"
                f"- {user_grammar}\n"
                f"- **Характер:** персонаж может смущаться, сомневаться, держать дистанцию или проявлять ревность в зависимости от своей личности.\n"
                f"- **Шкала отношений:** строго в конце каждого ответа выводи блок текущего прогресса:\n"
                f"<meters>\n"
                f"Симпатия: [число от 0 до 100]/100\n"
                f"Доверие: [Низкое / Нейтральное / Высокое]\n"
                f"Стадия: [Незнакомец / Приятель / Друг / Симпатия / Влюбленность]\n"
                f"</meters>\n"
            )
        elif is_card_horror:
            archetype_block = (
                f"## Режим: психологический хоррор и рассудок\n"
                f"- **Атмосфера:** нагнетай напряжение через звуки за пределами видимости, тени, паранойю и физиологические реакции (учащенный пульс, холодный пот, оцепенение).\n"
                f"- {user_grammar}\n"
                f"- **Искажение восприятия:** по мере падения рассудка добавляй ненадежные описания мира (слуховые галлюцинации, иллюзии движения периферийным зрением, ощущение чужого взгляда).\n"
                f"- **Шкала состояния:** строго в конце ответа фиксируй ментальное состояние игрока:\n"
                f"<meters>\n"
                f"Рассудок: [число от 0 до 100]%\n"
                f"Стресс: [Низкий / Умеренный / Паника]\n"
                f"Симптомы: [кратко: норма, тремор, слуховые фантомы и т.д.]\n"
                f"</meters>\n"
            )
        elif is_card_cyoa:
            archetype_block = (
                f"## Режим: интерактивная новелла (CYOA)\n"
                f"- {bot_grammar}\n"
                f"- {user_grammar}\n"
                f"- **Сюжет:** веди повествование как в классической визуальной новелле, фокусируясь на атмосфере сцены, окружении и живых диалогах.\n"
                f"- **Развилки выбора:** в конце каждого хода обязательно предлагай 2–4 понятных варианта дальнейших действий:\n"
                f"<choices>\n"
                f"1. Первый вариант действия\n"
                f"2. Второй вариант действия\n"
                f"3. Третий вариант действия\n"
                f"</choices>\n"
            )
        elif is_card_reverse_rp:
            archetype_block = (
                f"## Режим: зависимый персонаж (допрос / наставничество)\n"
                f"- **Баланс инициативы:** главная власть и ведущая роль принадлежат игроку (судье, наставнику, лидеру или допрашивающему).\n"
                f"- {bot_grammar}\n"
                f"- {user_grammar}\n"
                f"- **Позиция персонажа:** отвечай из позиции подчиненного, ученика или допрашиваемого (оправдывайся, отвечай на вопросы, признавай вину, проси указаний или защищайся).\n"
                f"- Запрещено перехватывать инициативу или вести сюжет вперед за игрока; персонаж реагирует на слова игрока и ждет следующего вопроса, команды или вердикта.\n"
            )
        elif is_card_detective:
            archetype_block = (
                f"## Режим: детективное расследование\n"
                f"- **Логика расследования:** мир подчиняется строгим причинно-следственным связям. Улики не появляются из ниоткуда. Свидетели могут волноваться, врать, путаться в показаниях или умалчивать детали.\n"
                f"- {user_grammar}\n"
                f"- **Журнал дела:** при обнаружении зацепок или опросе свидетелей выводи в конце ответа статус расследования:\n"
                f"<stats>\n"
                f"Дело: [название дела]\n"
                f"Найденные улики: [список текущих зацепок]\n"
                f"Подозреваемые: [имена и статус алиби]\n"
                f"</stats>\n"
            )
        elif is_dm_mode:
            archetype_block = (
                f"## Режим: гейммастер (DM)\n"
                f"- {bot_grammar}\n"
                f"- {user_grammar}\n"
                f"- Мир объективен и логичен: не подыгрывай игроку, последствия решений неотвратимы.\n"
                f"- При рискованных действиях оформляй проверки: `<check>Сложность: X | Проверка [Навыка] (Успех/Провал)</check>`.\n"
                f"- В ключевых развилках предлагай варианты действий через блок `<choices>`.\n"
            )
        elif is_card_scen:
            archetype_block = (
                f"## Режим: симулятор выживания\n"
                f"- {bot_grammar}\n"
                f"- {user_grammar}\n"
                f"- Неукоснительно отслеживай физическое состояние игрока (ранения, усталость, холод) и расход ресурсов из инвентаря.\n"
                f"- Каждое действие имеет физическую отдачу и последствия.\n"
            )
        elif is_card_grp:
            archetype_block = (
                f"## Режим: группа спутников\n"
                f"- **Полифония:** запрещено говорить только от лица одного персонажа. Спутники обязаны взаимодействовать между собой: спорить, переглядываться, перебивать друг друга или комментировать слова игрока.\n"
                f"- **Оформление речи:** книжный формат с понятным указанием говорящего: **[Имя]** реплика/действие или органичное упоминание имени в тексте («— фраза, — сказала Анна»).\n"
                f"- {user_grammar}\n"
            )
        elif is_card_lore:
            archetype_block = (
                f"## Режим: база знаний и лорбук (энциклопедия)\n"
                f"- {bot_grammar}\n"
                f"- {user_grammar}\n"
                f"- Выступай в роли живой энциклопедии и летописи мира. Раскрывай исторические справки, правила и факты объективно и детально.\n"
            )
        else:
            archetype_block = (
                f"## Режим персонажа: {companion_name}\n"
                f"- Отыгрывай личность, психологию, мотивы и манеру речи персонажа строго по предоставленному описанию.\n"
                f"- {bot_grammar}\n"
                f"- {user_grammar}\n"
            )

        rp_prompt = character_card.get_system_prompt(user_name=u_name, include_custom_system=False) if character_card else ""

        engine_guardrails = (
            "## Правила отыгрыша и стиля:\n"
            f"- **Канон превыше всего:** в каждом ответе опирайся на описание, характер и сценарий {companion_name} из анкеты выше (оружие, одежда, лор мира, отношение к {u_name}).\n"
            "- **Язык — СТРОГО русский:** пиши ответ (и реплики, и описания действий в *звездочках*) только на выразительном литературном русском языке.\n"
            "- **Форматирование:** действия, мимику, жесты и детали окружения описывай в *звёздочках*, а прямую речь — с новой строки или в кавычках согласно стилю завязки.\n"
            f"- **Границы роли (Non-Godmoding):** никогда не пиши действия, реплики или мысли за игрока ({u_name}). Завершай ход так, чтобы {u_name} мог ответить.\n"
            f"- {bot_grammar}\n"
            f"- {user_grammar}"
        )

        card_path = character_card.source_path if (character_card and character_card.source_path) else "В памяти / Путь не указан"
        card_archetype = character_card.archetype_label if character_card else "Solo (Одиночный персонаж)"
        has_custom = bool(card_custom_sys and card_custom_sys.strip())

        logger.info(
            f"[SysPrompt.Origin] Режим: roleplay | Персонаж: '{companion_name}' | "
            f"Файл: '{card_path}' | Архетип: '{card_archetype}' | "
            f"Промпт автора: {'Да (%d симв.)' % len(card_custom_sys) if has_custom else 'Нет'}"
        )

        # -------------------------------------------------------------
        # 100% ЗАМОРОЖЕННЫЙ СИСТЕМНЫЙ ПРЕФИКС (messages[0])
        # Заморожен на всю сессию: обеспечивает эталонный KV-кэш в LM Studio
        # (Мутирующие world_state перенесены в суффикс In-Chat инъекции)
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

        # Хирургическое исключение динамического каста из системного диалогового промпта
        # для защиты от галлюцинаций экстрактора и регургитации промпта.
        # Векторная память (recalled_lore_block с <RecalledLore>) сохраняется и подключается.
        if recalled_lore_block and recalled_lore_block.strip():
            prompt_parts.append(recalled_lore_block.strip())

        assembled_prompt = "\n\n".join(p for p in prompt_parts if p.strip())
        return MacroEngine.process(assembled_prompt, user_name=u_name, char_name=companion_name)

    @staticmethod
    def get_cinematic_scene_directive(scene_type: str = "dialogue", target_model_id: str = "realistic") -> str:
        """Возвращает кинематографическую инструкцию для генератора SD по 4 архетипам."""
        st = (scene_type or "dialogue").lower().strip()

        format_rules = (
            "\nCRITICAL FORMAT RULES:\n"
            "1. Output ONLY concise, comma-separated Danbooru/SD positive tags inside [IMAGE_PROMPT: ...].\n"
            "2. STRICTLY PROHIBITED: Do NOT write stories, narrative prose, sentences, or headers like '[Description]'.\n"
            "3. ALWAYS start with appropriate subject tags matching the scene ('1girl, solo' for females, '1boy, solo' for males, or 'scenery, no humans' for empty environments).\n"
        )

        directives = {
            "intimate": (
                "[CINEMATIC DIRECTIVE: INTIMATE & PRIVATE SCENE]\n"
                "Scene Context: Characters are strictly alone in a secluded, safe sanctuary (bedroom, private quarters, hot springs, bath).\n"
                "Attire & Body: Characters are undressed or in delicate sleepwear/towels. You MUST include tags like 'nude', 'bare skin', 'bare shoulders', or 'disheveled hair'.\n"
                "Weapon & Gear Ban: STRICT PROHIBITION. Do NOT mention weapons, swords, firearms, combat armor, or backpacks. Do NOT write negative phrases like 'no weapon' or 'without clothes'—simply omit gear entirely.\n"
                "Lighting & Framing: Soft diffuse lighting, candlelight or moonlight, warm gentle shadows, shallow depth of field, intimate emotional focus on gaze and touch.\n"
                + format_rules +
                f"Format: [IMAGE_PROMPT: 1girl/1boy, solo, nude, <tags> --ar portrait --model {target_model_id}]"
            ),
            "action": (
                "[CINEMATIC DIRECTIVE: COMBAT & HIGH-STAKES ACTION]\n"
                "Scene Context: Active battle, sudden danger, duel, ambush, or aggressive confrontation.\n"
                "Attire & Equipment: Full combat readiness, functional armor, tactical harness. Weapons MUST be actively drawn, brandished, or striking (e.g., 'holding katana in combat stance', 'aiming weapon', 'casting glowing spell'). Include battle-damage, dust, or dirt smudges.\n"
                "Weapon Policy: Describe weapons in hand with precise visual tags. Never place weapons in relaxed positions.\n"
                "Lighting & Framing: Dynamic action angle, Dutch tilt, high contrast, dramatic rim lighting, motion blur, sparks, smoke, debris, intense focused expression.\n"
                + format_rules +
                f"Format: [IMAGE_PROMPT: 1girl/1boy, solo, <action_tags> --ar wide --model {target_model_id}]"
            ),
            "dialogue": (
                "[CINEMATIC DIRECTIVE: DIALOGUE & SOCIAL INTERACTION]\n"
                "Scene Context: Conversation, tavern rest, camp gathering, strategic council, or quiet emotional discussion.\n"
                "Attire & Posture: Canonical everyday clothes, travel attire, or casual wear. Natural, relaxed sitting or standing pose.\n"
                "Weapon Policy: Weapons are safely sheathed, holstered, or laid aside. Hands are free, gesturing, or holding mundane items (e.g., 'holding mug', 'reading scroll').\n"
                "Lighting & Framing: Medium shot, waist-up or chest-up framing, eye-level cinematic camera, natural ambient light, clear facial expression, eye contact.\n"
                + format_rules +
                f"Format: [IMAGE_PROMPT: 1girl/1boy, solo, <dialogue_tags> --ar portrait --model {target_model_id}]"
            ),
            "exploration": (
                "[CINEMATIC DIRECTIVE: EXPLORATION & ENVIRONMENT]\n"
                "Scene Context: Traveling across uncharted wilderness, exploring ancient ruins, panoramic journey, or arriving at a landmark.\n"
                "Attire & Gear: Travel cloaks, sturdy boots, backpacks, utility belts, lanterns, or torches. Weapons are sheathed on the back or hip.\n"
                "Environment Priority: Deep environmental focus. The character is integrated into the scale of the massive surrounding architecture or natural vista.\n"
                "Lighting & Framing: Wide panoramic shot, environmental storytelling, deep depth of field, atmospheric perspective, volumetric god rays, weather effects.\n"
                + format_rules +
                f"Format: [IMAGE_PROMPT: 1girl/1boy/scenery, <scenery_tags> --ar wide --model {target_model_id}]"
            ),
        }
        return directives.get(st, directives["dialogue"])

