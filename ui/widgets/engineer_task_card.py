"""
Engineer Task Card Widget for NeonRoute.
Monolithic Agent Activity Stream (Cursor Composer / Devin style canvas) for autonomous Engineering Mode.
Features:
- Pure PyQt-Fluent-Widgets & PyQt6 component foundation
- Monolithic translucent task canvas occupying 100% feed width (rgba(15, 23, 42, 0.65), 10px radius, 1px solid rgba(255, 255, 255, 0.08))
- Fully removed avatar column and speech bubble asymmetry (no left indent, no speech tail)
- Activity Header: real-time phase indicator (⚡ RUNNING · 2.4s / ✓ COMPLETED / ✕ FAILED), active skill badge [🎮 Игры и GUI], stop/action buttons
- Smooth collapsible architecture spoiler (SmoothSpoiler) with QPropertyAnimation & QEasingCurve
- Execution Stepper: compact vertical action log (glyphs ✓, ⟳, ●, target paths, [📄 В инспектор] buttons)
- Clean summary box without raw unformatted code dumps
- Strict Qt Use-After-Free guard (sip.isdeleted checks) and cross-session race condition safety
"""

import re
import html
import time
import uuid
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Union

try:
    from PyQt6 import sip
except ImportError:
    import sip

from PyQt6.QtCore import (
    Qt, pyqtSignal, QRect, QSize, QTimer,
    QPropertyAnimation, QEasingCurve
)
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QCursor, QClipboard
)
from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QTextBrowser, QSizePolicy, QApplication, QLayout
)
from qfluentwidgets import (
    SimpleCardWidget,
    BodyLabel,
    CaptionLabel,
    SubtitleLabel,
    StrongBodyLabel,
    TransparentToolButton,
    PillPushButton,
    FluentIcon as FIF,
    ToolTipFilter,
    ToolTipPosition
)

from ui.components.chat_bubble import (
    SelectableTextBrowser,
    ConsoleOutputSpoilerWidget
)

logger = logging.getLogger("NeonRoute.EngineerTaskCard")


class EngineerGlassCard(SimpleCardWidget):
    """
    Монолитная полупрозрачная консоль задачи в стиле Cursor / Devin Task Canvas.
    Деликатная полупрозрачность rgba(15, 23, 42, 0.65) со скруглением 10px
    и тонкой рамкой 1px solid rgba(255, 255, 255, 0.08).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("EngineerGlassCard")
        self.setBorderRadius(10)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("""
            #EngineerGlassCard {
                background-color: rgba(15, 23, 42, 0.65);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }
            #EngineerGlassCard:hover {
                border-color: rgba(0, 240, 255, 0.35);
            }
        """)

    def _normalBackgroundColor(self):
        return QColor(15, 23, 42, 166)  # rgba(15, 23, 42, 0.65)

    def _hoverBackgroundColor(self):
        return QColor(15, 23, 42, 180)

    def _pressedBackgroundColor(self):
        return QColor(15, 23, 42, 190)

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self.backgroundColor)
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1.0))
        r = self.borderRadius
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), r, r)


class CompactWorkspaceFileBadge(QFrame):
    """
    Компактная строка/бейдж шага выполнения в степпере задачи.
    Содержит глиф статуса (✓, ⟳, ●), имя файла и кнопку перехода в инспектор [📄 В инспектор].
    """
    def __init__(
        self,
        file_path: Path | str,
        on_run: Optional[Callable] = None,
        action_text: str = "",
        status: str = "completed",
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.file_path = Path(file_path) if file_path else None
        self.on_run = on_run
        self.action_text = action_text
        self.status = status
        self.setObjectName("CompactWorkspaceFileBadge")
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QFrame#CompactWorkspaceFileBadge {
                background-color: rgba(30, 41, 59, 0.40);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 6px;
            }
            QFrame#CompactWorkspaceFileBadge:hover {
                background-color: rgba(30, 41, 59, 0.70);
                border-color: rgba(0, 240, 255, 0.30);
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)

        # 1. Глиф статуса
        self.glyph_lbl = QLabel(self)
        self.set_status(self.status)
        layout.addWidget(self.glyph_lbl)

        # 2. Иконка типа
        is_py = self.file_path and self.file_path.suffix.lower() == ".py"
        icon_lbl = QLabel(self)
        icon_lbl.setText("🐍" if is_py else "📄")
        icon_lbl.setStyleSheet("font-size: 12px; background: transparent; border: none;")
        layout.addWidget(icon_lbl)

        # 3. Название шага / файла
        display_name = self.action_text
        if not display_name and self.file_path:
            display_name = f"workspace / {self.file_path.name}"
        self.name_lbl = StrongBodyLabel(display_name, self)
        self.name_lbl.setStyleSheet("color: #E2E8F0; font-size: 12px; font-weight: 500; background: transparent; border: none;")
        if self.file_path:
            self.name_lbl.setToolTip(str(self.file_path))
        layout.addWidget(self.name_lbl, stretch=1)

        # 4. Кнопка «📄 В инспектор»
        self.btn_inspect = PillPushButton("📄 В инспектор", self)
        self.btn_inspect.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_inspect.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 240, 255, 0.10);
                color: #00F0FF;
                border: 1px solid rgba(0, 240, 255, 0.25);
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(0, 240, 255, 0.22);
                border-color: #00F0FF;
                color: #FFFFFF;
            }
        """)
        self.btn_inspect.clicked.connect(self._on_inspect_clicked)
        layout.addWidget(self.btn_inspect)

    def set_status(self, status: str):
        self.status = status
        if status in ("completed", "done", "success"):
            self.glyph_lbl.setText("✓")
            self.glyph_lbl.setStyleSheet("color: #10B981; font-size: 12px; font-weight: bold; background: transparent;")
        elif status in ("running", "progress", "active"):
            self.glyph_lbl.setText("●")
            self.glyph_lbl.setStyleSheet("color: #00F0FF; font-size: 12px; font-weight: bold; background: transparent;")
        else:
            self.glyph_lbl.setText("⟳")
            self.glyph_lbl.setStyleSheet("color: #F59E0B; font-size: 12px; font-weight: bold; background: transparent;")

    def _on_inspect_clicked(self):
        if sip.isdeleted(self):
            return

        if self.file_path:
            win = self.window()
            inspector = getattr(win, "workspace_inspector", None)
            if inspector:
                inspector.setVisible(True)
                if hasattr(inspector, "open_file"):
                    inspector.open_file(self.file_path)
                elif hasattr(inspector, "refresh_files"):
                    inspector.refresh_files()




