# -*- coding: utf-8 -*-
"""
Depth-0 Anchor Viewer & Editor Dialog for NeonRoute Roleplay Sessions.
Refactored to Windows 11 Fluent Design (FramelessDialog, FluentTitleBar, Adaptive Palette).
"""

from typing import Optional
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
from ui.common.palette_utils import extract_adaptive_palette


class AnchorViewerDialog(FramelessDialog):
    """
    Диалог просмотра и редактирования Depth-0 якоря сцены.
    Выполнен на компонентах QFluentWidgets в темной стилистике Windows 11 Fluent Design.
    """

    def __init__(self, parent: Optional[QWidget], character_card, user_name: str, current_anchor: Optional[str] = None):
        super().__init__(parent=parent)
        self.character_card = character_card
        self.user_name = user_name or "Игрок"
        self.char_name = getattr(character_card, "name", "Собеседник") if character_card else "Собеседник"
        self.archetype = getattr(character_card, "archetype", None) or TavernParser.detect_card_archetype(character_card)
        self.default_anchor = RoleplayPromptBuilder.get_default_depth_anchor(self.character_card, self.user_name).strip()
        self.initial_anchor = (current_anchor.strip() if current_anchor and current_anchor.strip() else self.default_anchor)

        # Совместимость с кодом, который обращается к self.widget
        self.widget = self

        self.setWindowTitle("⚓ Якорь сцены (Depth-0 Директива)")
        self.resize(680, 480)
        self.setMinimumSize(580, 380)
        self.setObjectName("AnchorViewerDialog")

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
            AnchorViewerDialog#AnchorViewerDialog {{
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
            QWidget#AnchorBodyWidget {{
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
        self.body_widget.setObjectName("AnchorBodyWidget")
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

        # Описание назначения
        desc_lbl = BodyLabel(
            "Директива пристыковывается в конец сообщения игрока перед отправкой в LLM. "
            "Она фиксирует роли, запрещает годмодинг и управляет служебными тегами.",
            self.body_widget
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: #94A3B8; font-size: 12px; margin-top: 2px; margin-bottom: 4px;")
        body_layout.addWidget(desc_lbl)

        # Текстовый редактор якоря
        self.anchor_edit = PlainTextEdit(self.body_widget)
        self.anchor_edit.setPlainText(self.initial_anchor)
        body_layout.addWidget(self.anchor_edit, 1)

        # Панель вспомогательных действий
        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 4, 0, 0)
        actions_layout.setSpacing(10)

        self.btn_reset_default = PushButton(FIF.SYNC, "По умолчанию", self.body_widget)
        self.btn_reset_default.setToolTip("Восстановить эталонный абстрактный якорь для этого архетипа")
        self.btn_reset_default.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_reset_default.clicked.connect(self._on_reset_to_default)

        self.btn_copy = PushButton(FIF.COPY, "Копировать", self.body_widget)
        self.btn_copy.setToolTip("Скопировать текст директивы в буфер обмена")
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
        self.anchor_edit.setPlainText(self.default_anchor)
        InfoBar.info(
            title="Сброс",
            content="Восстановлен эталонный абстрактный якорь по умолчанию",
            parent=self,
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
                parent=self,
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
