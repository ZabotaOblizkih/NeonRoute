"""
Tests for Adaptive RP Thought Spoiler and Thought Styles Resolver:
1. resolve_thought_style for Assistant (None), 11 character card archetypes, and custom overrides.
2. ThoughtSpoilerWidget behavior in RP (is_rp=True) vs Assistant (is_rp=False) modes.
3. Integration with RoleplayFlowController and History synchronization.
"""

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from typing import Dict, Any, Optional
from unittest.mock import MagicMock
import pytest
from PyQt6.QtWidgets import QApplication

from services.tavern_models import CharacterCard
from services.thought_styles import ThoughtStyleConfig, resolve_thought_style
from services.thought_stream_parser import ThoughtStreamParser
from ui.components.thought_spoiler_widget import ThoughtSpoilerWidget
from services.roleplay_flow_controller import RoleplayFlowControllerMixin


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# =========================================================================
# 1. ТЕСТЫ РЕЗОЛВЕРА СТИЛЕЙ (resolve_thought_style)
# =========================================================================

def test_resolve_thought_style_assistant_default():
    """Проверка дефолтного стиля для режима Ассистента (card=None)."""
    style = resolve_thought_style(None)
    assert style.icon == "🧠"
    assert style.active_label == "Анализ данных..."
    assert style.collapsed_label == "Ход мыслей"
    assert style.accent_color == "#38BDF8"
    assert style.is_rp is False


@pytest.mark.parametrize(
    "archetype_kwargs, expected_icon, expected_active, expected_collapsed, expected_color",
    [
        (
            {"is_system": True, "name": "Система Исекая"},
            "⚙️",
            "Обработка параметров Системы...",
            "Лог Системы",
            "#38BDF8",
        ),
        (
            {"is_dating": True, "name": "Серафина"},
            "✨",
            "Серафина прислушивается к чувствам...",
            "Невысказанное",
            "#B47EB3",
        ),
        (
            {"is_horror": True, "name": "Нечто"},
            "👁️",
            "Тьма сгущается вокруг...",
            "Шёпот за гранью",
            "#B94A5A",
        ),
        (
            {"is_cyoa": True, "name": "Судьба"},
            "🧭",
            "Нити сюжета сплетаются...",
            "Ветвление сцены",
            "#6B9AC4",
        ),
        (
            {"is_reverse_rp": True, "name": "Пленница"},
            "⚖️",
            "Пленница ищет выход из положения...",
            "Скрытый расчёт",
            "#6366F1",
        ),
        (
            {"is_detective": True, "name": "Лестрейд"},
            "🔍",
            "Сопоставляет улики и мотивы...",
            "Ход расследования",
            "#6B8CAE",
        ),
        (
            {"is_dm": True, "name": "Мастер"},
            "🎲",
            "Мастер сверяется с хроникой...",
            "Замысел Мастера",
            "#8B5CF6",
        ),
        (
            {"is_scenario": True, "name": "Зона"},
            "☢️",
            "Оценка обстановки и угроз...",
            "Сводка среды",
            "#B87A56",
        ),
        (
            {"is_group": True, "name": "Отряд наёмниц"},
            "👥",
            "Реакция спутников...",
            "За кадром диалога",
            "#569DAA",
        ),
        (
            {"is_lorebook": True, "name": "Летопись"},
            "📜",
            "Поиск в архивах мира...",
            "Заметки летописца",
            "#94A3B8",
        ),
        (
            {"name": "Алиса"},
            "💭",
            "Алиса погружается в раздумья...",
            "Внутренний монолог • Алиса",
            "#818CF8",
        ),
    ]
)
def test_resolve_thought_style_all_archetypes(
    archetype_kwargs, expected_icon, expected_active, expected_collapsed, expected_color
):
    """Проверка всех 11 архетипов карточки в порядке их канонического приоритета."""
    card = CharacterCard(**archetype_kwargs)
    style = resolve_thought_style(card)
    assert style.icon == expected_icon
    assert style.active_label == expected_active
    assert style.collapsed_label == expected_collapsed
    assert style.accent_color == expected_color
    assert style.is_rp is True


def test_resolve_thought_style_custom_override():
    """Проверка кастомного переопределения через extensions.neonroute.thought_style."""
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
    card = CharacterCard(name="Моргана", raw_data=custom_raw)
    style = resolve_thought_style(card)

    assert style.icon == "🔮"
    assert style.active_label == "Моргана читает ауру..."
    assert style.collapsed_label == "Астральное эхо"
    assert style.accent_color == "#D946EF"
    assert style.is_rp is True


# =========================================================================
# 2. ТЕСТЫ ВИДЖЕТА (ThoughtSpoilerWidget)
# =========================================================================

