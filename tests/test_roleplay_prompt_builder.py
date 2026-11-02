"""
Тесты для RoleplayPromptBuilder (services/roleplay_prompt_builder.py).
Проверка согласования грамматики (полов игрока и бота), 11 жанровых архетипов,
авторских инструкций, макросов и защиты от годмодинга (Non-Godmoding).
"""

import pytest
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.tavern_parser import CharacterCard


class TestRoleplayPromptBuilderMacrosAndPersona:
    """Проверка макросов и интеграции персонажа игрока."""

    def test_process_macros_standalone(self):
        text = "{{char}} осторожно смотрит на {{user}}."
        result = RoleplayPromptBuilder.process_macros(text, user_name="Роман", char_name="Селеста")
        assert result == "Селеста осторожно смотрит на Роман."

    def test_user_persona_block_assembly(self):
        persona = {
            "gender": "male",
            "personality": "Спокойный наемник",
            "backstory": "Бывший капитан стражи"
        }
        prompt = RoleplayPromptBuilder.build(
            character_card=None,
            user_name="Роман",
            active_persona=persona
        )

        assert "<PlayerCharacter>" in prompt
        assert "Имя протагониста: Роман" in prompt
        assert "Пол: Мужской" in prompt
        assert "Характер и повадки: Спокойный наемник" in prompt
        assert "Предыстория и статус: Бывший капитан стражи" in prompt
        assert "Обращайся к игроку по имени — Роман." in prompt
        assert "Игрок (Роман) — мужчина. Обращайся к игроку в мужском роде" in prompt


class TestGrammarAndPOVAgreement:
    """Проверка грамматического согласования родов и стиля повествования."""

    def test_female_user_and_male_bot_first_person(self):
        card = CharacterCard(
            name="Алекс",
            is_male=True,
            first_mes="*Я подошел к костру и сел рядом.*"  # Маркер 1-го лица
        )
        persona = {"gender": "female"}

        prompt = RoleplayPromptBuilder.build(
            character_card=card,
            user_name="Анна",
            active_persona=persona
        )

        # Проверка пола игрока
        assert "Игрок (Анна) — женщина. Обращайся к игроку в женском роде" in prompt
        # Проверка пола бота и POV
        assert "Алекс — мужской персонаж (глаголы и причастия в мужском роде)." in prompt
        assert "Манера повествования действий: от первого лица («я»)." in prompt

    def test_male_user_and_female_bot_third_person(self):
        card = CharacterCard(
            name="Селеста",
            is_male=False,
            first_mes="*Селеста оглядела библиотеку.*"  # Маркер 3-го лица
        )
        persona = {"gender": "male"}

        prompt = RoleplayPromptBuilder.build(
            character_card=card,
            user_name="Роман",
            active_persona=persona
        )

        assert "Игрок (Роман) — мужчина. Обращайся к игроку в мужском роде" in prompt
        assert "Селеста — женский персонаж (глаголы и причастия в женском роде)." in prompt
        assert "Манера повествования действий: от третьего лица («Селеста», «он/она»)." in prompt

    def test_third_person_scene_grammar(self):
        card = CharacterCard(name="Система", is_system=True)
        persona_f = {"gender": "female"}
        prompt_f = RoleplayPromptBuilder.build(
            character_card=card,
            user_name="Анна",
            active_persona=persona_f
        )
        assert "Игрок (Анна) — женщина. Обращайся к игроку в женском роде" in prompt_f

        persona_m = {"gender": "male"}
        prompt_m = RoleplayPromptBuilder.build(
            character_card=card,
            user_name="Роман",
            active_persona=persona_m
        )
        assert "Игрок (Роман) — мужчина. Обращайся к игроку в мужском роде" in prompt_m

    def test_depth_anchor_generation(self):
        # 1. LitRPG / System -> <stats>
        card_sys = CharacterCard(name="Система", is_system=True)
        anchor_sys = RoleplayPromptBuilder.build_depth_anchor(card_sys, user_name="Игрок", user_query="Статус")
        assert "<stats>" in anchor_sys and "</stats>" in anchor_sys

        # 2. Detective -> <stats>
        card_det = CharacterCard(name="Лестрейд", is_detective=True)
        anchor_det = RoleplayPromptBuilder.build_depth_anchor(card_det, user_name="Холмс", user_query="Где улика?")
        assert "<stats>" in anchor_det and "</stats>" in anchor_det

        # 3. Dating -> <meters>
        card_dat = CharacterCard(name="Мика", is_dating=True)
        anchor_dat = RoleplayPromptBuilder.build_depth_anchor(card_dat, user_name="Игрок", user_query="Привет")
        assert "<meters>" in anchor_dat and "симпатии и доверия" in anchor_dat

        # 4. Horror -> <meters>
        card_hor = CharacterCard(name="Нечто", is_horror=True)
        anchor_hor = RoleplayPromptBuilder.build_depth_anchor(card_hor, user_name="Игрок", user_query="Кто здесь?")
        assert "<meters>" in anchor_hor and "рассудка и стресса" in anchor_hor

        # 5. DM / CYOA -> <choices>
        card_dm = CharacterCard(name="ГМ", is_dm=True)
        anchor_dm = RoleplayPromptBuilder.build_depth_anchor(card_dm, user_name="Игрок", user_query="Иду на север")
        assert "<choices>" in anchor_dm

        # 6. Group
        card_grp = CharacterCard(name="Отряд", is_group=True)
        anchor_grp = RoleplayPromptBuilder.build_depth_anchor(card_grp, user_name="Командир", user_query="В атаку!")
        assert "Выдели одного спутника как инициатора ответа" in anchor_grp

        # 7. Solo
        card_solo = CharacterCard(name="Селеста")
        anchor_solo = RoleplayPromptBuilder.build_depth_anchor(card_solo, user_name="Игрок", user_query="Привет")
        assert "передай мимику, жесты и подтекст" in anchor_solo



