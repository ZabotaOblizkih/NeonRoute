"""
Тесты для CharactersGalleryDialog (ui/dialogs/characters_gallery_dialog.py).
Проверка пагинации приветствий, инспектора отряда, переключения языков RU/EN,
блокировки лорбуков и очистки фоновых задач анализа.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QWidget

from services.tavern_parser import CharacterCard
from ui.dialogs.characters_gallery_dialog import (
    CharactersGalleryDialog,
    get_cached_circular_avatar
)


@pytest.fixture(scope="session")
def qapp():
    """Гарантирует существование единого экземпляра QApplication в offscreen-режиме."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    return app


@pytest.fixture
def mock_gallery(qapp, tmp_path, monkeypatch):
    """
    Создает изолированный экземпляр CharactersGalleryDialog с чистой временной директорией,
    чтобы тесты не затрагивали реальные карточки персонажей.
    """
    test_chars_dir = tmp_path / "characters"
    test_chars_dir.mkdir(parents=True, exist_ok=True)
    test_thumbs_dir = tmp_path / "cache" / "gallery_thumbs"
    test_thumbs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("ui.dialogs.characters_gallery_dialog.CHARACTERS_DIR", test_chars_dir)
    monkeypatch.setattr("ui.dialogs.characters_gallery_dialog.GALLERY_THUMBS_DIR", test_thumbs_dir)

    # В качестве parent передаем реальный QWidget для совместимости с C++ ядром PyQt6
    parent_widget = QWidget()
    parent_widget.task_queue = None

    dlg = CharactersGalleryDialog(parent=parent_widget)
    yield dlg, test_chars_dir
    dlg._cleanup_dialog_tasks()
    dlg.close()
    parent_widget.deleteLater()


class TestThumbnailCache:
    """Проверка генерации и кэширования миниатюр аватаров."""

    def test_cached_avatar_fallback_on_none_or_missing(self, qapp, tmp_path):
        # При отсутствии файла возвращается валидная заглушка с 3x HiDPI сглаживанием
        pix_none = get_cached_circular_avatar(None, name="Селеста", size=44)
        assert not pix_none.isNull()
        assert pix_none.width() >= 44
        assert pix_none.height() >= 44

        missing_path = tmp_path / "non_existing.png"
        pix_missing = get_cached_circular_avatar(missing_path, name="Селеста", size=44)
        assert not pix_missing.isNull()
        assert pix_missing.width() >= 44


class TestGreetingPagination:
    """Проверка переключения альтернативных приветствий и защиты текста от затирания."""

    def test_single_greeting_navigation_buttons_disabled(self, mock_gallery):
        dlg, _ = mock_gallery
        card = CharacterCard(
            name="Алиса",
            first_mes="Привет! Я одна в комнате.",
            alternate_greetings=[]
        )
        dlg.active_entry = {"card": card, "path": Path("dummy.json")}
        dlg.current_greeting_idx = 0
        dlg._update_greeting_display()

        assert dlg.lbl_greeting_page.text() == "1 / 1"
        assert dlg.btn_prev_greeting.isEnabled() is False
        assert dlg.btn_next_greeting.isEnabled() is False
        assert dlg.input_first_mes.toPlainText() == "Привет! Я одна в комнате."

    def test_multi_greeting_navigation_and_paging(self, mock_gallery):
        dlg, _ = mock_gallery
        card = CharacterCard(
            name="Селеста",
            first_mes="Завязка 1: В библиотеке.",
            alternate_greetings=["Завязка 2: На башне магов.", "Завязка 3: В саду."]
        )
        dlg.active_entry = {"card": card, "path": Path("dummy.json")}
        dlg.current_greeting_idx = 0
        dlg._update_greeting_display()

        # Страница 1 из 3
        assert dlg.lbl_greeting_page.text() == "1 / 3"
        assert dlg.btn_prev_greeting.isEnabled() is False
        assert dlg.btn_next_greeting.isEnabled() is True
        assert dlg.input_first_mes.toPlainText() == "Завязка 1: В библиотеке."

        # Переход на страницу 2
        dlg._show_next_greeting()
        assert dlg.current_greeting_idx == 1
        assert dlg.lbl_greeting_page.text() == "2 / 3"
        assert dlg.btn_prev_greeting.isEnabled() is True
        assert dlg.btn_next_greeting.isEnabled() is True
        assert dlg.input_first_mes.toPlainText() == "Завязка 2: На башне магов."

        # Переход на страницу 3
        dlg._show_next_greeting()
        assert dlg.current_greeting_idx == 2
        assert dlg.lbl_greeting_page.text() == "3 / 3"
        assert dlg.btn_prev_greeting.isEnabled() is True
        assert dlg.btn_next_greeting.isEnabled() is False
        assert dlg.input_first_mes.toPlainText() == "Завязка 3: В саду."

        # Возврат на страницу 2
        dlg._show_prev_greeting()
        assert dlg.current_greeting_idx == 1
        assert dlg.lbl_greeting_page.text() == "2 / 3"

    def test_save_current_greeting_preserves_edits_on_switch(self, mock_gallery):
        dlg, _ = mock_gallery
        card = CharacterCard(
            name="Селеста",
            first_mes="Старый текст 1",
            alternate_greetings=["Старый текст 2"]
        )
        dlg.active_entry = {"card": card, "path": Path("dummy.json")}
        dlg.current_greeting_idx = 0
        dlg._update_greeting_display()

        # Редактируем первое сообщение
        dlg.input_first_mes.setPlainText("Отредактированный текст 1")

        # Переключаемся на второе — текст первого должен автоматически зафиксироваться
        dlg._show_next_greeting()
        saved_text = card.first_mes_ru if getattr(card, "first_mes_ru", "") else card.first_mes
        assert saved_text == "Отредактированный текст 1"


