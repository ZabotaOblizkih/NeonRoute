"""
Chat Header Bar Component for NeonRoute.
Contains Avatar, Title, Status Label, Scene Background Menu,
Forge Checkpoint Selector, Export, Audio (TTS), and Fullscreen toggle buttons.
"""

from typing import TYPE_CHECKING
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QComboBox, QMenu
)

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class ChatHeaderWidget(QFrame):
    def __init__(self, window: 'MainWindow'):
        super().__init__(window)
        self.window = window
        self.setObjectName("ChatHeaderBar")
        self._setup_ui()

    def _setup_ui(self):
        header_layout = QHBoxLayout(self)
        header_layout.setContentsMargins(16, 8, 16, 8)
        header_layout.setSpacing(12)

        # 1. Avatar
        self.avatar_lbl = QLabel(self)
        self.avatar_lbl.setFixedSize(48, 48)
        self.avatar_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.avatar_lbl.setStyleSheet("""
            QLabel {
                border: 2px solid #00F0FF;
                border-radius: 24px;
                background-color: #121824;
                color: #00F0FF;
                font-size: 16px;
                font-weight: bold;
            }
        """)
        self.avatar_lbl.mousePressEvent = self.window._on_header_avatar_mouse_press
        header_layout.addWidget(self.avatar_lbl)

        # 2. Name & Status Info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        info_layout.setContentsMargins(0, 0, 0, 0)

        self.name_lbl = QLabel("Элария", self)
        self.name_lbl.setStyleSheet("color: #F8FAFC; font-weight: bold; font-size: 14px;")
        self.name_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.name_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.name_lbl.customContextMenuRequested.connect(self.window._show_avatar_menu)
        info_layout.addWidget(self.name_lbl)

        self.status_lbl = QLabel("В сети", self)
        self.status_lbl.setStyleSheet("color: #94A3B8; font-size: 11px;")
        info_layout.addWidget(self.status_lbl)
        header_layout.addLayout(info_layout)

        header_layout.addStretch()

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

        # 3. Scene Background Button & Menu (Roleplay mode)
        self.btn_scene_bg = QPushButton("🖼️ Фон сцены", self)
        self.btn_scene_bg.setToolTip("Управление фоном сцены диалога")
        self.btn_scene_bg.setFixedHeight(34)
        self.btn_scene_bg.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_scene_bg.setStyleSheet(action_btn_qss + """
            QPushButton {
                padding: 0 10px;
                font-size: 12px;
                font-weight: 600;
            }
        """)

        bg_menu = QMenu(self.btn_scene_bg)
        bg_menu.setStyleSheet("""
            QMenu {
                background-color: #121824;
                color: #E2E8F0;
                border: 1px solid #1E283D;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 18px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #1C263B;
                color: #00F0FF;
            }
        """)
        act_choose_bg = bg_menu.addAction("📁 Выбрать изображение...")
        act_choose_bg.triggered.connect(self.window._on_choose_background_clicked)

        act_gen_bg = bg_menu.addAction("🎨 Сгенерировать фон (Forge)")
        act_gen_bg.triggered.connect(self.window._on_generate_background_clicked)

        act_reset_bg = bg_menu.addAction("❌ Сбросить фон")
        act_reset_bg.triggered.connect(self.window._on_reset_background_clicked)

        self.btn_scene_bg.setMenu(bg_menu)
        self.btn_scene_bg.setVisible(False)
        header_layout.addWidget(self.btn_scene_bg)

        # 4. Forge Model Selector Combo
        self.forge_combo = QComboBox(self)
        self.forge_combo.setToolTip("Выбор активной модели для генерации арта (Forge)")
        self.forge_combo.setFixedHeight(34)
        self.forge_combo.setMinimumWidth(180)
        self.forge_combo.setStyleSheet("""
            QComboBox {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(30, 41, 59, 0.85), stop:1 rgba(15, 23, 42, 0.95));
                color: #00F0FF;
                border: 1px solid rgba(0, 240, 255, 0.4);
                border-radius: 8px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QComboBox:hover {
                border-color: #00F0FF;
                background: rgba(0, 240, 255, 0.15);
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #0F172A;
                color: #F8FAFC;
                border: 1px solid #00F0FF;
                selection-background-color: #1E293B;
                selection-color: #00F0FF;
            }
        """)

        base_models = [
            "✨ Арт-директор (Авто)",
            "DreamShaper 8",
            "Realistic Vision v6.0",
            "CyberRealistic Classic",
            "MeinaHentai v5"
        ]
        self.window.available_forge_checkpoints = base_models
        self.forge_combo.addItems(base_models)
        self.forge_combo.currentTextChanged.connect(self.window._on_forge_model_changed)
        header_layout.addWidget(self.forge_combo)

        # 5. Export Button
        self.btn_export = QPushButton("📥", self)
        self.btn_export.setToolTip("Экспорт сессии (HTML / JSON / Текст)")
        self.btn_export.setFixedSize(34, 34)
        self.btn_export.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_export.setStyleSheet(action_btn_qss)
        self.btn_export.clicked.connect(self.window._on_action_export_clicked)
        header_layout.addWidget(self.btn_export)

        # 6. Audio (TTS) Button
        self.btn_audio = QPushButton("🎙️", self)
        self.btn_audio.setToolTip("Озвучить диалог / TTS")
        self.btn_audio.setFixedSize(34, 34)
        self.btn_audio.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_audio.setStyleSheet(action_btn_qss)
        self.btn_audio.clicked.connect(self.window._on_action_audio_clicked)
        header_layout.addWidget(self.btn_audio)

        # 7. Fullscreen Button
        self.btn_fullscreen = QPushButton("⛶", self)
        self.btn_fullscreen.setToolTip("Полноэкранный режим (F11 / Esc)")
        self.btn_fullscreen.setFixedSize(34, 34)
        self.btn_fullscreen.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_fullscreen.setStyleSheet(action_btn_qss)
        self.btn_fullscreen.clicked.connect(self.window._toggle_fullscreen)
        header_layout.addWidget(self.btn_fullscreen)
