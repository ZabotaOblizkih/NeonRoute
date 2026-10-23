"""
Sidebar Widget Component for NeonRoute.
Contains Brand Header, Roleplay Character Card,
LM Studio Model Selector, Chat Sessions List, and Quick Storage buttons.
Modernized with QFluentWidgets components under Agentrules.md.
"""

import os
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QSizePolicy
)
from qfluentwidgets import (
    ComboBox,
    PrimaryPushButton,
    PushButton,
    TransparentToolButton,
    FluentIcon as FIF,
    ToolTipFilter,
    ToolTipPosition,
)
from config import WORKSPACE_DIR, OUTPUTS_DIR, AUDIO_DIR

logger = logging.getLogger("NeonRoute.SidebarWidget")

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class SidebarWidget(QFrame):
    def __init__(self, window: 'MainWindow'):
        super().__init__(window)
        self.window = window
        self.setObjectName("SidebarWidget")
        self.setMinimumWidth(260)
        self.setMaximumWidth(320)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(14)

        # 1. App Brand Header
        brand_layout = QVBoxLayout()
        brand_layout.setSpacing(2)

        title_lbl = QLabel("⚡ NEON ROUTE", self)
        title_lbl.setStyleSheet("color: #00F0FF; font-size: 18px; font-weight: 900; letter-spacing: 1px;")
        brand_layout.addWidget(title_lbl)

        sub_lbl = QLabel("8GB VRAM ORCHESTRATOR", self)
        sub_lbl.setStyleSheet("color: #64748B; font-size: 10px; font-weight: bold; letter-spacing: 1.5px;")
        brand_layout.addWidget(sub_lbl)
        layout.addLayout(brand_layout)

        # 2. Character Card Panel (Roleplay Mode)
        self.char_card_widget = QFrame(self)
        self.char_card_widget.setObjectName("CharCardPanel")
        self.char_card_widget.setStyleSheet(
            "#CharCardPanel { background-color: #131A28; border: 1px solid #1E2B42; border-radius: 8px; padding: 8px; }"
        )
        char_layout = QVBoxLayout(self.char_card_widget)
        char_layout.setContentsMargins(8, 8, 8, 8)
        char_layout.setSpacing(8)

        self.char_name_lbl = QLabel("Персонаж не выбран", self)
        self.char_name_lbl.setStyleSheet("color: #FF007F; font-weight: bold; font-size: 13px;")
        char_layout.addWidget(self.char_name_lbl)

        self.btn_campaign_info = PushButton("📜 Правила мира и Лор ↗", self.char_card_widget)
        self.btn_campaign_info.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_campaign_info.clicked.connect(self.window._open_campaign_info_dialog)
        char_layout.addWidget(self.btn_campaign_info)

        self.btn_load_character = PushButton("🎴 Выбор компаньона", self.char_card_widget)
        self.btn_load_character.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_load_character.clicked.connect(self.window._import_character)
        char_layout.addWidget(self.btn_load_character)

        self.char_card_widget.setVisible(False)
        layout.addWidget(self.char_card_widget)

        # 3. Model Selectors (LM Studio)
        models_box = QVBoxLayout()
        models_box.setSpacing(6)

        lm_lbl = QLabel("МОДЕЛЬ LM STUDIO:", self)
        lm_lbl.setStyleSheet("color: #64748B; font-size: 10px; font-weight: bold;")
        models_box.addWidget(lm_lbl)

        lm_row = QHBoxLayout()
        self.lm_combo = ComboBox(self)
        self.lm_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.btn_refresh_models = TransparentToolButton(FIF.SYNC, self)
        self.btn_refresh_models.setFixedSize(32, 32)
        self.btn_refresh_models.setToolTip("Обновить список моделей LM Studio")
        self.btn_refresh_models.installEventFilter(
            ToolTipFilter(self.btn_refresh_models, showDelay=300, position=ToolTipPosition.TOP)
        )
        self.btn_refresh_models.clicked.connect(self.window._refresh_models)

        lm_row.addWidget(self.lm_combo)
        lm_row.addWidget(self.btn_refresh_models)
        models_box.addLayout(lm_row)
        layout.addLayout(models_box)

        # 4. Chat Sessions List
        layout.addSpacing(6)
        chats_header = QHBoxLayout()
        c_title = QLabel("ДИАЛОГИ", self)
        c_title.setStyleSheet("color: #64748B; font-size: 10px; font-weight: bold;")
        chats_header.addWidget(c_title)
        chats_header.addStretch(1)

        self.btn_new_chat = PrimaryPushButton(FIF.ADD, "Новый чат", self)
        self.btn_new_chat.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_new_chat.clicked.connect(self._handle_new_chat_clicked)
        chats_header.addWidget(self.btn_new_chat)
        layout.addLayout(chats_header)

        self.chat_list_widget = QListWidget(self)
        self.chat_list_widget.setStyleSheet("""
            QListWidget {
                background-color: #121824;
                border: 1px solid #1E273A;
                border-radius: 6px;
                color: #CBD5E1;
            }
            QListWidget::item {
                padding: 2px;
                border-bottom: 1px solid #182030;
            }
            QListWidget::item:selected {
                background-color: #1C263B;
                color: #00F0FF;
            }
        """)
        self.chat_list_widget.itemClicked.connect(self.window._on_chat_list_item_clicked)
        layout.addWidget(self.chat_list_widget)

        # 5. Quick Storage Actions
        storage_row = QHBoxLayout()
        storage_row.setSpacing(8)
        storage_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn_open_workspace = TransparentToolButton(FIF.FOLDER, self)
        btn_open_workspace.setToolTip("Рабочая папка песочницы (workspace)")
        btn_open_workspace.setFixedSize(34, 34)
        btn_open_workspace.installEventFilter(
            ToolTipFilter(btn_open_workspace, showDelay=300, position=ToolTipPosition.TOP)
        )
        btn_open_workspace.clicked.connect(lambda: SidebarWidget._safe_open_folder(WORKSPACE_DIR))

        btn_open_outputs = TransparentToolButton(FIF.PHOTO, self)
        btn_open_outputs.setToolTip("Папка сгенерированных изображений (outputs)")
        btn_open_outputs.setFixedSize(34, 34)
        btn_open_outputs.installEventFilter(
            ToolTipFilter(btn_open_outputs, showDelay=300, position=ToolTipPosition.TOP)
        )
        btn_open_outputs.clicked.connect(lambda: SidebarWidget._safe_open_folder(OUTPUTS_DIR))

        btn_open_audio = TransparentToolButton(FIF.MICROPHONE, self)
        btn_open_audio.setToolTip("Папка аудиозаписей (audio)")
        btn_open_audio.setFixedSize(34, 34)
        btn_open_audio.installEventFilter(
            ToolTipFilter(btn_open_audio, showDelay=300, position=ToolTipPosition.TOP)
        )
        btn_open_audio.clicked.connect(lambda: SidebarWidget._safe_open_folder(AUDIO_DIR))

        storage_row.addWidget(btn_open_workspace)
        storage_row.addWidget(btn_open_outputs)
        storage_row.addWidget(btn_open_audio)
        layout.addLayout(storage_row)

    def _handle_new_chat_clicked(self):
        """Открытие диалога NewChatDialog с выбором целевого режима (РП / Ассистент / Инженер)."""
        if hasattr(self.window, "show_new_chat_dialog"):
            self.window.show_new_chat_dialog()
        elif hasattr(self.window, "_open_new_chat_dialog"):
            self.window._open_new_chat_dialog()
        else:
            from ui.dialogs.new_chat_dialog import NewChatDialog
            from PyQt6.QtCore import QPoint
            from PyQt6.QtWidgets import QDialog
            dlg = NewChatDialog(parent=self.window)
            btn = self.btn_new_chat
            pos = btn.mapToGlobal(QPoint(0, btn.height() + 4))
            dlg.move(pos)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_mode:
                if hasattr(self.window, "create_chat"):
                    self.window.create_chat(mode=dlg.selected_mode)

    @staticmethod
    def _safe_open_folder(folder_path):
        try:
            p = Path(folder_path)
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(str(p.resolve()))
        except Exception as e:
            logger.warning(f"[Sidebar] Не удалось открыть папку {folder_path}: {e}")