class EngineerSpoilerWidget(QFrame):
    """
    Компактный спойлер архитектуры и анализа в стиле Cursor / Devin.
    Выполнен в виде тонкой горизонтальной плашки-разделителя с плавной анимацией.
    """
    toggled = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("EngineerSpoilerFrame")
        self._is_collapsed: bool = True
        self._is_animating: bool = False
        self._plan_text: str = ""
        self._target_height: int = 140

        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QFrame#EngineerSpoilerFrame {
                background-color: rgba(30, 41, 59, 0.35);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 6px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 2, 4, 2)
        main_layout.setSpacing(2)

        # 1. Тонкая плашка заголовка спойлера
        self.header_widget = QFrame(self)
        self.header_widget.setObjectName("EngineerSpoilerHeader")
        self.header_widget.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.header_widget.setStyleSheet("""
            QFrame#EngineerSpoilerHeader {
                background: transparent;
                border: none;
                border-radius: 4px;
            }
            QFrame#EngineerSpoilerHeader:hover {
                background-color: rgba(255, 255, 255, 0.04);
            }
        """)

        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(6, 4, 6, 4)
        header_layout.setSpacing(6)

        self.chevron_btn = TransparentToolButton(FIF.CHEVRON_RIGHT, self.header_widget)
        self.chevron_btn.setFixedSize(20, 20)
        self.chevron_btn.clicked.connect(self.toggle)
        header_layout.addWidget(self.chevron_btn)

        self.title_lbl = StrongBodyLabel("💭 Архитектура и анализ", self.header_widget)
        self.title_lbl.setStyleSheet("color: #CBD5E1; font-size: 12px; font-weight: 600; background: transparent;")
        header_layout.addWidget(self.title_lbl)

        self.status_lbl = CaptionLabel("· 🧠 Формирование плана...", self.header_widget)
        self.status_lbl.setStyleSheet("color: #38BDF8; font-size: 11px; background: transparent;")
        header_layout.addWidget(self.status_lbl)

        header_layout.addStretch()

        self.toggle_chip = PillPushButton("Развернуть", self.header_widget)
        self.toggle_chip.setFixedSize(76, 20)
        self.toggle_chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.toggle_chip.setStyleSheet("""
            QPushButton {
                background-color: rgba(56, 189, 248, 0.10);
                color: #38BDF8;
                border: 1px solid rgba(56, 189, 248, 0.20);
                border-radius: 10px;
                font-size: 10px;
                font-weight: 600;
                padding: 1px 6px;
            }
            QPushButton:hover {
                background-color: rgba(56, 189, 248, 0.22);
                color: #FFFFFF;
            }
        """)
        self.toggle_chip.clicked.connect(self.toggle)
        header_layout.addWidget(self.toggle_chip)

        self.header_widget.mousePressEvent = lambda e: self.toggle() if (not hasattr(e, "button") or e.button() == Qt.MouseButton.LeftButton) else None
        main_layout.addWidget(self.header_widget)

        # 2. Область содержимого плана
        self.content_area = QFrame(self)
        self.content_area.setObjectName("EngineerSpoilerContent")
        self.content_area.setStyleSheet("background: transparent; border: none;")
        content_layout = QVBoxLayout(self.content_area)
        content_layout.setContentsMargins(6, 2, 6, 4)
        content_layout.setSpacing(0)

        self.text_browser = SelectableTextBrowser(self.content_area)
        self.text_browser.setReadOnly(True)
        self.text_browser.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.text_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.text_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_browser.setMaximumHeight(260)
        self.text_browser.setMinimumHeight(40)
        self.text_browser.setStyleSheet("""
            QTextBrowser {
                background: transparent;
                border: none;
                color: #94A3B8;
                font-family: 'Consolas', 'Fira Code', 'Cascadia Code', monospace;
                font-size: 11px;
                line-height: 1.45;
                padding: 2px;
            }
        """)
        content_layout.addWidget(self.text_browser)
        main_layout.addWidget(self.content_area)

        # Анимация сворачивания/разворачивания
        self.anim = QPropertyAnimation(self.content_area, b"maximumHeight", self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.anim.finished.connect(self._on_anim_finished)

        # По умолчанию свёрнуто
        self.content_area.setMaximumHeight(0)
        self.content_area.setVisible(False)
        self._update_header_visuals(is_collapsed=True)

    def _update_header_visuals(self, is_collapsed: bool):
        if is_collapsed:
            self.chevron_btn.setIcon(FIF.CHEVRON_RIGHT)
            self.toggle_chip.setText("Развернуть")
        else:
            self.chevron_btn.setIcon(FIF.CHEVRON_DOWN_MED)
            self.toggle_chip.setText("Свернуть")

    @property
    def is_collapsed(self) -> bool:
        return self._is_collapsed

    def get_plan_text(self) -> str:
        return self._plan_text

    def set_plan_title(self, title: str):
        self.title_lbl.setText(title)

    def set_status_text(self, status: str, is_active: bool = False):
        self.status_lbl.setText(f"· {status}" if status and not status.startswith("·") else status)
        if is_active:
            self.status_lbl.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: 600; background: transparent;")
        else:
            self.status_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; background: transparent;")

    def set_plan_text(self, text: str, title: Optional[str] = None):
        self._plan_text = text or ""
        if title:
            self.set_plan_title(title)
        self.text_browser.setPlainText(self._plan_text)

        line_count = len(self._plan_text.splitlines()) if self._plan_text else 0
        if line_count > 0 and self._is_collapsed:
            self.set_status_text(f"(свернуто, клик для раскрытия) · {line_count} строк", is_active=False)

        doc = self.text_browser.document()
        doc_h = int(doc.documentLayout().documentSize().height()) if doc else 60
        self._target_height = max(50, min(doc_h + 20, 260))

    def append_plan_chunk(self, chunk: str):
        if not chunk:
            return
        self._plan_text += chunk
        self.text_browser.setPlainText(self._plan_text)
        sb = self.text_browser.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())
        self.set_status_text("🧠 Формирование плана...", is_active=True)

        if self._is_collapsed:
            self.set_collapsed(False, animated=False)

    def toggle(self):
        self.set_collapsed(not self._is_collapsed, animated=True)

    def set_collapsed(self, collapsed: bool, animated: bool = True, status: Optional[str] = None):
        if status:
            self.set_status_text(status, is_active=(not collapsed and "Формирование" in status))

        if self._is_collapsed == collapsed and self.content_area.isVisible() != collapsed:
            return

        self._is_collapsed = collapsed
        self._update_header_visuals(collapsed)

        doc = self.text_browser.document()
        doc_h = int(doc.documentLayout().documentSize().height()) if doc else 60
        target_h = max(50, min(doc_h + 20, 260))
        self._target_height = target_h

        if not animated:
            if collapsed:
                self.content_area.setMaximumHeight(0)
                self.content_area.setVisible(False)
            else:
                self.content_area.setVisible(True)
                self.content_area.setMaximumHeight(target_h)
            self._notify_parent_geometry()
            self.toggled.emit(self._is_collapsed)
            return

        self._is_animating = True
        self.anim.stop()

        if collapsed:
            start_val = self.content_area.height()
            self.anim.setStartValue(start_val)
            self.anim.setEndValue(0)
        else:
            self.content_area.setVisible(True)
            self.anim.setStartValue(0)
            self.anim.setEndValue(target_h)

        self.anim.start()
        self.toggled.emit(self._is_collapsed)

    def _on_anim_finished(self):
        self._is_animating = False
        if self._is_collapsed:
            self.content_area.setVisible(False)
        else:
            self.content_area.setMaximumHeight(self._target_height)
        self._notify_parent_geometry()

    def _notify_parent_geometry(self):
        p = self.parentWidget()
        while p:
            if hasattr(p, "_update_bubble_geometry"):
                p._update_bubble_geometry()
                break
            if hasattr(p, "layout") and p.layout():
                p.layout().activate()
            p = p.parentWidget()


