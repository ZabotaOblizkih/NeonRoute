"""
Тесты для direct_command_parser (проверка извлечения CLI-флагов генерации и негативного промпта).
"""

import pytest
from services.direct_command_parser import (
    parse_direct_command_params,
    parse_user_negative_trigger
)


class TestDirectCommandNumericParams:
    """Проверка извлечения базовых параметров генерации (steps, cfg, denoise, seed)."""

    def test_extract_steps_and_cfg(self):
        text = "masterpiece, 1girl --steps 28 --cfg 6.5 in the garden"
        pos, neg, params = parse_direct_command_params(text)

        assert params["steps"] == 28
        assert params["cfg_scale"] == 6.5
        assert "--steps" not in pos
        assert "--cfg" not in pos
        assert "masterpiece, 1girl" in pos
        assert "in the garden" in pos

    def test_extract_denoising_strength(self):
        # Поддерживаются оба варианта: --denoise и --dn
        _, _, params_full = parse_direct_command_params("portrait --denoise 0.45")
        assert params_full["denoising_strength"] == 0.45

        _, _, params_short = parse_direct_command_params("portrait --dn 0.3")
        assert params_short["denoising_strength"] == 0.3

    def test_extract_seed(self):
        _, _, params_pos = parse_direct_command_params("cyberpunk city --seed 424242")
        assert params_pos["seed"] == 424242

        # Отрицательный сид (случайный)
        _, _, params_neg = parse_direct_command_params("cyberpunk city --seed -1")
        assert params_neg["seed"] == -1


class TestDirectCommandHiresAndAdetailer:
    """Проверка флагов Hires.fix и ADetailer."""

    def test_hires_boolean_flags(self):
        _, _, p_on = parse_direct_command_params("scenery --hires on")
        assert p_on["enable_hr"] is True

        _, _, p_off = parse_direct_command_params("scenery --hr false")
        assert p_off["enable_hr"] is False

    def test_hires_scale_factor(self):
        # Передача коэффициента апскейла напрямую в --hr
        _, _, p_scale = parse_direct_command_params("scenery --hr 1.75")
        assert p_scale["enable_hr"] is True
        assert p_scale["hr_scale"] == 1.75

        # Использование отдельного флага --scale
        _, _, p_scale_sep = parse_direct_command_params("scenery --scale 2.0")
        assert p_scale_sep["enable_hr"] is True
        assert p_scale_sep["hr_scale"] == 2.0

    def test_adetailer_flag(self):
        _, _, p_ad_on = parse_direct_command_params("portrait --ad on")
        assert p_ad_on["enable_adetailer"] is True

        _, _, p_ad_off = parse_direct_command_params("portrait --adetailer off")
        assert p_ad_off["enable_adetailer"] is False


class TestDirectCommandResolutionAndModels:
    """Проверка соотношения сторон, явных разрешений и подмены модели."""

    def test_aspect_ratio_wide(self):
        for ar in ("wide", "16:9", "3:2"):
            _, _, params = parse_direct_command_params(f"landscape --ar {ar}")
            assert params["width"] == 768
            assert params["height"] == 512

    def test_aspect_ratio_tall(self):
        for ar in ("tall", "9:16", "2:3"):
            _, _, params = parse_direct_command_params(f"portrait --ar {ar}")
            assert params["width"] == 512
            assert params["height"] == 768

    def test_aspect_ratio_square(self):
        for ar in ("square", "1:1"):
            _, _, params = parse_direct_command_params(f"avatar --ar {ar}")
            assert params["width"] == 512
            assert params["height"] == 512

    def test_custom_size_and_w_h(self):
        # Через синтаксис WxH
        _, _, p_size = parse_direct_command_params("banner --size 1024x576")
        assert p_size["width"] == 1024
        assert p_size["height"] == 576

        # Через раздельные флаги --w и --h
        _, _, p_wh = parse_direct_command_params("banner --w 800 --h 600")
        assert p_wh["width"] == 800
        assert p_wh["height"] == 600

    def test_model_override(self):
        # Имя модели автоматически приводится к нижнему регистру
        _, _, params = parse_direct_command_params("anime style --model MeinaHentai_v5")
        assert params["model_override"] == "meinahentai_v5"

        _, _, p_ckpt = parse_direct_command_params("real photo --ckpt RealisticVision")
        assert p_ckpt["model_override"] == "realisticvision"


class TestDirectCommandNegativePrompt:
    """Проверка корректного отделения позитивного и негативного промптов."""

    def test_no_negative_prompt(self):
        pos, neg, params = parse_direct_command_params("cyberpunk alley, neon light")
        assert pos == "cyberpunk alley, neon light"
        assert neg == ""
        assert params == {}

    def test_cli_negative_flag(self):
        text = "cat on a table --steps 20 --negative low quality, blurry"
        pos, neg, params = parse_direct_command_params(text)

        assert pos == "cat on a table"
        assert neg == "low quality, blurry"
        assert params["steps"] == 20

    def test_cli_neg_short_flag(self):
        text = "magic forest --neg bad anatomy, extra limbs"
        pos, neg, _ = parse_direct_command_params(text)

        assert pos == "magic forest"
        assert neg == "bad anatomy, extra limbs"

    def test_russian_negative_triggers(self):
        # Триггер "негатив:"
        pos1, neg1, _ = parse_direct_command_params("ночной город негатив: размытие, шум")
        assert pos1 == "ночной город"
        assert neg1 == "размытие, шум"

        # Триггер "негативный промпт:"
        pos2, neg2, _ = parse_direct_command_params("девушка воин негативный промпт: артефакты, дефекты")
        assert pos2 == "девушка воин"
        assert neg2 == "артефакты, дефекты"

    def test_parse_user_negative_trigger_helper(self):
        has_neg, pos, neg = parse_user_negative_trigger("space station negative: blur")
        assert has_neg is True
        assert pos == "space station"
        assert neg == "blur"

        has_neg_none, pos_none, neg_none = parse_user_negative_trigger("space station without neg")
        assert has_neg_none is False
        assert pos_none == "space station without neg"
        assert neg_none == ""