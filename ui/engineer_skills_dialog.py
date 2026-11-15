"""
ui/engineer_skills_dialog.py - Центр управления знаниями инженера (Skills Hub).
Предоставляет интерфейс управления инженерными скиллами (архетипами разработки),
редактирования триггеров и правил, выбора иконок и тестовый стенд роутинга
с вычислением FastEmbed сходства на CPU в эстетике NeonRoute Glassmorphism.
"""

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap, QIcon, QColor, QBrush, QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QWidget,
    QSplitter, QScrollArea, QFileDialog, QMessageBox, QHeaderView,
    QProgressBar, QListWidgetItem, QTableWidgetItem
)
from qfluentwidgets import (
    LineEdit,
    TextEdit,
    PushButton,
    PrimaryPushButton,
    ListWidget,
    TableWidget,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition
)
from ui.common.toast_helper import toast_success, toast_warning, toast_error

from services.engineer_skill_router import (
    EngineerSkillRouter,
    SKILL_ICONS_DIR,
    FALLBACK_SKILL,
    CONFIDENCE_THRESHOLD
)

logger = logging.getLogger("NeonRoute.EngineerSkillsDialog")


class SkillListItemWidget(QWidget):
    """
    Кастомный виджет элемента списка скиллов:
    Иконка 36x36, название и Fluent-бейдж [Базовый] или [Пользовательский].
    """

    def __init__(self, title: str, is_builtin: bool, icon_path: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        # 1. Аватар / Иконка
        self.icon_lbl = QLabel(self)
        self.icon_lbl.setFixedSize(36, 36)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_lbl.setStyleSheet(
            "border-radius: 6px; background-color: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.08);"
        )

        self.update_icon(icon_path, is_builtin)
        layout.addWidget(self.icon_lbl)

        # 2. Текстовая колонка
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

    def update_icon(self, icon_path: str, is_builtin: bool):
        if icon_path and Path(icon_path).exists():
            pix = QPixmap(str(icon_path)).scaled(
                32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            self.icon_lbl.setPixmap(pix)
        else:
            self.icon_lbl.setText("⚙️" if is_builtin else "💡")


class RoutingResultBadge(QFrame):
    """
    Стилизованный цветной бейдж для отображения лидера роутинга:
    - Мгновенное сходство FastEmbed (неоновый циан)
    - Аварийный фоллбэк (сланцево-серый)
    - Состояние ожидания и загрузки
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RoutingResultBadge")
        self._setup_ui()
        self.set_idle()

    def _setup_ui(self):
        self.setFixedHeight(50)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(12)

        self.icon_lbl = QLabel(self)
        self.icon_lbl.setFixedWidth(24)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_lbl)

        self.text_lbl = QLabel(self)
        self.text_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.text_lbl.setWordWrap(True)
        layout.addWidget(self.text_lbl, stretch=1)

        self.tag_lbl = QLabel(self)
        self.tag_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tag_lbl.setVisible(False)
        layout.addWidget(self.tag_lbl)

    def set_idle(self):
        self._apply_style("idle")
        self.icon_lbl.setText("💡")
        self.text_lbl.setText(
            "<span style='color: #94A3B8; font-size: 11px;'><i>"
            "Введите пример задачи разработчика и нажмите «⚡ Проверить» для расчета скоров FastEmbed..."
            "</i></span>"
        )
        self.tag_lbl.setVisible(False)

    def set_loading(self):
        self._apply_style("loading")
        self.icon_lbl.setText("⏳")
        self.text_lbl.setText(
            "<span style='color: #F59E0B; font-size: 11px;'><i>"
            "Вычисление семантического сходства FastEmbed на CPU..."
            "</i></span>"
        )
        self.tag_lbl.setVisible(False)

    def set_winner(self, title: str, skill_id: str, score: float, is_builtin: bool):
        self._apply_style("winner")
        self.icon_lbl.setText("🎯")
        type_str = "Базовый" if is_builtin else "Пользовательский"
        self.text_lbl.setText(
            f"<b style='color: #FFFFFF; font-size: 12px;'>{title}</b> • "
            f"<span style='color: #00F0FF; font-family: Consolas, monospace;'>{skill_id}</span> "
            f"<span style='color: #64748B;'>({type_str})</span>"
        )
        self.tag_lbl.setText(f"🧠 FASTEMBED ({score:.4f})")
        self.tag_lbl.setStyleSheet("""
            background-color: rgba(0, 240, 255, 0.20);
            color: #00F0FF;
            border: 1px solid rgba(0, 240, 255, 0.50);
            border-radius: 6px;
            padding: 3px 8px;
            font-size: 10px;
            font-weight: 700;
        """)
        self.tag_lbl.setVisible(True)

    def set_fallback(self, title: str, skill_id: str, score: float):
        self._apply_style("fallback")
        self.icon_lbl.setText("⚪")
        self.text_lbl.setText(
            f"<b style='color: #CBD5E1; font-size: 12px;'>{title}</b> • "
            f"<span style='color: #94A3B8; font-family: Consolas, monospace;'>{skill_id}</span> "
            f"<span style='color: #F87171;'>(Ниже порога 0.25 — активирован фоллбэк)</span>"
        )
        self.tag_lbl.setText(f"⚪ ФОЛЛБЭК ({score:.4f})")
        self.tag_lbl.setStyleSheet("""
            background-color: rgba(148, 163, 184, 0.20);
            color: #CBD5E1;
            border: 1px solid rgba(148, 163, 184, 0.50);
            border-radius: 6px;
            padding: 3px 8px;
            font-size: 10px;
            font-weight: 700;
        """)
        self.tag_lbl.setVisible(True)

    def set_error(self, message: str):
        self._apply_style("error")
        self.icon_lbl.setText("❌")
        self.text_lbl.setText(f"<span style='color: #F87171; font-size: 11px;'>{message}</span>")
        self.tag_lbl.setVisible(False)

    def _apply_style(self, mode: str):
        border_color = "rgba(255, 255, 255, 0.08)"
        bg_color = "rgba(18, 24, 38, 0.65)"
        if mode == "winner":
            border_color = "rgba(0, 240, 255, 0.40)"
            bg_color = "rgba(0, 240, 255, 0.08)"
        elif mode == "loading":
            border_color = "rgba(245, 158, 11, 0.40)"
            bg_color = "rgba(245, 158, 11, 0.08)"
        elif mode == "fallback":
            border_color = "rgba(148, 163, 184, 0.40)"
            bg_color = "rgba(148, 163, 184, 0.08)"
        elif mode == "error":
            border_color = "rgba(239, 68, 68, 0.40)"
            bg_color = "rgba(239, 68, 68, 0.08)"

        self.setStyleSheet(f"""
            QFrame#RoutingResultBadge {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
        """)


class RoutingTestWorker(QThread):
    """Фоновый воркер для вычисления векторного ранжирования скиллов."""
    result_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, query: str, router: EngineerSkillRouter, parent=None):
        super().__init__(parent)
        self.query = query
        self.router = router
        self._is_aborted = False

    def abort(self):
        self._is_aborted = True
        self.requestInterruption()

    def run(self):
        try:
            if self._is_aborted or self.isInterruptionRequested():
                return
            results = self.router.test_route_ranking(self.query)
            if not self._is_aborted and not self.isInterruptionRequested():
                self.result_ready.emit(results)
        except Exception as e:
            if not self._is_aborted:
                self.error_occurred.emit(str(e))


class EngineerSkillsDialog(QDialog):
    """
    Центр управления знаниями инженера (Skills Hub):
    - Сплит 35% / 65% (Список скиллов + Форма просмотра/редактирования)
    - Тестовый стенд роутинга с RoutingResultBadge и интерактивной таблицей ранжирования
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🧠 Знания инженера (Skills Hub)")
        self.resize(1020, 780)
        self.setMinimumSize(880, 640)

        self.router = EngineerSkillRouter.get_instance()
        self._current_selected_id: Optional[str] = None
        self._current_icon_path: str = ""
        self._worker: Optional[RoutingTestWorker] = None

        self._setup_style()
        self._setup_ui()
        self._populate_skills_list()

    def _setup_style(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0B0F19;
                color: #CBD5E1;
                font-family: 'Inter', 'Segoe UI', sans-serif;
            }
            QSplitter::handle {
                background-color: rgba(255, 255, 255, 0.06);
            }
            QSplitter::handle:hover {
                background-color: #00F0FF;
            }
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
            TableWidget {
                background-color: rgba(15, 23, 42, 0.70);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                color: #E2E8F0;
                gridline-color: rgba(255, 255, 255, 0.04);
                selection-background-color: rgba(0, 240, 255, 0.16);
                selection-color: #00F0FF;
                font-size: 12px;
            }
            TableWidget::item {
                padding: 4px 8px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            }
            QHeaderView::section {
                background-color: rgba(18, 24, 38, 0.95);
                color: #94A3B8;
                font-size: 11px;
                font-weight: bold;
                padding: 6px 8px;
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.08);
                border-right: 1px solid rgba(255, 255, 255, 0.04);
            }
            QLabel {
                color: #CBD5E1;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
        """)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(14)

        # 1. Верхний заголовок
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        title_lbl = QLabel("🧠 Центр управления знаниями инженера", self)
        title_lbl.setStyleSheet("font-size: 18px; font-weight: 700; color: #FFFFFF;")
        header_row.addWidget(title_lbl)

        header_row.addStretch()

        btn_close_top = PushButton(FIF.CLOSE, "Закрыть", self)
        btn_close_top.clicked.connect(self.close)
        header_row.addWidget(btn_close_top)
        main_layout.addLayout(header_row)

        # 2. Основной вертикальный сплиттер (Верх: Сплит 35/65, Низ: Тестовый стенд)
        v_splitter = QSplitter(Qt.Orientation.Vertical, self)
        v_splitter.setChildrenCollapsible(False)

        # === ВЕРХНЯЯ ЧАСТЬ (Сплит 35% / 65%) ===
        top_splitter = QSplitter(Qt.Orientation.Horizontal, v_splitter)
        top_splitter.setChildrenCollapsible(False)

        # Левая панель (35%): Список скиллов + кнопка Добавить
        left_widget = QWidget(top_splitter)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(8)

        lbl_list_title = QLabel("Доступные навыки и архетипы:", left_widget)
        lbl_list_title.setStyleSheet("font-weight: 600; font-size: 12px; color: #94A3B8;")
        left_layout.addWidget(lbl_list_title)

        self.list_skills = ListWidget(left_widget)
        self.list_skills.currentRowChanged.connect(self._on_skill_selected)
        left_layout.addWidget(self.list_skills, stretch=1)

        self.btn_add_skill = PushButton(FIF.ADD, "Добавить скилл", left_widget)
        self.btn_add_skill.clicked.connect(self._on_add_skill_clicked)
        left_layout.addWidget(self.btn_add_skill)

        top_splitter.addWidget(left_widget)

        # Правая панель (65%): Форма просмотра/редактирования скилла
        right_scroll = QScrollArea(top_splitter)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)

        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)

        # Блок Название и ID
        id_title_row = QHBoxLayout()
        id_title_row.setSpacing(10)

        id_box = QVBoxLayout()
        id_box.setSpacing(4)
        lbl_id = QLabel("ID скилла (системный):", right_container)
        lbl_id.setStyleSheet("font-size: 11px; color: #94A3B8;")
        self.line_skill_id = LineEdit(right_container)
        self.line_skill_id.setPlaceholderText("например: my_custom_skill")
        id_box.addWidget(lbl_id)
        id_box.addWidget(self.line_skill_id)
        id_title_row.addLayout(id_box, stretch=1)

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        lbl_title = QLabel("Название:", right_container)
        lbl_title.setStyleSheet("font-size: 11px; color: #94A3B8;")
        self.line_title = LineEdit(right_container)
        self.line_title.setPlaceholderText("Название скилла (например: Анализ логов и метрик)")
        title_box.addWidget(lbl_title)
        title_box.addWidget(self.line_title)
        id_title_row.addLayout(title_box, stretch=2)

        right_layout.addLayout(id_title_row)

        # Блок "Иконка / Картинка"
        icon_group = QHBoxLayout()
        icon_group.setSpacing(12)

        self.lbl_icon_preview = QLabel(right_container)
        self.lbl_icon_preview.setFixedSize(48, 48)
        self.lbl_icon_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_icon_preview.setStyleSheet("""
            QLabel {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(0, 240, 255, 0.35);
                border-radius: 8px;
            }
        """)
        icon_group.addWidget(self.lbl_icon_preview)

        icon_btn_box = QVBoxLayout()
        icon_btn_box.setSpacing(4)
        self.btn_choose_icon = PushButton(FIF.PHOTO, "Выбрать картинку...", right_container)
        self.btn_choose_icon.clicked.connect(self._on_choose_icon_clicked)
        icon_btn_box.addWidget(self.btn_choose_icon)

        self.lbl_icon_hint = QLabel("Формат PNG/JPG, сохраняется в data/skill_icons/", right_container)
        self.lbl_icon_hint.setStyleSheet("font-size: 10px; color: #64748B;")
        icon_btn_box.addWidget(self.lbl_icon_hint)
        icon_group.addLayout(icon_btn_box)
        icon_group.addStretch()

        right_layout.addLayout(icon_group)

        # Поле «Понятное описание для человека»
        lbl_desc = QLabel("Понятное описание для человека:", right_container)
        lbl_desc.setStyleSheet("font-size: 11px; color: #94A3B8;")
        right_layout.addWidget(lbl_desc)

        self.text_human_desc = TextEdit(right_container)
        self.text_human_desc.setPlaceholderText("Кратко объясните, для каких задач этот навык...")
        self.text_human_desc.setFixedHeight(65)
        right_layout.addWidget(self.text_human_desc)

        # Поле «Якоря поиска (триггеры FastEmbed)»
        lbl_anchors = QLabel("Якоря поиска (триггеры FastEmbed, по одной ключевой фразе на строку):", right_container)
        lbl_anchors.setStyleSheet("font-size: 11px; color: #94A3B8;")
        right_layout.addWidget(lbl_anchors)

        self.text_anchors = TextEdit(right_container)
        self.text_anchors.setPlaceholderText("Ключевые фразы, по одной на строку...")
        self.text_anchors.setFixedHeight(75)
        right_layout.addWidget(self.text_anchors)

        # Поле «Инструкции для Архитектора»
        lbl_arch = QLabel("Инструкции для Архитектора:", right_container)
        lbl_arch.setStyleSheet("font-size: 11px; color: #94A3B8;")
        right_layout.addWidget(lbl_arch)

        self.text_architect = TextEdit(right_container)
        self.text_architect.setPlaceholderText("Архитектурные правила, контракты и ограничения...")
        self.text_architect.setFixedHeight(85)
        right_layout.addWidget(self.text_architect)

        # Поле «Инструкции для Работяги»
        lbl_work = QLabel("Инструкции для Работяги:", right_container)
        lbl_work.setStyleSheet("font-size: 11px; color: #94A3B8;")
        right_layout.addWidget(lbl_work)

        self.text_worker = TextEdit(right_container)
        self.text_worker.setPlaceholderText("Правила кодогенерации, целевые библиотеки, ограничения...")
        self.text_worker.setFixedHeight(85)
        right_layout.addWidget(self.text_worker)

        # Поле «Файл запуска по умолчанию»
        lbl_art = QLabel("Файл запуска по умолчанию:", right_container)
        lbl_art.setStyleSheet("font-size: 11px; color: #94A3B8;")
        right_layout.addWidget(lbl_art)

        self.line_artifact = LineEdit(right_container)
        self.line_artifact.setPlaceholderText("например: main.py, app.py или game.py")
        right_layout.addWidget(self.line_artifact)

        # Кнопки сохранения и удаления
        form_actions = QHBoxLayout()
        form_actions.setSpacing(10)

        self.btn_save = PrimaryPushButton(FIF.SAVE, "Сохранить изменения", right_container)
        self.btn_save.clicked.connect(self._on_save_skill_clicked)
        form_actions.addWidget(self.btn_save)

        self.btn_delete = PushButton(FIF.DELETE, "Удалить", right_container)
        self.btn_delete.clicked.connect(self._on_delete_skill_clicked)
        form_actions.addWidget(self.btn_delete)

        form_actions.addStretch()
        right_layout.addLayout(form_actions)

        right_scroll.setWidget(right_container)
        top_splitter.addWidget(right_scroll)

        top_splitter.setStretchFactor(0, 35)
        top_splitter.setStretchFactor(1, 65)

        v_splitter.addWidget(top_splitter)

        # === НИЖНЯЯ ЧАСТЬ (Тестовый стенд роутинга) ===
        test_panel = QWidget(v_splitter)
        test_layout = QVBoxLayout(test_panel)
        test_layout.setContentsMargins(0, 8, 0, 0)
        test_layout.setSpacing(8)

        lbl_test_title = QLabel("⚡ Тестовый стенд роутинга (FastEmbed CPU Matcher):", test_panel)
        lbl_test_title.setStyleSheet("font-weight: 700; font-size: 12px; color: #00F0FF;")
        test_layout.addWidget(lbl_test_title)

        test_input_row = QHBoxLayout()
        test_input_row.setSpacing(10)

        self.line_test_query = LineEdit(test_panel)
        self.line_test_query.setPlaceholderText("Введите пример задачи, чтобы проверить реакцию роутера...")
        self.line_test_query.returnPressed.connect(self._on_run_test_clicked)
        test_input_row.addWidget(self.line_test_query, stretch=1)

        self.btn_run_test = PrimaryPushButton(FIF.SEARCH, "Проверить", test_panel)
        self.btn_run_test.clicked.connect(self._on_run_test_clicked)
        test_input_row.addWidget(self.btn_run_test)
        test_layout.addLayout(test_input_row)

        self.result_badge = RoutingResultBadge(test_panel)
        test_layout.addWidget(self.result_badge)

        # Таблица результатов тестирования
        self.table_results = TableWidget(test_panel)
        self.table_results.setColumnCount(5)
        self.table_results.setHorizontalHeaderLabels([
            "Иконка",
            "Скилл",
            "Тип",
            "Скор (FastEmbed)",
            "Соответствие"
        ])
        self.table_results.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table_results.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table_results.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table_results.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table_results.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        self.table_results.setColumnWidth(0, 60)
        self.table_results.setColumnWidth(1, 260)
        self.table_results.setColumnWidth(2, 130)
        self.table_results.setColumnWidth(3, 140)

        self.table_results.verticalHeader().setVisible(False)
        self.table_results.verticalHeader().setDefaultSectionSize(36)
        self.table_results.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.table_results.setSelectionMode(TableWidget.SelectionMode.SingleSelection)
        test_layout.addWidget(self.table_results, stretch=1)

        v_splitter.addWidget(test_panel)

        v_splitter.setStretchFactor(0, 60)
        v_splitter.setStretchFactor(1, 40)

        main_layout.addWidget(v_splitter, stretch=1)

    def _populate_skills_list(self, select_id: Optional[str] = None):
        """Заполняет список скиллов из роутера."""
        self.list_skills.blockSignals(True)
        self.list_skills.clear()

        all_skills = self.router.get_all_skills()
        selected_row = 0

        row = 0
        for sid, sdata in all_skills.items():
            item = QListWidgetItem(self.list_skills)
            item.setSizeHint(QSize(220, 52))
            item.setData(Qt.ItemDataRole.UserRole, sid)

            widget = SkillListItemWidget(
                title=sdata.get("title", sid),
                is_builtin=sdata.get("is_builtin", False),
                icon_path=sdata.get("icon_path", "")
            )
            self.list_skills.setItemWidget(item, widget)

            if select_id and sid == select_id:
                selected_row = row
            row += 1

        self.list_skills.blockSignals(False)

        if self.list_skills.count() > 0:
            self.list_skills.setCurrentRow(selected_row)
            self._on_skill_selected(selected_row)

    def _on_skill_selected(self, row: int):
        """Отображает данные выбранного скилла в форме редактирования."""
        item = self.list_skills.item(row)
        if not item:
            return

        sid = item.data(Qt.ItemDataRole.UserRole)
        self._current_selected_id = sid

        all_skills = self.router.get_all_skills()
        skill = all_skills.get(sid, {})

        is_builtin = skill.get("is_builtin", False)

        self.line_skill_id.setText(sid)
        self.line_skill_id.setEnabled(not is_builtin)

        self.line_title.setText(skill.get("title", ""))
        self.line_title.setEnabled(not is_builtin)

        self.text_human_desc.setPlainText(skill.get("human_description", ""))
        self.text_human_desc.setEnabled(not is_builtin)

        anchors = skill.get("anchors", [])
        if isinstance(anchors, list):
            self.text_anchors.setPlainText("\n".join(anchors))
        else:
            self.text_anchors.setPlainText(str(anchors))
        self.text_anchors.setEnabled(not is_builtin)

        self.text_architect.setPlainText(skill.get("architect_rules", ""))
        self.text_architect.setEnabled(not is_builtin)

        self.text_worker.setPlainText(skill.get("worker_rules", ""))
        self.text_worker.setEnabled(not is_builtin)

        self.line_artifact.setText(skill.get("default_artifact", "main.py"))
        self.line_artifact.setEnabled(not is_builtin)

        self._current_icon_path = skill.get("icon_path", "")
        self._update_icon_preview(self._current_icon_path)
        self.btn_choose_icon.setEnabled(not is_builtin)

        self.btn_save.setEnabled(not is_builtin)
        self.btn_delete.setEnabled(not is_builtin)

    def _update_icon_preview(self, icon_path: str):
        if icon_path and Path(icon_path).exists():
            pix = QPixmap(str(icon_path)).scaled(
                42, 42, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            self.lbl_icon_preview.setPixmap(pix)
            self.lbl_icon_preview.setText("")
        else:
            self.lbl_icon_preview.setPixmap(QPixmap())
            self.lbl_icon_preview.setText("🖼️")

    def _on_choose_icon_clicked(self):
        """Диалог выбора картинки и копирование в data/skill_icons/."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите иконку скилла",
            "",
            "Изображения (*.png *.jpg *.jpeg *.webp *.svg *.bmp)"
        )
        if not file_path:
            return

        sid = self.line_skill_id.text().strip() or "custom_skill"
        sid = re.sub(r"[^a-zA-Z0-9_]", "_", sid.lower())

        dest_dir = SKILL_ICONS_DIR
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / f"{sid}.png"

        try:
            shutil.copy2(file_path, dest_file)
            self._current_icon_path = f"data/skill_icons/{sid}.png"
            self._update_icon_preview(str(dest_file))
            toast_success(
                self,
                title="Иконка загружена",
                content=f"Файл сохранен: {dest_file.name}",
            )
        except Exception as e:
            logger.error(f"[EngineerSkillsDialog] Ошибка копирования иконки: {e}", exc_info=True)
            toast_error(
                self,
                title="Ошибка",
                content=f"Не удалось сохранить иконку: {e}",
            )

    def _on_add_skill_clicked(self):
        """Очищает форму для создания нового кастомного скилла."""
        self.list_skills.clearSelection()
        self._current_selected_id = None
        self._current_icon_path = ""

        self.line_skill_id.setText("")
        self.line_skill_id.setEnabled(True)
        self.line_skill_id.setFocus()

        self.line_title.setText("")
        self.line_title.setEnabled(True)

        self.text_human_desc.setPlainText("")
        self.text_human_desc.setEnabled(True)

        self.text_anchors.setPlainText("")
        self.text_anchors.setEnabled(True)

        self.text_architect.setPlainText("")
        self.text_architect.setEnabled(True)

        self.text_worker.setPlainText("")
        self.text_worker.setEnabled(True)

        self.line_artifact.setText("main.py")
        self.line_artifact.setEnabled(True)

        self._update_icon_preview("")
        self.btn_choose_icon.setEnabled(True)

        self.btn_save.setEnabled(True)
        self.btn_delete.setEnabled(False)

    def _on_save_skill_clicked(self):
        """Валидация и сохранение кастомного скилла в JSON."""
        sid = self.line_skill_id.text().strip()
        title = self.line_title.text().strip()

        if not sid:
            if title:
                sid = re.sub(r"[^a-zA-Z0-9_]", "_", title.lower())
                self.line_skill_id.setText(sid)
            else:
                toast_warning(
                    self,
                    title="Ошибка валидации",
                    content="Укажите ID скилла или название.",
                )
                return

        if not title:
            toast_warning(
                self,
                title="Ошибка валидации",
                content="Укажите название скилла.",
            )
            return

        anchors_raw = self.text_anchors.toPlainText()
        anchors = [line.strip() for line in anchors_raw.splitlines() if line.strip()]

        skill_dict = {
            "skill_id": sid,
            "title": title,
            "human_description": self.text_human_desc.toPlainText().strip(),
            "anchors": anchors,
            "triggers": ", ".join(anchors[:5]),
            "architect_rules": self.text_architect.toPlainText().strip(),
            "worker_rules": self.text_worker.toPlainText().strip(),
            "default_artifact": self.line_artifact.text().strip() or "main.py",
            "icon_path": self._current_icon_path,
            "is_builtin": False
        }

        try:
            self.router.save_custom_skill(skill_dict)
            toast_success(
                self,
                title="Скилл сохранен",
                content=f"Навык «{title}» успешно сохранен и доступен в роутере.",
            )
            self._populate_skills_list(select_id=sid)
        except Exception as e:
            logger.error(f"[EngineerSkillsDialog] Ошибка сохранения скилла: {e}", exc_info=True)
            toast_error(
                self,
                title="Ошибка сохранения",
                content=str(e),
            )

    def _on_delete_skill_clicked(self):
        """Удаление кастомного скилла."""
        if not self._current_selected_id:
            return

        reply = QMessageBox.question(
            self,
            "Подтверждение удаления",
            f"Вы уверены, что хотите удалить скилл '{self._current_selected_id}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            ok = self.router.delete_custom_skill(self._current_selected_id)
            if ok:
                toast_success(
                    self,
                    title="Скилл удален",
                    content=f"Скилл '{self._current_selected_id}' успешно удален.",
                )
                self._populate_skills_list()
            else:
                toast_warning(
                    self,
                    title="Удаление невозможно",
                    content="Не удалось удалить скилл (возможно, он базовый).",
                )
        except Exception as e:
            logger.error(f"[EngineerSkillsDialog] Ошибка удаления скилла: {e}", exc_info=True)
            toast_error(
                self,
                title="Ошибка",
                content=str(e),
            )

    def _on_run_test_clicked(self):
        """Запуск проверки сопоставления задачи через FastEmbed."""
        query = self.line_test_query.text().strip()
        if not query:
            toast_warning(
                self,
                title="Пустой запрос",
                content="Введите описание задачи для проверки реакции роутера.",
            )
            return

        self.btn_run_test.setEnabled(False)
        self.result_badge.set_loading()

        if self._worker and self._worker.isRunning():
            self._worker.abort()
            self._worker.wait(500)

        self._worker = RoutingTestWorker(query, self.router, self)
        self._worker.result_ready.connect(self._on_test_results_ready)
        self._worker.error_occurred.connect(self._on_test_error)
        self._worker.start()

    def _on_test_results_ready(self, ranking: List[Dict[str, Any]]):
        """Отображение результатов векторного сопоставления в таблице."""
        self.btn_run_test.setEnabled(True)

        if not ranking:
            self.result_badge.set_error("Роутер не вернул результатов.")
            return

        winner = next((r for r in ranking if r.get("is_winner")), ranking[0])
        score = winner.get("score", 0.0)

        if score < CONFIDENCE_THRESHOLD:
            self.result_badge.set_fallback(
                title=winner.get("title", ""),
                skill_id=winner.get("skill_id", ""),
                score=score
            )
        else:
            self.result_badge.set_winner(
                title=winner.get("title", ""),
                skill_id=winner.get("skill_id", ""),
                score=score,
                is_builtin=winner.get("is_builtin", False)
            )

        # Заполнение таблицы
        self.table_results.setRowCount(len(ranking))

        for row, item in enumerate(ranking):
            is_win = item.get("is_winner", False)
            score_val = item.get("score", 0.0)
            icon_path = item.get("icon_path", "")
            is_builtin = item.get("is_builtin", False)

            # Col 0: Иконка
            lbl_icon = QLabel()
            lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if icon_path and Path(icon_path).exists():
                pix = QPixmap(str(icon_path)).scaled(
                    24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                lbl_icon.setPixmap(pix)
            else:
                lbl_icon.setText("⚙️" if is_builtin else "💡")
            self.table_results.setCellWidget(row, 0, lbl_icon)

            # Col 1: Скилл
            item_title = QTableWidgetItem(item.get("title", ""))
            item_title.setFlags(item_title.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if is_win:
                item_title.setForeground(QBrush(QColor("#00F0FF")))
                font = item_title.font()
                font.setBold(True)
                item_title.setFont(font)
                item_title.setBackground(QBrush(QColor(0, 240, 255, 30)))
            self.table_results.setItem(row, 1, item_title)

            # Col 2: Тип
            item_type = QTableWidgetItem("Базовый" if is_builtin else "Пользовательский")
            item_type.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_type.setFlags(item_type.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if is_win:
                item_type.setForeground(QBrush(QColor("#00F0FF")))
                item_type.setBackground(QBrush(QColor(0, 240, 255, 30)))
            self.table_results.setItem(row, 2, item_type)

            # Col 3: Скор
            item_score = QTableWidgetItem(f"{score_val:.4f}")
            item_score.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_score.setFlags(item_score.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if is_win:
                item_score.setForeground(QBrush(QColor("#00F0FF")))
                font = item_score.font()
                font.setBold(True)
                item_score.setFont(font)
                item_score.setBackground(QBrush(QColor(0, 240, 255, 30)))
            self.table_results.setItem(row, 3, item_score)

            # Col 4: Соответствие (ProgressBar)
            pbar_container = QWidget()
            pbar_layout = QHBoxLayout(pbar_container)
            pbar_layout.setContentsMargins(6, 4, 6, 4)

            pbar = QProgressBar(pbar_container)
            pbar.setRange(0, 100)
            pbar_pct = max(0, min(100, int(score_val * 100)))
            pbar.setValue(pbar_pct)
            pbar.setTextVisible(True)
            pbar.setFormat(f"{pbar_pct}%")

            bar_color = "#00F0FF" if is_win else "#38BDF8"
            pbar.setStyleSheet(f"""
                QProgressBar {{
                    background-color: rgba(255, 255, 255, 0.05);
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 4px;
                    text-align: center;
                    color: #FFFFFF;
                    font-size: 10px;
                    font-weight: bold;
                }}
                QProgressBar::chunk {{
                    background-color: {bar_color};
                    border-radius: 3px;
                }}
            """)
            pbar_layout.addWidget(pbar)
            self.table_results.setCellWidget(row, 4, pbar_container)

    def _on_test_error(self, err_msg: str):
        self.btn_run_test.setEnabled(True)
        self.result_badge.set_error(f"Ошибка тестирования: {err_msg}")
