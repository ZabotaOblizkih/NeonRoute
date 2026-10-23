"""
Unit tests for Active Cast and Inventory Injection into Roleplay System Prompt (Anchor 1).
Verifies:
1. RPCastManager.format_cast_data_block and get_cast_prompt_block.
2. RoleplayPromptBuilder.build injecting [Current Active Companions, Status & Inventory].
3. SessionManager.get_lm_studio_messages integrating cast & inventory into Anchor 1 (messages[0]).
4. Eviction resistance: Anchor 1 retains cast and inventory under context overflow.
5. Isolation: Assistant and Engineer modes do not inject cast blocks.
"""

import pytest
from unittest.mock import patch

from services.rp_cast_manager import RPCastManager
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.session_manager import SessionManager
from services.tavern_parser import CharacterCard


class TestRPCastFormatting:
    """Проверка форматирования структуры cast_data в RPCastManager."""

    def test_format_empty_or_none_returns_empty(self):
        assert RPCastManager.format_cast_data_block(None) == ""
        assert RPCastManager.format_cast_data_block({}) == ""

    def test_format_full_cast_data(self):
        cast_data = {
            "current_location": "Катакомбы цитадели",
            "environment": {
                "scene_ambience": "тусклый свет факелов, сырость",
                "is_enclosed": True
            },
            "active_quest": "Найти серебряный ключ",
            "inventory": ["факел", "зелье лечения", "древний свиток"],
            "stats": {"HP": "85/100", "Мана": "50"},
            "player_state": {
                "vital_status": "alive",
                "mutations": ["порез на левом плече"]
            },
            "entities": {
                "aelita": {
                    "role": "companion",
                    "canonical_name": "Аэлита",
                    "vital_status": "alive",
                    "facts": ["Маг огня"],
                    "companion_state": {
                        "current_outfit": "походная мантия",
                        "mutations": ["ожог на кисти"]
                    }
                },
                "guardian": {
                    "role": "npc",
                    "canonical_name": "Страж склепа",
                    "vital_status": "alive",
                    "presence": "physical",
                    "ttl": 3,
                    "facts": ["Держит алебарду наготове"]
                }
            }
        }

        block = RPCastManager.format_cast_data_block(cast_data)

        assert "Локация: Катакомбы цитадели (в укрытии/помещении), Атмосфера: тусклый свет факелов, сырость" in block
        assert "Текущая цель: Найти серебряный ключ" in block
        assert "Спутники в отряде:" in block
        assert "• Аэлита (Наряд: походная мантия, Следы/раны: ожог на кисти, Маг огня)" in block
        assert "Присутствующие персонажи (NPC):" in block
        assert "• Страж склепа (Держит алебарду наготове)" in block
        assert "Состояние игрока: Следы/раны: порез на левом плече" in block
        assert "Инвентарь: факел, зелье лечения, древний свиток" in block
        assert "Параметры/Шкалы: HP: 85/100, Мана: 50" in block

    def test_get_cast_prompt_block_loads_from_manager(self):
        session_id = "test_cast_session_123"
        data = {
            "current_location": "Таверна «Пьяный дракон»",
            "inventory": ["кошель с монетами", "кинжал"],
            "entities": {
                "mirana": {
                    "role": "companion",
                    "canonical_name": "Мирана",
                    "vital_status": "alive",
                    "facts": ["Следопыт"]
                }
            }
        }

        with patch.object(RPCastManager, "load_cast", return_value=data):
            block = RPCastManager.get_cast_prompt_block(session_id)
            assert "Таверна «Пьяный дракон»" in block
            assert "кошель с монетами, кинжал" in block
            assert "Мирана" in block


class TestRoleplayPromptBuilderInjection:
    """Проверка инъекции активного каста в RoleplayPromptBuilder.build."""

    def test_build_without_active_cast(self):
        card = CharacterCard(name="Селеста", description="Добрая волшебница")
        prompt = RoleplayPromptBuilder.build(
            character_card=card,
            user_name="Иван",
            active_cast_block=""
        )
        assert "[Current Active Companions, Status & Inventory]:" not in prompt

    def test_build_with_active_cast_block(self):
        card = CharacterCard(name="Селеста", description="Добрая волшебница")
        active_cast = (
            "Локация: Древний лес\n"
            "Инвентарь: магический посох, карта"
        )
        prompt = RoleplayPromptBuilder.build(
            character_card=card,
            user_name="Иван",
            active_cast_block=active_cast
        )

        assert "[Current Active Companions, Status & Inventory]:" in prompt
        assert "Локация: Древний лес" in prompt
        assert "Инвентарь: магический посох, карта" in prompt

        # Блок должен располагаться ближе к концу промпта (после базового описания)
        desc_pos = prompt.find("Добрая волшебница")
        cast_pos = prompt.find("[Current Active Companions, Status & Inventory]:")
        assert desc_pos != -1
        assert cast_pos != -1
        assert cast_pos > desc_pos


