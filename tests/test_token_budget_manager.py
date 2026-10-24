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


class TestTwoCircuitContextBudget:
    """Тестирование двухконтурной модели управления контекстом (Token Budget + Emergency VRAM Guard)."""

    def test_stage_1_normal_operation_vram_1400_ratio_035(self):
        """
        Проверка штатной работы:
        При VRAM = 1400 МБ и ratio = 0.35 должны отдаваться 60 сообщений и 1024 токена.
        """
        target_ctx = 14336
        tokens = int(target_ctx * 0.35)
        decision = TokenBudgetManager.calculate_context_budget(
            current_prompt_tokens=tokens,
            mode="assistant",
            free_vram_mb=1400,
            target_context_window=target_ctx
        )
        assert decision.tier == 1
        assert decision.history_limit == 60
        assert decision.max_tokens == 1024
        assert pytest.approx(decision.ratio, 0.01) == 0.35
        assert decision.is_emergency is False
        assert decision.target_context_window == 14336

        # Проверка безопасной распаковки в кортеж
        hist_limit, max_tok = decision
        assert hist_limit == 60
        assert max_tok == 1024

    def test_emergency_guard_triggers_at_vram_450(self):
        """
        Проверка аварийного режима:
        При VRAM = 450 МБ (< 600 МБ) аварийный рубильник принудительно включает
        Ступень 3 (24 сообщения, max_tokens = 512) даже при низком ratio (0.10 или 0.35).
        """
        target_ctx = 14336
        tokens = int(target_ctx * 0.35)
        decision = TokenBudgetManager.calculate_context_budget(
            current_prompt_tokens=tokens,
            mode="assistant",
            free_vram_mb=450,
            target_context_window=target_ctx
        )
        assert decision.tier == 3
        assert decision.history_limit == 24
        assert decision.max_tokens == 512
        assert decision.is_emergency is True

    def test_emergency_guard_triggers_regardless_of_ratio(self):
        """При свободной VRAM = 599 МБ аварийный предохранитель активен даже при пустом промпте."""
        decision = TokenBudgetManager.calculate_context_budget(
            current_prompt_tokens=0,
            mode="assistant",
            free_vram_mb=599
        )
        assert decision.tier == 3
        assert decision.history_limit == 24
        assert decision.max_tokens == 512
        assert decision.is_emergency is True

    def test_stage_boundaries_by_ratio(self):
        """
        Проверка переключения ступеней по коэффициенту заполнения:
        - ratio < 0.60: Ступень 1 (60 сообщ / 1024 токена)
        - 0.60 <= ratio < 0.80: Ступень 2 (42 сообщ / 768 токенов)
        - ratio >= 0.80: Ступень 3 (24 сообщ / 512 токенов)
        """
        target_ctx = 10000

        # Граница 1: ratio = 0.59 -> Ступень 1
        d1 = TokenBudgetManager.calculate_context_budget(
            current_prompt_tokens=5900,
            free_vram_mb=1500,
            target_context_window=target_ctx
        )
        assert d1.tier == 1
        assert d1.history_limit == 60
        assert d1.max_tokens == 1024

        # Граница 2: ratio = 0.60 -> Ступень 2
        d2 = TokenBudgetManager.calculate_context_budget(
            current_prompt_tokens=6000,
            free_vram_mb=1500,
            target_context_window=target_ctx
        )
        assert d2.tier == 2
        assert d2.history_limit == 42
        assert d2.max_tokens == 768

        # Внутри Ступени 2: ratio = 0.79 -> Ступень 2
        d2_upper = TokenBudgetManager.calculate_context_budget(
            current_prompt_tokens=7900,
            free_vram_mb=1500,
            target_context_window=target_ctx
        )
        assert d2_upper.tier == 2
        assert d2_upper.history_limit == 42
        assert d2_upper.max_tokens == 768

        # Граница 3: ratio = 0.80 -> Ступень 3
        d3 = TokenBudgetManager.calculate_context_budget(
            current_prompt_tokens=8000,
            free_vram_mb=1500,
            target_context_window=target_ctx
        )
        assert d3.tier == 3
        assert d3.history_limit == 24
        assert d3.max_tokens == 512

        # Превышение: ratio = 1.20 -> Ступень 3
        d3_over = TokenBudgetManager.calculate_context_budget(
            current_prompt_tokens=12000,
            free_vram_mb=1500,
            target_context_window=target_ctx
        )
        assert d3_over.tier == 3
        assert d3_over.history_limit == 24
        assert d3_over.max_tokens == 512

    def test_target_context_window_resolution_by_mode(self):
        """Проверка разрешения целевого контекста из MODE_PROFILES."""
        assert TokenBudgetManager.get_target_context_window("assistant") == 14336
        assert TokenBudgetManager.get_target_context_window("roleplay") == 24576
        assert TokenBudgetManager.get_target_context_window("rp") == 24576
        assert TokenBudgetManager.get_target_context_window("engineer") == 28672
        assert TokenBudgetManager.get_target_context_window("engineering") == 28672

    def test_input_as_messages_list(self):
        """Проверка автоматической оценки токенов списка сообщений."""
        messages = [
            {"role": "user", "content": "Привет! " * 50},
            {"role": "assistant", "content": "Здравствуйте! Чем помочь? " * 50}
        ]
        decision = TokenBudgetManager.calculate_context_budget(
            current_prompt_tokens=messages,
            mode="assistant",
            free_vram_mb=1400
        )
        assert decision.current_prompt_tokens > 0
        assert decision.tier in (1, 2, 3)

    def test_dictionary_interface_compatibility(self):
        """Проверка доступа по ключам, атрибутам и методу to_dict."""
        d = TokenBudgetManager.calculate_context_budget(1000, mode="assistant", free_vram_mb=1400)
        assert d["tier"] == 1
        assert d["history_limit"] == 60
        assert d["max_tokens"] == 1024
        assert d.get("tier") == 1
        assert d.to_dict()["history_limit"] == 60


class TestPromptBuildersHistorySlicing:
    """Проверка нарезки истории в AssistantPromptBuilder и RoleplayPromptBuilder."""

    def test_assistant_prompt_builder_slice_history(self):
        from services.assistant_prompt_builder import AssistantPromptBuilder
        history = [{"role": "user", "content": f"msg {i}"} for i in range(80)]

        # Ступень 1: 60 сообщений
        assert len(AssistantPromptBuilder.slice_history(history, max_history_messages=60)) == 60
        # Ступень 2: 42 сообщения
        assert len(AssistantPromptBuilder.slice_history(history, max_history_messages=42)) == 42
        # Ступень 3: 24 сообщения
        assert len(AssistantPromptBuilder.slice_history(history, max_history_messages=24)) == 24

        # Сохранение последних сообщений
        sliced = AssistantPromptBuilder.slice_history(history, max_history_messages=24)
        assert sliced[-1]["content"] == "msg 79"

    def test_roleplay_prompt_builder_slice_history(self):
        from services.roleplay_prompt_builder import RoleplayPromptBuilder
        history = [{"role": "user", "content": f"rp {i}"} for i in range(80)]

        assert len(RoleplayPromptBuilder.slice_history(history, max_history_messages=60)) == 60
        assert len(RoleplayPromptBuilder.slice_history(history, max_history_messages=42)) == 42
        assert len(RoleplayPromptBuilder.slice_history(history, max_history_messages=24)) == 24
        sliced = RoleplayPromptBuilder.slice_history(history, max_history_messages=24)
        assert sliced[-1]["content"] == "rp 79"
