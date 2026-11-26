"""
Тесты для TavernParser и CharacterCard (проверка логики парсинга карточек, 
санации переводов, архетипов и извлечения DNA).
"""

import pytest
from services.tavern_parser import TavernParser, CharacterCard, LoreEntry


class TestLoreEntry:
    """Проверка структуры и методов статей лорбука."""

    def test_from_dict_and_properties(self):
        data = {
            "keys": "magic, spell, mana",
            "text": "Магия требует маны.",
            "name": "Правило магии",
            "enabled": True,
            "constant": True
        }
        entry = LoreEntry.from_dict(data)

        assert entry.keys == ["magic", "spell", "mana"]
        assert entry.key == ["magic", "spell", "mana"]
        assert entry.content == "Магия требует маны."
        assert entry.text == "Магия требует маны."
        assert entry.comment == "Правило магии"
        assert entry.name == "Правило магии"
        assert entry.enabled is True
        assert entry.constant is True

    def test_dict_like_access(self):
        entry = LoreEntry(keys=["sword"], content="Острый клинок", comment="Оружие")
        assert entry["content"] == "Острый клинок"
        assert "keys" in entry
        assert entry.get("comment") == "Оружие"
        assert entry.get("non_existing", "default") == "default"


class TestCharacterCardSanitization:
    """Проверка очистки некорректных _ru переводов при инициализации карточки."""

    def test_sanitizes_identical_english_translation(self):
        # Если в _ru поле скопирован английский оригинал — поле должно обнулиться
        card = CharacterCard(
            name="Hero",
            description="A brave warrior from the north.",
            description_ru="A brave warrior from the north."
        )
        assert card.description_ru == ""

    def test_sanitizes_translation_without_cyrillic(self):
        # Если в _ru поле нет хотя бы 3 кириллических букв — оно сбрасывается
        card = CharacterCard(
            name="Hero",
            personality="Kind and calm",
            personality_ru="Still in English"
        )
        assert card.personality_ru == ""

    def test_preserves_valid_russian_translation(self):
        card = CharacterCard(
            name="Hero",
            description="A brave warrior.",
            description_ru="Храбрый северный воин."
        )
        assert card.description_ru == "Храбрый северный воин."


class TestCharacterCardHeuristics:
    """Проверка определения пола, стиля повествования и пула приветствий."""

    def test_is_male_by_tags(self):
        card_male = CharacterCard(tags=["warrior", "man"])
        assert card_male.is_male is True

        card_female = CharacterCard(tags=["mage", "waifu"])
        assert card_female.is_male is False

    def test_is_male_by_text_pronouns(self):
        card = CharacterCard(
            description="He is a lone traveler. His sword is sharp, and he fights bravely."
        )
        assert card.is_male is True

    def test_narrative_pov_first_person(self):
        card = CharacterCard(
            first_mes="*Я медленно открываю дверь и осматриваюсь вокруг. Мое сердце бьется чаще.*"
        )
        assert card.narrative_pov == "first"

    def test_narrative_pov_third_person(self):
        card = CharacterCard(
            name="Алиса",
            first_mes="*Алиса подошла к столу и посмотрела на собеседника спокойным взглядом.*"
        )
        assert card.narrative_pov == "third"

    def test_get_greetings_pool_indexing(self):
        card = CharacterCard(
            first_mes="First message",
            first_mes_ru="Первое сообщение",
            alternate_greetings=["Alt 1", "Alt 2"],
            alternate_greetings_ru=["Альт 1", ""]
        )
        pool = card.get_greetings_pool()

        assert len(pool) == 3
        # Индекс 0: Основное
        assert pool[0]["index"] == 0
        assert pool[0]["orig"] == "First message"
        assert pool[0]["ru"] == "Первое сообщение"
        assert pool[0]["effective"] == "Первое сообщение"

        # Индекс 1: Альтернатива с переводом
        assert pool[1]["orig"] == "Alt 1"
        assert pool[1]["ru"] == "Альт 1"

        # Индекс 2: Альтернатива без перевода (fallback на оригинал)
        assert pool[2]["orig"] == "Alt 2"
        assert pool[2]["ru"] == ""
        assert pool[2]["effective"] == "Alt 2"


