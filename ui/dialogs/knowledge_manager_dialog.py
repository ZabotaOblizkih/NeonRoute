"""
Knowledge Manager Dialog for Elaria's Knowledge Base (NeonRoute).
Strictly built using PyQt-Fluent-Widgets (qfluentwidgets) components.
Provides document viewing, trigger chips preview, folder opening, import, and sync.
"""

import os
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QFrame, QLabel,
    QStackedWidget, QSplitter, QListWidgetItem
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    TitleLabel,
    CaptionLabel,
    StrongBodyLabel,
    PrimaryPushButton,
    PushButton,
    TransparentToolButton,
    FluentIcon as FIF,
    SingleDirectionScrollArea,
    SimpleCardWidget,
    PillPushButton,
    FlowLayout,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    IconWidget,
    SegmentedWidget,
    LineEdit,
    TextEdit,
    ListWidget,
    setTheme,
    Theme
)

from services.knowledge_manager import KnowledgeManager
from ui.common.icon_utils import SafeFIF
from ui.common.toast_helper import toast_success, toast_info, toast_warning, toast_error

logger = logging.getLogger("NeonRoute.KnowledgeManagerDialog")


class DocumentCardWidget(SimpleCardWidget):
    """
    Карточка документа базы знаний на базе SimpleCardWidget qfluentwidgets.
    Отображает иконку типа, имя файла, метаданные, чипы извлеченных триггеров и кнопку удаления.
    """

    def __init__(self, doc: Dict[str, Any], on_delete: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.doc = doc
        self.filename = doc.get("filename", "")
        self.on_delete = on_delete

        self.setObjectName("DocumentCardWidget")
        self.setStyleSheet("""
            SimpleCardWidget#DocumentCardWidget {
                background-color: rgba(18, 24, 38, 0.85);
                border: 1px solid rgba(0, 240, 255, 0.18);
                border-radius: 10px;
                padding: 10px;
            }
            SimpleCardWidget#DocumentCardWidget:hover {
                border-color: rgba(0, 240, 255, 0.40);
                background-color: rgba(22, 30, 48, 0.95);
            }
        """)

        self._setup_ui()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 10, 12, 10)
        root_layout.setSpacing(8)

        # 1. Верхняя строка: Иконка + Имя и метаданные + Кнопка удаления
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)

        # Определение иконки файла
        ext = Path(self.filename).suffix.lower()
        ICON_MAP = {
            ".pdf": getattr(FIF, "DOCUMENT", FIF.DOCUMENT),
            ".txt": getattr(FIF, "DOCUMENT", FIF.DOCUMENT),
            ".md": getattr(FIF, "DOCUMENT", FIF.DOCUMENT),
            ".json": getattr(FIF, "CODE", FIF.DOCUMENT),
        }
        icon = ICON_MAP.get(ext.lower(), FIF.DOCUMENT)
        icon_widget = IconWidget(icon, self)
        icon_widget.setFixedSize(24, 24)
        header_layout.addWidget(icon_widget, 0, Qt.AlignmentFlag.AlignVCenter)

        # Текстовые метаданные
        title_meta_layout = QVBoxLayout()
        title_meta_layout.setSpacing(2)

        name_lbl = StrongBodyLabel(self.filename, self)
        name_lbl.setStyleSheet("color: #F8FAFC; font-weight: 600; font-size: 13px;")
        title_meta_layout.addWidget(name_lbl)

        size_kb = self.doc.get("size_kb", 0.0)
        lines = self.doc.get("line_count", 0)
        pages = self.doc.get("page_count")
        updated = self.doc.get("updated_at", "")
        date_str = updated[:10] if updated else ""

        if pages is not None:
            count_str = f"{pages} стр."
        else:
            count_str = f"{lines} строк"

        meta_text = f"{size_kb:.1f} КБ • {count_str}"
        if date_str:
            meta_text += f" • {date_str}"

        meta_lbl = CaptionLabel(meta_text, self)
        meta_lbl.setStyleSheet("color: #94A3B8; font-size: 11px;")
        title_meta_layout.addWidget(meta_lbl)

        header_layout.addLayout(title_meta_layout, 1)

        # Кнопка удаления
        btn_delete = TransparentToolButton(FIF.DELETE, self)
        btn_delete.setToolTip("Удалить документ")
        btn_delete.clicked.connect(lambda: self.on_delete(self.filename))
        header_layout.addWidget(btn_delete, 0, Qt.AlignmentFlag.AlignVCenter)

        root_layout.addLayout(header_layout)

        # 2. Нижняя строка: Контейнер триггеров
        triggers: List[str] = self.doc.get("triggers", [])
        if triggers:
            triggers_frame = QWidget(self)
            triggers_layout = FlowLayout(triggers_frame, needAni=False)
            triggers_layout.setContentsMargins(0, 2, 0, 0)
            triggers_layout.setSpacing(6)

            for trig in triggers:
                chip = PillPushButton(trig, triggers_frame)
                chip.setEnabled(False)  # Чип только для отображения
                chip.setFixedHeight(22)
                chip.setStyleSheet("""
                    PillPushButton {
                        background-color: rgba(0, 240, 255, 0.08);
                        color: #00F0FF;
                        border: 1px solid rgba(0, 240, 255, 0.28);
                        border-radius: 11px;
                        padding: 1px 10px;
                        font-size: 11px;
                        font-weight: 500;
                    }
                    PillPushButton:disabled {
                        background-color: rgba(0, 240, 255, 0.08);
                        color: #00F0FF;
                        border: 1px solid rgba(0, 240, 255, 0.28);
                    }
                """)
                triggers_layout.addWidget(chip)

            root_layout.addWidget(triggers_frame)
        else:
            no_trig = CaptionLabel("Триггеры не извлечены (документ пуст или содержит только код/разметку)", self)
            no_trig.setStyleSheet("color: #64748B; font-style: italic; font-size: 11px;")
            root_layout.addWidget(no_trig)


