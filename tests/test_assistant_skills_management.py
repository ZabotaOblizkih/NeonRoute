"""
Тесты для микро-скиллов Ассистента: реестр, кастомные скиллы, принудительный роутинг,
меню аватара и диалог управления в «Знаниях Эларии».
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from services.skill_router import SkillRouter, ASSISTANT_SKILLS, CUSTOM_ASSISTANT_SKILLS_FILE
from services.assistant_prompt_builder import AssistantPromptBuilder


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestAssistantSkillRouter:
    """Тестирование расширенного реестра и CRUD операций кастомных скиллов."""

    def test_builtin_skills_structure(self):
        router = SkillRouter(embedder=None)
        all_skills = router.get_all_skills()

        for skill_id in ("analyst_logic", "code_architect", "text_crafter", "tutor_feynman", "wellness_bio", "gallery", "deep_engineer"):
            assert skill_id in all_skills
            skill = all_skills[skill_id]
            assert skill.get("is_builtin") is True
            assert "title" in skill
            assert "human_description" in skill
            assert "triggers" in skill
            assert "system_prompt" in skill

    def test_save_and_delete_custom_skill(self, tmp_path: Path, monkeypatch):
        custom_file = tmp_path / "custom_assistant_skills.json"
        monkeypatch.setattr("services.skill_router.CUSTOM_ASSISTANT_SKILLS_FILE", custom_file)

        router = SkillRouter(embedder=None)

        # 1. Запрет перезаписи базового скилла
        bad_base = {
            "skill_id": "code_architect",
            "title": "Хак",
            "system_prompt": "..."
        }
        assert router.save_custom_skill(bad_base) is False

        # 2. Запрет создания с пустым ID или Title
        assert router.save_custom_skill({"skill_id": "", "title": "Test"}) is False
        assert router.save_custom_skill({"skill_id": "valid_id", "title": ""}) is False

        # 3. Успешное сохранение кастомного скилла
        custom_skill = {
            "skill_id": "database_tuner",
            "title": "🗄️ Оптимизатор СУБД",
            "human_description": "Тюнинг индексов и оптимизация сложных SQL запросов.",
            "triggers": "explain analyze, btree, postgres, индексы",
            "system_prompt": "Действуй как ведущий DBA. Оптимизируй планы запросов и индексы."
        }
        assert router.save_custom_skill(custom_skill) is True

        # Проверка наличия в памяти
        all_skills = router.get_all_skills()
        assert "database_tuner" in all_skills
        saved = all_skills["database_tuner"]
        assert saved["is_builtin"] is False
        assert saved["title"] == "🗄️ Оптимизатор СУБД"
        assert "database_tuner" in router.skill_anchors

        # Проверка сохранения на диске
        assert custom_file.exists()
        disk_data = json.loads(custom_file.read_text(encoding="utf-8"))
        assert "database_tuner" in disk_data

        # 4. Запрет удаления базового скилла
        assert router.delete_custom_skill("analyst_logic") is False

        # 5. Успешное удаление кастомного скилла
        assert router.delete_custom_skill("database_tuner") is True
        assert "database_tuner" not in router.get_all_skills()
        assert "database_tuner" not in router.skill_anchors

        disk_after = json.loads(custom_file.read_text(encoding="utf-8"))
        assert "database_tuner" not in disk_after


class TestAssistantPromptBuilderManualSkill:
    """Тестирование принудительной фиксации скилла в пайплайне промпта."""

    def test_pinned_skill_overrides_routing(self):
        builder = AssistantPromptBuilder.get_instance()

        # При активном "analyst_logic" даже при вопросе про код должен активироваться аналитик
        prompt = builder.build(
            user_name="Алекс",
            user_query="Напиши мне функцию на питоне def foo(): pass",
            active_assistant_skill="analyst_logic"
        )
        assert "[АКТИВНЫЙ СКИЛЛ: АНАЛИТИК И ЛОГИК" in prompt
        assert "[АКТИВНЫЙ СКИЛЛ: СИСТЕМНЫЙ РАЗРАБОТЧИК" not in prompt

    def test_pinned_skill_via_session_metadata(self):
        builder = AssistantPromptBuilder.get_instance()

        session_meta = {
            "active_assistant_skill": "tutor_feynman"
        }
        prompt = builder.build(
            user_name="Алекс",
            user_query="Привет, как дела?",
            session_metadata=session_meta
        )
        assert "[АКТИВНЫЙ СКИЛЛ: ПЕДАГОГ (МЕТОД ФЕЙНМАНА)]" in prompt

    def test_auto_skill_uses_semantic_routing(self):
        builder = AssistantPromptBuilder.get_instance()

        # auto должен маршрутизировать по тексту
        prompt = builder.build(
            user_name="Алекс",
            user_query="В чем разница между PostgreSQL и MySQL, плюсы и минусы?",
            active_assistant_skill="auto"
        )
        assert "[АКТИВНЫЙ СКИЛЛ: АНАЛИТИК И ЛОГИК" in prompt


class TestAvatarMenuAssistantSkills:
    """Тестирование контекстного меню аватара в режиме Ассистента."""

    def test_assistant_skills_submenu_and_selection(self, qapp):
        from ui.controllers.avatar_controller import AvatarControllerMixin

        class DummyAvatarHost(AvatarControllerMixin):
            def __init__(self):
                self.current_mode = "assistant"
                self.current_session = {
                    "id": "sess_1",
                    "mode": "assistant",
                    "metadata": {
                        "active_assistant_skill": "code_architect"
                    }
                }
                self._active_assistant_skill = "code_architect"
                self._engineering_mode_active = False

        host = DummyAvatarHost()
        assert host._active_assistant_skill == "code_architect"

        # Переключение на auto
        host._on_select_assistant_skill("auto", "🤖 Автоопределение")
        assert host._active_assistant_skill == "auto"
        assert host.current_session["metadata"]["active_assistant_skill"] == "auto"

        # Переключение на системного аналитика
        host._on_select_assistant_skill("analyst_logic", "📊 Системный аналитик")
        assert host._active_assistant_skill == "analyst_logic"
        assert host.current_session["metadata"]["active_assistant_skill"] == "analyst_logic"


class TestKnowledgeManagerSkillsDialogUI:
    """Тестирование вкладки «⚡ Скиллы Эларии» в окне Знаний Эларии."""

    def test_dialog_tabs_and_skills_page(self, qapp, tmp_path: Path, monkeypatch):
        from ui.dialogs.knowledge_manager_dialog import KnowledgeManagerDialog, SkillListItemWidget
        from services.knowledge_manager import KnowledgeManager

        kb_dir = tmp_path / "knowledge_base"
        kb_dir.mkdir()
        reg_file = kb_dir / "registry.json"

        monkeypatch.setattr(KnowledgeManager, "get_knowledge_dir", lambda custom_dir=None: kb_dir)
        monkeypatch.setattr(KnowledgeManager, "get_registry_path", lambda custom_path=None, custom_dir=None: reg_file)

        dlg = KnowledgeManagerDialog()

        # 1. Проверка SegmentedWidget и стека
        assert hasattr(dlg, "tab_bar")
        assert hasattr(dlg, "stack")
        assert dlg.stack.count() == 2
        assert hasattr(dlg, "docs_page")
        assert hasattr(dlg, "skills_page")

        # 2. Проверка списка скиллов
        assert dlg.list_skills.count() >= 7
        first_item = dlg.list_skills.item(0)
        assert first_item is not None
        widget = dlg.list_skills.itemWidget(first_item)
        assert isinstance(widget, SkillListItemWidget)

        # 3. Переключение на страницу скиллов
        dlg.tab_bar.setCurrentItem("tab_skills")
        assert dlg.stack.currentIndex() == 1

        # 4. Проверка формы
        assert dlg.line_skill_id.text() != ""
        assert dlg.btn_delete_skill.isEnabled() is False  # Для базового выключено

        # 5. Клик по кнопке "+ Добавить свой скилл"
        dlg.btn_add_skill.click()
        assert dlg.line_skill_id.text() == ""
        assert dlg.line_skill_id.isEnabled() is True
        assert dlg.btn_save_skill.isEnabled() is True

        dlg.close()


class TestMainWindowSkillSync:
    """Тестирование синхронизации зафиксированного скилла при смене чата."""

    def test_load_chat_history_restores_skill(self):
        from ui.main_window import MainWindow

        session_data = {
            "id": "chat_123",
            "title": "Архитектурный разбор",
            "mode": "assistant",
            "metadata": {
                "active_assistant_skill": "text_crafter"
            }
        }

        # Mock экземпляра
        class DummyWindow:
            _active_assistant_skill = "auto"
            _active_engineer_skill = "auto"

        dummy = DummyWindow()
        MainWindow._load_chat_history(dummy, session_data)

        assert dummy._active_assistant_skill == "text_crafter"