class EngineerTaskCardWidget(QWidget):
    """
    Монолитная агентская карточка выполнения задачи инженера (Task Execution Canvas).
    Полностью устраняет аватарку и асимметрию баблов, занимая всю ширину рабочей области.
    """
    tts_requested = pyqtSignal(str)
    translate_requested = pyqtSignal(str, object)
    regenerate_requested = pyqtSignal(object)
    delete_requested = pyqtSignal(object)
    scene_art_requested = pyqtSignal(str, object)
    run_script_requested = pyqtSignal(str)
    choice_selected = pyqtSignal(str)

    def __init__(
        self,
        sender: str,
        text: str = "",
        avatar_path: Optional[Path] = None,
        session_id: Optional[str] = None,
        parent=None,
        is_active: bool = True,
        **kwargs
    ):
        super().__init__(parent)
        self.setObjectName("EngineerTaskCardWidget")
        self.session_id: Optional[str] = session_id
        self.sender: str = sender
        self.avatar_path: Optional[Path] = None  # Аватарка демонтирована из UI инженера
        self.avatar_lbl = None  # Для совместимости без аватарного контейнера
        self.raw_text: str = text or ""
        self.original_text: str = text or ""
        self.translated_text: Optional[str] = kwargs.get("translated_text")
        self.is_showing_translation: bool = kwargs.get("is_showing_translation", False)
        self.is_user: bool = kwargs.get("is_user", False)
        self.is_error: bool = kwargs.get("is_error", False)
        self.message_id: str = kwargs.get("message_id") or str(uuid.uuid4())
        self.font_size: int = kwargs.get("font_size", 13)
        self.timestamp: str = kwargs.get("timestamp") or ""
        self.audio_path: Optional[Path] = Path(kwargs["audio_path"]) if kwargs.get("audio_path") else None
        self.image_path: Optional[Path] = Path(kwargs["image_path"]) if kwargs.get("image_path") else None
        self.visual_intent: Any = kwargs.get("visual_intent")
        self.generation_params: Optional[dict] = kwargs.get("generation_params")

        raw_files = kwargs.get("file_paths") or []
        self.file_paths: List[Path] = [Path(p) for p in raw_files]
        self._file_badges: Dict[str, CompactWorkspaceFileBadge] = {}

        self._has_detected_tools: bool = False
        self._captured_plan: str = ""
        self._start_time: float = time.time()
        self._is_active_task: bool = is_active
        self._saved_elapsed_time: str = str(kwargs.get("elapsed_time") or "").strip()

        self._init_ui()

        # Настройка активного скилла
        skill_cand = kwargs.get("skill_name") or kwargs.get("active_skill")
        if skill_cand:
            self.set_skill(str(skill_cand))
        elif self.raw_text:
            self._detect_skill_from_text(self.raw_text)

        if self.raw_text:
            self.set_text(self.raw_text)

        for fp in self.file_paths:
            if fp.exists():
                self.add_workspace_file_card(fp)

        # Финализация архивного сообщения из истории
        if not self._is_active_task:
            self.mark_completed()
            if self._saved_elapsed_time:
                self.timer_lbl.setText(self._saved_elapsed_time)

    def _init_ui(self):
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        # Монолитная компоновка: без горизонтального сдвига и без аватарки
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(16, 6, 16, 6)
        outer_layout.setSpacing(0)

        # Монолитная полупрозрачная консоль задачи (Agent Task Canvas)
        self.card = EngineerGlassCard(self)
        self.card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.bubble_container = self.card

        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(14, 12, 14, 12)
        self.card_layout.setSpacing(10)

        # ---------------------------------------------------------------------
        # 1. Верхний статус-бар задачи (Activity Header)
        # ---------------------------------------------------------------------
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        # Индикатор фазы задачи (⚡ RUNNING · 2.4s)
        self.phase_indicator = QLabel("⚡ RUNNING", self.card)
        self.phase_indicator.setStyleSheet("color: #00F0FF; font-size: 11px; font-weight: 700; background: transparent;")
        header_row.addWidget(self.phase_indicator)

        self.dot_separator = QLabel("·", self.card)
        self.dot_separator.setStyleSheet("color: rgba(148, 163, 184, 0.40); font-size: 11px; font-weight: bold; background: transparent;")
        header_row.addWidget(self.dot_separator)

        self.timer_lbl = QLabel("0.0s", self.card)
        self.timer_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 500; font-family: 'Consolas', monospace; background: transparent;")
        header_row.addWidget(self.timer_lbl)

        # Таймер реального времени выполнения
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._on_timer_tick)
        if self._is_active_task:
            self._timer.start()

        header_row.addStretch()

        # Бейдж активного скилла ([🎮 Игры и GUI])
        self.skill_badge = PillPushButton("⚡ Инженерная разработка", self.card)
        self.skill_badge.setFixedHeight(22)
        self.skill_badge.setEnabled(False)
        self.skill_badge.setStyleSheet("""
            QPushButton {
                background-color: rgba(56, 189, 248, 0.10);
                color: #38BDF8;
                border: 1px solid rgba(56, 189, 248, 0.25);
                border-radius: 11px;
                font-size: 11px;
                font-weight: 600;
                padding: 1px 10px;
            }
        """)
        header_row.addWidget(self.skill_badge)

        # Кнопка копирования
        self.copy_btn = TransparentToolButton(FIF.COPY, self.card)
        self.copy_btn.setFixedSize(22, 22)
        self.copy_btn.setIconSize(QSize(13, 13))
        self.copy_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.copy_btn.setToolTip("Копировать ответ")
        self.copy_btn.installEventFilter(ToolTipFilter(self.copy_btn, 250, ToolTipPosition.BOTTOM))
        self.copy_btn.clicked.connect(self.copy_text)
        header_row.addWidget(self.copy_btn)

        # Кнопка остановки / удаления
        self.del_btn = TransparentToolButton(FIF.DELETE, self.card)
        self.del_btn.setFixedSize(22, 22)
        self.del_btn.setIconSize(QSize(13, 13))
        self.del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.del_btn.setToolTip("Удалить сообщение")
        self.del_btn.installEventFilter(ToolTipFilter(self.del_btn, 250, ToolTipPosition.BOTTOM))
        self.del_btn.clicked.connect(lambda: self.delete_requested.emit(self))
        header_row.addWidget(self.del_btn)

        self.card_layout.addLayout(header_row)

        # ---------------------------------------------------------------------
        # 2. Спойлер мыслей/плана (SmoothSpoiler)
        # ---------------------------------------------------------------------
        self.spoiler = EngineerSpoilerWidget(self.card)
        self.card_layout.addWidget(self.spoiler)
        self.spoiler.setVisible(False)

        # ---------------------------------------------------------------------
        # 3. Степпер действий и файлов (Execution Stepper)
        # ---------------------------------------------------------------------
        self.files_container = QFrame(self.card)
        self.files_container.setObjectName("EngineerFilesContainer")
        self.files_container.setStyleSheet("background: transparent; border: none;")
        self.files_layout = QVBoxLayout(self.files_container)
        self.files_layout.setContentsMargins(0, 0, 0, 0)
        self.files_layout.setSpacing(4)

        # Заголовок ленты шагов
        self.stepper_title = CaptionLabel("📋 Выполнение шагов:", self.files_container)
        self.stepper_title.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 600; background: transparent;")
        self.files_layout.addWidget(self.stepper_title)

        self.card_layout.addWidget(self.files_container)
        self.files_container.setVisible(False)

        # ---------------------------------------------------------------------
        # 4. Финальное резюме (Summary Box)
        # ---------------------------------------------------------------------
        self.summary_browser = SelectableTextBrowser(self.card)
        self.summary_browser.setReadOnly(True)
        self.summary_browser.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.summary_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.summary_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.summary_browser.setOpenExternalLinks(False)
        self.summary_browser.setOpenLinks(False)
        self.summary_browser.setStyleSheet(f"""
            QTextBrowser {{
                background-color: transparent;
                color: #F1F5F9;
                border: none;
                font-family: 'Inter', 'Segoe UI', sans-serif;
                font-size: {self.font_size}px;
                line-height: 1.30;
                selection-background-color: #00F0FF;
                selection-color: #000000;
            }}
        """)
        self.text_browser = self.summary_browser
        self.card_layout.addWidget(self.summary_browser)
        self.summary_browser.setVisible(False)

        outer_layout.addWidget(self.card)

    def _on_timer_tick(self):
        if sip.isdeleted(self):
            return
        elapsed = time.time() - self._start_time
        self.timer_lbl.setText(f"{elapsed:.1f}s")

    def set_skill(self, skill_name: str):
        """Устанавливает отображение активного скилла в бейдже заголовка."""
        if sip.isdeleted(self):
            return
        if not skill_name:
            return
        clean_name = skill_name.strip("[]")
        self.skill_badge.setText(f"[{clean_name}]" if not clean_name.startswith("[") else clean_name)

    def _detect_skill_from_text(self, text: str):
        low = text.lower()
        if any(k in low for k in ("pygame", "gui", "игра", "интерфейс", "окно", "тетрис", "змейка")):
            self.set_skill("🎮 Игры и GUI")
        elif any(k in low for k in ("test", "pytest", "тест", "tdd", "assert")):
            self.set_skill("🧪 TDD и Тесты")
        elif any(k in low for k in ("excel", "xlsx", "docx", "word", "таблиц", "отчет")):
            self.set_skill("📊 Документы и Анализ")
        elif any(k in low for k in ("fastapi", "flask", "web", "веб", "api", "сервер")):
            self.set_skill("🌐 Web и API")
        elif any(k in low for k in ("refactor", "рефакторинг", "архитектур")):
            self.set_skill("📐 Архитектура")
        else:
            self.set_skill("⚡ Инженер")

    # -------------------------------------------------------------------------
    # Публичные методы со строгой защитой от Use-After-Free (sip.isdeleted)
    # -------------------------------------------------------------------------

    def set_text(self, text: str):
        """
        Универсальный парсинг плана, мыслей и резюме.
        Сценарий 1: есть <think>...</think>
        Сценарий 2: тегов <think> нет, но есть вызовы инструментов (план до вызова -> в спойлер)
        Сценарий 3: чистый текст без тулов и без <think> -> спойлер скрывается
        """
        if sip.isdeleted(self):
            return

        raw_in = text or ""
        self.raw_text = raw_in
        self.original_text = raw_in

        self._detect_skill_from_text(raw_in)

        # Сценарий 0: Архитектурный план инженера
        if "### 📐 Архитектурный план" in raw_in or "### 🔍 Инспекция проекта" in raw_in:
            m_body = re.search(r'> \*\*Техническое задание:\*\*\s*\n(.*?)(?=\n\n⏳|\n\n###|$)', raw_in, flags=re.DOTALL)
            if not m_body:
                m_body = re.search(r'> \*\*Цель запроса:\*\*\s*\n(.*?)(?=\n\n⏳|\n\n###|$)', raw_in, flags=re.DOTALL)
            plan_body = m_body.group(1).strip() if m_body else ""
            if plan_body:
                clean_plan = "\n".join(re.sub(r'^\s*>\s?', '', line) for line in plan_body.splitlines())
            else:
                clean_plan = raw_in

            self._captured_plan = clean_plan
            self.spoiler.setVisible(True)
            self.spoiler.set_plan_text(clean_plan, title="💭 Архитектура и анализ")
            self.spoiler.set_collapsed(True, status="✅ Архитектурный план утверждён")

            m_status = re.search(r'(⏳\s*\*.*?\*)', raw_in)
            status_text = m_status.group(1) if m_status else "⏳ Приступаю к автономной реализации кода..."
            self._set_summary_text(status_text)
            if not self._is_active_task:
                self.mark_completed()
            self._update_bubble_geometry()
            return


        # Сценарий 1: Есть теги <think>
        if "<think>" in raw_in:
            m_think = re.search(r'<think>(.*?)(?:</think>|$)', raw_in, flags=re.DOTALL)
            think_body = m_think.group(1).strip() if m_think else ""
            summary_body = re.sub(r'<think>.*?</think>', '', raw_in, flags=re.DOTALL).strip()
            summary_body = self._clean_tool_tags(summary_body)

            if think_body:
                self.spoiler.setVisible(True)
                self.spoiler.set_plan_text(think_body, title="💭 Архитектура и анализ")
                if "</think>" in raw_in:
                    self.spoiler.set_collapsed(True, status="✅ Анализ завершён")
                    self.mark_completed()
                else:
                    self.spoiler.set_collapsed(False, status="🧠 Формирование плана...")
            else:
                self.spoiler.setVisible(False)

            self._set_summary_text(summary_body)
            self._update_bubble_geometry()
            return

        # Сценарий 2: Тегов <think> нет, но есть вызовы инструментов
        tool_pos = self._find_first_tool_pos(raw_in)
        if tool_pos is not None:
            self._has_detected_tools = True
            plan_body = raw_in[:tool_pos].strip()
            if plan_body:
                self._captured_plan = plan_body

            effective_plan = plan_body or self._captured_plan
            if effective_plan:
                self.spoiler.setVisible(True)
                self.spoiler.set_plan_text(effective_plan, title="💭 Архитектура решения")
                self.spoiler.set_collapsed(True, status="▶ 💭 Архитектура решения · [Развернуть]")
            else:
                self.spoiler.setVisible(False)

            after_tool_text = self._extract_after_tools_text(raw_in, tool_pos)
            self._set_summary_text(after_tool_text)
            self._update_bubble_geometry()
            return

        # Сценарий 2b: Тулы уже выполнялись в этой задаче или есть файлы, а пришёл финальный отчёт
        if self._has_detected_tools or self._captured_plan or self.file_paths:
            if self._captured_plan:
                self.spoiler.setVisible(True)
                self.spoiler.set_plan_text(self._captured_plan, title="💭 Архитектура решения")
                self.spoiler.set_collapsed(True, status="▶ 💭 Архитектура решения · [Развернуть]")
            clean_summary = self._clean_tool_tags(raw_in).strip()
            self._set_summary_text(clean_summary)
            self.mark_completed()
            self._update_bubble_geometry()
            return

        # Сценарий 3: Чистый текст без тулов и без <think>
        if not self._captured_plan:
            self.spoiler.setVisible(False)
        self._set_summary_text(raw_in.strip())
        if not self._is_active_task:
            self.mark_completed()
        self._update_bubble_geometry()

    def append_stream_chunk(self, chunk: str):
        """Потоковое добавление токенов с немедленной авто-маршрутизацией плана и резюме."""
        if sip.isdeleted(self):
            return
        if not chunk:
            return

        self.raw_text += chunk
        self.original_text = self.raw_text

        # Если в потоке есть <think>
        if "<think>" in self.raw_text:
            m_think = re.search(r'<think>(.*?)(?:</think>|$)', self.raw_text, flags=re.DOTALL)
            if m_think:
                think_body = m_think.group(1).strip()
                self.spoiler.setVisible(True)
                self.spoiler.set_plan_text(think_body, title="💭 Архитектура и анализ")
                if "</think>" in self.raw_text:
                    self.spoiler.set_collapsed(True, status="✅ Анализ завершён")
                    after_think = self.raw_text.split("</think>", 1)[1].strip()
                    self._set_summary_text(after_think)
                else:
                    self.spoiler.set_collapsed(False, status="🧠 Формирование плана...")
            return

        # Если <think> нет, проверяем маркеры тулов
        tool_pos = self._find_first_tool_pos(self.raw_text)
        if tool_pos is not None:
            self._has_detected_tools = True
            plan_body = self.raw_text[:tool_pos].strip()
            if plan_body:
                self._captured_plan = plan_body
                self.spoiler.setVisible(True)
                self.spoiler.set_plan_text(plan_body, title="💭 Архитектура решения")
                if not self.spoiler.is_collapsed:
                    self.spoiler.set_collapsed(True, status="▶ 💭 Архитектура решения · [Развернуть]")
            after_text = self._extract_after_tools_text(self.raw_text, tool_pos)
            self._set_summary_text(after_text)
            return

        # До появления первого тула весь стриминг плана держится в открытом спойлере
        self.spoiler.setVisible(True)
        self.spoiler.set_plan_title("💭 Архитектура и анализ")
        self.spoiler.set_status_text("🧠 Формирование плана...", is_active=True)
        self.spoiler.append_plan_chunk(chunk)
        self.summary_browser.setVisible(False)
        self._update_bubble_geometry()

    def append_thought_chunk(self, chunk: str):
        """Интеграция с ThoughtStreamParser."""
        if sip.isdeleted(self):
            return
        if not chunk:
            return
        self.spoiler.setVisible(True)
        self.spoiler.set_plan_title("💭 Архитектура и анализ")
        self.spoiler.append_plan_chunk(chunk)
        self._update_bubble_geometry()

    def finalize_thought(self, duration_sec: float = 0.0, token_count: int = 0):
        """Завершение фазы мыслей с фиксацией метрик и плавным сворачиванием."""
        if sip.isdeleted(self):
            return
        dur_text = f"{max(0.0, float(duration_sec)):.1f}".replace(",", ".")
        status_msg = f"{token_count} токенов • {dur_text} сек"
        self.spoiler.set_collapsed(True, status=status_msg)
        self._update_bubble_geometry()

    def set_thought_data(self, text: str, duration: float = 0.0, token_count: int = 0):
        """Восстановление хода мыслей из сохраненной истории сессии."""
        if sip.isdeleted(self):
            return
        clean_t = str(text or "").strip()
        if not clean_t:
            return
        self.spoiler.setVisible(True)
        self.spoiler.set_plan_text(clean_t, title="💭 Архитектура и анализ")
        dur_text = f"{max(0.0, float(duration)):.1f}".replace(",", ".")
        tc = token_count if token_count > 0 else max(1, len(clean_t) // 4)
        self.spoiler.set_collapsed(True, animated=False, status=f"{tc} токенов • {dur_text} сек")
        self.mark_completed()
        self._update_bubble_geometry()

    def get_or_create_thought_spoiler(self):
        """Совместимость с контроллерами диалога."""
        if sip.isdeleted(self):
            return None
        self.spoiler.setVisible(True)
        return self.spoiler

    def add_workspace_file_card(self, file_path: Path | str, on_run: Optional[Callable] = None) -> CompactWorkspaceFileBadge:
        """
        Добавляет строку в вертикальный степпер действий:
        глиф статуса, имя файла и кнопку [📄 В инспектор].
        """
        if sip.isdeleted(self):
            return None

        p = Path(file_path).resolve()
        key = str(p).lower()

        if key in self._file_badges:
            badge = self._file_badges[key]
            badge.set_status("completed")
            return badge

        badge = CompactWorkspaceFileBadge(
            file_path=p,
            on_run=on_run,
            action_text=f"workspace / {p.name}",
            status="completed",
            parent=self.files_container
        )
        self._file_badges[key] = badge

        if p not in self.file_paths:
            self.file_paths.append(p)

        self.files_layout.addWidget(badge)
        self.files_container.setVisible(True)
        self._update_bubble_geometry()
        return badge

    def add_step(
        self,
        action_text: str,
        status: str = "completed",
        file_path: Optional[Path] = None,
        on_run: Optional[Callable] = None
    ) -> CompactWorkspaceFileBadge:
        """Добавляет произвольный шаг выполнения в степпер (чтение, патч, запуск тестов)."""
        if sip.isdeleted(self):
            return None

        key = action_text.lower()
        if key in self._file_badges:
            badge = self._file_badges[key]
            badge.set_status(status)
            return badge

        badge = CompactWorkspaceFileBadge(
            file_path=file_path,
            on_run=on_run,
            action_text=action_text,
            status=status,
            parent=self.files_container
        )
        self._file_badges[key] = badge
        self.files_layout.addWidget(badge)
        self.files_container.setVisible(True)
        self._update_bubble_geometry()
        return badge

    def add_console_spoiler(
        self,
        title: str = "Консольный вывод",
        output: str = "",
        is_error: bool = False,
        return_code: int = 0
    ) -> ConsoleOutputSpoilerWidget:
        """Добавляет сворачиваемый спойлер вывода консоли скрипта."""
        if sip.isdeleted(self):
            return None

        spoiler = ConsoleOutputSpoilerWidget(
            title=title,
            output=output,
            is_error=is_error,
            return_code=return_code,
            parent=self.card
        )
        self.card_layout.addWidget(spoiler)
        self._update_bubble_geometry()
        return spoiler

    def update_avatar(self, avatar_path=None):
        """Безопасный no-op: аватарка полностью демонтирована в режиме инженера."""
        if sip.isdeleted(self):
            return

    def mark_completed(self):
        """Переводит карточку задачи в статус успешного завершения."""
        if sip.isdeleted(self):
            return
        self._is_active_task = False
        if self._timer.isActive():
            self._timer.stop()
        self.phase_indicator.setText("✓ COMPLETED")
        self.phase_indicator.setStyleSheet("color: #10B981; font-size: 11px; font-weight: 700; background: transparent;")
        if hasattr(self, "spoiler") and self.spoiler and hasattr(self.spoiler, "get_plan_text") and self.spoiler.get_plan_text():
            self.spoiler.setVisible(True)

    def mark_failed(self, error_text: str = ""):
        """Переводит карточку задачи в статус сбоя."""
        if sip.isdeleted(self):
            return
        self._is_active_task = False
        if self._timer.isActive():
            self._timer.stop()
        self.phase_indicator.setText("✕ FAILED")
        self.phase_indicator.setStyleSheet("color: #EF4444; font-size: 11px; font-weight: 700; background: transparent;")
        if error_text:
            self._set_summary_text(f"⚠️ {error_text}")

    def set_knowledge_sources(self, sources: list):
        """Заглушка совместимости с базой знаний."""
        if sip.isdeleted(self):
            return

    def toggle_spoiler(self):
        """Ручное переключение состояния спойлера."""
        if sip.isdeleted(self):
            return
        self.spoiler.toggle()

    def set_spoiler_collapsed(self, collapsed: bool, status: Optional[str] = None):
        """Управление свернутостью спойлера."""
        if sip.isdeleted(self):
            return
        self.spoiler.set_collapsed(collapsed, animated=True, status=status)

    def get_text(self) -> str:
        """Возвращает актуальный текст сообщения."""
        if sip.isdeleted(self):
            return ""
        return self.raw_text

    def copy_text(self):
        """Копирует текст сообщения в системный буфер обмена."""
        if sip.isdeleted(self):
            return
        txt = self.summary_browser.toPlainText() or self.raw_text
        if txt:
            cb = QApplication.clipboard()
            if cb:
                cb.setText(txt)

    # -------------------------------------------------------------------------
    # Вспомогательные методы
    # -------------------------------------------------------------------------

    def _set_summary_text(self, text: str):
        clean_t = (text or "").strip()
        if not clean_t:
            self.summary_browser.setFixedHeight(0)
            self.summary_browser.setVisible(False)
            return

        self.summary_browser.setVisible(True)
        html_content = self._markdown_to_html(clean_t)
        self.summary_browser.setHtml(html_content)
        self._adjust_summary_height()

    def _adjust_summary_height(self):
        doc = self.summary_browser.document()
        if not doc:
            return
        doc.setDocumentMargin(4)
        target_w = max(180, self.card.width() - 32)
        doc.setTextWidth(target_w)
        ideal_h = int(doc.documentLayout().documentSize().height())
        self.summary_browser.setFixedHeight(max(ideal_h + 10, 24))

    def _update_bubble_geometry(self):
        self._adjust_summary_height()
        p = self.parentWidget()
        if p and hasattr(p, "layout") and p.layout():
            p.layout().activate()

    @staticmethod
    def _find_first_tool_pos(text: str) -> Optional[int]:
        if not text:
            return None

        try:
            from services.tool_engine import ToolEngine
            calls = ToolEngine.parse_tool_calls(text)
            if calls and calls[0].raw_tag:
                pos = text.find(calls[0].raw_tag)
                if pos != -1:
                    return pos
        except Exception:
            pass

        patterns = [
            r'(?:`+|<+)TOOL:\w+',
            r'<tool_call>',
            r'\{\s*"name"\s*:\s*"[^"]+"',
            r'\[TOOL_CALL\]',
            r'write_file\s*\(',
            r'```(?:python|py)?\s*filename=',
            r'<<TOOL:write_file',
            r'<<TOOL:patch_file',
            r'<<TOOL:run_script',
        ]
        min_pos = None
        for pat in patterns:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                if min_pos is None or m.start() < min_pos:
                    min_pos = m.start()
        return min_pos

    @staticmethod
    def _extract_after_tools_text(text: str, tool_pos: int) -> str:
        tail = text[tool_pos:]
        cleaned_tail = EngineerTaskCardWidget._clean_tool_tags(tail).strip()
        return cleaned_tail

    @staticmethod
    def _clean_tool_tags(text: str) -> str:
        if not text:
            return ""
        cleaned = re.sub(r'<<TOOL:write_file\s+(?:filename|path|file)=["\'][^"\']+["\']\s*>>?[\r\n]*.*?(?:<<?/+TOOL(?::write_file)?>>?|`+/+TOOL(?::write_file)?`+)', '', text, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<<TOOL:patch_file\s+(?:filename|path|file)=["\'][^"\']+["\']\s*>>?[\r\n]*.*?(?:<<?/+TOOL(?::patch_file)?>>?|`+/+TOOL(?::patch_file)?`+)', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<<TOOL:\w+.*?>>', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'<tool_call>.*?</tool_call>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'(`+|<+)TOOL:.*?(?:>>|>\'?|>`*|`+|$)', '', cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    @staticmethod
    def _markdown_to_html(text: str) -> str:
        if not text:
            return ""
        norm = text.replace('\r\n', '\n')
        escaped = html.escape(norm)

        def code_block_sub(match):
            code = match.group(1)
            return f"<pre style='background-color:#0B1120; border:1px solid #1E293B; border-radius:6px; padding:6px; color:#00F0FF; font-family:Consolas, monospace; white-space:pre-wrap;'>{code}</pre>"
        escaped = re.sub(r'```(?:[a-zA-Z0-9_-]+)?\n(.*?)```', code_block_sub, escaped, flags=re.DOTALL)

        escaped = re.sub(r'`([^`]+)`', r"<code style='background-color:#1E293B; color:#38BDF8; padding:1px 4px; border-radius:4px; font-family:Consolas;'>\1</code>", escaped)

        escaped = re.sub(r'\*\*(.*?)\*\*', r"<strong>\1</strong>", escaped)
        escaped = re.sub(r'\*([^*]+)\*', r"<em style='color:#38BDF8;'>\1</em>", escaped)

        def path_link_sub(match):
            raw_path = match.group(1)
            return f'<span style="color:#00F0FF; font-weight:600;">{raw_path}</span>'
        escaped = re.sub(r'(?<!href=[\"\']file:///)((?:[a-zA-Z]:[\\/]|(?:workspace[\\/]))[^\s<>\[\]\"\'\)\(&;]+)', path_link_sub, escaped)

        escaped = escaped.replace('\n', '<br>')
        return escaped
