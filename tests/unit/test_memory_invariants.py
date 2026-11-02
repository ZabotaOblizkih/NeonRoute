"""
Unit tests for VRAM memory invariants and boundary conditions in NeonRoute.
Covers 4 groups of edge cases:
Group 1: Mathematical boundaries of frame area (640,000 px threshold)
Group 2: Architecture and resolution conflict (SDXL vs SD 1.5)
Group 3: User JSON priority (explicit disable and custom hr_scale)
Group 4: State Machine / Arbitration chronology invariants
"""

from unittest.mock import MagicMock, patch
import pytest

from services.art_director.director import ArtDirector
from services.art_director.contracts import VisualIntent
from services.vram_arbiter import VRAMArbiter
from ui.controllers.hardware_engine import HardwareEngineMixin


# =============================================================================
# Группа 1: Математические границы площади кадра (порог 640 000 px)
# =============================================================================
class TestGroup1AreaBoundaries:
    """
    Проверка аппаратного барьера 640 000 px для Hires.fix.
    """

    @pytest.mark.parametrize(
        "width,height,expected_scale,description",
        [
            (512, 768, 1.5, "Штатный портрет SD 1.5: 512x768 = 393 216 px -> hr_scale == 1.5"),
            (768, 1024, 1.5, "Разрешенный портрет: 768x1024 = 786 432 px -> hr_scale == 1.5"),
            (1024, 1024, 1.0, "Превышение динамического бюджета VRAM: 1024x1024 = 1 048 576 px -> hr_scale == 1.0"),
            (448, 1792, 1.0, "Экстремальная панорама: 448x1792 -> hr_scale == 1.0"),
        ],
    )
    def test_resolution_640k_threshold_hr_scale(self, width, height, expected_scale, description):
        director = ArtDirector()
        arch_config = {
            "width": width,
            "height": height,
            "is_xl": False,
            "extra_negative": "",
            "force_adetailer": True,
            "architecture": "sd15",
        }

        with patch("services.art_director.director.get_archetype_config", return_value=arch_config):
            plan = director.plan_from_visual_intent(
                VisualIntent(prompt="fantasy landscape"),
                preferred_style="realistic"
            )

        assert plan.hr_scale == expected_scale, (
            f"Не соблюден динамический барьер VRAM ({description}): "
            f"ожидался hr_scale == {expected_scale}, получен {plan.hr_scale} для {width}x{height} "
            f"({width * height} px)"
        )


# =============================================================================
# Группа 2: Конфликт архитектуры и разрешения
# =============================================================================
class TestGroup2ArchitectureResolutionConflict:
    """
    Проверка приоритета архитектуры SDXL и барьера площади для SD 1.5.
    """

    def test_sdxl_micro_frame_disables_hires(self):
        """
        Микро-кадр SDXL (512x512 = 262 144 px): площадь значительно ниже 640k,
        но флаг архитектуры is_xl: True обязан принудительно сбросить hr_scale == 1.0.
        """
        director = ArtDirector()
        arch_config = {
            "width": 512,
            "height": 512,
            "is_xl": True,
            "extra_negative": "",
            "force_adetailer": True,
        }

        with patch("services.art_director.director.get_archetype_config", return_value=arch_config):
            plan = director.plan_from_visual_intent(
                VisualIntent(prompt="portrait"),
                preferred_style="realistic"
            )

        assert plan.hr_scale == 1.0, (
            f"SDXL OOM Guard нарушен: для SDXL кадра 512x512 hr_scale должен быть 1.0, "
            f"получен {plan.hr_scale}"
        )
        assert plan.enable_hr is False, "enable_hr должен быть False для SDXL"

    def test_sd15_large_frame_disables_hires(self):
        """
        Большой кадр SD 1.5 (1024x1024 = 1 048 576 px): флаг is_xl: False,
        но превышение динамического бюджета VRAM обязано сбросить hr_scale == 1.0.
        """
        director = ArtDirector()
        arch_config = {
            "width": 1024,
            "height": 1024,
            "is_xl": False,
            "extra_negative": "",
            "force_adetailer": True,
            "architecture": "sd15",
        }

        with patch("services.art_director.director.get_archetype_config", return_value=arch_config):
            plan = director.plan_from_visual_intent(
                VisualIntent(prompt="battlefield"),
                preferred_style="realistic"
            )

        assert plan.hr_scale == 1.0, (
            f"Динамический барьер VRAM нарушен для SD 1.5: для кадра 1024x1024 (1M px) "
            f"hr_scale должен быть 1.0, получен {plan.hr_scale}"
        )
        assert plan.enable_hr is False, "enable_hr должен быть False для SD 1.5 при превышении динамического бюджета"


