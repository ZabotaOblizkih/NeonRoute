"""
Тесты для RoleplayFlowControllerMixin.
Проверка шлюза триггеров арта, директив режиссуры сцены,
фильтрации стоп-слов (Ambiguity Gate) и реактивного извлечения VisualIntent.
"""

import pytest
from unittest.mock import MagicMock, patch

# Автоматический поиск миксина по возможным путям расположения в проекте
try:
    from ui.controllers.roleplay_flow import RoleplayFlowControllerMixin
except ImportError:
    try:
        from ui.roleplay_flow import RoleplayFlowControllerMixin
    except ImportError:
        try:
            from controllers.roleplay_flow import RoleplayFlowControllerMixin
        except ImportError:
            from ui.main_window import RoleplayFlowControllerMixin

from services.art_director import VisualIntent, TargetSubject


class FakeBubble:
    """Легковесный эмулятор графического бабла сообщения без Qt."""
    def __init__(self):
        self.text = ""
        self.art_source = None
        self.is_ready = None
        self.cue_text = None
        self.chip_prompt = None
        self.chip_callback = None

    def set_text(self, text: str):
        self.text = text

    def set_scene_art_state(self, is_ready: bool = False, cue_text: str = ""):
        self.is_ready = is_ready
        self.cue_text = cue_text

    def add_art_proposal_chip(self, prompt: str, callback):
        self.chip_prompt = prompt
        self.chip_callback = callback


class DummyHost(RoleplayFlowControllerMixin):
    """Тестовый хост-класс для проверки методов миксина в изоляции от Qt UI."""

    def __init__(self):
        self.current_session = {"id": "sess_test_123", "messages": []}
        self.active_character = MagicMock()
        self.active_character.name = "Селеста"
        self.active_character.avatar_path = None
        self.active_character.is_environment = False
        self.active_character.is_minor = False

        self._turn_bubble = None
        self._current_user_request = "покажи себя"
        self.selected_forge_checkpoint = None

        self.pill_forge = MagicMock()
        self.pill_forge.is_online = False
        self.app_config = {"auto_generate_art": False}

        self._sync_turn = MagicMock()
        self._append_message = MagicMock()
        self._on_scene_art_requested = MagicMock()


class TestRoleplayArtTriggers:
    """Проверка депрекации устаревших триггеров визуализации в диалоге."""

    def test_all_triggers_deprecated_and_return_false(self):
        assert RoleplayFlowControllerMixin._should_trigger_roleplay_art("") is False
        assert RoleplayFlowControllerMixin._should_trigger_roleplay_art("Привет, как прошел твой день?") is False
        assert RoleplayFlowControllerMixin._should_trigger_roleplay_art("нарисуй красивый замок на скале") is False
        assert RoleplayFlowControllerMixin._should_trigger_roleplay_art("сделай фото заката") is False
        assert RoleplayFlowControllerMixin._should_trigger_roleplay_art("покажи себя") is False
        assert RoleplayFlowControllerMixin._should_trigger_roleplay_art("в чем ты сейчас одета?") is False


class TestRoleplayArtDirectiveGeneration:
    """Проверка депрекации директив режиссуры арт-промптов в диалоге."""

    def test_dialogue_directive_returns_empty_to_prevent_prompt_corruption(self):
        directive = RoleplayFlowControllerMixin._get_roleplay_art_directive(
            target_model_id="anime",
            companion_name="Селеста",
            is_environment=False,
            has_minor=False
        )
        assert directive == ""


