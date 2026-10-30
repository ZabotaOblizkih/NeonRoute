# -*- coding: utf-8 -*-
"""
tests/test_engineer_skills_dialog.py
Интеграционные тесты для центра «Знания инженера» (Skills Hub):
- Сохранение и загрузка кастомных скиллов в JSON
- Защита базовых архетипов от удаления
- Расчёт скоров и ранжирования в test_route_ranking
- Сохранение и переключение активного скилла в памяти окна и сессии
- Инстанцирование диалога и компонентов UI
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget, QMenu
from qfluentwidgets import InfoBar

from services.engineer_skill_router import (
    EngineerSkillRouter,
    CUSTOM_SKILLS_FILE,
    ENGINEER_SKILLS,
    FALLBACK_SKILL
)
from ui.controllers.avatar_controller import AvatarControllerMixin
from ui.engineer_skills_dialog import (
    EngineerSkillsDialog,
    RoutingResultBadge,
    SkillListItemWidget
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def clean_custom_skills(tmp_path):
    """Изолирует файл кастомных скиллов во время тестов."""
    original_custom_file = CUSTOM_SKILLS_FILE
    test_custom_file = tmp_path / "custom_engineer_skills.json"

    with patch("services.engineer_skill_router.CUSTOM_SKILLS_FILE", test_custom_file):
        router = EngineerSkillRouter.get_instance()
        router.reload_skills()
        yield router, test_custom_file

        # Восстановление после теста
        if test_custom_file.exists():
            test_custom_file.unlink()
        router.reload_skills()


class DummyAvatarWindow(AvatarControllerMixin, QWidget):
    """Тестовый контейнер для проверки памяти окна и меню аватара."""

    def __init__(self, mode="engineer", active_skill="auto"):
        super().__init__()
        self.current_mode = mode
        self.current_session = {
            "id": "test-session-skills-hub-1",
            "mode": mode,
            "metadata": {
                "active_engineer_skill": active_skill
            }
        }
        self.session_manager = MagicMock()
        self._open_model_sampling_dialog = MagicMock()
        self._on_search_clicked = MagicMock()
        self._on_media_clicked = MagicMock()
        self._on_open_chat_files_clicked = MagicMock()
        self._on_change_avatar_clicked = MagicMock()
        self._on_reset_avatar_clicked = MagicMock()
        self._on_rename_clicked = MagicMock()


class TestEngineerSkillRouterCustomSkills:
    """Проверка работы с кастомными скиллами в EngineerSkillRouter."""

    def test_save_and_load_custom_skill(self, clean_custom_skills):
        router, custom_file = clean_custom_skills

        custom_skill_data = {
            "skill_id": "embedded_rust_iot",
            "title": "Embedded Rust & Микроконтроллеры",
            "human_description": "Разработка прошивок для STM32 и ESP32 на Rust no_std.",
            "anchors": [
                "Напиши прошивку для микроконтроллера STM32 на Rust no_std",
                "Драйвер датчика температуры по шине I2C на Rust embedded-hal",
                "Прошивка ESP32 WiFi маяк на Rust"
            ],
            "triggers": "rust, stm32, esp32, embedded, микроконтроллер, i2c, spi",
            "architect_rules": "- Используй no_std архитектуру.\n- Выделяй слой HAL.",
            "worker_rules": "- Целевой файл main.rs.\n- Никаких std аллокаций без крайней нужды.",
            "default_artifact": "main.rs",
            "icon_path": "data/skill_icons/embedded_rust_iot.png"
        }

        router.save_custom_skill(custom_skill_data)

        # 1. Проверяем, что файл JSON создан и содержит данные
        assert custom_file.exists(), "JSON файл кастомных скиллов должен быть создан на диске"
        with open(custom_file, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        assert "embedded_rust_iot" in disk_data
        assert disk_data["embedded_rust_iot"]["title"] == "Embedded Rust & Микроконтроллеры"
        assert disk_data["embedded_rust_iot"]["is_builtin"] is False

        # 2. Проверяем, что роутер видит кастомный скилл
        all_skills = router.get_all_skills()
        assert "embedded_rust_iot" in all_skills
        meta = router.get_skill_metadata("embedded_rust_iot")
        assert meta["title"] == "Embedded Rust & Микроконтроллеры"
        assert meta["is_builtin"] is False
        assert meta["default_artifact"] == "main.rs"
        assert "no_std" in meta["architect_rules"]

    def test_builtin_skills_cannot_be_deleted(self, clean_custom_skills):
        router, _ = clean_custom_skills

        builtin_ids = list(ENGINEER_SKILLS.keys())
        for bid in builtin_ids:
            deleted = router.delete_custom_skill(bid)
            assert deleted is False, f"Базовый скилл '{bid}' не должен удаляться!"

        # Убеждаемся, что все базовые скиллы по-прежнему в реестре
        all_skills = router.get_all_skills()
        for bid in builtin_ids:
            assert bid in all_skills, f"Базовый скилл '{bid}' должен оставаться в роутере"

    def test_cannot_overwrite_builtin_skill_with_custom(self, clean_custom_skills):
        router, _ = clean_custom_skills

        malicious_skill = {
            "skill_id": "interactive_loop",
            "title": "Хакерский оверрайд",
            "anchors": ["фейк"]
        }
        with pytest.raises(ValueError, match="Нельзя перезаписать базовый"):
            router.save_custom_skill(malicious_skill)

    def test_delete_custom_skill_success(self, clean_custom_skills):
        router, custom_file = clean_custom_skills

        skill_data = {
            "skill_id": "temporary_skill_to_delete",
            "title": "Временный скилл",
            "anchors": ["тестовый якорь"]
        }
        router.save_custom_skill(skill_data)
        assert "temporary_skill_to_delete" in router.get_all_skills()

        # Удаляем
        res = router.delete_custom_skill("temporary_skill_to_delete")
        assert res is True
        assert "temporary_skill_to_delete" not in router.get_all_skills()

        # В файле JSON тоже должно быть удалено
        with open(custom_file, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        assert "temporary_skill_to_delete" not in disk_data


class TestEngineerSkillRouterRanking:
    """Проверка метода test_route_ranking."""

    def test_test_route_ranking_sorted_and_has_winner(self):
        router = EngineerSkillRouter.get_instance()
        ranking = router.test_route_ranking("Напиши аркадную игру тетрис или змейку на pygame")

        assert isinstance(ranking, list)
        assert len(ranking) >= 5

        # Проверяем структуру элементов
        for item in ranking:
            assert "skill_id" in item
            assert "title" in item
            assert "score" in item
            assert "is_winner" in item
            assert isinstance(item["score"], float)

        # Проверяем сортировку по убыванию
        scores = [item["score"] for item in ranking]
        assert scores == sorted(scores, reverse=True), "Список результатов должен быть отсортирован по убыванию score"

        # Должен быть ровно один победитель
        winners = [item for item in ranking if item["is_winner"]]
        assert len(winners) == 1, "Должен быть ровно один победитель"

        # Для игры на pygame победителем должен быть interactive_loop
        assert winners[0]["skill_id"] == "interactive_loop"
        assert winners[0]["score"] >= 0.25

    def test_test_route_ranking_empty_query_fallback(self):
        router = EngineerSkillRouter.get_instance()
        ranking = router.test_route_ranking("")

        assert isinstance(ranking, list)
        winners = [item for item in ranking if item["is_winner"]]
        assert len(winners) == 1
        assert winners[0]["skill_id"] == FALLBACK_SKILL


class TestAvatarControllerActiveSkillState:
    """Проверка фиксации активного скилла в памяти окна и сессии."""

    def test_select_engineer_skill_updates_window_memory_and_session(self, qapp):
        win = DummyAvatarWindow(mode="engineer", active_skill="auto")

        with patch("services.session_manager.SessionManager.save_session") as mock_save:
            with patch.object(InfoBar, "success"):
                win._on_select_engineer_skill("service_backend", "🌐 Сервисы и API")

                # 1. Проверяем фиксацию в оперативной памяти окна
                assert getattr(win, "_active_engineer_skill", None) == "service_backend"

                # 2. Проверяем обновление словаря сессии
                assert win.current_session["metadata"]["active_engineer_skill"] == "service_backend"

                # 3. Проверяем вызов SessionManager.save_session
                mock_save.assert_called_once_with(win.current_session)

    def test_avatar_menu_prioritizes_window_memory(self, qapp):
        win = DummyAvatarWindow(mode="engineer", active_skill="batch_artifact")

        # Насильно выставляем в память окна другой скилл
        win._active_engineer_skill = "interactive_loop"

        added_menus = []
        with patch.object(QMenu, "addMenu", side_effect=lambda sm: added_menus.append(sm)):
            with patch.object(QMenu, "exec"):
                win._show_avatar_menu(None)

        skill_menu = [m for m in added_menus if hasattr(m, "title") and "Скиллы разработки" in m.title()][0]
        actions = skill_menu.actions()

        # Галочка должна быть на interactive_loop (Игры и GUI), так как память окна имеет приоритет над сессией
        gui_act = [a for a in actions if "Игры и GUI" in a.text()][0]
        assert gui_act.text().startswith("✓"), f"Активный скилл из памяти окна должен быть помечен галочкой: {gui_act.text()}"

        # batch_artifact (Документы) НЕ должен иметь галочку
        doc_act = [a for a in actions if "Документы и отчеты" in a.text()][0]
        assert not doc_act.text().startswith("✓"), "Скилл из сессии не должен иметь галочку при наличии оверрайда в памяти"


class TestEngineerSkillsDialogUI:
    """Проверка инициализации и компонентов диалога EngineerSkillsDialog."""

    def test_dialog_creation_and_population(self, qapp):
        dlg = EngineerSkillsDialog()
        assert dlg.list_skills.count() >= 5

        # Проверяем наличие базовых архетипов в списке
        item_ids = [
            dlg.list_skills.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(dlg.list_skills.count())
        ]
        assert "interactive_loop" in item_ids
        assert "pure_logic_tdd" in item_ids
        assert "batch_artifact" in item_ids
        assert "service_backend" in item_ids
        assert "cli_automation" in item_ids

        # Проверяем, что для базового скилла кнопка Удалить выключена
        dlg._on_skill_selected(0)
        assert dlg.btn_delete.isEnabled() is False

        dlg.close()

    def test_routing_result_badge_states(self, qapp):
        badge = RoutingResultBadge()
        badge.set_idle()
        assert "💡" in badge.icon_lbl.text()

        badge.set_loading()
        assert "⏳" in badge.icon_lbl.text()

        badge.set_winner("Игры и GUI", "interactive_loop", 0.8543, True)
        assert "🎯" in badge.icon_lbl.text()
        assert "interactive_loop" in badge.text_lbl.text()
        assert "0.8543" in badge.tag_lbl.text()

        badge.set_fallback("Чистая логика", "pure_logic_tdd", 0.1200)
        assert "⚪" in badge.icon_lbl.text()
        assert "ФОЛЛБЭК" in badge.tag_lbl.text()
