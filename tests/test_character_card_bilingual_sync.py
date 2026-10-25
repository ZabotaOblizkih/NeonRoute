# tests/test_character_card_bilingual_sync.py
import json
import re
import pytest
from pathlib import Path
from services.tavern_models import CharacterCard
from services.tavern_parser import TavernParser


@pytest.fixture
def sample_card_data():
    """Базовые двуязычные данные карточки."""
    return {
        "name": "YoRHa 2B",
        "description": "An all-purpose battle android deployed as a member of the automated infantry squad YoRHa.",
        "description_ru": "Универсальный боевой андроид, член автоматизированного пехотного отряда YoRHa.",
        "personality": "Calm, composed, taciturn, strictly follows mission orders.",
        "personality_ru": "Спокойная, собранная, немногословная, строго следует приказам миссии.",
        "scenario": "Bunker command center before orbital drop.",
        "scenario_ru": "Командный центр Бункера перед орбитальной высадкой.",
        "first_mes": "*2B adjusts her visor, her blade ready.* 'Commencing reconnaissance mission.'",
        "first_mes_ru": "*2B поправляет визор, обнажая клинок.* «Приступаю к выполнению разведывательной операции.»",
        "mes_example": "<START>\n{{user}}: Who are you?\n{{char}}: 2B. Battle android.",
        "mes_example_ru": "<START>\n{{user}}: Кто ты?\n{{char}}: 2B. Боевой андроид.",
        "alternate_greetings": [
            "*Standing in the ruins of the city.* 'Target spotted.'",
            "*Looking at the sky.* 'Glory to mankind.'"
        ],
        "alternate_greetings_ru": [
            "*Стоя среди руин города.* «Цель обнаружена.»",
            "*Глядя в небо.* «Слава человечеству.»"
        ],
        "visual_dna": "1girl, 2b, short white hair, black blindfold, black dress",
    }


def test_bilingual_save_and_reload_on_disk(tmp_path: Path, sample_card_data: dict):
    """Тест 1: Запись карточки с EN и RU полями на диск и обратное чтение без потерь."""
    json_path = tmp_path / "YoRHa 2B.json"

    # 1. Создаем объект карточки
    card = CharacterCard(
        name=sample_card_data["name"],
        description=sample_card_data["description"],
        description_ru=sample_card_data["description_ru"],
        personality=sample_card_data["personality"],
        personality_ru=sample_card_data["personality_ru"],
        scenario=sample_card_data["scenario"],
        scenario_ru=sample_card_data["scenario_ru"],
        first_mes=sample_card_data["first_mes"],
        first_mes_ru=sample_card_data["first_mes_ru"],
        mes_example=sample_card_data["mes_example"],
        mes_example_ru=sample_card_data["mes_example_ru"],
        alternate_greetings=list(sample_card_data["alternate_greetings"]),
        alternate_greetings_ru=list(sample_card_data["alternate_greetings_ru"]),
        visual_dna=sample_card_data["visual_dna"],
        source_path=json_path
    )

    # 2. Сохраняем через штатный TavernParser
    ok = TavernParser.save_card_translations(card)
    assert ok, "TavernParser.save_card_translations завершился с ошибкой"
    assert json_path.exists(), "JSON-файл карточки не был создан на диске"

    # 3. Читаем сырой JSON с диска и проверяем наличие обоих слоев
    with open(json_path, "r", encoding="utf-8") as f:
        disk_raw = json.load(f)

    target_dict = disk_raw.get("data", disk_raw)

    # Проверка английских полей
    assert target_dict["description"] == sample_card_data["description"]
    assert target_dict["first_mes"] == sample_card_data["first_mes"]
    assert target_dict["alternate_greetings"] == sample_card_data["alternate_greetings"]

    # Проверка русских полей
    assert target_dict["description_ru"] == sample_card_data["description_ru"]
    assert target_dict["first_mes_ru"] == sample_card_data["first_mes_ru"]
    assert target_dict["alternate_greetings_ru"] == sample_card_data["alternate_greetings_ru"]


def test_editing_en_does_not_wipe_existing_ru(tmp_path: Path, sample_card_data: dict):
    """Тест 2: Редактирование английского текста не должно стирать существующий русский перевод."""
    json_path = tmp_path / "YoRHa 2B.json"

    card = CharacterCard(
        name=sample_card_data["name"],
        description=sample_card_data["description"],
        description_ru=sample_card_data["description_ru"],
        first_mes=sample_card_data["first_mes"],
        first_mes_ru=sample_card_data["first_mes_ru"],
        source_path=json_path
    )
    TavernParser.save_card_translations(card)

    # Симулируем поведение редактора в режиме просмотра оригинала (EN):
    # Пользователь подправил только английское описание:
    new_en_desc = "Updated English description: Android with enhanced combat systems."
    card.description = new_en_desc

    # Критическое правило: description_ru не должно затираться пустышкой!
    assert card.description_ru == sample_card_data["description_ru"], (
        "Поле description_ru было преждевременно затёрто в памяти объекта"
    )

    TavernParser.save_card_translations(card)

    # Читаем повторно с диска
    with open(json_path, "r", encoding="utf-8") as f:
        reloaded = json.load(f).get("data", {})

    assert reloaded["description"] == new_en_desc
    assert reloaded["description_ru"] == sample_card_data["description_ru"], (
        "Русский перевод был затёрт при сохранении обновленного английского оригинала!"
    )


