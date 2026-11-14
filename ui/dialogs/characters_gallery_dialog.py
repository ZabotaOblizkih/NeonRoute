"""
Characters Gallery Dialog for NeonRoute.
Allows viewing, editing, creating, and importing character cards for Roleplay mode.
"""

import html
import hashlib
import re
import json
import shutil
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Union

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QTimer, QRect, QRectF
from PyQt6.QtGui import (
    QIcon, QPixmap, QColor, QPainter, QPainterPath,
    QBrush, QPen, QLinearGradient, QRadialGradient, QFont, QImage
)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTextEdit, QTextBrowser, QPushButton, QListWidget, QListWidgetItem,
    QFileDialog, QMessageBox, QWidget, QScrollArea, QSplitter,
    QFrame, QApplication, QCheckBox, QGraphicsDropShadowEffect,
    QStyledItemDelegate
)

from ui.components.step_progress_button import StepProgressButton
from qfluentwidgets import (
    SearchLineEdit,
    PushButton,
    PrimaryPushButton,
    TransparentPushButton,
    SmoothScrollArea,
    TextEdit,
    LineEdit,
    CheckBox,
    FluentIcon as FIF,
)

from config import CHARACTERS_DIR, DATA_DIR
from services.tavern_parser import TavernParser, CharacterCard
from services.token_budget_manager import TokenBudgetManager
from services.task_queue import InferenceTask
from services.chub_client import sanitize_card_description
from services.card_translator import (
    CardTranslator,
    mask_card_tags,
    unmask_card_tags,
    RE_TAG_PATTERN,
    RE_CJK_PATTERN
)
from ui.components.chat_bubble import render_circular_avatar
from ui.dialogs.avatar_crop_dialog import AvatarCropDialog
from ui.dialogs.local_lora_dialog import LocalLoraDialog
from ui.dialogs.card_visual_setup_dialog import CardVisualSetupDialog
from ui.dialogs.gallery import GalleryDnaPartyMixin, GalleryTranslationMixin

logger = logging.getLogger("NeonRoute.CharactersGallery")

GALLERY_THUMBS_DIR = DATA_DIR / "cache" / "gallery_thumbs"
GALLERY_THUMBS_DIR.mkdir(parents=True, exist_ok=True)

# Псевдонимы функций маскирования для обратной совместимости на уровне модуля
_mask_card_tags = CardTranslator.mask_tags
_unmask_card_tags = CardTranslator.unmask_tags
_mask_tags = CardTranslator.mask_tags
_unmask_tags = CardTranslator.unmask_tags


# === ВЫДЕЛЕННЫЙ ВИДЖЕТ КАРТОЧКИ И РЕНДЕРИНГ АВАТАРОВ ===
from ui.dialogs.gallery.character_card_widget import (
    CardItemDelegate,
    render_seamless_avatar,
    get_cached_circular_avatar,
    CharacterCardWidget,
)

