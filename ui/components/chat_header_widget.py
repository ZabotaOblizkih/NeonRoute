"""
Chat Header Bar Component for NeonRoute.
Contains Avatar, Title, Status Label, Scene Background Menu,
Forge Checkpoint Selector, Export, Audio (TTS), and Fullscreen toggle buttons.
Modernized with QFluentWidgets components under Agentrules.md.
"""

from typing import TYPE_CHECKING
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel
)
from qfluentwidgets import (
    ComboBox,
    DropDownPushButton,
    TransparentToolButton,
    FluentIcon as FIF,
    RoundMenu,
    Action,
    ToolTipFilter,
    ToolTipPosition,
)

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class ChatHeaderWidget(QFrame):
    def __init__(self, window: 'MainWindow'):
        super().__init__(window)
        self.window = window
        self.setObjectName("ChatHeaderBar")
        self.setStyleSheet("""
            QFrame#ChatHeaderBar {
                background-color: rgba(15, 19, 28, 0.45);
                border-bottom: 1px solid rgba(255, 255, 255, 0.07);
            }
        """)
        self._setup_ui()

    def _setup_ui(self):
        header_layout = QHBoxLayout(self)
        header_layout.setContentsMargins(16, 8, 16, 8)
        header_layout.setSpacing(12)

        # 1. Avatar (interactive: LMB lightbox, RMB context menu)
        self.avatar_lbl = QLabel(self)
        self.avatar_lbl.setFixedSize(48, 48)
        self.avatar_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.avatar_lbl.setStyleSheet("""
            QLabel {
                border: 2px solid rgba(0, 240, 255, 0.45);
                border-radius: 24px;
                background-color: rgba(18, 24, 36, 0.65);
                color: #00F0FF;
                font-size: 16px;
                font-weight: bold;
            }
            QLabel:hover {
                border-color: #00F0FF;
            }
        """)
        self.avatar_lbl.mousePressEvent = self.window._on_header_avatar_mouse_press
        header_layout.addWidget(self.avatar_lbl)

        # 2. Name & Status Info (Glassmorphism styling)
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        info_layout.setContentsMargins(0, 0, 0, 0)

        self.name_lbl = QLabel("Элария", self)
        self.name_lbl.setStyleSheet("color: #F8FAFC; font-weight: 600; font-size: 14px; background: transparent;")
        self.name_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.name_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.name_lbl.customContextMenuRequested.connect(self.window._show_avatar_menu)
        info_layout.addWidget(self.name_lbl)

        self.status_lbl = QLabel("В сети", self)
        self.status_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; background: transparent;")
        info_layout.addWidget(self.status_lbl)
        header_layout.addLayout(info_layout)

        header_layout.addStretch()

        # 3. Scene Background Button & Menu (Roleplay mode)
        self.btn_scene_bg = DropDownPushButton("🖼️ Фон сцены", self)
        self.btn_scene_bg.setToolTip("Управление фоном сцены диалога")
        self.btn_scene_bg.setFixedHeight(34)
        self.btn_scene_bg.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_scene_bg.installEventFilter(
            ToolTipFilter(self.btn_scene_bg, showDelay=300, position=ToolTipPosition.TOP)
        )
        self.btn_scene_bg.setStyleSheet("""
            DropDownPushButton {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 6px;
                color: #E2E8F0;
                font-size: 12px;
                font-weight: 500;
                padding: 4px 10px;
            }
            DropDownPushButton:hover {
                background-color: rgba(0, 240, 255, 0.12);
                border-color: rgba(0, 240, 255, 0.35);
                color: #00F0FF;
            }
            DropDownPushButton:pressed {
                background-color: rgba(0, 240, 255, 0.08);
            }
        """)

        bg_menu = RoundMenu(parent=self.btn_scene_bg)
        act_choose_bg = Action(FIF.FOLDER, "📁 Выбрать изображение...", parent=self.btn_scene_bg)
        act_choose_bg.triggered.connect(self.window._on_choose_background_clicked)
        bg_menu.addAction(act_choose_bg)

        act_gen_bg = Action(FIF.PALETTE, "🎨 Сгенерировать фон (Forge)", parent=self.btn_scene_bg)
        act_gen_bg.triggered.connect(self.window._on_generate_background_clicked)
        bg_menu.addAction(act_gen_bg)

        bg_menu.addSeparator()

        act_reset_bg = Action(FIF.DELETE, "❌ Сбросить фон", parent=self.btn_scene_bg)
        act_reset_bg.triggered.connect(self.window._on_reset_background_clicked)
        bg_menu.addAction(act_reset_bg)

        self.btn_scene_bg.setMenu(bg_menu)
        self.btn_scene_bg.setVisible(False)
        header_layout.addWidget(self.btn_scene_bg)

        # 4. Forge Model Selector Combo
        self.forge_combo = ComboBox(self)
        self.forge_combo.setToolTip("Выбор активной модели для генерации арта (Forge)")
        self.forge_combo.setFixedHeight(34)
        self.forge_combo.setMinimumWidth(190)
        self.forge_combo.installEventFilter(
            ToolTipFilter(self.forge_combo, showDelay=300, position=ToolTipPosition.TOP)
        )

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
        self.btn_export = TransparentToolButton(FIF.DOWNLOAD, self)
        self.btn_export.setToolTip("Экспорт сессии (HTML / JSON / Текст)")
        self.btn_export.setFixedSize(34, 34)
        self.btn_export.installEventFilter(
            ToolTipFilter(self.btn_export, showDelay=300, position=ToolTipPosition.BOTTOM)
        )
        self.btn_export.clicked.connect(self.window._on_action_export_clicked)
        header_layout.addWidget(self.btn_export)

        # 6. Audio (TTS) Button
        self.btn_audio = TransparentToolButton(FIF.MICROPHONE, self)
        self.btn_audio.setToolTip("Озвучить диалог / TTS")
        self.btn_audio.setFixedSize(34, 34)
        self.btn_audio.installEventFilter(
            ToolTipFilter(self.btn_audio, showDelay=300, position=ToolTipPosition.BOTTOM)
        )
        self.btn_audio.clicked.connect(self.window._on_action_audio_clicked)
        header_layout.addWidget(self.btn_audio)

        # 7. Fullscreen Button
        self.btn_fullscreen = TransparentToolButton(FIF.FULL_SCREEN, self)
        self.btn_fullscreen.setToolTip("Полноэкранный режим (F11 / Esc)")
        self.btn_fullscreen.setFixedSize(34, 34)
        self.btn_fullscreen.installEventFilter(
            ToolTipFilter(self.btn_fullscreen, showDelay=300, position=ToolTipPosition.BOTTOM)
        )
        self.btn_fullscreen.clicked.connect(self.window._toggle_fullscreen)
        header_layout.addWidget(self.btn_fullscreen)
