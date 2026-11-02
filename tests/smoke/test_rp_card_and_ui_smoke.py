import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from services.tavern_models import CharacterCard
from services.tavern_parser import TavernParser
from services.session_manager import SessionManager
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.task_queue import InferenceTask
import config


@pytest.fixture(autouse=True)
def isolate_session_storage(tmp_path, monkeypatch):
    """Полная изоляция сессий в ephemeral tmp_path."""
    ephemeral_history = tmp_path / "history"
    ephemeral_history.mkdir(parents=True, exist_ok=True)
    if hasattr(config, "HISTORY_DIR"):
        monkeypatch.setattr(config, "HISTORY_DIR", ephemeral_history)

    import services.session_manager as sm
    monkeypatch.setattr(sm, "HISTORY_DIR", ephemeral_history, raising=False)
    import services.rp_cast_manager as rcm
    monkeypatch.setattr(rcm, "HISTORY_DIR", ephemeral_history, raising=False)
    monkeypatch.setattr(SessionManager, "SESSIONS_DIR", ephemeral_history, raising=False)

    SessionManager._sessions_cache.clear()
    yield ephemeral_history
    SessionManager._sessions_cache.clear()


def test_card_visual_dna_and_lora_parsing():
    """Сценарий 1: Парсинг карточки, внешности и LoRA."""
    # 1. Прямое создание в памяти (модель CharacterCard)
    card = CharacterCard(
        name="Тестовый Персонаж",
        description="Воительница с серебряными волосами. <lora:silver_hair_v1:0.75>",
        personality="Холодная, собранная",
        scenario="Руины древней крепости",
        first_mes="*Стоит на страже у разбитых ворот.*",
        visual_dna="silver hair, pale skin"
    )
    assert "Тестовый Персонаж" in card.name
    assert "<lora:silver_hair_v1:0.75>" in card.description
    assert card.visual_dna == "silver hair, pale skin"

    # 2. Моделирование импорта Chub / TavernCard через словарь без записи на диск
    raw_card_data = {
        "name": "Импортированная Героиня",
        "description": "Эльфийка-следопыт. <lora:elven_scout:0.8>",
        "personality": "Осторожная, внимательная",
        "scenario": "Заброшенный лес",
        "first_mes": "*Следит из листвы.*",
        "data": {
            "visual_dna": "pointed ears, green eyes, hooded cloak"
        }
    }
    imported_card = TavernParser.parse_dict(raw_card_data)
    assert imported_card.name == "Импортированная Героиня"
    assert "<lora:elven_scout:0.8>" in imported_card.description
    assert "pointed ears" in imported_card.visual_dna

    # 3. Fallback: извлечение физического ДНК через регулярные выражения
    regex_card_data = {
        "name": "Герой с тегом внешности",
        "description": "[Appearance: golden eyes, braided hair] <lora:gold_v1:0.6>",
        "personality": "Спокойный",
        "scenario": "Цитадель",
        "first_mes": "*Приветствие*"
    }
    regex_card = TavernParser.parse_dict(regex_card_data)
    assert "golden eyes" in regex_card.visual_dna
    assert "<lora:gold_v1:0.6>" in regex_card.description


def test_session_ui_overrides_and_strict_isolation(isolate_session_storage):
    """Сценарий 2: Кастомный якорь и системный промпт не текут между сессиями."""
    base_card = CharacterCard(
        name="Общий Герой",
        description="Базовое описание",
        personality="Базовый характер",
        scenario="Базовый сценарий",
        first_mes="*Приветствие*"
    )

    sess_a_id = "session_A_isolated"
    sess_b_id = "session_B_isolated"

    history_a = [{"role": "assistant", "content": base_card.first_mes}, {"role": "user", "content": "Действие в А"}]
    history_b = [{"role": "assistant", "content": base_card.first_mes}, {"role": "user", "content": "Действие в Б"}]

    custom_prompt_a = "# Специфичный системный промпт для сессии А"
    custom_anchor_a = "[Кинематографичный кастомный стиль для А]"

    # Сборка пакета для Сессии А (с переопределениями)
    messages_a = SessionManager.get_lm_studio_messages(
        history=history_a,
        is_roleplay=True,
        character_card=base_card,
        user_name="Игрок",
        session_id=sess_a_id,
        custom_system_prompt=custom_prompt_a
    )
    anchor_a = RoleplayPromptBuilder.build_depth_anchor(
        base_card,
        user_name="Игрок",
        user_query=history_a[-1]["content"],
        custom_anchor=custom_anchor_a
    )
    for idx in reversed(range(len(messages_a))):
        if messages_a[idx].get("role") == "user":
            messages_a.insert(idx, {"role": "system", "content": anchor_a.strip()})
            break

    # Сборка пакета для Сессии Б (чистая, без переопределений)
    messages_b = SessionManager.get_lm_studio_messages(
        history=history_b,
        is_roleplay=True,
        character_card=base_card,
        user_name="Игрок",
        session_id=sess_b_id
    )
    anchor_b = RoleplayPromptBuilder.build_depth_anchor(
        base_card,
        user_name="Игрок",
        user_query=history_b[-1]["content"],
        custom_anchor=None
    )
    for idx in reversed(range(len(messages_b))):
        if messages_b[idx].get("role") == "user":
            messages_b.insert(idx, {"role": "system", "content": anchor_b.strip()})
            break

    # Проверки сессии А
    assert custom_prompt_a in messages_a[0]["content"]
    assert messages_a[-2]["role"] == "system"
    assert custom_anchor_a in messages_a[-2]["content"]

    # Проверки изоляции сессии Б
    assert custom_prompt_a not in messages_b[0]["content"]
    assert custom_anchor_a not in messages_b[-2]["content"]
    assert base_card.description == "Базовое описание"