class TestArchetypePromptGeneration:
    """Проверка генерации специальных блоков для всех ключевых архетипов."""

    def test_litrpg_system_archetype(self):
        card = CharacterCard(name="Система", is_system=True)
        prompt = RoleplayPromptBuilder.build(character_card=card, user_name="Игрок")

        assert "## Режим: интерфейс системы (ЛитРПГ / Исекай)" in prompt
        assert "Ты — бесплотный Рассказчик и интерфейс мира / Системы." in prompt
        assert "Запрещено использовать правила, классы и броски кубиков из настольных ролевых игр (d&d)" in prompt
        assert "<stats>...</stats>" in prompt
        assert "[Система: получено достижение]" in prompt

    def test_dating_sim_archetype(self):
        card = CharacterCard(name="Мика", is_dating=True)
        prompt = RoleplayPromptBuilder.build(character_card=card, user_name="Игрок")

        assert "## Режим: симулятор отношений (дейт-сим)" in prompt
        assert "запрещено мгновенное возникновение любви" in prompt
        assert "<meters>" in prompt
        assert "Симпатия: [число от 0 до 100]/100" in prompt
        assert "Стадия: [Незнакомец / Приятель / Друг / Симпатия / Влюбленность]" in prompt

    def test_horror_archetype(self):
        card = CharacterCard(name="Тень", is_horror=True)
        prompt = RoleplayPromptBuilder.build(character_card=card, user_name="Игрок")

        assert "## Режим: психологический хоррор и рассудок" in prompt
        assert "Рассудок: [число от 0 до 100]%" in prompt
        assert "Стресс: [Низкий / Умеренный / Паника]" in prompt

    def test_cyoa_archetype(self):
        card = CharacterCard(name="Новелла", is_cyoa=True)
        prompt = RoleplayPromptBuilder.build(character_card=card, user_name="Игрок")

        assert "## Режим: интерактивная новелла (CYOA)" in prompt
        assert "<choices>" in prompt
        assert "1. Первый вариант действия" in prompt

    def test_reverse_rp_archetype(self):
        card = CharacterCard(name="Пленник", is_reverse_rp=True)
        prompt = RoleplayPromptBuilder.build(character_card=card, user_name="Инквизитор")

        assert "## Режим: зависимый персонаж (допрос / наставничество)" in prompt
        assert "главная власть и ведущая роль принадлежат игроку" in prompt
        assert "Запрещено перехватывать инициативу" in prompt

    def test_detective_archetype(self):
        card = CharacterCard(name="Детектив", is_detective=True)
        prompt = RoleplayPromptBuilder.build(character_card=card, user_name="Следователь")

        assert "## Режим: детективное расследование" in prompt
        assert "Дело: [название дела]" in prompt
        assert "Найденные улики: [список текущих зацепок]" in prompt

    def test_group_companions_archetype(self):
        card = CharacterCard(
            name="Отряд",
            is_group=True,
            companions=[{"name": "Лия"}, {"name": "Висп"}]
        )
        prompt = RoleplayPromptBuilder.build(character_card=card, user_name="Командир")

        assert "## Режим: группа спутников" in prompt
        assert "Группа спутников (Лия, Висп)" in prompt
        assert "Запрещено одиночное первое лицо («я сделала»)" in prompt
        assert "**[Имя]** реплика/действие" in prompt

    def test_environment_and_dm_mode(self):
        card = CharacterCard(name="Мир Пустоши", is_scenario=True)
        prompt = RoleplayPromptBuilder.build(character_card=card, user_name="Выживший")

        assert "## Режим: симулятор выживания" in prompt
        assert "Ты — бесплотный Рассказчик и интерфейс мира / Системы." in prompt
        assert "У тебя нет физического тела, категорически запрещено вести речь от первого лица" in prompt


class TestCustomInstructionsAndGuardrails:
    """Проверка авторских инструкций и правил безопасности отыгрыша."""

    def test_author_system_prompt_macro_expansion(self):
        card = CharacterCard(
            name="Мастер",
            system_prompt="Помни: {{char}} всегда защищает {{user}} любой ценой."
        )
        prompt = RoleplayPromptBuilder.build(character_card=card, user_name="Роман")

        assert "## Главная авторская инструкция персонажа (Мастер):" in prompt
        assert "Помни: Мастер всегда защищает Роман любой ценой." in prompt

    def test_non_godmoding_guardrail(self):
        card = CharacterCard(name="Алиса")
        prompt = RoleplayPromptBuilder.build(character_card=card, user_name="Роман")

        assert "## Правила отыгрыша и стиля:" in prompt
        assert "Границы роли (Non-Godmoding):" in prompt
        assert "никогда не пиши действия, реплики или мысли за игрока (Роман)." in prompt
        assert "Язык — СТРОГО русский" in prompt

    def test_dict_input_auto_conversion(self):
        raw_dict = {
            "name": "Тестовый Бот",
            "description": "Описание из словаря",
            "is_dating": True
        }
        # Передаем обычный словарь вместо инстанса CharacterCard
        prompt = RoleplayPromptBuilder.build(character_card=raw_dict, user_name="Игрок")
        assert "## Режим: симулятор отношений (дейт-сим)" in prompt
        assert "Тестовый Бот" in prompt