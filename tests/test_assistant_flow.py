"""
Тесты для AssistantFlowControllerMixin.
Проверка шлюза триггеров ассистента, детекции автопортрета Эларии,
механизма Seed Lock, очистки ответов и шлюза памяти LanceDB.
"""

import pytest
from unittest.mock import MagicMock, patch

try:
    from ui.controllers.assistant_flow import AssistantFlowControllerMixin
except ImportError:
    try:
        from ui.assistant_flow import AssistantFlowControllerMixin
    except ImportError:
        try:
            from controllers.assistant_flow import AssistantFlowControllerMixin
        except ImportError:
            from ui.main_window import AssistantFlowControllerMixin

from services.art_director import VisualIntent, TargetSubject


class FakeBubble:
    """Легковесный эмулятор графического бабла сообщения без Qt."""
    def __init__(self):
        self.text = ""
        self.visual_intent = None
        self.chip_prompt = None
        self.chip_callback = None

    def set_text(self, text: str):
        self.text = text

    def add_art_proposal_chip(self, prompt: str, callback):
        self.chip_prompt = prompt
        self.chip_callback = callback


class DummyAssistantHost(AssistantFlowControllerMixin):
    """Тестовый хост для проверки логики ассистента в изоляции от интерфейса."""

    def __init__(self):
        self.current_session = {"id": "sess_asst_123", "messages": []}
        self.chat_history = []
        self._turn_bubble = None
        self._current_user_request = ""
        self.selected_forge_checkpoint = None

        self.pill_forge = MagicMock()
        self.pill_forge.is_online = False
        self.app_config = {"auto_generate_art": False}

        self._sync_turn = MagicMock()
        self._append_message = MagicMock()
        self._on_scene_art_requested = MagicMock()
        self._active_tasks = {}
        self.task_queue = MagicMock()
        self.lm_client = MagicMock()
        self.lm_combo = MagicMock()
        self.lm_combo.currentText.return_value = "default"


class TestAssistantArtTriggers:
    """Проверка фильтрации запросов на арт в режиме ассистента."""

    def test_empty_query_returns_false(self):
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("") is False
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("   ") is False
        assert AssistantFlowControllerMixin._should_trigger_assistant_art(None) is False

    def test_ordinary_questions_return_false(self):
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("Как работает гравитация?") is False
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("Помоги составить план на день") is False

    def test_technical_exclusions_override_verbs(self):
        # Технические слова блокируют вызов арта даже при наличии глаголов "нарисуй" / "сделай"
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("нарисуй график в matplotlib") is False
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("сделай сводную таблицу excel") is False
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("сгенерируй скрипт на python") is False
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("создай схему базы данных sql") is False

    def test_roleplay_sensual_triggers_are_ignored_in_assistant_mode(self):
        # В режиме ассистента ролевые фразы без явных глаголов рисования не запускают арт-движок
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("в чем ты сейчас одета?") is False
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("подойди ближе") is False

    def test_explicit_art_triggers_pass(self):
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("нарисуй неоновый город под дождем") is True
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("сделай фото заката в горах") is True
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("визуализируй космическую станцию") is True
        assert AssistantFlowControllerMixin._should_trigger_assistant_art("draw fantasy dragon") is True


class TestAssistantDirective:
    """Проверка генерации системной директивы SD 1.5."""

    def test_directive_contains_rules_and_model_hint(self):
        directive = AssistantFlowControllerMixin._get_ephemeral_art_directive(
            target_model_id="cyberrealistic",
            target_model_desc="Киберпанк и глянец"
        )

        assert "[SYSTEM DIRECTIVE: Image Generation for Stable Diffusion]" in directive
        assert "cyberrealistic (Киберпанк и глянец)" in directive
        assert "Tag Language: STRICTLY English" in directive
        assert "[IMAGE_PROMPT: <comma-separated SD positive tags> --ar <wide|portrait|square> --model cyberrealistic]" in directive