class SkillListItemWidget(QWidget):
    """
    Кастомный виджет элемента списка скиллов:
    Иконка/эмодзи, название и бейдж [Базовый] или [Пользовательский].
    """

    def __init__(self, title: str, is_builtin: bool, icon_str: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        self.icon_lbl = QLabel(self)
        self.icon_lbl.setFixedSize(32, 32)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_lbl.setStyleSheet(
            "border-radius: 6px; background-color: rgba(255, 255, 255, 0.04); "
            "border: 1px solid rgba(255, 255, 255, 0.08); font-size: 16px;"
        )
        self.icon_lbl.setText(icon_str or ("⚡" if is_builtin else "✨"))
        layout.addWidget(self.icon_lbl)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)

        self.title_lbl = QLabel(title, self)
        self.title_lbl.setStyleSheet("color: #F8FAFC; font-weight: 600; font-size: 12px;")
        text_layout.addWidget(self.title_lbl)

        badge_type = "Базовый" if is_builtin else "Пользовательский"
        badge_style = (
            "color: #94A3B8; background-color: rgba(148, 163, 184, 0.12); "
            "border: 1px solid rgba(148, 163, 184, 0.25); border-radius: 4px; "
            "padding: 1px 6px; font-size: 10px; font-weight: 600;"
            if is_builtin else
            "color: #00F0FF; background-color: rgba(0, 240, 255, 0.12); "
            "border: 1px solid rgba(0, 240, 255, 0.35); border-radius: 4px; "
            "padding: 1px 6px; font-size: 10px; font-weight: 600;"
        )
        self.badge_lbl = QLabel(badge_type, self)
        self.badge_lbl.setStyleSheet(badge_style)
        self.badge_lbl.setFixedHeight(18)

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.addWidget(self.badge_lbl)
        badge_row.addStretch()
        text_layout.addLayout(badge_row)

        layout.addLayout(text_layout, stretch=1)


