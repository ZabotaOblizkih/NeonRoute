"""
Тесты для ModelRouter (services/model_router.py).
Проверка нормализации выбора модели, поиска по триггерам, 
управления профилями и пре-роутинга запросов.
"""

import json
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from pathlib import Path

from services.model_router import (
    ModelRouter,
    normalize_model_choice,
    DEFAULT_ROUTER_MODELS
)


@pytest.fixture(autouse=True)
def reset_router_singleton():
    """Сбрасывает синглтон перед каждым тестом для изоляции."""
    ModelRouter._instance = None
    yield
    ModelRouter._instance = None


class TestNormalizeModelChoice:
    """Проверка очистки UI-заглушек и пользовательского выбора модели."""

    def test_empty_and_falsy_inputs(self):
        assert normalize_model_choice(None) is None
        assert normalize_model_choice("") is None
        assert normalize_model_choice("   ") is None
        assert normalize_model_choice(123) is None

    def test_ui_auto_placeholders(self):
        # Все варианты авто-режима и заглушек должны сбрасываться в None
        assert normalize_model_choice("✨ Арт-директор (Авто)") is None
        assert normalize_model_choice("✨ Любой текст с искрой") is None
        assert normalize_model_choice("default") is None
        assert normalize_model_choice("DEFAULT (АВТО)") is None
        assert normalize_model_choice("auto") is None
        assert normalize_model_choice("авто") is None
        assert normalize_model_choice("none") is None
        assert normalize_model_choice("null") is None
        assert normalize_model_choice("арт-директор (авто)") is None

    def test_valid_custom_styles_preserved(self):
        assert normalize_model_choice("cyberrealistic") == "cyberrealistic"
        assert normalize_model_choice("  anime  ") == "anime"
        assert normalize_model_choice("Realistic Vision v6.0") == "Realistic Vision v6.0"
        assert normalize_model_choice("my_custom_model.safetensors") == "my_custom_model.safetensors"


class TestModelProfileManagement:
    """Проверка загрузки, сохранения и поиска профилей моделей."""

    @pytest.fixture
    def custom_router(self, tmp_path):
        """Создает изолированный инстанс ModelRouter с временным файлом JSON."""
        test_file = tmp_path / "test_models.json"
        router = ModelRouter(data_path=test_file, auto_load=False)
        router.profiles = [dict(m) for m in DEFAULT_ROUTER_MODELS]
        return router

    def test_save_profiles_guarantees_single_default(self, custom_router):
        # Намеренно делаем две модели дефолтными
        custom_router.profiles[0]["is_default"] = True
        custom_router.profiles[1]["is_default"] = True

        custom_router.save_profiles()

        # Первая должна остаться дефолтной, вторая сброшена в False
        assert custom_router.profiles[0]["is_default"] is True
        assert custom_router.profiles[1]["is_default"] is False

    def test_update_profile_resets_other_defaults(self, custom_router):
        # Исходно дефолтная модель — realistic (индекс 0)
        assert custom_router.profiles[0]["id"] == "realistic"
        assert custom_router.profiles[0]["is_default"] is True

        # Назначаем дефолтной модель anime
        success = custom_router.update_profile("anime", {"is_default": True})
        assert success is True

        # Проверяем переключение
        realistic_p = custom_router.get_profile("realistic")
        anime_p = custom_router.get_profile("anime")

        assert anime_p["is_default"] is True
        assert realistic_p["is_default"] is False

    def test_get_profile_lookup_variants(self, custom_router):
        # Поиск по id
        assert custom_router.get_profile("anime")["id"] == "anime"

        # Поиск по имени модели (name)
        assert custom_router.get_profile("CyberRealistic Classic")["id"] == "cyberrealistic"

        # Поиск по имени файла чекпоинта и без расширения (стэму)
        assert custom_router.get_profile("dreamshaper_8.safetensors")["id"] == "artistic"
        assert custom_router.get_profile("dreamshaper_8")["id"] == "artistic"

        # Несуществующая модель
        assert custom_router.get_profile("non_existing_model") is None
        assert custom_router.get_profile(None) is None

    def test_get_checkpoint_for_model(self, custom_router):
        ckpt = custom_router.get_checkpoint_for_model("cyberrealistic")
        assert ckpt == "CyberRealistic_V4.1_FP16.safetensors"

        ckpt_by_name = custom_router.get_checkpoint_for_model("MeinaHentai v5")
        assert ckpt_by_name == "meinahentai_v5.safetensors"

        assert custom_router.get_checkpoint_for_model("unknown") is None