def test_scene_art_generation_offline_dispatch(monkeypatch):
    """Сценарий 3: Проверка кнопки 'Арт сцены' без вызова модели."""
    from services.rp_cast_manager import RPCastManager
    from services.roleplay_flow_controller import RoleplayFlowControllerMixin

    mock_cast_data = {
        "environment": "dark torchlit dungeon, stone walls",
        "scene_type": "cinematic_dialogue",
        "visual_dna": "amber eyes, tattered tunic",
        "active_loras": ["<lora:dungeon_lighting:0.8>"]
    }
    monkeypatch.setattr(RPCastManager, "load_cast", MagicMock(return_value=mock_cast_data))

    last_assistant_msg = "*Кай держит кинжал обеими руками, уставившись во тьму коридора.*"

    # Имитация сборщика арта
    art_task_spy = MagicMock()

    # Эмуляция формирования промпта на основе текущего стейта
    cast = RPCastManager.load_cast("test_sess_art")
    assert cast["environment"] == "dark torchlit dungeon, stone walls"
    assert "<lora:dungeon_lighting:0.8>" in cast["active_loras"]
    assert "кинжал" in last_assistant_msg

    art_task = InferenceTask(
        name="Генерация арта сцены",
        task_type="sd_image",
        priority=10,
        func=art_task_spy,
        kwargs={
            "scene_text": last_assistant_msg,
            "environment": cast["environment"],
            "loras": cast["active_loras"]
        }
    )

    assert art_task.name == "Генерация арта сцены"
    assert art_task.kwargs["environment"] == "dark torchlit dungeon, stone walls"
    assert "<lora:dungeon_lighting:0.8>" in art_task.kwargs["loras"]

    # Проверка вызова без сетевого инференса
    art_task.func(**art_task.kwargs)
    art_task_spy.assert_called_once_with(
        scene_text=last_assistant_msg,
        environment="dark torchlit dungeon, stone walls",
        loras=["<lora:dungeon_lighting:0.8>"]
    )

    # Верификация контроллера генерации _generate_ephemeral_rp_art_prompt
    controller = RoleplayFlowControllerMixin.__new__(RoleplayFlowControllerMixin)
    controller.lm_client = MagicMock()
    controller.lm_client.chat_completion.return_value = "[IMAGE_PROMPT: 1boy, amber eyes, holding dagger, dark torchlit dungeon, stone walls <lora:dungeon_lighting:0.8>]"
    controller.active_character = CharacterCard(
        name="Кай",
        description="Разведчик",
        visual_dna="amber eyes, tattered tunic"
    )

    structured_cast = {
        "scene_type": "action",
        "environment": {"location_dna": "dark torchlit dungeon, stone walls"},
        "companion_state": {"visual_dna": "amber eyes, tattered tunic"},
        "inventory": ["кинжал"]
    }
    monkeypatch.setattr(RPCastManager, "load_cast", MagicMock(return_value=structured_cast))

    prompt = controller._generate_ephemeral_rp_art_prompt("test_sess_art", last_assistant_msg)
    assert controller.lm_client.chat_completion.called
    call_kwargs = controller.lm_client.chat_completion.call_args[1]
    called_messages = call_kwargs["messages"]
    assert len(called_messages) >= 2
    user_payload = called_messages[1]["content"]
    assert "dark torchlit dungeon, stone walls" in user_payload
    assert "кинжал" in user_payload
    assert "amber eyes, tattered tunic" in user_payload
    assert "1boy" in prompt or "amber eyes" in prompt
