"""
Тесты для проверки синхронизации и пессимистичной оценки токенов (Token Budget Safety Margin).
"""

import pytest
from services.token_budget_manager import TokenBudgetManager
from services.session_manager import SessionManager
from services.tavern_parser import CharacterCard


class TestTokenBudgetSafetyMargin:
    """Проверка единой пессимистичной формулы и синхронизации с SessionManager."""

    def test_estimate_text_tokens_pessimistic_formula(self):
        # Пустая строка -> 0
        assert TokenBudgetManager.estimate_text_tokens("") == 0
        assert TokenBudgetManager.estimate_text_tokens(None) == 0

        # Только кириллица: 10 символов -> (10 / 2.0) * 1.12 + 2 = 5 * 1.12 + 2 = 5.6 + 2 = 7 токенов
        cyr_text = "абвгдеёжзий"[:10]
        assert TokenBudgetManager.estimate_text_tokens(cyr_text) == 7
        assert isinstance(TokenBudgetManager.estimate_text_tokens(cyr_text), int)

        # Только латиница/спецсимволы: 33 символа -> (33 / 3.3) * 1.12 + 2 = 10 * 1.12 + 2 = 13 токенов
        latin_text = "a" * 33
        assert TokenBudgetManager.estimate_text_tokens(latin_text) == 13
        assert isinstance(TokenBudgetManager.estimate_text_tokens(latin_text), int)

        # Смешанный текст с кодом и тегами
        mixed = "<|im_start|>user\ndef foo():\n    return 'Привет, мир!'<|im_end|>"
        tok = TokenBudgetManager.estimate_text_tokens(mixed)
        assert tok > 0
        assert isinstance(tok, int)

    def test_estimate_message_tokens_framing(self):
        # 5 токенов на служебные теги диалогового формата
        msg = {"role": "user", "content": "Привет"}
        # text_tokens("Привет") = int((6/2.0)*1.12) + 2 = 3 + 2 = 5
        # message_tokens = 5 + 5 = 10
        expected = TokenBudgetManager.estimate_text_tokens("Привет") + 5
        assert TokenBudgetManager.estimate_message_tokens(msg) == expected
        assert isinstance(TokenBudgetManager.estimate_message_tokens(msg), int)

        # Невалидные входные данные
        assert TokenBudgetManager.estimate_message_tokens(None) == 0
        assert TokenBudgetManager.estimate_message_tokens({}) == 0
        assert TokenBudgetManager.estimate_message_tokens("string") == 0

    def test_session_manager_delegation(self):
        # SessionManager._estimate_message_tokens делегирует в TokenBudgetManager
        msg = {"role": "assistant", "content": "Тестовый ответ системы"}
        assert SessionManager._estimate_message_tokens(msg) == TokenBudgetManager.estimate_message_tokens(msg)

        # SessionManager.estimate_tokens делегирует в TokenBudgetManager.estimate_text_tokens
        text = "Проверка делегирования функций расчета токенов"
        assert SessionManager.estimate_tokens(text) == TokenBudgetManager.estimate_text_tokens(text)

    def test_middle_messages_eviction_with_generation_reserve(self):
        card = CharacterCard(
            name="Ассистент",
            first_mes="Здравствуйте! Чем я могу вам помочь?"
        )

        history = [
            {"role": "assistant", "content": "Здравствуйте! Чем я могу вам помочь?"}
        ]
        # Создаем длинную историю (30 шагов)
        for i in range(1, 31):
            role = "user" if i % 2 == 1 else "assistant"
            history.append({"role": role, "content": f"Реплика #{i}: " + "Подробное обсуждение деталей " * 15})

        messages = SessionManager.get_lm_studio_messages(
            history=history,
            is_roleplay=True,
            character_card=card,
            user_name="Игрок"
        )

        # Проверяем, что запрос сформирован и соблюдает структуру диалога
        assert len(messages) >= 14
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "assistant"

        # Суммарный объем токенов сформированного промпта не превышает окно с вычетом резерва ответа
        total_prompt_tokens = sum(SessionManager._estimate_message_tokens(m) for m in messages)
        # В 24K контексте при резерве генерации ~4K и инъекции ~1.2K лимит сообщений ~19-20K
        assert total_prompt_tokens <= 22000
