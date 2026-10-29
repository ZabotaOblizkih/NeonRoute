"""
Сквозной интеграционный тест игрового хода (Full Roleplay Turn Pipeline).
Проверяет непрерывный сценарий:
MacroEngine -> RoleplayPromptBuilder -> RoleplayFlowController -> TextRegexFilter -> VisualIntent -> ArtDirector.
"""

import pytest
from unittest.mock import MagicMock, patch

from services.tavern_parser import CharacterCard
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.macro_engine import MacroEngine
from services.regex_filter import TextRegexFilter
from services.art_director import ArtDirector, VisualIntent, TargetSubject

# Импорт миксина контроллера
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


class IntegrationFakeBubble:
    """Эмулятор бабла чата для проверки сквозной передачи данных."""
    def __init__(self):
        self.text = ""
        self.visual_intent = None
        self.art_source = None
        self.is_ready = None

    def set_text(self, text: str):
        self.text = text

    def set_scene_art_state(self, is_ready: bool = False, cue_text: str = ""):
        self.is_ready = is_ready

    def add_art_proposal_chip(self, prompt: str, callback):
        self.chip_prompt = prompt


class IntegrationDummyHost(RoleplayFlowControllerMixin):
    """Хост-контроллер для сквозного тестирования."""
    def __init__(self, character: CharacterCard):
        self.current_session = {"id": "session_e2e_turn", "messages": []}
        self.active_character = character
        self._turn_bubble = None
        self._current_user_request = ""
        self.selected_forge_checkpoint = None
        self.pill_forge = MagicMock()
        self.pill_forge.is_online = False
        self.app_config = {"auto_generate_art": False}

        self._sync_turn = MagicMock()
        self._append_message = MagicMock()
        self._on_scene_art_requested = MagicMock()


class TestEndToEndRoleplayTurnPipeline:
    """Сквозной тест живого игрового цикла."""

    def test_full_roleplay_turn_from_prompt_to_art_plan(self):
        # =========================================================================
        # ЭТАП 1: Создание персонажа и персоны игрока
        # =========================================================================
        card = CharacterCard(
            name="Селеста",
            is_male=False,
            description="Могущественная чародейка. [Appearance: silver hair, purple eyes, slender build]",
            personality="Спокойная и наблюдательная",
            system_prompt="Помни: {{char}} предана {{user}}."
        )

        persona = {
            "name": "Роман",
            "gender": "male",
            "personality": "Опытный капитан",
            "backstory": "Исследователь древних руин"
        }

        # Сборка системного промпта через RoleplayPromptBuilder + MacroEngine
        system_prompt = RoleplayPromptBuilder.build(
            character_card=card,
            user_name="Роман",
            active_persona=persona
        )

        assert "Игрок (Роман) — мужчина. В прямой речи обращайся к Роман в мужском роде." in system_prompt
        assert "Селеста — женский персонаж" in system_prompt
        assert "Помни: Селеста предана Роман." in system_prompt
        assert "Категорически запрещено выходить из роли" in system_prompt

        # =========================================================================
        # ЭТАП 2: Реплика игрока с чувственным арт-триггером
        # =========================================================================
        host = IntegrationDummyHost(character=card)
        user_query = "Селеста, покажи себя в этом зале"
        host._current_user_request = user_query

        # Проверка срабатывания шлюза арт-директора
        should_art = host._should_trigger_roleplay_art(user_query)
        assert should_art is True

        # Формирование директивы режиссуры сцены
        directive = host._get_roleplay_art_directive(
            target_model_id="anime",
            companion_name=card.name,
            is_environment=False,
            has_minor=False
        )
        assert "[IMAGE_PROMPT: <english_danbooru_tags> --model anime]" in directive

        # =========================================================================
        # ЭТАП 3: Модель генерирует ответ с размышлениями и тегом генерации
        # =========================================================================
        simulated_model_raw_stream = (
            "<think>\n"
            "Игрок хочет увидеть меня. Опишу свое появление у окна и добавлю визуальный тег.\n"
            "</think>\n"
            "- Я медленно поворачиваюсь к тебе в лунном свете.\n"
            "[IMAGE_PROMPT: 1girl, solo, silver hair, purple eyes, moonlight, castle hall --model anime]\n"
            "— Тебе нравится этот вид?"
        )

        bubble = IntegrationFakeBubble()
        host._turn_bubble = bubble

        # Обработка ответа контроллером диалога
        with patch("services.roleplay_state_manager.RoleplayStateManager.process_model_turn", side_effect=lambda s, t: t), \
             patch("services.rp_cast_manager.RPCastManager.load_cast", return_value={}), \
             patch.object(host, "_trigger_roleplay_memory_extraction"):

            host._handle_roleplay_llm_response(simulated_model_raw_stream)

        # =========================================================================
        # ЭТАП 4: Проверка очистки текста ответа (TextRegexFilter)
        # =========================================================================
        # 1. Мысли <think> полностью вырезаны
        assert "<think>" not in bubble.text
        assert "Игрок хочет увидеть меня" not in bubble.text

        # 2. Тег генерации [IMAGE_PROMPT] убран из отображаемого чата
        assert "[IMAGE_PROMPT" not in bubble.text

        # 3. Дефис в начале реплики заменен на литературное тире (—)
        assert "— Я медленно поворачиваюсь" in bubble.text
        assert "— Тебе нравится этот вид?" in bubble.text

        # =========================================================================
        # ЭТАП 5: Проверка сформированного VisualIntent
        # =========================================================================
        assert bubble.visual_intent is not None
        v_intent = bubble.visual_intent
        assert isinstance(v_intent, VisualIntent)
        assert "1girl, solo, silver hair, purple eyes, moonlight, castle hall" in v_intent.prompt
        assert v_intent.suggested_model == "anime"
        assert v_intent.session_id == "session_e2e_turn"

        # =========================================================================
        # ЭТАП 6: Передача VisualIntent в ArtDirector и сборка GenerationPlan
        # =========================================================================
        with patch("services.art_director.director.ModelRouter") as mock_router_cls:
            mock_router = MagicMock()
            mock_router.match_prompt.return_value = ("anime", 0.95)
            mock_router.get_checkpoint_for_model.return_value = "meinahentai_v5.safetensors"
            mock_router.get_profile.return_value = {
                "id": "anime",
                "name": "MeinaHentai v5",
                "pipeline": {"steps": 25, "cfg_scale": 7.0}
            }
            mock_router.profiles = []
            mock_router_cls.return_value = mock_router

            director = ArtDirector()
            director.router = mock_router

            # Арт-директор строит план на основе реактивного VisualIntent
            plan = director.plan_from_visual_intent(
                visual_intent=v_intent,
                character=card,
                character_name=card.name,
                session_id=host.current_session["id"]
            )

            # Проверяем финальный контракт плана генерации для Forge:
            assert plan.target_checkpoint == "meinahentai_v5.safetensors"
            assert plan.steps == 25
            assert plan.cfg_scale == 7.0
            assert "silver hair" in plan.positive_prompt
            assert "purple eyes" in plan.positive_prompt
            assert plan.enable_hr is True
            # Для аниме ADetailer отключен во избежание искажения рисовки
            assert plan.enable_adetailer is False