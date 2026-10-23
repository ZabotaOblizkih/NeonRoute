import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog, QWidget

# Инициализация Qt Application
app = QApplication.instance() or QApplication([])

import config
import ui.dialogs.gallery as gallery_pkg
from ui.dialogs.characters_gallery_dialog import CharactersGalleryDialog
from ui.dialogs.gallery.dna_party_mixin import GalleryDnaPartyMixin
from ui.dialogs.gallery.translation_mixin import GalleryTranslationMixin
from services.tavern_parser import CharacterCard, TavernParser


@pytest.fixture
def gallery_test_env(tmp_path, monkeypatch):
    """
    Создает изолированную среду во временной папке:
    - Подменяет CHARACTERS_DIR и кэш миниатюр.
    - Создает тестовые карточки (одиночную и групповую).
    - Предоставляет настроенный экземпляр CharactersGalleryDialog с полноценным mock_parent.
    """
    temp_chars = tmp_path / "characters"
    temp_chars.mkdir(parents=True, exist_ok=True)
    temp_cache = tmp_path / "cache" / "gallery_thumbs"
    temp_cache.mkdir(parents=True, exist_ok=True)

    # Изоляция путей
    monkeypatch.setattr(config, "CHARACTERS_DIR", temp_chars)
    import ui.dialogs.characters_gallery_dialog as cgd
    monkeypatch.setattr(cgd, "CHARACTERS_DIR", temp_chars)
    monkeypatch.setattr(cgd, "GALLERY_THUMBS_DIR", temp_cache)
    import services.tavern_parser as tp
    monkeypatch.setattr(tp, "CHARACTERS_DIR", temp_chars)

    # 1. Одиночная карточка
    solo_card_data = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "Селеста",
            "description": "[Appearance: short silver hair, purple eyes, slender build] Механик нижних уровней.",
            "personality": "Изобретательная, саркастичная",
            "scenario": "Мастерская в трущобах под дождем",
            "first_mes": "Чего тебе? Инструменты не трогай.",
            "first_mes_ru": "Чего тебе? Инструменты не трогай.",
            "mes_example": "<START>\n{{char}}: Давай быстрее.",
            "visual_dna": "",
            "scenery_dna": ""
        }
    }
    solo_path = temp_chars / "Селеста.json"
    with open(solo_path, "w", encoding="utf-8") as f:
        json.dump(solo_card_data, f, ensure_ascii=False, indent=2)

    # 2. Групповая карточка (отряд сопартийцев)
    party_card_data = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "Отряд Наёмниц",
            "description": "Элитная боевая группа.",
            "personality": "Слаженная команда профессионалов.",
            "scenario": "Заброшенный склад корпорации",
            "first_mes": "Мы на позиции.",
            "tags": ["multiple characters", "mercenaries"],
            "is_group": True,
            "companions": [
                {"name": "Вайпер", "role": "Снайпер", "visual_dna": "1girl, short black hair, cybernetic eye"},
                {"name": "Нова", "role": "Штурмовик", "visual_dna": "1girl, blonde ponytail, heavy armor"}
            ],
            "extensions": {
                "neonroute": {
                    "companions": [
                        {"name": "Вайпер", "role": "Снайпер", "visual_dna": "1girl, short black hair, cybernetic eye"},
                        {"name": "Нова", "role": "Штурмовик", "visual_dna": "1girl, blonde ponytail, heavy armor"}
                    ]
                }
            }
        }
    }
    party_path = temp_chars / "Отряд Наёмниц.json"
    with open(party_path, "w", encoding="utf-8") as f:
        json.dump(party_card_data, f, ensure_ascii=False, indent=2)

    # Полноценный mock_parent для совместимости с очередью и задачами
    mock_parent = QWidget()
    mock_parent.task_queue = MagicMock()
    mock_parent.lm_client = MagicMock()
    mock_parent.forge_client = MagicMock()
    mock_parent.art_director = MagicMock()
    mock_parent.current_session = None
    mock_parent._active_tasks = {}
    mock_parent.selected_forge_checkpoint = None

    dialog = CharactersGalleryDialog(parent=mock_parent)
    dialog.show()
    app.processEvents()

    yield {
        "dialog": dialog,
        "chars_dir": temp_chars,
        "solo_path": solo_path,
        "party_path": party_path,
        "parent": mock_parent
    }

    dialog.close()
    mock_parent.deleteLater()
    app.processEvents()


