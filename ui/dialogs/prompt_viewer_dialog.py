# -*- coding: utf-8 -*-
from typing import Optional, Dict, Any, Union, List
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from qfluentwidgets import (
    MessageBoxBase,
    SubtitleLabel,
    BodyLabel,
    CaptionLabel,
    PlainTextEdit,
    PushButton,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition
)
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.tavern_parser import TavernParser
from services.session_manager import SessionManager


class PromptViewerDialog(MessageBoxBase):
    """
    Диалог инспекции и редактирования системного промпта карточки для текущей РП сессии.
    Выполнен на компонентах QFluentWidgets в темной стилистике NeonRoute.
    """
    def __init__(
        self,
        parent: QWidget,
        character_card,
        user_name: str,
        current_prompt: Optional[str] = None,
        world_facts: Union[str, List[str]] = "",
        active_persona: Optional[Dict[str, Any]] = None
    ):
        super().__init__(parent)
        self.character_card = character_card
        self.user_name = user_name or "Игрок"

        if isinstance(character_card, dict):
            self.char_name = character_card.get("name", "Собеседник") or "Собеседник"
            self.archetype = character_card.get("archetype") or TavernParser.detect_card_archetype(character_card)
        else:
            self.char_name = getattr(character_card, "name", "Собеседник") if character_card else "Собеседник"
            self.archetype = getattr(character_card, "archetype", None) or TavernParser.detect_card_archetype(character_card)

        if isinstance(self.archetype, dict):
            active_flags = [k.replace("is_", "").capitalize() for k, v in self.archetype.items() if v is True]
            self.archetype = active_flags[0] if active_flags else "Solo"

        if active_persona is None and parent and hasattr(parent, "current_session") and parent.current_session:
            sess_persona_id = parent.current_session.get("user_persona_id")
            active_persona = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=sess_persona_id)

        # Нормализация world_facts
        if isinstance(world_facts, list):
            wf_str = "\n".join(str(f) for f in world_facts if f)
        else:
            wf_str = str(world_facts or "")

        # Получаем исходный эталонный промпт для карточки
        self.default_prompt = RoleplayPromptBuilder.get_default_system_prompt(
            self.character_card, 
            user_name=self.user_name,
            world_facts=wf_str,
            active_persona=active_persona
        ).strip()
        
        self.initial_prompt = (current_prompt.strip() if current_prompt and current_prompt.strip() else self.default_prompt)

        self._init_widget()

    def _init_widget(self):
        # Настройка размеров окна под комфортное чтение промптов
        self.widget.setMinimumWidth(720)
        self.widget.setMaximumWidth(850)

        # Заголовок
        self.title_lbl = SubtitleLabel("📜 Системный промпт карточки", self)
        self.viewLayout.addWidget(self.title_lbl)

        # Метаданные сцены
        meta_layout = QHBoxLayout()
        meta_layout.setSpacing(16)

        c_info = CaptionLabel(f"Персонаж: <b style='color: #00F0FF;'>{self.char_name}</b>", self)
        u_info = CaptionLabel(f"Игрок: <b style='color: #38BDF8;'>{self.user_name}</b>", self)
        a_info = CaptionLabel(f"Архетип: <b style='color: #A855F7;'>{self.archetype}</b>", self)

        meta_layout.addWidget(c_info)
        meta_layout.addWidget(u_info)
        meta_layout.addWidget(a_info)
        meta_layout.addStretch()
        self.viewLayout.addLayout(meta_layout)

        # Поясняющее описание
        desc_lbl = BodyLabel(
            "Этот системный промпт задает поведение персонажа, личность, мир и инструкции отыгрыша для LLM. "
            "Изменения применяются строго к текущей сессии.",
            self
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: #94A3B8; font-size: 12px; margin-top: 4px; margin-bottom: 6px;")
        self.viewLayout.addWidget(desc_lbl)

        # Редактор промпта
        self.prompt_edit = PlainTextEdit(self)
        self.prompt_edit.setPlainText(self.initial_prompt)
        self.prompt_edit.setMinimumHeight(320)
        self.prompt_edit.setStyleSheet("""
            PlainTextEdit {
                background-color: rgba(15, 19, 28, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                color: #F8FAFC;
                font-family: 'Consolas', 'Cascadia Code', monospace;
                font-size: 12px;
                padding: 10px;
                line-height: 1.4;
            }
            PlainTextEdit:focus {
                border: 1px solid #00F0FF;
                background-color: rgba(15, 19, 28, 0.95);
            }
        """)
        self.viewLayout.addWidget(self.prompt_edit)

        # Панель вспомогательных кнопок
        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 6, 0, 0)
        actions_layout.setSpacing(10)

        self.btn_reset_default = PushButton(FIF.SYNC, "По умолчанию", self)
        self.btn_reset_default.setToolTip("Сбросить к исходному системному промпту карточки")
        self.btn_reset_default.clicked.connect(self._on_reset_to_default)

        self.btn_copy = PushButton(FIF.COPY, "Копировать", self)
        self.btn_copy.setToolTip("Скопировать текст промпта в буфер обмена")
        self.btn_copy.clicked.connect(self._on_copy_clicked)

        actions_layout.addWidget(self.btn_reset_default)
        actions_layout.addWidget(self.btn_copy)
        actions_layout.addStretch()
        self.viewLayout.addLayout(actions_layout)

        # Кнопки MessageBoxBase
        self.yesButton.setText("Применить")
        self.cancelButton.setText("Отмена")

    def _on_reset_to_default(self):
        self.prompt_edit.setPlainText(self.default_prompt)
        InfoBar.info(
            title="Сброс",
            content="Восстановлен исходный системный промпт карточки",
            parent=self.widget,
            position=InfoBarPosition.TOP,
            duration=2000
        )

    def _on_copy_clicked(self):
        text = self.prompt_edit.toPlainText().strip()
        if text:
            QGuiApplication.clipboard().setText(text)
            InfoBar.success(
                title="Скопировано",
                content="Системный промпт помещен в буфер обмена",
                parent=self.widget,
                position=InfoBarPosition.TOP,
                duration=2000
            )

    def get_configured_prompt(self) -> Optional[str]:
        """
        Возвращает:
        - None, если текст совпадает с дефолтным (чистый сброс ключа в сессии).
        - Отредактированную строку, если пользователь задал кастомный промпт.
        """
        current_text = self.prompt_edit.toPlainText().strip()
        if not current_text or current_text == self.default_prompt:
            return None
        return current_text