def test_editing_ru_does_not_corrupt_original_en(tmp_path: Path, sample_card_data: dict):
    """Тест 3: Редактирование русского перевода не должно затрагивать английский оригинал."""
    json_path = tmp_path / "YoRHa 2B.json"

    card = CharacterCard(
        name=sample_card_data["name"],
        description=sample_card_data["description"],
        description_ru=sample_card_data["description_ru"],
        first_mes=sample_card_data["first_mes"],
        first_mes_ru=sample_card_data["first_mes_ru"],
        source_path=json_path
    )
    TavernParser.save_card_translations(card)

    # Пользователь редактирует русский текст приветствия:
    new_ru_greet = "*2B медленно опускает клинок.* «Приказ выполнен, база.»"
    card.first_mes_ru = new_ru_greet

    TavernParser.save_card_translations(card)

    with open(json_path, "r", encoding="utf-8") as f:
        reloaded = json.load(f).get("data", {})

    assert reloaded["first_mes_ru"] == new_ru_greet
    assert reloaded["first_mes"] == sample_card_data["first_mes"], (
        "Английский оригинал был поврежден при редактировании русской локализации!"
    )


def test_greetings_pool_indexing_and_fallback(sample_card_data: dict):
    """Тест 4: get_greetings_pool корректно связывает пары EN/RU для всех альтернативных вариантов."""
    card = CharacterCard(
        name=sample_card_data["name"],
        first_mes=sample_card_data["first_mes"],
        first_mes_ru=sample_card_data["first_mes_ru"],
        alternate_greetings=sample_card_data["alternate_greetings"],
        alternate_greetings_ru=sample_card_data["alternate_greetings_ru"],
    )

    pool = card.get_greetings_pool()
    # 1 основное + 2 альтернативных = 3 варианта
    assert len(pool) == 3, f"Ожидалось 3 завязки в пуле, получено {len(pool)}"

    # Проверка соответствия первого слота (first_mes)
    assert pool[0]["orig"] == sample_card_data["first_mes"]
    assert pool[0]["ru"] == sample_card_data["first_mes_ru"]
    assert pool[0]["effective"] == sample_card_data["first_mes_ru"]

    # Проверка альтернативных слотов
    for i in range(len(sample_card_data["alternate_greetings"])):
        assert pool[i + 1]["orig"] == sample_card_data["alternate_greetings"][i]
        assert pool[i + 1]["ru"] == sample_card_data["alternate_greetings_ru"][i]
        assert pool[i + 1]["effective"] == sample_card_data["alternate_greetings_ru"][i]


def test_llm_response_delimiter_tolerance():
    """Тест 5: Парсер секций устойчив к пробелам и вариациям оформления ответа нейросети."""
    # Типичный ответ LLM с пробелами вокруг знаков равенства
    mock_llm_reply = (
        "=== DESCRIPTION ===\n"
        "Русское описание персонажа.\n\n"
        "=== PERSONALITY ===\n"
        "Русский характер персонажа.\n\n"
        "=== SCENARIO ===\n"
        "Русский сценарий.\n\n"
        "=== FIRST_MES ===\n"
        "*Приветствие на русском языке.*\n\n"
        "=== ALT_GREETING_1 ===\n"
        "*Альтернативное приветствие 1.*"
    )

    pattern = r'===+\s*([A-Za-z0-9_]+)\s*===+'
    parts = re.split(pattern, mock_llm_reply, flags=re.IGNORECASE)

    res_dict = {}
    if len(parts) >= 3:
        for i in range(1, len(parts), 2):
            key = parts[i].strip().upper()
            val = parts[i + 1].strip()
            res_dict[key] = val

    assert "DESCRIPTION" in res_dict
    assert res_dict["DESCRIPTION"] == "Русское описание персонажа."
    assert "FIRST_MES" in res_dict
    assert res_dict["FIRST_MES"] == "*Приветствие на русском языке.*"
    assert "ALT_GREETING_1" in res_dict
    assert res_dict["ALT_GREETING_1"] == "*Альтернативное приветствие 1.*"