class TestDnaRegexExtraction:
    """Проверка фильтрации тегов внешности и фона с отсечением стоп-слов."""

    def test_extract_physical_dna_clothing_stops(self):
        # Стоп-слова одежды (plate, boots, armor) должны отсеиваться
        text = "[Appearance: silver hair, blue eyes, plate armor, leather boots, tall build]"
        dna = TavernParser.extract_physical_dna_regex(text)

        assert "silver hair" in dna
        assert "blue eyes" in dna
        assert "tall build" in dna
        assert "plate armor" not in dna
        assert "leather boots" not in dna

    def test_extract_scenery_dna_lighting_stops(self):
        # Стоп-слова времени суток и освещения (night, dim, dark) должны отсеиваться
        text = "[Setting: ancient stone dungeon, torchlight, night, dim room]"
        scenery = TavernParser.extract_scenery_dna_regex(text)

        assert "ancient stone dungeon" in scenery
        assert "night" not in scenery
        assert "dim room" not in scenery


class TestTavernParserDict:
    """Проверка парсинга словарей форматов Tavern V1, V2 и автоопределения архетипов."""

    def test_parse_v1_card(self):
        v1_data = {
            "name": "Рейчел",
            "description": "Следопыт из гильдии",
            "personality": "Храбрая",
            "first_mes": "Приветствую, путник.",
            "tags": ["девушка"]
        }
        card = TavernParser.parse_dict(v1_data)

        assert card.name == "Рейчел"
        assert card.description == "Следопыт из гильдии"
        assert card.first_mes == "Приветствую, путник."
        assert card.is_male is False
        assert card.archetype_label == "Solo (Одиночный персонаж)"

    def test_parse_v2_archetypes(self):
        # Проверяем определение ЛитРПГ/Системы
        litrpg_data = {
            "spec": "chara_card_v2",
            "data": {
                "name": "Система Разлома",
                "tags": ["litrpg", "system"],
                "description": "Игровой интерфейс подземелья"
            }
        }
        card_sys = TavernParser.parse_dict(litrpg_data)
        assert card_sys.is_system is True
        assert card_sys.is_environment is True
        assert card_sys.archetype_label == "System (ЛитРПГ/Исекай)"

        # Проверяем определение Дейт-сима
        dating_data = {
            "spec": "chara_card_v2",
            "data": {
                "name": "Школьная романтика",
                "tags": ["dating-sim", "romance"],
                "description": "Интерактивное свидание"
            }
        }
        card_dating = TavernParser.parse_dict(dating_data)
        assert card_dating.is_dating is True
        assert card_dating.archetype_label == "Dating-Sim (Симпатия)"

    def test_parse_standalone_lorebook(self):
        # Лорбук без реплик и личности персонажа
        lorebook_data = {
            "entries": [
                {"keys": ["dragons"], "content": "Драконы вымерли 500 лет назад."}
            ]
        }
        card = TavernParser.parse_dict(lorebook_data)

        assert card.is_lorebook is True
        assert card.is_environment is True
        assert card.archetype_label == "Lorebook (Энциклопедия)"
        assert len(card.lore_entries) == 1
        assert "Модуль правил и энциклопедия мира" in card.description

    def test_parse_group_card(self):
        group_data = {
            "name": "Авантюристы",
            "tags": ["group", "party"],
            "first_mes": "Мы готовы к походу."
        }
        card = TavernParser.parse_dict(group_data)

        assert card.is_group is True
        assert card.archetype_label == "Group (Группа спутников)"

    def test_card_to_dict_serialization(self):
        card = CharacterCard(
            name="Тестовый маг",
            description="Маг огня",
            tags=["magic"]
        )
        serialized = card.to_dict()

        assert isinstance(serialized, dict)
        assert serialized["name"] == "Тестовый маг"
        assert serialized["description"] == "Маг огня"
        assert serialized["tags"] == ["magic"]