def test_thought_spoiler_widget_rp_mode(qapp):
    """
    В режиме РП:
    - При стриминге (append_thought_chunk) спойлер остаётся свёрнутым.
    - При финализации (finalize_thought) секунды и токены выносятся в toolTip.
    - Текст мыслей оформлен курсивом в #CBD5E1.
    """
    widget = ThoughtSpoilerWidget()
    rp_style = ThoughtStyleConfig(
        icon="✨",
        active_label="Серафина прислушивается к чувствам...",
        collapsed_label="Невысказанное",
        accent_color="#B47EB3",
        is_rp=True
    )
    widget.apply_style(rp_style)

    assert widget.is_collapsed is True
    assert widget.title_lbl.text() == "Невысказанное"
    assert widget.icon_lbl.text() == "✨"

    # 1. Потоковое добавление
    widget.append_thought_chunk("Он смотрит так проницательно... ")
    widget.append_thought_chunk("Нужно скрыть волнение.")

    # Должен остаться свёрнутым при стриминге в RP
    assert widget.is_collapsed is True
    assert widget.metrics_label.text() == "Серафина прислушивается к чувствам..."
    assert "Он смотрит так проницательно..." in widget.get_thought_text()

    # 2. Финализация мыслей
    widget.finalize_thought(duration_sec=3.2, token_count=58)
    assert widget.is_collapsed is True
    # В видимом заголовке чистый collapsed_label без секунд
    assert widget.title_lbl.text() == "Невысказанное"
    assert widget.metrics_label.text() == ""
    # Секунды и токены в тултипе
    assert "58 токенов • 3.2 сек" in widget.toolTip()


def test_thought_spoiler_widget_assistant_mode(qapp):
    """
    В режиме Ассистента:
    - При стриминге спойлер разворачивается автоматически.
    - При финализации секунды и токены отображаются в видимом metrics_label.
    """
    widget = ThoughtSpoilerWidget()
    assistant_style = resolve_thought_style(None)
    widget.apply_style(assistant_style)

    assert widget.is_collapsed is True
    assert widget.title_lbl.text() == "Ход мыслей"
    assert widget.icon_lbl.text() == "🧠"

    # 1. Потоковое добавление разворачивает спойлер
    widget.append_thought_chunk("Формирую план ответа...")
    assert widget.is_collapsed is False
    assert widget.metrics_label.text() == "Анализ данных..."

    # 2. Финализация сворачивает и отображает счётчик
    widget.finalize_thought(duration_sec=1.5, token_count=24)
    assert widget.is_collapsed is True
    assert widget.metrics_label.text() == "24 токенов • 1.5 сек"
    assert widget.title_lbl.text() == "Ход мыслей"


def test_thought_spoiler_widget_set_thought_data_rp(qapp):
    """Восстановление мыслей из сохранённой истории в РП режиме."""
    widget = ThoughtSpoilerWidget()
    rp_style = ThoughtStyleConfig(
        icon="🔍",
        active_label="Сопоставляет улики...",
        collapsed_label="Ход расследования",
        accent_color="#6B8CAE",
        is_rp=True
    )
    widget.apply_style(rp_style)
    widget.set_thought_data("Зацепка №1: следы у окна ведут в сад.", duration=2.4, token_count=18)

    assert widget.isVisible() is True
    assert widget.is_collapsed is True
    assert widget.title_lbl.text() == "Ход расследования"
    assert widget.metrics_label.text() == ""
    assert "18 токенов • 2.4 сек" in widget.toolTip()
    assert "Зацепка №1" in widget.get_thought_text()


# =========================================================================
# 3. ТЕСТЫ ИНТЕГРАЦИИ В РОЛЕВОЙ ПАЙПЛАЙН (RoleplayFlowControllerMixin)
# =========================================================================

class DummyRPController(RoleplayFlowControllerMixin):
    """Тестовый фасад для проверки RoleplayFlowControllerMixin без UI зависимостей."""
    def __init__(self, card: CharacterCard):
        self.active_character = card
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


def test_rp_controller_thought_interception_and_clean_output():
    """
    Проверяет, что:
    1. Потоковые теги <think>...</think> перехватываются ThoughtStreamParser.
    2. Теги и мысли вырезаются из чистового текста реплики персонажа.
    3. Структура thought передаётся в _sync_turn для сохранения в session.json.
    """
    card = CharacterCard(
        name="Серафина",
        is_dating=True,
        description="Эльфийка-маг",
        first_mes="Привет!"
    )
    controller = DummyRPController(card)

    # Имитируем старт очереди
    controller._thought_stream_parser = ThoughtStreamParser()
    assert controller._thought_stream_parser is not None

    # Имитируем стриминг через _thought_stream_parser
    stream_chunks = [
        "<think>",
        "Он подошёл слишком близко. Сердце колотится, но я не должна подавать виду.",
        "</think>",
        "*Серафина делает шаг назад и смущённо улыбается:*\n— Ты сегодня рано."
    ]

    for chunk in stream_chunks:
        controller._thought_stream_parser.feed(chunk)

    # Имитируем завершение хода через _handle_roleplay_llm_response
    raw_full_text = "".join(stream_chunks)
    controller._handle_roleplay_llm_response(raw_full_text)

    # 1. Проверяем парсер сброшен
    assert controller._thought_stream_parser is None

    # 2. Проверяем сохранение хода
    saved = controller._sync_turn_called_with
    assert saved is not None
    assert saved["role"] == "assistant"
    assert saved["mode"] == "roleplay"

    # 3. Чистовой текст не содержит тегов <think> и мыслей
    assert "<think>" not in saved["content"]
    assert "</think>" not in saved["content"]
    assert "Сердце колотится" not in saved["content"]
    assert "Ты сегодня рано" in saved["content"]

    # 4. Блок мыслей передан в thought
    assert saved["thought"] is not None
    assert "Сердце колотится" in saved["thought"]["content"]
    assert saved["thought"]["token_count"] > 0
