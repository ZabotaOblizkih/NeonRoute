"""
Тесты для MacroEngine (services/macro_engine.py).
Проверка подстановки тегов {{user}}, {{char}}, Chub/Tavern <START>,
случайного выбора {{random: ...}} и бросков костей {{roll: XdY[+-Z]}}.
"""

import pytest
from unittest.mock import patch
from services.macro_engine import MacroEngine


class TestMacroEngineBasicSubstitutions:
    """Проверка базовых строковых замен и краевых случаев."""

    def test_empty_or_none_returns_empty_string(self):
        assert MacroEngine.process(None) == ""
        assert MacroEngine.process("") == ""

    def test_user_tag_variants(self):
        # Проверяем все варианты: регистр, пробелы, {{user_name}}, <USER>
        templates = [
            "Привет, {{user}}!",
            "Привет, {{ USER }}!",
            "Привет, {{user_name}}!",
            "Привет, {{ user_name }}!",
            "Привет, <USER>!",
            "Привет, <USER_NAME>!"
        ]
        for tmpl in templates:
            res = MacroEngine.process(tmpl, user_name="Роман")
            assert res == "Привет, Роман!"

    def test_char_tag_variants(self):
        # Проверяем варианты: {{char}}, {{bot}}, <CHAR>, <BOT>
        templates = [
            "Я {{char}}.",
            "Я {{ CHAR }}.",
            "Я {{char_name}}.",
            "Я {{bot}}.",
            "Я {{ bot }}.",
            "Я <CHAR>.",
            "Я <BOT>."
        ]
        for tmpl in templates:
            res = MacroEngine.process(tmpl, char_name="Селеста")
            assert res == "Я Селеста."

    def test_chub_tavern_start_separator(self):
        text = "Описание мира.\n<START>\n{{char}}: Привет!"
        res = MacroEngine.process(text, char_name="Алиса")

        assert "--- [Пример сцены] ---" in res
        assert "<START>" not in res
        assert res.endswith("Алиса: Привет!")


class TestMacroEngineRandomPicker:
    """Проверка макроса случайного выбора {{random: opt1, opt2, ...}}."""

    def test_random_single_option(self):
        text = "Цвет глаз: {{random: зеленый}}"
        res = MacroEngine.process(text)
        assert res == "Цвет глаз: зеленый"

    def test_random_strips_whitespace(self):
        text = "{{random:   красный  ,   синий   ,   черный  }}"
        options = {"красный", "синий", "черный"}
        for _ in range(15):
            res = MacroEngine.process(text)
            assert res in options

    def test_random_empty_options(self):
        text = "Значение: {{random:   , , }}"
        res = MacroEngine.process(text)
        assert res == "Значение: "

    def test_random_mocked_choice(self):
        text = "Погода: {{random: дождь, снег, солнце}}"
        with patch("random.choice", return_value="дождь") as mock_choice:
            res = MacroEngine.process(text)
            assert res == "Погода: дождь"
            mock_choice.assert_called_once_with(["дождь", "снег", "солнце"])


class TestMacroEngineDiceRolls:
    """Проверка парсера бросков костей {{roll: XdY[+-Z]}}."""

    def test_simple_roll_mocked(self):
        text = "Урон: {{roll: 1d6}}"
        # Эмулируем выпадение 4 на шестиграннике
        with patch("random.randint", return_value=4):
            res = MacroEngine.process(text)
            assert res == "Урон: 4"

    def test_roll_with_addition_and_subtraction(self):
        # Бросок 2d6 + 5: эмулируем выпадение [3, 4] -> сумма 7 + 5 = 12
        with patch("random.randint", side_effect=[3, 4]):
            res_add = MacroEngine.process("Атака: {{roll: 2d6 + 5}}")
            assert res_add == "Атака: 12"

        # Бросок 1d20 - 3: эмулируем выпадение 15 -> 15 - 3 = 12
        with patch("random.randint", return_value=15):
            res_sub = MacroEngine.process("Спасбросок: {{roll: 1d20 - 3}}")
            assert res_sub == "Спасбросок: 12"

    def test_roll_clamps_limits(self):
        # Защита от перегрузки: count зажат в [1, 20], sides в [1, 1000]
        # Проверяем, что запрос на 100 кубиков ограничит бросок 20 вызовами randint
        with patch("random.randint", return_value=1) as mock_rand:
            res = MacroEngine.process("Огромный залп: {{roll: 100d6}}")
            assert mock_rand.call_count == 20
            assert res == "Огромный залп: 20"

    def test_roll_unmocked_bounds(self):
        # Реальный прогон: результат d20 обязан лежать строго между 1 и 20
        for _ in range(20):
            res = MacroEngine.process("{{roll: 1d20}}")
            val = int(res)
            assert 1 <= val <= 20


class TestMacroEngineCombined:
    """Комплексная сквозная проверка нескольких макросов в одном тексте."""

    def test_full_roleplay_prompt(self):
        raw_prompt = (
            "<START>\n"
            "*{{char}} смотрит на {{user}} и вытаскивает {{random: меч, лук}}.*\n"
            "Инициатива: {{roll: 1d20 + 2}}."
        )

        with patch("random.choice", return_value="меч"), \
             patch("random.randint", return_value=10):
            
            res = MacroEngine.process(
                raw_prompt,
                user_name="Роман",
                char_name="Селеста"
            )

            assert "--- [Пример сцены] ---" in res
            assert "*Селеста смотрит на Роман и вытаскивает меч.*" in res
            assert "Инициатива: 12." in res
            assert "{{" not in res