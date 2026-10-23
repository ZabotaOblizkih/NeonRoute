"""
Сквозные интеграционные тесты (End-to-End Pipeline) для NeonRoute.
Проверяют совместную работу цепочки:
TavernParser -> TokenBudgetManager -> DirectCommandParser -> ModelRouter -> ArtDirector.
"""

import pytest
from unittest.mock import patch, MagicMock

from services.tavern_parser import TavernParser
from services.token_budget_manager import TokenBudgetManager
from services.art_director.director import ArtDirector
from services.model_router import ModelRouter


class TestEndToEndRoleplayToArtPipeline:
    """Сквозной сценарий: от импорта карточки персонажа до генерации финального плана кадра."""

    @pytest.fixture(autouse=True)
    def setup_router_isolation(self):
        """Гарантирует изоляцию роутера моделей на время теста."""
        ModelRouter._instance = None
        yield
        ModelRouter._instance = None

    def test_full_character_rp_to_art_pipeline(self):
        # =========================================================================
        # ШАГ 1: Загрузка и парсинг карточки персонажа (Tavern / Chub формат)
        # =========================================================================
        raw_card_data = {
            "spec": "chara_card_v2",
            "data": {
                "name": "Селеста",
                "tags": ["anime", "mage", "девушка"],
                "description": (
                    "Могущественная волшебница. "
                    "[Appearance: silver hair, purple eyes, slender build, silk robe]"
                ),
                "personality": "Гордая, но заботливая",
                "scenario": "[Setting: ancient library, glowing crystals, magic runes]",
                "first_mes": "*Селеста отрывает взгляд от фолианта и смотрит на тебя.* Здравствуй, путник."
            }
        }

        card = TavernParser.parse_dict(raw_card_data)

        assert card.name == "Селеста"
        assert card.is_male is False
        # Проверяем, что DNA внешности автоматически извлеклась (без одежды robe)
        assert "silver hair" in card.visual_dna
        assert "purple eyes" in card.visual_dna
        assert "silk robe" not in card.visual_dna

        # =========================================================================
        # ШАГ 2: Контроль бюджета токенов карточки перед началом диалога
        # =========================================================================
        base_tokens, lore_tokens = TokenBudgetManager.estimate_card_budget(card.to_dict())
        traffic = TokenBudgetManager.get_traffic_light(base_tokens)

        assert base_tokens > 0
        assert traffic["badge"] == "🟢"
        assert traffic["color"] == "#00FF66"

        # =========================================================================
        # ШАГ 3: Генерация кадра арт-директором во время ролевой сцены
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

            scene_prompt = "1girl, casting a glowing spell in the hall --ar wide --steps 28"

            plan = director.plan_generation(
                raw_prompt=scene_prompt,
                character=card,
                character_name=card.name,
                mode="roleplay"
            )

            # =====================================================================
            # ШАГ 4: Сквозная проверка итогового GenerationPlan для Forge
            # =====================================================================
            # 1. Внешность персонажа (DNA) встроилась в промпт:
            assert "silver hair" in plan.positive_prompt
            assert "purple eyes" in plan.positive_prompt

            # 2. Флаг --ar wide превратился в нативное альбомное разрешение SD:
            assert plan.width == 768
            assert plan.height == 512
            assert "--ar" not in plan.positive_prompt

            # 3. Для 2D-аниме ADetailer намеренно отключен в профиле модели,
            #    чтобы реальная YOLO-сетка не портила 2D-лицо:
            assert plan.enable_adetailer is False

            # 4. Hires.fix настроен:
            assert plan.enable_hr is True
            assert plan.hr_scale == 1.5

            # 5. Роутер корректно передал целевой чекпоинт:
            assert plan.target_checkpoint == "meinahentai_v5.safetensors"


class TestEndToEndDirectCommandPipeline:
    """Сквозной сценарий: от ручной команды со слешем (!арт) до параметров инференса."""

    def test_direct_art_command_full_pass(self):
        raw_user_input = (
            "!art cyberpunk warrior girl in rain <lora:neon_glow:0.65> "
            "--steps 30 --cfg 7.0 --seed 999999 --ar tall --neg low quality, blurry"
        )

        with patch("services.art_director.director.ModelRouter") as mock_router_cls:
            mock_router = MagicMock()
            mock_router.match_prompt.return_value = ("cyberrealistic", 0.90)
            mock_router.get_checkpoint_for_model.return_value = "CyberRealistic_V4.1_FP16.safetensors"
            mock_router_cls.return_value = mock_router

            director = ArtDirector()
            director.router = mock_router

            plan = director.build_direct_plan(raw_user_input)

            assert plan.seed == 999999
            assert plan.steps == 30
            assert plan.cfg_scale == 7.0
            assert plan.width == 512
            assert plan.height == 768
            assert "low quality, blurry" in plan.negative_prompt
            assert "!art" not in plan.positive_prompt
            assert "cyberpunk warrior girl in rain" in plan.positive_prompt