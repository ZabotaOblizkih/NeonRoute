"""
Unit tests for ThoughtStreamParser and ThoughtSpoilerWidget:
1. Standard <think>...</think> token stream parsing.
2. Tag boundary stitching when <think> or </think> are broken across chunks.
3. Fail-safe circuit breaker on exceeding 1800 chars / ~450 tokens.
4. Clean dialogue message without think tags.
5. Persistence of 'thought' block in session.json and reload.
6. ThoughtSpoilerWidget UI states, metrics, and collapse toggling.
"""

import json
import pytest
from pathlib import Path
from PyQt6.QtWidgets import QApplication

from services.thought_stream_parser import ThoughtStreamParser, ThinkParserState
from services.session_manager import SessionManager
from ui.components.thought_spoiler_widget import ThoughtSpoilerWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_standard_think_parsing():
    parser = ThoughtStreamParser()
    chunks = [
        "<think>",
        "Анализирую параметры запроса пользователя. ",
        "Требуется вычислить температуру охлаждения.",
        "</think>",
        "Для эффективного охлаждения процессора установите кулер."
    ]

    events = []
    for c in chunks:
        events.extend(parser.feed(c))
    events.extend(parser.finalize())

    # Проверяем последовательность событий
    ev_types = [e[0] for e in events]
    assert "start_think" in ev_types
    assert "thought_chunk" in ev_types
    assert "end_think" in ev_types
    assert "normal_chunk" in ev_types

    assert parser.state == ThinkParserState.THINK_FINISHED
    assert "Анализирую параметры" in parser.thought_text
    assert "Для эффективного охлаждения" in parser.normal_text
    assert "<think>" not in parser.normal_text
    assert "</think>" not in parser.normal_text

    thought_data = parser.get_thought_data()
    assert thought_data is not None
    assert "Анализирую параметры" in thought_data["content"]
    assert thought_data["token_count"] > 0
    assert thought_data["duration"] >= 0.0


def test_split_tags_across_chunks():
    parser = ThoughtStreamParser()
    # Теги <think> и </think> разорваны между соседними сетевыми чанками
    chunks = [
        "Преамбула диалога. <th",
        "ink>Скрытые мысли ассистента на границе",
        " чанков сети.</th",
        "ink>Окончательный чистовой ответ."
    ]

    events = []
    for c in chunks:
        events.extend(parser.feed(c))
    events.extend(parser.finalize())

    assert parser.state == ThinkParserState.THINK_FINISHED
    assert parser.thought_text == "Скрытые мысли ассистента на границе чанков сети."
    assert parser.normal_text == "Преамбула диалога. Окончательный чистовой ответ."

    thought_data = parser.get_thought_data()
    assert thought_data is not None
    assert thought_data["content"] == "Скрытые мысли ассистента на границе чанков сети."


def test_failsafe_limit_triggered():
    parser = ThoughtStreamParser()
    # Модель «забыла» закрыть тег </think> и генерирует гигантский поток рассуждений
    half_limit = parser.MAX_THINK_CHARS // 2 + 500
    chunks = [
        "<think>",
        "Бесконечные мысли: " + ("X" * half_limit),
        "Продолжение мыслей: " + ("Y" * half_limit),
        "Хвост сообщения после превышения лимита."
    ]

    events = []
    for c in chunks:
        events.extend(parser.feed(c))
    events.extend(parser.finalize())

    # Предохранитель должен был принудительно перевести состояние в THINK_FINISHED
    assert parser.state == ThinkParserState.THINK_FINISHED
    assert parser._failsafe_triggered is True

    # Объем мыслей ограничен MAX_THINK_CHARS (1800 символов)
    assert len(parser.thought_text) <= parser.MAX_THINK_CHARS
    # Весь последующий текст направлен в чистовой ответ
    assert len(parser.normal_text) > 0
    assert "Хвост сообщения" in parser.normal_text


def test_message_without_think_tags():
    parser = ThoughtStreamParser()
    chunks = [
        "Привет, ",
        "Элария! ",
        "Расскажи интересную историю про киберпанк."
    ]

    events = []
    for c in chunks:
        events.extend(parser.feed(c))
    events.extend(parser.finalize())

    ev_types = [e[0] for e in events]
    assert "start_think" not in ev_types
    assert "thought_chunk" not in ev_types
    assert "end_think" not in ev_types

    assert parser.state == ThinkParserState.NORMAL_TEXT
    assert parser.thought_text == ""
    assert parser.normal_text == "Привет, Элария! Расскажи интересную историю про киберпанк."
    assert parser.get_thought_data() is None


def test_lone_starting_closing_think_tag_stripped():
    """
    Проверяет, что одинокий закрывающий тег </think> в самом начале потока
    (например, артефакт модели после assistant prefill) вырезается без эмита событий мыслей.
    """
    parser = ThoughtStreamParser()
    chunks = [
        "</think>\n\n",
        "Конечно, вот готовый ",
        "результат работы."
    ]

    events = []
    for c in chunks:
        events.extend(parser.feed(c))
    events.extend(parser.finalize())

    ev_types = [e[0] for e in events]
    assert "start_think" not in ev_types
    assert "thought_chunk" not in ev_types
    assert "end_think" not in ev_types

    assert parser.state == ThinkParserState.NORMAL_TEXT
    assert parser.thought_text == ""
    assert "</think>" not in parser.normal_text
    assert "Конечно, вот готовый результат работы." in parser.normal_text
    assert parser.get_thought_data() is None



