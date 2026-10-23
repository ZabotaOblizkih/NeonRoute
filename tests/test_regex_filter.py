"""
Тесты для TextRegexFilter (services/regex_filter.py).
Проверка очистки мыслей модели (<think>, <thought>), удаления спецтокенов (<|im_end|>),
нормализации диалоговых тире, парсинга тегов [IMAGE_PROMPT] и выполнения кастомных правил.
"""

import pytest
from services.regex_filter import TextRegexFilter


class TestExtractImagePrompt:
    """Проверка извлечения промпта генерации изображений из текста ответа."""

    def test_empty_or_none_inputs(self):
        prompt, text = TextRegexFilter.extract_image_prompt(None)
        assert prompt is None
        assert text == ""

        prompt_empty, text_empty = TextRegexFilter.extract_image_prompt("")
        assert prompt_empty is None
        assert text_empty == ""

    def test_extract_standard_image_prompt(self):
        raw_text = (
            "Конечно, я нарисую для тебя этот пейзаж!\n"
            "[IMAGE_PROMPT: 1girl, solo, fantasy castle, vibrant colors, sunset]\n"
            "Надеюсь, тебе понравится!"
        )
        prompt, clean_text = TextRegexFilter.extract_image_prompt(raw_text)

        assert prompt == "1girl, solo, fantasy castle, vibrant colors, sunset"
        assert "[IMAGE_PROMPT" not in clean_text
        assert "Конечно, я нарисую для тебя этот пейзаж!" in clean_text
        assert "Надеюсь, тебе понравится!" in clean_text

    def test_extract_tag_variations_and_markdown_cleaning(self):
        # Поддержка дефиса и нижнего подчеркивания: IMAGE-PROMPT / IMAGE_PROMPT
        text_with_dash = "Вот арт: [IMAGE-PROMPT: **cyberpunk city, neon rain**]"
        prompt, clean_text = TextRegexFilter.extract_image_prompt(text_with_dash)

        # Markdown-звездочки в начале промпта должны срезаться
        assert prompt.startswith("cyberpunk city")
        assert "**" not in prompt[:2]
        assert "[IMAGE-PROMPT" not in clean_text

    def test_cleans_trailing_speaker_labels(self):
        # Когда модель случайно дописывает имя следующего спикера в конце ответа
        raw_text = (
            "Я заварила для нас чай.\n"
            "[IMAGE_PROMPT: steaming cup of herbal tea on wooden table]\n"
            "Элария:"
        )
        _, clean_text = TextRegexFilter.extract_image_prompt(raw_text)

        assert "Элария:" not in clean_text
        assert clean_text == "Я заварила для нас чай."

    def test_no_image_prompt_present(self):
        normal_dialogue = "Привет! Как твои дела сегодня?"
        prompt, clean_text = TextRegexFilter.extract_image_prompt(normal_dialogue)

        assert prompt is None
        assert clean_text == normal_dialogue


class TestProcessOutputThinkingTags:
    """Проверка удаления тегов цепочки рассуждений (<think>, <thought>)."""

    def test_strip_closed_thinking_tags(self):
        text = "<think>Пользователь спросил про погоду. Отвечу вежливо.</think>Сегодня отличный солнечный день!"
        result = TextRegexFilter.process_output(text)
        assert result == "Сегодня отличный солнечный день!"

    def test_strip_thought_synonym_multiline(self):
        text = (
            "<thought>\n"
            "Line 1: Analyze user intent\n"
            "Line 2: Prepare answer\n"
            "</thought>\n"
            "Ответ сформирован."
        )
        result = TextRegexFilter.process_output(text)
        assert result == "Ответ сформирован."

    def test_strip_unclosed_thinking_tag_on_stream_cutoff(self):
        # Если ответ оборвался посреди размышлений, незакрытый тег сносит весь хвост
        text = "Здравствуйте! <think>Мне нужно подумать о..."
        result = TextRegexFilter.process_output(text)
        assert result == "Здравствуйте!"

    def test_case_insensitive_tags(self):
        text = "<THINK>Внутренний монолог</THINK>Привет!"
        result = TextRegexFilter.process_output(text)
        assert result == "Привет!"


class TestProcessOutputSystemArtifacts:
    """Проверка очистки служебных токенов моделей и напоминаний."""

    def test_strip_model_special_tokens(self):
        text = "<|im_start|>assistant\nПривет!<|im_end|><|endoftext|>"
        result = TextRegexFilter.process_output(text)
        assert "<|" not in result
        assert "|>" not in result
        assert "Привет!" in result

    def test_strip_system_reminder(self):
        text = (
            "Я готова продолжить историю.\n"
            "🔔 Напоминаю: соблюдай стиль персонажа и отвечай от 3-го лица\n"
            "— Мы можем идти дальше."
        )
        result = TextRegexFilter.process_output(text)
        assert "🔔" not in result
        assert "Напоминаю:" not in result
        assert "— Мы можем идти дальше." in result

    def test_strip_trailing_model_flags(self):
        text = "Сгенерированный ответ --model realistic"
        result = TextRegexFilter.process_output(text)
        assert result == "Сгенерированный ответ"


class TestDialogueFormattingAndCustomRules:
    """Проверка типографики диалогов и кастомных правил персонажа."""

    def test_normalize_dialogue_hyphens_to_em_dash(self):
        text = (
            "- Привет, — сказала она.\n"
            "- Как дела?\n"
            "Обычный текст без тире в начале."
        )
        result = TextRegexFilter.process_output(text)

        lines = result.split("\n")
        assert lines[0].startswith("— Привет")
        assert lines[1].startswith("— Как дела?")
        assert lines[2].startswith("Обычный текст")

    def test_apply_custom_regex_rules(self):
        # Замена авторских маркеров или специфических слов
        rules = [
            {"pattern": r"\[улыбка\]", "replacement": "*улыбнулась*"},
            {"find": r"ИИ-помощник", "replace": "Элария"}
        ]
        text = "Я твой ИИ-помощник. [улыбка]"
        result = TextRegexFilter.process_output(text, custom_rules=rules)

        assert result == "Я твой Элария. *улыбнулась*"

    def test_custom_rules_resilience_to_broken_regex(self):
        # Невалидное регулярное выражение (незакрытая скобка) не должно ронять метод
        broken_rules = [
            {"pattern": r"(незакрытая скобка[", "replacement": "замена"},
            {"pattern": r"кот", "replacement": "пёс"}
        ]
        text = "Рыжий кот спит."
        result = TextRegexFilter.process_output(text, custom_rules=broken_rules)

        # Ошибочное правило пропустилось, валидное отработало
        assert result == "Рыжий пёс спит."