class TestGalleryExtractedModulesE2E:

    def test_01_package_exports_and_class_inheritance(self):
        """
        Проверка архитектурной целостности:
        - Пакет ui.dialogs.gallery экспортирует все заявленные миксины.
        - CharactersGalleryDialog корректно наследует GalleryDnaPartyMixin и GalleryTranslationMixin.
        """
        assert hasattr(gallery_pkg, "GalleryDnaPartyMixin")
        assert hasattr(gallery_pkg, "GalleryTranslationMixin")
        assert issubclass(CharactersGalleryDialog, GalleryDnaPartyMixin)
        assert issubclass(CharactersGalleryDialog, GalleryTranslationMixin)

    def test_02_dna_party_mixin_solo_character_pipeline(self, gallery_test_env):
        """
        Тестирование GalleryDnaPartyMixin на одиночной карточке:
        - Выбор персонажа.
        - Автоизвлечение Visual DNA без ошибок.
        - Панель отряда сопартийцев СТРОГО скрыта (N <= 1).
        - Корректная запись параметров на диск.
        """
        dialog = gallery_test_env["dialog"]
        solo_path = gallery_test_env["solo_path"]

        items = [dialog.list_widget.item(i) for i in range(dialog.list_widget.count())]
        solo_item = next(it for it in items if "Селеста" in it.text())

        dialog._pending_visual_setup_path = str(solo_path.resolve())
        dialog._on_character_selected(solo_item)
        app.processEvents()

        # 1. Проверка поля Visual DNA
        dna_text = dialog.input_visual_dna.toPlainText().lower()
        assert "silver hair" in dna_text or "purple eyes" in dna_text
        assert not dialog.sec_dna.isHidden()

        # 2. Проверка, что панель сопартийцев скрыта
        assert dialog.party_selector_container.isHidden()
        assert dialog.btn_gallery_generate_art.text() == "🎨 Сгенерировать арт"

        # 3. Проверка сохранения в JSON на диске
        with open(solo_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        saved_dna = data.get("data", data).get("visual_dna", "")
        assert "silver hair" in saved_dna

    def test_03_dna_party_mixin_party_character_pipeline(self, gallery_test_env):
        """
        Тестирование GalleryDnaPartyMixin на групповой карточке:
        - Динамический селектор отряда виден (N > 1).
        - Переключение между сопартийцами (чипы) обновляет input_visual_dna и меняет стиль кнопок.
        - Режим 'Общий кадр' объединяет теги всех спутников через BREAK.
        """
        dialog = gallery_test_env["dialog"]

        items = [dialog.list_widget.item(i) for i in range(dialog.list_widget.count())]
        party_item = next(it for it in items if "Отряд Наёмниц" in it.text())
        dialog._on_character_selected(party_item)
        app.processEvents()

        # 1. Панель отряда должна быть активна
        assert not dialog.party_selector_container.isHidden()
        assert len(dialog.party_buttons) == 3  # Вайпер, Нова, Общий кадр

        # 2. Проверяем выбор первого спутника (Вайпер)
        assert dialog.active_companion_idx == 0
        assert "cybernetic eye" in dialog.input_visual_dna.toPlainText()
        assert "Вайпер" in dialog.btn_gallery_generate_art.text()

        # 3. Переключаемся на второго спутника (Нова)
        dialog._on_companion_chip_clicked(1)
        app.processEvents()
        assert dialog.active_companion_idx == 1
        assert "blonde ponytail" in dialog.input_visual_dna.toPlainText()
        assert "Нова" in dialog.btn_gallery_generate_art.text()

        # 4. Переключаемся на 'Общий кадр' (индекс -1)
        dialog._on_companion_chip_clicked(-1)
        app.processEvents()
        assert dialog.active_companion_idx == -1
        assert "BREAK" in dialog.input_visual_dna.toPlainText()
        assert "Отряд" in dialog.btn_gallery_generate_art.text()

    def test_04_dna_party_mixin_direct_art_dispatch(self, gallery_test_env):
        """
        Тестирование вызова генерации арта напрямую из галереи (_on_gallery_generate_art_clicked):
        - Сборка VisualIntent с текущим активным сопартийцем.
        - Передача задачи обработчику арт-директора родительского окна.
        """
        dialog = gallery_test_env["dialog"]
        parent = gallery_test_env["parent"]

        items = [dialog.list_widget.item(i) for i in range(dialog.list_widget.count())]
        solo_item = next(it for it in items if "Селеста" in it.text())
        dialog._on_character_selected(solo_item)
        dialog.input_visual_dna.setPlainText("silver hair, purple eyes")
        app.processEvents()

        parent._on_scene_art_requested = MagicMock()

        dialog._on_gallery_generate_art_clicked()
        app.processEvents()

        assert parent._on_scene_art_requested.called
        call_kwargs = parent._on_scene_art_requested.call_args.kwargs
        assert "visual_intent" in call_kwargs
        assert call_kwargs["visual_intent"].mode == "roleplay"

    def test_05_dna_party_mixin_lora_callbacks(self, gallery_test_env):
        """
        Тестирование внедрения тегов LoRA в поле внешности персонажа (_on_lora_installed_for_card):
        - Автоматическое формирование <lora:filename:weight> и триггерных слов.
        - Безопасное обновление поля и сохранение карточки.
        """
        dialog = gallery_test_env["dialog"]

        items = [dialog.list_widget.item(i) for i in range(dialog.list_widget.count())]
        solo_item = next(it for it in items if "Селеста" in it.text())
        dialog._on_character_selected(solo_item)
        app.processEvents()

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog._on_lora_installed_for_card(
                file_stem="cyber_tactical_v1",
                character_name="Селеста",
                trained_words=["tactical vest", "goggles"]
            )
        app.processEvents()

        current_dna = dialog.input_visual_dna.toPlainText()
        assert "<lora:cyber_tactical_v1:0.8>" in current_dna
        assert "tactical vest" in current_dna

    def test_06_translation_mixin_toggle_and_state(self, gallery_test_env):
        """
        Тестирование GalleryTranslationMixin:
        - Переключение языка карточки (RU / EN или ORIG).
        - Проверка смены текста кнопки btn_toggle_lang.
        - Корректная смена внутреннего флага _showing_ru.
        """
        dialog = gallery_test_env["dialog"]

        items = [dialog.list_widget.item(i) for i in range(dialog.list_widget.count())]
        solo_item = next(it for it in items if "Селеста" in it.text())
        dialog._on_character_selected(solo_item)
        app.processEvents()

        initial_state = dialog._showing_ru
        initial_lang_btn = dialog.btn_toggle_lang.text()

        # 1. Первое переключение языка
        dialog._toggle_card_language()
        app.processEvents()
        assert dialog._showing_ru != initial_state
        assert dialog.btn_toggle_lang.text() != initial_lang_btn

        # 2. Второе переключение (возврат)
        dialog._toggle_card_language()
        app.processEvents()
        assert dialog._showing_ru == initial_state
        assert any(badge in dialog.btn_toggle_lang.text() for badge in ("RU", "EN", "ORIG"))

    def test_07_dialog_lifecycle_and_cleanup(self, gallery_test_env):
        """
        Тестирование безопасного закрытия и очистки фоновых задач:
        - Проверка _cleanup_dialog_tasks при closeEvent/reject.
        - Отключение Qt-сигналов очереди задач во избежание утечек памяти и RuntimeError.
        """
        dialog = gallery_test_env["dialog"]

        mock_abort = MagicMock()
        dialog._active_dialog_tasks["task_test_01"] = {"abort_fn": mock_abort}

        dialog.reject()
        app.processEvents()

        assert mock_abort.called
        assert len(dialog._active_dialog_tasks) == 0
        assert len(dialog._running_analysis_tasks) == 0
