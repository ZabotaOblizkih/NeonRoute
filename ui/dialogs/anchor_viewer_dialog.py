# -*- coding: utf-8 -*-
from typing import Optional
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from qfluentwidgets import (
    MessageBoxBase,
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


class AnchorViewerDialog(MessageBoxBase):
    """
    Диалог просмотра и редактирования Depth-0 якоря сцены.
    Выполнен на компонентах QFluentWidgets в темной стилистике NeonRoute.
    """
    def __init__(self, parent: QWidget, character_card, user_name: str, current_anchor: Optional[str] = None):
        super().__init__(parent)
        self.character_card = character_card
        self.user_name = user_name or "Игрок"
        self.char_name = getattr(character_card, "name", "Собеседник") if character_card else "Собеседник"
        self.archetype = getattr(character_card, "archetype", None) or TavernParser.detect_card_archetype(character_card)
        self.default_anchor = RoleplayPromptBuilder.get_default_depth_anchor(self.character_card, self.user_name).strip()
        self.initial_anchor = (current_anchor.strip() if current_anchor and current_anchor.strip() else self.default_anchor)

        self._init_widget()

    def _init_widget(self):
        # Настройка габаритов
        self.widget.setMinimumWidth(620)
        self.widget.setMaximumWidth(700)

        # Кастомный заголовок
        self.title_lbl = SubtitleLabel("⚓ Якорь сцены (Depth-0 Директива)", self)
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

        # Описание назначения
        desc_lbl = BodyLabel(
            "Директива пристыковывается в конец сообщения игрока перед отправкой в LLM. "
            "Она фиксирует роли, запрещает годмодинг и управляет служебными тегами.",
            self
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: #94A3B8; font-size: 12px; margin-top: 4px; margin-bottom: 6px;")
        self.viewLayout.addWidget(desc_lbl)

        # Текстовый редактор якоря
        self.anchor_edit = PlainTextEdit(self)
        self.anchor_edit.setPlainText(self.initial_anchor)
        self.anchor_edit.setMinimumHeight(140)
        self.anchor_edit.setStyleSheet("""
            PlainTextEdit {
                background-color: rgba(15, 19, 28, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                color: #F8FAFC;
                font-family: 'Consolas', 'Cascadia Code', monospace;
                font-size: 12px;
                padding: 8px;
            }
            PlainTextEdit:focus {
                border: 1px solid #00F0FF;
                background-color: rgba(15, 19, 28, 0.95);
            }
        """)
        self.viewLayout.addWidget(self.anchor_edit)

        # Панель вспомогательных действий
        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 6, 0, 0)
        actions_layout.setSpacing(10)

        self.btn_reset_default = PushButton(FIF.SYNC, "По умолчанию", self)
        self.btn_reset_default.setToolTip("Восстановить эталонный абстрактный якорь для этого архетипа")
        self.btn_reset_default.clicked.connect(self._on_reset_to_default)

        self.btn_copy = PushButton(FIF.COPY, "Копировать", self)
        self.btn_copy.setToolTip("Скопировать текст директивы в буфер обмена")
        self.btn_copy.clicked.connect(self._on_copy_clicked)

        actions_layout.addWidget(self.btn_reset_default)
        actions_layout.addWidget(self.btn_copy)
        actions_layout.addStretch()
        self.viewLayout.addLayout(actions_layout)

        # Настройка стандартных кнопок MessageBoxBase
        self.yesButton.setText("Применить")
        self.cancelButton.setText("Отмена")

    def _on_reset_to_default(self):
        self.anchor_edit.setPlainText(self.default_anchor)
        InfoBar.info(
            title="Сброс",
            content="Восстановлен эталонный абстрактный якорь по умолчанию",
            parent=self.widget,
            position=InfoBarPosition.TOP,
            duration=2000
        )

    def _on_copy_clicked(self):
        text = self.anchor_edit.toPlainText().strip()
        if text:
            QGuiApplication.clipboard().setText(text)
            InfoBar.success(
                title="Скопировано",
                content="Текст якоря помещен в буфер обмена",
                parent=self.widget,
                position=InfoBarPosition.TOP,
                duration=2000
            )

    def get_configured_anchor(self) -> Optional[str]:
        """
        Возвращает настроенный текст:
        - None, если текст совпадает с дефолтным (чистый сброс ключа в сессии).
        - Отредактированную строку, если пользователь задал кастомный текст.
        """
        current_text = self.anchor_edit.toPlainText().strip()
        if not current_text or current_text == self.default_anchor:
            return None
        return current_text