class TestResponseProcessingAndAmbiguityGate:
    """Проверка извлечения промпта из ответа модели и фильтрации мусора."""

    @pytest.fixture
    def host(self):
        return DummyHost()

    def test_extract_image_prompt_and_clean_dialogue(self, host):
        raw_llm_response = (
            "Я с улыбкой подошла к окну и взглянула на звезды.\n"
            "[IMAGE_PROMPT: 1girl, solo, silver hair, starry sky, night --model anime --neg blurry, deformed]\n"
            "Какая чудесная ночь!"
        )

        fake_bubble = FakeBubble()
        host._turn_bubble = fake_bubble

        with patch("services.roleplay_state_manager.RoleplayStateManager.process_model_turn", side_effect=lambda s, t: t), \
             patch("services.rp_cast_manager.RPCastManager.load_cast", return_value={}), \
             patch.object(host, "_trigger_roleplay_memory_extraction"):

            host._handle_roleplay_llm_response(raw_llm_response)

        # 1. Тег [IMAGE_PROMPT] срезан из текста сообщения, диалог сохранен
        assert "[IMAGE_PROMPT" not in fake_bubble.text
        assert "Я с улыбкой подошла к окну" in fake_bubble.text
        assert "Какая чудесная ночь!" in fake_bubble.text

        # 2. Создан и прикреплен объект VisualIntent
        assert hasattr(fake_bubble, "visual_intent")
        v_intent = fake_bubble.visual_intent
        assert isinstance(v_intent, VisualIntent)
        assert "1girl, solo, silver hair, starry sky, night" in v_intent.prompt
        assert v_intent.user_neg == "blurry, deformed"
        assert fake_bubble.art_source == "inline_tag"
        assert fake_bubble.chip_prompt == "1girl, solo, silver hair, starry sky, night"

    def test_ambiguity_gate_blocks_meaningless_prompts(self, host):
        raw_llm_response = (
            "Вот рисунок.\n"
            "[IMAGE_PROMPT: нарисуй картинку арт]"
        )

        fake_bubble = FakeBubble()
        host._turn_bubble = fake_bubble

        with patch("services.roleplay_state_manager.RoleplayStateManager.process_model_turn", side_effect=lambda s, t: t), \
             patch("services.rp_cast_manager.RPCastManager.load_cast", return_value={}), \
             patch.object(host, "_trigger_roleplay_memory_extraction"):

            host._handle_roleplay_llm_response(raw_llm_response)

        # Ambiguity Gate должен заблокировать создание VisualIntent
        assert not hasattr(fake_bubble, "visual_intent")
        assert fake_bubble.is_ready is False


class TestMemoryExtractionTriggerGuard:
    """Проверка условий запуска асинхронного извлечения сущностей сцены."""

    @pytest.fixture
    def host(self):
        h = DummyHost()
        h.lm_client = MagicMock()
        return h

    def test_skips_extraction_during_initialization(self, host):
        host._is_initializing = True
        host.chat_history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

        with patch("services.rp_cast_extractor.RPCastExtractor.extract_cast_async") as mock_extract:
            host._trigger_roleplay_memory_extraction()
            mock_extract.assert_not_called()

    def test_skips_extraction_when_history_too_short(self, host):
        host._is_initializing = False
        host._is_switching_session = False
        host.chat_history = [{"role": "user", "content": "первое сообщение"}]

        with patch("services.rp_cast_extractor.RPCastExtractor.extract_cast_async") as mock_extract:
            host._trigger_roleplay_memory_extraction()
            mock_extract.assert_not_called()

    def test_triggers_extraction_when_valid(self, host):
        host._is_initializing = False
        host._is_switching_session = False
        host.chat_history = [
            {"role": "user", "content": "Мы пришли в таверну."},
            {"role": "assistant", "content": "Хозяин поклонился нам."}
        ]
        host.current_session = {
            "id": "sess_valid",
            "messages": host.chat_history,
            "user_persona_id": None
        }

        with patch("services.rp_cast_extractor.RPCastExtractor.extract_cast_async") as mock_extract, \
             patch("services.session_manager.SessionManager.get_active_user_persona", return_value={"name": "Роман"}):

            host._trigger_roleplay_memory_extraction()
            mock_extract.assert_called_once()
            args, kwargs = mock_extract.call_args
            assert kwargs["session_id"] == "sess_valid"
            assert kwargs["user_name"] == "Роман"