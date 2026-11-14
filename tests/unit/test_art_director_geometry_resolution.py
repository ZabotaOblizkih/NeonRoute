"""
Unit tests for Art Director geometry resolution and aspect ratio flag mapping.
Ensures deterministic geometry selection, removal of has_human heuristic for explicit --ar,
and preservation of detected archetypes when no explicit --ar is given.
"""
import pytest

from services.art_director.geometry import (
    ASPECT_RATIO_BUCKETS_SD15,
    ASPECT_RATIO_BUCKETS_SDXL,
    detect_scene_archetype,
    get_archetype_config,
)
from services.art_director.director import ArtDirector
from services.art_director.contracts import VisualIntent, TargetSubject

SDXL_CHECKPOINT = "juggernaut_xl_v9.safetensors"
SD15_CHECKPOINT = "realistic"


def test_adetailer_force_ad_all_true():
    """Проверяет, что force_ad равен True для всех архетипов в SD 1.5 и SDXL."""
    for arch, cfg in ASPECT_RATIO_BUCKETS_SD15.items():
        assert cfg["force_ad"] is True, f"SD15 {arch} force_ad must be True"
        cfg_resolved = get_archetype_config(SD15_CHECKPOINT, arch)
        assert cfg_resolved["force_adetailer"] is True

    for arch, cfg in ASPECT_RATIO_BUCKETS_SDXL.items():
        assert cfg["force_ad"] is True, f"SDXL {arch} force_ad must be True"
        cfg_resolved = get_archetype_config(SDXL_CHECKPOINT, arch)
        assert cfg_resolved["force_adetailer"] is True


def test_detect_scene_archetype_cleaned_fullbody_markers():
    """Проверяет, что бытовые слова (standing, boots, dress) не триггерят fullbody."""
    # Бытовые слова больше не должны делать архетип fullbody
    assert detect_scene_archetype("warrior standing in a dress wearing boots") == "solo"
    assert detect_scene_archetype("girl in a beautiful red dress") == "solo"
    assert detect_scene_archetype("soldier standing at attention") == "solo"

    # Явные маркеры полного роста должны делать fullbody
    assert detect_scene_archetype("warrior in full body armor") == "fullbody"
    assert detect_scene_archetype("full-body portrait of a wizard") == "fullbody"
    assert detect_scene_archetype("head to toe shot of an adventurer") == "fullbody"
    assert detect_scene_archetype("full length photograph of a model") == "fullbody"


def test_direct_ar_flag_mapping_sdxl():
    """Проверяет прямой маппинг явных флагов --ar в SDXL без эвристики has_human."""
    director = ArtDirector()

    # Проверяем, что samurai/cyborg/knight при --ar wide строго дают pair (1152x896), а не scenery (1216x832)
    plan_wide_samurai = director.plan_generation(
        raw_prompt="cybernetic samurai with katana in neon street --ar wide",
        preferred_style=SDXL_CHECKPOINT
    )
    assert plan_wide_samurai.archetype == "pair"
    assert plan_wide_samurai.width == 1152
    assert plan_wide_samurai.height == 896

    # --ar portrait -> solo (896x1152)
    plan_portrait = director.plan_generation(
        raw_prompt="cyborg warrior --ar portrait",
        preferred_style=SDXL_CHECKPOINT
    )
    assert plan_portrait.archetype == "solo"
    assert plan_portrait.width == 896
    assert plan_portrait.height == 1152

    # --ar square / closeup -> closeup (1024x1024)
    plan_square = director.plan_generation(
        raw_prompt="robot face --ar square",
        preferred_style=SDXL_CHECKPOINT
    )
    assert plan_square.archetype == "closeup"
    assert plan_square.width == 1024
    assert plan_square.height == 1024

    # --ar fullbody / 9:16 -> fullbody (832x1216)
    plan_fullbody = director.plan_generation(
        raw_prompt="mage casting spell --ar fullbody",
        preferred_style=SDXL_CHECKPOINT
    )
    assert plan_fullbody.archetype == "fullbody"
    assert plan_fullbody.width == 832
    assert plan_fullbody.height == 1216

    # --ar cinematic / 16:9 -> cinematic (1344x768)
    plan_cinematic = director.plan_generation(
        raw_prompt="battlefield with armies --ar cinematic",
        preferred_style=SDXL_CHECKPOINT
    )
    assert plan_cinematic.archetype == "cinematic"
    assert plan_cinematic.width == 1344
    assert plan_cinematic.height == 768

    # --ar scenery -> scenery (1216x832)
    plan_scenery = director.plan_generation(
        raw_prompt="floating island in the clouds --ar scenery",
        preferred_style=SDXL_CHECKPOINT
    )
    assert plan_scenery.archetype == "scenery"
    assert plan_scenery.width == 1216
    assert plan_scenery.height == 832


