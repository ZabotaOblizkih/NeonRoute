"""
Dependency Dialog for NeonRoute.
Provides diagnostics and automated background installation for optional Python dependencies
and external tools required by the Engineer Mode.
Refactored to Windows 11 Fluent Design (FramelessDialog, FluentTitleBar, Adaptive Palette).
"""

from typing import Optional, Dict
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QCursor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QFrame,
    QMessageBox,
)
from qframelesswindow import FramelessDialog
from qfluentwidgets import (
    FluentTitleBar,
    PushButton,
    PrimaryPushButton,
    CheckBox,
    ProgressBar,
    CaptionLabel,
    FluentIcon as FIF,
)

from services.environment_doctor import EnvironmentDoctor, PipInstallWorker
from utils.config_manager import ConfigManager
from ui.common.palette_utils import extract_adaptive_palette


class DependencyDialog(FramelessDialog):
    """
    Диалоговое окно «Проверка компонентов» NeonRoute в стиле Windows 11 Fluent Design.
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.window = parent
        self.setWindowTitle("Проверка компонентов — NeonRoute")
        self.resize(680, 560)
        self.setMinimumSize(580, 460)

        # 1. Custom Fluent TitleBar с [X] в правом верхнем углу
        title_bar = FluentTitleBar(self)
        self.setTitleBar(title_bar)
        self.titleBar.setFixedHeight(46)
        if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
            self.titleBar.minBtn.hide()
        if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
            self.titleBar.maxBtn.hide()
        if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
            self.titleBar.closeBtn.setVisible(True)
            try:
                self.titleBar.closeBtn.clicked.disconnect()
            except Exception:
                pass
            self.titleBar.closeBtn.clicked.connect(self._on_skip)

        self.setObjectName("DependencyDialog")
        self._current_palette: Dict[str, str] = {}

        self.missing_items = EnvironmentDoctor.check_missing()
        self.checkboxes = {}
        self.worker = None

        self._init_ui()
        self.update_adaptive_palette()

    def _resolve_pixmap(self) -> Optional[QPixmap]:
        """Ищет активные обои в иерархии родительских окон для адаптивной палитры."""
        p = self.parent()
        while p:
            if hasattr(p, "background_pixmap") and p.background_pixmap:
                return p.background_pixmap
            if hasattr(p, "window") and hasattr(p.window, "background_pixmap") and p.window.background_pixmap:
                return p.window.background_pixmap
            if hasattr(p, "_bg_render_cache") and p._bg_render_cache:
                return p._bg_render_cache
            p = p.parent()
        return None

    def update_adaptive_palette(self):
        """
        Адаптирует цветовую палитру окна, карточек и кнопок под активные обои.
        """
        pixmap = self._resolve_pixmap()
        palette = extract_adaptive_palette(pixmap)
        self._current_palette = palette

        self.setStyleSheet(f"""
            DependencyDialog#DependencyDialog {{
                background-color: {palette['bg']};
                border: 1px solid {palette['border']};
                border-radius: 14px;
            }}
            FluentTitleBar {{
                background-color: {palette['bg']};
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }}
            FluentTitleBar QLabel {{
                color: #F8FAFC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-weight: 600;
                font-size: 13px;
            }}
            QWidget#DependencyBodyWidget {{
                background-color: {palette['bg']};
                border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px;
            }}
            #DependencyDialog QWidget {{
                background-color: transparent;
                color: #CBD5E1;
            }}
            #DependencyDialog QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QFrame.DepCard {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
                padding: 12px 14px;
            }}
            QFrame.DepCard:hover {{
                background-color: {palette['card_hover']};
                border-color: {palette['accent']};
            }}
            QFrame#SuccessCard {{
                background-color: {palette['card']};
                border: 1px solid {palette['card_border']};
                border-radius: 10px;
                padding: 24px;
            }}
            PushButton {{
                background-color: {palette['btn']};
                border: 1px solid {palette['btn_border']};
                border-radius: 6px;
                color: #F8FAFC;
                font-weight: 500;
                font-size: 13px;
                padding: 6px 20px;
            }}
            PushButton:hover {{
                background-color: {palette['btn_hover']};
                border-color: {palette['btn_hover']};
                color: {palette['btn_hover_text']};
                font-weight: 600;
            }}
            PushButton:pressed {{
                background-color: {palette['card_hover']};
                border-color: {palette['border']};
                color: #F8FAFC;
            }}
            PrimaryPushButton {{
                background-color: {palette['accent']};
                border: 1px solid {palette['accent']};
                border-radius: 6px;
                color: #0A0D14;
                font-weight: 600;
                font-size: 13px;
                padding: 6px 20px;
            }}
            PrimaryPushButton:hover {{
                background-color: {palette['group_title']};
                border-color: {palette['group_title']};
            }}
            PrimaryPushButton:disabled {{
                background-color: {palette['btn']};
                border-color: {palette['btn_border']};
                color: #64748B;
            }}
            #DependencyDialog QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0px;
            }}
            #DependencyDialog QScrollBar::handle:vertical {{
                background: {palette['border']};
                min-height: 24px;
                border-radius: 4px;
            }}
            #DependencyDialog QScrollBar::handle:vertical:hover {{
                background: {palette['accent']};
            }}
        """)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        body_widget = QWidget(self)
        body_widget.setObjectName("DependencyBodyWidget")
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(22, 14, 22, 18)
        body_layout.setSpacing(12)

        # 1. Шапка раздела
        header_layout = QVBoxLayout()
        header_layout.setSpacing(2)

        h_lbl = QLabel("🛠️ Диагностика окружения и компонентов", body_widget)
        h_lbl.setStyleSheet("font-size: 18px; font-weight: 700; color: #F8FAFC;")
        header_layout.addWidget(h_lbl)

        sub_lbl = CaptionLabel(
            "Проверка необходимых Python-библиотек и утилит для автономной работы режима «Инженер»",
            body_widget
        )
        sub_lbl.setStyleSheet("font-size: 12px; color: #94A3B8;")
        sub_lbl.setWordWrap(True)
        header_layout.addWidget(sub_lbl)

        body_layout.addLayout(header_layout)

        # 2. Область прокрутки со списком компонентов
        scroll = QScrollArea(body_widget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        c_layout = QVBoxLayout(container)
        c_layout.setSpacing(10)
        c_layout.setContentsMargins(0, 4, 10, 4)

        if not self.missing_items:
            success_card = QFrame(container)
            success_card.setObjectName("SuccessCard")
            s_layout = QVBoxLayout(success_card)
            s_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            s_layout.setSpacing(8)

            all_ok_lbl = QLabel("✅ Все необходимые компоненты и библиотеки уже установлены!", success_card)
            all_ok_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #34D399;")
            all_ok_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            s_layout.addWidget(all_ok_lbl)

            detail_lbl = QLabel(
                "Системное окружение полностью готово к автономной работе во всех режимах (Ассистент, РП и Инженер).",
                success_card
            )
            detail_lbl.setStyleSheet("font-size: 12px; color: #94A3B8;")
            detail_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            detail_lbl.setWordWrap(True)
            s_layout.addWidget(detail_lbl)

            c_layout.addWidget(success_card)
        else:
            for item in self.missing_items:
                card = QFrame(container)
                card.setProperty("class", "DepCard")
                card_l = QVBoxLayout(card)
                card_l.setSpacing(6)
                card_l.setContentsMargins(14, 12, 14, 12)

                cb = CheckBox(item["name"], card)
                cb.setStyleSheet("font-size: 14px; font-weight: 600; color: #F8FAFC;")
                if item["type"] == "python":
                    cb.setChecked(True)
                    self.checkboxes[item["package"]] = cb
                else:
                    cb.setChecked(False)
                    cb.setEnabled(False)

                desc = QLabel(item["description"], card)
                desc.setStyleSheet("font-size: 12px; color: #CBD5E1; margin-left: 24px;")
                desc.setWordWrap(True)

                conseq = QLabel(f"⚠️ Если не ставить: {item['consequence']}", card)
                conseq.setStyleSheet("font-size: 12px; color: #FBBF24; margin-left: 24px;")
                conseq.setWordWrap(True)

                card_l.addWidget(cb)
                card_l.addWidget(desc)
                card_l.addWidget(conseq)

                if item["type"] == "binary" and "install_guide" in item:
                    guide = QLabel(f"💡 Подсказка: {item['install_guide']}", card)
                    guide.setStyleSheet("font-size: 11px; color: #38BDF8; margin-left: 24px;")
                    guide.setWordWrap(True)
                    card_l.addWidget(guide)

                c_layout.addWidget(card)

        c_layout.addStretch(1)
        scroll.setWidget(container)
        body_layout.addWidget(scroll, stretch=1)

        # 3. Индикатор прогресса установки
        self.progress_bar = ProgressBar(body_widget)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        body_layout.addWidget(self.progress_bar)

        self.status_lbl = CaptionLabel("", body_widget)
        self.status_lbl.setStyleSheet("font-size: 12px; color: #38BDF8;")
        self.status_lbl.hide()
        body_layout.addWidget(self.status_lbl)

        # 4. Чекбокс «Больше не показывать»
        self.cb_dont_show = CheckBox("Больше не показывать это окно при запуске", body_widget)
        self.cb_dont_show.setStyleSheet("color: #94A3B8; font-size: 12px;")
        body_layout.addWidget(self.cb_dont_show)

        # 5. Нижняя панель действий
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch(1)

        skip_text = "Закрыть" if not self.missing_items else "Пропустить"
        self.btn_skip = PushButton(skip_text, body_widget)
        self.btn_skip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_skip.clicked.connect(self._on_skip)
        btn_layout.addWidget(self.btn_skip)

        if self.missing_items:
            self.btn_install = PrimaryPushButton("Установить выбранное", body_widget)
            self.btn_install.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.btn_install.setEnabled(bool(self.checkboxes))
            self.btn_install.clicked.connect(self._on_install)
            btn_layout.addWidget(self.btn_install)
        else:
            self.btn_install = None

        body_layout.addLayout(btn_layout)

        root_layout.addWidget(self.titleBar)
        root_layout.addWidget(body_widget, 1)

    def _on_skip(self):
        if self.cb_dont_show.isChecked():
            cfg = ConfigManager.load_config()
            cfg["check_dependencies_on_startup"] = False
            ConfigManager.save_config(cfg)
        self.reject()

    def _on_install(self):
        to_install = [pkg for pkg, cb in self.checkboxes.items() if cb.isChecked()]
        if not to_install:
            QMessageBox.warning(self, "Внимание", "Не выбран ни один компонент для установки.")
            return

        if self.btn_install:
            self.btn_install.setEnabled(False)
        self.btn_skip.setEnabled(False)
        self.progress_bar.show()
        self.status_lbl.show()

        self.worker = PipInstallWorker(to_install)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, msg: str):
        self.status_lbl.setText(msg)

    def _on_finished(self, success: bool, msg: str):
        self.progress_bar.hide()
        self.status_lbl.hide()
        self.btn_skip.setEnabled(True)
        if self.btn_install:
            self.btn_install.setEnabled(True)

        if success:
            QMessageBox.information(self, "Успех", msg)
            self.accept()
        else:
            QMessageBox.critical(self, "Ошибка установки", msg)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "titleBar") and self.titleBar:
            if hasattr(self.titleBar, "minBtn") and self.titleBar.minBtn:
                self.titleBar.minBtn.hide()
            if hasattr(self.titleBar, "maxBtn") and self.titleBar.maxBtn:
                self.titleBar.maxBtn.hide()
            if hasattr(self.titleBar, "closeBtn") and self.titleBar.closeBtn:
                self.titleBar.closeBtn.setVisible(True)

        self.update_adaptive_palette()

        if self.parent():
            try:
                parent_geom = self.parent().geometry()
                x = parent_geom.x() + (parent_geom.width() - self.width()) // 2
                y = parent_geom.y() + (parent_geom.height() - self.height()) // 2
                self.move(max(20, x), max(20, y))
            except Exception:
                pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._on_skip()
            event.accept()
        else:
            super().keyPressEvent(event)
