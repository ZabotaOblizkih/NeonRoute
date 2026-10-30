# -*- coding: utf-8 -*-
"""
tests/test_engineer_skill_menu_integration.py
Интеграционные тесты для Fluent-меню скиллов в avatar_controller.py и ручного оверрайда в engineer_planner.py.
"""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QWidget, QMenu
from qfluentwidgets import RoundMenu, InfoBar

from ui.controllers.avatar_controller import AvatarControllerMixin
from services.engineer_planner import EngineerPlanner
from services.engineer_skill_router import EngineerSkillRouter


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class DummyAvatarWindow(AvatarControllerMixin, QWidget):
    """Тестовый контейнер для проверки контекстного меню аватара."""

    def __init__(self, mode="engineer", active_skill="auto"):
        super().__init__()
        self.current_mode = mode
        self.current_session = {
            "id": "test-session-eng-1",
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


class TestAvatarControllerSkillMenu:
    """Проверка генерации Fluent-подменю скиллов разработки."""

    def test_skills_menu_present_in_engineer_mode(self, qapp):
        win = DummyAvatarWindow(mode="engineer", active_skill="auto")

        added_menus = []
        with patch.object(QMenu, "addMenu", side_effect=lambda sm: added_menus.append(sm)):
            with patch.object(QMenu, "exec"):
                win._show_avatar_menu(None)

        # Должно быть добавлено подменю скиллов
        skill_submenus = [m for m in added_menus if hasattr(m, "title") and "Скиллы разработки" in m.title()]
        assert len(skill_submenus) == 1, "Подменю '⚡ Скиллы разработки' должно быть добавлено в режиме engineer"

        sub = skill_submenus[0]
        actions = sub.actions()
        assert len(actions) == 6, f"В меню должно быть 6 пунктов (Авто + 5 скиллов), найдено {len(actions)}"

        # Проверяем наличие всех 6 архетипов
        action_texts = [a.text() for a in actions]
        assert any("Автоопределение" in t for t in action_texts)
        assert any("Игры и GUI" in t for t in action_texts)
        assert any("Чистая логика" in t for t in action_texts)
        assert any("Документы и отчеты" in t for t in action_texts)
        assert any("Сервисы и API" in t for t in action_texts)
        assert any("Консольные утилиты" in t for t in action_texts)

        # Проверяем tooltips с триггерами
        tooltips = {a.text().strip(): a.toolTip() for a in actions}
        auto_key = [k for k in tooltips if "Автоопределение" in k][0]
        gui_key = [k for k in tooltips if "Игры и GUI" in k][0]
        doc_key = [k for k in tooltips if "Документы и отчеты" in k][0]

        assert "Векторный поиск" in tooltips[auto_key]
        assert "pygame" in tooltips[gui_key].lower()
        assert "excel" in tooltips[doc_key].lower()

    def test_active_skill_checkmark_indicator(self, qapp):
        win = DummyAvatarWindow(mode="engineer", active_skill="batch_artifact")

        added_menus = []
        with patch.object(QMenu, "addMenu", side_effect=lambda sm: added_menus.append(sm)):
            with patch.object(QMenu, "exec"):
                win._show_avatar_menu(None)

        sub = [m for m in added_menus if hasattr(m, "title") and "Скиллы разработки" in m.title()][0]
        actions = sub.actions()

        # Пункт batch_artifact должен начинаться с галочки ✓
        doc_action = [a for a in actions if "Документы и отчеты" in a.text()][0]
        assert doc_action.text().startswith("✓"), f"Активный скилл должен быть помечен галочкой: {doc_action.text()}"

        # Другие пункты не должны иметь галочку
        other_actions = [a for a in actions if "Документы и отчеты" not in a.text()]
        for oa in other_actions:
            assert not oa.text().startswith("✓"), f"Неактивный скилл не должен иметь галочку: {oa.text()}"

    def test_skills_menu_absent_in_assistant_mode(self, qapp):
        win = DummyAvatarWindow(mode="assistant")

        added_menus = []
        with patch.object(QMenu, "addMenu", side_effect=lambda sm: added_menus.append(sm)):
            with patch.object(QMenu, "exec"):
                win._show_avatar_menu(None)

        skill_submenus = [m for m in added_menus if hasattr(m, "title") and "Скиллы разработки" in m.title()]
        assert len(skill_submenus) == 0, "Подменю скиллов разработки НЕ должно отображаться в режиме assistant"

    def test_skills_menu_absent_in_roleplay_mode(self, qapp):
        win = DummyAvatarWindow(mode="roleplay")

        added_menus = []
        with patch.object(QMenu, "addMenu", side_effect=lambda sm: added_menus.append(sm)):
            with patch.object(QMenu, "exec"):
                win._show_avatar_menu(None)

        skill_submenus = [m for m in added_menus if hasattr(m, "title") and "Скиллы разработки" in m.title()]
        assert len(skill_submenus) == 0, "Подменю скиллов разработки НЕ должно отображаться в режиме roleplay"


class TestEngineerSkillSelection:
    """Проверка сохранения скилла и отсутствия сайдкар-файлов."""

    def test_on_select_engineer_skill_saves_metadata(self, qapp, tmp_path):
        win = DummyAvatarWindow(mode="engineer", active_skill="auto")

        # До сохранения
        assert win.current_session["metadata"]["active_engineer_skill"] == "auto"

        with patch.object(InfoBar, "success") as mock_infobar:
            win._on_select_engineer_skill("service_backend", "🌐 Сервисы и API")

            # Проверяем обновление в памяти
            assert win.current_session["metadata"]["active_engineer_skill"] == "service_backend"

            # Проверяем вызов session_manager.save_session
            win.session_manager.save_session.assert_called_once_with(win.current_session)

            # Проверяем вызов InfoBar
            mock_infobar.assert_called_once()
            call_kwargs = mock_infobar.call_args[1]
            assert "Сервисы и API" in call_kwargs.get("content", "")

    def test_on_select_engineer_skill_empty_session_safe(self, qapp):
        win = DummyAvatarWindow(mode="engineer")
        win.current_session = None

        # Не должно вызывать исключений
        win._on_select_engineer_skill("pure_logic_tdd", "⚙️ Чистая логика (TDD)")


class TestEngineerPlannerManualOverride:
    """Проверка ручного оверрайда роутинга в EngineerPlanner."""

    def test_manual_override_via_session_metadata(self, tmp_path):
        # Запрос очевидно про pygame/игры, но пользователь принудительно выбрал batch_artifact
        plan = EngineerPlanner.plan_architecture(
            task_prompt="Сделай аркадную игру змейка на pygame с управлением стрелками",
            ws_dir=tmp_path,
            session_metadata={"active_engineer_skill": "batch_artifact"},
        )
        assert plan.skill_id == "batch_artifact", (
            f"Ожидался принудительный скилл 'batch_artifact', получен '{plan.skill_id}'"
        )

    def test_manual_override_via_active_engineer_skill_kwarg(self, tmp_path):
        plan = EngineerPlanner.plan_architecture(
            task_prompt="Рассчитай фибоначчи и числа простых множителей",
            ws_dir=tmp_path,
            active_engineer_skill="cli_automation",
        )
        assert plan.skill_id == "cli_automation", (
            f"Ожидался принудительный скилл 'cli_automation', получен '{plan.skill_id}'"
        )

    def test_auto_uses_vector_routing(self, tmp_path):
        # При 'auto' должен сработать векторный роутер и выбрать interactive_loop для змейки на pygame
        plan = EngineerPlanner.plan_architecture(
            task_prompt="Сделай аркадную игру змейка на pygame с управлением стрелками",
            ws_dir=tmp_path,
            session_metadata={"active_engineer_skill": "auto"},
        )
        assert plan.skill_id == "interactive_loop", (
            f"При 'auto' должен векторно определиться 'interactive_loop', получен '{plan.skill_id}'"
        )

    def test_invalid_skill_falls_back_to_vector_routing(self, tmp_path):
        # Неизвестный скилл должен игнорироваться и использовать векторный роутер
        plan = EngineerPlanner.plan_architecture(
            task_prompt="Сделай REST API микросервис на FastAPI с эндпоинтом /health",
            ws_dir=tmp_path,
            session_metadata={"active_engineer_skill": "non_existent_skill_xyz"},
        )
        assert plan.skill_id == "service_backend", (
            f"При неизвестном скилле должен сработать векторный роутер ('service_backend'), получен '{plan.skill_id}'"
        )