class CharactersGalleryDialog(QDialog, GalleryDnaPartyMixin, GalleryTranslationMixin):
    """
    Two-pane characters gallery dialog with NeonRoute cyberpunk styling.
    Left: List of characters with circular avatars.
    Right: Detailed view and editing of character card fields.
    """

    start_session_requested = pyqtSignal(object)  # передает CharacterCard
    character_selected = pyqtSignal(object)       # передает CharacterCard (связка с сессией)
    card_saved = pyqtSignal(object)              # передает CharacterCard

    _mask_tags = CardTranslator.mask_tags
    _unmask_tags = CardTranslator.unmask_tags
    _mask_card_tags = CardTranslator.mask_tags
    _unmask_card_tags = CardTranslator.unmask_tags

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Галерея персонажей — NeonRoute")
        self.setModal(True)

        self.characters: List[Dict[str, Any]] = []  # list of {"card": CharacterCard, "path": Path}
        self.active_entry: Optional[Dict[str, Any]] = None
        self.selected_card: Optional[CharacterCard] = None
        self.current_greeting_idx: int = 0
        self._showing_ru: bool = True
        self.active_companion_idx: int = -1
        self.party_buttons: List[QPushButton] = []
        self._pending_visual_setup_path: Optional[str] = None
        self._awaiting_visual_modal_path: Optional[str] = None

        self._active_dialog_tasks: dict[str, dict] = {}
        self._running_analysis_tasks: dict[str, set[str]] = {}  # {normalized_path: set(task_ids)}
        self._task_queue = getattr(parent, "task_queue", None) if parent else None
        if self._task_queue:
            try:
                self._task_queue.task_finished.connect(self._on_queue_task_finished)
                self._task_queue.task_failed.connect(self._on_queue_task_failed)
            except Exception as e:
                logger.debug(f"Failed to connect task_queue signals in gallery: {e}")

        self._apply_styles()
        self._setup_ui()

        screen = QApplication.primaryScreen().availableGeometry()
        target_w = min(1280, max(1020, int(screen.width() * 0.75)))
        target_h = min(850, max(620, int(screen.height() * 0.85)))
        self.resize(target_w, target_h)
        self.setMinimumSize(980, 580)
        self.splitter.setSizes([320, target_w - 320])

        self._load_characters()

    def _ensure_task_queue_connected(self):
        """Гарантирует однократное подключение сигналов очереди задач."""
        if not self._task_queue:
            parent_win = self.parent()
            self._task_queue = getattr(parent_win, "task_queue", None) if parent_win else None
            if self._task_queue:
                try:
                    self._task_queue.task_finished.connect(self._on_queue_task_finished)
                    self._task_queue.task_failed.connect(self._on_queue_task_failed)
                except Exception as e:
                    logger.debug(f"Failed to connect task_queue signals: {e}")
        return self._task_queue

    def _on_queue_task_finished(self, task_id: str, result: Any):
        """Именованный диспетчер завершения задачи очереди для диалога."""
        task_info = self._active_dialog_tasks.pop(task_id, None)
        if task_info and task_info.get("on_done"):
            try:
                task_info["on_done"](task_id, result)
            except Exception as e:
                logger.error(f"Error in on_done callback for dialog task {task_id}: {e}", exc_info=True)

    def _on_queue_task_failed(self, task_id: str, err: str):
        """Именованный диспетчер ошибки задачи очереди для диалога."""
        task_info = self._active_dialog_tasks.pop(task_id, None)
        if task_info and task_info.get("on_fail"):
            try:
                task_info["on_fail"](task_id, err)
            except Exception as e:
                logger.error(f"Error in on_fail callback for dialog task {task_id}: {e}", exc_info=True)

    def _cleanup_dialog_tasks(self):
        """Отключает сигналы очереди и очищает активные задачи диалога во избежание утечек памяти."""
        # Принудительная отмена фоновых запросов к LLM (защита от RuntimeError при итерации)
        for task_info in list(self._active_dialog_tasks.values()):
            abort_fn = task_info.get("abort_fn")
            if callable(abort_fn):
                try:
                    abort_fn()
                except Exception as e:
                    logger.debug(f"Failed to abort dialog task: {e}")
        if self._task_queue:
            try:
                self._task_queue.task_finished.disconnect(self._on_queue_task_finished)
            except Exception:
                pass
            try:
                self._task_queue.task_failed.disconnect(self._on_queue_task_failed)
            except Exception:
                pass
            self._task_queue = None
        self._active_dialog_tasks.clear()
        self._running_analysis_tasks.clear()

    def closeEvent(self, event):
        self._cleanup_dialog_tasks()
        super().closeEvent(event)

    def reject(self):
        self._cleanup_dialog_tasks()
        super().reject()

    def accept(self):
        self._cleanup_dialog_tasks()
        super().accept()

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: rgba(14, 19, 31, 0.96);
                color: #CBD5E1;
                border: 1px solid rgba(0, 240, 255, 0.22);
                border-radius: 14px;
            }
            QLabel {
                color: #94A3B8;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.4px;
            }
            QLabel#HeaderTitle {
                color: #00F0FF;
                font-size: 16px;
                font-weight: bold;
                letter-spacing: 0.5px;
            }
            QListWidget {
                background-color: rgba(10, 13, 22, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
                padding: 6px;
                outline: none;
            }
            QListWidget::item {
                background-color: transparent;
                color: transparent;
                border: none;
                padding: 0px;
                margin-bottom: 4px;
            }
            QListWidget::item:selected {
                background-color: transparent;
                color: transparent;
                border: none;
            }
            QLineEdit, TextEdit, QTextEdit {
                background-color: rgba(10, 13, 20, 0.75);
                color: #F8FAFC;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 6px 8px;
                font-size: 12px;
                selection-background-color: #00F0FF;
                selection-color: #0A0D14;
            }
            QLineEdit:focus, TextEdit:focus, QTextEdit:focus {
                border: 1px solid rgba(0, 240, 255, 0.55);
                background-color: rgba(12, 17, 28, 0.90);
            }
            SmoothScrollArea, QScrollArea {
                border: none;
                background-color: transparent;
            }
            QFrame#EditorFrame {
                background-color: rgba(18, 24, 38, 0.60);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
            }
            QPushButton#btn_tr_field, PushButton#btn_tr_field {
                background-color: rgba(18, 30, 49, 0.80);
                border: 1px solid rgba(30, 58, 95, 0.80);
                color: #38BDF8;
                font-size: 11px;
                padding: 3px 8px;
                border-radius: 6px;
            }
            QPushButton#btn_tr_field:hover, PushButton#btn_tr_field:hover {
                background-color: rgba(26, 46, 76, 0.90);
                border-color: #00F0FF;
                color: #00F0FF;
            }
            QPushButton#btn_page_nav, PushButton#btn_page_nav {
                background-color: rgba(20, 28, 43, 0.80);
                border: 1px solid rgba(30, 46, 72, 0.80);
                color: #00F0FF;
                font-weight: bold;
                padding: 2px 6px;
                border-radius: 4px;
            }
            QPushButton#btn_page_nav:hover, PushButton#btn_page_nav:hover {
                background-color: rgba(30, 45, 72, 0.95);
                border-color: #00F0FF;
            }
            QPushButton#btn_page_nav:disabled, PushButton#btn_page_nav:disabled {
                color: #475569;
                border-color: rgba(30, 41, 59, 0.50);
            }
        """)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 16)
        root_layout.setSpacing(12)

        # Header title
        header_lbl = QLabel("🎴 ГАЛЕРЕЯ ПЕРСОНАЖЕЙ")
        header_lbl.setObjectName("HeaderTitle")
        root_layout.addWidget(header_lbl)

        # Main splitter (Left: List, Right: Editor)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # --- LEFT PANEL ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        left_lbl = QLabel("ДОСТУПНЫЕ КАРТОЧКИ:")
        left_layout.addWidget(left_lbl)

        # Строка поиска компаньона (SearchLineEdit)
        self.search_bar = SearchLineEdit(left_widget)
        self.search_bar.setPlaceholderText("🔍 Поиск персонажа...")
        self.search_bar.setClearButtonEnabled(True)
        self.search_bar.textChanged.connect(self._on_search_text_changed)
        left_layout.addWidget(self.search_bar)

        self.list_widget = QListWidget()
        self.list_widget.setItemDelegate(CardItemDelegate(self.list_widget))
        self.list_widget.setIconSize(QSize(44, 44))
        self.list_widget.currentItemChanged.connect(self._on_character_selected)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_list_context_menu)
        left_layout.addWidget(self.list_widget)

        self.splitter.addWidget(left_widget)

        # --- RIGHT PANEL (EDITOR) ---
        right_container = QFrame()
        right_container.setObjectName("EditorFrame")
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(10)

        # Scroll Area for editor fields
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        form_layout = QVBoxLayout(scroll_content)
        form_layout.setContentsMargins(0, 0, 6, 0)
        form_layout.setSpacing(10)

        # Avatar and Name Header Row
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        self.avatar_preview_lbl = QLabel()
        self.avatar_preview_lbl.setObjectName("AvatarPreviewLbl")
        self.avatar_preview_lbl.setFixedSize(64, 64)
        self.avatar_preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_preview_lbl.setStyleSheet("""
            QLabel {
                border: none;
                background-color: transparent;
            }
        """)
        top_row.addWidget(self.avatar_preview_lbl)

        name_box = QVBoxLayout()
        name_box.setSpacing(4)
        name_hdr = QHBoxLayout()
        name_hdr.addWidget(QLabel("ИМЯ ПЕРСОНАЖА:"))
        self.lbl_group_status = QLabel("")
        self.lbl_group_status.setStyleSheet("color: #00F0FF; font-weight: bold; font-size: 11px;")
        name_hdr.addWidget(self.lbl_group_status)

        self.lbl_author_info = QLabel("")
        self.lbl_author_info.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: normal; margin-left: 6px;")
        name_hdr.addWidget(self.lbl_author_info)

        name_hdr.addStretch()
        name_box.addLayout(name_hdr)

        self.input_name = LineEdit()
        self.input_name.setPlaceholderText("Имя персонажа...")
        name_box.addWidget(self.input_name)
        top_row.addLayout(name_box, stretch=1)

        self.btn_change_avatar = PushButton(FIF.PHOTO, "🖼️ Аватар...")
        self.btn_change_avatar.clicked.connect(self._change_avatar)
        top_row.addWidget(self.btn_change_avatar)

        self.btn_translate_all = StepProgressButton(FIF.LANGUAGE, "🌐 Перевести всё (RU)")
        self.btn_translate_all.clicked.connect(self._on_translate_all_clicked)
        top_row.addWidget(self.btn_translate_all)

        self.btn_toggle_lang = PushButton("🌐 RU / EN")
        self.btn_toggle_lang.setObjectName("btn_tr_field")
        self.btn_toggle_lang.setToolTip("Переключить отображение между оригиналом и русским переводом (RU/EN)")
        self.btn_toggle_lang.clicked.connect(self._toggle_card_language)
        top_row.addWidget(self.btn_toggle_lang)

        form_layout.addLayout(top_row)

        # Traffic light frame placed immediately below character name
        self.traffic_light_frame = QFrame()
        self.traffic_light_frame.setStyleSheet("""
            QFrame {
                background-color: #0A0E18;
                border: 1px solid #1E283D;
                border-radius: 8px;
                padding: 6px 10px;
                margin-top: 2px;
                margin-bottom: 4px;
            }
        """)
        tl_layout = QHBoxLayout(self.traffic_light_frame)
        tl_layout.setContentsMargins(4, 2, 4, 2)
        self.lbl_traffic_light = QLabel("🟢 Оптимальный объем: 0 токенов")
        self.lbl_traffic_light.setStyleSheet("font-size: 11px; font-weight: bold;")
        tl_layout.addWidget(self.lbl_traffic_light)
        form_layout.addWidget(self.traffic_light_frame)

        # Информационный баннер фоновой обработки карточки
        self.processing_banner = QFrame()
        self.processing_banner.setStyleSheet("""
            QFrame {
                background-color: #121E31;
                border: 1px solid #00F0FF;
                border-radius: 6px;
                padding: 6px 10px;
                margin-top: 2px;
                margin-bottom: 4px;
            }
        """)
        pb_layout = QHBoxLayout(self.processing_banner)
        pb_layout.setContentsMargins(6, 4, 6, 4)
        self.lbl_processing_text = QLabel("⏳ Идет обработка карточки: анализ внешности, сопартийцев и окружения для Stable Diffusion...")
        self.lbl_processing_text.setStyleSheet("color: #00F0FF; font-size: 11px; font-weight: bold;")
        pb_layout.addWidget(self.lbl_processing_text)
        self.processing_banner.setVisible(False)
        form_layout.addWidget(self.processing_banner)

        # --- MODULAR SECTION CONTAINERS ---

        # 1. Visual DNA
        self.sec_dna = QWidget()
        sec_dna_layout = QVBoxLayout(self.sec_dna)
        sec_dna_layout.setContentsMargins(0, 0, 0, 0)
        sec_dna_layout.setSpacing(4)

        # Верхняя строка секции Visual DNA
        self.dna_header_widget = QWidget()
        dna_header_layout = QHBoxLayout(self.dna_header_widget)
        dna_header_layout.setContentsMargins(0, 0, 0, 2)
        dna_header_layout.setSpacing(8)

        self.lbl_dna_title = QLabel("🎨 ВНЕШНОСТЬ И ДЕТАЛИ ОБРАЗА (STABLE DIFFUSION):")
        self.lbl_dna_title.setStyleSheet("color: #00F0FF; font-size: 11px; font-weight: bold;")
        dna_header_layout.addWidget(self.lbl_dna_title)

        dna_header_layout.addStretch()

        # Кнопка прямой генерации арта из галереи
        self.btn_gallery_generate_art = PushButton(FIF.PALETTE, "🎨 Сгенерировать арт")
        self.btn_gallery_generate_art.setObjectName("btn_tr_field")
        self.btn_gallery_generate_art.setToolTip("Запустить фоновую генерацию арта в SD Forge по текущему позитивному промпту")
        self.btn_gallery_generate_art.clicked.connect(self._on_gallery_generate_art_clicked)
        dna_header_layout.addWidget(self.btn_gallery_generate_art)

        self.btn_find_lora = PushButton(FIF.SEARCH, "🔍 Найти LoRA на Civitai")
        self.btn_find_lora.setObjectName("btn_tr_field")
        self.btn_find_lora.setToolTip("Найти и скачать персонажную LoRA для текущего персонажа с Civitai")
        self.btn_find_lora.clicked.connect(self._on_find_lora_clicked)
        dna_header_layout.addWidget(self.btn_find_lora)

        self.btn_local_lora = PushButton(FIF.FOLDER, "📁 Локальная LoRA")
        self.btn_local_lora.setObjectName("btn_tr_field")
        self.btn_local_lora.setToolTip("Выбрать и привязать имеющийся на диске файл LoRA из папки Forge")
        self.btn_local_lora.clicked.connect(self._on_choose_local_lora_clicked)
        dna_header_layout.addWidget(self.btn_local_lora)

        sec_dna_layout.addWidget(self.dna_header_widget)

        # Динамический селектор отряда (скрыт по умолчанию)
        self.party_selector_container = QWidget()
        self.party_selector_layout = QHBoxLayout(self.party_selector_container)
        self.party_selector_layout.setContentsMargins(0, 2, 0, 4)
        self.party_selector_layout.setSpacing(6)
        self.party_selector_container.setVisible(False)  # СТРОГО скрыт, пока N <= 1
        sec_dna_layout.addWidget(self.party_selector_container)

        self.input_visual_dna = QTextEdit()
        self.input_visual_dna.setMinimumHeight(60)
        self.input_visual_dna.setMaximumHeight(80)
        self.input_visual_dna.setPlaceholderText("Английские SD-теги: пол, возраст, раса, волосы, глаза, черты лица, телосложение. Без одежды.")
        self.input_visual_dna.setStyleSheet("border: 1px solid #1E3A5F; background-color: #080D18; color: #38BDF8; font-family: 'Consolas', monospace; font-size: 11px;")
        sec_dna_layout.addWidget(self.input_visual_dna)
        form_layout.addWidget(self.sec_dna)

        # 2. Description
        self.sec_desc = QWidget()
        sec_desc_layout = QVBoxLayout(self.sec_desc)
        sec_desc_layout.setContentsMargins(0, 0, 0, 0)
        sec_desc_layout.setSpacing(4)
        desc_hdr = QHBoxLayout()
        desc_hdr.addWidget(QLabel("ОПИСАНИЕ (DESCRIPTION):"))
        desc_hdr.addStretch()
        self.btn_tr_desc = PushButton(FIF.LANGUAGE, "🌐 Перевести")
        self.btn_tr_desc.setObjectName("btn_tr_field")
        self.btn_tr_desc.clicked.connect(lambda: self._translate_field(self.input_desc, "description", self.btn_tr_desc))
        desc_hdr.addWidget(self.btn_tr_desc)
        sec_desc_layout.addLayout(desc_hdr)
        self.input_desc = QTextEdit()
        self.input_desc.setMinimumHeight(85)
        self.input_desc.setPlaceholderText("Внешность, история, личность и черты персонажа...")
        sec_desc_layout.addWidget(self.input_desc)
        form_layout.addWidget(self.sec_desc)

        # 3. Personality
        self.sec_pers = QWidget()
        sec_pers_layout = QVBoxLayout(self.sec_pers)
        sec_pers_layout.setContentsMargins(0, 0, 0, 0)
        sec_pers_layout.setSpacing(4)
        pers_hdr = QHBoxLayout()
        pers_hdr.addWidget(QLabel("ХАРАКТЕР / ЧЕРТЫ (PERSONALITY):"))
        pers_hdr.addStretch()
        self.btn_tr_pers = PushButton(FIF.LANGUAGE, "🌐 Перевести")
        self.btn_tr_pers.setObjectName("btn_tr_field")
        self.btn_tr_pers.clicked.connect(lambda: self._translate_field(self.input_personality, "personality", self.btn_tr_pers))
        pers_hdr.addWidget(self.btn_tr_pers)
        sec_pers_layout.addLayout(pers_hdr)
        self.input_personality = QTextEdit()
        self.input_personality.setMinimumHeight(65)
        self.input_personality.setPlaceholderText("Характер, поведение, психологические черты...")
        sec_pers_layout.addWidget(self.input_personality)
        form_layout.addWidget(self.sec_pers)

        # 4. Scenario
        self.sec_scen = QWidget()
        sec_scen_layout = QVBoxLayout(self.sec_scen)
        sec_scen_layout.setContentsMargins(0, 0, 0, 0)
        sec_scen_layout.setSpacing(4)
        scen_hdr = QHBoxLayout()
        scen_hdr.addWidget(QLabel("СЦЕНАРИЙ (SCENARIO / SETTING):"))
        scen_hdr.addStretch()
        self.btn_tr_scen = PushButton(FIF.LANGUAGE, "🌐 Перевести")
        self.btn_tr_scen.setObjectName("btn_tr_field")
        self.btn_tr_scen.clicked.connect(lambda: self._translate_field(self.input_scenario, "scenario", self.btn_tr_scen))
        scen_hdr.addWidget(self.btn_tr_scen)
        sec_scen_layout.addLayout(scen_hdr)
        self.input_scenario = QTextEdit()
        self.input_scenario.setMinimumHeight(70)
        self.input_scenario.setPlaceholderText("Обстановка, сцена знакомства, текущий контекст...")
        sec_scen_layout.addWidget(self.input_scenario)
        form_layout.addWidget(self.sec_scen)

        # 5. System Prompt (Author Directives)
        self.sec_system_prompt = QWidget()
        sec_sys_layout = QVBoxLayout(self.sec_system_prompt)
        sec_sys_layout.setContentsMargins(0, 0, 0, 0)
        sec_sys_layout.setSpacing(4)
        sys_hdr = QHBoxLayout()
        sys_hdr.addWidget(QLabel("АВТОРСКИЕ ПРАВИЛА / СИСТЕМНЫЙ ПРОМПТ (SYSTEM PROMPT):"))
        sys_hdr.addStretch()
        sec_sys_layout.addLayout(sys_hdr)
        self.input_system_prompt = QTextEdit()
        self.input_system_prompt.setMinimumHeight(70)
        self.input_system_prompt.setPlaceholderText("Инструкции ведущего, правила форматирования, скрытые ограничения модели...")
        self.input_system_prompt.setStyleSheet("border: 1px solid #2B1E3F; background-color: #0A0814; color: #DDD6FE; font-size: 11px;")
        sec_sys_layout.addWidget(self.input_system_prompt)
        form_layout.addWidget(self.sec_system_prompt)

        # 6. Dialogue Examples
        self.sec_mes_example = QWidget()
        sec_ex_layout = QVBoxLayout(self.sec_mes_example)
        sec_ex_layout.setContentsMargins(0, 0, 0, 0)
        sec_ex_layout.setSpacing(4)
        ex_hdr = QHBoxLayout()
        ex_hdr.addWidget(QLabel("ПРИМЕРЫ ДИАЛОГА (MES_EXAMPLE):"))
        ex_hdr.addStretch()
        self.btn_tr_ex = PushButton(FIF.LANGUAGE, "🌐 Перевести")
        self.btn_tr_ex.setObjectName("btn_tr_field")
        self.btn_tr_ex.clicked.connect(self._translate_mes_example)
        ex_hdr.addWidget(self.btn_tr_ex)
        sec_ex_layout.addLayout(ex_hdr)
        self.input_mes_example = QTextEdit()
        self.input_mes_example.setMinimumHeight(80)
        self.input_mes_example.setPlaceholderText("Примеры реплик, диалогов и манеры речи персонажа...")
        sec_ex_layout.addWidget(self.input_mes_example)
        form_layout.addWidget(self.sec_mes_example)

        # 7. First Message
        self.sec_first_mes = QWidget()
        sec_first_layout = QVBoxLayout(self.sec_first_mes)
        sec_first_layout.setContentsMargins(0, 0, 0, 0)
        sec_first_layout.setSpacing(4)
        first_mes_hdr = QHBoxLayout()
        first_mes_hdr.addWidget(QLabel("ПЕРВОЕ СООБЩЕНИЕ:"))
        first_mes_hdr.addStretch()

        self.btn_prev_greeting = PushButton("◀")
        self.btn_prev_greeting.setObjectName("btn_page_nav")
        self.btn_prev_greeting.setFixedWidth(28)
        self.btn_prev_greeting.setFixedHeight(24)
        self.btn_prev_greeting.clicked.connect(self._show_prev_greeting)
        first_mes_hdr.addWidget(self.btn_prev_greeting)

        self.lbl_greeting_page = QLabel("1 / 1")
        self.lbl_greeting_page.setStyleSheet("color: #38BDF8; font-weight: bold; padding: 0 4px;")
        first_mes_hdr.addWidget(self.lbl_greeting_page)

        self.btn_next_greeting = PushButton("▶")
        self.btn_next_greeting.setObjectName("btn_page_nav")
        self.btn_next_greeting.setFixedWidth(28)
        self.btn_next_greeting.setFixedHeight(24)
        self.btn_next_greeting.clicked.connect(self._show_next_greeting)
        first_mes_hdr.addWidget(self.btn_next_greeting)

        self.btn_tr_first_mes = PushButton(FIF.LANGUAGE, "🌐 Перевести")
        self.btn_tr_first_mes.setObjectName("btn_tr_field")
        self.btn_tr_first_mes.clicked.connect(self._translate_first_mes)
        first_mes_hdr.addWidget(self.btn_tr_first_mes)

        self.chk_use_as_start = CheckBox("📌 Выбрать для старта")
        self.chk_use_as_start.setToolTip(
            "Если флажок снят — при создании нового чата запустится случайная завязка (рандом).\n"
            "Если флажок установлен — чат начнётся строго с отображаемой сейчас реплики."
        )
        self.chk_use_as_start.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: bold; margin-left: 8px;")
        first_mes_hdr.addWidget(self.chk_use_as_start)
        sec_first_layout.addLayout(first_mes_hdr)

        self.input_first_mes = QTextEdit()
        self.input_first_mes.setMinimumHeight(85)
        self.input_first_mes.setPlaceholderText("Приветственная реплика и вводная сцена персонажа...")
        sec_first_layout.addWidget(self.input_first_mes)
        form_layout.addWidget(self.sec_first_mes)

        # 7.5. Lorebook Entries Inspector Section
        self.sec_lore_entries = QWidget()
        sec_lore_layout = QVBoxLayout(self.sec_lore_entries)
        sec_lore_layout.setContentsMargins(0, 0, 0, 0)
        sec_lore_layout.setSpacing(4)
        self.lbl_lore_header = QLabel("📚 СТАТЬИ СПРАВОЧНИКА / ЛОРБУКА:")
        self.lbl_lore_header.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: bold;")
        sec_lore_layout.addWidget(self.lbl_lore_header)

        self.input_lore_preview = QTextBrowser()
        self.input_lore_preview.setMinimumHeight(95)
        self.input_lore_preview.setMaximumHeight(220)
        self.input_lore_preview.setStyleSheet("""
            QTextBrowser {
                background-color: #070B14;
                border: 1px solid rgba(56, 189, 248, 0.25);
                border-radius: 6px;
                color: #CBD5E1;
                font-family: 'Inter', 'Segoe UI', sans-serif;
                font-size: 11px;
                line-height: 1.3;
                padding: 6px;
            }
        """)
        sec_lore_layout.addWidget(self.input_lore_preview)
        form_layout.addWidget(self.sec_lore_entries)

        # 8. Dynamic Section Addition Bar
        self.add_sections_bar = QWidget()
        add_bar_layout = QHBoxLayout(self.add_sections_bar)
        add_bar_layout.setContentsMargins(0, 6, 0, 2)
        add_bar_layout.setSpacing(6)
        lbl_add_title = QLabel("➕ Добавить секцию:")
        lbl_add_title.setStyleSheet("color: #64748B; font-size: 11px; font-weight: bold;")
        add_bar_layout.addWidget(lbl_add_title)

        self.btn_add_dna = PushButton(FIF.ADD, "+ Внешность SD")
        self.btn_add_dna.setObjectName("btn_tr_field")
        self.btn_add_dna.clicked.connect(lambda: self._reveal_section(self.sec_dna, self.input_visual_dna))
        add_bar_layout.addWidget(self.btn_add_dna)

        self.btn_add_pers = PushButton(FIF.ADD, "+ Характер")
        self.btn_add_pers.setObjectName("btn_tr_field")
        self.btn_add_pers.clicked.connect(lambda: self._reveal_section(self.sec_pers, self.input_personality))
        add_bar_layout.addWidget(self.btn_add_pers)

        self.btn_add_scen = PushButton(FIF.ADD, "+ Сценарий")
        self.btn_add_scen.setObjectName("btn_tr_field")
        self.btn_add_scen.clicked.connect(lambda: self._reveal_section(self.sec_scen, self.input_scenario))
        add_bar_layout.addWidget(self.btn_add_scen)

        self.btn_add_sys = PushButton(FIF.ADD, "+ Авторские правила")
        self.btn_add_sys.setObjectName("btn_tr_field")
        self.btn_add_sys.clicked.connect(lambda: self._reveal_section(self.sec_system_prompt, self.input_system_prompt))
        add_bar_layout.addWidget(self.btn_add_sys)

        self.btn_add_ex = PushButton(FIF.ADD, "+ Примеры диалога")
        self.btn_add_ex.setObjectName("btn_tr_field")
        self.btn_add_ex.clicked.connect(lambda: self._reveal_section(self.sec_mes_example, self.input_mes_example))
        add_bar_layout.addWidget(self.btn_add_ex)

        add_bar_layout.addStretch()
        form_layout.addWidget(self.add_sections_bar)

        # Budget calculation bindings
        self.input_name.textChanged.connect(self._update_context_budget)
        self.input_visual_dna.textChanged.connect(self._update_context_budget)
        self.input_desc.textChanged.connect(self._update_context_budget)
        self.input_personality.textChanged.connect(self._update_context_budget)
        self.input_scenario.textChanged.connect(self._update_context_budget)
        self.input_system_prompt.textChanged.connect(self._update_context_budget)
        self.input_mes_example.textChanged.connect(self._update_context_budget)
        self.input_first_mes.textChanged.connect(self._update_context_budget)

        scroll.setWidget(scroll_content)
        right_layout.addWidget(scroll)

        self.splitter.addWidget(right_container)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 5)

        root_layout.addWidget(self.splitter, stretch=1)

        # --- BOTTOM ACTION BAR ---
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(10)

        self.btn_create = PushButton(FIF.ADD, "+ Создать", self)
        self.btn_create.clicked.connect(self._create_new_character)
        btn_bar.addWidget(self.btn_create)

        self.btn_chub = PushButton(FIF.GLOBE, "🌐 Добавить с Chub.ai", self)
        self.btn_chub.clicked.connect(self._open_chub_catalog)
        btn_bar.addWidget(self.btn_chub)

        self.btn_import = PushButton(FIF.DOWNLOAD, "📥 Импортировать карту", self)
        self.btn_import.clicked.connect(self._import_character)
        btn_bar.addWidget(self.btn_import)

        self.btn_delete = PushButton(FIF.DELETE, "🗑️ Удалить", self)
        self.btn_delete.clicked.connect(self._delete_character)
        btn_bar.addWidget(self.btn_delete)

        btn_bar.addStretch()

        self.btn_save = PushButton(FIF.SAVE, "💾 Сохранить изменения", self)
        self.btn_save.setObjectName("btn_save")
        self.btn_save.clicked.connect(self._save_changes)
        btn_bar.addWidget(self.btn_save)

        self.btn_start_dialog = PrimaryPushButton("🎴 Выбрать компаньона", self)
        self.btn_start_dialog.setObjectName("btn_start_dialog")
        self.btn_start_dialog.clicked.connect(self._start_dialog)
        btn_bar.addWidget(self.btn_start_dialog)

        self.btn_close = TransparentPushButton("✕ Отмена", self)
        self.btn_close.clicked.connect(self.reject)
        btn_bar.addWidget(self.btn_close)

        root_layout.addLayout(btn_bar)

    def _on_search_text_changed(self, text: str):
        """Фильтрует список персонажей в галерее по имени, тегам и описанию."""
        query = text.strip().lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item:
                continue
            if not query:
                item.setHidden(False)
                continue

            json_path_str = item.data(Qt.ItemDataRole.UserRole)
            entry = next((e for e in self.characters if str(e.get("path")) == json_path_str), None)
            card = entry.get("card") if entry else None

            name_match = query in (item.text() or "").lower()
            card_name = (getattr(card, "name", "") or "").lower() if card else ""
            desc_match = (getattr(card, "description", "") or "").lower() if card else ""
            desc_ru_match = (getattr(card, "description_ru", "") or "").lower() if card else ""
            tags = getattr(card, "tags", []) or []
            tags_match = any(query in str(t).lower() for t in tags)

            matched = name_match or (query in card_name) or (query in desc_match) or (query in desc_ru_match) or tags_match
            item.setHidden(not matched)

    def _get_card_author(self, card: CharacterCard) -> str:
        """Извлекает имя автора карточки из метаданных (creator / creator_notes)."""
        raw = card.raw_data if isinstance(card.raw_data, dict) else {}
        target = raw.get("data", raw) if isinstance(raw, dict) else {}
        author = target.get("creator") or raw.get("creator") or ""
        if not author and getattr(card, "creator_notes", ""):
            m = re.search(r'(?:by|автор|creator)[:\s]+([^\n\r]+)', card.creator_notes, re.IGNORECASE)
            if m:
                author = m.group(1).strip()
        return author or ""

    def _load_characters(self, select_path: Optional[Path] = None):
        """Loads all character .json files from CHARACTERS_DIR."""
        self.list_widget.blockSignals(True)
        # Очищаем все привязанные custom-виджеты перед повторным заполнением
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it:
                w = self.list_widget.itemWidget(it)
                if w:
                    self.list_widget.removeItemWidget(it)
                    w.deleteLater()
        self.list_widget.clear()
        self.characters.clear()

        if not CHARACTERS_DIR.exists():
            CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)

        target_row = 0
        json_files = sorted(CHARACTERS_DIR.glob("*.json"), key=lambda p: p.name.lower())

        for idx, json_path in enumerate(json_files):
            try:
                card = TavernParser.parse_file(json_path)
                entry = {"card": card, "path": json_path}
                self.characters.append(entry)

                # Estimate card tokens to assign traffic light dot
                card_fields = {
                    "name": card.name,
                    "visual_dna": getattr(card, "visual_dna", "") or "",
                    "description": card.description_ru or card.description or "",
                    "personality": card.personality_ru or card.personality or "",
                    "scenario": card.scenario_ru or card.scenario or "",
                    "system_prompt": getattr(card, "system_prompt", "") or "",
                    "mes_example": card.mes_example_ru or card.mes_example or "",
                    "first_mes": card.first_mes_ru or card.first_mes or ""
                }
                active_lore_entries = [e for e in (card.lore_entries or []) if getattr(e, "enabled", True)]
                lore_texts = [e.content for e in active_lore_entries if getattr(e, "content", "")]
                base_tokens, lore_tokens = TokenBudgetManager.estimate_card_budget(card_fields, lore_texts)
                is_lore = bool(getattr(card, "is_lorebook", False))
                effective_tokens = (base_tokens + lore_tokens) if is_lore else base_tokens
                traffic = TokenBudgetManager.get_traffic_light(
                    effective_tokens,
                    is_lorebook=is_lore,
                    entry_count=len(active_lore_entries)
                )
                badge = traffic["badge"]

                pix = get_cached_circular_avatar(card.avatar_path, name=card.name, size=44)
                if getattr(card, "is_lorebook", False):
                    type_badge = "📚 [Лорбук] "
                elif getattr(card, "is_group", False):
                    type_badge = "👥 [Групповая] "
                else:
                    type_badge = ""
                # Не передаем QIcon в QListWidgetItem, чтобы нативный рендер Qt не рисовал иконку под карточкой
                item = QListWidgetItem()
                item.setText(f"{card.name}  {type_badge}{badge}")
                item.setData(Qt.ItemDataRole.UserRole, str(json_path))
                item.setSizeHint(QSize(250, 62))

                card_widget = CharacterCardWidget(
                    card.name,
                    pix,
                    badge_text=badge,
                    type_badge=type_badge
                )
                card_widget.clicked.connect(lambda it=item: self.list_widget.setCurrentItem(it))
                card_widget.double_clicked.connect(self._start_dialog)

                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, card_widget)

                if select_path and json_path.resolve() == select_path.resolve():
                    target_row = self.list_widget.count() - 1
            except Exception as e:
                logger.warning(f"Error parsing character file {json_path}: {e}")

        self.list_widget.blockSignals(False)

        if hasattr(self, "search_bar") and self.search_bar.text().strip():
            self._on_search_text_changed(self.search_bar.text())

        if self.characters:
            self.list_widget.setCurrentRow(target_row)
        else:
            self.active_entry = None
            self._clear_editor()

    load_characters = _load_characters

    def _on_character_selected(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem] = None):
        """Populates editor with selected character details."""
        # Update selection visuals for all CharacterCardWidget items
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            w = self.list_widget.itemWidget(it)
            if isinstance(w, CharacterCardWidget):
                w.set_selected(it == current)

        if not current:
            self.active_entry = None
            self._clear_editor()
            return

        json_path_str = current.data(Qt.ItemDataRole.UserRole)
        found = next((e for e in self.characters if str(e["path"]) == json_path_str), None)
        if not found:
            return

        self.active_entry = found
        card: CharacterCard = found["card"]
        self.current_greeting_idx = 0
        has_ru_translation = bool(
            (card.description_ru and re.search(r'[а-яА-ЯёЁ]', card.description_ru))
            or (card.first_mes_ru and re.search(r'[а-яА-ЯёЁ]', card.first_mes_ru))
        )
        self._showing_ru = has_ru_translation
        if hasattr(self, "btn_toggle_lang"):
            lang_badge = "RU" if self._showing_ru else "ORIG"
            self.btn_toggle_lang.setText(f"🌐 RU / EN [{lang_badge}]")

        self.input_name.setText(card.name)
        self.input_visual_dna.setPlainText(getattr(card, "visual_dna", "") or "")
        if self._showing_ru:
            self.input_desc.setPlainText(card.description_ru or card.description or "")
            self.input_personality.setPlainText(card.personality_ru or card.personality or "")
            self.input_scenario.setPlainText(card.scenario_ru or card.scenario or "")
            self.input_mes_example.setPlainText(card.mes_example_ru or card.mes_example or "")
        else:
            self.input_desc.setPlainText(card.description or "")
            self.input_personality.setPlainText(card.personality or "")
            self.input_scenario.setPlainText(card.scenario or "")
            self.input_mes_example.setPlainText(card.mes_example or "")
        self.input_system_prompt.setPlainText(getattr(card, "system_prompt", "") or "")
        self.current_greeting_idx = 0
        self.chk_use_as_start.setChecked(False)
        self._update_greeting_display()

        # Update visibility of modular sections
        self._update_sections_visibility()

        is_lore = getattr(card, "is_lorebook", False)
        is_grp = getattr(card, "is_group", False)

        if is_lore:
            self.lbl_group_status.setText("📚 [Модуль правил / Лорбук]")
            self.lbl_group_status.setStyleSheet("color: #F59E0B; font-weight: bold; font-size: 11px;")
        elif is_grp:
            self.lbl_group_status.setText("👥 [Групповая карточка]")
            self.lbl_group_status.setStyleSheet("color: #00F0FF; font-weight: bold; font-size: 11px;")
        else:
            self.lbl_group_status.setText("")

        author = self._get_card_author(card)
        if hasattr(self, "lbl_author_info"):
            if author:
                self.lbl_author_info.setText(f"✍️ {author}")
                self.lbl_author_info.setToolTip(f"Автор карточки: {author}")
            else:
                self.lbl_author_info.setText("✍️ Автор не указан")
                self.lbl_author_info.setToolTip("")

        pix = render_seamless_avatar(card.avatar_path, name=card.name, size=64)
        self.avatar_preview_lbl.setPixmap(pix)
        self._update_context_budget()

        self.active_companion_idx = -1
        resolved_card_path = str(Path(found["path"]).resolve())
        if getattr(self, "_pending_visual_setup_path", None) == resolved_card_path:
            self._pending_visual_setup_path = None
            self._awaiting_visual_modal_path = resolved_card_path
            if not getattr(card, "scenery_dna", "").strip():
                self._extract_and_apply_scenery_dna(card, Path(found["path"]))
        self._check_and_extract_party(card, Path(found["path"]))

        self._update_card_analysis_ui()

    def _reveal_section(self, section_widget: QWidget, target_input: QTextEdit):
        section_widget.setVisible(True)
        self._refresh_add_buttons_bar()
        target_input.setFocus()

    def _refresh_add_buttons_bar(self):
        is_lore = bool(self.active_entry and getattr(self.active_entry["card"], "is_lorebook", False))
        self.btn_add_dna.setVisible(self.sec_dna.isHidden() and not is_lore)
        self.btn_add_pers.setVisible(self.sec_pers.isHidden())
        self.btn_add_scen.setVisible(self.sec_scen.isHidden())
        self.btn_add_sys.setVisible(self.sec_system_prompt.isHidden())
        self.btn_add_ex.setVisible(self.sec_mes_example.isHidden() and not is_lore)

        has_any_hidden = any([
            not self.btn_add_dna.isHidden(),
            not self.btn_add_pers.isHidden(),
            not self.btn_add_scen.isHidden(),
            not self.btn_add_sys.isHidden(),
            not self.btn_add_ex.isHidden()
        ])
        self.add_sections_bar.setVisible(has_any_hidden)

    def _update_sections_visibility(self):
        if not self.active_entry:
            return
        card: CharacterCard = self.active_entry["card"]
        is_lore = getattr(card, "is_lorebook", False)

        entries = card.lore_entries if card and hasattr(card, "lore_entries") else []
        active_entries = [e for e in entries if getattr(e, "enabled", True) is not False]
        has_lore = len(active_entries) > 0

        # Populate lore entries preview if present
        if has_lore:
            lore_blocks = []
            for idx, entry in enumerate(active_entries, 1):
                raw_keys = getattr(entry, "keys", None) or (entry.get("keys") if isinstance(entry, dict) else [])
                if isinstance(raw_keys, list):
                    keys_str = ", ".join(str(k) for k in raw_keys if k)
                else:
                    keys_str = str(raw_keys)
                comment = getattr(entry, "comment", "") or getattr(entry, "name", "") or f"Запись #{idx}"
                content = (getattr(entry, "content", "") or getattr(entry, "text", "")).strip()
                preview = content[:280] + ("..." if len(content) > 280 else "")

                lore_blocks.append(
                    f"<div style='margin-bottom:8px; padding-bottom:6px; border-bottom:1px solid rgba(255,255,255,0.08);'>"
                    f"<div style='color:#38BDF8; font-weight:bold;'>📌 {html.escape(comment)} "
                    f"<span style='color:#64748B; font-size:10px;'>[Ключи: {html.escape(keys_str or 'всегда активна')}]</span></div>"
                    f"<div style='color:#94A3B8; margin-top:2px;'>{html.escape(preview)}</div></div>"
                )
            self.lbl_lore_header.setText(f"📚 СТАТЬИ СПРАВОЧНИКА / ЛОРБУКА ({len(active_entries)} записей):")
            self.input_lore_preview.setHtml("".join(lore_blocks))
            self.sec_lore_entries.setVisible(True)
        else:
            self.sec_lore_entries.setVisible(False)

        if is_lore:
            self.sec_dna.setVisible(False)
            self.sec_first_mes.setVisible(False)
            self.sec_mes_example.setVisible(False)
            self.sec_desc.setVisible(True)
            self.sec_pers.setVisible(bool(self.input_personality.toPlainText().strip()))
            self.sec_scen.setVisible(bool(self.input_scenario.toPlainText().strip()))
            self.sec_system_prompt.setVisible(bool(self.input_system_prompt.toPlainText().strip()))
        else:
            self.sec_desc.setVisible(True)
            self.sec_first_mes.setVisible(True)
            # Секция внешности ВСЕГДА открыта для персонажей, чтобы LoRA и теги можно было задать в любой момент
            self.sec_dna.setVisible(True)
            self.sec_pers.setVisible(bool(self.input_personality.toPlainText().strip()))
            self.sec_scen.setVisible(bool(self.input_scenario.toPlainText().strip()))
            self.sec_system_prompt.setVisible(bool(self.input_system_prompt.toPlainText().strip()))
            self.sec_mes_example.setVisible(bool(self.input_mes_example.toPlainText().strip()))

        self._refresh_add_buttons_bar()

    def _update_context_budget(self, *args):
        card = self.active_entry["card"] if self.active_entry else None
        is_lore = bool(card and getattr(card, "is_lorebook", False))

        card_fields = {
            "name": self.input_name.text(),
            "visual_dna": self.input_visual_dna.toPlainText(),
            "description": self.input_desc.toPlainText(),
            "personality": self.input_personality.toPlainText(),
            "scenario": self.input_scenario.toPlainText(),
            "system_prompt": getattr(self, "input_system_prompt", QTextEdit()).toPlainText() if hasattr(self, "input_system_prompt") else "",
            "mes_example": self.input_mes_example.toPlainText(),
            "first_mes": self.input_first_mes.toPlainText()
        }

        active_entries = [e for e in (card.lore_entries if card and hasattr(card, "lore_entries") else []) if getattr(e, "enabled", True)]
        entry_cnt = len(active_entries)
        lore_texts = [e.content for e in active_entries if getattr(e, "content", "")]

        base_tokens, lore_tokens = TokenBudgetManager.estimate_card_budget(card_fields, lore_texts)
        effective_tokens = (base_tokens + lore_tokens) if is_lore else base_tokens

        traffic = TokenBudgetManager.get_traffic_light(
            effective_tokens,
            is_lorebook=is_lore,
            entry_count=entry_cnt
        )

        status = traffic["status"]
        if not is_lore and lore_tokens > 0:
            status += f"  •  📚 Лор-пул: ~{lore_tokens} токенов ({entry_cnt} ст.)"

        self.lbl_traffic_light.setText(status)
        self.lbl_traffic_light.setStyleSheet(f"color: {traffic['color']}; font-size: 11px; font-weight: bold;")
        self.traffic_light_frame.setStyleSheet(f"background-color: #0A0E18; border: 1px solid {traffic['border']}; border-radius: 8px; padding: 6px 10px; margin-top: 2px; margin-bottom: 4px;")

        curr = self.list_widget.currentItem()
        if curr:
            raw_title = self.input_name.text().strip() or "Персонаж"
            type_badge = "📚 [Лорбук] " if is_lore else ("👥 [Групповая] " if (card and getattr(card, "is_group", False)) else "")
            curr.setText(f"{raw_title}  {type_badge}{traffic['badge']}")
            w = self.list_widget.itemWidget(curr)
            if isinstance(w, CharacterCardWidget):
                w.update_info(raw_title, badge_text=traffic["badge"], type_badge=type_badge)

    def _clear_editor(self):
        self.input_name.clear()
        if hasattr(self, "lbl_group_status"):
            self.lbl_group_status.clear()
        if hasattr(self, "lbl_author_info"):
            self.lbl_author_info.clear()
        self._update_card_analysis_ui()
        self.input_visual_dna.clear()
        self.input_desc.clear()
        self.input_personality.clear()
        self.input_scenario.clear()
        if hasattr(self, "input_system_prompt"):
            self.input_system_prompt.clear()
        self.input_mes_example.clear()
        self.input_first_mes.clear()
        self.current_greeting_idx = 0
        self.lbl_greeting_page.setText("1 / 1")
        self.btn_prev_greeting.setEnabled(False)
        self.btn_next_greeting.setEnabled(False)
        pix = render_seamless_avatar(None, name="?", size=64)
        self.avatar_preview_lbl.setPixmap(pix)
        self.active_companion_idx = -1
        self._update_party_selector_ui()
        self._update_context_budget()

    def _update_card_analysis_ui(self):
        """Обновляет состояние кнопки выбора компаньона в зависимости от типа карточки и фоновых задач."""
        if not self.active_entry:
            if hasattr(self, "processing_banner"):
                self.processing_banner.setVisible(False)
            self.btn_start_dialog.setEnabled(False)
            self.btn_start_dialog.setText("🎴 Выбрать компаньона")
            self.btn_start_dialog.setToolTip("")
            return

        card: CharacterCard = self.active_entry["card"]
        if getattr(card, "is_lorebook", False):
            if hasattr(self, "processing_banner"):
                self.processing_banner.setVisible(False)
            self.btn_start_dialog.setEnabled(False)
            self.btn_start_dialog.setText("🎴 Выбрать компаньона")
            self.btn_start_dialog.setToolTip(
                "Это модуль правил мира (Лорбук).\n"
                "Подключите его к активной игре через окно «📜 Правила мира и Лор»."
            )
            return

        card_path_key = str(Path(self.active_entry["path"]).resolve())
        active_tasks = self._running_analysis_tasks.get(card_path_key, set())

        if active_tasks:
            self.processing_banner.setVisible(True)
            self.btn_start_dialog.setEnabled(False)
            self.btn_start_dialog.setText("⏳ Распознавание карточки...")
            self.btn_start_dialog.setToolTip("Идет фоновый анализ окружения и параметров карточки через локальную LLM.")
        else:
            self.processing_banner.setVisible(False)
            self.btn_start_dialog.setEnabled(True)
            self.btn_start_dialog.setText("🎴 Выбрать компаньона")
            self.btn_start_dialog.setToolTip("")

    def _open_chub_catalog(self):
        """Открывает нативный браузер каталога Chub.ai и загружает скачанную карточку."""
        from ui.dialogs.chub_catalog_dialog import ChubCatalogDialog
        dlg = ChubCatalogDialog(parent=self)
        dlg.card_downloaded.connect(self._on_chub_card_downloaded)
        dlg.exec()

    def _on_chub_card_downloaded(self, saved_path: Path):
        """Обрабатывает загруженную карточку Chub.ai: обновляет список и запускает извлечение Visual DNA/отряда."""
        self._pending_visual_setup_path = str(saved_path.resolve())
        self._load_characters(select_path=saved_path)

    def _create_new_character(self):
        """Creates a new JSON character card in CHARACTERS_DIR."""
        new_id = f"char_{uuid.uuid4().hex[:8]}"
        new_filename = f"{new_id}.json"
        dest_path = CHARACTERS_DIR / new_filename

        new_data = {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": "Новый персонаж",
                "description": "",
                "personality": "",
                "scenario": "",
                "first_mes": "",
                "mes_example": "",
                "visual_dna": "",
                "alternate_greetings": [],
                "creator_notes": "",
                "tags": []
            }
        }

        try:
            with open(dest_path, "w", encoding="utf-8") as f:
                json.dump(new_data, f, ensure_ascii=False, indent=2)
            self._load_characters(select_path=dest_path)
            self.input_name.setFocus()
            self.input_name.selectAll()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать карточку: {e}")

    def _import_character(self):
        """Imports an external JSON or PNG character card into CHARACTERS_DIR with standardized naming."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Импорт карточки персонажа",
            "",
            "Карточки персонажей (*.json *.png);;JSON (*.json);;PNG (*.png)"
        )
        if not file_path:
            return

        src_path = Path(file_path)
        try:
            CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
            # Предварительный парсинг для извлечения каноничного имени
            temp_card = TavernParser.parse_file(src_path)
            if not temp_card or not temp_card.name:
                char_title = src_path.stem
            else:
                char_title = temp_card.name

            safe_name = "".join(c for c in char_title if c.isalnum() or c in (' ', '_', '-')).strip() or "character"

            if src_path.suffix.lower() == ".png":
                dest_avatar = CHARACTERS_DIR / f"{safe_name}_avatar.png"
                dest_json = CHARACTERS_DIR / f"{safe_name}.json"

                if dest_avatar.exists() and dest_avatar.resolve() != src_path.resolve():
                    dest_avatar = CHARACTERS_DIR / f"{safe_name}_{uuid.uuid4().hex[:4]}_avatar.png"
                if dest_json.exists():
                    dest_json = CHARACTERS_DIR / f"{safe_name}_{uuid.uuid4().hex[:4]}.json"

                if dest_avatar.resolve() != src_path.resolve():
                    shutil.copy2(src_path, dest_avatar)

                card = TavernParser.parse_file(dest_avatar)
                card.avatar_path = dest_avatar
                raw = dict(card.raw_data) if card.raw_data else {}
                if not raw:
                    raw = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {}}
                is_v2 = raw.get("spec") == "chara_card_v2" or "data" in raw
                target = raw.setdefault("data", {}) if is_v2 else raw

                # Санитизация описания и характера при импорте
                clean_desc, extra_notes = sanitize_card_description(card.description or "")
                clean_pers, pers_notes = sanitize_card_description(card.personality or "")

                target["name"] = card.name
                target["description"] = clean_desc
                target["personality"] = clean_pers
                target["scenario"] = card.scenario
                target["first_mes"] = card.first_mes
                target["mes_example"] = card.mes_example
                target["alternate_greetings"] = getattr(card, "alternate_greetings", [])
                target["avatar_path"] = str(dest_avatar.resolve())
                target["visual_dna"] = getattr(card, "visual_dna", "")

                # Сохраняем ссылки и мета-примечания автора в creator_notes, не засоряя prompt
                all_extra = extra_notes + pers_notes
                if all_extra:
                    existing_notes = target.get("creator_notes", "") or getattr(card, "creator_notes", "") or ""
                    merged = [existing_notes] if existing_notes else []
                    merged.extend(all_extra)
                    target["creator_notes"] = "\n\n".join(dict.fromkeys([n for n in merged if n]))

                card.description = clean_desc
                card.personality = clean_pers
                if hasattr(card, "creator_notes") and target.get("creator_notes"):
                    card.creator_notes = target["creator_notes"]

                with open(dest_json, "w", encoding="utf-8") as f:
                    json.dump(raw, f, ensure_ascii=False, indent=2)

                card.source_path = dest_json
                select_target = dest_json
                char_name = card.name
            else:
                dest_path = CHARACTERS_DIR / f"{safe_name}.json"
                if dest_path.exists() and dest_path.resolve() != src_path.resolve():
                    dest_path = CHARACTERS_DIR / f"{safe_name}_{uuid.uuid4().hex[:4]}.json"

                if dest_path.resolve() != src_path.resolve():
                    shutil.copy2(src_path, dest_path)

                # Поиск связанного аватара рядом с исходным файлом
                for ext in [".png", ".jpg", ".jpeg", ".webp"]:
                    candidate_img = src_path.parent / f"{src_path.stem}{ext}"
                    if candidate_img.exists() and candidate_img.is_file():
                        dest_img = CHARACTERS_DIR / f"{dest_path.stem}_avatar{ext}"
                        if dest_img.resolve() != candidate_img.resolve():
                            shutil.copy2(candidate_img, dest_img)
                        break

                card = TavernParser.parse_file(dest_path)

                # Санитизация описания и характера при импорте JSON
                raw = dict(card.raw_data) if card.raw_data else {}
                if not raw:
                    raw = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {}}
                is_v2 = raw.get("spec") == "chara_card_v2" or "data" in raw
                target = raw.setdefault("data", {}) if is_v2 else raw

                clean_desc, extra_notes = sanitize_card_description(card.description or "")
                clean_pers, pers_notes = sanitize_card_description(card.personality or "")

                target["name"] = card.name
                target["description"] = clean_desc
                target["personality"] = clean_pers
                target["scenario"] = card.scenario
                target["first_mes"] = card.first_mes
                target["mes_example"] = card.mes_example

                all_extra = extra_notes + pers_notes
                if all_extra:
                    existing_notes = target.get("creator_notes", "") or getattr(card, "creator_notes", "") or ""
                    merged = [existing_notes] if existing_notes else []
                    merged.extend(all_extra)
                    target["creator_notes"] = "\n\n".join(dict.fromkeys([n for n in merged if n]))

                card.description = clean_desc
                card.personality = clean_pers
                if hasattr(card, "creator_notes") and target.get("creator_notes"):
                    card.creator_notes = target["creator_notes"]

                with open(dest_path, "w", encoding="utf-8") as f:
                    json.dump(raw, f, ensure_ascii=False, indent=2)

                card.source_path = dest_path
                select_target = dest_path
                char_name = card.name

            # Авто-извлечение и сохранение Visual DNA или состава отряда
            self._pending_visual_setup_path = str(select_target.resolve())
            self._load_characters(select_path=select_target)
            QMessageBox.information(self, "Импорт завершен", f"Персонаж '{char_name}' успешно импортирован.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка импорта", f"Не удалось импортировать файл: {e}")

    def _show_list_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item or not self.active_entry:
            return

        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #121826;
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
                background-color: #2D1120;
                color: #FF007F;
            }
        """)
        del_action = menu.addAction("🗑️ Удалить карточку")
        del_action.triggered.connect(self._delete_character)
        menu.exec(self.list_widget.mapToGlobal(pos))

    def _delete_character(self):
        """Удаляет файл карточки и связанный аватар персонажа с диска после подтверждения."""
        if not self.active_entry:
            QMessageBox.warning(self, "Внимание", "Выберите карточку персонажа для удаления.")
            return

        card: CharacterCard = self.active_entry["card"]
        char_name = card.name or "Персонаж"
        json_path: Path = Path(self.active_entry["path"])

        confirm = QMessageBox.question(
            self,
            "Подтверждение удаления",
            f"Вы уверены, что хотите безвозвратно удалить персонажа «{char_name}»?\n\nФайлы карточки будут стёрты с диска.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            # 1. Удаление связанного файла аватара и его кэшированных миниатюр
            if card.avatar_path:
                av_path = Path(card.avatar_path)
                try:
                    if av_path.exists() and av_path.is_file() and av_path.is_relative_to(CHARACTERS_DIR):
                        av_path.unlink(missing_ok=True)
                    
                    # Очистка кэша круглых миниатюр для этого аватара
                    for cache_file in GALLERY_THUMBS_DIR.glob(f"{av_path.stem}_*.png"):
                        cache_file.unlink(missing_ok=True)
                except Exception as e_av:
                    logger.debug(f"Не удалось удалить аватар или его кэш: {e_av}")

            # 2. Удаление основного JSON файла и всех возможных изображений (включая _avatar)
            stem_name = json_path.stem
            for ext in (".json", ".png", ".jpg", ".jpeg", ".webp"):
                for suffix in ("", "_avatar"):
                    candidate = CHARACTERS_DIR / f"{stem_name}{suffix}{ext}"
                    if candidate.exists() and candidate.is_file():
                        try:
                            candidate.unlink(missing_ok=True)
                        except Exception as e_f:
                            logger.warning(f"Ошибка удаления файла {candidate}: {e_f}")

            # 2.5 Очистка остаточных записей из реестра LoRA
            try:
                from services.art_director.lora_manager import LoraManager, LORA_REGISTRY_PATH
                reg = LoraManager.load_registry()
                chars = reg.get("characters", {})
                c_key = char_name.lower().replace(" ", "_")
                if c_key in chars:
                    del chars[c_key]
                    from utils.file_utils import atomic_write_json
                    atomic_write_json(LORA_REGISTRY_PATH, reg)
            except Exception as e_reg:
                logger.debug(f"Ошибка очистки LoRA реестра: {e_reg}")

            # 3. Перезагрузка списка и очистка редактора
            self._load_characters()
            QMessageBox.information(self, "Успешно", f"Персонаж «{char_name}» удалён.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка удаления", f"Не удалось удалить карточку: {e}")

    def _show_prev_greeting(self):
        if not self.active_entry or self.current_greeting_idx <= 0:
            return
        self._save_current_greeting_text()
        self.current_greeting_idx -= 1
        self._update_greeting_display()

    def _show_next_greeting(self):
        if not self.active_entry:
            return
        card: CharacterCard = self.active_entry["card"]
        pool = card.get_greetings_pool()
        total = max(1, len(pool))
        if self.current_greeting_idx >= total - 1:
            return
        self._save_current_greeting_text()
        self.current_greeting_idx += 1
        self._update_greeting_display()

    def _save_current_greeting_text(self):
        if not self.active_entry:
            return
        card: CharacterCard = self.active_entry["card"]
        text = self.input_first_mes.toPlainText().strip()
        has_cyr = bool(text and re.search(r'[а-яА-ЯёЁ]{3,}', text))
        showing_ru = getattr(self, "_showing_ru", False)
        if showing_ru:
            if has_cyr:
                card.set_greeting_variant(self.current_greeting_idx, text, is_ru=True)
            # Если в режиме RU нет кириллицы, не затираем существующий русский перевод
        else:
            card.set_greeting_variant(self.current_greeting_idx, text, is_ru=False)

    def _update_greeting_display(self):
        if not self.active_entry:
            self.lbl_greeting_page.setText("1 / 1")
            self.btn_prev_greeting.setEnabled(False)
            self.btn_next_greeting.setEnabled(False)
            return

        card: CharacterCard = self.active_entry["card"]
        pool = card.get_greetings_pool()
        total = max(1, len(pool))

        if self.current_greeting_idx >= total:
            self.current_greeting_idx = max(0, total - 1)
        if self.current_greeting_idx < 0:
            self.current_greeting_idx = 0

        self.lbl_greeting_page.setText(f"{self.current_greeting_idx + 1} / {total}")
        self.btn_prev_greeting.setEnabled(self.current_greeting_idx > 0)
        self.btn_next_greeting.setEnabled(self.current_greeting_idx < total - 1)

        showing_ru = getattr(self, "_showing_ru", True)
        if pool and self.current_greeting_idx < len(pool):
            if showing_ru:
                text = pool[self.current_greeting_idx]["effective"]
            else:
                text = pool[self.current_greeting_idx]["orig"] or pool[self.current_greeting_idx]["effective"]
        else:
            if showing_ru:
                text = card.first_mes_ru or card.first_mes or ""
            else:
                text = card.first_mes or ""
        self.input_first_mes.setPlainText(text)

    def _change_avatar(self):
        """Allows picking a new avatar image for the selected character and interactive cropping."""
        if not self.active_entry:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите изображение аватара", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if not file_path:
            return

        card: CharacterCard = self.active_entry["card"]
        src = Path(file_path)
        safe_name = "".join(c for c in card.name if c.isalnum() or c in (' ', '_', '-')).strip() or card.id
        dest = CHARACTERS_DIR / f"{safe_name}_avatar.png"

        try:
            crop_dlg = AvatarCropDialog(source_image_path=src, destination_path=dest, parent=self)
            if crop_dlg.exec() != QDialog.DialogCode.Accepted:
                return

            card.avatar_path = dest

            # Update avatar preview
            pix = render_seamless_avatar(card.avatar_path, name=card.name, size=64)
            self.avatar_preview_lbl.setPixmap(pix)

            # Update list widget card
            curr = self.list_widget.currentItem()
            if curr:
                list_pix = get_cached_circular_avatar(card.avatar_path, name=card.name, size=44)
                w = self.list_widget.itemWidget(curr)
                if isinstance(w, CharacterCardWidget):
                    w.update_info(card.name, avatar_pixmap=list_pix)

            # Automatically persist avatar link in card JSON
            self._save_changes(silent=True)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось обновить аватар: {e}")

    def _save_changes(self, silent: bool = False):
        """Overwrites JSON file with updated fields from the editor."""
        if not self.active_entry:
            return

        card: CharacterCard = self.active_entry["card"]
        json_path: Path = Path(self.active_entry["path"])
        if json_path.suffix.lower() != ".json":
            json_path = CHARACTERS_DIR / f"{json_path.stem}.json"
            self.active_entry["path"] = json_path

        name = self.input_name.text().strip() or "Персонаж"
        desc = self.input_desc.toPlainText().strip()
        personality = self.input_personality.toPlainText().strip()
        scenario = self.input_scenario.toPlainText().strip()
        system_prompt = self.input_system_prompt.toPlainText().strip()
        mes_example = self.input_mes_example.toPlainText().strip()

        # Update greeting for current page slot
        self._save_current_greeting_text()

        # Update card object without polluting _ru fields with English text
        def _has_cyrillic(s: str) -> bool:
            return bool(s and re.search(r'[а-яА-ЯёЁ]{3,}', s))

        card.name = name
        if getattr(self, "_showing_ru", False):
            # В режиме RU обновляем ТОЛЬКО русские поля карточки при наличии кириллицы.
            # Английские оригиналы (card.description и т.д.) НИ ПРИ КАКИХ УСЛОВИЯХ НЕ ЗАТИРАЮТСЯ!
            if _has_cyrillic(desc):
                card.description_ru = desc
            if _has_cyrillic(personality):
                card.personality_ru = personality
            if _has_cyrillic(scenario):
                card.scenario_ru = scenario
            if _has_cyrillic(mes_example):
                card.mes_example_ru = mes_example
        else:
            # В режиме оригинала обновляем английские поля, только если поле не пустое
            if desc.strip():
                card.description = desc
            if personality.strip():
                card.personality = personality
            if scenario.strip():
                card.scenario = scenario
            if mes_example.strip():
                card.mes_example = mes_example

        if system_prompt.strip():
            card.system_prompt = system_prompt

        # Update raw data dictionary
        raw = card.raw_data if isinstance(card.raw_data, dict) else {}
        if not raw:
            raw = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {}}

        is_v2 = raw.get("spec") == "chara_card_v2" or "data" in raw
        target = raw.setdefault("data", {}) if is_v2 else raw

        card.visual_dna = self.input_visual_dna.toPlainText().strip()
        target["visual_dna"] = card.visual_dna
        if "visual_dna" in raw and raw is not target:
            raw["visual_dna"] = card.visual_dna

        # Сохранение изменений активного сопартийца
        if 0 <= self.active_companion_idx < len(getattr(card, "companions", [])):
            card.companions[self.active_companion_idx]["visual_dna"] = card.visual_dna
            TavernParser.save_card_companions(card, json_path)
        elif getattr(card, "companions", []):
            TavernParser.save_card_companions(card, json_path)

        if getattr(card, "companions", None):
            if "extensions" not in target or not isinstance(target["extensions"], dict):
                target["extensions"] = {}
            if "neonroute" not in target["extensions"] or not isinstance(target["extensions"]["neonroute"], dict):
                target["extensions"]["neonroute"] = {}
            target["extensions"]["neonroute"]["companions"] = card.companions

        target["name"] = name
        target["description"] = card.description
        target["personality"] = card.personality
        target["scenario"] = card.scenario
        target["system_prompt"] = card.system_prompt
        if "system_prompt" in raw and raw is not target:
            raw["system_prompt"] = card.system_prompt
        target["mes_example"] = card.mes_example
        target["first_mes"] = card.first_mes
        target["alternate_greetings"] = getattr(card, "alternate_greetings", [])

        for key in ("description", "personality", "scenario", "mes_example", "first_mes"):
            attr_ru = f"{key}_ru"
            val = getattr(card, attr_ru, None)
            if val:
                target[attr_ru] = val
            elif target.get(attr_ru) and not val:
                setattr(card, attr_ru, target[attr_ru])
            else:
                target[attr_ru] = val or ""

        alts_ru = getattr(card, "alternate_greetings_ru", None)
        if alts_ru:
            target["alternate_greetings_ru"] = alts_ru
        elif target.get("alternate_greetings_ru") and not alts_ru:
            card.alternate_greetings_ru = target["alternate_greetings_ru"]
        else:
            target["alternate_greetings_ru"] = target.get("alternate_greetings_ru", []) or []

        if card.avatar_path:
            target["avatar_path"] = str(card.avatar_path)

        if "name" in raw and raw is not target:
            raw["name"] = name

        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)

            TavernParser.save_card_translations(card)

            card.source_path = json_path

            curr = self.list_widget.currentItem()
            if curr:
                curr.setText(name)
                pix = get_cached_circular_avatar(card.avatar_path, name=name, size=44)
                w = self.list_widget.itemWidget(curr)
                if isinstance(w, CharacterCardWidget):
                    w.update_info(name, avatar_pixmap=pix)

            self.card_saved.emit(card)

            # Синхронизация обновленного Visual DNA с кэшем текущей активной сессии
            try:
                parent_win = self.parent()
                if parent_win and getattr(parent_win, "current_session", None):
                    curr_sess = parent_win.current_session
                    cd = curr_sess.get("character_data")
                    if cd and isinstance(cd, dict):
                        card_id = getattr(card, "id", None)
                        if (card_id and cd.get("id") == card_id) or (cd.get("name") == card.name):
                            cd["visual_dna"] = card.visual_dna
                            sess_id = curr_sess.get("id")
                            if sess_id:
                                from services.rp_cast_manager import RPCastManager
                                cast_data = RPCastManager.load_cast(sess_id)
                                cast_data.setdefault("companion_state", {})["visual_dna"] = card.visual_dna
                                RPCastManager.save_cast(sess_id, cast_data)
                                logger.info(f"[Gallery] Visual DNA с LoRA синхронизирована с сессией {sess_id}")
            except Exception as e_sync:
                logger.debug(f"[Gallery] Не удалось синхронизировать visual_dna с сессией: {e_sync}")

            if not silent:
                QMessageBox.information(self, "Сохранено", f"Карточка '{name}' успешно сохранена!")
        except Exception as e:
            if not silent:
                QMessageBox.critical(self, "Ошибка сохранения", f"Не удалось сохранить карточку: {e}")
            else:
                logger.error(f"Error saving character card {json_path}: {e}")

    def _start_dialog(self):
        """Starts a roleplay session with the active character card."""
        if not self.active_entry:
            QMessageBox.warning(self, "Внимание", "Пожалуйста, выберите персонажа из списка.")
            return

        card: CharacterCard = self.active_entry["card"]
        if getattr(card, "is_lorebook", False):
            QMessageBox.information(
                self,
                "Модуль правил (Лорбук)",
                "Этот файл является энциклопедией правил (World Info / Lorebook).\n\n"
                "С ним нельзя начать прямой диалог как с персонажем. "
                "Вы можете подключить его к любому активному чату через окно «📜 Правила мира и Лор»."
            )
            return

        self._save_current_greeting_text()
        self._save_changes(silent=True)

        # Only lock greeting if user explicitly checked the box; otherwise trigger random choice
        if self.chk_use_as_start.isChecked():
            card.selected_greeting_idx = self.current_greeting_idx
        else:
            card.selected_greeting_idx = None

        self.selected_card = card
        self.accept()
        self.start_session_requested.emit(card)
        self.character_selected.emit(card)
