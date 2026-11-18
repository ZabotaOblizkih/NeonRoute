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
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QSplitter, QListWidgetItem,
    QSizePolicy, QApplication, QDialog, QMenu
)
from qframelesswindow import FramelessMainWindow
from qframelesswindow.windows import WindowsFramelessWindow
from qfluentwidgets import FluentTitleBar

from config import (
    APP_NAME, DEFAULT_LM_STUDIO_URL,
    DEFAULT_FORGE_URL, DEFAULT_TTS_URL,
    WORKSPACE_DIR
)
from services.task_queue import TaskQueueWorker, InferenceTask
from services.lm_studio import LMStudioClient
from services.forge_client import ForgeClient
from services.art_director import ArtDirector
from services.tts_service import TTSService
from services.tavern_parser import TavernParser, CharacterCard
from services.stt_service import AudioInputService
from services.process_manager import ProcessManager
from services.session_manager import SessionManager
from services.tool_engine import ToolEngine
from services.sandbox_manager import SandboxManager
from utils.system_monitor import SystemMonitor
from utils.health_checker import HealthCheckWorker
from utils.config_manager import ConfigManager
from services.environment_doctor import EnvironmentDoctor

from ui.components.chat_bubble import (
    ChatMessageWidget,
    render_circular_avatar
)
from ui.components.chat_list_item import ChatSessionItemWidget
from ui.components.chat_stream_widget import ChatStreamWidget
from ui.components.workspace_inspector import WorkspaceInspectorWidget
from ui.components.typing_loader import TypingLoaderWidget
from ui.components.status_bar_widget import MainStatusBar
from ui.components.emoji_picker import EmojiPickerMenu
from ui.components.search_bar import SearchBarWidget
from ui.components.sidebar_widget import SidebarWidget
from ui.components.chat_header_widget import ChatHeaderWidget
from ui.components.chat_input_panel import ChatInputPanel
from ui.components.scroll_button import FloatingScrollDownButton
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
from ui.common.ui_context import UIContext
from ui.styles import NEON_THEME_QSS

