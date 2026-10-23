import pytest
import logging
from unittest.mock import patch

from services.session_manager import SessionManager
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.tavern_parser import CharacterCard
from services.macro_engine import MacroEngine


def test_roleplay_prompt_builder_macro_replacement():
    """Проверяет замену макросов {{user}} и {{char}} ДО расчета токенов в RoleplayPromptBuilder.build."""
    card = CharacterCard(
        name="Алиса",
        description="Верная спутница {{user}}, всегда рядом с {{user}}.",
        personality="Характер {{char}} добрый и отзывчивый.",
        scenario="Мир Киберпанка, где {{char}} встречает {{user}}.",
        mes_example="<START>\n{{user}}: Привет!\n{{char}}: Рада тебя видеть, {{user}}!",
        system_prompt="Помни, {{char}}, ты обязана защищать {{user}}."
    )

    prompt = RoleplayPromptBuilder.build(
        character_card=card,
        user_name="Иван",
        active_persona={"name": "Иван", "gender": "male"}
    )

    # Все макросы должны быть заменены
    assert "{{user}}" not in prompt
    assert "{{char}}" not in prompt
    assert "<USER>" not in prompt
    assert "<CHAR>" not in prompt
    assert "Иван" in prompt
    assert "Алиса" in prompt
    assert "--- [Пример сцены] ---" in prompt


def test_three_anchors_short_history_no_eviction():
    """
    Если история <= 14 сообщений: вытеснение не требуется,
    вся история возвращается целиком, если укладывается в бюджет.
    """
    card = CharacterCard(
        name="Каэли",
        first_mes="*Каэли приветствует {{user}}.* Здравствуй, путник!"
    )

    history = [
        {"role": "assistant", "content": "*Каэли приветствует Ивана.* Здравствуй, путник!"}
    ]
    for i in range(1, 12):
        role = "user" if i % 2 == 1 else "assistant"
        history.append({"role": role, "content": f"Реплика #{i}"})

    assert len(history) == 12  # <= 14

    messages = SessionManager.get_lm_studio_messages(
        history=history,
        is_roleplay=True,
        character_card=card,
        user_name="Иван",
        max_context_tokens=24000
    )

    # messages[0] = system_prompt
    assert messages[0]["role"] == "system"
    # messages[1] = first_message (канонический first_mes)
    assert messages[1]["role"] == "assistant"
    assert "Каэли приветствует Ивана" in messages[1]["content"]

    # Все 12 сообщений истории сохранены в том же хронологическом порядке
    assert len(messages) == 1 + 12
    for i in range(1, 12):
        assert f"Реплика #{i}" in messages[1 + i]["content"]


def test_three_anchors_middle_eviction_on_token_overflow():
    """
    Проверяет архитектуру Трёх Якорей на длинной сессии (50+ ходов):
    Якорь 1 (system_prompt): messages[0] — сохраняется.
    Якорь 2 (first_message): messages[1] — сохраняется (канонический first_mes).
    Якорь 3 (recent_tail): последние 12 сообщений — сохраняются.
    Средняя зона (middle_messages): вытесняется по одному самому старому сообщению.
    """
    card = CharacterCard(
        name="Каэли",
        first_mes="*Каэли улыбается {{user}}.* Добро пожаловать в таверну!"
    )

    # Собираем 50 сообщений истории (1 канонический first_mes + 49 реплик)
    history = [
        {"role": "assistant", "content": "*Каэли улыбается {{user}}.* Добро пожаловать в таверну!"}
    ]
    for i in range(1, 50):
        role = "user" if i % 2 == 1 else "assistant"
        history.append({"role": role, "content": f"Длинное сообщение шага {i:02d}: " + "слово " * 30})

    assert len(history) == 50

    # Задаем жесткий бюджет токенов, при котором 50 сообщений не влезают
    # Системный промпт ~1500 токенов, first_message ~30, tail (12 сообщений) ~12 * 70 = 840 токенов
    # Всего минимум ~2400 токенов для якорей. Зададим бюджет 3500 токенов.
    test_budget = 3500

    messages = SessionManager.get_lm_studio_messages(
        history=history,
        is_roleplay=True,
        character_card=card,
        user_name="Иван",
        max_context_tokens=test_budget
    )

    # 1. Структура элементов списка: словарь с role и content
    for m in messages:
        assert isinstance(m, dict)
        assert "role" in m
        assert "content" in m

    # 2. Якорь 1: Системный промпт messages[0]
    assert messages[0]["role"] == "system"

    # 3. Якорь 2: Завязка сюжета (first_message = history[0], канонический first_mes)
    assert messages[1]["role"] == "assistant"
    assert "Добро пожаловать в таверну!" in messages[1]["content"]
    assert "Иван" in messages[1]["content"]  # макрос {{user}} заменен

    # 4. Якорь 3: Хвост из последних 12 сообщений (history[-12:]) ВСЕГДА сохранен
    tail_source = history[-12:]
    recent_tail_in_messages = messages[-12:]
    for orig_m, msg_m in zip(tail_source, recent_tail_in_messages):
        assert orig_m["role"] == msg_m["role"]
        # Содержание оригинального шага присутствует
        step_id = orig_m["content"].split(":")[0]
        assert step_id in msg_m["content"]

    # 5. Средняя зона вытеснена: количество сообщений меньше исходных 51
    assert len(messages) < 51
    # При этом минимум 1 + 1 + 12 = 14 сообщений гарантированно сохранено
    assert len(messages) >= 14

    # 6. Хронологический порядок строго соблюден
    # Шаги должны монотонно возрастать
    step_indices = []
    for m in messages[2:]:  # Пропускаем system и first_message
        c = m["content"]
        if "Длинное сообщение шага " in c:
            num = int(c.split("Длинное сообщение шага ")[1].split(":")[0])
            step_indices.append(num)

    assert step_indices == sorted(step_indices), "Сообщения должны следовать строго в хронологическом порядке"


def test_three_anchors_warning_on_extreme_overflow(caplog):
    """
    Проверяет защиту: если даже после полной очистки middle_messages токены
    превышают лимит, приложение не падает, а логируется logger.warning.
    """
    card = CharacterCard(
        name="Титан",
        first_mes="*Титан громогласно приветствует {{user}}.*"
    )

    # 20 огромных сообщений
    history = [
        {"role": "assistant", "content": "*Титан громогласно приветствует {{user}}.*"}
    ]
    for i in range(1, 25):
        role = "user" if i % 2 == 1 else "assistant"
        history.append({"role": role, "content": f"Шаг {i}: " + "огромный текст " * 200})

    # Задаем искусственно микроскопический бюджет
    micro_budget = 50

    with caplog.at_level(logging.WARNING):
        messages = SessionManager.get_lm_studio_messages(
            history=history,
            is_roleplay=True,
            character_card=card,
            user_name="Иван",
            max_context_tokens=micro_budget
        )

    # Приложение не упало, сообщения возвращены
    assert len(messages) >= 14
    # Якорь 1 и 2 на месте
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "assistant"
    assert "Титан громогласно приветствует" in messages[1]["content"]

    # Предупреждение залогировано
    assert any("[RoleplayEviction]" in record.message for record in caplog.records)
