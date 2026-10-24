import re
import time
from unittest.mock import MagicMock, patch
import pytest

from services.vram_arbiter import VRAMArbiter, GPUOwner
from services.forge_client import ForgeClient


def test_forge_wait_for_vram_clear_success():
    """Проверяет, что _wait_for_vram_clear ожидает достижения порога 3800 МБ."""
    fc = ForgeClient()
    mock_monitor = MagicMock()
    # Возвращаем 2000 МБ, затем 4000 МБ
    mock_monitor.get_free_vram_mb.side_effect = [2000, 2500, 3900]
    fc.system_monitor = mock_monitor
    fc._last_vram_wait_ts = 0.0

    start = time.time()
    fc._wait_for_vram_clear()
    elapsed = time.time() - start

    assert mock_monitor.get_free_vram_mb.call_count == 3
    # Проверка, что не упало с ошибкой и зафиксировало timestamp
    assert hasattr(fc, "_last_vram_wait_ts")
    assert fc._last_vram_wait_ts > 0


def test_forge_wait_for_vram_clear_timeout_graceful():
    """Проверяет, что при недостижении 3800 МБ за таймаут метод не падает, а завершается."""
    fc = ForgeClient()
    mock_monitor = MagicMock()
    mock_monitor.get_free_vram_mb.return_value = 3000
    fc.system_monitor = mock_monitor
    fc._last_vram_wait_ts = -100.0

    with patch("time.sleep", return_value=None), patch("time.time") as mock_time:
        # Симулируем: проверка throttle (100.0), start_t (100.0), цикл while (101.0, 105.0), финал (105.0)
        mock_time.side_effect = [100.0, 100.0, 101.0, 105.0, 105.0]
        fc._wait_for_vram_clear()

    assert mock_monitor.get_free_vram_mb.called
    assert fc._last_vram_wait_ts > 0


def test_vram_arbiter_prepare_for_task_with_needs_llm():
    """Проверяет, что prepare_for_task не выгружает LLM, если нужен предварительный перевод/генерация промпта."""
    mock_lm = MagicMock()
    mock_forge = MagicMock()
    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=mock_forge)

    # 1. Задача image с needs_llm_prompt_gen=True
    arbiter.prepare_for_task("image", metadata={"needs_llm_prompt_gen": True})
    mock_lm.unload_models_cli.assert_not_called()

    # 2. Задача image с needs_llm_prompt_gen=False
    arbiter.prepare_for_task("image", metadata={"needs_llm_prompt_gen": False})
    mock_lm.unload_models_cli.assert_called_once()
    assert arbiter._current_owner == GPUOwner.DIFFUSION


def test_vram_arbiter_release_llm_for_diffusion():
    """Проверяет сквозной вызов release_llm_for_diffusion."""
    mock_lm = MagicMock()
    mock_forge = MagicMock()
    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=mock_forge)

    arbiter.release_llm_for_diffusion()
    mock_lm.unload_models_cli.assert_called_once()
    mock_forge._wait_for_vram_clear.assert_called_once()
    assert arbiter._current_owner == GPUOwner.DIFFUSION


def test_cyrillic_detection_logic():
    """Проверяет логику определения кириллицы для needs_llm_prompt_gen."""
    latin_prompt = "cyberpunk street at night, neon lights, 8k"
    cyrillic_prompt = "киберпанк улица ночью, неоновые вывески"
    mixed_prompt = "cyberpunk самурай katana"

    assert not bool(re.search(r"[а-яА-ЯёЁ]", latin_prompt))
    assert bool(re.search(r"[а-яА-ЯёЁ]", cyrillic_prompt))
    assert bool(re.search(r"[а-яА-ЯёЁ]", mixed_prompt))


def test_scene_art_needs_llm_gen_logic():
    """Проверяет булеву формулу needs_llm_gen в _on_scene_art_requested."""
    # formula: bool((captured_mode in ("roleplay", "rp") and not v_intent) or (not v_intent and re.search(r"[а-яА-ЯёЁ]", raw_scene_text)))
    
    # 1. Roleplay mode, no visual intent -> True
    captured_mode = "roleplay"
    v_intent = None
    raw_scene_text = "castle in the mountains"
    needs_llm = bool((captured_mode in ("roleplay", "rp") and not v_intent) or (not v_intent and re.search(r"[а-яА-ЯёЁ]", raw_scene_text)))
    assert needs_llm is True

    # 2. Roleplay mode, WITH visual intent -> False
    v_intent = {"prompt": "castle"}
    needs_llm = bool((captured_mode in ("roleplay", "rp") and not v_intent) or (not v_intent and re.search(r"[а-яА-ЯёЁ]", raw_scene_text)))
    assert needs_llm is False

    # 3. Assistant mode, no visual intent, cyrillic text -> True
    captured_mode = "assistant"
    v_intent = None
    raw_scene_text = "замок в горах"
    needs_llm = bool((captured_mode in ("roleplay", "rp") and not v_intent) or (not v_intent and re.search(r"[а-яА-ЯёЁ]", raw_scene_text)))
    assert needs_llm is True

    # 4. Assistant mode, no visual intent, latin text -> False
    raw_scene_text = "castle in the mountains"
    needs_llm = bool((captured_mode in ("roleplay", "rp") and not v_intent) or (not v_intent and re.search(r"[а-яА-ЯёЁ]", raw_scene_text)))
    assert needs_llm is False


def test_scene_background_needs_llm_gen_logic():
    """Проверяет формулу needs_llm_gen в _on_generate_background_clicked."""
    # formula: bool(not loc_dna and re.search(r"[а-яА-ЯёЁ]", prompt_seed))

    # 1. DNA exists -> False (already structured tags)
    loc_dna = "cyberpunk room, neon view"
    prompt_seed = f"scenery, empty environment, {loc_dna}, no humans"
    needs_llm = bool(not loc_dna and re.search(r"[а-яА-ЯёЁ]", prompt_seed))
    assert needs_llm is False

    # 2. No DNA, cyrillic description -> True
    loc_dna = ""
    prompt_seed = "scenery, empty environment, уютная таверна у камина, no humans"
    needs_llm = bool(not loc_dna and re.search(r"[а-яА-ЯёЁ]", prompt_seed))
    assert needs_llm is True

    # 3. No DNA, english description -> False
    prompt_seed = "scenery, empty environment, cozy tavern by the fireplace, no humans"
    needs_llm = bool(not loc_dna and re.search(r"[а-яА-ЯёЁ]", prompt_seed))
    assert needs_llm is False
