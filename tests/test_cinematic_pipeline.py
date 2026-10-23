import os
import sys

# Добавляем корень проекта в пути поиска модулей
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Ваши стандартные импорты дальше:
# from services.roleplay_prompt_builder import RoleplayPromptBuilder
# from services.art_director.director import ArtDirector

"""
Сквозной тест кинематографического арт-пайплайна NeonRoute (test_cinematic_pipeline.py).
Проверяет:
1. Контракт VisualIntent (нормализация scene_type, защита от лишних полей).
2. Директивы RoleplayPromptBuilder (4 архетипа, format_rules, теги 1girl, solo, фоллбэк).
3. ArtDirector (подавление одежды в intimate, Zero-logic ADetailer, отсутствие ложных 2girls от together/both, очистка (lora:...) и хвостов версий).
4. LM Tagifier (изоляция флагов и чистка кириллицы).
5. LoraManager (чтение метаданных safetensors без VRAM и сборка LoRA).
6. RoleplayFlow (английское ТЗ для LLM, отсечение экипировки в интиме, бронебойный парсинг ответа со срезом --model и хвостов версий).
"""

import sys
import json
import struct
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

# Гарантируем видимость корневых модулей приложения
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from services.art_director.contracts import VisualIntent, TargetSubject
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.art_director.director import ArtDirector, tagify_prompt_with_lm
from services.art_director.lora_manager import extract_lora_metadata, LoraManager
from services.roleplay_flow_controller import RoleplayFlowControllerMixin


