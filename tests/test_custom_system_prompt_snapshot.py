"""
Unit tests for Custom System Prompt Snapshot & KnowledgeManagerDialog 3rd tab.
"""
import sys
import pytest
from pathlib import Path
from PyQt6.QtWidgets import QApplication

from utils.config_manager import ConfigManager, DEFAULT_SETTINGS
from services.session_manager import SessionManager
from services.assistant_prompt_builder import AssistantPromptBuilder
from services.context_window_builder import ContextWindowBuilder


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


def test_config_manager_default_key():
    """Проверка наличия ключа assistant_custom_prompt_template в DEFAULT_SETTINGS."""
    assert "assistant_custom_prompt_template" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["assistant_custom_prompt_template"] == ""


def test_session_manager_snapshot_isolation(tmp_path: Path, monkeypatch):
    """
    Проверка снимка системного промпта в SessionManager:
    1. Новая сессия захватывает шаблон из конфига.
    2. Изменение конфига не затрагивает уже созданную сессию (Snapshot).
    3. Явная передача custom_system_prompt переопределяет конфиг.
    """
    mock_cfg = {"assistant_custom_prompt_template": "Шаблон Ассистента V1: {current_dt_str}"}
    monkeypatch.setattr(ConfigManager, "load_config", lambda: mock_cfg.copy())

    # Создаем сессию 1
    sess1 = SessionManager.create_session(mode="assistant")
    assert sess1.get("custom_system_prompt") == "Шаблон Ассистента V1: {current_dt_str}"

    # Меняем конфиг на V2
    mock_cfg["assistant_custom_prompt_template"] = "Шаблон Ассистента V2: {current_dt_str}"

    # Создаем сессию 2
    sess2 = SessionManager.create_session(mode="assistant")
    assert sess2.get("custom_system_prompt") == "Шаблон Ассистента V2: {current_dt_str}"

    # Сессия 1 сохранила свой снимок V1 (Изоляция старых сессий)
    assert sess1.get("custom_system_prompt") == "Шаблон Ассистента V1: {current_dt_str}"

    # Сессия с явно переданным промптом
    sess3 = SessionManager.create_session(mode="assistant", custom_system_prompt="Явный промпт V3")
    assert sess3.get("custom_system_prompt") == "Явный промпт V3"


def test_assistant_prompt_builder_custom_prompt():
    """
    Проверка подстановки custom_prompt в AssistantPromptBuilder:
    1. С плейсхолдером {current_dt_str}.
    2. Без плейсхолдера (дата добавляется в конец).
    3. Пустой / пробельный промпт (fallback к SYSTEM_PERSONA_TEMPLATE).
    """
    # 1. С плейсхолдером
    tmpl_with_dt = "Персона: Элария-Тест. Время: {current_dt_str}."
    built = AssistantPromptBuilder.build(custom_prompt=tmpl_with_dt)
    assert "Персона: Элария-Тест." in built
    assert "Время:" in built
    assert "{current_dt_str}" not in built

    # 2. Без плейсхолдера
    tmpl_no_dt = "Персона: Элария-Тест-Без-Даты."
    built_no_dt = AssistantPromptBuilder.build(custom_prompt=tmpl_no_dt)
    assert "Персона: Элария-Тест-Без-Даты." in built_no_dt
    assert "Текущая дата и время:" in built_no_dt

    # 3. Пустой / whitespace промпт (fallback)
    built_default = AssistantPromptBuilder.build(custom_prompt="   ")
    assert "элария" in built_default.lower()
    assert "напарник" in built_default.lower()


def test_context_window_builder_passes_custom_prompt(monkeypatch):
    """
    Проверка проброса снимка custom_system_prompt из сессии через ContextWindowBuilder.build_system_prompt.
    """
    custom_text = "Тестовый снимок персоны из сессии: {current_dt_str}"
    sess = SessionManager.create_session(mode="assistant", custom_system_prompt=custom_text)
    sess_id = sess["id"]

    sys_prompt = ContextWindowBuilder.build_system_prompt(
        is_roleplay=False,
        session_id=sess_id
    )
    assert "Тестовый снимок персоны из сессии:" in sys_prompt
    assert "{current_dt_str}" not in sys_prompt


def test_knowledge_manager_dialog_prompt_tab(qapp, monkeypatch, tmp_path: Path):
    """
    Проверка вкладки «⚙️ Системный промпт» в KnowledgeManagerDialog:
    - 3 вкладки в tab_bar
    - prompt_editor загружает значение
    - кнопки сброса и сохранения
    """
    from ui.dialogs.knowledge_manager_dialog import KnowledgeManagerDialog
    from services.knowledge_manager import KnowledgeManager

    kb_dir = tmp_path / "knowledge_base"
    kb_dir.mkdir()
    reg_file = kb_dir / "registry.json"

    monkeypatch.setattr(KnowledgeManager, "get_knowledge_dir", lambda custom_dir=None: kb_dir)
    monkeypatch.setattr(KnowledgeManager, "get_registry_path", lambda custom_path=None, custom_dir=None: reg_file)

    saved_config = {"assistant_custom_prompt_template": "Кастомный промпт из конфига {current_dt_str}"}
    monkeypatch.setattr(ConfigManager, "load_config", lambda: saved_config.copy())

    def mock_save_config(cfg):
        saved_config.clear()
        saved_config.update(cfg)

    monkeypatch.setattr(ConfigManager, "save_config", mock_save_config)

    dlg = KnowledgeManagerDialog()

    # 1. Проверка вкладок
    assert dlg.stack.count() == 3
    assert hasattr(dlg, "prompt_page")
    assert hasattr(dlg, "prompt_editor")
    assert hasattr(dlg, "btn_reset_prompt")
    assert hasattr(dlg, "btn_save_prompt")

    # 2. Проверка загрузки текста
    assert dlg.prompt_editor.toPlainText().strip() == "Кастомный промпт из конфига {current_dt_str}"

    # 3. Переключение на вкладку tab_prompt
    dlg.tab_bar.setCurrentItem("tab_prompt")
    assert dlg.stack.currentIndex() == 2

    # 4. Проверка сброса к заводскому
    dlg.btn_reset_prompt.click()
    assert dlg.prompt_editor.toPlainText().strip() == AssistantPromptBuilder.SYSTEM_PERSONA_TEMPLATE.strip()

    # 5. Проверка сохранения
    dlg.prompt_editor.setPlainText("Новый сохраненный промпт для новых сессий")
    dlg.btn_save_prompt.click()
    assert saved_config.get("assistant_custom_prompt_template") == "Новый сохраненный промпт для новых сессий"
