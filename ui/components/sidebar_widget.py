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
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QSizePolicy
)
from qfluentwidgets import (
    ComboBox,
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
    # Сигнал запроса открытия галереи компаньонов
    character_gallery_requested = pyqtSignal()

    def __init__(self, window: 'MainWindow'):
        super().__init__(window)
        self.window = window
        self.setObjectName("SidebarWidget")
        self.setMinimumWidth(260)
        self.setMaximumWidth(320)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(14)

        # Brand Header removed for clean minimalist layout

        # 2. Character Card Panel (Roleplay Mode)
        self.char_card_widget = QFrame(self)
        self.char_card_widget.setObjectName("CharCardPanel")
        self.char_card_widget.setStyleSheet(
            "#CharCardPanel { background-color: #131A28; border: 1px solid #1E2B42; border-radius: 8px; padding: 8px; }"
        )
        self.char_card_widget.setMaximumWidth(260)
        char_layout = QVBoxLayout(self.char_card_widget)
        char_layout.setContentsMargins(8, 8, 8, 8)
        char_layout.setSpacing(8)

        self.char_name_lbl = QLabel("Персонаж не выбран", self)
        self.char_name_lbl.setStyleSheet("color: #FF007F; font-weight: bold; font-size: 13px;")
        char_layout.addWidget(self.char_name_lbl)

        self.btn_campaign_info = PushButton("📜 Правила мира и Лор ↗", self.char_card_widget)
        self.btn_campaign_info.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_campaign_info.setMaximumWidth(240)
        self.btn_campaign_info.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.btn_campaign_info.clicked.connect(self.window._open_campaign_info_dialog)
        char_layout.addWidget(self.btn_campaign_info)

        self.btn_load_character = PushButton("🎴 Выбор компаньона", self.char_card_widget)
        self.btn_load_character.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_load_character.setMaximumWidth(240)
        self.btn_load_character.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.btn_load_character.clicked.connect(self.character_gallery_requested.emit)
        char_layout.addWidget(self.btn_load_character)

        self.char_card_widget.setVisible(False)
        layout.addWidget(self.char_card_widget)

        # Backward compatibility placeholders
        self.lm_combo = None
        self.btn_refresh_models = None

        # 4. Chat Sessions List
        chats_header = QHBoxLayout()
        c_title = QLabel("ДИАЛОГИ", self)
        c_title.setStyleSheet("color: #64748B; font-size: 10px; font-weight: bold;")
        chats_header.addWidget(c_title)
        chats_header.addStretch(1)

        self.btn_new_chat = PushButton("Новый чат", self)
        self.btn_new_chat.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_new_chat.setMinimumWidth(160)
        self.btn_new_chat.setStyleSheet("""
            PushButton {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 6px;
                color: #E2E8F0;
                font-size: 12px;
                font-weight: 500;
                padding: 4px 18px;
                text-align: center;
            }
            PushButton:hover {
                background-color: rgba(0, 240, 255, 0.12);
                border-color: rgba(0, 240, 255, 0.35);
                color: #00F0FF;
            }
            PushButton:pressed {
                background-color: rgba(0, 240, 255, 0.08);
            }
        """)
        self.btn_new_chat.clicked.connect(self._handle_new_chat_clicked)
        chats_header.addWidget(self.btn_new_chat)
        layout.addLayout(chats_header)

        self.chat_list_widget = QListWidget(self)
        self.chat_list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chat_list_widget.setStyleSheet("""
            QListWidget {
                background-color: rgba(18, 24, 36, 0.65);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 8px;
                color: #CBD5E1;
                outline: none;
            }
            QListWidget:focus {
                outline: none;
            }
            QListWidget::item {
                background: transparent;
                padding: 2px;
                margin: 2px 4px;
                border: 1px solid transparent;
                border-radius: 6px;
                outline: none;
            }
            QListWidget::item:hover {
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.08);
            }
            QListWidget::item:selected {
                background-color: rgba(0, 240, 255, 0.10);
                border: 1px solid rgba(0, 240, 255, 0.30);
                color: #00F0FF;
            }
            QListWidget::item:selected:hover {
                background-color: rgba(0, 240, 255, 0.15);
                border: 1px solid rgba(0, 240, 255, 0.45);
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

    def update_companion_state(self, is_roleplay: bool, active_character=None, has_user_messages: bool = False):
        """Управляет видимостью и текстом карточки компаньона без раздувания геометрии."""
        self.char_card_widget.setVisible(is_roleplay)
        if not is_roleplay:
            return

        if active_character:
            char_name = getattr(active_character, "name", "Компаньон")
            self.char_name_lbl.setText(f"🎭 {char_name}")
            self.btn_campaign_info.setEnabled(True)
            self.btn_campaign_info.setText("📜 Правила мира и Лор ↗")
            self.btn_campaign_info.setToolTip("Просмотреть лор, правила мира и системный контекст")

            if has_user_messages:
                self.btn_load_character.setEnabled(False)
                self.btn_load_character.setText("🔒 Персонаж зафиксирован")
                self.btn_load_character.setToolTip("Смена персонажа заблокирована после старта диалога. Для выбора другого начните новый чат.")
            else:
                self.btn_load_character.setEnabled(True)
                self.btn_load_character.setText("🎴 Сменить компаньона")
                self.btn_load_character.setToolTip("Выбрать другую карточку персонажа из галереи")
        else:
            self.char_name_lbl.setText("Компаньон не выбран")
            self.btn_campaign_info.setEnabled(False)
            self.btn_campaign_info.setText("📜 Модуль не загружен")
            self.btn_campaign_info.setToolTip("Выберите персонажа для загрузки лора")

            self.btn_load_character.setEnabled(True)
            self.btn_load_character.setText("🎴 Выбор компаньона")
            self.btn_load_character.setToolTip("Открыть галерею персонажей")

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
