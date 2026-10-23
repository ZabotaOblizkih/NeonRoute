"""
Тесты для TokenBudgetManager (проверка расчета токенов и индикаторов нагрузки).
"""

import pytest
from services.token_budget_manager import TokenBudgetManager


class TestEstimateTextTokens:
    """Проверка оценки токенов для произвольного текста."""

    def test_empty_or_none(self):
        assert TokenBudgetManager.estimate_text_tokens("") == 0
        assert TokenBudgetManager.estimate_text_tokens(None) == 0

    def test_pure_ascii(self):
        # 16 символов латиницы: int((16 / 3.3) * 1.12) + 2 = int(5.43) + 2 = 7 токенов
        text = "Hello world test"
        assert TokenBudgetManager.estimate_text_tokens(text) == 7

    def test_pure_cyrillic(self):
        # 13 кириллических букв: int((13 / 2.0) * 1.12) + 2 = int(7.28) + 2 = 9 токенов
        text = "ПриветМирТест"
        assert TokenBudgetManager.estimate_text_tokens(text) == 9

    def test_mixed_text(self):
        # 'Привет ' (6 кир, 1 пробел) + 'World' (5 лат) -> cyr=6, other=6
        # int(((6 / 2.0) + (6 / 3.3)) * 1.12) + 2 = int((3.0 + 1.818) * 1.12) + 2 = int(5.396) + 2 = 7
        text = "Привет World"
        assert TokenBudgetManager.estimate_text_tokens(text) == 7


class TestEstimateMessageTokens:
    """Проверка оценки токенов для структуры сообщения чата."""

    def test_invalid_input(self):
        assert TokenBudgetManager.estimate_message_tokens(None) == 0
        assert TokenBudgetManager.estimate_message_tokens([]) == 0
        assert TokenBudgetManager.estimate_message_tokens("not a dict") == 0

    def test_missing_or_invalid_content(self):
        assert TokenBudgetManager.estimate_message_tokens({}) == 0
        assert TokenBudgetManager.estimate_message_tokens({"content": 12345}) == 0

    def test_standard_text_message(self):
        # 'Test' = int((4 / 3.3) * 1.12) + 2 = 3 токена + 5 служебных = 8
        msg = {"role": "user", "content": "Test"}
        assert TokenBudgetManager.estimate_message_tokens(msg) == 8

    def test_multimodal_message_with_image(self):
        # Список блоков: 1 текстовый ("Test" = 3 токена) + изображение (800) + 5 служебных = 808
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Test"},
                {"type": "image_url", "image_url": "data:image/png;base64,..."}
            ]
        }
        assert TokenBudgetManager.estimate_message_tokens(msg) == 808


class TestEstimateCardBudget:
    """Проверка оценки бюджета полей карточки и лорбуков."""

    def test_card_dict_without_lore(self):
        fields = {
            "name": "Alice",
            "description": "Friendly companion"
        }
        base, lore = TokenBudgetManager.estimate_card_budget(fields)
        # "Alice Friendly companion" = 24 chars -> int(24/3.3 * 1.12) + 2 = 10
        assert base == 10
        assert lore == 0

    def test_card_with_lore_texts(self):
        fields = {"name": "Robert", "role": "Companion"}
        lore = ["Lore entry one", "Lore entry two"]
        base, lore_tokens = TokenBudgetManager.estimate_card_budget(fields, lore)
        assert base > 0
        assert lore_tokens > 0

    def test_card_short_text(self):
        # 3 символа латиницы: int((3 / 3.3) * 1.12) + 2 = 3 токена
        base, _ = TokenBudgetManager.estimate_card_budget({"name": "Bob"})
        assert base == 3

    def test_card_list_input(self):
        fields = ["Item1", "Item2"]
        base, lore = TokenBudgetManager.estimate_card_budget(fields)
        assert base > 0
        assert lore == 0

    def test_empty_card(self):
        base, lore = TokenBudgetManager.estimate_card_budget({}, None)
        assert base == 0
        assert lore == 0


class TestTrafficLight:
    """Проверка цветового светофора и пороговых значений."""

    def test_green_threshold(self):
        res = TokenBudgetManager.get_traffic_light(2300, is_lorebook=False)
        assert res["color"] == "#00FF66"
        assert res["badge"] == "🟢"
        assert "Оптимальный объем" in res["status"]

    def test_green_lorebook(self):
        res = TokenBudgetManager.get_traffic_light(1500, is_lorebook=True, entry_count=5)
        assert res["color"] == "#00FF66"
        assert "(5 статей)" in res["status"]

    def test_yellow_light_threshold(self):
        res = TokenBudgetManager.get_traffic_light(2301)
        assert res["color"] == "#FFE600"
        assert res["badge"] == "🟡"
        assert "Умеренный объем" in res["status"]

        res_boundary = TokenBudgetManager.get_traffic_light(4500)
        assert res_boundary["color"] == "#FFE600"

    def test_yellow_dense_threshold(self):
        res = TokenBudgetManager.get_traffic_light(4501)
        assert res["color"] == "#F59E0B"
        assert res["badge"] == "🟡"
        assert "Плотный лор" in res["status"]

        res_boundary = TokenBudgetManager.get_traffic_light(6999)
        assert res_boundary["color"] == "#F59E0B"

    def test_red_threshold(self):
        res = TokenBudgetManager.get_traffic_light(7000)
        assert res["color"] == "#FF007F"
        assert res["badge"] == "🔴"
        assert "7000+" in res["status"]

        res_over = TokenBudgetManager.get_traffic_light(12000, is_lorebook=True, entry_count=40)
        assert res_over["color"] == "#FF007F"
        assert "(40 статей, 7000+)" in res_over["status"]