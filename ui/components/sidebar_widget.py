"""
Sidebar Widget Component for NeonRoute.
Contains Brand Header, Mode Switcher Tabs, Roleplay Character Card,
LM Studio Model Selector, Chat Sessions List, and Quick Storage buttons.
"""

import os
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QListWidget, QSizePolicy
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



        # 3. Character Card Panel (Roleplay Mode)
        self.char_card_widget = QFrame(self)
        self.char_card_widget.setStyleSheet(
            "background-color: #131A28; border: 1px solid #1E2B42; border-radius: 8px; padding: 8px;"
        )
        char_layout = QVBoxLayout(self.char_card_widget)
        char_layout.setContentsMargins(8, 8, 8, 8)
        char_layout.setSpacing(8)

        self.char_name_lbl = QLabel("Персонаж не выбран", self)
        self.char_name_lbl.setStyleSheet("color: #FF007F; font-weight: bold; font-size: 13px;")
        char_layout.addWidget(self.char_name_lbl)

        rp_btn_qss = """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(26, 36, 56, 0.9), stop:1 rgba(15, 22, 35, 0.9));
                color: #38BDF8;
                border: 1px solid rgba(56, 189, 248, 0.4);
                border-radius: 6px;
                padding: 7px 10px;
                font-size: 11px;
                font-weight: 600;
                text-align: center;
            }
            QPushButton:hover {
                background: rgba(56, 189, 248, 0.2);
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton:disabled {
                background: rgba(15, 20, 30, 0.6);
                border-color: rgba(255, 255, 255, 0.08);
                color: #64748B;
            }
        """

        self.btn_campaign_info = QPushButton("📜 Правила мира и Лор ↗", self)
        self.btn_campaign_info.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_campaign_info.setStyleSheet(rp_btn_qss)
        self.btn_campaign_info.clicked.connect(self.window._open_campaign_info_dialog)
        char_layout.addWidget(self.btn_campaign_info)

        self.btn_load_character = QPushButton("🎴 Выбор компаньона", self)
        self.btn_load_character.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_load_character.setStyleSheet(rp_btn_qss)
        self.btn_load_character.clicked.connect(self.window._import_character)
        char_layout.addWidget(self.btn_load_character)

        self.char_card_widget.setVisible(False)
        layout.addWidget(self.char_card_widget)

        # 4. Model Selectors (LM Studio)
        models_box = QVBoxLayout()
        models_box.setSpacing(6)

        lm_lbl = QLabel("МОДЕЛЬ LM STUDIO:", self)
        lm_lbl.setStyleSheet("color: #64748B; font-size: 10px; font-weight: bold;")
        models_box.addWidget(lm_lbl)

        lm_row = QHBoxLayout()
        self.lm_combo = QComboBox(self)
        self.lm_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.btn_refresh_models = QPushButton("🔄", self)
        self.btn_refresh_models.setFixedSize(30, 30)
        self.btn_refresh_models.setToolTip("Обновить список моделей LM Studio")
        self.btn_refresh_models.clicked.connect(self.window._refresh_models)

        lm_row.addWidget(self.lm_combo)
        lm_row.addWidget(self.btn_refresh_models)
        models_box.addLayout(lm_row)
        layout.addLayout(models_box)

        # 5. Chat Sessions List
        layout.addSpacing(10)
        chats_header = QHBoxLayout()
        c_title = QLabel("ДИАЛОГИ", self)
        c_title.setStyleSheet("color: #64748B; font-size: 10px; font-weight: bold;")
        chats_header.addWidget(c_title)

        self.btn_new_chat = QPushButton("Новый чат", self)
        self.btn_new_chat.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self.btn_new_chat.clicked.connect(self.window.show_new_chat_dialog)
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

        # 6. Quick Storage Actions
        storage_row = QHBoxLayout()
        storage_row.setSpacing(8)
        storage_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        action_btn_qss = """
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(30, 41, 59, 0.75), stop:1 rgba(15, 23, 42, 0.85));
                color: #E2E8F0;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-bottom: 2px solid rgba(0, 0, 0, 0.4);
                border-radius: 8px;
                font-size: 15px;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(56, 189, 248, 0.25), stop:1 rgba(30, 41, 59, 0.85));
                border: 1px solid #00F0FF;
                border-bottom: 2px solid #00C8D7;
                color: #00F0FF;
            }
            QPushButton:pressed {
                background-color: rgba(15, 23, 42, 0.95);
                border-bottom: 1px solid #00F0FF;
                padding-top: 1px;
            }
        """

        btn_open_workspace = QPushButton("📁", self)
        btn_open_workspace.setToolTip("Рабочая папка (WORKSPACE_DIR)")
        btn_open_workspace.setFixedSize(34, 34)
        btn_open_workspace.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_open_workspace.setStyleSheet(action_btn_qss)
        btn_open_workspace.clicked.connect(lambda: SidebarWidget._safe_open_folder(WORKSPACE_DIR))

        btn_open_outputs = QPushButton("🎨", self)
        btn_open_outputs.setToolTip("Папка сгенерированных изображений (OUTPUTS_DIR)")
        btn_open_outputs.setFixedSize(34, 34)
        btn_open_outputs.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_open_outputs.setStyleSheet(action_btn_qss)
        btn_open_outputs.clicked.connect(lambda: SidebarWidget._safe_open_folder(OUTPUTS_DIR))

        btn_open_audio = QPushButton("🎙️", self)
        btn_open_audio.setToolTip("Папка аудиозаписей (AUDIO_DIR)")
        btn_open_audio.setFixedSize(34, 34)
        btn_open_audio.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_open_audio.setStyleSheet(action_btn_qss)
        btn_open_audio.clicked.connect(lambda: SidebarWidget._safe_open_folder(AUDIO_DIR))

        storage_row.addWidget(btn_open_workspace)
        storage_row.addWidget(btn_open_outputs)
        storage_row.addWidget(btn_open_audio)
        layout.addLayout(storage_row)

    @staticmethod
    def _safe_open_folder(folder_path):
        try:
            p = Path(folder_path)
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(str(p.resolve()))
        except Exception as e:
            logger.warning(f"[Sidebar] Не удалось открыть папку {folder_path}: {e}")