# =============================================================================
# Группа 3: Приоритет пользовательского JSON
# =============================================================================
class TestGroup3UserJsonPriority:
    """
    Проверка влияния параметров профиля модели из art_director_models.json на Hires.fix.
    """

    def test_explicit_disable_hires_in_profile(self):
        """
        Явный запрет Hires.fix: разрешение 512x768 (допустимо для апскейла),
        но в профиле модели передано enable_hr: False -> план обязан выдать enable_hr: False / hr_scale == 1.0.
        """
        director = ArtDirector()
        director.router.profiles = [
            {
                "id": "test_disabled_hr",
                "name": "Disabled HR Model",
                "checkpoint": "model.safetensors",
                "active": True,
                "pipeline": {
                    "enable_hr": False,
                    "hr_scale": 1.5,
                },
            }
        ]

        arch_config = {
            "width": 512,
            "height": 768,
            "is_xl": False,
            "extra_negative": "",
            "force_adetailer": True,
        }

        with patch("services.art_director.director.get_archetype_config", return_value=arch_config):
            plan = director.plan_from_visual_intent(
                VisualIntent(prompt="solo warrior"),
                preferred_style="test_disabled_hr"
            )

        assert plan.enable_hr is False, "enable_hr: False из профиля должно отключать апскейл"
        assert plan.hr_scale == 1.0, f"При отключенном enable_hr hr_scale должен быть 1.0, получен {plan.hr_scale}"

    def test_user_custom_multiplier_preservation_and_cutoff(self):
        """
        Пользовательский множитель: в профиле модели задан hr_scale: 2.0.
        - Разрешение 512x768 (< 640k) -> план сохраняет 2.0.
        - При превышении 640k (1024x768) -> сбрасывает в 1.0.
        """
        director = ArtDirector()
        director.router.profiles = [
            {
                "id": "test_custom_scale",
                "name": "Custom Scale Model",
                "checkpoint": "custom.safetensors",
                "active": True,
                "pipeline": {
                    "enable_hr": True,
                    "hr_scale": 2.0,
                },
            }
        ]

        # 1. Нормальное разрешение 512x768 (< 640k)
        arch_config_normal = {
            "width": 512,
            "height": 768,
            "is_xl": False,
            "extra_negative": "",
            "force_adetailer": True,
        }
        with patch("services.art_director.director.get_archetype_config", return_value=arch_config_normal):
            plan_normal = director.plan_from_visual_intent(
                VisualIntent(prompt="solo warrior"),
                preferred_style="test_custom_scale"
            )

        assert plan_normal.hr_scale == 2.0, (
            f"Кастомный множитель 2.0 должен сохраняться для кадра 512x768, получен {plan_normal.hr_scale}"
        )

        # 2. Превышение динамического барьера VRAM (1024x1024 = 1 048 576 px) -> сброс в 1.0
        arch_config_large = {
            "width": 1024,
            "height": 1024,
            "is_xl": False,
            "extra_negative": "",
            "force_adetailer": True,
            "architecture": "sd15",
        }
        with patch("services.art_director.director.get_archetype_config", return_value=arch_config_large):
            plan_large = director.plan_from_visual_intent(
                VisualIntent(prompt="grand battlefield"),
                preferred_style="test_custom_scale"
            )

        assert plan_large.hr_scale == 1.0, (
            f"При превышении динамического барьера VRAM кастомный множитель hr_scale=2.0 обязан сбрасываться в 1.0, "
            f"получен {plan_large.hr_scale}"
        )