def run_tests():
    passed = 0
    total = 6
    print("\n=======================================================")
    print("🚀 СТАРТ СКВОЗНОГО ТЕСТИРОВАНИЯ КИНО-АРХЕТИПОВ И РЕЖИССУРЫ")
    print("=======================================================\n")

    # -------------------------------------------------------------
    # ТЕСТ 1: VisualIntent — контракт, from_dict и нормализация
    # -------------------------------------------------------------
    try:
        raw_payload = {
            "prompt": "1girl, 2B, nude",
            "scene_type": "INTIMATE",  # проверка регистра
            "unexpected_noise_key": "ignore_me",  # проверка отсечения мусора
            "target_subject": "companion",
            "aspect_ratio": "portrait"
        }
        intent = VisualIntent.from_dict(raw_payload)
        assert intent.scene_type == "intimate", f"Ожидался 'intimate', получено '{intent.scene_type}'"
        assert intent.target_subject == TargetSubject.COMPANION

        # Проверка фоллбэка неизвестного типа
        fallback_intent = VisualIntent.from_dict({"prompt": "test", "scene_type": "unreal_genre"})
        assert fallback_intent.scene_type == "dialogue", f"Ожидался фоллбэк 'dialogue', получено '{fallback_intent.scene_type}'"

        print("✅ [1/6] [PASSED] VisualIntent Contract: Парсинг, нормализация и защита от лишних полей — OK")
        passed += 1
    except Exception as e:
        print(f"❌ [1/6] [FAILED] VisualIntent Contract: СБОЙ -> {e}")

    # -------------------------------------------------------------
    # ТЕСТ 2: RoleplayPromptBuilder — кино-директивы и format_rules
    # -------------------------------------------------------------
    try:
        intimate_dir = RoleplayPromptBuilder.get_cinematic_scene_directive("intimate", "realistic")
        action_dir = RoleplayPromptBuilder.get_cinematic_scene_directive("action", "realistic")
        dialogue_dir = RoleplayPromptBuilder.get_cinematic_scene_directive("dialogue", "realistic")
        exploration_dir = RoleplayPromptBuilder.get_cinematic_scene_directive("exploration", "realistic")

        assert "CRITICAL FORMAT RULES:" in intimate_dir
        assert "CRITICAL FORMAT RULES:" in action_dir
        assert "CRITICAL FORMAT RULES:" in dialogue_dir
        assert "CRITICAL FORMAT RULES:" in exploration_dir

        assert "1girl/1boy, solo, nude" in intimate_dir
        assert "1girl/1boy, solo, <action_tags>" in action_dir
        assert "1girl/1boy, solo, <dialogue_tags>" in dialogue_dir
        assert "1girl/1boy/scenery, <scenery_tags>" in exploration_dir

        assert "INTIMATE & PRIVATE SCENE" in intimate_dir
        assert "STRICT PROHIBITION" in intimate_dir
        assert "COMBAT & HIGH-STAKES ACTION" in action_dir
        assert "DIALOGUE & SOCIAL INTERACTION" in dialogue_dir
        assert "EXPLORATION & ENVIRONMENT" in exploration_dir
        assert "--ar wide" in exploration_dir

        # Фоллбэк при пустом/неизвестном типе
        assert "DIALOGUE" in RoleplayPromptBuilder.get_cinematic_scene_directive("invalid_type")

        print("✅ [2/6] [PASSED] RoleplayPromptBuilder: Все 4 архетипа кадра, format_rules и адаптивные теги — OK")
        passed += 1
    except Exception as e:
        print(f"❌ [2/6] [FAILED] RoleplayPromptBuilder: СБОЙ -> {e}")

    # -------------------------------------------------------------
    # ТЕСТ 3: ArtDirector — Zero-logic ADetailer, чистка LoRA, отсутствие ложных 2girls
    # -------------------------------------------------------------
    try:
        director = ArtDirector()
        
        # 1. Интимная сцена: подавление одежды должно инжектироваться
        intent_intimate = VisualIntent(
            prompt="1girl, 2B, nude, lying in bed",
            scene_type="intimate",
            suggested_model="realistic"
        )
        plan_intimate = director.plan_from_visual_intent(intent_intimate)
        assert "clothes, dress, armor" in plan_intimate.negative_prompt, "В негативном промпте отсутствует подавление одежды!"

        # 2. Боевая сцена: подавление одежды НЕ должно присутствовать
        intent_action = VisualIntent(
            prompt="1girl, 2B, combat stance, sword",
            scene_type="action",
            suggested_model="realistic"
        )
        plan_action = director.plan_from_visual_intent(intent_action)
        assert "clothes, dress, armor" not in plan_action.negative_prompt, "Одежда ошибочно заблокирована в боевой сцене!"

        # 3. Zero-logic ADetailer: активен даже для абстрактных сцен, если это не background
        intent_no_human = VisualIntent(
            prompt="cyberpunk neon alley, rain reflections, volumetric fog",
            suggested_model="realistic"
        )
        plan_no_human = director.plan_from_visual_intent(intent_no_human)
        assert plan_no_human.enable_adetailer is True, "Zero-logic ADetailer должен быть включен!"
        assert plan_no_human.adetailer_hands_enabled is True, "Hands ADetailer должен быть включен!"

        # 4. Проверка исключения together/both из триггеров multi-char
        from services.art_director.director import _assemble_roleplay_context
        ctx_res = _assemble_roleplay_context(
            base_prompt="talking together, both smiling, solo view",
            session_id=None,
            character_name="2B",
            character=None
        )
        assert not ctx_res["has_secondary"], "Слова 'together' и 'both' не должны провоцировать групповую сцену!"

        # 5. Очистка паразитных LoRA в скобках, голых lora:... и оторванных хвостов версий
        intent_dirty = VisualIntent(
            prompt="1girl, solo, (lora:bad_lora:0.8), [lora:another_lora], lora:naked_lora:0.9, detailed eyes v5",
            suggested_model="realistic"
        )
        plan_dirty = director.plan_from_visual_intent(intent_dirty)
        assert "bad_lora" not in plan_dirty.positive_prompt, "Паразитный (lora:...) не был очищен!"
        assert "another_lora" not in plan_dirty.positive_prompt, "Паразитный [lora:...] не был очищен!"
        assert "naked_lora" not in plan_dirty.positive_prompt, "Голый lora:... не был очищен!"
        assert not plan_dirty.positive_prompt.endswith("v5"), "Оторванный хвост v5 не был срезан!"

        print("✅ [3/6] [PASSED] ArtDirector: Zero-logic ADetailer, подавление одежды, чистка LoRA и отсутствие ложных 2girls — OK")
        passed += 1
    except Exception as e:
        print(f"❌ [3/6] [FAILED] ArtDirector: СБОЙ -> {e}")

    # -------------------------------------------------------------
    # ТЕСТ 4: Tagifier — изоляция флагов и чистка кириллицы
    # -------------------------------------------------------------
    try:
        # Проверка изоляции флагов без LM Studio (fallback)
        raw_cli = "девушка воин на улице --ar wide --model realistic"
        sanitized = tagify_prompt_with_lm(raw_cli, lm_client=None)
        
        # Кириллица должна быть срезана, а флаги сохранены
        assert "--ar wide" in sanitized
        assert "--model realistic" in sanitized
        assert not any(ord(c) >= 0x0400 and ord(c) <= 0x04FF for c in sanitized), "Обнаружены русские буквы после очистки!"

        # Проверка с моком LM Studio
        mock_lms = MagicMock()
        mock_lms.chat_completion.return_value = "1girl, warrior, street"
        res_mock = tagify_prompt_with_lm("девушка воин на улице --ar square", lm_client=mock_lms)
        assert "1girl, warrior, street" in res_mock
        assert "--ar square" in res_mock

        print("✅ [4/6] [PASSED] LM Tagifier: Сохранение CLI-флагов и защита токенизатора от кириллицы — OK")
        passed += 1
    except Exception as e:
        print(f"❌ [4/6] [FAILED] LM Tagifier: СБОЙ -> {e}")

    # -------------------------------------------------------------
    # ТЕСТ 5: LoraManager — чтение safetensors без VRAM и сборка тега
    # -------------------------------------------------------------
    try:
        # Создаем валидный заголовок safetensors во временном файле
        dummy_meta = {
            "__metadata__": {
                "modelspec.trigger_phrase": "yorha 2b, blindfold, combat dress"
            }
        }
        json_bytes = json.dumps(dummy_meta).encode("utf-8")
        header_len = len(json_bytes)
        header_prefix = struct.pack("<Q", header_len)

        with tempfile.NamedTemporaryFile(suffix=".safetensors", delete=False) as tf:
            tf.write(header_prefix)
            tf.write(json_bytes)
            tf.write(b"\x00" * 128)  # имитация тензоров
            tmp_path = Path(tf.name)

        try:
            triggers = extract_lora_metadata(tmp_path)
            assert "yorha 2b" in triggers, f"Триггеры не извлечены: {triggers}"

            # Проверка сборки тега через LoraManager
            LoraManager._trigger_cache["mock_lora"] = triggers
            tag = LoraManager.build_lora_tag("mock_lora", 0.75, prompt="blindfold")
            # blindfold уже в промпте, должен дописать недостающий yorha 2b
            assert "<lora:mock_lora:0.75>" in tag
            assert "yorha 2b" in tag
            assert "blindfold" not in tag.split(">")[1], "Дубликат триггера не был отсечен!"
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

        print("✅ [5/6] [PASSED] LoraManager: Парсер заголовка safetensors на CPU и автосборка триггеров — OK")
        passed += 1
    except Exception as e:
        print(f"❌ [5/6] [FAILED] LoraManager: СБОЙ -> {e}")

    # -------------------------------------------------------------
    # ТЕСТ 6: RoleplayFlow — ТЗ на английском, блокировка экипировки в intimate и парсинг
    # -------------------------------------------------------------
    try:
        class DummyController(RoleplayFlowControllerMixin):
            def __init__(self):
                self.active_character = MagicMock()
                self.active_character.name = "2B"
                self.active_character.visual_dna = "white hair, blindfold, <lora:char_lora:0.8>"
                self.lm_client = MagicMock()
                self.active_lms_model = "l3-8b-stheno-v3.2"

        controller = DummyController()

        # Мокаем ответ LM Studio с мусором, [Description] и хвостом модели
        controller.lm_client.chat_completion.return_value = (
            "[IMAGE_PROMPT: 1girl, solo, 2B, nude, soft lighting --ar portrait --model MeinaHentai v5]\n\n"
            "[Description]\n"
            "Here is a scene describing 2B in a quiet chamber."
        )

        from services.rp_cast_manager import RPCastManager
        orig_load = RPCastManager.load_cast

        # 1. Прогон intimate: инвентарь должен быть отсечен, ТЗ строго на английском
        RPCastManager.load_cast = MagicMock(return_value={
            "scene_type": "intimate",
            "current_location": "Private quarters",
            "inventory": ["katana", "pod 042", "combat belt"],
            "companion_state": {"current_outfit": "combat dress"}
        })

        prompt_res = controller._generate_ephemeral_rp_art_prompt("sess_123", "We remained alone in the room.", target_model_id="MeinaHentai v5")
        assert "nude" in prompt_res or "1girl" in prompt_res
        assert "--model" not in prompt_res, f"Флаг --model не был срезан: {prompt_res}"
        assert not prompt_res.endswith("v5"), f"Хвост v5 не был срезан: {prompt_res}"
        assert "Description" not in prompt_res, f"Блок Description просочился: {prompt_res}"

        # Проверяем английское ТЗ, отправленное в LM Studio
        call_args = controller.lm_client.chat_completion.call_args[1]
        sent_user_payload = call_args["messages"][1]["content"]
        assert "Equipment" not in sent_user_payload, "Инвентарь не был заблокирован в интимной сцене!"
        assert "<lora:" not in sent_user_payload, "Тег LoRA просочился во входящее ТЗ для LLM!"
        assert "strictly alone in private intimate sanctuary" in sent_user_payload
        assert "[CINEMATIC DIRECTIVE: INTIMATE & PRIVATE SCENE]" in call_args["messages"][0]["content"]

        # 2. Прогон action: оружие обязано передаваться через Equipment
        RPCastManager.load_cast = MagicMock(return_value={
            "scene_type": "action",
            "current_location": "Ruined bridge",
            "inventory": ["katana"],
            "companion_state": {"current_outfit": "combat dress"}
        })
        controller._generate_ephemeral_rp_art_prompt("sess_123", "Enemies incoming!", target_model_id="MeinaHentai v5")
        sent_user_payload_action = controller.lm_client.chat_completion.call_args[1]["messages"][1]["content"]
        assert "- Equipment: katana" in sent_user_payload_action, "Оружие не попало в ТЗ боевой сцены!"

        RPCastManager.load_cast = orig_load

        print("✅ [6/6] [PASSED] RoleplayFlow: Английское ТЗ, исключение оружия в интиме и бронебойный парсер — OK")
        passed += 1
    except Exception as e:
        print(f"❌ [6/6] [FAILED] RoleplayFlow: СБОЙ -> {e}")

    # -------------------------------------------------------------
    # ИТОГ
    # -------------------------------------------------------------
    print("\n-------------------------------------------------------")
    if passed == total:
        print(f"🎉 ВСЕ ТЕСТЫ УСПЕШНО ПРОЙДЕНЫ ({passed}/{total})! СТАТУС: PASSED")
    else:
        print(f"⚠️ ТЕСТЫ ЗАВЕРШЕНЫ С ОШИБКАМИ: {passed}/{total} успешно. СТАТУС: FAILED")
    print("-------------------------------------------------------\n")
    return passed == total


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