class TestAssistantResponseAndSelfPortrait:
    """Проверка обработки ответа, детекции автопортрета и Seed Lock."""

    @pytest.fixture
    def host(self):
        return DummyAssistantHost()

    def test_self_portrait_detection_sets_elaria_target(self, host):
        host._current_user_request = "Элария, нарисуй свой автопортрет"
        raw_response = (
            "С удовольствием! Перевожу образ в кадр...\n"
            "[IMAGE_PROMPT: 1girl, elaria, blue eyes, silver hair, cyber room --model anime]"
        )

        bubble = FakeBubble()
        host._turn_bubble = bubble

        with patch.object(host, "_trigger_assistant_memory_extraction"):
            host._handle_assistant_llm_response(raw_response)

        assert bubble.visual_intent is not None
        v_intent = bubble.visual_intent
        # Автопортрет должен распознать сущность ELARIA
        assert v_intent.target_subject == TargetSubject.ELARIA
        assert "[IMAGE_PROMPT" not in bubble.text
        assert "С удовольствием!" in bubble.text

    def test_polite_addressing_is_not_self_portrait(self, host):
        # Имя "Элария" в начале запроса — вежливое обращение, а не автопортрет
        host._current_user_request = "Элария, нарисуй старинный замок в лесу"
        raw_response = (
            "Вот такой замок я представляю:\n"
            "[IMAGE_PROMPT: ancient castle, dense forest, morning mist --model realistic]"
        )

        bubble = FakeBubble()
        host._turn_bubble = bubble

        with patch.object(host, "_trigger_assistant_memory_extraction"):
            host._handle_assistant_llm_response(raw_response)

        assert bubble.visual_intent is not None
        v_intent = bubble.visual_intent
        assert v_intent.target_subject == TargetSubject.SCENE_OBJECT

    def test_seed_lock_preserves_previous_seed(self, host):
        # Пользователь просит сохранить позу с предыдущей генерации
        host._current_user_request = "повтори в той же позе, но смени фон на снежный"
        host._last_art_meta = {"seed": 777123}

        raw_response = (
            "Оставляю композицию, меняю фон:\n"
            "[IMAGE_PROMPT: 1girl, winter coat, falling snow --seed 777123]"
        )

        bubble = FakeBubble()
        host._turn_bubble = bubble

        with patch.object(host, "_trigger_assistant_memory_extraction"):
            host._handle_assistant_llm_response(raw_response)

        assert bubble.visual_intent is not None
        assert bubble.visual_intent.seed == 777123

    def test_fallback_dialogue_when_only_image_tag_generated(self, host):
        # Модель выдала чистый тег без текста реплики
        raw_response = "[IMAGE_PROMPT: futuristic sportscar on highway]"
        bubble = FakeBubble()
        host._turn_bubble = bubble

        with patch.object(host, "_trigger_assistant_memory_extraction"):
            host._handle_assistant_llm_response(raw_response)

        # Должна подставиться одна из живых фраз-заглушек
        assert len(bubble.text.strip()) > 0
        assert "[IMAGE_PROMPT" not in bubble.text


class TestAssistantMemoryExtractionGuard:
    """Проверка условий запуска пассивной памяти LanceDB."""

    @pytest.fixture
    def host(self):
        h = DummyAssistantHost()
        h.chat_history = [
            {"role": "user", "content": "Меня зовут Роман, я пишу на Python."},
            {"role": "assistant", "content": "Приятно познакомиться, Роман!"}
        ]
        h.current_session = {"id": "sess_asst_123", "messages": h.chat_history}
        return h

    def test_memory_extraction_skipped_during_initialization(self, host):
        host._is_initializing = True

        with patch("services.memory_extractor.MemoryExtractor.extract_facts_async") as mock_extract:
            host._trigger_assistant_memory_extraction()
            mock_extract.assert_not_called()

    def test_memory_extraction_triggered_on_plain_text(self, host):
        host._is_initializing = False
        host._is_switching_session = False

        with patch("services.memory_extractor.MemoryExtractor.extract_facts_async") as mock_extract:
            host._trigger_assistant_memory_extraction()
            mock_extract.assert_called_once_with(host.chat_history, context=host)

    def test_art_responses_bypass_memory_extraction(self, host):
        # Если ответ содержал арт-тег, вызов памяти блокируется
        raw_response = "Вот арт: [IMAGE_PROMPT: cyberpunk neon city]"
        bubble = FakeBubble()
        host._turn_bubble = bubble

        with patch.object(host, "_trigger_assistant_memory_extraction") as mock_trigger:
            host._handle_assistant_llm_response(raw_response)
            mock_trigger.assert_not_called()


class TestAssistantPromptBuilderSSOT:
    """Проверка централизованных констант и построителя промпта навыка в assistant_prompt_builder.py."""

    def test_default_system_prompt_contains_gallery_protocol(self):
        from services.assistant_prompt_builder import DEFAULT_ELARIA_SYSTEM_PROMPT
        assert "протокол визуализации (навык «галерея»):" in DEFAULT_ELARIA_SYSTEM_PROMPT
        assert "[GALERY]" in DEFAULT_ELARIA_SYSTEM_PROMPT
        assert "добавь команду вызова навыка: [GALERY]" in DEFAULT_ELARIA_SYSTEM_PROMPT

    def test_gallery_skill_directive_template_rules(self):
        from services.assistant_prompt_builder import GALLERY_SKILL_DIRECTIVE_TEMPLATE
        assert "[SYSTEM DIRECTIVE: Image Generation for Stable Diffusion]" in GALLERY_SKILL_DIRECTIVE_TEMPLATE
        assert "CRITICAL: Do NOT write any conversational filler, greetings, or Russian text." in GALLERY_SKILL_DIRECTIVE_TEMPLATE
        assert "[IMAGE_PROMPT: <comma-separated SD positive tags> --ar <wide|portrait|square> --model {target_model_id}]" in GALLERY_SKILL_DIRECTIVE_TEMPLATE

    def test_build_gallery_art_prompt_formats_correctly(self):
        from services.assistant_prompt_builder import AssistantPromptBuilder
        prompt = AssistantPromptBuilder.build_gallery_art_prompt(
            user_query="нарисуй девушку на балконе",
            target_model_id="cyberrealistic",
            target_model_desc="Киберпанк и глянец"
        )
        assert prompt.startswith("нарисуй девушку на балконе")
        assert "cyberrealistic (Киберпанк и глянец)" in prompt
        assert "--model cyberrealistic" in prompt