class TestSessionManagerCastIntegration:
    """Проверка связки SessionManager и RPCastManager в Якоре 1."""

    def test_get_lm_studio_messages_injects_cast_into_system_prompt(self):
        session_id = "test_rp_integration_456"
        cast_data = {
            "current_location": "Крыша небоскреба",
            "inventory": ["плазменный резак", "декодер"],
            "entities": {
                "v": {
                    "role": "companion",
                    "canonical_name": "Ви",
                    "vital_status": "alive",
                    "facts": ["Напарница"]
                }
            }
        }

        with patch.object(RPCastManager, "load_cast", return_value=cast_data):
            card = CharacterCard(name="Ви", first_mes="Привет, партнер!")
            history = [
                {"role": "assistant", "content": "Привет, партнер!"},
                {"role": "user", "content": "Что у нас есть из снаряжения?"}
            ]

            messages = SessionManager.get_lm_studio_messages(
                history=history,
                is_roleplay=True,
                character_card=card,
                user_name="Игрок",
                session_id=session_id
            )

            # Формат возвращаемого значения: List[Dict[str, Any]]
            assert isinstance(messages, list)
            assert len(messages) >= 3

            # Якорь 1 (messages[0] = system) обязан содержать каст и инвентарь
            system_msg = messages[0]
            assert system_msg["role"] == "system"
            sys_content = system_msg["content"]
            assert "[Current Active Companions, Status & Inventory]:" in sys_content
            assert "Крыша небоскреба" in sys_content
            assert "плазменный резак, декодер" in sys_content
            assert "Ви" in sys_content

    def test_cast_block_never_evicted_under_token_pressure(self):
        """Проверяет, что блок каста в Якоре 1 никогда не вытесняется даже при переполнении бюджета токенов."""
        session_id = "test_eviction_pressure_789"
        cast_data = {
            "current_location": "Катакомбы",
            "inventory": ["факел", "святая вода"],
            "entities": {
                "elena": {
                    "role": "companion",
                    "canonical_name": "Елена",
                    "vital_status": "alive"
                }
            }
        }

        with patch.object(RPCastManager, "load_cast", return_value=cast_data):
            card = CharacterCard(name="Елена", first_mes="Начало пути.")
            # 40 длинных сообщений
            history = [{"role": "assistant", "content": "Начало пути."}]
            for i in range(1, 40):
                role = "user" if i % 2 == 1 else "assistant"
                history.append({"role": role, "content": f"Реплика #{i}: " + "слово " * 30})

            # Ограничиваем бюджет токенов, вызывая вытеснение середины
            messages = SessionManager.get_lm_studio_messages(
                history=history,
                is_roleplay=True,
                character_card=card,
                user_name="Игрок",
                session_id=session_id,
                max_context_tokens=1800
            )

            system_msg = messages[0]
            assert system_msg["role"] == "system"
            assert "[Current Active Companions, Status & Inventory]:" in system_msg["content"]
            assert "факел, святая вода" in system_msg["content"]
            assert "Елена" in system_msg["content"]

    def test_isolation_assistant_and_engineer_modes(self):
        """Проверяет, что для режимов assistant и engineer инъекция каста не применяется."""
        session_id = "test_isolation_000"
        cast_data = {
            "inventory": ["секретный чертеж"],
            "current_location": "Бункер"
        }

        with patch.object(RPCastManager, "load_cast", return_value=cast_data):
            history = [{"role": "user", "content": "Помоги решить задачу."}]

            # Режим assistant
            assistant_msgs = SessionManager.get_lm_studio_messages(
                history=history,
                is_roleplay=False,
                is_engineer=False,
                session_id=session_id
            )
            assert "[Current Active Companions, Status & Inventory]:" not in assistant_msgs[0]["content"]
            assert "секретный чертеж" not in assistant_msgs[0]["content"]

            # Режим engineer
            engineer_msgs = SessionManager.get_lm_studio_messages(
                history=history,
                is_roleplay=False,
                is_engineer=True,
                session_id=session_id
            )
            assert "[Current Active Companions, Status & Inventory]:" not in engineer_msgs[0]["content"]
            assert "секретный чертеж" not in engineer_msgs[0]["content"]
