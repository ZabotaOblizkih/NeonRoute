"""
tests/test_rp_art_prompt_injection.py
Тесты инъекции данных лора (локация, экипировка, наряд) в ТЗ для LLM-директора артов.
Проверяется реальный метод _generate_ephemeral_rp_art_prompt и его обращение к lm_client.
"""

import uuid
from unittest.mock import MagicMock
import pytest

from services.rp_cast_manager import RPCastManager
from services.roleplay_flow_controller import RoleplayFlowControllerMixin


@pytest.fixture
def temp_rp_session():
    """Создает изолированную сессию с тестовыми данными лора в cast_<id>.json."""
    sid = f"test_art_lore_{uuid.uuid4().hex[:8]}"

    cast = RPCastManager.load_cast(sid, companion_name="Элария")
    cast["current_location"] = "Священная роща у подножия Башни"
    cast["inventory"] = ["Божественный символ", "Стальной кинжал (экипировано)"]
    cast.setdefault("companion_state", {})["current_outfit"] = "Серебряная кольчуга и синий плащ"
    cast.setdefault("stats", {})["BP"] = "5,000,000"
    cast["stats"]["Уровень"] = "∞"
    RPCastManager.save_cast(sid, cast)

    yield sid

    RPCastManager.cleanup_session(sid)


def test_art_prompt_lore_injection_action_scene(temp_rp_session):
    """
    Проверяет сценарий экшен-сцены:
    - scene_type='action' в cast_data включает строку Equipment с инвентарем.
    - Данные локации, наряда и внешности подставляются в user_payload для LLM.
    - Из реплики срезаются <stats> и числа, не попадая в Scene context.
    """
    sid = temp_rp_session

    # 1. Задаем архетип экшен-сцены в касте
    cast = RPCastManager.load_cast(sid)
    cast["scene_type"] = "action"
    RPCastManager.save_cast(sid, cast)

    # 2. Инициализируем контроллер с боевыми полями
    controller = MagicMock(spec=RoleplayFlowControllerMixin)
    controller.active_character = MagicMock()
    controller.active_character.name = "Элария"
    controller.active_character.visual_dna = "1girl, silver hair, blue eyes, elf ears"
    controller.active_lms_model = "qwen2.5-vl-7b"

    # Перехватываем боевой вызов chat_completion
    captured_messages = None

    def fake_chat_completion(**kwargs):
        nonlocal captured_messages
        captured_messages = kwargs.get("messages", [])
        return "[IMAGE_PROMPT: masterpiece, 1girl, silver hair, holding dagger]"

    controller.lm_client = MagicMock()
    controller.lm_client.chat_completion = fake_chat_completion

    raw_assistant_turn = (
        "Элария встает в боевую стойку, сжимая оружие обеими руками.\n"
        "<stats>\n"
        "Местоположение: Священная роща у подножия Башни\n"
        "BP: 5,000,000\n"
        "XP: 0/100\n"
        "Золото: 0G\n"
        "</stats>\n"
        "Листья вокруг закружились от поднявшегося порыва ветра."
    )

    # 3. Вызываем реальный метод продакшена
    result = RoleplayFlowControllerMixin._generate_ephemeral_rp_art_prompt(
        controller,
        sess_id=sid,
        last_assistant_message=raw_assistant_turn
    )

    assert captured_messages is not None, "Метод не отправил запрос в lm_client.chat_completion!"
    user_payload = captured_messages[1]["content"]

    # 4. Проверяем наполнение данными из каста ("Правила мира и Лор")
    assert "- Location: Священная роща у подножия Башни" in user_payload
    assert "- Outfit: Серебряная кольчуга и синий плащ" in user_payload
    assert "- Equipment: Божественный символ, Стальной кинжал (экипировано)" in user_payload
    assert "Элария: 1girl, silver hair, blue eyes, elf ears" in user_payload

    # 5. Проверяем сохранение живого контекста
    assert "Элария встает в боевую стойку" in user_payload
    assert "Листья вокруг закружились" in user_payload

    # 6. Проверяем фильтрацию мусора: теги и циферки отсечены
    assert "<stats>" not in user_payload
    assert "</stats>" not in user_payload
    assert "5,000,000" not in user_payload
    assert "0/100" not in user_payload
    assert "BP:" not in user_payload


def test_art_prompt_lore_injection_intimate_scene_strips_gear(temp_rp_session):
    """
    Проверяет сценарий интимной сцены:
    - scene_type='intimate' отсекает снаряжение (строка Equipment отсутствует).
    - В ТЗ добавляется директива bare skin / undressed.
    """
    sid = temp_rp_session

    cast = RPCastManager.load_cast(sid)
    cast["scene_type"] = "intimate"
    RPCastManager.save_cast(sid, cast)

    controller = MagicMock(spec=RoleplayFlowControllerMixin)
    controller.active_character = MagicMock()
    controller.active_character.name = "Элария"
    controller.active_character.visual_dna = "1girl, silver hair"
    controller.active_lms_model = "qwen2.5-vl-7b"

    captured_messages = None

    def fake_chat_completion(**kwargs):
        nonlocal captured_messages
        captured_messages = kwargs.get("messages", [])
        return "[IMAGE_PROMPT: 1girl, silver hair, bare shoulders]"

    controller.lm_client = MagicMock()
    controller.lm_client.chat_completion = fake_chat_completion

    RoleplayFlowControllerMixin._generate_ephemeral_rp_art_prompt(
        controller,
        sess_id=sid,
        last_assistant_message="Тихий шёпот в полумраке купальни."
    )

    assert captured_messages is not None
    user_payload = captured_messages[1]["content"]

    assert "- Location: Священная роща у подножия Башни" in user_payload
    assert "characters undressed, bare skin" in user_payload
    assert "- Equipment:" not in user_payload
