import json
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PyQt6.QtCore import Qt, QCoreApplication
from PyQt6.QtWidgets import QApplication, QMessageBox, QFileDialog

# Гарантируем наличие экземпляра QApplication для виджетов Qt
app = QApplication.instance() or QApplication([])

import config
from ui.dialogs.characters_gallery_dialog import CharactersGalleryDialog
from services.tavern_parser import TavernParser, CharacterCard


@pytest.fixture
def isolated_gallery(tmp_path, monkeypatch):
    """
    Изолированное окружение: перенаправляет папки персонажей и кэша
    во временную директорию, чтобы тесты не трогали реальные файлы.
    """
    temp_chars_dir = tmp_path / "characters"
    temp_chars_dir.mkdir(parents=True, exist_ok=True)
    temp_cache_dir = tmp_path / "cache" / "gallery_thumbs"
    temp_cache_dir.mkdir(parents=True, exist_ok=True)

    # Подменяем пути в модулях
    monkeypatch.setattr(config, "CHARACTERS_DIR", temp_chars_dir)
    import ui.dialogs.characters_gallery_dialog as cgd
    monkeypatch.setattr(cgd, "CHARACTERS_DIR", temp_chars_dir)
    monkeypatch.setattr(cgd, "GALLERY_THUMBS_DIR", temp_cache_dir)
    import services.tavern_parser as tp
    monkeypatch.setattr(tp, "CHARACTERS_DIR", temp_chars_dir)

    # Создаем базовую карточку для тестов
    base_card_data = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "Тестовая Героиня",
            "description": "[Appearance: silver hair, blue eyes, tall athletic build] Бывалый наемник.",
            "personality": "Сдержанная, расчетливая",
            "scenario": "Таверна в портовом городе",
            "first_mes": "Приветствую тебя, путник.",
            "first_mes_ru": "Приветствую тебя, путник.",
            "mes_example": "<START>\n{{char}}: Чего тебе нужно?",
            "alternate_greetings": ["Ты что-то хотел спросить?", "Не стой в дверях."],
            "visual_dna": ""
        }
    }
    card_path = temp_chars_dir / "Тестовая Героиня.json"
    with open(card_path, "w", encoding="utf-8") as f:
        json.dump(base_card_data, f, ensure_ascii=False, indent=2)

    # Создаем диалог
    dialog = CharactersGalleryDialog()
    app.processEvents()

    yield dialog, temp_chars_dir, card_path

    # Корректное закрытие диалога
    dialog.close()
    app.processEvents()