logger = logging.getLogger("NeonRoute.MainWindow")

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
    startup_ready = pyqtSignal()
    _startup_milestone_done = pyqtSignal(str)
    _sig_reload_lms = pyqtSignal(float)
    _sig_cancel_lms = pyqtSignal()
    _sig_engineer_plan_card = pyqtSignal(str)
    _sig_engineer_thought = pyqtSignal(str, str, str)
    _sig_inspector_status = pyqtSignal(str, str, int, str)

    def __init__(self):
        super().__init__()
        self.ui_context = UIContext(self)
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
        from ui.common.image_utils import load_display_master_pixmap
        from config import DEFAULT_BACKGROUND_PATH, BACKGROUNDS_DIR, CANONICAL_BACKGROUND_NAME
        self.background_path: Optional[str] = self.app_config.get("background_path", None)
        self.background_dim_opacity: int = self.app_config.get("background_dim_opacity", 140)

        # Выбираем активный фон: кастомный из конфига или защищённый системный по умолчанию
        active_bg_path: Optional[str] = None
        if self.background_path and Path(self.background_path).exists():
            active_bg_path = self.background_path
        elif DEFAULT_BACKGROUND_PATH.exists():
            active_bg_path = str(DEFAULT_BACKGROUND_PATH)
        elif (BACKGROUNDS_DIR / CANONICAL_BACKGROUND_NAME).exists():
            active_bg_path = str(BACKGROUNDS_DIR / CANONICAL_BACKGROUND_NAME)

        self.background_pixmap: Optional[QPixmap] = None
        self._bg_render_cache: Optional[QPixmap] = None
        self._active_bg_path: Optional[str] = active_bg_path
        if active_bg_path and Path(active_bg_path).exists():
            QTimer.singleShot(0, self._load_initial_background)

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
        self._active_assistant_skill: str = "auto"
        self._active_engineer_skill: str = "auto"
        self._forge_launching = False
        self._is_refreshing_models = False

        # Smart Sticky Bottom & Floating Scroll Down Button State
        self._is_at_bottom: bool = True
        self._prev_scroll_max: int = 0
        self._is_loading_more_history: bool = False
        self.btn_scroll_down: Optional[FloatingScrollDownButton] = None

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

        # Таймер дебаунса для переключения весов моделей
        self._model_switch_timer = QTimer(self)
        self._model_switch_timer.setSingleShot(True)
        self._model_switch_timer.setInterval(400)  # 400 мс задержки
        self._pending_switch_model: Optional[str] = None
        self._pending_switch_mode: Optional[str] = None
        self._model_switch_timer.timeout.connect(self._execute_debounced_model_switch)

        self._sig_reload_lms.connect(self._do_reload_active_lms_timer)
        self._sig_cancel_lms.connect(self._do_cancel_lms_reload)
        self._sig_engineer_plan_card.connect(self._on_engineer_plan_card)
        self._sig_engineer_thought.connect(self._on_engineer_thought)
        self._sig_inspector_status.connect(self._on_inspector_status)

        # Startup Readiness Barrier & Watchdog
        self._pending_startup_milestones = {"knowledge_sync", "memory_warmup", "llm_server"}
        self._startup_barrier_released = False
        self._startup_milestone_done.connect(self._on_startup_milestone_done)
        self._startup_watchdog_timer = QTimer(self)
        self._startup_watchdog_timer.setSingleShot(True)
        self._startup_watchdog_timer.setInterval(18000)  # 18-second fallback
        self._startup_watchdog_timer.timeout.connect(self._force_release_startup_barrier)
        self._startup_watchdog_timer.start()

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


    def _open_main_settings_menu(self, target_widget=None):
        """Открывает современное окно параметров и настроек в стиле Fluent Design."""
        try:
            if hasattr(self, "_open_settings_dialog"):
                self._open_settings_dialog("system")
            else:
                from ui.dialogs.settings_dialog import SettingsDialog
                dialog = SettingsDialog(self)
                self._active_settings_dialog = dialog
                try:
                    dialog.exec()
                finally:
                    self._active_settings_dialog = None
                    dialog.deleteLater()
        except Exception as e:
            logger.error(f"Ошибка при открытии окна настроек: {e}", exc_info=True)
            try:
                menu = MenuBuilder.create_settings_menu(self)
                if target_widget:
                    global_pos = target_widget.mapToGlobal(QPoint(0, 0))
                    menu_hint = menu.sizeHint()
                    popup_pos = QPoint(
                        global_pos.x(),
                        max(10, global_pos.y() - menu_hint.height() - 4)
                    )
                    menu.exec(popup_pos, ani=True)
                else:
                    from PyQt6.QtGui import QCursor
                    menu.exec(QCursor.pos(), ani=True)
            except Exception:
                pass

    def menuBar(self):
        """Меню перенесено на кнопку-шестеренку в сайдбар, верхний MenuBar полностью отключен."""
        return None

    # --- Единый источник правды (SSOT): делегирование в self.ui_context ---
    @property
    def current_session(self) -> Optional[Dict[str, Any]]:
        return self.ui_context.current_session if hasattr(self, 'ui_context') else getattr(self, '_legacy_current_session', None)

    @current_session.setter
    def current_session(self, val: Optional[Dict[str, Any]]) -> None:
        if hasattr(self, 'ui_context'):
            self.ui_context.current_session = val
        else:
            self._legacy_current_session = val

    @property
    def _turn_bubble(self) -> Optional[Any]:
        return self.ui_context.turn_bubble if hasattr(self, 'ui_context') else getattr(self, '_legacy_turn_bubble', None)

    @_turn_bubble.setter
    def _turn_bubble(self, val: Optional[Any]) -> None:
        if hasattr(self, 'ui_context'):
            self.ui_context.turn_bubble = val
        else:
            self._legacy_turn_bubble = val

    def _setup_ui(self):
        from config import APP_ICON_PATH
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))

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
            self.titleBar.iconLabel.setVisible(False)
            self.titleBar.iconLabel.clear()
            self.titleBar.iconLabel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        central_widget = QWidget(self)
        central_widget.setObjectName("CentralWidget")
        central_widget.setAutoFillBackground(False)
        central_widget.setStyleSheet("background: transparent;")
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root_layout.setSpacing(0)

        # 0.2 Меню перенесено на кнопку-шестеренку [FIF.SETTING] в нижнюю панель сайдбара
        self.setMenuBar(None)
        self._menu_bar = None
        MenuBuilder.setup_shortcuts(self)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.main_splitter.setHandleWidth(1)
        root_layout.addWidget(self.main_splitter, 1)

        # Принудительно поднимаем заголовок над всеми слоями интерфейса
        self.titleBar.raise_()

        # 1. Left Sidebar Component
        self.sidebar = SidebarWidget(window=self)
        self.main_splitter.addWidget(self.sidebar)

        # Aliases for backward compatibility with mixins
        self.char_card_widget = self.sidebar.char_card_widget
        self.char_name_lbl = self.sidebar.char_name_lbl
        self.btn_campaign_info = self.sidebar.btn_campaign_info
        self.btn_load_character = self.sidebar.btn_load_character
        self.lm_combo = getattr(self.sidebar, "lm_combo", None)
        self.btn_refresh_models = getattr(self.sidebar, "btn_refresh_models", None)
        self.chat_list_widget = self.sidebar.chat_list_widget

        # Первичное быстрое наполнение lm_combo найденными .gguf моделями через ServerConfigResolver
        if self.lm_combo:
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
                    else:
                        first_model = list(discovered_models.keys())[0]
                        self.lm_combo.setCurrentIndex(0)
                        self.app_config["active_model"] = first_model
                        from utils.config_manager import ConfigManager
                        ConfigManager.update_setting("active_model", first_model)
                    self.lm_combo.blockSignals(False)
            except Exception as e:
                logger.debug(f"Initial GGUF discovery error: {e}")

        # 2. Main Central Work Area
        self.main_content = self._create_main_content()
        self.main_splitter.addWidget(self.main_content)

        # Третий элемент: инспектор песочницы для режима Инженер (ленивая инициализация)
        self._workspace_inspector: Optional[WorkspaceInspectorWidget] = None
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        if self.main_splitter.count() >= 3:
            self.main_splitter.setSizes([280, 920, 0])
        else:
            self.main_splitter.setSizes([280, 920])

        # 3. Status Bar
        self._setup_status_bar()

        self.titleBar.resize(self.width(), self.titleBar.height())

    def _create_main_content(self) -> QWidget:
        container = QWidget(self)
        container.setObjectName("MainContentContainer")
        container.setAutoFillBackground(False)
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Chat Header Bar Component
        self.chat_header_bar = ChatHeaderWidget(window=self, parent=container)
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
        self.btn_toggle_inspector = self.chat_header_bar.btn_toggle_inspector
        self.btn_toggle_inspector.setVisible(False)

        # Search Bar Widget (Docked right below header)
        self.search_bar_widget = SearchBarWidget(container)
        self.search_bar_widget.setVisible(False)
        self.search_bar_widget.query_changed.connect(self._on_search_query_changed)
        self.search_bar_widget.find_next.connect(self._search_next)
        self.search_bar_widget.find_prev.connect(self._search_prev)
        self.search_bar_widget.closed.connect(self._close_search_bar)
        layout.addWidget(self.search_bar_widget)

        # Chat Stream Area Component
        self.chat_stream = ChatStreamWidget(window=self, parent=container)
        layout.addWidget(self.chat_stream, stretch=1)

        # Aliases for 100% backward compatibility with mixins
        self.scroll_area = self.chat_stream.scroll_area
        self.chat_viewport = self.chat_stream.chat_viewport
        self.chat_container = self.chat_stream.chat_container
        self.chat_layout = self.chat_stream.chat_layout

        # Интеллектуальная система скролла (Smart Sticky Bottom) и кнопка «Вниз»
        if hasattr(self, "scroll_area") and self.scroll_area:
            v_bar = self.scroll_area.verticalScrollBar()
            v_bar.rangeChanged.connect(self._on_chat_scroll_range_changed)
            v_bar.valueChanged.connect(self._on_chat_scroll_value_changed)

            # Синхронизация отслеживания со SmoothScrollBar делегата (при наличии)
            delegate = getattr(self.scroll_area, "delegate", None)
            delegate_vbar = getattr(delegate, "vScrollBar", None) if delegate else None
            if delegate_vbar:
                delegate_vbar.valueChanged.connect(self._on_chat_scroll_value_changed)

            # Плавающая кнопка «Вниз», смонтированная во viewport скролл-арены
            self.btn_scroll_down = FloatingScrollDownButton(parent=self.scroll_area.viewport())
            self.btn_scroll_down.clicked.connect(self._on_scroll_down_clicked)
            self.btn_scroll_down.hide()

            # EventFilter для сохранения строгого центра при resize вьюпорта
            self.scroll_area.viewport().installEventFilter(self)

        # Bottom Input Capsule Component
        self.input_panel = ChatInputPanel(window=self, parent=container)
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
        self.sc_toggle_inspector = QShortcut(QKeySequence("Ctrl+B"), self, self.toggle_workspace_inspector)

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
        if hasattr(self, "lm_combo") and self.lm_combo:
            self.lm_combo.activated[int].connect(self._on_lms_model_activated)

        # Character Gallery signal from Sidebar
        if hasattr(self, "sidebar") and hasattr(self.sidebar, "character_gallery_requested"):
            self.sidebar.character_gallery_requested.connect(self._import_character)

    def _load_initial_background(self):
        """Отложенная загрузка системного или кастомного фона для мгновенного отклика UI."""
        if getattr(self, "_active_bg_path", None) and Path(self._active_bg_path).exists():
            if self.background_pixmap is None:
                from ui.common.image_utils import load_display_master_pixmap
                self.background_pixmap = load_display_master_pixmap(self._active_bg_path)
                self._update_background_cache()
                if hasattr(self, "_apply_chat_background"):
                    self._apply_chat_background()
                elif hasattr(self, "chat_viewport") and self.chat_viewport:
                    self.chat_viewport.set_background(None)
                self.update()

    def _start_background_services(self):
        self.task_queue.start()
        self.system_monitor.start()
        self.health_worker.start()

        import threading
        def _background_services_worker():
            try:
                if self.process_manager.is_lm_studio_running():
                    self._startup_milestone_done.emit("llm_server")
                else:
                    self.process_manager.start_lm_studio_async()
            except Exception as lms_err:
                logger.warning(f"[MainWindow] Ошибка фонового запуска LLM: {lms_err}")
                self._startup_milestone_done.emit("llm_server")

            try:
                from services.art_director.prompting import warmup_argos
                from services.memory_db import warmup_memory_engine
                warmup_argos()
                warmup_memory_engine(
                    delay=0.2,
                    on_finished=lambda: self._startup_milestone_done.emit("memory_warmup")
                )
                logger.info("[MainWindow] Фоновый прогрев Argos и MemoryEngine успешно запущен.")
            except Exception as warmup_err:
                logger.warning(f"[MainWindow] Ошибка фонового прогрева подсистем: {warmup_err}")
                self._startup_milestone_done.emit("memory_warmup")

        threading.Thread(target=_background_services_worker, daemon=True, name="BackgroundServicesWorker").start()

        def _finish_initialization():
            self._is_initializing = False
            logger.info("[NeonRoute] Холодная инициализация завершена. Очередь задач готова.")

        # Загружаем сохраненные сессии сразу, не блокируя их ожиданием сетевого опроса LMS/Forge
        self._load_saved_sessions()
        _finish_initialization()
        self._refresh_models()

        from utils.config_manager import ConfigManager
        cfg = ConfigManager.load_config()
        if not cfg.get("hardware_autotune_done", False):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(1200, self._perform_hardware_kv_autotune)

        # Фоновая синхронизация базы знаний Эларии (CPU, 0 МБ VRAM)
        try:
            from services.knowledge_manager import KnowledgeSyncWorker
            self._knowledge_sync_worker = KnowledgeSyncWorker(self)
            self._knowledge_sync_worker.sync_finished.connect(self._on_knowledge_sync_finished)
            self._knowledge_sync_worker.start()
        except Exception as e:
            logger.warning(f"[NeonRoute] Не удалось запустить фоновую синхронизацию знаний: {e}")
            self._startup_milestone_done.emit("knowledge_sync")

        # Инвалидация базы знаний на лету (KnowledgeWatcher)
        try:
            from services.knowledge_watcher import KnowledgeWatcher
            self.knowledge_watcher = KnowledgeWatcher(parent=self)
            self.knowledge_watcher.start()
        except Exception as e:
            logger.warning(f"[NeonRoute] Не удалось запустить KnowledgeWatcher: {e}")

    def _on_startup_milestone_done(self, milestone: str):
        """Thread-safe handler for completed startup milestones."""
        if milestone in self._pending_startup_milestones:
            self._pending_startup_milestones.discard(milestone)
            logger.info(f"[StartupBarrier] Milestone '{milestone}' done. Remaining: {self._pending_startup_milestones}")
            if not self._pending_startup_milestones:
                self._release_startup_barrier()

    def _force_release_startup_barrier(self):
        """Watchdog fallback triggering if any milestone times out."""
        if not self._startup_barrier_released:
            logger.warning(
                f"[StartupBarrier] Watchdog timeout reached (18s). "
                f"Forcing barrier release. Remaining milestones: {self._pending_startup_milestones}"
            )
            self._pending_startup_milestones.clear()
            self._release_startup_barrier()

    def _release_startup_barrier(self):
        """Releases the startup barrier, emits startup_ready, and runs deferred modals."""
        if self._startup_barrier_released:
            return
        self._startup_barrier_released = True
        if hasattr(self, "_startup_watchdog_timer") and self._startup_watchdog_timer.isActive():
            self._startup_watchdog_timer.stop()

        logger.info("[StartupBarrier] All startup milestones verified. Emitting startup_ready signal.")
        self.startup_ready.emit()

        # Run deferred startup dialogs once MainWindow is visible
        from utils.config_manager import ConfigManager
        cfg = ConfigManager.load_config()
        if cfg.get("check_dependencies_on_startup", True):
            missing = EnvironmentDoctor.check_missing()
            if missing:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(800, self.open_dependency_dialog)

        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1500, self._check_forge_onboarding)

    def _on_process_action_completed(self, action_name: str, success: bool, message: str):
        if action_name == "lms_start":
            self._startup_milestone_done.emit("llm_server")
        super()._on_process_action_completed(action_name, success, message)

    def _on_server_state_changed(self, old_state: str, new_state: str):
        norm = (new_state or "").lower()
        if norm in ("ready", "error", "stopped"):
            self._startup_milestone_done.emit("llm_server")
        super()._on_server_state_changed(old_state, new_state)

    def _on_knowledge_sync_finished(self, registry_data: dict):
        total = registry_data.get("total_documents", len(registry_data.get("documents", {})))
        logger.info(f"[NeonRoute] Фоновая синхронизация базы знаний завершена: {total} документов.")
        self._startup_milestone_done.emit("knowledge_sync")

    def _sync_splitter_layout(self, is_engineer: bool):
        """
        Динамический расчет геометрии сплиттера под реальную ширину окна.
        Предотвращает сжатие области чата и баги вёрстки.
        """
        if not hasattr(self, "main_splitter") or not self.main_splitter:
            return
        if self.isMinimized():
            return

        total_w = self.main_splitter.width()
        if total_w < 600:
            total_w = self.width()
        if total_w < 600:
            total_w = 1200

        sidebar_w = 270

        if is_engineer:
            self.workspace_inspector.setVisible(True)
            if hasattr(self.workspace_inspector, "refresh_files"):
                self.workspace_inspector.refresh_files()
            inspector_w = 380
            chat_w = max(450, total_w - sidebar_w - inspector_w)
            if self.main_splitter.count() >= 3:
                self.main_splitter.setSizes([sidebar_w, chat_w, inspector_w])
            else:
                self.main_splitter.setSizes([sidebar_w, chat_w])
        else:
            if getattr(self, "_workspace_inspector", None) is not None:
                self._workspace_inspector.setVisible(False)
            chat_w = max(500, total_w - sidebar_w)
            if self.main_splitter.count() >= 3:
                self.main_splitter.setSizes([sidebar_w, chat_w, 0])
            else:
                self.main_splitter.setSizes([sidebar_w, chat_w])

    @property
    def workspace_inspector(self) -> WorkspaceInspectorWidget:
        """Ленивый геттер инспектора песочницы: инициализирует тяжелые виджеты только при реальной необходимости."""
        if getattr(self, "_workspace_inspector", None) is None:
            self._init_workspace_inspector()
        return self._workspace_inspector

    @workspace_inspector.setter
    def workspace_inspector(self, val):
        self._workspace_inspector = val

    def _init_workspace_inspector(self) -> WorkspaceInspectorWidget:
        if getattr(self, "_workspace_inspector", None) is not None:
            return self._workspace_inspector

        inspector = WorkspaceInspectorWidget(self)
        inspector.setVisible(False)
        inspector.rename_session_requested.connect(self._on_rename_session_requested)
        if hasattr(self, "main_splitter") and self.main_splitter is not None:
            self.main_splitter.addWidget(inspector)
            self.main_splitter.setStretchFactor(2, 0)

        if getattr(self, "current_session", None) and isinstance(self.current_session, dict):
            sess_title = self.current_session.get("title", "Песочница")
            inspector.update_title(sess_title)

        self._workspace_inspector = inspector
        return self._workspace_inspector

    def toggle_workspace_inspector(self):
        if not self.is_engineer_mode_active():
            return
        is_visible = not self.workspace_inspector.isVisible()
        self._sync_splitter_layout(is_engineer=is_visible)

    def _update_mode_ui(self):
        is_eng = self.is_engineer_mode_active()
        is_rp = (self.current_mode == "roleplay" and not is_eng)

        # Делегируем сайдбару отображение состояния компаньона
        has_user_messages = False
        if self.current_session and self.current_session.get("messages"):
            has_user_messages = any(m.get("is_user") for m in self.current_session["messages"])

        if hasattr(self, "sidebar") and hasattr(self.sidebar, "update_companion_state"):
            self.sidebar.update_companion_state(
                is_roleplay=is_rp,
                active_character=getattr(self, "active_character", None),
                has_user_messages=has_user_messages
            )

        if is_eng:
            if hasattr(self, "btn_forge_toggle"):
                self.btn_forge_toggle.setVisible(False)
            workspace_path = SandboxManager.get_workspace_dir() if hasattr(SandboxManager, "get_workspace_dir") else WORKSPACE_DIR
            self.statusBar().showMessage(f"Режим: 🛠️ Инженер (Песочница {workspace_path})")
        elif self.current_mode == "assistant":
            if hasattr(self, "btn_forge_toggle"):
                self.btn_forge_toggle.setVisible(True)
            self.statusBar().showMessage("Режим: 💬 Ассистент (Элария)")
        else:
            if hasattr(self, "btn_forge_toggle"):
                self.btn_forge_toggle.setVisible(True)
            self.statusBar().showMessage("Режим: Ролевой чат (Импорт Chub/Tavern JSON)")

        if hasattr(self, "btn_scene_bg"):
            self.btn_scene_bg.setVisible(is_rp)
        if hasattr(self, "btn_toggle_inspector"):
            self.btn_toggle_inspector.setVisible(is_eng)

        self._update_header_for_current_state()
        # singleShot гарантирует, что sizeHint кнопок и visibility всех виджетов применились
        QTimer.singleShot(0, lambda: self._sync_splitter_layout(is_eng))

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
    def _on_engineer_plan_card(self, card_text: str):
        if hasattr(self, "_display_engineer_plan_card"):
            self._display_engineer_plan_card(card_text)

    @pyqtSlot(str, str, str)
    def _on_engineer_thought(self, phase: str, ev_type: str, ev_data: str):
        if hasattr(self, "_display_engineer_thought"):
            self._display_engineer_thought(phase, ev_type, ev_data)

    @pyqtSlot(str, str, int, str)
    def _on_inspector_status(self, status: str, filename: str, line_num: int, error_msg: str):
        inspector = getattr(self, "workspace_inspector", None)
        if not inspector:
            return
        if status == "running":
            inspector.mark_running()
        elif status == "success":
            inspector.mark_success()
        elif status == "error":
            inspector.show_error(filename, line_num, error_msg)

    def _on_llm_error_occurred(self, error: str):
        """Called directly when LMStudioClient emits error_occurred signal."""
        logger.error(f"LM Studio reported error: {error}")



    def eventFilter(self, watched, event: QEvent):
        """Слушает изменение размера вьюпорта скролл-зоны для идеального центрирования кнопки."""
        try:
            if hasattr(self, "scroll_area") and self.scroll_area:
                if watched is self.scroll_area.viewport():
                    if event.type() == QEvent.Type.Resize:
                        self._reposition_scroll_down_button()
        except RuntimeError:
            pass
        return super().eventFilter(watched, event)

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



    def closeEvent(self, event):
        if hasattr(self, "art_director") and hasattr(self.art_director, "router"):
            try:
                self.art_director.router.cancel_deferred_warmup()
            except Exception:
                pass
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
                self.reminder_controller.cleanup()
            except Exception as e_tray:
                logger.debug(f"Error cleaning up reminder controller: {e_tray}")

        if hasattr(self, "_knowledge_sync_worker") and self._knowledge_sync_worker:
            try:
                if self._knowledge_sync_worker.isRunning():
                    self._knowledge_sync_worker.wait(400)
            except Exception:
                pass

        if hasattr(self, "knowledge_watcher") and self.knowledge_watcher:
            try:
                self.knowledge_watcher.stop()
            except Exception:
                pass

        # Hide main window to give instant responsiveness
        self.hide()

        # Create and display compact glassmorphism progress dialog
        from ui.dialogs.shutdown_dialog import ShutdownProgressDialog
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
                self.titleBar.resize(self.width(), self.titleBar.height())
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
                self.titleBar.resize(self.width(), self.titleBar.height())
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
