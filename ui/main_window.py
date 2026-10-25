"""
Main Window Controller for NeonRoute
Integrates UI, Task Queue (8GB VRAM safety), Local Backends, and Tool Calling.
"""

import os
import sys
import re
import time
import shutil
import base64
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any, Union

from PyQt6.QtCore import Qt, QTimer, pyqtSlot, pyqtSignal, QEvent, QThread, QPoint, QPointF
from PyQt6.QtGui import (
    QIcon, QPixmap, QAction, QPainter, QColor,
    QPainterPath, QCursor, QKeySequence, QShortcut, QMouseEvent
)
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QFileDialog, QScrollArea, QFrame,
    QMessageBox, QSplitter, QListWidget, QListWidgetItem, QSizePolicy,
    QApplication, QInputDialog, QDialog, QProgressBar, QMenu,
    QSlider, QSpinBox, QDoubleSpinBox, QWidgetAction, QGridLayout,
    QGraphicsDropShadowEffect, QMenuBar
)
from qframelesswindow import FramelessMainWindow
from qframelesswindow.windows import WindowsFramelessWindow
from qfluentwidgets import FluentTitleBar, setTheme, Theme

from config import (
    APP_NAME, APP_TAGLINE, APP_VERSION, DEFAULT_LM_STUDIO_URL,
    DEFAULT_FORGE_URL, DEFAULT_TTS_URL,
    EXPORTS_DIR, OUTPUTS_DIR, AUDIO_DIR,
    HISTORY_DIR, BACKGROUNDS_DIR, WORKSPACE_DIR
)
from services.task_queue import TaskQueueWorker, InferenceTask
from services.lm_studio import LMStudioClient, ModelNotFoundError
from services.forge_client import ForgeClient, ForgeOfflineError
from services.art_director import ArtDirector, parse_user_negative_trigger
from services.forge_models import (
    FORGE_MODELS,
    find_checkpoint_for_style,
    detect_style_from_checkpoint
)
from services.tts_service import TTSService
from services.file_generator import FileGenerator
from services.tavern_parser import TavernParser, CharacterCard
from services.stt_service import AudioInputService
from services.process_manager import ProcessManager
from services.session_manager import SessionManager
from services.memory_db import memory_db, handle_memory_command
from services.tool_engine import ToolEngine, ToolCall
from services.sandbox_manager import SandboxManager
from services.workspace_launcher import WorkspaceLauncher
from utils.system_monitor import SystemMonitor
from utils.health_checker import HealthCheckWorker
from utils.config_manager import ConfigManager
from services.environment_doctor import EnvironmentDoctor

from ui.components.chat_bubble import (
    ChatMessageWidget,
    render_circular_avatar,
    WorkspaceFileCardWidget,
    ConsoleOutputSpoilerWidget
)
from ui.components.assistant_chat_bubble import AssistantChatMessageWidget
from ui.components.rp.rp_chat_bubble import RPChatMessageWidget
from ui.components.chat_list_item import ChatSessionItemWidget
from ui.components.chat_background import ChatBackgroundViewport
from ui.components.chat_stream_widget import ChatStreamWidget
from ui.components.typing_loader import TypingLoaderWidget
from ui.components.input_box import ChatInputBox
from ui.components.status_pill import StatusPill
from ui.components.status_bar_widget import MainStatusBar
from ui.components.emoji_picker import EmojiPickerMenu
from ui.components.search_bar import SearchBarWidget
from ui.components.sidebar_widget import SidebarWidget
from ui.components.chat_header_widget import ChatHeaderWidget
from ui.components.chat_input_panel import ChatInputPanel
from ui.dialogs.shutdown_dialog import ShutdownProgressDialog
from ui.dialogs.avatar_lightbox_dialog import AvatarLightboxDialog
from ui.controllers.reminder_controller import ReminderController
from ui.builders.menu_builder import MenuBuilder, ClickOnlyMenuBar
from ui.controllers.window_state_controller import WindowStateMixin
from ui.controllers.dialog_controller import DialogControllerMixin
from ui.controllers.task_lifecycle_controller import TaskLifecycleMixin
from ui.controllers.search_controller import ChatSearchMixin
from ui.controllers.avatar_controller import AvatarControllerMixin
from ui.controllers.session_controller import SessionControllerMixin
from ui.builders.chat_bubble_builder import ChatBubbleBuilderMixin
from ui.controllers.chat_flow_controller import ChatFlowControllerMixin
from ui.controllers.roleplay_controller import RoleplayControllerMixin
from services.roleplay_flow_controller import RoleplayFlowControllerMixin
from ui.controllers.assistant_flow_controller import AssistantFlowControllerMixin
from ui.controllers.hardware_engine import HardwareEngineMixin
from ui.controllers.engineer_controller import EngineerControllerMixin
from ui.controllers.header_controller import HeaderStateController
from ui.workers.shutdown_worker import ShutdownWorker
from ui.workers.tool_execution_worker import ToolExecutionWorker
from ui.log_console import LogConsoleDialog, setup_global_log_handler
from ui.styles import NEON_THEME_QSS

logger = logging.getLogger("NeonRoute.MainWindow")

try:
    from ui.dialogs.avatar_crop_dialog import AvatarCropDialog
    if not hasattr(AvatarCropDialog, "get_cropped_avatar_path"):
        AvatarCropDialog.get_cropped_avatar_path = lambda self: self.destination_path
    if not hasattr(AvatarCropDialog, "_original_init"):
        _orig_crop_init = AvatarCropDialog.__init__
        def _wrapped_crop_init(self, *args, **kwargs):
            if "image_path" in kwargs and "source_image_path" not in kwargs:
                kwargs["source_image_path"] = kwargs.pop("image_path")
            _orig_crop_init(self, *args, **kwargs)
        AvatarCropDialog.__init__ = _wrapped_crop_init
        AvatarCropDialog._original_init = _orig_crop_init
except Exception:
    AvatarCropDialog = None


