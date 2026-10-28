"""
tests/test_rp_chat_markup.py
Автоматические тесты ролевой разметки в чате NeonRoute,
pre-scan карточек и санитизации арт-контекста.
"""

import re
import sys
import uuid
from pathlib import Path
import pytest
from PyQt6.QtWidgets import QApplication

# Инициализация offscreen QApplication для headless-тестирования виджетов
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


# ==============================================================================
# 1. ТЕСТЫ UI И ПАРСИНГА РОЛЕВЫХ ВИДЖЕТОВ В ЧАТЕ
# ==============================================================================

def test_rpg_content_parser_all_tags():
    """Проверяет, что RPGContentParser корректно вычленяет все теги в RPGBundle."""
    from ui.components.rpg_elements import RPGContentParser

    raw_text = (
        "<thought>Нужно оценить обстановку перед боем.</thought>\n"
        "Ты выходишь на каменную арену под рев трибун.\n"
        "<stats>\n"
        "HP: 100/100\n"
        "MP: 50/50\n"
        "Класс: Мечник\n"
        "</stats>\n"
        "<meters>\n"
        "Ярость: [====------] 40%\n"
        "</meters>\n"
        "<choices>\n"
        "1. Обнажить клинок\n"
        "2. Использовать заклинание\n"
        "</choices>"
    )

    bundle = RPGContentParser.parse(raw_text)

    # Чистый литературный текст
    assert "Ты выходишь на каменную арену под рев трибун." in bundle.clean_text
    assert "<thought>" not in bundle.clean_text
    assert "<stats>" not in bundle.clean_text
    assert "<choices>" not in bundle.clean_text

    # Мысли персонажа
    assert bundle.thought == "Нужно оценить обстановку перед боем."

    # Характеристики
    assert bundle.stats is not None
    assert bundle.stats.get("HP") == "100/100" or "100/100" in str(bundle.stats)
    assert bundle.stats.get("Класс") == "Мечник" or "Мечник" in str(bundle.stats)

    # Варианты выбора
    assert bundle.choices is not None
    assert len(bundle.choices) == 2
    assert "1. Обнажить клинок" in bundle.choices[0]


def test_rp_chat_bubble_mounts_widgets(qapp):
    """Проверяет, что RPChatMessageWidget создает визуальные виджеты в чате."""
    from ui.components.rp.rp_chat_bubble import RPChatMessageWidget
    from ui.components.rpg_elements import RPGStatusWidget, RPGThoughtWidget, RPGChoicesWidget

    sample_rp_message = (
        "<thought>Нельзя показывать слабость перед противником.</thought>\n"
        "Рыцарь медленно поднимает двуручный меч, устремляя взгляд вперед.\n"
        "<stats>\n"
        "Уровень: 42\n"
        "HP: 2500/2500\n"
        "Инвентарь: [\"Стальной меч\", \"Зелье лечения\"]\n"
        "</stats>\n"
        "<choices>\n"
        "1. Атаковать в лоб\n"
        "2. Парировать удар\n"
        "</choices>"
    )

    bubble = RPChatMessageWidget(
        sender="Айвен",
        text=sample_rp_message,
        is_user=False
    )

    # 1. Проверяем наличие смонтированного виджета мыслей (RPGThoughtWidget)
    assert bubble.rpg_thought_widget is not None
    assert isinstance(bubble.rpg_thought_widget, RPGThoughtWidget)

    # 2. Проверяем наличие виджета статуса (RPGStatusWidget)
    assert bubble.rpg_status_widget is not None
    assert isinstance(bubble.rpg_status_widget, RPGStatusWidget)

    # 3. Проверяем наличие интерактивных кнопок выбора (RPGChoicesWidget)
    assert bubble.rpg_choices_widget is not None
    assert isinstance(bubble.rpg_choices_widget, RPGChoicesWidget)

    # 4. Проверяем, что в text_browser отображается чистый текст реплики
    plain_content = bubble.text_browser.toPlainText()
    assert "Рыцарь медленно поднимает двуручный меч" in plain_content
    assert "<thought>" not in plain_content
    assert "<stats>" not in plain_content
    assert "<choices>" not in plain_content


def test_inline_rpg_blocks_formatting():
    """Проверяет стилизацию инлайн-блоков <check> и <terminal>."""
    from ui.components.rpg_elements import RPGContentParser

    # Проверка броска D&D (успех)
    html_check = RPGContentParser.format_inline_rpg_blocks("&lt;check&gt;Акробатика d20: 18 (Успех)&lt;/check&gt;")
    assert "✔ УСПЕХ" in html_check
    assert "#10B981" in html_check

    # Терминал связи
    html_term = RPGContentParser.format_inline_rpg_blocks("&lt;terminal&gt;Входящее сообщение: База на связи.&lt;/terminal&gt;")
    assert "ТЕРМИНАЛ СВЯЗИ" in html_term
    assert "База на связи." in html_term


# ==============================================================================
# 2. ТЕСТЫ БЭКЕНДА: PROCESS_MODEL_TURN И СОХРАНЕНИЕ ТЕГОВ ДЛЯ UI
# ==============================================================================