class TestGalleryPipelineE2E:

    def test_01_gallery_load_and_selection_lifecycle(self, isolated_gallery):
        """
        Проверка: загрузка списка, выбор карточки и работа миксина dna_party_mixin.
        Ловит: NameError, отсутствие импортов, вылеты при извлечении Visual DNA.
        """
        dialog, _, card_path = isolated_gallery

        assert dialog.list_widget.count() == 1, "Карточка не загрузилась в список"
        
        # Симулируем клик по элементу списка
        item = dialog.list_widget.item(0)
        dialog.list_widget.setCurrentItem(item)
        app.processEvents()

        # Проверяем заполнение полей в UI
        assert dialog.input_name.text() == "Тестовая Героиня"
        assert "сильвер" in dialog.input_visual_dna.toPlainText().lower() or "silver hair" in dialog.input_visual_dna.toPlainText().lower(), (
            "Visual DNA не был автоматически извлечен при выборе карточки через миксин!"
        )

        # Проверяем, что файл на диске обновился с сохраненным visual_dna
        with open(card_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert "silver hair" in saved["data"].get("visual_dna", "")

    def test_02_create_new_character(self, isolated_gallery):
        """
        Проверка: создание нового персонажа через кнопку '+ Создать'.
        Ловит: сбои при генерации уникальных ID, записи шаблона v2 и обновлении списка.
        """
        dialog, chars_dir, _ = isolated_gallery

        dialog._create_new_character()
        app.processEvents()

        assert dialog.list_widget.count() == 2, "Новая карточка не добавилась в список"
        assert dialog.input_name.text() == "Новый персонаж"

        # Проверяем, что на диске появился второй .json файл
        all_jsons = list(chars_dir.glob("*.json"))
        assert len(all_jsons) == 2

    def test_03_edit_and_save_character(self, isolated_gallery):
        """
        Проверка: редактирование полей в UI и нажатие 'Сохранить изменения'.
        Ловит: сбои сериализации, рассинхрон модели и JSON на диске.
        """
        dialog, _, card_path = isolated_gallery

        # Меняем данные через виджеты
        dialog.input_name.setText("Элария Обновленная")
        dialog.input_desc.setPlainText("Новое подробное описание.")
        dialog.input_visual_dna.setPlainText("red hair, green eyes, gothic jacket")
        app.processEvents()

        # Подавляем всплывающий QMessageBox об успехе сохранения
        with patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok):
            dialog._save_changes()
        app.processEvents()

        # Проверяем, что изменения физически записались в JSON
        with open(card_path, "r", encoding="utf-8") as f:
            updated_data = json.load(f)

        target = updated_data.get("data", updated_data)
        assert target["name"] == "Элария Обновленная"
        assert target["visual_dna"] == "red hair, green eyes, gothic jacket"

    def test_04_greetings_pagination_and_language_toggle(self, isolated_gallery):
        """
        Проверка: пагинация по альтернативным приветствиям и кнопка RU/EN.
        Ловит: IndexOutOfBounds, сбои при переключении локализаций.
        """
        dialog, _, _ = isolated_gallery

        # Проверяем начальное состояние пагинации (1 / 3)
        assert dialog.lbl_greeting_page.text() == "1 / 3"

        # Переход к следующему приветствию
        dialog._show_next_greeting()
        app.processEvents()
        assert dialog.lbl_greeting_page.text() == "2 / 3"
        assert dialog.input_first_mes.toPlainText() == "Ты что-то хотел спросить?"

        # Возврат назад
        dialog._show_prev_greeting()
        app.processEvents()
        assert dialog.lbl_greeting_page.text() == "1 / 3"

        # Переключение языка RU / ORIG
        initial_lang_text = dialog.btn_toggle_lang.text()
        dialog._toggle_card_language()
        app.processEvents()
        assert dialog.btn_toggle_lang.text() != initial_lang_text

    def test_05_chub_download_callback_pipeline(self, isolated_gallery):
        """
        Проверка: симуляция колбэка скачивания с Chub.ai.
        Ловит: сбои цепочки _on_chub_card_downloaded -> _check_and_extract_party -> dna_party_mixin.
        """
        dialog, chars_dir, _ = isolated_gallery

        # Создаем имитацию скачанного с Chub файла
        chub_card_path = chars_dir / "ChubHero.json"
        chub_payload = {
            "name": "Chub Hero",
            "description": "[Appearance: golden hair, blue eyes] A warrior from Chub catalog.",
            "personality": "Brave",
            "first_mes": "Greetings!"
        }
        with open(chub_card_path, "w", encoding="utf-8") as f:
            json.dump(chub_payload, f, ensure_ascii=False)

        # Вызываем метод-обработчик загрузки
        dialog._on_chub_card_downloaded(chub_card_path)
        app.processEvents()

        assert dialog.list_widget.count() == 2
        assert dialog.input_name.text() == "Chub Hero"
        # Проверяем, что DNA автоматически извлекся без исключений
        assert "golden hair" in dialog.input_visual_dna.toPlainText().lower()

    def test_06_delete_character_lifecycle(self, isolated_gallery):
        """
        Проверка: удаление карточки через диалог с очисткой файлов.
        Ловит: блокировки дескрипторов файлов и падения при очистке кэша.
        """
        dialog, chars_dir, card_path = isolated_gallery

        # Симулируем нажатие 'Yes' в диалоге подтверждения удаления
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
             patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok):
            dialog._delete_character()
        app.processEvents()

        assert dialog.list_widget.count() == 0, "Список не очистился после удаления"
        assert not card_path.exists(), "Файл JSON остался на диске после удаления"

    def test_07_start_session_signal_emission(self, isolated_gallery):
        """
        Проверка: нажатие кнопки 'Начать диалог' передает карточку наружу через сигнал.
        Ловит: блокировки сигналов Qt и сбои перехода в РП-чат.
        """
        dialog, _, _ = isolated_gallery

        received_cards = []
        dialog.start_session_requested.connect(lambda card: received_cards.append(card))

        with patch.object(dialog, "accept"):  # подавляем закрытие окна
            dialog._start_dialog()
        app.processEvents()

        assert len(received_cards) == 1, "Сигнал start_session_requested не был вызван"
        assert received_cards[0].name == "Тестовая Героиня"