def test_session_persistence_thought_block(tmp_path, monkeypatch):
    history_tmp = tmp_path / "history"
    outputs_tmp = tmp_path / "outputs"
    history_tmp.mkdir()
    outputs_tmp.mkdir()

    monkeypatch.setattr("services.session_manager.HISTORY_DIR", history_tmp)
    monkeypatch.setattr("services.session_manager.OUTPUTS_DIR", outputs_tmp)

    sess_id = "test_thought_persist_session"
    session_data = {
        "id": sess_id,
        "title": "Сессия с рассуждениями",
        "created_at": "2026-10-25T00:00:00",
        "messages": [
            {"role": "user", "text": "Сколько ядер у процессора?", "is_user": True},
            {
                "role": "assistant",
                "text": "У этого процессора 8 производительных и 4 энергоэффективных ядра.",
                "is_user": False,
                "thought": {
                    "content": "Пользователь спрашивает спецификацию. Вспоминаю архитектуру hybrid CPU...",
                    "duration": 2.4,
                    "token_count": 68
                }
            }
        ]
    }

    # 1. Сохранение
    saved_path = SessionManager.save_session(session_data)
    assert saved_path is not None
    assert saved_path.exists()

    # Проверяем outputs/sessions/<id>/session.json
    session_json_file = outputs_tmp / "sessions" / sess_id / "session.json"
    assert session_json_file.exists()

    with open(session_json_file, "r", encoding="utf-8") as f:
        data_disk = json.load(f)

    ast_msg = data_disk["messages"][1]
    assert "thought" in ast_msg
    assert ast_msg["thought"]["content"].startswith("Пользователь спрашивает спецификацию")
    assert ast_msg["thought"]["duration"] == 2.4
    assert ast_msg["thought"]["token_count"] == 68

    # 2. Загрузка через load_session
    reloaded = SessionManager.load_session(sess_id)
    assert reloaded is not None
    r_ast = reloaded["messages"][1]
    assert "thought" in r_ast
    assert r_ast["thought"]["duration"] == 2.4
    assert r_ast["thought"]["token_count"] == 68


def test_thought_spoiler_widget_ui(qapp):
    widget = ThoughtSpoilerWidget()
    widget.show()

    # Исходное состояние: свернут
    assert widget.is_collapsed is True
    assert widget.content_area.isHidden() is True

    # Стриминг чанков
    widget.append_thought_chunk("Первая мысль... ")
    assert widget.is_collapsed is False
    assert widget.content_area.isHidden() is False
    assert "Анализ данных..." in widget.metrics_label.text()

    widget.append_thought_chunk("Вторая мысль.")
    assert "Первая мысль... Вторая мысль." in widget.get_thought_text()

    # Финализация
    widget.finalize_thought(duration_sec=3.5, token_count=45)
    # После финализации виджет автоматически аккуратно сворачивается
    assert widget.is_collapsed is True
    assert widget.content_area.isHidden() is True
    assert "45 токенов • 3.5 сек" in widget.metrics_label.text()

    # Ручное переключение кликом
    widget.toggle_collapsed()
    assert widget.is_collapsed is False
    assert widget.content_area.isHidden() is False

    widget.toggle_collapsed()
    assert widget.is_collapsed is True
    assert widget.content_area.isHidden() is True

    # Восстановление из истории
    widget.set_thought_data("Мысли загруженные из session.json", duration=1.8, token_count=32)
    assert widget.is_collapsed is True
    assert widget.isHidden() is False
    assert "32 токенов • 1.8 сек" in widget.metrics_label.text()
    assert "Мысли загруженные" in widget.get_thought_text()


def test_implicit_open_think_tag_closed_in_stream():
    """
    Сценарий DeepSeek-R1 / Qwen3: llama-server инжектирует открывающий <think> в промпт,
    поэтому в потоке идут сразу мысли и закрывающий </think>, а затем чистовой ответ.
    """
    parser = ThoughtStreamParser()
    chunks = [
        "(Ну что же, пользователь написал Привет. ",
        "Надо ответить живо и по делу.)\n",
        "</think>\n",
        "Привет, Виктор! Готова к работе."
    ]

    events = []
    for c in chunks:
        events.extend(parser.feed(c))
    events.extend(parser.finalize())

    ev_types = [e[0] for e in events]
    assert "start_think" in ev_types
    assert "reset_normal" in ev_types
    assert "end_think" in ev_types

    assert parser.state == ThinkParserState.THINK_FINISHED
    assert "Ну что же, пользователь написал Привет" in parser.thought_text
    assert parser.normal_text.strip() == "Привет, Виктор! Готова к работе."
    assert "</think>" not in parser.normal_text


def test_multiple_think_blocks_merged_into_thought_text():
    """
    Сценарий двойного <think>: сначала прошел блок reasoning_content (<think>...</think>),
    а затем в текстовом потоке модель открыла второй <think>...</think> перед ответом.
    Оба блока должны попасть в thought_text, не протекая в normal_text.
    """
    parser = ThoughtStreamParser()
    chunks = [
        "<think>Первичная мысль из reasoning_content.</think>",
        "\n<think>\nВторая мысль (самопроверка перед ответом).\n</think>\n",
        "Чистовой ответ без утечки мыслей!"
    ]

    for c in chunks:
        parser.feed(c)
    parser.finalize()

    assert parser.state == ThinkParserState.THINK_FINISHED
    assert "Первичная мысль" in parser.thought_text
    assert "Вторая мысль" in parser.thought_text
    assert parser.normal_text.strip() == "Чистовой ответ без утечки мыслей!"
    assert "<think>" not in parser.normal_text
    assert "Вторая мысль" not in parser.normal_text