class TestGalleryTwoPassProtocol:
    """Проверка двухпроходного механизма [GALERY] -> SD Forge."""

    @pytest.fixture
    def host(self):
        return DummyAssistantHost()

    def test_first_pass_cleans_gallery_tag_and_enqueues_second_pass(self, host):
        host._current_user_request = "Представь, как ты пьешь кофе на балконе"
        raw_response = "Представила! Утренний прохладный воздух, чашка ароматного кофе... [GALERY]"
        bubble = FakeBubble()
        host._turn_bubble = bubble

        with patch.object(host, "_trigger_assistant_memory_extraction") as mock_mem:
            host._handle_assistant_llm_response(raw_response)
            mock_mem.assert_not_called()

        assert "[GALERY]" not in bubble.text
        assert "Представила!" in bubble.text
        assert host._is_running_gallery_art_pass is True
        assert host._pending_gallery_art_widget is bubble
        assert host._pending_gallery_user_query == "Представь, как ты пьешь кофе на балконе"
        host.task_queue.enqueue.assert_called_once()
        task = host.task_queue.enqueue.call_args[0][0]
        assert task.metadata.get("silent") is True
        assert task.metadata.get("is_gallery_art_pass") is True
        assert task.kwargs.get("stream") is False

    def test_second_pass_silently_dispatches_to_forge_without_ui_messages(self, host):
        bubble = FakeBubble()
        bubble.set_text("Мой ответ про кофе.")
        host._is_running_gallery_art_pass = True
        host._pending_gallery_art_widget = bubble
        host._pending_gallery_user_query = "Представь, как ты пьешь кофе на балконе"

        second_pass_response = (
            "[IMAGE_PROMPT: 1girl, sitting on balcony, drinking coffee, morning light, realistic, 8k uhd --ar portrait --model realistic]"
        )

        with patch.object(host, "_append_message") as mock_append:
            host._handle_assistant_llm_response(second_pass_response)
            # Второй проход не должен создавать новых сообщений
            mock_append.assert_not_called()

        assert host._is_running_gallery_art_pass is False
        assert host._pending_gallery_art_widget is None
        assert host._pending_gallery_user_query is None
        assert bubble.visual_intent is not None
        assert "1girl, sitting on balcony" in bubble.visual_intent.prompt
        assert bubble.visual_intent.aspect_ratio == "portrait"
        # Текст первого бабла должен остаться без изменений
        assert bubble.text == "Мой ответ про кофе."

    def test_only_gallery_tag_fallback_text(self, host):
        # Если модель вернула только [GALERY] без текста
        bubble = FakeBubble()
        host._turn_bubble = bubble
        host._current_user_request = "покажи космос"

        with patch.object(host, "_enqueue_gallery_art_pass"):
            host._handle_assistant_llm_response("[GALERY]")

        assert len(bubble.text.strip()) > 0
        assert "[GALERY]" not in bubble.text
        assert "Связь с моделью" not in bubble.text

    @pytest.mark.parametrize("marker", [
        "[GALERY]", "[GALLERY]", "[galery]", "[gallery]",
        "[ГАЛЕРЕЯ]", "[Галерея]", "[галерея]"
    ])
    def test_all_gallery_trigger_variants_recognized_and_stripped(self, host, marker):
        bubble = FakeBubble()
        host._turn_bubble = bubble
        host._current_user_request = "Нарисуй закат над морем"
        raw_response = f"Вот такой волшебный закат! {marker}\n![Девушка в шляпе](https://example.com/image.jpg)"

        with patch.object(host, "_enqueue_gallery_art_pass") as mock_enqueue:
            host._handle_assistant_llm_response(raw_response)
            mock_enqueue.assert_called_once_with("Нарисуй закат над морем")

        assert marker not in bubble.text
        assert "![Девушка в шляпе]" not in bubble.text
        assert "https://example.com/image.jpg" not in bubble.text
        assert "Вот такой волшебный закат!" in bubble.text
        assert host._is_running_gallery_art_pass is True