class TestMatchingLogic:
    """Проверка сопоставления запросов с профилями моделей."""

    @pytest.fixture
    def router(self, tmp_path):
        test_file = tmp_path / "models.json"
        r = ModelRouter(data_path=test_file, auto_load=False)
        r.profiles = [dict(m) for m in DEFAULT_ROUTER_MODELS]
        return r

    def test_empty_profiles_returns_none(self, router):
        winner, score, reason = router.match_prompt_detailed("любой промпт", profiles=[])
        assert winner is None
        assert score == 0.0
        assert reason == "none"

    def test_style_triggers_priority(self, router):
        # Слово "аниме" есть в style_triggers модели 'anime'
        winner, score, reason = router.match_prompt_detailed("красивая девушка, стиль аниме, 8k")
        assert winner == "anime"
        assert score == 0.95
        assert reason == "trigger"

        # Слово "акварель" есть в style_triggers модели 'artistic'
        winner_art, score_art, reason_art = router.match_prompt_detailed("пейзаж горная река акварель")
        assert winner_art == "artistic"
        assert score_art == 0.95
        assert reason_art == "trigger"

    def test_neutral_prompt_falls_back_to_default_model(self, router):
        # Запрос без триггеров и без векторного движка должен попадать в default (realistic)
        winner, score, reason = router.match_prompt_detailed("обычная комната, стол и стул")
        assert winner == "realistic"
        assert score >= 0.85
        assert reason == "default"

    def test_vector_matching_with_mocked_embeddings(self, router):
        # Создаем искусственные эмбеддинги: cyberrealistic получает максимальный скор
        router.embeddings = {
            "realistic": np.array([0.1, 0.1, 0.0], dtype=np.float32),
            "cyberrealistic": np.array([0.9, 0.9, 0.9], dtype=np.float32),
            "artistic": np.array([0.1, 0.0, 0.1], dtype=np.float32),
            "anime": np.array([0.0, 0.1, 0.1], dtype=np.float32)
        }
        # Нормализуем векторы
        for k in router.embeddings:
            router.embeddings[k] /= np.linalg.norm(router.embeddings[k])

        mock_engine = MagicMock()
        # Вектор запроса точно совпадает с cyberrealistic
        query_vec = router.embeddings["cyberrealistic"].copy()
        mock_engine.embed.return_value = [query_vec]

        with patch.object(router, "get_embedding_engine", return_value=mock_engine):
            winner, score, reason = router.match_prompt_detailed("futuristic cyborg in neon city")
            assert winner == "cyberrealistic"
            assert reason == "vector"
            assert score >= 0.35


class TestPreRoute:
    """Проверка предварительной классификации запросов в чате."""

    @pytest.fixture
    def router(self, tmp_path):
        test_file = tmp_path / "models.json"
        r = ModelRouter(data_path=test_file, auto_load=False)
        r.profiles = [dict(m) for m in DEFAULT_ROUTER_MODELS]
        return r

    def test_non_visual_query_is_ignored(self, router):
        # Обычный разговор не должен триггерить роутинг арта
        model_id, guidance = router.pre_route("Привет! Расскажи мне про квантовую физику.")
        assert model_id == "realistic"
        assert guidance == ""

    def test_visual_query_detects_style_and_guidance(self, router):
        # Запрос с глаголом "нарисуй" и стилем "аниме"
        model_id, guidance = router.pre_route("нарисуй пожалуйста девушку в стиле аниме")
        assert model_id == "anime"
        assert "<ArtGuidance>" in guidance
        assert "[Целевой стиль: MeinaHentai v5]" in guidance
        assert "Стиль: Чистый 2D аниме" in guidance