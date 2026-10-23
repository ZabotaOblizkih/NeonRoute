"""
Тесты для ArtDirector (services/art_director/director.py).
Проверка распределения экипировки, синхронизации BREAK-чанков,
подмешивания guidance-тегов и прямого ручного инференса (!арт).
"""

import pytest
from unittest.mock import patch, MagicMock

from services.art_director.director import (
    ArtDirector,
    _distribute_inventory_gear,
    _resolve_inventory_gear,
    _symmetrize_break_chunks,
    _mix_guidance_tags,
    WEAPON_TAG_MAP,
    PEACEFUL_SCENE_MARKERS
)


class TestInventoryGearLogic:
    """Проверка распределения оружия и фильтрации мирных сцен."""

    def test_peaceful_scene_strips_all_weapons(self):
        # В мирной обстановке оружие строго блокируется
        inventory = ["Острый меч", "Катана тени", "Боевой лук"]
        focus_gear, sec_gear = _distribute_inventory_gear(
            inventory_items=inventory,
            is_peaceful=True,
            focus_name="Алиса"
        )
        assert focus_gear == []
        assert sec_gear == []

        # Функция быстрого резолвинга также возвращает пустоту
        assert _resolve_inventory_gear(inventory, is_peaceful=True) == []

    def test_distribute_gear_by_companion_names(self):
        # Именное оружие должно распределяться строго по слотам персонажей
        inventory = ["Катана Алисы", "Лук Боба", "Стальной щит"]
        focus_gear, sec_gear = _distribute_inventory_gear(
            inventory_items=inventory,
            is_peaceful=False,
            focus_name="Алиса",
            secondary_name="Боб"
        )

        assert "holding katana" in focus_gear
        assert "holding bow" in sec_gear
        # Нераспределенный щит достается персонажу без слота
        assert "holding shield" in focus_gear or "holding shield" in sec_gear

    def test_unarmed_items_ignored(self):
        # Бытовые предметы без маркеров оружия не должны превращаться в теги экипировки
        inventory = ["Золотое яблоко", "Лечебное зелье", "Старая книга"]
        focus_gear, sec_gear = _distribute_inventory_gear(
            inventory_items=inventory,
            is_peaceful=False,
            focus_name="Алиса"
        )
        assert focus_gear == []
        assert sec_gear == []


class TestMixGuidanceTags:
    """Проверка подмешивания и дедупликации llm_guidance тегов модели."""

    def test_mix_guidance_no_duplicates(self):
        prompt = "1girl, solo, masterpiece"
        guidance = "masterpiece, best quality, ultra detailed, sharp focus"

        result = _mix_guidance_tags(prompt, guidance, max_tags=8)

        # Тег 'masterpiece' не должен повториться дважды
        assert result.count("masterpiece") == 1
        assert "best quality" in result
        assert "ultra detailed" in result

    def test_mix_guidance_respects_max_tags(self):
        prompt = "landscape"
        guidance = "tag1, tag2, tag3, tag4, tag5, tag6, tag7, tag8, tag9, tag10"

        # Ограничиваем первыми 3 тегами
        result = _mix_guidance_tags(prompt, guidance, max_tags=3)
        assert "tag1" in result
        assert "tag2" in result
        assert "tag3" in result
        assert "tag4" not in result

    def test_mix_guidance_empty_inputs(self):
        assert _mix_guidance_tags("", "masterpiece, 8k") == "masterpiece, 8k"
        assert _mix_guidance_tags("1girl", "") == "1girl"
        assert _mix_guidance_tags("1girl", None) == "1girl"