class MainWindow(
    FramelessMainWindow,
    WindowStateMixin,
    DialogControllerMixin,
    TaskLifecycleMixin,
    ChatSearchMixin,
    AvatarControllerMixin,
    SessionControllerMixin,
    ChatBubbleBuilderMixin,
    ChatFlowControllerMixin,
    AssistantFlowControllerMixin,
    RoleplayControllerMixin,
    RoleplayFlowControllerMixin,
    HardwareEngineMixin,
    EngineerControllerMixin
):
    models_refreshed = pyqtSignal()
    _sig_reload_lms = pyqtSignal(float)
    _sig_cancel_lms = pyqtSignal()

    def __init__(self):
        super().__init__()
        setup_global_log_handler()
        self.setWindowTitle(APP_NAME)
        self._is_initializing = True  # Флаг подавления фоновых триггеров при старте
        self.resize(1200, 820)
        self.setMinimumSize(960, 640)
        self.setStyleSheet(NEON_THEME_QSS)

        # Аппаратное размытие Mica / Acrylic под Windows 11
        if sys.platform == "win32":
            try:
                self.windowEffect.setMicaEffect(self.winId(), isDarkMode=True)
            except Exception:
                try:
                    self.windowEffect.setAcrylicEffect(self.winId())
                except Exception:
                    pass

        # Config & Preferences
        self.app_config = ConfigManager.load_config()
        self.chat_font_size = self.app_config.get("chat_font_size", 13)
        self.auto_translate = self.app_config.get("auto_translate", False)
        self.active_lms_model = self.app_config.get("active_model", "")

        # Wallpaper & Dimming settings (MainWindow background rendering)
        self.background_path: Optional[str] = self.app_config.get("background_path", None)
        self.background_dim_opacity: int = self.app_config.get("background_dim_opacity", 140)
        self.background_pixmap: Optional[QPixmap] = None
        if self.background_path and Path(self.background_path).exists():
            self.background_pixmap = QPixmap(self.background_path)
        self._bg_render_cache: Optional[QPixmap] = None
        self._update_background_cache()

        self._log_console_dialog: Optional[LogConsoleDialog] = None

        # Service Backends
        self.lm_client = LMStudioClient(DEFAULT_LM_STUDIO_URL)
        self.forge_client = ForgeClient(DEFAULT_FORGE_URL)
        from services.vram_arbiter import VRAMArbiter
        VRAMArbiter.get_instance().set_clients(self.lm_client, self.forge_client)
        self.art_director = ArtDirector()
        self.tts_service = TTSService(DEFAULT_TTS_URL)
        self.stt_service = AudioInputService()
        self.task_queue = TaskQueueWorker()
        self.system_monitor = SystemMonitor(interval_ms=2800)
        self.forge_client.system_monitor = self.system_monitor
        self.health_worker = HealthCheckWorker(interval_ms=3500)
        self.process_manager = ProcessManager(self)
        self.process_manager.lm_client = self.lm_client
        self.lm_client.set_process_manager(self.process_manager)
        self.chat_controller = self
        self._knowledge_sync_worker = None

        # State Variables
        self.current_mode = "assistant"  # "assistant" or "roleplay"
        self.active_character: Optional[CharacterCard] = None
        self.chat_history: List[Dict[str, str]] = []
        self.attached_files: List[Path] = []
        self.is_busy = False
        self.current_mode_is_art = False
        self._forge_launching = False
        self._is_refreshing_models = False

        # Live Inference & Tasks
        self.typing_loader: Optional[TypingLoaderWidget] = None
        self._active_tasks: Dict[str, InferenceTask] = {}
        self._active_generation_status: Optional[str] = None
        if hasattr(self, "forge_client") and self.forge_client:
            self.forge_client.generation_progress.connect(self._on_forge_generation_progress)
        self.header_controller = HeaderStateController(parent=self)
        self.header_controller.state_updated.connect(self._apply_header_state)
        self._active_avatar_menu: Optional[QMenu] = None
        self._tool_worker: Optional[ToolExecutionWorker] = None
        self._tool_loop_count: int = 0
        self._tool_executed_this_turn: bool = False
        self._active_manual_script_worker: Optional[ToolExecutionWorker] = None
        self._turn_bubble: Any = None
        self._is_post_tool_synthesis: bool = False
        self._synthesis_first_chunk: bool = False
        self._turn_assistant_widget: Any = None
        self._greeting_cycler_widget: Optional[QWidget] = None

        # Session History Variables
        self.current_session: Optional[Dict[str, Any]] = None
        self._session_items: Dict[str, Any] = {}  # session_id -> (QListWidgetItem, ChatSessionItemWidget)
        self._is_switching_session = False

        # Forge Checkpoint (Configured via Menu)
        self.selected_forge_checkpoint: str = "✨ Арт-директор (Авто)"

        # In-chat Search State
        self._search_matching_widgets: List[ChatMessageWidget] = []
        self._current_search_idx: int = -1

        self._lms_reload_timer: Optional[QTimer] = None
        self._pending_scene_art_request: Optional[Dict[str, Any]] = None

        self._sig_reload_lms.connect(self._do_reload_active_lms_timer)
        self._sig_cancel_lms.connect(self._do_cancel_lms_reload)

        from services.mode_coordinator import ModeCoordinator, AppMode
        self.mode_coordinator = ModeCoordinator(self)

        self._setup_ui()
        self._setup_shortcuts()
        self._setup_signals()
        self._start_background_services()
        self._update_header_for_current_state()

        self.reminder_controller = ReminderController(parent_window=self)
        self.reminder_controller.reminder_triggered.connect(self._on_reminder_triggered)

    @property
    def active_mode(self) -> str:
        return getattr(self, "current_mode", "assistant")

    @property
    def font_size(self) -> int:
        return getattr(self, "chat_font_size", 13)

    @property
    def chat_scroll_content(self) -> Optional[QWidget]:
        return getattr(self, "chat_container", None)

    def menuBar(self) -> ClickOnlyMenuBar:
        if not hasattr(self, "_menu_bar") or self._menu_bar is None:
            self._menu_bar = ClickOnlyMenuBar(self)
        return self._menu_bar

    def _setup_ui(self):
        # 0. Custom Fluent TitleBar with Windows 11 Snap Layouts
        title_bar = FluentTitleBar(self)
        title_bar.setFixedHeight(48)
        title_bar.setTitle("")
        if hasattr(title_bar, "titleLabel") and title_bar.titleLabel:
            title_bar.titleLabel.setVisible(False)
            title_bar.titleLabel.clear()
            try:
                self.windowTitleChanged.disconnect(title_bar.setTitle)
            except Exception:
                pass
        if not self.windowIcon().isNull():
            title_bar.setIcon(self.windowIcon())
        self.setTitleBar(title_bar)
        title_bar.raise_()

        # 0.1 Explicit window control buttons connection
        if hasattr(self.titleBar, "minBtn"):
            try:
                self.titleBar.minBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.minBtn.clicked.connect(self.showMinimized)

        if hasattr(self.titleBar, "maxBtn"):
            try:
                self.titleBar.maxBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.maxBtn.clicked.connect(
                lambda: self.showNormal() if self.isMaximized() else self.showMaximized()
            )

        if hasattr(self.titleBar, "closeBtn"):
            try:
                self.titleBar.closeBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.closeBtn.clicked.connect(self.close)

        # Hide title label completely for clean titlebar, keep labels transparent for mouse dragging
        if hasattr(self.titleBar, "titleLabel") and self.titleBar.titleLabel:
            self.titleBar.titleLabel.setVisible(False)
            self.titleBar.titleLabel.clear()
            self.titleBar.titleLabel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if hasattr(self.titleBar, "iconLabel") and self.titleBar.iconLabel:
            self.titleBar.iconLabel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        central_widget = QWidget(self)
        central_widget.setObjectName("CentralWidget")
        central_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        central_widget.setAutoFillBackground(False)
        central_widget.setStyleSheet("background: transparent;")
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root_layout.setSpacing(0)

        # 0.2 Menu Bar strictly under TitleBar (Click-only opening)
        self._menu_bar = ClickOnlyMenuBar(central_widget)
        self._menu_bar.setFixedHeight(30)
        root_layout.addWidget(self._menu_bar, 0)

        MenuBuilder.setup_menus(self)
        help_menu = None
        for action in self.menuBar().actions():
            if action.text().replace("&", "") == "Справка":
                help_menu = action.menu()
                break
        if not help_menu:
            help_menu = self.menuBar().addMenu("Справка")

        if not any("Проверка компонентов" in a.text() for a in help_menu.actions()):
            act_check_deps = help_menu.addAction("Проверка компонентов...")
            act_check_deps.setStatusTip("Проверить наличие Git и библиотек для режима Инженер")
            act_check_deps.triggered.connect(self.open_dependency_dialog)

        # Register menu action shortcuts on MainWindow for global window interception (Ctrl+N, Ctrl+Q, etc.)
        for action in self.menuBar().actions():
            menu = action.menu()
            if menu:
                for sub_action in menu.actions():
                    if sub_action.shortcut().toString():
                        self.addAction(sub_action)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setHandleWidth(1)
        root_layout.addWidget(splitter, 1)

        # Принудительно поднимаем заголовок над всеми слоями интерфейса
        self.titleBar.raise_()

        # 1. Left Sidebar Component
        self.sidebar = SidebarWidget(window=self)
        splitter.addWidget(self.sidebar)

        # Aliases for backward compatibility with mixins
        self.char_card_widget = self.sidebar.char_card_widget
        self.char_name_lbl = self.sidebar.char_name_lbl
        self.btn_campaign_info = self.sidebar.btn_campaign_info
        self.btn_load_character = self.sidebar.btn_load_character
        self.lm_combo = self.sidebar.lm_combo
        self.btn_refresh_models = self.sidebar.btn_refresh_models
        self.chat_list_widget = self.sidebar.chat_list_widget

        # Первичное быстрое наполнение lm_combo найденными .gguf моделями через ServerConfigResolver
        try:
            from services.server_config import ServerConfigResolver
            discovered_models = ServerConfigResolver.discover_gguf_models()
            if discovered_models:
                self.lm_combo.blockSignals(True)
                self.lm_combo.clear()
                self.lm_combo.addItems(list(discovered_models.keys()))
                active_cfg_model = self.app_config.get("active_model", "")
                if active_cfg_model and self.lm_combo.findText(active_cfg_model) >= 0:
                    self.lm_combo.setCurrentIndex(self.lm_combo.findText(active_cfg_model))
                self.lm_combo.blockSignals(False)
        except Exception as e:
            logger.debug(f"Initial GGUF discovery error: {e}")

        # 2. Main Central Work Area
        self.main_content = self._create_main_content()
        splitter.addWidget(self.main_content)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 920])

        root_layout.addWidget(splitter)

        # 3. Status Bar
        self._setup_status_bar()

        self.titleBar.resize(self.width(), self.titleBar.height())

    def _create_main_content(self) -> QWidget:
        container = QWidget(self)
        container.setObjectName("MainContentContainer")
        container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        container.setAutoFillBackground(False)
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Chat Header Bar Component
        self.chat_header_bar = ChatHeaderWidget(window=self)
        layout.addWidget(self.chat_header_bar)

        # Aliases for 100% backward compatibility
        self.header_avatar_lbl = self.chat_header_bar.avatar_lbl
        self.header_name_lbl = self.chat_header_bar.name_lbl
        self.header_status_lbl = self.chat_header_bar.status_lbl
        self.btn_scene_bg = self.chat_header_bar.btn_scene_bg
        self.forge_combo = self.chat_header_bar.forge_combo
        self.btn_export = self.chat_header_bar.btn_export
        self.btn_audio = self.chat_header_bar.btn_audio
        self.btn_fullscreen = self.chat_header_bar.btn_fullscreen

        # Floating close button for fullscreen mode
        self.floating_close_btn = QPushButton("✕", self)
        self.floating_close_btn.setFixedSize(36, 36)
        self.floating_close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.floating_close_btn.setToolTip("Выйти из полного экрана (Esc / F11)")
        self.floating_close_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(15, 23, 42, 0.8);
                color: #00F0FF;
                border: 1px solid #00F0FF;
                border-radius: 18px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(0, 240, 255, 0.25);
                color: #FFFFFF;
                border: 1px solid #38BDF8;
            }
            QPushButton:pressed {
                background-color: rgba(15, 23, 42, 0.95);
            }
        """)
        self.floating_close_btn.clicked.connect(self._toggle_fullscreen)
        self.floating_close_btn.setVisible(False)

        # Search Bar Widget (Docked right below header)
        self.search_bar_widget = SearchBarWidget(self)
        self.search_bar_widget.setVisible(False)
        self.search_bar_widget.query_changed.connect(self._on_search_query_changed)
        self.search_bar_widget.find_next.connect(self._search_next)
        self.search_bar_widget.find_prev.connect(self._search_prev)
        self.search_bar_widget.closed.connect(self._close_search_bar)
        layout.addWidget(self.search_bar_widget)

        # Chat Stream Area Component
        self.chat_stream = ChatStreamWidget(window=self)
        layout.addWidget(self.chat_stream, stretch=1)

        # Aliases for 100% backward compatibility with mixins
        self.scroll_area = self.chat_stream.scroll_area
        self.chat_viewport = self.chat_stream.chat_viewport
        self.chat_container = self.chat_stream.chat_container
        self.chat_layout = self.chat_stream.chat_layout

        # Нативный детерминированный автоскролл без QTimer.singleShot:
        if hasattr(self, "scroll_area") and self.scroll_area:
            v_bar = self.scroll_area.verticalScrollBar()
            v_bar.rangeChanged.connect(lambda min_val, max_val: v_bar.setValue(max_val) if getattr(self, "_auto_scroll_enabled", True) else None)

        # Bottom Input Capsule Component
        self.input_panel = ChatInputPanel(window=self)
        layout.addWidget(self.input_panel)

        # Aliases for 100% backward compatibility
        self.btn_attach = self.input_panel.btn_attach
        self.btn_mic = self.input_panel.btn_mic
        self.btn_emoji = self.input_panel.btn_emoji
        self.input_box = self.input_panel.input_box
        self.btn_send = self.input_panel.input_box.action_btn
        self.btn_stop = self.input_panel.input_box.action_btn
        self.attachments_container = self.input_panel.attachments_container
        self.attachments_layout = self.input_panel.attachments_layout
        self.attached_files = self.input_panel.attached_files

        return container

    def _on_send_clicked(self):
        """Отправка сообщения с гарантированной отменой таймера LM Studio."""
        # Мгновенная вытесняющая отмена фонового анализа сцены перед новым ходом игрока
        if hasattr(self, "task_queue") and self.task_queue:
            self.task_queue.abort_tasks_by_type("memory")
        self._remove_greeting_cycler_chip()
        self._cancel_lms_reload()
        super()._on_send_clicked()

    def _setup_status_bar(self):
        status_bar = MainStatusBar(window=self)
        self.setStatusBar(status_bar)

        # Aliases for 100% backward compatibility with monitoring/process mixins
        self.lbl_vram = status_bar.lbl_vram
        self.lbl_ram = status_bar.lbl_ram
        self.btn_lms_toggle = status_bar.btn_lms_toggle
        self.pill_lm = status_bar.pill_lm
        self.btn_forge_toggle = status_bar.btn_forge_toggle
        self.pill_forge = status_bar.pill_forge
        self.pill_tts = status_bar.pill_tts

    def _setup_shortcuts(self):
        MenuBuilder.setup_shortcuts(self)

    def _setup_signals(self):
        # Task Queue Signals
        self.task_queue.task_started.connect(self._on_task_started)
        self.task_queue.task_finished.connect(self._on_task_finished)
        self.task_queue.task_failed.connect(self._on_task_failed)
        self.task_queue.task_aborted.connect(self._on_task_aborted)
        self.task_queue.queue_empty.connect(self._on_queue_empty)

        # Hardware Monitor
        self.system_monitor.stats_updated.connect(self._on_hardware_stats)

        # STT Service Signals
        self.stt_service.recording_started.connect(lambda: self.btn_mic.setText("🔴"))
        self.stt_service.recording_stopped.connect(lambda: self.btn_mic.setText("🎙️"))
        self.stt_service.transcription_ready.connect(self._on_transcription_ready)

        # LM Studio Direct Signals
        self.lm_client.response_received.connect(self._on_llm_response_received)
        self.lm_client.token_streamed.connect(self._on_token_streamed)
        self.lm_client.error_occurred.connect(self._on_llm_error_occurred)

        # Process Manager Signals
        self.process_manager.action_completed.connect(self._on_process_action_completed)
        self.process_manager.server_state_changed.connect(self._on_server_state_changed)

        # Health Checker Background Worker (Non-blocking)
        self.health_worker.health_updated.connect(self._on_health_updated)

        # Model Selector Signal
        self.lm_combo.activated[int].connect(self._on_lms_model_activated)

    def _start_background_services(self):
        self.task_queue.start()
        self.system_monitor.start()
        self.health_worker.start()

        import threading
        def _check_lms_background():
            if not self.process_manager.is_lm_studio_running():
                self.process_manager.start_lm_studio_async()
        threading.Thread(target=_check_lms_background, daemon=True).start()

        from services.art_director.prompting import warmup_argos
        from services.memory_db import warmup_memory_engine
        warmup_argos()
        warmup_memory_engine()

        def _finish_initialization():
            self._is_initializing = False
            logger.info("[NeonRoute] Холодная инициализация завершена. Очередь задач готова.")

        # Загружаем сохраненные сессии сразу, не блокируя их ожиданием сетевого опроса LMS/Forge
        self._load_saved_sessions()
        _finish_initialization()
        self._refresh_models()

        from utils.config_manager import ConfigManager
        cfg = ConfigManager.load_config()
        if cfg.get("check_dependencies_on_startup", True):
            missing = EnvironmentDoctor.check_missing()
            if missing:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(800, self.open_dependency_dialog)

        # Фоновая синхронизация базы знаний Эларии (CPU, 0 МБ VRAM)
        try:
            from services.knowledge_manager import KnowledgeSyncWorker
            self._knowledge_sync_worker = KnowledgeSyncWorker(self)
            self._knowledge_sync_worker.sync_finished.connect(self._on_knowledge_sync_finished)
            self._knowledge_sync_worker.start()
        except Exception as e:
            logger.warning(f"[NeonRoute] Не удалось запустить фоновую синхронизацию знаний: {e}")

    def _on_knowledge_sync_finished(self, registry_data: dict):
        total = registry_data.get("total_documents", len(registry_data.get("documents", {})))
        logger.info(f"[NeonRoute] Фоновая синхронизация базы знаний завершена: {total} документов.")

    def _load_saved_sessions(self):
        """Loads all sessions from C:\\NeonRoute\\history\\ or creates a fresh one."""
        # Явно отключаем сигналы и удаляем существующие виджеты перед очисткой:
        for item, widget in self._session_items.values():
            try:
                widget.delete_requested.disconnect()
                widget.rename_requested.disconnect()
                if hasattr(widget, "change_persona_requested"):
                    widget.change_persona_requested.disconnect()
            except (TypeError, RuntimeError):
                pass
            widget.deleteLater()

        self.chat_list_widget.clear()
        self._session_items.clear()

        sessions = SessionManager.list_sessions()

        for s in sessions:
            sess_id = s["id"]
            title = s.get("title", "Диалог")
            mode = s.get("mode", "assistant")
            char_data = s.get("character_data")
            char_name = char_data.get("name") if char_data else None
            # Извлекаем аватар СТРОГО из данных конкретной сессии:
            avatar_path = (
                (char_data.get("avatar_path") if char_data else None)
                or s.get("assistant_avatar_path")
            )

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, sess_id)
            w = ChatSessionItemWidget(sess_id, title, mode, char_name, avatar_path=avatar_path, session_type=s.get("type"), parent=self)
            self._configure_session_item_widget(w, title)
            w.delete_requested.connect(self._on_delete_session_requested)
            w.rename_requested.connect(self._on_rename_session_requested)
            w.change_persona_requested.connect(self._on_change_persona_requested)
            item.setSizeHint(w.sizeHint())

            self.chat_list_widget.addItem(item)
            self.chat_list_widget.setItemWidget(item, w)
            self._session_items[sess_id] = (item, w)

        if sessions:
            # Switch to most recent session
            self._switch_to_session(sessions[0]["id"])
        else:
            # Fresh initial session
            self._create_new_session("assistant", is_initial=True)

    def show_new_chat_dialog(self):
        """Displays the popup dialog to create a new chat in the selected mode."""
        from ui.dialogs.new_chat_dialog import NewChatDialog
        dlg = NewChatDialog(parent=self)
        btn = self.sidebar.btn_new_chat
        pos = btn.mapToGlobal(QPoint(0, btn.height() + 4))
        dlg.move(pos)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_mode:
            self.create_chat(mode=dlg.selected_mode)

    def create_chat(self, mode: str = "assistant"):
        """Creates a new chat session for roleplay, assistant, or engineer mode."""
        self._turn_bubble = None
        self._current_streaming_widget = None

        if mode in ("rp", "roleplay"):
            self._create_new_session(
                mode="roleplay",
                character_card=self.active_character,
                title=None
            )
        elif mode in ("engineer", "engineering"):
            self._create_new_session(
                mode="engineer",
                character_card=None,
                title="🛠️ Инженерная задача"
            )
            if self.current_session:
                self.current_session["type"] = "engineering"
                self.current_session["mode"] = "engineer"
                self._save_current_session()
        else:
            self._create_new_session(
                mode="assistant",
                character_card=None,
                title=None
            )

        if self.current_session:
            sess_id = self.current_session.get("id")
            if sess_id:
                self.on_active_chat_changed(sess_id)

    def _create_new_session(
        self,
        mode: str = "assistant",
        character_card: Optional[CharacterCard] = None,
        title: Optional[str] = None,
        is_initial: bool = False
    ):
        """Creates and activates a new chat session with locked/unlocked model tracking."""
        self._turn_bubble = None
        self._current_streaming_widget = None
        if self.current_session and not is_initial:
            self._save_current_session()

        current_model = self.lm_combo.currentText().strip()
        if current_model.startswith("default"):
            current_model = ""

        new_sess = SessionManager.create_session(
            mode=mode,
            character_card=character_card,
            title=title,
            model_id=current_model,
            is_model_locked=False
        )
        if mode in ("engineer", "engineering") or title == "🛠️ Инженерная задача":
            new_sess["type"] = "engineering"
            new_sess["mode"] = "engineer"

        from services.mode_coordinator import AppMode
        target_app_mode = AppMode.from_string(mode, is_engineer_checked=(mode in ("engineer", "engineering")))
        self.mode_coordinator.transition_to(target_app_mode, session_data=new_sess)

        effective_mode = "engineer" if (mode in ("engineer", "engineering") or new_sess.get("type") == "engineering") else mode
        cfg_key = f"default_sampling_{effective_mode}"
        from services.session_manager import get_default_sampling_params
        fallback_params = get_default_sampling_params(effective_mode)
        initial_sampling = dict(self.app_config.get(cfg_key, fallback_params))

        new_sess["sampling_params"] = initial_sampling
        new_sess["is_custom_sampling"] = False
        logger.info(f"[SessionManager] [NEW-CHAT] Создан диалог в режиме '{effective_mode}' с дефолтами из config.json: temp={initial_sampling.get('temperature')}")
        self.current_session = new_sess
        self.active_character = character_card if mode == "roleplay" else None
        self.chat_history.clear()
        self._update_mode_ui()
        self._unlock_mode_selector()

        # Clear existing message widgets
        self._clear_chat_widgets()
        self._apply_chat_background()

        # Unlock model selector for a fresh dialog
        self.lm_combo.setEnabled(True)
        self.lm_combo.setToolTip("Выберите модель LM Studio")

        # Insert new list item at top of sidebar
        sess_id = new_sess["id"]
        SandboxManager.set_active_session_id(sess_id)
        ToolEngine.set_active_session_id(sess_id)

        char_name = character_card.name if character_card else None
        avatar_path = str(character_card.avatar_path) if (character_card and character_card.avatar_path) else None
        item_title = new_sess["title"]

        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, sess_id)
        w = ChatSessionItemWidget(sess_id, item_title, mode, char_name, avatar_path=avatar_path, session_type=new_sess.get("type"), parent=self)
        self._configure_session_item_widget(w, item_title)
        w.delete_requested.connect(self._on_delete_session_requested)
        w.rename_requested.connect(self._on_rename_session_requested)
        w.change_persona_requested.connect(self._on_change_persona_requested)
        item.setSizeHint(w.sizeHint())

        self.chat_list_widget.insertItem(0, item)
        self.chat_list_widget.setItemWidget(item, w)
        self._session_items[sess_id] = (item, w)
        self.chat_list_widget.setCurrentItem(item)

        # Welcome / greeting message
        if mode == "assistant":
            self._add_assistant_welcome()
        elif mode == "roleplay" and character_card:
            self._remove_greeting_cycler_chip()
            if not getattr(character_card, "visual_dna", "") and getattr(character_card, "description", ""):
                from services.tavern_parser import TavernParser
                dna_reg = TavernParser.extract_physical_dna_regex(character_card.description)
                if dna_reg:
                    character_card.visual_dna = dna_reg
            from services.rp_cast_manager import RPCastManager
            user_persona = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=new_sess.get("user_persona_id"))
            cast_data = RPCastManager.load_cast(new_sess["id"], user_name=user_persona.get("name", "Игрок"), companion_name=character_card.name)

            if hasattr(character_card, "companions") and len(getattr(character_card, "companions", [])) > 1:
                RPCastManager.bootstrap_party_from_card(new_sess["id"], character_card)
                cast_data = RPCastManager.load_cast(new_sess["id"], user_name=user_persona.get("name", "Игрок"), companion_name=character_card.name)

            if character_card and getattr(character_card, "visual_dna", ""):
                cast_data.setdefault("companion_state", {})["visual_dna"] = character_card.visual_dna

            if character_card and getattr(character_card, "scenery_dna", "").strip():
                cast_data.setdefault("environment", {})["location_dna"] = character_card.scenery_dna.strip()

            if user_persona:
                if user_persona.get("visual_dna"):
                    cast_data.setdefault("player_state", {})["visual_dna"] = user_persona["visual_dna"]
                if user_persona.get("outfit"):
                    cast_data.setdefault("player_state", {})["current_outfit"] = user_persona["outfit"]
                if user_persona.get("inventory"):
                    raw_inv = user_persona["inventory"]
                    inv_items = [it.strip() for it in raw_inv.split(",") if it.strip()] if isinstance(raw_inv, str) else list(raw_inv)
                    existing_inv = cast_data.setdefault("inventory", [])
                    for it in inv_items:
                        if it not in existing_inv:
                            existing_inv.append(it)

            RPCastManager.save_cast(new_sess["id"], cast_data)

            # 1. Загрузка пула завязок
            pool = character_card.get_greetings_pool()
            total_greetings = len(pool)

            chosen_idx = getattr(character_card, "selected_greeting_idx", None)
            if chosen_idx is not None and 0 <= chosen_idx < total_greetings:
                active_greet_idx = chosen_idx
            elif total_greetings > 0:
                import random
                active_greet_idx = random.randint(0, total_greetings - 1)
            else:
                active_greet_idx = 0

            character_card.selected_greeting_idx = None

            if pool and active_greet_idx < len(pool):
                selected_item = pool[active_greet_idx]
                raw_orig = selected_item["orig"]
                cached_ru = selected_item["ru"]
                effective_raw = selected_item["effective"]
            else:
                raw_orig = getattr(character_card, "first_mes", "")
                cached_ru = getattr(character_card, "first_mes_ru", "")
                effective_raw = cached_ru or raw_orig

            from services.macro_engine import MacroEngine
            u_name = self._get_active_user_name() or "Игрок"

            if cached_ru:
                greeting = MacroEngine.process(cached_ru, user_name=u_name, char_name=character_card.name)
                msg_widget = self._append_message(
                    character_card.name,
                    greeting,
                    is_user=False,
                    avatar_path=character_card.avatar_path,
                    translated_text=None,
                    is_showing_translation=False
                )
            else:
                if effective_raw:
                    greeting = MacroEngine.process(effective_raw, user_name=u_name, char_name=character_card.name)
                else:
                    greeting = f"*{character_card.name} появляется перед вами и ждет ваших слов.*"

                latin_cnt = sum(1 for c in greeting if 'a' <= c.lower() <= 'z')
                cyr_cnt = sum(1 for c in greeting if 'а' <= c.lower() <= 'я')
                is_eng = latin_cnt > cyr_cnt and latin_cnt > 15

                msg_widget = self._append_message(
                    character_card.name,
                    greeting,
                    is_user=False,
                    avatar_path=character_card.avatar_path,
                    translated_text=None,
                    is_showing_translation=False
                )
                if self.auto_translate and is_eng:
                    self._enqueue_translation_task(greeting, msg_widget)

            # 2. Синхронизация завязки в память модели (Root Anchor)
            self.chat_history.append({"role": "assistant", "content": greeting})
            if self.current_session:
                self.current_session["chat_history"] = list(self.chat_history)
                self.current_session["active_greeting_idx"] = active_greet_idx

            # 3. Чип переключения завязок прямо в чате (если вариантов > 1)
            if total_greetings > 1:
                self._create_greeting_cycler_chip(msg_widget, character_card, active_greet_idx)

        self._save_current_session()
        self._unlock_mode_selector()

    def _create_greeting_cycler_chip(self, msg_widget, character_card: CharacterCard, current_idx: int):
        """Создает интерактивный чип переключения альтернативных завязок под первым сообщением."""
        self._remove_greeting_cycler_chip()

        pool = character_card.get_greetings_pool()
        total = len(pool)
        if total <= 1:
            return

        cycler_widget = QWidget(self.chat_container)
        cycler_layout = QHBoxLayout(cycler_widget)
        cycler_layout.setContentsMargins(58, 2, 0, 8)
        cycler_layout.setSpacing(6)

        btn_cycle = QPushButton(f"🎲 Сменить завязку ({current_idx + 1}/{total})", cycler_widget)
        btn_cycle.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_cycle.setToolTip("Переключить на другую сюжетную завязку персонажа (пока вы не сделали первый ход)")
        btn_cycle.setStyleSheet("""
            QPushButton {
                background-color: rgba(14, 28, 48, 0.85);
                color: #38BDF8;
                border: 1px solid #1E3A5F;
                border-radius: 12px;
                padding: 4px 14px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(0, 240, 255, 0.2);
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton:pressed {
                background-color: #0A101D;
            }
        """)

        def _on_cycle():
            nonlocal current_idx
            p = character_card.get_greetings_pool()
            if not p:
                return
            current_idx = (current_idx + 1) % len(p)
            item = p[current_idx]
            effective_raw = item.get("effective")
            cached_ru = item.get("ru")

            from services.macro_engine import MacroEngine
            u_name = self._get_active_user_name() or "Игрок"
            if cached_ru:
                new_text = MacroEngine.process(cached_ru, user_name=u_name, char_name=character_card.name)
                trans_text = None
            else:
                new_text = MacroEngine.process(effective_raw or "", user_name=u_name, char_name=character_card.name)
                trans_text = None

            if hasattr(msg_widget, "set_text"):
                msg_widget.set_text(new_text)
            if hasattr(msg_widget, "set_translation"):
                msg_widget.set_translation(trans_text)

            if self.chat_history:
                self.chat_history[0]["content"] = new_text

            if self.current_session:
                if self.current_session.get("chat_history"):
                    self.current_session["chat_history"][0]["content"] = new_text
                if self.current_session.get("messages"):
                    self.current_session["messages"][0]["text"] = new_text
                    self.current_session["messages"][0]["translated_text"] = trans_text
                self.current_session["active_greeting_idx"] = current_idx
                self._save_current_session()

            btn_cycle.setText(f"🎲 Сменить завязку ({current_idx + 1}/{len(p)})")
            self.statusBar().showMessage(f"Завязка переключена на вариант #{current_idx + 1}.", 3000)

        btn_cycle.clicked.connect(_on_cycle)
        cycler_layout.addWidget(btn_cycle)
        cycler_layout.addStretch()

        insert_idx = min(1, self.chat_layout.count())
        self.chat_layout.insertWidget(insert_idx, cycler_widget)
        self._greeting_cycler_widget = cycler_widget

    def _remove_greeting_cycler_chip(self):
        """Удаляет чип смены завязок после совершения первого хода или смены чата."""
        if getattr(self, "_greeting_cycler_widget", None) is not None:
            try:
                self.chat_layout.removeWidget(self._greeting_cycler_widget)
                self._greeting_cycler_widget.deleteLater()
            except Exception:
                pass
            finally:
                self._greeting_cycler_widget = None

    def _on_chat_list_item_clicked(self, item: QListWidgetItem):
        """Handler when clicking a chat item in sidebar."""
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if not session_id:
            return
        if self.current_session and self.current_session.get("id") == session_id:
            return
        self._switch_to_session(session_id)

    def _switch_to_session(self, session_id: str):
        """Loads and activates a saved session by ID with instant rendering and isolated context."""
        self._remove_greeting_cycler_chip()
        if self._is_switching_session:
            return
        self._is_switching_session = True
        try:
            if self.current_session:
                self._save_current_session()

            # 1. Total context isolation: wipe in-memory history and existing widgets first
            self._turn_bubble = None
            self._current_streaming_widget = None
            self.chat_history.clear()
            self._clear_chat_widgets()

            session_data = SessionManager.load_session(session_id)
            if not session_data:
                return

            from services.mode_coordinator import AppMode
            is_eng_sess = bool(session_data.get("type") == "engineering" or session_data.get("mode") == "engineering")
            target_app_mode = AppMode.from_string(session_data.get("mode"), is_engineer_checked=is_eng_sess)
            self.mode_coordinator.transition_to(target_app_mode, session_data=session_data)

            self.current_session = session_data
            self.chat_history = list(session_data.get("chat_history", []))
            mode = session_data.get("mode", "assistant")

            # Sync active session ID to SandboxManager & ToolEngine
            SandboxManager.set_active_session_id(session_id)
            ToolEngine.set_active_session_id(session_id)

            # Restore character card if roleplay
            char_data = session_data.get("character_data")
            if mode == "roleplay" and char_data:
                self.active_character = self._restore_roleplay_character_from_session(char_data)
            else:
                self.active_character = None

            self._update_header_for_current_state()

            # Restore model and lock status for this session WITHOUT triggering async weight reloads
            sess_model = session_data.get("model_id")
            self.lm_combo.blockSignals(True)
            try:
                if sess_model and not sess_model.startswith("default"):
                    idx = self.lm_combo.findText(sess_model)
                    if idx >= 0:
                        if self.lm_combo.currentIndex() != idx:
                            self.lm_combo.setCurrentIndex(idx)
                    else:
                        self.lm_combo.addItem(sess_model)
                        self.lm_combo.setCurrentIndex(self.lm_combo.count() - 1)
                    self.active_lms_model = sess_model

                is_locked = session_data.get("is_model_locked", False)
                if is_locked:
                    self.lm_combo.setEnabled(False)
                    self.lm_combo.setToolTip("Модель зафиксирована для этого диалога")
                else:
                    self.lm_combo.setEnabled(True)
                    self.lm_combo.setToolTip("Выберите модель LM Studio")
            finally:
                self.lm_combo.blockSignals(False)

            # Set current item in sidebar
            if session_id in self._session_items:
                it, _ = self._session_items[session_id]
                self.chat_list_widget.setCurrentItem(it)

            # Restore custom background if configured
            self._apply_chat_background()

            # Re-render messages smoothly with screen updates temporarily disabled
            if hasattr(self, "chat_container"):
                self.chat_container.setUpdatesEnabled(False)
            try:
                self._all_session_messages = session_data.get("messages", []) or []
                total_msgs = len(self._all_session_messages)
                page_size = 35

                if total_msgs <= page_size:
                    self._rendered_messages_offset = 0
                    self._render_session_messages(self._all_session_messages)
                else:
                    self._rendered_messages_offset = total_msgs - page_size
                    self._create_load_more_button()
                    self._render_session_messages(self._all_session_messages[self._rendered_messages_offset:])
            finally:
                if hasattr(self, "chat_container"):
                    self.chat_container.setUpdatesEnabled(True)

            # Реактивное восстановление Greeting Cycler при смене диалога до первого хода игрока
            all_msgs = session_data.get("messages", []) or []
            if (
                mode == "roleplay"
                and self.active_character
                and len(all_msgs) == 1
                and not all_msgs[0].get("is_user")
                and len(self.active_character.get_greetings_pool()) > 1
            ):
                first_widget = None
                for i in range(self.chat_layout.count()):
                    w = self.chat_layout.itemAt(i).widget()
                    if isinstance(w, ChatMessageWidget):
                        first_widget = w
                        break
                if first_widget:
                    curr_idx = session_data.get("active_greeting_idx", 0)
                    self._create_greeting_cycler_chip(first_widget, self.active_character, curr_idx)

            self._scroll_to_bottom()
            self._check_mode_lock()
            self.on_active_chat_changed(session_id)
            self.statusBar().showMessage(f"Загружен диалог: {session_data.get('title', 'Диалог')}", 3000)
        finally:
            self._is_switching_session = False

    def on_active_chat_changed(self, chat_id: str):
        chat = (
            self.current_session
            if (self.current_session and self.current_session.get("id") == chat_id)
            else SessionManager.load_session(chat_id)
        )
        if not chat:
            return

        raw_mode = str(chat.get("mode") or "assistant").lower()
        raw_type = str(chat.get("type") or "").lower()
        if raw_type == "engineering" or raw_mode in ("engineer", "engineering"):
            target_mode = "engineer"
        elif raw_mode in ("rp", "roleplay"):
            target_mode = "rp"
        else:
            target_mode = "assistant"

        self._engineering_mode_active = (target_mode == "engineer")
        if hasattr(self, "input_box") and self.input_box:
            self.input_box.set_engineer_mode(self._engineering_mode_active)

        if target_mode == "engineer":
            target_model = self.app_config.get("engineer_model_name") or self.app_config.get("engineer_model")
        elif target_mode == "rp":
            char_data = chat.get("character_data") or {}
            target_model = (
                char_data.get("character_model")
                or self.app_config.get("rp_model_name")
                or chat.get("model_id")
                or self.app_config.get("active_model")
            )
        elif target_mode == "assistant":
            target_model = self.app_config.get("assistant_model_name") or self.app_config.get("active_model")
        else:
            target_model = None

        current_model = getattr(self.lm_client, "_last_loaded_model_id", None) or self.active_lms_model
        if target_model and current_model != target_model:
            self.active_lms_model = target_model
            if hasattr(self, "lm_combo") and self.lm_combo:
                self.lm_combo.blockSignals(True)
                idx = self.lm_combo.findText(target_model)
                if idx >= 0:
                    self.lm_combo.setCurrentIndex(idx)
                else:
                    self.lm_combo.addItem(target_model)
                    self.lm_combo.setCurrentIndex(self.lm_combo.count() - 1)
                self.lm_combo.blockSignals(False)



    def _render_session_messages(self, messages: List[Dict[str, Any]], target_index: Optional[int] = None):
        """
        Рендеринг сохраненных сообщений сессии при переключении чата или подгрузке истории.
        Проверяет наличие прикрепленного изображения и корректно отрисовывает карточки арта
        с сохранением отправителя.
        """
        self._turn_bubble = None
        self._current_streaming_widget = None
        default_sender = getattr(self.active_character, "name", "Элария") if hasattr(self, "active_character") and self.active_character else "Элария"
        for msg in messages:
            img_path = msg.get("image_path")
            if not img_path and msg.get("file_paths"):
                for fp in msg.get("file_paths", []):
                    if str(fp).lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        img_path = fp
                        break

            sender = msg.get("sender") or (default_sender if not msg.get("is_user") and msg.get("role") != "user" else "Вы")

            if img_path and Path(img_path).exists():
                # Отрисовать бабл с изображением с сохранением отправителя (msg.get("sender", "Элария"))
                msg_copy = dict(msg)
                msg_copy["sender"] = sender
                msg_copy["image_path"] = str(img_path)
                self._render_stored_message(msg_copy, target_index=target_index)
            else:
                # Стандартная отрисовка текстового бабла
                msg_copy = dict(msg)
                msg_copy["sender"] = sender
                if img_path and not Path(img_path).exists():
                    msg_copy["image_path"] = None
                self._render_stored_message(msg_copy, target_index=target_index)

            if target_index is not None:
                target_index += 1

    def _create_load_more_button(self):
        remaining = getattr(self, "_rendered_messages_offset", 0)
        btn = self.chat_stream.create_load_more_button(remaining, on_click_callback=self._load_previous_messages_chunk)
        self._btn_load_more_widget = btn






    def _on_change_persona_requested(self, session_id: str, persona_id: str):
        """Persists chosen user persona for session and refreshes header if active."""
        SessionManager.set_session_user_persona(session_id, persona_id)
        if self.current_session and self.current_session.get("id") == session_id:
            self.current_session["user_persona_id"] = persona_id
            self._update_header_for_current_state()

        try:
            from services.rp_cast_manager import RPCastManager
            persona = SessionManager.get_active_user_persona(is_roleplay=True, session_persona_id=persona_id)
            p_name = persona.get("name", "Игрок")
            p_dna = persona.get("visual_dna", "")
            cast_data = RPCastManager.load_cast(session_id, user_name=p_name)
            if "player_state" not in cast_data or not isinstance(cast_data["player_state"], dict):
                cast_data["player_state"] = {}
            cast_data["player_state"]["name"] = p_name
            cast_data["player_state"]["visual_dna"] = p_dna
            RPCastManager.save_cast(session_id, cast_data)
        except Exception as e:
            logger.warning(f"Ошибка синхронизации player_state в RPCastManager: {e}")


    def _check_mode_lock(self):
        """Locks mode switching if active session is roleplay with messages."""
        pass

    def _unlock_mode_selector(self):
        """Unlocks mode switching for a fresh session."""
        pass

    def _configure_session_item_widget(self, w: ChatSessionItemWidget, title: str):
        """Applies robust layout sizing: fixed 24x24 delete button, expanding title with right elision."""
        if hasattr(w, "btn_del"):
            w.btn_del.setFixedSize(24, 24)
            w.btn_del.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if hasattr(w, "title_lbl"):
            w.title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            w.title_lbl._full_title = title
            def _elide():
                fm = w.title_lbl.fontMetrics()
                avail_w = max(10, w.title_lbl.width())
                elided = fm.elidedText(w.title_lbl._full_title, Qt.TextElideMode.ElideRight, avail_w)
                QLabel.setText(w.title_lbl, elided)
            w.title_lbl._update_elide = _elide
            orig_resize = w.title_lbl.resizeEvent
            def _resize(ev):
                if orig_resize:
                    orig_resize(ev)
                _elide()
            w.title_lbl.resizeEvent = _resize
            if not hasattr(w, "_orig_update_title_fn"):
                w._orig_update_title_fn = w.update_title

            def _upd_t(new_t):
                w.title_lbl._full_title = new_t
                w._orig_update_title_fn(new_t)
                _elide()
            w.update_title = _upd_t
            _elide()

    def _get_active_user_name(self, is_roleplay: Optional[bool] = None) -> str:
        """Извлекает актуальное имя пользователя напрямую из SessionManager (user_profiles.json)."""
        try:
            rp_flag = (self.current_mode == "roleplay") if is_roleplay is None else bool(is_roleplay)
            sess_pid = self.current_session.get("user_persona_id") if getattr(self, "current_session", None) else None
            persona = SessionManager.get_active_user_persona(is_roleplay=rp_flag, session_persona_id=sess_pid)
            name = str(persona.get("name") or "").strip()
            if name and name not in ("User", "Пользователь"):
                return name
            return name or ("Игрок" if rp_flag else "Пользователь")
        except Exception as e:
            logger.debug(f"Error resolving active user name: {e}")
            return "Игрок" if (is_roleplay or getattr(self, "current_mode", "") == "roleplay") else "Пользователь"

    def _add_assistant_welcome(self):
        """Формирует естественное стартовое приветствие Эларии при открытии нового диалога."""
        import random
        user_name = self._get_active_user_name()

        if user_name:
            welcomes = [
                "Привет. С чего начнём?",
                f"Привет, {user_name}. Рассказывай, что придумал.",
                f"Привет, {user_name}. Готова к работе.",
                "Привет. Справимся с чем угодно. Поделись своими мыслями.",
                "Привет. Готова к любым экспериментам. Что на повестке?"
            ]
        else:
            welcomes = [
                "Привет. С чего начнём?",
                "Привет. Рассказывай, что придумал.",
                "Привет. Готова к работе.",
                "Привет. Справимся с чем угодно. Поделись своими мыслями.",
                "Привет. Готова к любым экспериментам. Что на повестке?"
            ]

        welcome_text = random.choice(welcomes)
        self._append_message("Элария", welcome_text, is_user=False)

    def _switch_mode(self, mode: str, update_session: bool = True):
        from services.mode_coordinator import AppMode
        target = AppMode.ROLEPLAY if mode == "roleplay" else AppMode.ASSISTANT
        self.mode_coordinator.transition_to(target)


    def _update_mode_ui(self):
        is_eng = self.is_engineer_mode_active()
        if is_eng:
            self.char_card_widget.setVisible(False)
            if hasattr(self, "btn_forge_toggle"):
                self.btn_forge_toggle.setVisible(False)
            self.statusBar().showMessage("Режим: 🛠️ Инженер (Песочница C:\\NeonRoute\\workspace)")
        elif self.current_mode == "assistant":
            self.char_card_widget.setVisible(False)
            if hasattr(self, "btn_forge_toggle"):
                self.btn_forge_toggle.setVisible(True)
            self.statusBar().showMessage("Режим: 💬 Ассистент (Элария)")
        else:
            self.char_card_widget.setVisible(True)
            if hasattr(self, "btn_forge_toggle"):
                self.btn_forge_toggle.setVisible(True)
            has_user_messages = False
            if self.current_session and self.current_session.get("messages"):
                has_user_messages = any(m.get("is_user") for m in self.current_session["messages"])

            if self.active_character:
                self.char_name_lbl.setText(f"🎭 {self.active_character.name}")
                if hasattr(self, "btn_campaign_info"):
                    self.btn_campaign_info.setEnabled(True)
                    self.btn_campaign_info.setText("📜 Правила мира и Лор ↗")
                if hasattr(self, "btn_load_character"):
                    if has_user_messages:
                        self.btn_load_character.setEnabled(False)
                        self.btn_load_character.setText("🔒 Компаньон зафиксирован")
                        self.btn_load_character.setToolTip("Смена персонажа заблокирована после старта диалога. Для выбора другого начните новый чат (Новый чат).")
                    else:
                        self.btn_load_character.setEnabled(True)
                        self.btn_load_character.setText("🎴 Выбор компаньона")
                        self.btn_load_character.setToolTip("Выбрать карточку персонажа из галереи")
            else:
                self.char_name_lbl.setText("Компаньон не выбран")
                if hasattr(self, "btn_campaign_info"):
                    self.btn_campaign_info.setEnabled(False)
                    self.btn_campaign_info.setText("📜 Модуль не загружен")
                if hasattr(self, "btn_load_character"):
                    self.btn_load_character.setEnabled(True)
                    self.btn_load_character.setText("🎴 Выбор компаньона")
                    self.btn_load_character.setToolTip("Открыть галерею персонажей")
            self.statusBar().showMessage("Режим: Ролевой чат (Импорт Chub/Tavern JSON)")

        if hasattr(self, "btn_scene_bg"):
            self.btn_scene_bg.setVisible(self.current_mode == "roleplay" and not is_eng)

        self._update_header_for_current_state()

    # =========================================================================
    # Font Zoom & Preference Controls
    # =========================================================================
    def _change_chat_font_size(self, delta: int):
        self.chat_stream.change_font_size(delta)

    def _reset_chat_font_size(self):
        self.chat_stream.reset_font_size()

    def _apply_chat_font_size(self, size: int):
        self.chat_stream.apply_font_size(size)

    def _on_auto_translate_toggled(self, checked: bool):
        self.auto_translate = checked
        self.app_config["auto_translate"] = checked
        ConfigManager.save_config(self.app_config)
        state_str = "включен" if checked else "выключен"
        self.statusBar().showMessage(f"Автоперевод ответов {state_str}.", 3000)

    # =========================================================================
    # Translation, Regeneration, and Message Deletion
    # =========================================================================

    @pyqtSlot(str)
    def _on_llm_response_received(self, text: str):
        """Завершение генерации ответа LLM и делегирование в контроллер потока чата."""
        super()._on_llm_response_received(text)

    def _on_llm_error_occurred(self, error: str):
        """Called directly when LMStudioClient emits error_occurred signal."""
        logger.error(f"LM Studio reported error: {error}")



    def _scroll_to_bottom(self):
        if hasattr(self, "scroll_area") and self.scroll_area:
            v_bar = self.scroll_area.verticalScrollBar()
            v_bar.setValue(v_bar.maximum())

    def _show_emoji_picker(self):
        picker = EmojiPickerMenu(target_input=self.input_box, parent=self)
        picker.show_above_widget(self.btn_emoji)

    def _select_attachment(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Выберите файлы", "", "All Files (*.*);;Images (*.png *.jpg *.jpeg);;Docs (*.xlsx *.docx *.txt)"
        )
        if files:
            for f in files:
                self.input_panel.add_attachment(Path(f))

    def _on_image_pasted(self, image_path: Path):
        self.input_panel.add_attachment(image_path)
        self.statusBar().showMessage("Скриншот из буфера добавлен во вложения.")

    def _on_files_dropped(self, file_paths: List[Path]):
        for p in file_paths:
            self.input_panel.add_attachment(p)
        self.statusBar().showMessage(f"Прикреплено файлов: {len(file_paths)}", 3000)

    def _update_attachments_ui(self):
        self.input_panel.update_attachments_ui()

    def _remove_attachment(self, path: Path):
        self.input_panel.remove_attachment(path)

    def _toggle_voice_input(self):
        if self.stt_service.is_recording:
            self.stt_service.stop_recording_and_transcribe()
        else:
            self.stt_service.start_recording()

    def _on_transcription_ready(self, text: str):
        cur = self.input_box.toPlainText()
        self.input_box.setPlainText((cur + " " + text).strip())


    # =========================================================================
    # Header Bar & Character Personalization
    # =========================================================================



    def _apply_header_state(self, title: str, status_text: str, color_hex: str, avatar_path: Optional[Any]):
        """Пассивный рендеринг шапки чата от HeaderStateController."""
        if hasattr(self, "header_name_lbl"):
            self.header_name_lbl.setText(title)
        if hasattr(self, "header_status_lbl"):
            self.header_status_lbl.setText(status_text)
            self.header_status_lbl.setStyleSheet(f"color: {color_hex}; font-size: 11px;")
        if hasattr(self, "header_avatar_lbl"):
            effective_avatar = avatar_path
            if not effective_avatar or not Path(str(effective_avatar)).exists():
                effective_avatar = self._get_current_avatar_path() if hasattr(self, "_get_current_avatar_path") else None
            round_pix = render_circular_avatar(effective_avatar, title, size=48, is_user=False)
            self.header_avatar_lbl.setPixmap(round_pix)
            self.header_avatar_lbl.setText("")

    def _update_header_for_current_state(self):
        """Делегирует синхронизацию контекста в HeaderStateController."""
        if hasattr(self, "header_controller"):
            self.header_controller.sync_context(
                mode=self.current_mode,
                character=self.active_character,
                session=self.current_session
            )



    # =========================================================================
    # In-Chat Search & Navigation
    # =========================================================================





    def _search_next(self):
        total = len(self._search_matching_widgets)
        if total == 0:
            return
        self._current_search_idx = (self._current_search_idx + 1) % total
        self.search_bar_widget.set_match_info(self._current_search_idx + 1, total)
        self._navigate_search_match(self._current_search_idx)

    def _search_prev(self):
        total = len(self._search_matching_widgets)
        if total == 0:
            return
        self._current_search_idx = (self._current_search_idx - 1) % total
        self.search_bar_widget.set_match_info(self._current_search_idx + 1, total)
        self._navigate_search_match(self._current_search_idx)

    def _navigate_search_match(self, idx: int):
        if 0 <= idx < len(self._search_matching_widgets):
            w = self._search_matching_widgets[idx]
            self.scroll_area.ensureWidgetVisible(w)
            query = self.search_bar_widget.search_input.text().strip()
            if query and hasattr(w, "text_browser") and w.text_browser:
                doc = w.text_browser.document()
                found_cur = doc.find(query)
                if not found_cur.isNull():
                    w.text_browser.setTextCursor(found_cur)


    def open_dependency_dialog(self):
        """Открывает диалог проверки и фоновой установки компонентов окружения."""
        try:
            from ui.dialogs.dependency_dialog import DependencyDialog
            dlg = DependencyDialog(parent=self)
            dlg.exec()
        except Exception as e:
            logger.error(f"Не удалось открыть диалог компонентов: {e}")

    def closeEvent(self, event):
        if getattr(self, "_shutdown_completed", False):
            event.accept()
            super().closeEvent(event)
            return

        # Ignore close event to keep GUI event loop spinning smoothly
        event.ignore()

        if getattr(self, "_shutdown_in_progress", False):
            return

        self._shutdown_in_progress = True

        # Clean up typing loader if active
        self._remove_typing_loader()

        if hasattr(self, "reminder_controller") and self.reminder_controller:
            try:
                self.reminder_controller._timer.stop()
                self.reminder_controller.tray_icon.hide()
            except Exception as e_tray:
                logger.debug(f"Error stopping reminder timer or hiding tray icon: {e_tray}")

        if hasattr(self, "_knowledge_sync_worker") and self._knowledge_sync_worker:
            try:
                if self._knowledge_sync_worker.isRunning():
                    self._knowledge_sync_worker.wait(400)
            except Exception:
                pass

        # Hide main window to give instant responsiveness
        self.hide()

        # Create and display compact glassmorphism progress dialog
        self._shutdown_dialog = ShutdownProgressDialog()
        self._shutdown_dialog.show()

        # Run shutdown sequence in background worker thread
        self._shutdown_worker = ShutdownWorker(self)
        self._shutdown_worker.finished.connect(self._on_shutdown_finished)
        self._shutdown_worker.start()

    def _on_shutdown_finished(self):
        logger.info("Graceful shutdown completed. Exiting application.")
        self._shutdown_completed = True
        if hasattr(self, "_shutdown_dialog") and self._shutdown_dialog:
            try:
                self._shutdown_dialog.close()
                self._shutdown_dialog.deleteLater()
            except Exception:
                pass
        QApplication.quit()

    def _on_reminder_triggered(self, text: str):
        asst_name = "Элария" if self.current_mode != "roleplay" or not self.active_character else self.active_character.name
        reminder_bubble = f"🔔 **Напоминаю:** {text}"
        if not hasattr(self, "chat_history"):
            self.chat_history = []
        if self.chat_history and self.chat_history[-1].get("role") == "assistant":
            self.chat_history.append({"role": "user", "content": "[Системное напоминание по таймеру]"})
        self._append_message(asst_name, reminder_bubble, is_user=False)
        self.chat_history.append({"role": "assistant", "content": reminder_bubble})
        self._save_current_session()

    # -------------------------------------------------------------------------
    # Оконный менеджер: совместимость с WindowStateMixin и Windows 11 Fluent
    # -------------------------------------------------------------------------
    def paintEvent(self, event):
        # При наличии пользовательских обоев отрисовываем их через WindowStateMixin.
        # При отсутствии обоев используем полупрозрачную темную подложку,
        # чтобы аппаратный эффект размытия Mica/Acrylic под Windows 11 не перекрывался.
        if getattr(self, '_bg_render_cache', None) and not self._bg_render_cache.isNull():
            WindowStateMixin.paintEvent(self, event)
        else:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(15, 19, 28, 160))
            painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "titleBar") and self.titleBar:
            self.titleBar.resize(self.width(), self.titleBar.height())
            self.titleBar.raise_()
        WindowStateMixin.resizeEvent(self, event)

    def changeEvent(self, event):
        super().changeEvent(event)
        WindowStateMixin.changeEvent(self, event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "titleBar"):
            if self.isFullScreen():
                self.titleBar.setVisible(False)
                if hasattr(self, "_menu_bar") and self._menu_bar:
                    self._menu_bar.setVisible(False)
                if self.centralWidget() and self.centralWidget().layout():
                    self.centralWidget().layout().setContentsMargins(0, 0, 0, 0)
            else:
                self.titleBar.setVisible(True)
                if hasattr(self, "_menu_bar") and self._menu_bar:
                    self._menu_bar.setVisible(True)
                if self.centralWidget() and self.centralWidget().layout():
                    self.centralWidget().layout().setContentsMargins(0, self.titleBar.height(), 0, 0)
                self.titleBar.raise_()

    def keyPressEvent(self, event):
        WindowStateMixin.keyPressEvent(self, event)
        if not event.isAccepted():
            super().keyPressEvent(event)

    def _toggle_fullscreen(self):
        WindowStateMixin._toggle_fullscreen(self)
        if hasattr(self, "titleBar"):
            if self.isFullScreen():
                self.titleBar.setVisible(False)
                if hasattr(self, "_menu_bar") and self._menu_bar:
                    self._menu_bar.setVisible(False)
                if self.centralWidget() and self.centralWidget().layout():
                    self.centralWidget().layout().setContentsMargins(0, 0, 0, 0)
            else:
                self.titleBar.setVisible(True)
                if hasattr(self, "_menu_bar") and self._menu_bar:
                    self._menu_bar.setVisible(True)
                if self.centralWidget() and self.centralWidget().layout():
                    self.centralWidget().layout().setContentsMargins(0, self.titleBar.height(), 0, 0)
                self.titleBar.raise_()

    def nativeEvent(self, eventType, message):
        """Поддержка Windows 11 Snap Layouts на кнопке разворачивания."""
        if sys.platform == "win32" and hasattr(self, "titleBar"):
            try:
                from ctypes.wintypes import MSG
                import win32con
                from qframelesswindow.titlebar.title_bar_buttons import TitleBarButtonState

                msg = MSG.from_address(message.__int__())
                if msg.hWnd:
                    if msg.message == win32con.WM_NCHITTEST and getattr(self, "_isResizeEnabled", True):
                        if self._is_hover_max_btn():
                            if hasattr(self.titleBar, "maxBtn"):
                                self.titleBar.maxBtn.setState(TitleBarButtonState.HOVER)
                            return True, win32con.HTMAXBUTTON

                    elif msg.message in [0x2A2, win32con.WM_MOUSELEAVE]:
                        if hasattr(self.titleBar, "maxBtn"):
                            self.titleBar.maxBtn.setState(TitleBarButtonState.NORMAL)

                    elif msg.message in [win32con.WM_NCLBUTTONDOWN, win32con.WM_NCLBUTTONDBLCLK] and self._is_hover_max_btn():
                        e = QMouseEvent(
                            QEvent.Type.MouseButtonPress,
                            QPointF(),
                            Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier
                        )
                        QApplication.sendEvent(self.titleBar.maxBtn, e)
                        return True, 0

                    elif msg.message in [win32con.WM_NCLBUTTONUP, win32con.WM_NCRBUTTONUP] and self._is_hover_max_btn():
                        e = QMouseEvent(
                            QEvent.Type.MouseButtonRelease,
                            QPointF(),
                            Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier
                        )
                        QApplication.sendEvent(self.titleBar.maxBtn, e)
                        return True, 0
            except Exception:
                pass

        if sys.platform == "win32":
            return WindowsFramelessWindow.nativeEvent(self, eventType, message)
        return False, 0

    def _is_hover_max_btn(self) -> bool:
        if not hasattr(self, "titleBar") or not hasattr(self.titleBar, "maxBtn"):
            return False
        return self.titleBar.childAt(self.titleBar.mapFromGlobal(QCursor.pos())) is self.titleBar.maxBtn