def test_direct_ar_flag_mapping_sd15():
    """Проверяет прямой маппинг явных флагов --ar в SD 1.5."""
    director = ArtDirector()

    # --ar wide -> pair (768x512)
    plan_wide = director.plan_generation(
        raw_prompt="knight fighting monster --ar wide",
        preferred_style=SD15_CHECKPOINT
    )
    assert plan_wide.archetype == "pair"
    assert plan_wide.width == 768
    assert plan_wide.height == 512

    # --ar portrait -> solo (512x768)
    plan_portrait = director.plan_generation(
        raw_prompt="knight standing --ar portrait",
        preferred_style=SD15_CHECKPOINT
    )
    assert plan_portrait.archetype == "solo"
    assert plan_portrait.width == 512
    assert plan_portrait.height == 768

    # --ar square -> closeup (512x512)
    plan_square = director.plan_generation(
        raw_prompt="knight helm --ar square",
        preferred_style=SD15_CHECKPOINT
    )
    assert plan_square.archetype == "closeup"
    assert plan_square.width == 512
    assert plan_square.height == 512


def test_visual_intent_preserves_detected_archetype_when_no_ar():
    """Проверяет, что при отсутствии явного --ar результат detect_scene_archetype не затирается."""
    director = ArtDirector()

    # 1. Closeup
    intent_closeup = VisualIntent(
        prompt="headshot close-up of a dark elf with glowing eyes",
        target_subject=TargetSubject.SCENE_OBJECT
    )
    plan_cu = director.plan_from_visual_intent(intent_closeup, preferred_style=SD15_CHECKPOINT)
    assert plan_cu.archetype == "closeup"
    assert plan_cu.width == 512
    assert plan_cu.height == 512

    # 2. Scenery
    intent_scenery = VisualIntent(
        prompt="ancient castle ruins, forest mountains, fog",
        target_subject=TargetSubject.SCENE_OBJECT
    )
    plan_sc = director.plan_from_visual_intent(intent_scenery, preferred_style=SDXL_CHECKPOINT)
    assert plan_sc.archetype == "scenery"
    assert plan_sc.width == 1216
    assert plan_sc.height == 832

    # 3. Fullbody
    intent_fb = VisualIntent(
        prompt="warrior in ornate armor, head to toe, full body shot",
        target_subject=TargetSubject.SCENE_OBJECT
    )
    plan_fb = director.plan_from_visual_intent(intent_fb, preferred_style=SDXL_CHECKPOINT)
    assert plan_fb.archetype == "fullbody"
    assert plan_fb.width == 832
    assert plan_fb.height == 1216

    # 4. Cinematic
    intent_cine = VisualIntent(
        prompt="battlefield with armies, panoramic horizon vista",
        target_subject=TargetSubject.SCENE_OBJECT
    )
    plan_cine = director.plan_from_visual_intent(intent_cine, preferred_style=SDXL_CHECKPOINT)
    assert plan_cine.archetype == "cinematic"
    assert plan_cine.width == 1344
    assert plan_cine.height == 768

    # 5. Default solo
    intent_solo = VisualIntent(
        prompt="mysterious wanderer walking through town",
        target_subject=TargetSubject.SCENE_OBJECT
    )
    plan_solo = director.plan_from_visual_intent(intent_solo, preferred_style=SDXL_CHECKPOINT)
    assert plan_solo.archetype == "solo"
    assert plan_solo.width == 896
    assert plan_solo.height == 1152
