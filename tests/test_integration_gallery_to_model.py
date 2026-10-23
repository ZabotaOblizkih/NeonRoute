"""
Сквозной интеграционный тест: от CharactersGalleryDialog до пакета вызова LLM.
Проверяет прохождение карточки через весь стек приложения для всех 11 архетипов:
[Галерея] -> [Выбор реплики / Старт] -> [Сигнал Qt] -> [RoleplayFlow] ->
[SessionManager] -> [RoleplayPromptBuilder] -> [InferenceTask.messages].
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QWidget

from services.tavern_parser import CharacterCard
from services.task_queue import InferenceTask
from ui.dialogs.characters_gallery_dialog import CharactersGalleryDialog

try:
    from ui.controllers.roleplay_flow import RoleplayFlowControllerMixin
except ImportError:
    try:
        from ui.roleplay_flow import RoleplayFlowControllerMixin
    except ImportError:
        try:
            from controllers.roleplay_flow import RoleplayFlowControllerMixin
        except ImportError:
            from ui.main_window import RoleplayFlowControllerMixin


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


class HostMainWindowMock(QWidget, RoleplayFlowControllerMixin):
    def __init__(self):
        super().__init__()
        self.current_session = None
        self.active_character = None
        self.chat_history = []
        self._current_user_request = ""
        self._active_tasks = {}
        self.selected_forge_checkpoint = None

        self.lm_combo = MagicMock()
        self.lm_combo.currentText.return_value = "default (Qwen 2.5 7B)"

        self.lm_client = MagicMock()
        self.task_queue = MagicMock()
        self.pill_forge = MagicMock()
        self.pill_forge.is_online = False
        self.app_config = {"auto_generate_art": False}

    def start_roleplay_session(self, card: CharacterCard):
        self.active_character = card
        
        pool = card.get_greetings_pool() if hasattr(card, "get_greetings_pool") else []
        idx = getattr(card, "selected_greeting_idx", None)
        if idx is not None and pool and 0 <= idx < len(pool):
            first_greeting = pool[idx].get("effective") or pool[idx].get("orig") or getattr(card, "first_mes", "")
        else:
            first_greeting = getattr(card, "first_mes_ru", None) or getattr(card, "first_mes", "") or "Здравствуй!"
        
        self.current_session = {
            "id": "e2e_sess_001",
            "character_id": getattr(card, "id", "char_test"),
            "character_name": card.name,
            "user_persona_id": None,
            "messages": [
                {"role": "assistant", "content": first_greeting, "is_user": False}
            ]
        }
        self.chat_history = list(self.current_session["messages"])

    def user_turn(self, query: str):
        self._current_user_request = query
        self.chat_history.append({"role": "user", "content": query, "is_user": True})
        self.current_session["messages"].append({"role": "user", "content": query, "is_user": True})
        self._enqueue_roleplay_llm_task()


ARCHETYPE_TEST_CASES = [
    (
        "Solo Female",
        CharacterCard(
            name="Селеста",
            is_male=False,
            description="Могущественная чародейка.",
            first_mes="*Селеста закрывает гримуар.* Здравствуй."
        ),
        [
            "## Режим персонажа: Селеста",
            "Селеста — женский персонаж (глаголы и причастия в женском роде)",
            "Игрок (Роман) — мужчина",
            "Манера повествования действий: от третьего лица"
        ]
    ),
    (
        "Solo Male",
        CharacterCard(
            name="Артур",
            is_male=True,
            description="Рыцарь ордена.",
            first_mes="*Артур обнажает клинок.*"
        ),
        [
            "## Режим персонажа: Артур",
            "Артур — мужской персонаж (глаголы и причастия в мужском роде)"
        ]
    ),
    (
        "LitRPG / System",
        CharacterCard(
            name="Система",
            is_system=True,
            description="Голос интерфейса подземелья.",
            first_mes="[Система: Добро пожаловать в Башню]"
        ),
        [
            "## Режим: интерфейс системы (ЛитРПГ / Исекай)",
            "Ты — бесплотный Рассказчик и интерфейс мира / Системы",
            "<stats>...</stats>",
            "[Система: получено достижение]"
        ]
    ),
    (
        "Dating Sim",
        CharacterCard(
            name="Мика",
            is_dating=True,
            description="Стеснительная подруга детства.",
            first_mes="*Мика краснеет и отводит взгляд.*"
        ),
        [
            "## Режим: симулятор отношений (дейт-сим)",
            "<meters>",
            "Симпатия: [число от 0 до 100]/100",
            "Стадия: [Незнакомец / Приятель / Друг / Симпатия / Влюбленность]"
        ]
    ),
    (
        "Psychological Horror",
        CharacterCard(
            name="Нечто",
            is_horror=True,
            description="Тень в темном коридоре.",
            first_mes="*Фонарик начинает мерцать.*"
        ),
        [
            "## Режим: психологический хоррор и рассудок",
            "<meters>",
            "Рассудок: [число от 0 до 100]%",
            "Стресс: [Низкий / Умеренный / Паника]"
        ]
    ),
    (
        "Interactive CYOA",
        CharacterCard(
            name="Киберпанк Новелла",
            is_cyoa=True,
            description="Интерактивный сюжет в Нео-Токио.",
            first_mes="Ты стоишь перед запертой дверью склада."
        ),
        [
            "## Режим: интерактивная новелла (CYOA)",
            "<choices>",
            "1. Первый вариант действия"
        ]
    ),
    (
        "Reverse RP / Dependent",
        CharacterCard(
            name="Пленник",
            is_reverse_rp=True,
            description="Схваченный диверсант.",
            first_mes="*Смотрит в пол, ожидая допроса.*"
        ),
        [
            "## Режим: зависимый персонаж (допрос / наставничество)",
            "главная власть и ведущая роль принадлежат игроку",
            "Запрещено перехватывать инициативу"
        ]
    ),
    (
        "Detective Investigation",
        CharacterCard(
            name="Инспектор Лестрейд",
            is_detective=True,
            description="Место преступления на Бейкер-стрит.",
            first_mes="*Осматривает разбитое окно.*"
        ),
        [
            "## Режим: детективное расследование",
            "<stats>",
            "Дело: [название дела]",
            "Найденные улики:"
        ]
    ),
    (
        "Game Master (DM)",
        CharacterCard(
            name="Мастер Подземелий",
            is_dm=True,
            description="Голос ведущего настольной ролевой игры.",
            first_mes="Перед вашим отрядом развилка в пещере."
        ),
        [
            "## Режим: гейммастер (DM)",
            "<check>Сложность:",
            "<choices>"
        ]
    ),
    (
        "Survival / Scenario",
        CharacterCard(
            name="Зона Отчуждения",
            is_scenario=True,
            description="Выживание в радиоактивных лесах.",
            first_mes="Счетчик Гейгера тихо потрескивает на ветру."
        ),
        [
            "## Режим: симулятор выживания",
            "физическое состояние игрока (ранения, усталость, холод)"
        ]
    ),
    (
        "Group Companions",
        CharacterCard(
            name="Отряд Наёмниц",
            is_group=True,
            companions=[
                {"name": "Лия", "visual_dna": "1girl, warrior"},
                {"name": "Висп", "visual_dna": "1girl, mage"}
            ],
            description="Две напарницы у костра.",
            first_mes="*Лия точит клинок, а Висп читает свиток.*"
        ),
        [
            "## Режим: группа спутников",
            "Группа спутников (Лия, Висп)",
            "Запрещено одиночное первое лицо"
        ]
    )
]


class TestEndToEndGalleryToModelPipeline:
    @pytest.mark.parametrize("archetype_name,card,expected_markers", ARCHETYPE_TEST_CASES)
    def test_full_pipeline_from_gallery_to_llm_messages(
        self, qapp, tmp_path, monkeypatch, archetype_name, card, expected_markers
    ):
        test_dir = tmp_path / "gallery_e2e"
        test_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("ui.dialogs.characters_gallery_dialog.CHARACTERS_DIR", test_dir)
        monkeypatch.setattr("ui.dialogs.characters_gallery_dialog.GALLERY_THUMBS_DIR", test_dir / "cache")

        host_window = HostMainWindowMock()
        gallery_dlg = CharactersGalleryDialog(parent=host_window)
        gallery_dlg.start_session_requested.connect(host_window.start_roleplay_session)

        card_path = test_dir / f"{card.name}.json"
        gallery_dlg.active_entry = {"card": card, "path": card_path}
        
        # Заполняем поля редактора в диалоге данными карточки (эмуляция выбора карточки в UI)
        gallery_dlg._showing_ru = False
        gallery_dlg.input_name.setText(card.name)
        gallery_dlg.input_desc.setPlainText(card.description or "")
        gallery_dlg.input_personality.setPlainText(card.personality or "")
        gallery_dlg.input_scenario.setPlainText(card.scenario or "")
        gallery_dlg.input_mes_example.setPlainText(card.mes_example or "")
        gallery_dlg.input_first_mes.setPlainText(card.first_mes or "")

        gallery_dlg.current_greeting_idx = 0
        gallery_dlg.chk_use_as_start.setChecked(True)

        gallery_dlg._start_dialog()

        assert host_window.active_character is not None
        assert host_window.active_character.name == card.name
        assert host_window.current_session is not None
        assert len(host_window.chat_history) == 1

        user_reply = "Здравствуй, расскажи мне о текущей ситуации."

        with patch("services.session_manager.SessionManager.get_active_user_persona", return_value={"name": "Роман", "gender": "male"}), \
             patch("services.rp_cast_manager.RPCastManager.scan_and_tick", return_value={}), \
             patch("services.rp_cast_manager.RPCastManager.get_world_state", return_value=[]):

            host_window.user_turn(user_reply)

        host_window.task_queue.enqueue.assert_called_once()
        task: InferenceTask = host_window.task_queue.enqueue.call_args[0][0]

        assert isinstance(task, InferenceTask)
        assert task.task_type == "llm"
        llm_kwargs = task.kwargs

        messages = llm_kwargs["messages"]
        assert len(messages) >= 3

        system_content = messages[0]["content"]
        assert messages[0]["role"] == "system"

        assert "## Правила отыгрыша и стиля:" in system_content
        assert "Границы роли (Non-Godmoding):" in system_content
        assert "Язык — СТРОГО русский" in system_content
        assert "Имя протагониста: Роман" in system_content

        for marker in expected_markers:
            assert marker in system_content, (
                f"Маркер '{marker}' архетипа '{archetype_name}' отсутствует в итоговом system prompt!"
            )

        assert messages[1]["role"] == "assistant"
        assert messages[2]["role"] == "user"
        # Реплика пользователя присутствует в начале сообщения (с учетом системных суффиксов окружения)
        assert user_reply in messages[2]["content"]

        assert llm_kwargs["is_roleplay"] is True
        assert llm_kwargs["context_length"] == 24576

        gallery_dlg._cleanup_dialog_tasks()
        gallery_dlg.close()
        host_window.deleteLater()

    def test_lorebook_card_is_strictly_blocked_from_session_start(self, qapp, tmp_path, monkeypatch):
        test_dir = tmp_path / "lore_e2e"
        test_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("ui.dialogs.characters_gallery_dialog.CHARACTERS_DIR", test_dir)
        monkeypatch.setattr("ui.dialogs.characters_gallery_dialog.GALLERY_THUMBS_DIR", test_dir / "cache")

        host_window = HostMainWindowMock()
        gallery_dlg = CharactersGalleryDialog(parent=host_window)

        lore_card = CharacterCard(
            name="Бестиарий Пустошей",
            is_lorebook=True,
            description="Справочник монстров мира."
        )
        gallery_dlg.active_entry = {"card": lore_card, "path": test_dir / "lore.json"}
        gallery_dlg.input_name.setText(lore_card.name)

        signal_mock = MagicMock()
        gallery_dlg.start_session_requested.connect(signal_mock)

        with patch("PyQt6.QtWidgets.QMessageBox.information") as mock_info:
            gallery_dlg._start_dialog()
            mock_info.assert_called_once()
            signal_mock.assert_not_called()
            assert host_window.current_session is None

        gallery_dlg._cleanup_dialog_tasks()
        gallery_dlg.close()
        host_window.deleteLater()