class TestUniversalCascadeParser:
    """Тесты многоуровневого каскадного парсинга карточек SillyTavern / Chub."""

    def test_chub_nested_character_definition(self):
        # Chub API format: data -> character -> definition
        chub_data = {
            "data": {
                "character": {
                    "name": "Chub Hero",
                    "definition": {
                        "description": "Chub description",
                        "personality": "Chub personality",
                        "scenario": "Chub scenario",
                        "first_mes": "Chub first message",
                        "mes_example": "<START>\n{{user}}: Hello\n{{char}}: Hey!"
                    }
                }
            }
        }
        card = TavernParser.parse_dict(chub_data)
        assert card.name == "Chub Hero"
        assert card.description == "Chub description"
        assert card.personality == "Chub personality"
        assert card.scenario == "Chub scenario"
        assert card.first_mes == "Chub first message"
        assert card.mes_example == "<START>\n{{user}}: Hello\n{{char}}: Hey!"

    def test_venus_nested_definition(self):
        # Venus format: data -> definition (or top level definition)
        venus_data = {
            "name": "Venus Bot",
            "definition": {
                "description": "Venus description",
                "personality": "Venus personality",
                "world_scenario": "Venus world scenario",
                "greeting": "Venus greeting",
                "example_dialogue": "Venus examples"
            }
        }
        card = TavernParser.parse_dict(venus_data)
        assert card.name == "Venus Bot"
        assert card.description == "Venus description"
        assert card.personality == "Venus personality"
        assert card.scenario == "Venus world scenario"
        assert card.first_mes == "Venus greeting"
        assert card.mes_example == "Venus examples"

    def test_field_aliases_fallback(self):
        # Проверка всех альтернативных названий полей
        alias_data = {
            "char_name": "Alias Hero",
            "char_persona": "Persona & Appearance",
            "situation": "Situation setting",
            "first_message": "Hello from first_message",
            "examples": "Example lines",
            "post_history_instructions": "Remember rules"
        }
        card = TavernParser.parse_dict(alias_data)
        assert card.name == "Alias Hero"
        assert card.personality == "Persona & Appearance"
        assert card.description == "Persona & Appearance"
        assert card.scenario == "Situation setting"
        assert card.first_mes == "Hello from first_message"
        assert card.mes_example == "Example lines"
        assert card.system_prompt == "Remember rules"
        assert card.post_history_instructions == "Remember rules"

    def test_macro_preservation(self):
        # Макросы {{char}}, {{user}}, <START> обязаны сохраняться без изменений
        macro_data = {
            "name": "MacroBot",
            "description": "Hero: {{char}}, Friend: {{user}}.",
            "personality": "Always treats {{user}} with respect.",
            "scenario": "{{user}} and {{char}} meet at taverna.",
            "first_mes": "*{{char}} waves to {{user}}.*",
            "mes_example": "<START>\n{{user}}: Hi!\n{{char}}: Greetings {{user}}!"
        }
        card = TavernParser.parse_dict(macro_data)
        assert "{{char}}" in card.description
        assert "{{user}}" in card.description
        assert "{{user}}" in card.personality
        assert "{{user}}" in card.scenario
        assert "{{char}}" in card.scenario
        assert "{{char}}" in card.first_mes
        assert "{{user}}" in card.first_mes
        assert "<START>" in card.mes_example
        assert "{{user}}" in card.mes_example
        assert "{{char}}" in card.mes_example

    def test_lorebook_preservation(self):
        # Структура character_book / lorebook сохраняется в объекте и парсятся entries
        book_structure = {
            "name": "Fantasy Lore",
            "description": "World lorebook",
            "entries": [
                {
                    "keys": ["magic", "mana"],
                    "content": "Magic uses mana points.",
                    "comment": "Magic Rule",
                    "enabled": True,
                    "constant": True
                }
            ]
        }
        card_data = {
            "name": "Mage",
            "description": "Archmage of the tower",
            "character_book": book_structure
        }
        card = TavernParser.parse_dict(card_data)
        assert card.character_book == book_structure
        assert len(card.lore_entries) == 1
        assert card.lore_entries[0].content == "Magic uses mana points."
        assert card.to_dict()["character_book"] == book_structure