class KnowledgeManagerDialog(FramelessDialog):
    """
    Модальное окно управления базой знаний и скиллами Эларии.
    Построено исключительно на компонентах PyQt-Fluent-Widgets (qfluentwidgets).
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setWindowTitle("Знания Эларии")
        self.resize(860, 600)
        self.setMinimumSize(700, 480)
        self._current_selected_skill_id: Optional[str] = None

        # Стилизация темного Fluent-окна
        self.setStyleSheet("""
            KnowledgeManagerDialog {
                background-color: #0E131F;
                border: 1px solid rgba(0, 240, 255, 0.22);
                border-radius: 12px;
            }
        """)

        # Fluent TitleBar
        self.titleBar = FluentTitleBar(self)
        self.titleBar.setTitle("")
        self.setTitleBar(self.titleBar)

        self._setup_ui()
        self.reload_documents()
        self._load_skills_list()
        self._load_system_prompt()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, 40, 24, 20)
        root_layout.setSpacing(14)

        # 1. Заголовок и счетчик + кнопка закрытия [ ✕ ]
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(4)

        self.title_label = TitleLabel("Знания Эларии", self)
        self.title_label.setStyleSheet("color: #00F0FF; font-size: 20px; font-weight: 700;")
        header_text_layout.addWidget(self.title_label)

        self.caption_label = CaptionLabel("0 документов • 0 триггеров", self)
        self.caption_label.setStyleSheet("color: #94A3B8; font-size: 12px;")
        header_text_layout.addWidget(self.caption_label)

        header_layout.addLayout(header_text_layout, 1)

        # Кнопка закрытия [ ✕ ]
        self.close_btn = TransparentToolButton(FIF.CLOSE, self)
        self.close_btn.setToolTip("Закрыть")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.reject)
        self.btn_close = self.close_btn  # Дополнительный алиас
        header_layout.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        root_layout.addLayout(header_layout)

        # 2. Переключатель вкладок SegmentedWidget
        self.tab_bar = SegmentedWidget(self)
        self.tab_bar.addItem("tab_docs", "📚 База знаний")
        self.tab_bar.addItem("tab_skills", "⚡ Скиллы Эларии")
        self.tab_bar.addItem("tab_prompt", "⚙️ Системный промпт")
        root_layout.addWidget(self.tab_bar)

        self.stack = QStackedWidget(self)
        root_layout.addWidget(self.stack, 1)

        # ── Вкладка 1: «📚 База знаний» ──
        self.docs_page = QWidget(self)
        docs_layout = QVBoxLayout(self.docs_page)
        docs_layout.setContentsMargins(0, 6, 0, 0)
        docs_layout.setSpacing(10)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(10)

        self.btn_add = PrimaryPushButton(FIF.ADD, "Добавить документ", self.docs_page)
        self.btn_add.setToolTip("Импортировать файл в базу знаний")
        self.btn_add.clicked.connect(self._on_add_document_clicked)
        toolbar_layout.addWidget(self.btn_add)

        self.btn_open_folder = PushButton(FIF.FOLDER, "Открыть папку", self.docs_page)
        self.btn_open_folder.setToolTip("Открыть проводник с папкой knowledge_base/")
        self.btn_open_folder.clicked.connect(self._on_open_folder_clicked)
        toolbar_layout.addWidget(self.btn_open_folder)

        self.btn_sync = TransparentToolButton(FIF.SYNC, self.docs_page)
        self.btn_sync.setToolTip("Синхронизировать")
        self.btn_sync.clicked.connect(self._on_sync_clicked)
        toolbar_layout.addWidget(self.btn_sync)

        toolbar_layout.addStretch(1)
        docs_layout.addLayout(toolbar_layout)

        self.scroll_area = SingleDirectionScrollArea(self.docs_page)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
                background-color: rgba(14, 19, 31, 0.6);
            }
        """)

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet("background: transparent;")
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(10, 10, 10, 10)
        self.cards_layout.setSpacing(10)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll_area.setWidget(self.cards_container)
        docs_layout.addWidget(self.scroll_area, 1)

        self.stack.addWidget(self.docs_page)

        # ── Вкладка 2: «⚡ Скиллы Эларии» ──
        self.skills_page = QWidget(self)
        self._setup_skills_ui()
        self.stack.addWidget(self.skills_page)

        # ── Вкладка 3: «⚙️ Системный промпт» ──
        self.prompt_page = QWidget(self)
        self._setup_prompt_ui()
        self.stack.addWidget(self.prompt_page)

        def _on_tab_changed(key: str):
            idx_map = {"tab_docs": 0, "tab_skills": 1, "tab_prompt": 2}
            idx = idx_map.get(key, 0)
            self.stack.setCurrentIndex(idx)
            if idx == 0:
                self.reload_documents()
            elif idx == 1:
                self._load_skills_list()
            elif idx == 2:
                self._load_system_prompt()

        self.tab_bar.currentItemChanged.connect(_on_tab_changed)
        self.tab_bar.setCurrentItem("tab_docs")

    def _setup_skills_ui(self):
        skills_layout = QHBoxLayout(self.skills_page)
        skills_layout.setContentsMargins(0, 6, 0, 0)
        skills_layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal, self.skills_page)
        splitter.setChildrenCollapsible(False)

        # Левая панель: Список скиллов + кнопка Добавить
        left_widget = QWidget(splitter)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(8)

        lbl_list = StrongBodyLabel("Микро-скиллы Ассистента:", left_widget)
        lbl_list.setStyleSheet("color: #94A3B8; font-size: 12px; font-weight: 600;")
        left_layout.addWidget(lbl_list)

        self.list_skills = ListWidget(left_widget)
        self.list_skills.setStyleSheet("""
            ListWidget {
                background-color: rgba(15, 23, 42, 0.70);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 4px;
            }
            ListWidget::item {
                border-radius: 6px;
                margin-bottom: 2px;
            }
            ListWidget::item:selected {
                background-color: rgba(0, 240, 255, 0.14);
            }
            ListWidget::item:hover:!selected {
                background-color: rgba(255, 255, 255, 0.04);
            }
        """)
        self.list_skills.currentRowChanged.connect(self._on_skill_row_changed)
        left_layout.addWidget(self.list_skills, 1)

        self.btn_add_skill = PushButton(FIF.ADD, "Добавить свой скилл", left_widget)
        self.btn_add_skill.clicked.connect(self._on_add_skill_clicked)
        left_layout.addWidget(self.btn_add_skill)

        splitter.addWidget(left_widget)

        # Правая панель: Форма просмотра / редактирования скилла
        right_scroll = SingleDirectionScrollArea(splitter)
        right_scroll.setWidgetResizable(True)
        right_scroll.setStyleSheet("""
            QScrollArea {
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
                background-color: rgba(14, 19, 31, 0.6);
            }
        """)

        right_container = QWidget()
        right_container.setStyleSheet("background: transparent;")
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(12, 10, 12, 10)
        right_layout.setSpacing(8)

        # ID скилла
        id_box = QVBoxLayout()
        id_box.setSpacing(3)
        lbl_id = CaptionLabel("ID скилла (системный идентификатор):", right_container)
        lbl_id.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.line_skill_id = LineEdit(right_container)
        self.line_skill_id.setPlaceholderText("например: sql_expert")
        id_box.addWidget(lbl_id)
        id_box.addWidget(self.line_skill_id)
        right_layout.addLayout(id_box)

        # Название скилла
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        lbl_title = CaptionLabel("Название скилла:", right_container)
        lbl_title.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.line_skill_title = LineEdit(right_container)
        self.line_skill_title.setPlaceholderText("например: 💡 Эксперт по базам данных")
        title_box.addWidget(lbl_title)
        title_box.addWidget(self.line_skill_title)
        right_layout.addLayout(title_box)

        # Описание
        desc_box = QVBoxLayout()
        desc_box.setSpacing(3)
        lbl_desc = CaptionLabel("Человеческое описание (фокус и специализация):", right_container)
        lbl_desc.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.text_human_desc = TextEdit(right_container)
        self.text_human_desc.setPlaceholderText("Краткое и емкое описание назначения скилла...")
        self.text_human_desc.setFixedHeight(60)
        desc_box.addWidget(lbl_desc)
        desc_box.addWidget(self.text_human_desc)
        right_layout.addLayout(desc_box)

        # Триггеры
        trig_box = QVBoxLayout()
        trig_box.setSpacing(3)
        lbl_trig = CaptionLabel("Ключевые фразы / триггеры (через запятую):", right_container)
        lbl_trig.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.text_triggers = TextEdit(right_container)
        self.text_triggers.setPlaceholderText("sql, postgres, индексы, оптимизация запросов, explain")
        self.text_triggers.setFixedHeight(60)
        trig_box.addWidget(lbl_trig)
        trig_box.addWidget(self.text_triggers)
        right_layout.addLayout(trig_box)

        # Системный промпт
        sys_box = QVBoxLayout()
        sys_box.setSpacing(3)
        self.lbl_system_prompt = CaptionLabel("Системный промпт / инструкция скилла:", right_container)
        self.lbl_system_prompt.setStyleSheet("color: #94A3B8; font-size: 11px;")
        self.text_system_prompt = TextEdit(right_container)
        self.text_system_prompt.setPlaceholderText("Инструкция для модели при активации этого скилла...")
        self.text_system_prompt.setMinimumHeight(210)
        self.text_system_prompt.setFont(QFont("Consolas", 10))
        sys_box.addWidget(self.lbl_system_prompt)
        sys_box.addWidget(self.text_system_prompt, 1)
        right_layout.addLayout(sys_box, 1)

        # Кнопки Сохранить и Удалить
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)
        self.btn_save_skill = PrimaryPushButton(FIF.SAVE, "Сохранить", right_container)
        self.btn_save_skill.clicked.connect(self._on_save_skill_clicked)
        btn_box.addWidget(self.btn_save_skill)

        self.btn_delete_skill = PushButton(FIF.DELETE, "Удалить", right_container)
        self.btn_delete_skill.clicked.connect(self._on_delete_skill_clicked)
        btn_box.addWidget(self.btn_delete_skill)

        btn_box.addStretch(1)
        right_layout.addLayout(btn_box)

        right_scroll.setWidget(right_container)
        splitter.addWidget(right_scroll)

        splitter.setStretchFactor(0, 36)
        splitter.setStretchFactor(1, 64)

        skills_layout.addWidget(splitter)

    def _setup_prompt_ui(self):
        """Построение интерфейса 3-й вкладки: редактор системного промпта Эларии."""
        prompt_layout = QVBoxLayout(self.prompt_page)
        prompt_layout.setContentsMargins(0, 6, 0, 0)
        prompt_layout.setSpacing(10)

        # 1. Заголовок и пояснение
        header_vbox = QVBoxLayout()
        header_vbox.setSpacing(4)

        lbl_prompt_title = StrongBodyLabel("Базовый системный промпт Эларии", self.prompt_page)
        lbl_prompt_title.setStyleSheet("color: #F8FAFC; font-weight: 600; font-size: 14px;")
        header_vbox.addWidget(lbl_prompt_title)

        lbl_prompt_desc = CaptionLabel(
            "Настройка базовой личности и инструкций. Изменения сохраняются в конфигурацию и применяются только к новым сессиям, не затрагивая историю существующих чатов.",
            self.prompt_page
        )
        lbl_prompt_desc.setStyleSheet("color: #94A3B8; font-size: 12px;")
        lbl_prompt_desc.setWordWrap(True)
        header_vbox.addWidget(lbl_prompt_desc)

        prompt_layout.addLayout(header_vbox)

        # 2. Редактор текста
        self.prompt_editor = TextEdit(self.prompt_page)
        self.prompt_editor.setMinimumHeight(320)
        self.prompt_editor.setPlaceholderText("Введите системный промпт персоны...")
        self.prompt_editor.setFont(QFont("Consolas", 10))
        self.prompt_editor.setStyleSheet("""
            TextEdit {
                background-color: rgba(14, 19, 31, 0.75);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                color: #F8FAFC;
                font-family: 'Consolas', 'Cascadia Code', 'Segoe UI', monospace;
                font-size: 13px;
                padding: 10px;
            }
            TextEdit:focus {
                border: 1px solid rgba(0, 240, 255, 0.50);
                background-color: rgba(18, 24, 38, 0.90);
            }
        """)
        prompt_layout.addWidget(self.prompt_editor, 1)

        # 3. Нижняя панель кнопок
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_reset_prompt = PushButton(FIF.SYNC, "Сбросить к заводскому", self.prompt_page)
        self.btn_reset_prompt.clicked.connect(self._on_reset_prompt_clicked)
        btn_layout.addWidget(self.btn_reset_prompt)

        btn_layout.addStretch(1)

        self.btn_save_prompt = PrimaryPushButton(FIF.SAVE, "Сохранить для новых сессий", self.prompt_page)
        self.btn_save_prompt.clicked.connect(self._on_save_prompt_clicked)
        btn_layout.addWidget(self.btn_save_prompt)

        prompt_layout.addLayout(btn_layout)

    def _load_system_prompt(self):
        """Загрузка системного промпта из конфига или заводского шаблона."""
        from utils.config_manager import ConfigManager
        from services.assistant_prompt_builder import AssistantPromptBuilder
        cfg = ConfigManager.load_config()
        custom_tmpl = cfg.get("assistant_custom_prompt_template", "")
        if custom_tmpl and custom_tmpl.strip():
            self.prompt_editor.setPlainText(custom_tmpl.strip())
        else:
            self.prompt_editor.setPlainText(AssistantPromptBuilder.SYSTEM_PERSONA_TEMPLATE.strip())

    def _on_save_prompt_clicked(self):
        """Сохранение отредактированного системного промпта для новых сессий."""
        from utils.config_manager import ConfigManager
        new_text = self.prompt_editor.toPlainText().strip()
        cfg = ConfigManager.load_config()
        cfg["assistant_custom_prompt_template"] = new_text
        ConfigManager.save_config(cfg)

        # Обновляем память окна, если доступно
        parent_win = self.parent()
        if parent_win and hasattr(parent_win, "app_config") and isinstance(parent_win.app_config, dict):
            parent_win.app_config["assistant_custom_prompt_template"] = new_text

        toast_success(
            self,
            title="Сохранено",
            content="Системный промпт обновлен. Он будет применяться ко всем вновь создаваемым диалогам.",
        )

    def _on_reset_prompt_clicked(self):
        """Сброс системного промпта в редакторе к заводскому шаблону."""
        from services.assistant_prompt_builder import AssistantPromptBuilder
        self.prompt_editor.setPlainText(AssistantPromptBuilder.SYSTEM_PERSONA_TEMPLATE.strip())
        toast_info(
            self,
            title="Восстановлен шаблон",
            content="Отображен заводской промпт. Нажмите «Сохранить для новых сессий», чтобы применить.",
        )

    def _get_router(self):
        """Получает активный экземпляр роутера скиллов Ассистента."""
        try:
            from services.assistant_prompt_builder import AssistantPromptBuilder
            builder = AssistantPromptBuilder.get_instance()
            if hasattr(builder, "router") and builder.router:
                return builder.router
        except Exception:
            pass
        from services.skill_router import SkillRouter
        return SkillRouter.get_instance()

    def _load_skills_list(self, select_skill_id: Optional[str] = None):
        """Наполняет список скиллами из SkillRouter.get_all_skills()."""
        self.list_skills.clear()
        router = self._get_router()
        all_skills = router.get_all_skills() if hasattr(router, "get_all_skills") else {}

        target_row = -1
        row_idx = 0
        for sid, sdata in all_skills.items():
            title = sdata.get("title", sid)
            is_builtin = sdata.get("is_builtin", False)
            first_token = title.split()[0] if title and title.split() else ""
            icon_char = first_token if len(first_token) <= 2 else ("⚡" if is_builtin else "✨")

            item = QListWidgetItem(self.list_skills)
            item.setData(Qt.ItemDataRole.UserRole, sid)
            item_widget = SkillListItemWidget(title, is_builtin, icon_char)
            item.setSizeHint(item_widget.sizeHint())
            self.list_skills.addItem(item)
            self.list_skills.setItemWidget(item, item_widget)

            if select_skill_id and sid == select_skill_id:
                target_row = row_idx
            row_idx += 1

        if target_row >= 0:
            self.list_skills.setCurrentRow(target_row)
        elif self.list_skills.count() > 0:
            self.list_skills.setCurrentRow(0)

    def _on_skill_row_changed(self, row: int):
        if row < 0:
            return
        item = self.list_skills.item(row)
        if not item:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        self._on_skill_selected(sid)

    def _on_skill_selected(self, skill_id: str):
        """Заполняет поля данными выбранного скилла."""
        router = self._get_router()
        all_skills = router.get_all_skills() if hasattr(router, "get_all_skills") else {}
        skill = all_skills.get(skill_id)
        if not skill:
            return

        self._current_selected_skill_id = skill_id
        is_builtin = skill.get("is_builtin", False)

        self.line_skill_id.setText(skill_id)
        self.line_skill_id.setEnabled(False)

        self.line_skill_title.setText(skill.get("title", ""))
        self.line_skill_title.setEnabled(True)
        self.line_skill_title.setReadOnly(is_builtin)

        self.text_human_desc.setPlainText(skill.get("human_description", ""))
        self.text_human_desc.setEnabled(True)
        self.text_human_desc.setReadOnly(is_builtin)

        self.text_triggers.setPlainText(skill.get("triggers", ""))
        self.text_triggers.setEnabled(True)
        self.text_triggers.setReadOnly(is_builtin)

        display_prompt = skill.get("system_prompt", "")
        if is_builtin:
            try:
                from services.assistant_prompt_builder import AssistantPromptBuilder
                parent_win = self.parent()
                u_name = None
                if parent_win and hasattr(parent_win, "app_config") and isinstance(parent_win.app_config, dict):
                    u_name = parent_win.app_config.get("user_name")
                effective = AssistantPromptBuilder.get_effective_skill_prompt(skill_id, user_name=u_name)
                if effective:
                    display_prompt = effective
            except Exception:
                pass
            if hasattr(self, "lbl_system_prompt"):
                self.lbl_system_prompt.setText("Реальный системный промпт (подставляется в LLM, только чтение):")
        else:
            if hasattr(self, "lbl_system_prompt"):
                self.lbl_system_prompt.setText("Системный промпт / инструкция скилла:")

        self.text_system_prompt.setPlainText(display_prompt)
        self.text_system_prompt.setEnabled(True)
        self.text_system_prompt.setReadOnly(is_builtin)

        self.btn_save_skill.setEnabled(not is_builtin)
        self.btn_delete_skill.setEnabled(not is_builtin)
        if is_builtin:
            self.btn_delete_skill.setToolTip("Базовые скиллы защищены от удаления")
            self.btn_save_skill.setToolTip("Базовые скиллы защищены от изменений")
        else:
            self.btn_delete_skill.setToolTip("Удалить пользовательский скилл")
            self.btn_save_skill.setToolTip("Сохранить изменения скилла")

    def _on_add_skill_clicked(self):
        """Подготавливает форму для создания нового кастомного скилла."""
        self._current_selected_skill_id = None
        self.list_skills.setCurrentRow(-1)

        self.line_skill_id.clear()
        self.line_skill_id.setEnabled(True)

        self.line_skill_title.clear()
        self.line_skill_title.setEnabled(True)
        self.line_skill_title.setReadOnly(False)

        self.text_human_desc.clear()
        self.text_human_desc.setEnabled(True)
        self.text_human_desc.setReadOnly(False)

        self.text_triggers.clear()
        self.text_triggers.setEnabled(True)
        self.text_triggers.setReadOnly(False)

        self.text_system_prompt.clear()
        self.text_system_prompt.setEnabled(True)
        self.text_system_prompt.setReadOnly(False)

        if hasattr(self, "lbl_system_prompt"):
            self.lbl_system_prompt.setText("Системный промпт / инструкция скилла:")

        self.btn_save_skill.setEnabled(True)
        self.btn_save_skill.setToolTip("Сохранить новый скилл")
        self.btn_delete_skill.setEnabled(False)
        self.btn_delete_skill.setToolTip("")

        self.line_skill_id.setFocus()

    def _on_save_skill_clicked(self):
        """Сохраняет кастомный скилл через роутер."""
        s_id = self.line_skill_id.text().strip()
        title = self.line_skill_title.text().strip()
        desc = self.text_human_desc.toPlainText().strip()
        triggers = self.text_triggers.toPlainText().strip()
        sys_prompt = self.text_system_prompt.toPlainText().strip()

        if not s_id:
            toast_warning(
                self,
                title="Заполните ID",
                content="Идентификатор скилла (ID) не может быть пустым.",
            )
            self.line_skill_id.setFocus()
            return

        from services.skill_router import ASSISTANT_SKILLS
        if s_id in ASSISTANT_SKILLS:
            toast_error(
                self,
                title="Запрещено",
                content=f"Нельзя перезаписать базовый скилл «{s_id}».",
            )
            return

        if not title:
            toast_warning(
                self,
                title="Заполните название",
                content="Название скилла не может быть пустым.",
            )
            self.line_skill_title.setFocus()
            return

        skill_dict = {
            "skill_id": s_id,
            "title": title,
            "human_description": desc,
            "triggers": triggers,
            "system_prompt": sys_prompt,
            "is_builtin": False
        }

        router = self._get_router()
        ok = router.save_custom_skill(skill_dict)
        if ok:
            toast_success(
                self,
                title="Скилл сохранен",
                content=f"Скилл «{title}» успешно сохранен в базе.",
            )
            self._load_skills_list(select_skill_id=s_id)
        else:
            toast_error(
                self,
                title="Ошибка сохранения",
                content="Не удалось сохранить скилл. Проверьте правильность данных.",
            )

    def _on_delete_skill_clicked(self):
        """Удаляет выбранный кастомный скилл с подтверждением."""
        s_id = self.line_skill_id.text().strip()
        title = self.line_skill_title.text().strip() or s_id
        if not s_id:
            return

        from services.skill_router import ASSISTANT_SKILLS
        if s_id in ASSISTANT_SKILLS:
            toast_error(
                self,
                title="Запрещено",
                content="Нельзя удалять базовые скиллы.",
            )
            return

        box = MessageBox(
            "Удаление скилла",
            f"Вы действительно хотите удалить скилл «{title}»?",
            self
        )
        box.yesButton.setText("Удалить")
        box.cancelButton.setText("Отмена")

        if box.exec():
            router = self._get_router()
            ok = router.delete_custom_skill(s_id)
            if ok:
                toast_success(
                    self,
                    title="Скилл удален",
                    content=f"Скилл «{title}» успешно удален.",
                )
                self._load_skills_list()
                self._on_add_skill_clicked()
            else:
                toast_error(
                    self,
                    title="Ошибка удаления",
                    content=f"Не удалось удалить скилл «{s_id}».",
                )

    def reload_documents(self):
        """Перезагружает и отображает список карточек документов из реестра."""
        # Очистка существующих карточек
        while self.cards_layout.count() > 0:
            child = self.cards_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        documents = KnowledgeManager.get_documents()
        total_triggers = sum(len(d.get("triggers", [])) for d in documents)

        self.caption_label.setText(
            f"Проиндексировано документов: {len(documents)} • Ключевых триггеров: {total_triggers}"
        )

        if not documents:
            empty_frame = QWidget(self.cards_container)
            empty_layout = QVBoxLayout(empty_frame)
            empty_layout.setContentsMargins(20, 60, 20, 60)
            empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            empty_icon = IconWidget(SafeFIF.BOOK_SHELF, empty_frame)
            empty_icon.setFixedSize(48, 48)
            empty_layout.addWidget(empty_icon, 0, Qt.AlignmentFlag.AlignCenter)

            empty_lbl = CaptionLabel("База знаний пуста.", empty_frame)
            empty_lbl.setStyleSheet("color: #CBD5E1; font-size: 13px; font-weight: 600; margin-top: 8px;")
            empty_layout.addWidget(empty_lbl, 0, Qt.AlignmentFlag.AlignCenter)

            sub_lbl = CaptionLabel("Нажмите «Добавить документ» или скопируйте файлы в папку knowledge_base/", empty_frame)
            sub_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
            empty_layout.addWidget(sub_lbl, 0, Qt.AlignmentFlag.AlignCenter)

            self.cards_layout.addWidget(empty_frame)
            return

        # Добавляем карточки документов
        for doc in documents:
            card = DocumentCardWidget(doc, on_delete=self._on_delete_document, parent=self.cards_container)
            self.cards_layout.addWidget(card)

    def _on_add_document_clicked(self):
        """Открытие диалога выбора файлов для добавления в базу знаний."""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите документы для базы знаний Эларии",
            "",
            "Все поддерживаемые документы (*.pdf *.docx *.xlsx *.txt *.md *.json *.yaml *.yml *.toml *.csv *.py *.js *.ts *.cpp *.cs *.rs *.sql *.sh *.bat *.fb2);;Документы Office (*.pdf *.docx *.xlsx);;Исходный код и конфиги (*.py *.js *.ts *.cpp *.cs *.rs *.sql *.json *.yaml *.toml);;Все файлы (*.*)"
        )
        if not file_paths:
            return

        added_count = 0
        for f_path in file_paths:
            if KnowledgeManager.import_file(f_path):
                added_count += 1

        self.reload_documents()

        if added_count > 0:
            toast_success(
                self,
                title="Документы добавлены",
                content=f"Успешно импортировано и проиндексировано файлов: {added_count}",
            )
        else:
            toast_error(
                self,
                title="Ошибка импорта",
                content="Не удалось импортировать выбранные файлы.",
            )

    def _on_open_folder_clicked(self):
        """Открывает системную папку knowledge_base/."""
        k_dir = KnowledgeManager.get_knowledge_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(k_dir)))

    def _on_sync_clicked(self):
        """Ручной запуск сканирования и синхронизации реестра."""
        try:
            reg = KnowledgeManager.scan_and_sync()
            self.reload_documents()
            doc_count = reg.get("total_documents", len(reg.get("documents", {})))
            toast_success(
                self,
                title="Синхронизация завершена",
                content=f"База знаний актуализирована. Документов в реестре: {doc_count}",
            )
        except Exception as e:
            logger.error(f"[KnowledgeManagerDialog] Ошибка синхронизации: {e}", exc_info=True)
            toast_error(
                self,
                title="Ошибка синхронизации",
                content=str(e),
            )

    def _on_delete_document(self, filename: str):
        """Удаление документа с подтверждением пользователя."""
        box = MessageBox(
            "Удаление документа",
            f"Вы действительно хотите удалить документ «{filename}» из базы знаний?",
            self
        )
        box.yesButton.setText("Удалить")
        box.cancelButton.setText("Отмена")

        if box.exec():
            success = KnowledgeManager.delete_file(filename)
            self.reload_documents()
            if success:
                toast_success(
                    self,
                    title="Удалено",
                    content=f"Документ «{filename}» удален из базы знаний.",
                )
            else:
                toast_error(
                    self,
                    title="Ошибка удаления",
                    content=f"Не удалось удалить документ «{filename}».",
                )