class TestSymmetrizeBreakChunks:
    """Проверка синхронизации окружения для мульти-персонажей через BREAK."""

    def test_no_break_returns_unchanged(self):
        prompt = "1girl, standing in the garden"
        result = _symmetrize_break_chunks(prompt, loc_suffix="ancient temple", light_suffix="night")
        assert result == prompt

    def test_symmetrizes_both_chunks(self):
        prompt = "1girl, red dress BREAK 1girl, blue dress"
        loc_suffix = "ancient temple, stone pillars"
        light_suffix = "torchlight"

        result = _symmetrize_break_chunks(prompt, loc_suffix=loc_suffix, light_suffix=light_suffix)

        chunks = result.split(" BREAK ")
        assert len(chunks) == 2
        # Обе стороны BREAK должны получить общее окружение
        assert "ancient temple" in chunks[0]
        assert "ancient temple" in chunks[1]

    def test_does_not_duplicate_existing_location(self):
        # Если чанк уже содержит имя локации, оно не дублируется
        prompt = "1girl, red dress, ancient temple BREAK 1girl, blue dress"
        result = _symmetrize_break_chunks(prompt, loc_suffix="ancient temple", light_suffix="")
        chunks = result.split(" BREAK ")

        assert chunks[0].count("ancient temple") == 1
        assert "ancient temple" in chunks[1]


class TestDirectArtPlan:
    """Проверка прямого ручного инференса (!арт / !art)."""

    @pytest.fixture
    def director(self):
        with patch("services.art_director.director.ModelRouter"):
            d = ArtDirector()
            d.router = MagicMock()
            d.router.match_prompt.return_value = ("realistic", 0.95)
            d.router.get_checkpoint_for_model.return_value = "CyberRealistic.safetensors"
            d.router.profiles = []
            return d

    def test_build_direct_plan_basic_flags(self, director):
        cmd = "!art beautiful cyberpunk city --steps 28 --cfg 7.5 --seed 424242"
        plan = director.build_direct_plan(cmd)

        assert plan.steps == 28
        assert plan.cfg_scale == 7.5
        assert plan.seed == 424242
        assert "!art" not in plan.positive_prompt
        assert "beautiful cyberpunk city" in plan.positive_prompt

    def test_build_direct_plan_hires_and_resolution(self, director):
        cmd = "!арт portrait of a girl --size 768x1024 --scale 2.0 --denoise 0.45"
        plan = director.build_direct_plan(cmd)

        assert plan.width == 768
        assert plan.height == 1024
        assert plan.hr_scale == 2.0
        assert plan.denoising_strength == 0.45
        assert plan.enable_hr is True

    def test_build_direct_plan_negative_override(self, director):
        cmd = "!art anime warrior --negative blurry, extra limbs, bad hands"
        plan = director.build_direct_plan(cmd)

        assert "blurry" in plan.negative_prompt
        assert "extra limbs" in plan.negative_prompt
        assert "bad hands" in plan.negative_prompt


class TestPlanGenerationLogic:
    """Проверка логики сборки плана генерации (plan_generation)."""

    @pytest.fixture
    def director(self):
        with patch("services.art_director.director.ModelRouter"):
            d = ArtDirector()
            d.router = MagicMock()
            d.router.match_prompt.return_value = ("realistic", 0.95)
            d.router.get_checkpoint_for_model.return_value = "RealisticVision.safetensors"
            d.router.get_profile.return_value = {
                "id": "realistic",
                "name": "Realistic",
                "pipeline": {"steps": 25, "cfg_scale": 6.0}
            }
            d.router.profiles = []
            return d

    def test_plan_generation_strips_cli_flags(self, director):
        raw_prompt = "1girl, sitting on a bench --steps 20 --ar wide --neg blurry"
        plan = director.plan_generation(raw_prompt)

        # Флаги CLI и соотношения сторон должны быть удалены из позитивного промпта
        assert "--ar" not in plan.positive_prompt
        assert "--neg" not in plan.positive_prompt
        assert plan.width == 768
        assert plan.height == 512
        assert "blurry" in plan.negative_prompt

    def test_plan_generation_human_vs_scenery_adetailer(self, director):
        # Персонаж в кадре -> ADetailer включен
        human_plan = director.plan_generation("1girl, close up portrait, beautiful eyes")
        assert human_plan.enable_adetailer is True

        # Чистый пейзаж без людей -> ADetailer выключен
        scenery_plan = director.plan_generation("scenery, majestic mountains, pine forest, sunset")
        assert scenery_plan.enable_adetailer is True
        bg_plan = director.plan_generation("scenery, majestic mountains, pine forest, sunset", is_scene_background=True)
        assert bg_plan.enable_adetailer is False

    def test_plan_generation_preserves_lora_tags(self, director):
        raw_prompt = "<lora:add_detail:0.8>, 1girl, cyberpunk street"
        plan = director.plan_generation(raw_prompt)

        # LoRA не должна быть удалена санитизатором и обязана остаться в финальном промпте
        assert "<lora:add_detail:0.8>" in plan.positive_prompt