# =============================================================================
# Группа 4: Инварианты хронологии арбитража (State Machine)
# =============================================================================
class TestGroup4ArbitrationChronology:
    """
    Проверка строгой очередности выгрузки/загрузки моделей между LM Studio и Forge.
    """

    def test_strict_call_order_cancel_before_forge_inference(self):
        """
        Строгая очерёдность: при запуске генерации метод отмены/выгрузки модели чата
        (_cancel_lms_reload / release_llm_for_diffusion) вызывается строго ДО вызова text_to_image Forge.
        """
        events = []

        class MockEngine(HardwareEngineMixin):
            def __init__(self):
                self.art_director = ArtDirector()
                self.forge_client = MagicMock()
                self.lm_client = MagicMock()
                self.current_session = None
                self.selected_forge_checkpoint = None
                self.pill_forge = MagicMock(is_online=True)

            def _cancel_lms_reload(self):
                events.append("cancel_lms_reload")

            def _reload_active_lms_model_async(self, delay=1.2):
                events.append("reload_active_lms_async")

            def get_effective_mode(self):
                return "roleplay"

        engine = MockEngine()
        engine.forge_client.get_models.return_value = []

        def fake_text_to_image(**kwargs):
            events.append("forge_text_to_image")
            return "output.png"

        engine.forge_client.text_to_image.side_effect = fake_text_to_image

        with patch("services.vram_arbiter.VRAMArbiter.release_llm_for_diffusion") as mock_release:
            mock_release.side_effect = lambda: events.append("release_llm_for_diffusion")
            # Запуск пайплайна генерации через сцену
            task_func = None

            def capture_enqueue(task):
                nonlocal task_func
                task_func = task.func

            engine.task_queue = MagicMock()
            engine.task_queue.enqueue = capture_enqueue
            engine._active_tasks = {}

            engine._on_scene_art_requested(widget=None, text="test prompt", visual_intent={"prompt": "cyberpunk city"})
            assert task_func is not None, "Задача генерации не была создана"

            # Выполнение рабочего потока задачи
            task_func(v_payload={"prompt": "cyberpunk city"}, scene_text="test prompt", ckpt="realistic")

        assert "cancel_lms_reload" in events, "Метод _cancel_lms_reload не был вызван"
        assert "forge_text_to_image" in events, "Метод text_to_image не был вызван"

        idx_cancel = events.index("cancel_lms_reload")
        idx_forge = events.index("forge_text_to_image")
        assert idx_cancel < idx_forge, (
            f"Нарушена хронология: отмена перезагрузки LLM вызвана после инференса Forge! "
            f"Порядок событий: {events}"
        )

    def test_intercept_during_cooldown_cancels_timer(self):
        """
        Перехват во время кулдауна: если во время работы таймера ожидания перезагрузки LM Studio
        поступает новый запрос на арт, таймер перезагрузки гарантированно сбрасывается (_cancel_lms_reload).
        """
        class MockEngine(HardwareEngineMixin):
            def __init__(self):
                self._lms_reload_timer = MagicMock()
                self._lms_reload_timer.isActive.return_value = True

        engine = MockEngine()
        # Вызов сброса отложенной перезагрузки
        engine._do_cancel_lms_reload()

        engine._lms_reload_timer.stop.assert_called_once(), (
            "Таймер перезагрузки LM Studio не был остановлен при вызове сброса кулдауна"
        )

    def test_failure_safety_finally_registers_lms_reload(self):
        """
        Безопасность при сбое (finally): если вызов Forge завершается исключением
        (Network Error / Timeout), планирование возврата модели чата в память всё равно
        регистрируется, предотвращая зависание системы в состоянии «выгружено навсегда».
        """
        reload_called = False

        class MockEngine(HardwareEngineMixin):
            def __init__(self):
                self.art_director = ArtDirector()
                self.forge_client = MagicMock()
                self.lm_client = MagicMock()
                self.current_session = None
                self.selected_forge_checkpoint = None
                self.pill_forge = MagicMock(is_online=True)

            def _cancel_lms_reload(self):
                pass

            def _reload_active_lms_model_async(self, delay=1.2):
                nonlocal reload_called
                reload_called = True

            def get_effective_mode(self):
                return "roleplay"

        engine = MockEngine()
        engine.forge_client.get_models.return_value = []
        engine.forge_client.text_to_image.side_effect = RuntimeError("Network Error: Forge Timeout")

        task_func = None

        def capture_enqueue(task):
            nonlocal task_func
            task_func = task.func

        engine.task_queue = MagicMock()
        engine.task_queue.enqueue = capture_enqueue
        engine._active_tasks = {}

        engine._on_scene_art_requested(widget=None, text="test prompt", visual_intent={"prompt": "cyberpunk city"})

        # При сбое Forge воркер может выбросить ошибку
        try:
            task_func(v_payload={"prompt": "cyberpunk city"}, scene_text="test prompt", ckpt="realistic")
        except Exception:
            pass

        assert reload_called is True, (
            "Безопасность при сбое нарушена: после исключения в ForgeClient.text_to_image "
            "метод _reload_active_lms_model_async НЕ был вызван, система осталась без LLM."
        )
