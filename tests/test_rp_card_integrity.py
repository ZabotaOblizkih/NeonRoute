import json
import uuid
import io
import pytest
from PIL import Image
from pathlib import Path
from unittest.mock import MagicMock, patch

from config import WORKSPACE_DIR
from services.chub_client import ChubClient
from services.macro_engine import MacroEngine
from services.tavern_parser import CharacterCard, TavernParser
from services.session_manager import SessionManager
from services.rp_cast_manager import RPCastManager
from services.rp_cast_extractor import build_cast_extraction_spec


def test_chub_client_lossless_download():
    """Проверяет, что ChubClient не теряет description и personality при download_card."""
    client = ChubClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_data = {
        "node": {
            "name": "Evelyn",
            "fullPath": "creator/evelyn",
            "tagline": "A brilliant cyber-detective",
            "description": "Short bio from site",
            "topics": ["cyberpunk", "detective"],
            "definition": {
                "description": "Physical traits: tall, silver hair, cyber-eye.",
                "personality": "Cold, calculating, loyal to justice.",
                "tavern_personality": "Speaks calmly and methodically.",
                "first_message": "Good evening, detective.",
                "example_dialogs": "<START>\nUser: Hi\nChar: Hello",
                "scenario": "Neon city rain",
                "depth_prompt": "Always remain analytical."
            }
        }
    }
    mock_resp.text = json.dumps(mock_data)
    mock_resp.json = MagicMock(return_value=mock_data)

    img = Image.new("RGB", (10, 10), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    avatar_bytes = buf.getvalue()

    chars_dir = WORKSPACE_DIR / "temp_test_chars"
    chars_dir.mkdir(parents=True, exist_ok=True)
    try:
        with patch.object(client.session, "get") as mock_get, \
             patch("services.chub_client.CHARACTERS_DIR", chars_dir):
            mock_avatar = MagicMock()
            mock_avatar.status_code = 200
            mock_avatar.content = avatar_bytes
            mock_get.side_effect = [mock_resp, mock_avatar, mock_avatar]

            saved_path = client.download_card("creator/evelyn", "Evelyn")
            assert saved_path is not None
            assert saved_path.exists()

            with open(saved_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            card_data = data.get("data", {})
            assert "Physical traits: tall, silver hair, cyber-eye." in card_data["description"]
            assert "Cold, calculating, loyal to justice." in card_data["personality"]
            assert "Speaks calmly and methodically." in card_data["personality"]
            assert card_data["creator_notes"] == "A brilliant cyber-detective"
            assert card_data["depth_prompt"] == "Always remain analytical."
    finally:
        import shutil
        shutil.rmtree(chars_dir, ignore_errors=True)


def test_macro_engine_replaces_start_marker():
    """Проверяет, что MacroEngine заменяет <START> на разделитель сцен, а не склеивает примеры."""
    raw = "<START>\n{{user}}: What is this place?\n{{char}}: This is Sector 7.\n\n<START>\n{{user}}: Can we leave?\n{{char}}: Not yet."
    processed = MacroEngine.process(raw, user_name="Alex", char_name="Elaria")

    assert "<START>" not in processed
    assert "<start>" not in processed.lower()
    assert "--- [Пример сцены] ---" in processed
    assert not processed.startswith("\n")
    # Verify that the two scenes are distinct and not glued together
    scenes = processed.split("--- [Пример сцены] ---")
    assert len(scenes) == 3  # leading separator splits into empty/prefix and 2 scenes
    assert "Alex: What is this place?" in scenes[1]
    assert "Alex: Can we leave?" in scenes[2]


def test_tavern_parser_preserves_rpg_blocks_and_depth_prompt():
    """Проверяет, что TavernParser сохраняет extra_rpg_blocks даже при наличии scenario_ru, а также извлекает depth_prompt."""
    card_dict = {
        "name": "GameMaster",
        "description": "World keeper",
        "personality": "Strict and fair",
        "scenario": "Fantasy dungeon",
        "scenario_ru": "Фэнтезийное подземелье",
        "depth_prompt": "Always describe dice consequences.",
        "character_creation": {"races": ["Human", "Elf"], "classes": ["Warrior", "Mage"]},
        "rules": {"death": "permadeath", "magic": "limited"}
    }

    card = TavernParser.parse_dict(card_dict)
    assert card.depth_prompt == "Always describe dice consequences."
    assert "CHARACTER_CREATION" in card.rpg_mechanics_block
    assert "RULES" in card.rpg_mechanics_block

    sys_prompt = card.get_system_prompt(user_name="Player")
    # Verify that Russian scenario AND mechanics block are BOTH included!
    assert "Фэнтезийное подземелье" in sys_prompt
    assert "CHARACTER_CREATION" in sys_prompt
    assert "RULES" in sys_prompt
    assert "Ключевая заметка автора (Character Note):" in sys_prompt
    assert "Always describe dice consequences." in sys_prompt

    # Verify serialization roundtrip
    saved_dict = card.to_dict()
    assert saved_dict["depth_prompt"] == "Always describe dice consequences."
    assert "CHARACTER_CREATION" in saved_dict["rpg_mechanics_block"]

    restored = CharacterCard.from_dict(saved_dict)
    assert restored.depth_prompt == "Always describe dice consequences."
    assert "CHARACTER_CREATION" in restored.rpg_mechanics_block


def test_narrative_pov_detection():
    """Проверяет точное определение 1-го vs 3-го лица в CharacterCard."""
    # First-person card
    card_first = CharacterCard(
        name="Elena",
        first_mes="*Я тихо вошла в комнату, сняла плащ и посмотрела на тебя.* Привет. Я так долго тебя ждала."
    )
    assert card_first.narrative_pov == "first"

    # Third-person card
    card_third = CharacterCard(
        name="Elena",
        first_mes="*Елена тихо вошла в комнату, сняла свой тяжелый плащ и взглянула на гостя.* Привет. Она долго ждала этой встречи."
    )
    assert card_third.narrative_pov == "third"


def test_session_manager_adaptive_pov_and_stats():
    """
    Проверяет, что SessionManager.get_lm_studio_messages() в РП-режиме:
    1) Не навязывает «от первого лица» карточкам от 3-го лица.
    2) Корректно включает stats из RPCastManager.
    3) Включает depth_prompt в combined_injection.
    """
    card = CharacterCard(
        name="Каэли",
        description="Эльфийская лучница",
        first_mes="*Каэли бесшумно скользнула между деревьями, держа лук наготове.* Стой, кто идет?",
        depth_prompt="Никогда не доверяй незнакомцам сразу."
    )
    assert card.narrative_pov == "third"

    hist_dir = WORKSPACE_DIR / "temp_test_history"
    hist_dir.mkdir(parents=True, exist_ok=True)
    try:
        sess_id = f"test_rp_{uuid.uuid4().hex[:6]}"
        chat_file = hist_dir / f"chat_{sess_id}.json"
        chat_data = {
            "id": sess_id,
            "mode": "roleplay",
            "character_card": card.to_dict(),
            "messages": [
                {"role": "user", "content": "Привет, Каэли!"}
            ]
        }
        chat_file.write_text(json.dumps(chat_data, ensure_ascii=False), encoding="utf-8")

        # Set up cast with stats
        cast_data = {
            "session_id": sess_id,
            "entities": {},
            "environment": {"current_location": "Лес"},
            "inventory": ["Кинжал", "Стрелы"],
            "stats": {"HP": "100/100", "Симпатия": "25/100", "Стресс": "0%"}
        }
        cast_file = hist_dir / f"cast_{sess_id}.json"
        cast_file.write_text(json.dumps(cast_data, ensure_ascii=False), encoding="utf-8")

        with patch("services.session_manager.HISTORY_DIR", hist_dir), \
             patch("services.rp_cast_manager.HISTORY_DIR", hist_dir):
            history = chat_data["messages"]
            messages = SessionManager.get_lm_studio_messages(
                history=history,
                is_roleplay=True,
                character_card=card,
                session_id=sess_id,
                user_name="Игрок"
            )

            user_content = messages[-1]["content"]

            # 1. No hardcoded 'от первого лица'
            assert "от первого лица" not in user_content
            # 2. Adaptive directive is present
            assert "Держи канонический характер, стиль речи и манеру повествования" in user_content
            assert "Каэли" in user_content

            # 3. Stats from RPCastManager are included
            assert "Текущие параметры и шкалы (<Stats>):" in user_content
            assert "HP: 100/100" in user_content
            assert "Симпатия: 25/100" in user_content

            # 4. depth_prompt is included in injection
            assert "Никогда не доверяй незнакомцам сразу." in user_content
    finally:
        import shutil
        shutil.rmtree(hist_dir, ignore_errors=True)


def test_structured_output_json_schema_stability():
    """Проверяет, что schema_payload не содержит None в enum и совместим с GBNF LM Studio."""
    prompt, spec = build_cast_extraction_spec(has_companion=True, has_stats=True)
    schema = spec["json_schema"]["schema"]
    npc_props = schema["properties"]["npc"]["properties"]

    assert None not in npc_props["presence"]["enum"]
    assert "none" in npc_props["presence"]["enum"]
    assert None not in npc_props["vital_status"]["enum"]
    assert "none" in npc_props["vital_status"]["enum"]
