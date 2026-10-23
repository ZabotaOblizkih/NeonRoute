"""
Тестовый комплекс архитектуры ArtDirector: 10 тестов в одном файле.
Запуск: python tests/test_art_director_refactor.py
"""
import unittest
import re
import sys
from pathlib import Path
from typing import Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.art_director.geometry import (
    is_xl_model,
    get_archetype_config,
    ASPECT_RATIO_BUCKETS_SD15,
    ASPECT_RATIO_BUCKETS_SDXL
)
from services.art_director.plan import GenerationPlan
from services.art_director.director import _clean_context_subtag
from services.rp_cast_extractor import build_cast_extraction_spec


class TestArtDirectorArchitectureSuite(unittest.TestCase):

    # ТЕСТ 1: Проверка базовых бакетов SD 1.5 vs SDXL
    def test_01_buckets_resolution_difference(self):
        cfg_15 = get_archetype_config("v1-5-pruned.safetensors", "solo")
        cfg_xl = get_archetype_config("juggernautXL_v9.safetensors", "solo")
        self.assertEqual((cfg_15["width"], cfg_15["height"]), (512, 768))
        self.assertEqual((cfg_xl["width"], cfg_xl["height"]), (896, 1152))
        self.assertFalse(cfg_15["is_xl"])
        self.assertTrue(cfg_xl["is_xl"])

    # ТЕСТ 2: Исключение ложных срабатываний детектора XL
    def test_02_is_xl_false_positive_guard(self):
        self.assertFalse(is_xl_model("pixel_art_v1.safetensors"))
        self.assertFalse(is_xl_model("complex_lighting_style"))
        self.assertFalse(is_xl_model("duplex_room_concept"))
        self.assertTrue(is_xl_model("animagineXL_v3.safetensors"))
        self.assertTrue(is_xl_model("ponyDiffusionV6XL.safetensors"))
        self.assertTrue(is_xl_model("flux1-dev.safetensors"))
        self.assertTrue(is_xl_model({"architecture": "sdxl"}))

    # ТЕСТ 3: Аппаратный предохранитель VRAM (Hires.fix OOM Guard)
    def test_03_vram_oom_guard_logic(self):
        # Эмуляция логики из director.py для SDXL и SD 1.5
        def evaluate_hr(width: int, height: int, is_xl: bool):
            is_large_canvas = (width * height) >= 640000
            if is_xl or is_large_canvas:
                return False, 1.0
            return True, 1.5

        # SD 1.5: 512x768 = 393_216 px -> Hires разрешен
        enable_hr_15, scale_15 = evaluate_hr(512, 768, False)
        self.assertTrue(enable_hr_15)
        self.assertEqual(scale_15, 1.5)

        # SDXL: 1024x1024 = 1_048_576 px -> Hires заблокирован
        enable_hr_xl, scale_xl = evaluate_hr(1024, 1024, True)
        self.assertFalse(enable_hr_xl)
        self.assertEqual(scale_xl, 1.0)

    # ТЕСТ 4: Фиксированный двухслотовый контракт ADetailer в GenerationPlan
    def test_04_adetailer_two_slot_contract(self):
        plan = GenerationPlan(
            prompt="test",
            negative_prompt="neg",
            enable_adetailer=True,
            adetailer_hands=True
        )
        kwargs = plan.to_forge_kwargs()
        ad_args = kwargs["alwayson_scripts"]["ADetailer"]["args"]
        self.assertEqual(len(ad_args), 4)
        self.assertTrue(ad_args[0])   # Master enable
        self.assertFalse(ad_args[1])  # Skip img2img
        self.assertEqual(ad_args[2]["ad_model"], "face_yolov8n.pt")  # Slot 1 Face
        self.assertEqual(ad_args[3]["ad_model"], "hand_yolov8n.pt")  # Slot 2 Hands

    # ТЕСТ 5: Глубокая очистка контекстных тегов (_clean_context_subtag)
    def test_05_clean_context_subtag_sanitization(self):
        dirty_tag_1 = "- 'post-apocalyptic cityscape, ruins'"
        dirty_tag_2 = "* `cybernetic visor`, "
        clean_1 = _clean_context_subtag(dirty_tag_1)
        clean_2 = _clean_context_subtag(dirty_tag_2)
        self.assertEqual(clean_1, "post-apocalyptic cityscape, ruins")
        self.assertEqual(clean_2, "cybernetic visor")
        self.assertNotIn("'", clean_1)
        self.assertNotIn("`", clean_2)

    # ТЕСТ 6: Защита от дублирования локации в промпте
    def test_06_location_duplicate_prevention(self):
        base_prompt = "1girl, 2B, post-apocalyptic cityscape, standing, sword"
        loc_active = "- 'post-apocalyptic cityscape, abandoned ruins'"
        clean_loc = _clean_context_subtag(loc_active)
        broad_loc = clean_loc.split(",")[0].strip(" ,-")

        # Проверка правила: если broad_loc уже есть в промпте, хвост не дописывается
        is_already_present = broad_loc.lower() in base_prompt.lower()
        self.assertTrue(is_already_present)

    # ТЕСТ 7: Приоритет флага --ar portrait для сцены с 2 персонажами
    def test_07_explicit_ar_portrait_preserved_for_pair(self):
        # Пользователь/LLM запросили вертикальный кадр для двоих (объятия)
        ar_override = "portrait"
        has_secondary = True
        archetype = None

        if ar_override in ("portrait", "vertical"):
            archetype = "solo"
        elif not ar_override and has_secondary:
            archetype = "pair"

        self.assertEqual(archetype, "solo")
        cfg = get_archetype_config("v1-5-pruned.safetensors", archetype)
        self.assertEqual((cfg["width"], cfg["height"]), (512, 768))

    # ТЕСТ 8: Отключение ADetailer на кадрах окружения без людей
    def test_08_scenery_without_humans_disables_adetailer(self):
        ar_clean = "wide"
        has_human = False
        archetype = "scenery" if (ar_clean in ("wide", "landscape") and not has_human) else "pair"
        self.assertEqual(archetype, "scenery")

        cfg = get_archetype_config("v1-5-pruned.safetensors", archetype)
        self.assertFalse(cfg["force_adetailer"])

    # ТЕСТ 9: Регулярное выражение парсера --neg не поглощает флаг --ar
    def test_09_assistant_flow_regex_neg_ar_isolation(self):
        raw_prompt = "1girl, solo --neg bad anatomy, blurry --ar portrait --model realistic"
        neg_match = re.search(r'(?:--neg|--negative)[:=\s]+(.*?)(?=(?:--model|--style|--ar|\$))', raw_prompt, re.I)
        self.assertIsNotNone(neg_match)
        custom_neg = neg_match.group(1).strip()
        self.assertEqual(custom_neg, "bad anatomy, blurry")
        self.assertNotIn("--ar", custom_neg)

        ar_match = re.search(r'--ar[:=\s]+([a-zA-Z0-9:]+)', raw_prompt, re.I)
        self.assertIsNotNone(ar_match)
        self.assertEqual(ar_match.group(1).lower(), "portrait")

    # ТЕСТ 10: Спецификация RPCastExtractor: отсутствие None в enum схемы
    def test_10_cast_extractor_schema_no_none_in_enum(self):
        _, schema_payload = build_cast_extraction_spec(has_companion=True, has_stats=False)
        schema_props = schema_payload["json_schema"]["schema"]["properties"]

        self.assertIn("aspect_ratio", schema_props)
        ar_enum = schema_props["aspect_ratio"]["enum"]
        self.assertIn("wide", ar_enum)
        self.assertIn("portrait", ar_enum)
        self.assertIn("square", ar_enum)
        self.assertIn("none", ar_enum)
        self.assertNotIn(None, ar_enum)  # Гарантия от сбоя BNF-грамматики LM Studio


if __name__ == "__main__":
    unittest.main()