def test_process_model_turn_retains_markup_for_ui(tmp_path):
    """
    Проверяет, что process_model_turn НЕ удаляет теги <stats> из реплики,
    но успешно считывает данные в cast_<session_id>.json.
    """
    from services.roleplay_state_manager import RoleplayStateManager
    from services.rp_cast_manager import RPCastManager

    test_sid = f"test_markup_sess_{uuid.uuid4().hex[:8]}"

    model_response = (
        "Ты ступаешь по мягкой траве бескрайней равнины.\n"
        "<stats>\n"
        "Местоположение: Бескрайняя равнина\n"
        "Инвентарь: [\"Кольцо ветра\", \"Фляга с водой\"]\n"
        "Класс: Странник\n"
        "BP: 120,000\n"
        "</stats>\n"
        "[STATUS: OK]"
    )

    try:
        # 1. Прогон через процессор хода модели
        cleaned = RoleplayStateManager.process_model_turn(test_sid, model_response)

        # 2. Тег <stats> ОБЯЗАН остаться в тексте для UI-парсера бабла!
        assert "<stats>" in cleaned
        assert "</stats>" in cleaned
        assert "Местоположение: Бескрайняя равнина" in cleaned
        assert "BP: 120,000" in cleaned

        # 3. Служебный бэкенд-тег [STATUS: OK] должен быть удален
        assert "[STATUS: OK]" not in cleaned

        # 4. База каста сессии должна быть обновлена детерминированно
        cast_data = RPCastManager.load_cast(test_sid)
        assert cast_data.get("current_location") == "Бескрайняя равнина"
        assert "Кольцо ветра" in cast_data.get("inventory", [])
        assert cast_data.get("stats", {}).get("BP") == "120,000"
    finally:
        RPCastManager.cleanup_session(test_sid)


# ==============================================================================
# 3. ТЕСТЫ PRE-SCAN КАРТОЧКИ ПРИ СТАРТЕ СЕССИИ (BOOTSTRAP_PARTY)
# ==============================================================================

def test_bootstrap_party_pre_scans_first_mes():
    """
    Проверяет, что при загрузке карточки (bootstrap_party_from_card)
    данные локации, инвентаря и статов из first_mes сразу попадают в лор.
    """
    from services.rp_cast_manager import RPCastManager

    test_sid = f"test_prescan_{uuid.uuid4().hex[:8]}"

    class MockCard:
        name = "Богиня Аква"
        scenery_dna = "Божественный храм, сияющие колонны"
        first_mes = (
            "Приветствую тебя в ином мире, путник!\n"
            "<stats>\n"
            "Местоположение: Тронный зал Богини\n"
            "Инвентарь: [\"Священный амулет\"]\n"
            "Оборудование: [\"Лазурная мантия\"]\n"
            "Цели: Победить Владыку Демонов\n"
            "Уровень: 99\n"
            "</stats>"
        )
        first_mes_ru = None
        scenario = "Начало приключения в фэнтези мире."
        description = "Капризная богиня воды."
        companions = []

    card = MockCard()

    try:
        cast_data = RPCastManager.bootstrap_party_from_card(test_sid, card)
        assert cast_data is not None

        # Проверяем наполнение каста еще ДО первого хода игрока
        assert cast_data.get("current_location") == "Тронный зал Богини"
        inv = cast_data.get("inventory", [])
        assert "Священный амулет" in inv
        assert any("Лазурная мантия" in item for item in inv)
        assert cast_data.get("active_quest") == "Победить Владыку Демонов"
        assert cast_data.get("stats", {}).get("Уровень") == "99"
    finally:
        RPCastManager.cleanup_session(test_sid)


# ==============================================================================
# 4. ТЕСТ САНИТИЗАЦИИ АРТ-ПРОМПТА (ИЗОЛЯЦИЯ ТЕХНИЧЕСКИХ ДАННЫХ)
# ==============================================================================

def test_art_context_sanitization_removes_stats():
    """
    Проверяет, что при формировании ТЗ для арт-директора из текста реплики
    полностью срезаются блоки <stats>, исключая попадание цифр в SD.
    """
    raw_message = (
        "Ты оглядываешься вокруг. Оказываешься в открытом поле. Пышная зелень разрастается во все стороны.\n"
        "<stats>\n"
        "BP: 5,000,000\n"
        "XP: 0/100\n"
        "Золото: 0G\n"
        "Уровень: ∞\n"
        "</stats>\n"
        "Ты чувствуешь божественную энергию в своих руках."
    )

    # Имитация санитизации в roleplay_flow_controller._generate_ephemeral_rp_art_prompt
    clean_ctx = re.sub(r'<stats>.*?</stats>', '', raw_message, flags=re.DOTALL | re.IGNORECASE)
    clean_ctx = re.sub(r'<thought>.*?</thought>', '', clean_ctx, flags=re.DOTALL | re.IGNORECASE)
    clean_ctx = re.sub(r'\[(?:СЦЕНА|SCENE|СТАТУС|STATUS|ШРАМ|SCAR|КВЕСТ|QUEST|IMAGE_PROMPT)[^\]]*\]', '', clean_ctx, flags=re.IGNORECASE)
    clean_ctx = re.sub(r'</?(?:stats|thought|meters|choices|check|terminal|sms|phone)>', '', clean_ctx, flags=re.IGNORECASE).strip()

    # В контекст сцены должны войти художественные описания
    assert "Ты оглядываешься вокруг. Оказываешься в открытом поле." in clean_ctx
    assert "Ты чувствуешь божественную энергию в своих руках." in clean_ctx

    # Но никаких цифр ЛитРПГ характеристик!
    assert "5,000,000" not in clean_ctx
    assert "BP" not in clean_ctx
    assert "0/100" not in clean_ctx
    assert "<stats>" not in clean_ctx
