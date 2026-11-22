"""
Consolidated End-to-End Smoke Test Suite for NeonRoute Roleplay Chat.
Covers 7 comprehensive scenarios with structured diagnostic failure reporting
(Scenario -> Input -> Expected -> Actual) completely offline.

Scenarios:
1. Base RP turn, macros ({{user}}, {{char}}, {{roll}}), depth anchors, thought streaming and sanitization.
2. Background RPCastExtractor on thinking models (suppression, prefill guard, slot isolation) and scene updates.
3. Scene art generation click pipeline (SD 1.5, SDXL Danbooru, SDXL phrases, Auto, subject fallback, tag typos).
4. Fail-Fast Guard before VRAMArbiter (thought-only response & offline Forge resilience).
5. VRAM arbitrage, sequential switching, and KV-slot preservation (evicted_slot_0.bin).
6. Card archetypes (system, dating, cyoa, detective, group), Depth anchors, and RPG markup preservation.
7. Session isolation during generation switching, SQLite/JSON roundtrip persistence, and leak detection.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch
import pytest

import config
try:
    assert_diag
except NameError:
    import sys
    _smoke_dir = str(Path(__file__).resolve().parent)
    if _smoke_dir not in sys.path:
        sys.path.insert(0, _smoke_dir)
    from conftest import assert_diag, assert_diag_eq, assert_diag_in

from services.tavern_models import CharacterCard
from services.tavern_parser import TavernParser
from services.session_manager import SessionManager
from services.roleplay_prompt_builder import (
    RoleplayPromptBuilder,
    SD15_FORMAT_RULES,
    SDXL_FORMAT_RULES,
)
from services.assistant_prompt_builder import (
    AssistantPromptBuilder,
    resolve_prompt_dialect,
    resolve_model_architecture,
)
from services.rp_cast_manager import RPCastManager
from services.rp_cast_extractor import RPCastExtractor
from services.rp_scene_updater import apply_scene_updates_to_cast
from services.roleplay_state_manager import RoleplayStateManager
from services.roleplay_flow_controller import RoleplayFlowControllerMixin
from ui.controllers.hardware_engine import HardwareEngineMixin
from services.thought_styles import ThoughtStyleConfig, resolve_thought_style
from services.thought_stream_parser import ThoughtStreamParser
from services.regex_filter import TextRegexFilter
from services.macro_engine import MacroEngine
from services.vram_arbiter import VRAMArbiter, GPUOwner
from services.art_director.director import ArtDirector
from services.model_router import ModelRouter, normalize_model_choice
from services.assistant_flow import resolve_effective_thinking
from services.process_manager import ProcessManager, KV_SLOTS_DIR
from ui.components.rpg_elements import RPGContentParser


class DummyRPWindow(RoleplayFlowControllerMixin, HardwareEngineMixin):
    """Headless window для тестирования связки RoleplayFlowControllerMixin и HardwareEngineMixin."""

    def __init__(self, art_director, forge_client, lm_client, session_id="test_rp_session"):
        self.art_director = art_director
        self.forge_client = forge_client
        self.lm_client = lm_client
        self.current_mode = "roleplay"
        self.selected_forge_checkpoint = None
        self.current_session = {"id": session_id}
        self.active_character = CharacterCard(
            name="Серафина",
            gender="female",
            visual_dna="amber eyes, long blonde hair"
        )
        self.available_forge_checkpoints = []
        self._active_tasks = {}

    def get_effective_mode(self):
        return "roleplay"

    def _cancel_lms_reload(self):
        pass

    def _reload_active_lms_model_async(self, delay=1.2):
        pass

    def _is_forge_available(self):
        return True


class DummyRPController(RoleplayFlowControllerMixin):
    """Тестовый контроллер для проверки перехвата потоковых мыслей и сохранения истории."""

    def __init__(self, character_card: CharacterCard):
        self.active_character = character_card
        self.current_session = {"id": "test_rp_session_123"}
        self._turn_bubble = MagicMock()
        self._sync_turn_called_with = None
        self._thought_stream_parser = None

    def _sync_turn(self, role, content, replace_last=True, mode="roleplay", thought=None):
        self._sync_turn_called_with = {
            "role": role,
            "content": content,
            "replace_last": replace_last,
            "mode": mode,
            "thought": thought
        }

    def _append_message(self, *args, **kwargs):
        return self._turn_bubble


# =========================================================================
# СЦЕНАРИЙ 1: БАЗОВЫЙ ХОД, МАКРОСЫ, ЯКОРЯ DEPTH И СТРИМИНГ МЫСЛЕЙ
# =========================================================================

def test_scenario_1_base_turn_macros_sanitization_thinking(smoke_isolation_guard):
    """
    Сценарий 1:
    - Замена макросов {{user}}, {{char}} и бросков {{roll:XdY+Z}} в репликах и сценарии.
    - Сборка сообщений через SessionManager.get_lm_studio_messages.
    - Инъекция якоря Depth-1 (предпоследнее системное сообщение).
    - Перехват и отделение блока <think>...</think> в RoleplayFlowControllerMixin.
    - Резолвер resolve_effective_thinking для thinking-моделей.
    """
    scen = "Сценарий 1: Базовый RP-ход, макросы, Depth-якорь и мысли"

    user_name = "Дэниэл"
    char_name = "Кай"
    first_mes_raw = "*{{char}} смотрит на {{user}} и делает бросок: {{roll:1d1+10}}.* Готов идти?"

    # 1. Проверка макросов MacroEngine
    processed_first_mes = MacroEngine.process(first_mes_raw, user_name=user_name, char_name=char_name)
    assert_diag("Кай смотрит на Дэниэл" in processed_first_mes, scen, first_mes_raw, "Имена заменены", processed_first_mes)
    assert_diag("11" in processed_first_mes, scen, first_mes_raw, "Бросок кубика вычислен", processed_first_mes)

    card = CharacterCard(
        name=char_name,
        description="Опытный проводник",
        personality="Осторожный",
        scenario="{{user}} и {{char}} исследуют древний замок",
        first_mes=processed_first_mes,
        mes_example="<START>\n{{user}}: Кто здесь?\n{{char}}: Тихо.",
        visual_dna="1boy, short dark hair, leather armor, dagger",
        is_dating=True,
    )

    recalled_block = "<RecalledLore>\n- Дэниэл нашел карту замка\n</RecalledLore>"
    history = [
        {"role": "assistant", "content": card.first_mes},
        {"role": "user", "content": "Осматриваю коридор."},
    ]

    # 2. Сборка системного окна и Depth-якоря
    messages = SessionManager.get_lm_studio_messages(
        history=history,
        is_roleplay=True,
        character_card=card,
        user_name=user_name,
        session_id="test_sess_01",
    )
    messages[0]["content"] += f"\n\n{recalled_block}"

    depth_anchor = RoleplayPromptBuilder.build_depth_anchor(card, user_name=user_name, user_query="Осматриваю коридор")
    for idx in reversed(range(len(messages))):
        if messages[idx].get("role") == "user":
            messages.insert(idx, {"role": "system", "content": depth_anchor.strip()})
            break

    sys_prompt = messages[0]["content"]
    assert_diag("<RecalledLore>" in sys_prompt, scen, "System prompt assembly", "Contains <RecalledLore>", sys_prompt)
    assert_diag("Дэниэл нашел карту замка" in sys_prompt, scen, "System prompt assembly", "Contains recalled memory", sys_prompt)

    # Позиция Depth-якоря: строго предпоследнее сообщение
    assert_diag(messages[-2]["role"] == "system", scen, "Anchor position", "role == 'system'", messages[-2]["role"])
    assert_diag(messages[-2]["content"] == depth_anchor.strip(), scen, "Anchor content", depth_anchor.strip(), messages[-2]["content"])
    assert_diag(messages[-1]["role"] == "user", scen, "User prompt position", "role == 'user'", messages[-1]["role"])

    # 3. Перехват мыслей через ThoughtStreamParser и RoleplayFlowControllerMixin
    controller = DummyRPController(card)
    controller._thought_stream_parser = ThoughtStreamParser()

    stream_chunks = [
        "<think>\n",
        "Коридор подозрительно пуст. Нужно предупредить Дэниэла.\n",
        "</think>\n",
        "*Кай прикладывает палец к губам и тихо шепчет:*\n— Стой, впереди стража."
    ]
    for ch in stream_chunks:
        controller._thought_stream_parser.feed(ch)

    controller._handle_roleplay_llm_response("".join(stream_chunks))
    saved_turn = controller._sync_turn_called_with

    assert_diag(saved_turn is not None, scen, "Turn synchronization", "Turn dictionary present", saved_turn)
    clean_text = saved_turn["content"]
    assert_diag("<think>" not in clean_text and "</think>" not in clean_text, scen, "Thought stripping", "No <think> tags", clean_text)
    assert_diag("Коридор подозрительно пуст" not in clean_text, scen, "Thought stripping", "Inner thought removed", clean_text)
    assert_diag("Стой, впереди стража" in clean_text, scen, "Content preservation", "Contains spoken reply", clean_text)

    thought_obj = saved_turn["thought"]
    assert_diag(thought_obj is not None, scen, "Thought storage", "thought dict present", thought_obj)
    assert_diag("Коридор подозрительно пуст" in thought_obj["content"], scen, "Thought storage", "Matches raw thoughts", thought_obj["content"])
    assert_diag(thought_obj["token_count"] > 0, scen, "Thought tokens", "token_count > 0", thought_obj["token_count"])

    # 4. Резолвер resolve_effective_thinking
    eff_gallery = resolve_effective_thinking("roleplay", "qwen3.5-35b", active_skill="gallery", explicit_override=False)
    assert_diag(eff_gallery is False, scen, "Thinking suppression in gallery skill", False, eff_gallery)

    eff_override_off = resolve_effective_thinking("roleplay", "qwen3.5-35b", active_skill="none", explicit_override=False)
    assert_diag(eff_override_off is False, scen, "Explicit False override", False, eff_override_off)

    eff_override_on = resolve_effective_thinking("roleplay", "qwen3.5-35b", active_skill="none", explicit_override=True)
    assert_diag(eff_override_on is True, scen, "Explicit True override", True, eff_override_on)


# =========================================================================
# СЦЕНАРИЙ 2: ФОНОВЫЙ RP_CAST_EXTRACTOR И ОБНОВЛЕНИЕ СЦЕНЫ
# =========================================================================

def test_scenario_2_background_rp_cast_extractor_thinking_suppression(smoke_isolation_guard):
    """
    Сценарий 2:
    - RPCastExtractor._run_extraction на thinking-модели (Qwen3.5-35B-Thinking).
    - Передача параметров: enable_thinking=False, id_slot=None, cache_prompt=False.
    - Grammar Prefill Guard: отсутствие assistant prefill при response_format={'type': 'json_object'}.
    - Очистка блока <think> перед JSON и парсинг visual_prompt, scene_type.
    - Транзакционное обновление сцены apply_scene_updates_to_cast (сохранение реликвий, локации и NPC).
    """
    scen = "Сценарий 2: RPCastExtractor на думающей модели и транзакция сцены"
    sess_id = "sess_extractor_test"
    sess_file = smoke_isolation_guard["history_dir"] / f"chat_{sess_id}.json"
    sess_file.write_text("{}", encoding="utf-8")

    mock_client = MagicMock()
    mock_client._current_loaded_ctx = 16384
    mock_client._last_loaded_model_id = "Qwen3.5-35B-Thinking"

    valid_json_payload = {
        "scene_type": "exploration",
        "current_location": "древние руины",
        "visual_prompt": "1girl, solo, silver hair, hooded cloak, ancient mossy ruins, misty dawn",
        "environment": {"location_dna": "ancient ruins, mossy stones", "scene_ambience": "misty dawn"}
    }
    raw_response = f"<think>\nАнализирую последние реплики...\nЛокация изменилась на руины.\n</think>\n{json.dumps(valid_json_payload)}"
    mock_client.chat_completion.return_value = raw_response

    history = [
        {"role": "user", "content": "Мы подходим к вратам древних руин."},
        {"role": "assistant", "content": "*Оглядывает полуразрушенные колонны сквозь туман.*"}
    ]

    extracted = RPCastExtractor._run_extraction(
        recent_history=history,
        session_id=sess_id,
        lm_client=mock_client
    )

    assert_diag(mock_client.chat_completion.called, scen, "LLM Chat Completion invocation", "Called once", mock_client.chat_completion.called)
    call_kwargs = mock_client.chat_completion.call_args[1]

    # Проверка служебных флагов
    assert_diag(call_kwargs.get("enable_thinking") is False, scen, "enable_thinking suppression", False, call_kwargs.get("enable_thinking"))
    assert_diag(call_kwargs.get("id_slot") is None, scen, "id_slot isolation (protect slot 0)", None, call_kwargs.get("id_slot"))
    assert_diag(call_kwargs.get("cache_prompt") is False, scen, "cache_prompt isolation", False, call_kwargs.get("cache_prompt"))

    # Проверка отсутствия assistant prefill при response_format
    req_messages = call_kwargs.get("messages", [])
    has_prefill_assistant = any(m.get("role") == "assistant" and "<think>" in m.get("content", "") for m in req_messages)
    assert_diag(not has_prefill_assistant, scen, "Grammar prefill guard", "No <think> prefill when response_format active", has_prefill_assistant)

    # Проверка очистки и обновления
    assert_diag(extracted is not None, scen, "Extraction result", "Dict returned", extracted)
    assert_diag(extracted.get("visual_prompt") == "1girl, solo, silver hair, hooded cloak, ancient mossy ruins, misty dawn", scen, "Extracted visual_prompt", "contains ruins", extracted.get("visual_prompt"))
    assert_diag(extracted.get("visual_intent", {}).get("scene_type") == "exploration", scen, "Extracted scene_type", "exploration", extracted.get("visual_intent", {}).get("scene_type"))

    # Проверка транзакционного обновления сцены (apply_scene_updates_to_cast)
    initial_cast = RoleplayStateManager.load_cast(sess_id, user_name="Игрок", companion_name="Лира")
    initial_cast["inventory"] = ["Старый плащ", "Квестовый Амулет Вечности"]
    RoleplayStateManager.save_cast(sess_id, initial_cast)

    scene_delta = {
        "new_location": "Темный лес",
        "items_added": ["Факел"],
        "npcs_present": [{"name": "Лесник Бран", "role": "npc", "visual_dna": "tall bearded ranger"}],
    }
    apply_scene_updates_to_cast(sess_id, scene_delta, user_name="Игрок", companion_name="Лира")
    updated_cast = RoleplayStateManager.load_cast(sess_id, user_name="Игрок", companion_name="Лира")
    assert_diag(updated_cast.get("current_location") == "Темный лес", scen, scene_delta, "Темный лес", updated_cast.get("current_location"))
    assert_diag("Квестовый Амулет Вечности" in updated_cast.get("inventory", []), scen, "Relic preservation", "Contains amulet", updated_cast.get("inventory"))


# =========================================================================
# СЦЕНАРИЙ 3: ГЕНЕРАЦИЯ АРТА СЦЕНЫ ПО КЛИКУ И ДИСПЕТЧЕРИЗАЦИЯ ДИАЛЕКТОВ
# =========================================================================

def test_scenario_3_scene_art_generation_multi_architecture_and_dialects(isolated_art_models_env, monkeypatch):
    """
    Сценарий 3:
    - Генерация по клику 'Арт сцены' (_scene_art_pipeline + ArtDirector).
    - SD 1.5 Auto Mode: Danbooru теги, пассивное оружие не добавляется в Equipment, разрешение 512x768.
    - SDXL Danbooru-модели: выбор тегового шаблона на основе routing_description.
    - SDXL фразовые модели: генерация описательных предложений без booru-тегов.
    - Авто-режим: очистка технической заглушки target_model_id='realistic' в None.
    - Фоллбэк Subject на имя персонажа при пустом visual_dna.
    - Обработка опечаток [IMAGE_PROP: ...] через TextRegexFilter.IMAGE_TAG_LABEL_PATTERN.
    """
    scen = "Сценарий 3: Генерация арта сцены по клику"
    art_director = isolated_art_models_env

    mock_lm = MagicMock()
    mock_lm.chat_completion.return_value = (
        "[IMAGE_PROP: 1girl, solo, amber eyes, long blonde hair, traveler tunic, "
        "sitting at wooden table, rustic tavern, soft lighting --ar portrait --model model_sd15_classic]"
    )

    mock_forge = MagicMock()
    mock_forge.get_models.return_value = [
        {"title": "cyberrealistic_v50.safetensors", "model_name": "cyberrealistic_v50"},
        {"title": "counterfeitxl_v10.safetensors", "model_name": "counterfeitxl_v10"}
    ]
    mock_forge.text_to_image.return_value = "C:/tmp/images/output_sd15.png"
    mock_forge.is_server_ready.return_value = True
    monkeypatch.setattr("services.forge_client.ForgeClient.get_instance", lambda: mock_forge)

    window = DummyRPWindow(art_director=art_director, forge_client=mock_forge, lm_client=mock_lm)
    window.available_forge_checkpoints = ["cyberrealistic_v50.safetensors", "counterfeitxl_v10.safetensors"]

    cast_dialogue = {
        "scene_type": "dialogue",
        "environment": {"location_dna": "уютная таверна", "scene_ambience": "теплый камин"},
        "companion_state": {"visual_dna": "amber eyes, long blonde hair", "current_outfit": "traveler tunic"},
        "inventory": ["кинжал в ножнах", "кожаный мешок"]
    }
    monkeypatch.setattr("services.rp_cast_manager.RPCastManager.load_cast", lambda *args, **kwargs: cast_dialogue)

    scene_speech = "Серафина отпила глоток чая: — Здесь тепло и спокойно. <stats>Усталость: 0</stats>"
    res_sd15 = window._scene_art_pipeline(scene_text=scene_speech, ckpt="realistic")

    assert_diag(mock_lm.chat_completion.called, scen, "LLM Chat call (SD15)", "Called", mock_lm.chat_completion.called)
    call_kwargs_15 = mock_lm.chat_completion.call_args.kwargs
    user_payload_15 = call_kwargs_15["messages"][1]["content"]
    sys_payload_15 = call_kwargs_15["messages"][0]["content"]

    assert_diag("CRITICAL FORMAT RULES (SD 1.5" in sys_payload_15, scen, "SD 1.5 Directive", "SD 1.5 format rules", sys_payload_15)
    assert_diag("Generate concise, comma-separated Danbooru tags" in user_payload_15, scen, "SD 1.5 Task Header", "Danbooru tags header", user_payload_15)
    assert_diag("- Equipment:" not in user_payload_15, scen, "Passive gear guard in dialogue", "No active equipment", user_payload_15)
    assert_diag("<stats>" not in user_payload_15, scen, "Stats sanitization", "No <stats> tag", user_payload_15)

    plan_15 = res_sd15.get("plan")
    assert_diag(plan_15 is not None, scen, "SD 1.5 Plan Generation", "Plan exists", plan_15)
    assert_diag(plan_15.width == 512 and plan_15.height == 768, scen, "SD 1.5 Resolution", "512x768", f"{plan_15.width}x{plan_15.height}")

    # Очистка опечатки [IMAGE_PROP: ...]
    forge_call = mock_forge.text_to_image.call_args.kwargs
    assert_diag("1girl, solo" in forge_call["prompt"], scen, "Typo IMAGE_PROP cleaned", "Clean tags in Forge prompt", forge_call["prompt"])
    assert_diag("IMAGE_PROP" not in forge_call["prompt"], scen, "Typo header removed", "No IMAGE_PROP in prompt", forge_call["prompt"])

    # 2. Проверка диалекта для Illustrious (SDXL arch + Danbooru description -> tags)
    illustrious_profile = (
        "WAI-Nsfw-Illustrious-17",
        "sdxl",
        "### Prompt using dense Danbooru anime tags (1girl, character tags). Avoid photorealism.",
        "WAI-Nsfw-Illustrious-17"
    )
    monkeypatch.setattr(AssistantPromptBuilder, "resolve_gallery_target_profile", MagicMock(return_value=illustrious_profile))

    window._generate_ephemeral_rp_art_prompt("sess1", "Диалог", target_model_id="WAI-Nsfw-Illustrious-17")
    call_ill = window.lm_client.chat_completion.call_args[1]
    assert_diag("CRITICAL FORMAT RULES (SD 1.5" in call_ill["messages"][0]["content"], scen, "Illustrious tag dialect", "SD 1.5 rules for Illustrious", call_ill["messages"][0]["content"])

    # 3. Проверка диалекта для Juggernaut (SDXL arch + 'Avoid booru tags' -> phrases)
    juggernaut_profile = (
        "Juggernaut_XI",
        "sdxl",
        "Cinematic photorealistic portrait. Avoid booru tags, 1girl.",
        "Juggernaut_XI"
    )
    monkeypatch.setattr(AssistantPromptBuilder, "resolve_gallery_target_profile", MagicMock(return_value=juggernaut_profile))

    window._generate_ephemeral_rp_art_prompt("sess1", "Диалог", target_model_id="Juggernaut_XI")
    call_jug = window.lm_client.chat_completion.call_args[1]
    assert_diag("CRITICAL FORMAT RULES (SDXL" in call_jug["messages"][0]["content"], scen, "Juggernaut phrases dialect", "SDXL rules for Juggernaut", call_jug["messages"][0]["content"])

    # 4. Фоллбэк Subject на имя персонажа при пустом visual_dna
    window.active_character = CharacterCard(name="Лира", visual_dna="")
    cast_empty_dna = {
        "scene_type": "dialogue",
        "environment": {"location_dna": "лес"},
        "companion_state": {"current_outfit": "плащ"},
        "inventory": []
    }
    monkeypatch.setattr("services.rp_cast_manager.RPCastManager.load_cast", lambda *args, **kwargs: cast_empty_dna)

    window._generate_ephemeral_rp_art_prompt("sess1", "Реплика", target_model_id="generic_model")
    call_subj = window.lm_client.chat_completion.call_args[1]
    user_payload_subj = call_subj["messages"][1]["content"]
    assert_diag("- Subject: Лира" in user_payload_subj, scen, "Subject fallback to char_name", "- Subject: Лира", user_payload_subj)
    assert_diag("- Subject: Unspecified" not in user_payload_subj, scen, "Subject Unspecified removed", "No Unspecified", user_payload_subj)


# =========================================================================
# СЦЕНАРИЙ 4: FAIL-FAST GUARD ПЕРЕД VRAM_ARBITER
# =========================================================================

def test_scenario_4_fail_fast_guard_vram_arbiter(monkeypatch):
    """
    Сценарий 4:
    - Ответ LLM состоит только из <think>...</think> -> пустой эфемерный промпт.
    - Пайплайн прерывается по Fail-Fast Guard до обращения к диффузии.
    - VRAMArbiter.release_llm_for_diffusion НЕ вызывается (сохранение LLM в VRAM).
    - При оффлайн Forge (_is_forge_available == False) выгрузка LLM также блокируется.
    """
    scen = "Сценарий 4: Fail-Fast Guard перед VRAMArbiter"

    host = HardwareEngineMixin()
    host.active_character = None
    host.current_mode = "roleplay"
    host.current_session = {"id": "sess_fail_fast"}
    host._cancel_lms_reload = MagicMock()

    arbiter_mock = MagicMock()
    monkeypatch.setattr(VRAMArbiter, "get_instance", MagicMock(return_value=arbiter_mock))

    # Случай А: Ответ LLM пуст (все мысли срезаны)
    host._is_forge_available = MagicMock(return_value=True)
    host._generate_ephemeral_rp_art_prompt = MagicMock(return_value="")

    res_empty = host._scene_art_pipeline(v_payload=None, scene_text="Сцена с думами")
    assert_diag(res_empty.get("image_path") is None, scen, "Fail-fast on thought-only", "image_path is None", res_empty.get("image_path"))
    assert_diag(res_empty.get("error") == "Не удалось сформировать описание сцены", scen, "Fail-fast error message", "Не удалось сформировать описание сцены", res_empty.get("error"))
    assert_diag(not arbiter_mock.release_llm_for_diffusion.called, scen, "VRAM Arbiter protection", "release_llm not called", arbiter_mock.release_llm_for_diffusion.called)

    # Случай Б: Forge недоступен
    arbiter_mock.reset_mock()
    host._is_forge_available = MagicMock(return_value=False)
    host._generate_ephemeral_rp_art_prompt = MagicMock(return_value="1girl, valid prompt")

    res_offline = host._scene_art_pipeline(v_payload=None, scene_text="Сцена")
    assert_diag(res_offline.get("image_path") is None, scen, "Offline forge guard", "image_path is None", res_offline.get("image_path"))
    assert_diag(not arbiter_mock.release_llm_for_diffusion.called, scen, "VRAM Arbiter offline guard", "release_llm not called", arbiter_mock.release_llm_for_diffusion.called)


# =========================================================================
# СЦЕНАРИЙ 5: АРБИТРАЖ VRAM И СОХРАНЕНИЕ/ВОССТАНОВЛЕНИЕ KV-СЛОТА
# =========================================================================

def test_scenario_5_vram_arbitrage_and_kv_slot_preservation(smoke_isolation_guard, monkeypatch):
    """
    Сценарий 5:
    - Монопольное переключение владения GPU в VRAMArbiter (LLM <-> DIFFUSION).
    - Идемпотентность: повторный запуск одного типа задач не вызывает повторных выгрузок.
    - Отложенная выгрузка LLM при needs_llm_prompt_gen=True.
    - Сохранение KV-слота 0 в evicted_slot_0.bin и slot_metadata.json перед выгрузкой.
    - Восстановление KV-слота 0 при совпадающих параметрах запуска (context_length, model).
    - Инвалидация и безопасный сброс при несовпадении context_length или модели.
    """
    scen = "Сценарий 5: VRAM арбитраж и KV-слоты"

    # 1. Проверка монопольного владения VRAMArbiter
    mock_lm = MagicMock()
    mock_forge = MagicMock()
    mock_forge.is_server_ready.return_value = True

    arbiter = VRAMArbiter(lm_client=mock_lm, forge_client=mock_forge)
    assert_diag(arbiter._current_owner == GPUOwner.NONE, scen, "Initial GPU owner", GPUOwner.NONE, arbiter._current_owner)

    arbiter.prepare_for_task("llm")
    assert_diag(arbiter._current_owner == GPUOwner.LLM, scen, "Task LLM owner", GPUOwner.LLM, arbiter._current_owner)
    assert_diag(mock_forge.unload_checkpoint.call_count == 1, scen, "Unload Forge on LLM task", 1, mock_forge.unload_checkpoint.call_count)

    # Идемпотентность
    arbiter.prepare_for_task("llm")
    assert_diag(mock_forge.unload_checkpoint.call_count == 1, scen, "Idempotent LLM task", 1, mock_forge.unload_checkpoint.call_count)

    # Отложенная выгрузка для генерации арт-промпта
    arbiter.prepare_for_task("image", metadata={"needs_llm_prompt_gen": True})
    assert_diag(arbiter._current_owner == GPUOwner.LLM, scen, "Deferred release owner", GPUOwner.LLM, arbiter._current_owner)
    assert_diag(not mock_lm.unload_models_cli.called, scen, "Deferred release LLM not unloaded", False, mock_lm.unload_models_cli.called)

    arbiter.prepare_for_task("image", metadata={"needs_llm_prompt_gen": False})
    assert_diag(arbiter._current_owner == GPUOwner.DIFFUSION, scen, "Pure diffusion task owner", GPUOwner.DIFFUSION, arbiter._current_owner)
    assert_diag(mock_lm.unload_models_cli.call_count == 1, scen, "LLM unloaded for diffusion", 1, mock_lm.unload_models_cli.call_count)

    # 2. Сохранение и восстановление KV-слота 0 (ProcessManager)
    kv_dir = smoke_isolation_guard["kv_cache_dir"]

    pm = ProcessManager()
    pm._current_running_port = 1234
    fake_model = smoke_isolation_guard["tmp_path"] / "models" / "qwen.gguf"
    fake_model.parent.mkdir(parents=True, exist_ok=True)
    fake_model.write_bytes(b"GGUF_MODEL_DATA")
    pm._current_running_model_path = str(fake_model)
    pm._current_running_ctx = 8192
    pm._current_running_ngl = 99
    pm._current_running_ctk = "f16"
    pm._current_running_ctv = "f16"
    pm._current_running_fa = "true"
    pm.is_port_open = MagicMock(return_value=True)

    mock_resp_save = MagicMock(status_code=200)
    mock_resp_restore = MagicMock(status_code=200)

    def mock_requests_post(url, json=None, timeout=5.0):
        if "action=save" in url:
            (kv_dir / "evicted_slot_0.bin").write_bytes(b"KV_DUMP_DATA_BYTES")
            return mock_resp_save
        elif "action=restore" in url:
            return mock_resp_restore
        return MagicMock(status_code=400)

    monkeypatch.setattr("requests.post", mock_requests_post)

    meta_current = {
        "model_path": str(fake_model.resolve()),
        "model_mtime": os.path.getmtime(fake_model),
        "context_length": 8192,
        "gpu_layers": 99,
        "kv_cache_type_k": "f16",
        "kv_cache_type_v": "f16",
        "flash_attn": "true"
    }

    # Сохранение слота
    saved = pm._save_kv_slot_for_eviction()
    assert_diag(saved is True, scen, "Save KV slot status", True, saved)
    assert_diag((kv_dir / "slot_metadata.json").exists(), scen, "Metadata file exists", True, (kv_dir / "slot_metadata.json").exists())
    assert_diag((kv_dir / "evicted_slot_0.bin").exists(), scen, "Dump file exists", True, (kv_dir / "evicted_slot_0.bin").exists())

    # Восстановление слота при совпадающих параметрах
    restored = pm._restore_kv_slot_after_start(expected_meta=meta_current)
    assert_diag(restored is True, scen, "Restore KV slot status", True, restored)
    assert_diag(not (kv_dir / "slot_metadata.json").exists(), scen, "Metadata cleaned up after restore", False, (kv_dir / "slot_metadata.json").exists())
    pm.clear_kv_slot_cache()
    assert_diag(not (kv_dir / "evicted_slot_0.bin").exists(), scen, "Dump cleaned up after post-stop", False, (kv_dir / "evicted_slot_0.bin").exists())

    # Инвалидация при несовпадении context_length
    pm._save_kv_slot_for_eviction()
    meta_mismatched = dict(meta_current)
    meta_mismatched["context_length"] = 16384

    restored_mismatch = pm._restore_kv_slot_after_start(expected_meta=meta_mismatched)
    assert_diag(restored_mismatch is False, scen, "Invalidate KV on context mismatch", False, restored_mismatch)
    assert_diag(not (kv_dir / "slot_metadata.json").exists(), scen, "Metadata cleaned up after invalidation", False, (kv_dir / "slot_metadata.json").exists())
    pm.clear_kv_slot_cache()
    assert_diag(not (kv_dir / "evicted_slot_0.bin").exists(), scen, "Dump cleaned up after post-stop", False, (kv_dir / "evicted_slot_0.bin").exists())


# =========================================================================
# СЦЕНАРИЙ 6: АРХЕТИПЫ КАРТОЧЕК И RPG-РАЗМЕТКА
# =========================================================================

def test_scenario_6_card_archetypes_and_rpg_markup_preservation():
    """
    Сценарий 6:
    - Архетипы карточек: system (<stats>), dating (<meters>), cyoa (<choices>), detective (<stats>), group.
    - Детекция архетипов через TavernParser.detect_card_archetype.
    - Парсер RPG-разметки RPGContentParser.parse (<check>, <stats>, <meters>, <choices>).
    - Генерация эталонных Depth-якорей для каждого жанра.
    - Стили мыслей по архетипам и поддержка кастомных расширений (extensions.neonroute.thought_style).
    - Санитизация: технические теги вырезаются из промпта арта, но сохраняются в тексте диалога.
    """
    scen = "Сценарий 6: Архетипы карточек и RPG-разметка"

    # 1. Детекция архетипов через TavernParser
    arch_info = TavernParser.detect_card_archetype({
        "name": "System Guide",
        "description": "Система уровней, характеристик, квестов и инвентаря игрока [STATUS] Level Up LitRPG."
    })
    assert_diag(isinstance(arch_info, dict) and arch_info.get("is_system") is True, scen, "Detect system archetype", True, arch_info.get("is_system"))

    # 2. Проверка Depth-якорей по 5 архетипам
    card_sys = CharacterCard(name="Система", is_system=True)
    anchor_sys = RoleplayPromptBuilder.get_default_depth_anchor(card_sys, "Игрок")
    assert_diag("<stats>" in anchor_sys, scen, "System archetype anchor", "Contains <stats>", anchor_sys)

    card_dating = CharacterCard(name="Лира", is_dating=True)
    anchor_dating = RoleplayPromptBuilder.get_default_depth_anchor(card_dating, "Игрок")
    assert_diag("<meters>" in anchor_dating and "симпатии" in anchor_dating, scen, "Dating archetype anchor", "Contains <meters>", anchor_dating)

    card_cyoa = CharacterCard(name="Хроники", is_cyoa=True)
    anchor_cyoa = RoleplayPromptBuilder.get_default_depth_anchor(card_cyoa, "Игрок")
    assert_diag("<choices>" in anchor_cyoa, scen, "CYOA archetype anchor", "Contains <choices>", anchor_cyoa)

    card_det = CharacterCard(name="Холмс", is_detective=True)
    anchor_det = RoleplayPromptBuilder.get_default_depth_anchor(card_det, "Игрок")
    assert_diag("<stats>" in anchor_det and "расследование" in anchor_det, scen, "Detective archetype anchor", "Contains <stats>", anchor_det)

    card_grp = CharacterCard(name="Отряд", is_group=True)
    anchor_grp = RoleplayPromptBuilder.get_default_depth_anchor(card_grp, "Игрок")
    assert_diag("группы спутников" in anchor_grp, scen, "Group archetype anchor", "Contains группы спутников", anchor_grp)

    # 3. Стили мыслей по архетипам и кастомный оверрайд
    assert_diag(resolve_thought_style(card_sys).icon == "⚙️", scen, "System thought icon", "⚙️", resolve_thought_style(card_sys).icon)
    assert_diag(resolve_thought_style(card_dating).icon == "✨", scen, "Dating thought icon", "✨", resolve_thought_style(card_dating).icon)
    assert_diag(resolve_thought_style(card_cyoa).icon == "🧭", scen, "CYOA thought icon", "🧭", resolve_thought_style(card_cyoa).icon)
    assert_diag(resolve_thought_style(card_det).icon == "🔍", scen, "Detective thought icon", "🔍", resolve_thought_style(card_det).icon)

    custom_raw = {
        "data": {
            "name": "Моргана",
            "extensions": {
                "neonroute": {
                    "thought_style": {
                        "icon": "🔮",
                        "active_label": "Моргана читает ауру...",
                        "collapsed_label": "Астральное эхо",
                        "accent_color": "#D946EF"
                    }
                }
            }
        }
    }
    card_custom = CharacterCard(name="Моргана", raw_data=custom_raw)
    style_custom = resolve_thought_style(card_custom)
    assert_diag(style_custom.icon == "🔮", scen, "Custom override icon", "🔮", style_custom.icon)
    assert_diag(style_custom.collapsed_label == "Астральное эхо", scen, "Custom override label", "Астральное эхо", style_custom.collapsed_label)

    # 4. RPGContentParser: парсинг игровых виджетов
    rp_message = (
        "Стражник преградил путь.\n"
        "<check>Убеждение | Сложность 15 | Успех</check>\n"
        "<stats>\nЛокация: Ворота замка\nИнвентарь: Меч, Перстень\n</stats>\n"
        "<meters>\nДоверие: 65%\n</meters>\n"
        "<choices>\n1. Показать пропуск\n2. Обойти стену\n</choices>"
    )
    bundle = RPGContentParser.parse(rp_message)
    assert_diag(bundle is not None, scen, "RPGContentParser output", "Bundle present", bundle)
    assert_diag(bundle.choices is not None and len(bundle.choices) == 2, scen, "Parsed choices", 2, len(bundle.choices) if bundle.choices else 0)

    # 5. Санитизация контекста сцены от игровых метатегов
    raw_rpg_text = (
        "Кай атакует орка!\n"
        "<stats>HP: 85/100 | MP: 20/40</stats>\n"
        "<thought>Нужно ударить в спину</thought>\n"
        "[СТАТУС: Бой]\n"
        "[СЦЕНА: Пещера]\n"
        "[КВЕСТ: Спасение сестер]\n"
        "[ШРАМ: Ожог плеча]\n"
        "[IMAGE_PROMPT: 1boy, combat stance]\n"
        "*Кай делает выпад кинжалом.*"
    )

    clean_ctx = raw_rpg_text
    clean_ctx = re.sub(r'<stats>.*?</stats>', '', clean_ctx, flags=re.DOTALL | re.IGNORECASE)
    clean_ctx = re.sub(r'<thought>.*?</thought>', '', clean_ctx, flags=re.DOTALL | re.IGNORECASE)
    clean_ctx = re.sub(rf'\[(?:СЦЕНА|SCENE|СТАТУС|STATUS|ШРАМ|SCAR|КВЕСТ|QUEST|{TextRegexFilter.IMAGE_TAG_LABEL_PATTERN})[^\]]*\]', '', clean_ctx, flags=re.IGNORECASE)
    clean_ctx = re.sub(r'</?(?:stats|thought|meters|choices|check|terminal|sms|phone)>', '', clean_ctx, flags=re.IGNORECASE).strip()

    assert_diag("HP: 85/100" not in clean_ctx, scen, "Stats stripped", "No HP in clean_ctx", clean_ctx)
    assert_diag("Нужно ударить в спину" not in clean_ctx, scen, "Thoughts stripped", "No thought in clean_ctx", clean_ctx)
    assert_diag("[СТАТУС: Бой]" not in clean_ctx, scen, "Status stripped", "No STATUS in clean_ctx", clean_ctx)
    assert_diag("[IMAGE_PROMPT:" not in clean_ctx, scen, "Image prompt stripped", "No IMAGE_PROMPT in clean_ctx", clean_ctx)
    assert_diag("Кай делает выпад кинжалом" in clean_ctx, scen, "Narrative preserved", "Narrative in clean_ctx", clean_ctx)


# =========================================================================
# СЦЕНАРИЙ 7: ИЗОЛЯЦИЯ СЕССИЙ, ПЕРСИСТЕНТНОСТЬ И ДЕТЕКЦИЯ УТЕЧЕК
# =========================================================================

def test_scenario_7_session_isolation_and_persistence(smoke_isolation_guard):
    """
    Сценарий 7:
    - Изоляция сессий при переключении: кастомные промпты и якоря Сессии А не протекают в Сессию Б.
    - Сохранение РП-сессии со всеми метаданными: chat_history, character_card, thoughts, image_path, visual_intent.
    - Круговая загрузка сессии через SessionManager.load_session.
    - Защита от утечек (leak detector): отсутствие временных файлов в реальном C:\\NeonRoute\\history.
    """
    scen = "Сценарий 7: Изоляция и персистентность сессий"

    card = CharacterCard(
        name="Серафина",
        description="Эльфийка",
        personality="Спокойная",
        scenario="Лес",
        first_mes="*Приветствует путника.*",
        visual_dna="amber eyes, golden hair"
    )

    sess_a_id = "session_A_persisted"
    sess_b_id = "session_B_isolated"

    # 1. Проверка изоляции кастомного промпта
    custom_sys_a = "# Специфический системный промпт Сессии А"
    custom_anc_a = "[Кастомный стиль якоря А]"

    history_a = [{"role": "assistant", "content": card.first_mes}, {"role": "user", "content": "Действие в А"}]
    history_b = [{"role": "assistant", "content": card.first_mes}, {"role": "user", "content": "Действие в Б"}]

    msg_a = SessionManager.get_lm_studio_messages(
        history=history_a,
        is_roleplay=True,
        character_card=card,
        user_name="Игрок",
        session_id=sess_a_id,
        custom_system_prompt=custom_sys_a
    )
    anc_a = RoleplayPromptBuilder.build_depth_anchor(card, user_name="Игрок", user_query="Действие в А", custom_anchor=custom_anc_a)
    msg_a.insert(-1, {"role": "system", "content": anc_a.strip()})

    msg_b = SessionManager.get_lm_studio_messages(
        history=history_b,
        is_roleplay=True,
        character_card=card,
        user_name="Игрок",
        session_id=sess_b_id,
        custom_system_prompt=None
    )
    anc_b = RoleplayPromptBuilder.build_depth_anchor(card, user_name="Игрок", user_query="Действие в Б", custom_anchor=None)
    msg_b.insert(-1, {"role": "system", "content": anc_b.strip()})

    assert_diag(custom_sys_a in msg_a[0]["content"], scen, "Session A custom prompt", "Custom prompt in A", msg_a[0]["content"])
    assert_diag(custom_anc_a in msg_a[-2]["content"], scen, "Session A custom anchor", "Custom anchor in A", msg_a[-2]["content"])

    assert_diag(custom_sys_a not in msg_b[0]["content"], scen, "Session B isolation from prompt A", "No custom prompt in B", msg_b[0]["content"])
    assert_diag(custom_anc_a not in msg_b[-2]["content"], scen, "Session B isolation from anchor A", "No custom anchor in B", msg_b[-2]["content"])

    # 2. Сохранение и загрузка сессии с мыслями и картинкой
    sess_data = {
        "id": sess_a_id,
        "name": "Сессия Серафины",
        "character_card": card.to_dict(),
        "chat_history": [
            {"role": "user", "content": "Привет!"},
            {
                "role": "assistant",
                "content": "*Улыбается и протягивает руку.*",
                "thought": {"content": "Он кажется дружелюбным.", "token_count": 15, "duration": 1.2},
                "image_path": "images/sess_a_img.png",
                "visual_intent": {
                    "positive_prompt": "1girl, amber eyes, forest",
                    "checkpoint": "cyberrealistic_v50.safetensors"
                }
            }
        ]
    }

    SessionManager.save_session(sess_data)

    loaded_sess = SessionManager.load_session(sess_a_id)
    assert_diag(loaded_sess is not None, scen, "Load persisted session", "Loaded dict", loaded_sess)
    assert_diag(loaded_sess.get("name") == "Сессия Серафины", scen, "Loaded session name", "Сессия Серафины", loaded_sess.get("name"))

    loaded_history = loaded_sess.get("chat_history", [])
    assert_diag(len(loaded_history) == 2, scen, "Loaded history count", 2, len(loaded_history))

    last_assistant = loaded_history[1]
    assert_diag(last_assistant.get("role") == "assistant", scen, "Loaded turn role", "assistant", last_assistant.get("role"))
    assert_diag("Улыбается" in last_assistant.get("content", ""), scen, "Loaded turn content", "Contains text", last_assistant.get("content"))
    assert_diag(last_assistant.get("image_path") == "images/sess_a_img.png", scen, "Loaded image_path", "images/sess_a_img.png", last_assistant.get("image_path"))

    loaded_thought = last_assistant.get("thought", {})
    assert_diag(loaded_thought.get("content") == "Он кажется дружелюбным.", scen, "Loaded thought content", "Он кажется дружелюбным.", loaded_thought.get("content"))
    assert_diag(loaded_thought.get("token_count") == 15, scen, "Loaded thought tokens", 15, loaded_thought.get("token_count"))

    loaded_v_intent = last_assistant.get("visual_intent", {})
    assert_diag("1girl, amber eyes" in loaded_v_intent.get("positive_prompt", ""), scen, "Loaded visual intent", "Contains tags", loaded_v_intent.get("positive_prompt"))

    # 3. Leak detector: проверка отсутствия файлов в реальном C:\NeonRoute\history
    prod_history = Path(r"C:\NeonRoute\history")
    if prod_history.exists():
        leaked = list(prod_history.glob(f"*{sess_a_id}*")) + list(prod_history.glob(f"*{sess_b_id}*"))
        assert_diag(len(leaked) == 0, scen, "Production leak detector", "0 leaked files", leaked)