class TestArtDirectorRefactoring:
    """Тесты на новые требования: бакеты SDXL, очистка промптов, фиксация ADetailer и изоляция режимов."""

    def test_clean_context_subtag(self):
        from services.art_director.director import _clean_context_subtag
        assert _clean_context_subtag("`'\"cyberpunk room\"'`") == "cyberpunk room"
        assert _clean_context_subtag("- • * ancient ruins, , stone") == "ancient ruins, stone"
        assert _clean_context_subtag(None) == ""
        assert _clean_context_subtag("") == ""

    def test_sdxl_geometry_buckets(self):
        from services.art_director.geometry import (
            ASPECT_RATIO_BUCKETS_SD15,
            ASPECT_RATIO_BUCKETS_SDXL,
            is_xl_model,
            get_archetype_config
        )
        assert is_xl_model("animagine_xl") is True
        assert is_xl_model("pony_diffusion") is True
        assert is_xl_model("illustrious_real") is True
        assert is_xl_model("flux_dev") is True
        assert is_xl_model("realistic_vision_v51") is False

        # SD 1.5 bucket
        cfg_sd15 = get_archetype_config("realistic", "solo")
        assert cfg_sd15["width"] == 512
        assert cfg_sd15["height"] == 768
        assert cfg_sd15["is_xl"] is False

        # SDXL bucket
        cfg_xl = get_archetype_config("pony_diffusion", "solo")
        assert cfg_xl["width"] == 896
        assert cfg_xl["height"] == 1152
        assert cfg_xl["is_xl"] is True

    def test_adetailer_two_slot_contract(self):
        from services.art_director.plan import GenerationPlan
        plan = GenerationPlan(
            style="realistic",
            target_model_key="realistic",
            model_title="Realistic",
            target_checkpoint="model.safetensors",
            positive_prompt="1girl",
            negative_prompt="bad",
            width=512,
            height=768,
            steps=20,
            cfg_scale=6.0,
            sampler_name="DPM++ 2M",
            scheduler="Karras",
            enable_hr=True,
            hr_scale=1.5,
            hr_upscaler="R-ESRGAN 4x+",
            denoising_strength=0.35,
            hr_second_pass_steps=10,
            clip_skip=1,
            enable_adetailer=True,
            enable_hand_adetailer=True,
            ad_prompt="detailed face",
            ad_negative_prompt="ugly face",
            adetailer_hands_enabled=True
        )
        forge_kw = plan.to_forge_kwargs()
        alwayson = forge_kw["alwayson_scripts"]
        assert "ADetailer" in alwayson
        ad_args = alwayson["ADetailer"]["args"]
        assert ad_args[0] is True  # ad_enable
        assert ad_args[1] is False  # ad_skip_img2img
        
        # Слот 1: Лицо
        face_slot = ad_args[2]
        assert face_slot["ad_model"] == "face_yolov8n.pt"
        assert face_slot["ad_prompt"] == "detailed face"
        assert face_slot["ad_negative_prompt"] == "ugly face"

        # Слот 2: Руки
        assert len(ad_args) == 4
        hand_slot = ad_args[3]
        assert hand_slot["ad_model"] == "hand_yolov8n.pt"
        assert hand_slot["ad_denoising_strength"] == 0.22

    def test_sdxl_disables_hires_fix_to_prevent_oom(self):
        from services.art_director.director import ArtDirector
        with patch("services.art_director.director.ModelRouter"):
            d = ArtDirector()
            d.router = MagicMock()
            d.router.match_prompt.return_value = ("pony_realism", 0.95)
            d.router.get_checkpoint_for_model.return_value = "PonyRealism_XL.safetensors"
            d.router.get_profile.return_value = {
                "id": "pony_realism",
                "name": "Pony Realism XL",
                "pipeline": {"steps": 25, "cfg_scale": 6.0}
            }
            d.router.profiles = []

            plan = d.plan_generation("1girl, knight armor", preferred_style="pony_realism")
            # На моделях SDXL Hires.fix должен быть строго отключен (enable_hr=False, hr_scale=1.0)
            assert plan.enable_hr is False
            assert plan.hr_scale == 1.0
            # Разрешение должно браться из бакетов SDXL
            assert plan.width == 896
            assert plan.height == 1152

    def test_assistant_mode_isolation(self):
        from services.art_director.director import ArtDirector
        with patch("services.art_director.director.ModelRouter"), \
             patch("services.art_director.director._assemble_roleplay_context") as mock_rp_ctx:
            d = ArtDirector()
            d.router = MagicMock()
            d.router.match_prompt.return_value = ("realistic", 0.95)
            d.router.get_checkpoint_for_model.return_value = "RealisticVision.safetensors"
            d.router.get_profile.return_value = None
            d.router.profiles = []

            # В режиме Assistant функция сборки контекста РП строго НЕ вызывается
            plan = d.plan_generation("Draw a cute cat", mode="assistant")
            assert mock_rp_ctx.call_count == 0

    def test_plan_from_visual_intent_sdxl_and_mode_isolation(self):
        from services.art_director.director import ArtDirector
        from services.art_director.contracts import VisualIntent, TargetSubject
        with patch("services.art_director.director.ModelRouter"), \
             patch("services.art_director.director._assemble_roleplay_context") as mock_rp_ctx:
            d = ArtDirector()
            d.router = MagicMock()
            d.router.match_prompt.return_value = ("pony_realism", 0.95)
            d.router.get_checkpoint_for_model.return_value = "PonyRealism_XL.safetensors"
            d.router.get_profile.return_value = {
                "id": "pony_realism",
                "name": "Pony Realism XL",
                "pipeline": {"steps": 25, "cfg_scale": 6.0}
            }
            d.router.profiles = []

            # 1. SDXL from VisualIntent must disable Hires.fix
            intent_xl = VisualIntent(
                prompt="1girl, fantasy forest",
                suggested_model="pony_realism",
                mode="assistant"
            )
            plan_xl = d.plan_from_visual_intent(intent_xl)
            assert plan_xl.enable_hr is False
            assert plan_xl.hr_scale == 1.0
            assert plan_xl.width == 896
            assert plan_xl.height == 1152
            # Assistant mode: no RP context assembly
            assert mock_rp_ctx.call_count == 0

            # 2. Roleplay mode: calls _assemble_roleplay_context
            mock_rp_ctx.return_value = {
                "prompt": "1girl, in dungeon",
                "focus_name": "Seraphina",
                "secondary_name": "",
                "has_secondary": False,
                "loc_dna": "dungeon",
                "cast_data": {}
            }
            intent_rp = VisualIntent(
                prompt="looking around",
                suggested_model="realistic",
                mode="roleplay"
            )
            plan_rp = d.plan_from_visual_intent(intent_rp)
            assert mock_rp_ctx.call_count == 1

    def test_is_xl_model_comprehensive(self):
        from services.art_director.geometry import is_xl_model

        # Valid SDXL / Pony / Flux / Illustrious / SD3
        assert is_xl_model("animagine_xl") is True
        assert is_xl_model("AnimagineXL_v3.safetensors") is True
        assert is_xl_model("NovelXL.safetensors") is True
        assert is_xl_model("pony_diffusion_v6") is True
        assert is_xl_model("illustrious_real") is True
        assert is_xl_model("flux_dev.safetensors") is True
        assert is_xl_model("sd3_medium.safetensors") is True
        assert is_xl_model("stable-diffusion-3.5-large") is True
        assert is_xl_model("juggernaut_xl_v9.safetensors") is True

        # False positives prevention: pixel, complex, duplex, reflex, flexible, relax
        assert is_xl_model("pixel_art_v1.safetensors") is False
        assert is_xl_model("complex_details.safetensors") is False
        assert is_xl_model("duplex_rendering.safetensors") is False
        assert is_xl_model("reflex_photo.safetensors") is False
        assert is_xl_model("flexible_diffusion.safetensors") is False
        assert is_xl_model("relax_atmosphere.safetensors") is False
        assert is_xl_model("realistic_vision_v51.safetensors") is False

        # Dictionary input
        assert is_xl_model({"architecture": "sdxl"}) is True
        assert is_xl_model({"architecture": "pony"}) is True
        assert is_xl_model({"architecture": "flux"}) is True
        assert is_xl_model({"architecture": "illustrious"}) is True
        assert is_xl_model({"architecture": "sd3"}) is True
        assert is_xl_model({"checkpoint": "animagine_xl.safetensors", "name": "Anime", "id": "anime"}) is True
        assert is_xl_model({"checkpoint": "pixel_art.safetensors", "name": "Pixel Art", "id": "pixel"}) is False

        # Sequence input
        assert is_xl_model(["animagine_xl.safetensors", "anime"]) is True
        assert is_xl_model(["pixel_art.safetensors", "pixel"]) is False

        # Empty / None
        assert is_xl_model(None) is False
        assert is_xl_model("") is False

    def test_composite_model_identity_and_ar_handling(self):
        from services.art_director.director import ArtDirector
        with patch("services.art_director.director.ModelRouter"):
            d = ArtDirector()
            d.router = MagicMock()
            # Profile id is generic 'anime', but checkpoint has 'AnimagineXL'
            d.router.match_prompt.return_value = ("anime", 0.95)
            d.router.get_checkpoint_for_model.return_value = "AnimagineXL_v3.safetensors"
            d.router.get_profile.return_value = {
                "id": "anime",
                "name": "Anime Style",
                "pipeline": {"steps": 25, "cfg_scale": 6.0, "hr_scale": 1.5}
            }
            d.router.profiles = []

            # 1. SDXL detected via target_ckpt filename in plan_generation
            plan = d.plan_generation("1girl, futuristic city --ar 16:9")
            # Bucket should be SDXL cinematic: 1344x768 (not SD15 768x448)
            assert plan.width == 1344
            assert plan.height == 768
            # VRAM fail-safe: hires.fix disabled for XL
            assert plan.enable_hr is False
            assert plan.hr_scale == 1.0

            # 2. Non-XL checkpoint with --ar 16:9
            d.router.get_checkpoint_for_model.return_value = "pixel_model.safetensors"
            d.router.get_profile.return_value = {
                "id": "pixel",
                "name": "Pixel Art",
                "pipeline": {"steps": 20, "cfg_scale": 7.0, "hr_scale": 1.5}
            }
            plan_sd15 = d.plan_generation("landscape --ar 16:9", preferred_style="pixel")
            # Bucket should be SD15 cinematic: 768x448
            assert plan_sd15.width == 768
            assert plan_sd15.height == 448
            # Canvas < 640000 px and not XL -> hires.fix enabled
            assert plan_sd15.enable_hr is True
            assert plan_sd15.hr_scale == 1.5