class TestLanguageToggling:
    """Проверка переключения отображения RU / EN."""

    def test_toggle_card_language_swaps_content(self, mock_gallery):
        dlg, _ = mock_gallery
        card = CharacterCard(
            name="Селеста",
            description="English description of the mage.",
            description_ru="Русское описание могущественной волшебницы.",
            first_mes="Hello adventurer.",
            first_mes_ru="Здравствуй, путник."
        )
        dlg.active_entry = {"card": card, "path": Path("dummy.json")}
        dlg._showing_ru = True
        dlg._update_greeting_display()
        dlg.input_desc.setPlainText(card.description_ru)

        # Изначально отображается русский перевод
        assert dlg.input_desc.toPlainText() == "Русское описание могущественной волшебницы."

        # Переключаем на английский оригинал
        dlg._toggle_card_language()
        assert dlg._showing_ru is False
        assert dlg.input_desc.toPlainText() == "English description of the mage."
        assert "EN" in dlg.btn_toggle_lang.text()

        # Возвращаем на русский перевод
        dlg._toggle_card_language()
        assert dlg._showing_ru is True
        assert dlg.input_desc.toPlainText() == "Русское описание могущественной волшебницы."
        assert "RU" in dlg.btn_toggle_lang.text()


class TestGroupCardAndPartySelector:
    """Проверка поведения инспектора сопартийцев для одиночных и групповых карточек."""

    def test_solo_card_strictly_hides_party_container(self, mock_gallery):
        dlg, _ = mock_gallery
        card = CharacterCard(name="Соло персонаж", companions=[])
        dlg.active_entry = {"card": card, "path": Path("dummy.json")}

        dlg._update_party_selector_ui()

        # Для одиночной карточки панель отряда обязана быть скрыта
        assert dlg.party_selector_container.isHidden() is True
        assert dlg.active_companion_idx == -1
        assert dlg.btn_gallery_generate_art.text() == "🎨 Сгенерировать арт"

    def test_group_card_reveals_party_chips_and_switches_dna(self, mock_gallery):
        dlg, _ = mock_gallery
        companions = [
            {"name": "Лия", "visual_dna": "1girl, silver hair, slender"},
            {"name": "Висп", "visual_dna": "1girl, blue hair, small"}
        ]
        card = CharacterCard(name="Отряд сестёр", companions=companions, is_group=True)
        dlg.active_entry = {"card": card, "path": Path("dummy.json")}

        dlg._update_party_selector_ui()

        # Панель сопартийцев должна быть отображена
        assert dlg.party_selector_container.isHidden() is False
        # Должны создаться 3 кнопки: 2 сопартийца + 1 "Общий кадр"
        assert len(dlg.party_buttons) == 3

        # По умолчанию выбран первый сопартиец
        assert dlg.active_companion_idx == 0
        assert dlg.input_visual_dna.toPlainText() == "1girl, silver hair, slender"
        assert "Лия" in dlg.btn_gallery_generate_art.text()

        # Клик по второму сопартийцу (индекс 1)
        dlg._on_companion_chip_clicked(1)
        assert dlg.active_companion_idx == 1
        assert dlg.input_visual_dna.toPlainText() == "1girl, blue hair, small"
        assert "Висп" in dlg.btn_gallery_generate_art.text()

        # Клик по кнопке «Общий кадр» (индекс -1)
        dlg._on_companion_chip_clicked(-1)
        assert dlg.active_companion_idx == -1
        assert "BREAK" in dlg.input_visual_dna.toPlainText()
        assert "Отряд" in dlg.btn_gallery_generate_art.text()


class TestLorebookGuardrailsAndTaskCleanup:
    """Проверка блокировки лорбуков и очистки фоновых задач."""

    def test_lorebook_disables_start_dialog_and_hides_visual_dna(self, mock_gallery):
        dlg, _ = mock_gallery
        card = CharacterCard(name="Энциклопедия магии", is_lorebook=True)
        dlg.active_entry = {"card": card, "path": Path("dummy.json")}

        dlg._update_card_analysis_ui()
        dlg._update_sections_visibility()

        # Кнопка старта диалога заблокирована для лорбуков
        assert dlg.btn_start_dialog.isEnabled() is False
        assert "Лорбук" in dlg.btn_start_dialog.toolTip()
        # Секция внешности SD для лорбука скрыта
        assert dlg.sec_dna.isHidden() is True

    def test_start_dialog_with_lorebook_shows_notice_and_does_not_emit_signal(self, mock_gallery):
        dlg, _ = mock_gallery
        card = CharacterCard(name="Энциклопедия магии", is_lorebook=True)
        dlg.active_entry = {"card": card, "path": Path("dummy.json")}

        signal_mock = MagicMock()
        dlg.start_session_requested.connect(signal_mock)

        with patch("PyQt6.QtWidgets.QMessageBox.information") as mock_info:
            dlg._start_dialog()
            mock_info.assert_called_once()
            signal_mock.assert_not_called()

    def test_cleanup_dialog_tasks_aborts_active_workers(self, mock_gallery):
        dlg, _ = mock_gallery
        abort_mock = MagicMock()
        dlg._active_dialog_tasks["task_test_1"] = {
            "abort_fn": abort_mock,
            "on_done": MagicMock()
        }

        # Принудительная очистка при закрытии окна
        dlg._cleanup_dialog_tasks()

        abort_mock.assert_called_once()
        assert len(dlg._active_dialog_tasks) == 0
        assert len(dlg._running_analysis_tasks) == 0