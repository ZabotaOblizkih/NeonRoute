# -*- coding: utf-8 -*-
"""
Prompt Viewer & Editor Dialog for NeonRoute Roleplay Sessions.
Refactored to Windows 11 Fluent Design (FramelessDialog, FluentTitleBar, Adaptive Palette).
"""

from typing import Optional, Dict, Any, Union, List
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QCursor, QPixmap
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    SubtitleLabel,
    BodyLabel,
    CaptionLabel,
    PlainTextEdit,
    PushButton,
    PrimaryPushButton,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition
)
from services.roleplay_prompt_builder import RoleplayPromptBuilder
from services.tavern_parser import TavernParser
from services.session_manager import SessionManager
from ui.common.palette_utils import extract_adaptive_palette


class PromptViewerDialog(FramelessDialog):
    """
    Диалог инспекции и редактирования системного промпта карточки для текущей РП сессии.
    Выполнен на компонентах QFluentWidgets в темной стилистике Windows 11 Fluent Design.
    """

    def __init__(
        self,
        parent: Optional[QWidget],
        character_card,
        user_name: str,
        current_prompt: Optional[str] = None,
        world_facts: Union[str, List[str]] = "",
        active_persona: Optional[Dict[str, Any]] = None
    ):
        super().__init__(parent=parent)
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

        # Совместимость с кодом, который обращается к self.widget
        self.widget = self

        self.setWindowTitle("📜 Системный промпт карточки")
        self.resize(780, 640)
        self.setMinimumSize(640, 500)
        self.setObjectName("PromptViewerDialog")

        # 1. Заголовок FluentTitleBar
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(46)
        if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
            self.titleBar.minBtn.hide()
        if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
            self.titleBar.maxBtn.hide()
        if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
            self.titleBar.closeBtn.setVisible(True)
            try:
                self.titleBar.closeBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.closeBtn.clicked.connect(self.reject)

        self._setup_ui()
        self.update_adaptive_palette()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
        """Ищет активные обои в иерархии родительских окон для адаптивной палитры."""
        p = self.parent()
        while p:
            if hasattr(p, "background_pixmap") and p.background_pixmap:
                return p.background_pixmap
            if hasattr(p, "window") and hasattr(p.window, "background_pixmap") and p.window.background_pixmap:
                return p.window.background_pixmap
            if hasattr(p, "_bg_render_cache") and p._bg_render_cache:
                return p._bg_render_cache
            p = p.parent()
        return None

    def update_adaptive_palette(self):
        """Адаптирует цветовую палитру окна, карточек и полей ввода под активную тему."""
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap)

        self.setStyleSheet(f"""
            PromptViewerDialog#PromptViewerDialog {{
                background-color: {palette['bg']};
                border: 1px solid {palette['border']};
                border-radius: 14px;
            }}
            FluentTitleBar {{
                background-color: {palette['bg']};
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }}
            FluentTitleBar QLabel {{
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 13px;
            }}
            QWidget#PromptBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            PlainTextEdit {{
                background-color: rgba(15, 19, 28, 0.85);
                border: 1px solid {palette['border']};
                border-radius: 8px;
                color: #F8FAFC;
                font-family: 'Consolas', 'Cascadia Code', monospace;
                font-size: 12px;
                padding: 10px;
                line-height: 1.4;
            }}
            PlainTextEdit:focus {{
                border: 1px solid #00F0FF;
                background-color: rgba(15, 19, 28, 0.95);
            }}
        """)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Добавляем заголовок в корневой макет
        root_layout.addWidget(self.titleBar)

        # Контейнер содержимого
        self.body_widget = QWidget(self)
        self.body_widget.setObjectName("PromptBodyWidget")
        body_layout = QVBoxLayout(self.body_widget)
        body_layout.setContentsMargins(22, 12, 22, 20)
        body_layout.setSpacing(12)

        # Метаданные сцены
        meta_layout = QHBoxLayout()
        meta_layout.setSpacing(16)

        c_info = CaptionLabel(f"Персонаж: <b style='color: #00F0FF;'>{self.char_name}</b>", self.body_widget)
        u_info = CaptionLabel(f"Игрок: <b style='color: #38BDF8;'>{self.user_name}</b>", self.body_widget)
        a_info = CaptionLabel(f"Архетип: <b style='color: #A855F7;'>{self.archetype}</b>", self.body_widget)

        meta_layout.addWidget(c_info)
        meta_layout.addWidget(u_info)
        meta_layout.addWidget(a_info)
        meta_layout.addStretch()
        body_layout.addLayout(meta_layout)

        # Поясняющее описание
        desc_lbl = BodyLabel(
            "Этот системный промпт задает поведение персонажа, личность, мир и инструкции отыгрыша для LLM. "
            "Изменения применяются строго к текущей сессии.",
            self.body_widget
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: #94A3B8; font-size: 12px; margin-top: 2px; margin-bottom: 4px;")
        body_layout.addWidget(desc_lbl)

        # Редактор промпта
        self.prompt_edit = PlainTextEdit(self.body_widget)
        self.prompt_edit.setPlainText(self.initial_prompt)
        body_layout.addWidget(self.prompt_edit, 1)

        # Панель действий и кнопок
        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 4, 0, 0)
        actions_layout.setSpacing(10)

        self.btn_reset_default = PushButton(FIF.SYNC, "По умолчанию", self.body_widget)
        self.btn_reset_default.setToolTip("Сбросить к исходному системному промпту карточки")
        self.btn_reset_default.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_reset_default.clicked.connect(self._on_reset_to_default)

        self.btn_copy = PushButton(FIF.COPY, "Копировать", self.body_widget)
        self.btn_copy.setToolTip("Скопировать текст промпта в буфер обмена")
        self.btn_copy.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_copy.clicked.connect(self._on_copy_clicked)

        self.cancelButton = PushButton("Отмена", self.body_widget)
        self.cancelButton.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.cancelButton.clicked.connect(self.reject)

        self.yesButton = PrimaryPushButton("Применить", self.body_widget)
        self.yesButton.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.yesButton.clicked.connect(self.accept)

        actions_layout.addWidget(self.btn_reset_default)
        actions_layout.addWidget(self.btn_copy)
        actions_layout.addStretch()
        actions_layout.addWidget(self.cancelButton)
        actions_layout.addWidget(self.yesButton)
        body_layout.addLayout(actions_layout)

        root_layout.addWidget(self.body_widget, 1)

    def _on_reset_to_default(self):
        self.prompt_edit.setPlainText(self.default_prompt)
        InfoBar.info(
            title="Сброс",
            content="Восстановлен исходный системный промпт карточки",
            parent=self,
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
                parent=self,
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

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "titleBar") and self.titleBar:
            if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
                self.titleBar.minBtn.hide()
            if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
                self.titleBar.maxBtn.hide()
            if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
                self.titleBar.closeBtn.setVisible(True)

        self.update_adaptive_palette()

        if self.parent():
            try:
                parent_geom = self.parent().geometry()
                x = parent_geom.x() + (parent_geom.width() - self.width()) // 2
                y = parent_geom.y() + (parent_geom.height() - self.height()) // 2
                self.move(max(20, x), max(20, y))
            except Exception:
                pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
        else:
            super().keyPressEvent(event)
